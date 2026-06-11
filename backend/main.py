"""
FastAPI backend — Gift Recommendation Agent
============================================

Endpoints
─────────
Health
  GET  /health

Workflow lifecycle
  POST /api/workflow/run             start workflow(s) — returns immediately, runs async
  POST /api/workflow/run-file        upload contacts.json and start workflows
  GET  /api/workflow/                list all runs (summary)
  GET  /api/workflow/{id}            full result for one run
  POST /api/workflow/{id}/review     approve | reject | edit | regenerate

Intermediate step inspection  ← great for Postman / debugging
  GET  /api/workflow/{id}/steps      all node outputs in order
  GET  /api/workflow/{id}/signals    just the extracted profile signals
  GET  /api/workflow/{id}/products   products the agent considered
  GET  /api/workflow/{id}/gifts      ranked gift recommendations
  GET  /api/workflow/{id}/trace      full timing + error trace

Real-time
  GET  /api/workflow/{id}/stream     SSE stream — one event per node completion
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent))

import config
import storage
from models.schemas import Contact, ReviewAction, ReviewStatus, RunWorkflowRequest
from workflow.graph import compiled_graph

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Gift Recommendation Agent API",
    description=(
        "Hyper-personalised gift recommendations powered by a LangGraph workflow, "
        "Ollama (local LLM), and DuckDuckGo product search. "
        "Supports human-in-the-loop review with approve / reject / edit / regenerate actions."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── In-memory stores ─────────────────────────────────────────────────────────

# thread_id → run metadata
_runs: Dict[str, Dict[str, Any]] = {}

# thread_id → ordered list of completed node snapshots
# each snapshot: { node, timestamp_start, timestamp_end, duration_ms, output, error }
_steps: Dict[str, List[Dict[str, Any]]] = {}

storage.init_db()
_runs.update(storage.load_runs())
_steps.update(storage.load_all_steps())


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_config(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_langgraph_state(thread_id: str) -> Optional[Dict[str, Any]]:
    snap = compiled_graph.get_state(_make_config(thread_id))
    if snap and snap.values:
        return dict(snap.values)
    return storage.load_state(thread_id)


def _token_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    usage = state.get("llm_usage") or []
    return {
        "total_llm_calls": len(usage),
        "total_prompt_tokens": sum(u.get("prompt_tokens", 0) for u in usage),
        "total_completion_tokens": sum(u.get("completion_tokens", 0) for u in usage),
        "total_tokens": sum(u.get("prompt_tokens", 0) + u.get("completion_tokens", 0) for u in usage),
        "per_node": usage,
    }


def _build_result(state: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
    contact    = state.get("contact", {})
    signals    = state.get("profile_signals") or {}
    trace      = state.get("search_trace") or {}
    gifts      = state.get("recommended_gifts") or []
    wf_status  = state.get("status", "unknown")

    status_map = {
        "approved": ReviewStatus.APPROVED, "rejected": ReviewStatus.REJECTED,
        "edited": ReviewStatus.APPROVED,   "regenerating": ReviewStatus.REGENERATING,
        "completed": ReviewStatus.COMPLETED,
    }
    review_status = status_map.get(wf_status, ReviewStatus.PENDING).value

    return {
        "thread_id": thread_id,
        "contact_name": contact.get("name", "Unknown"),
        "profile_signals": {
            "strong_signals": signals.get("strong_signals", []),
            "weak_signals":   signals.get("weak_signals", []),
            "signals_to_avoid": signals.get("signals_to_avoid", []),
        },
        "search_trace": {
            "queries_used": trace.get("queries_used", []),
            "products_considered_count": trace.get("products_considered_count", 0),
        },
        "recommended_gifts": gifts,
        "human_review": {
            "status": review_status,
            "available_actions": ["approve", "reject", "edit", "regenerate"],
        },
        "workflow_status": wf_status,
        "errors": [e for e in state.get("errors", []) if e],
        "token_usage": _token_summary(state),
    }


def _initial_state(contact: Contact, thread_id: str, tone: str = "professional", feedback: str = None) -> Dict[str, Any]:
    return {
        "contact": contact.model_dump(),
        "profile_signals": None,
        "search_queries": None,
        "products_considered": None,
        "recommended_gifts": None,
        "human_action": None,
        "human_edits": None,
        "human_feedback": feedback,
        "tone": tone,
        "workflow_id": thread_id,
        "status": "started",
        "errors": [],
        "llm_usage": [],
        "retry_count": 0,
        "search_trace": None,
    }


# ─── Background workflow runner ───────────────────────────────────────────────

async def _run_workflow_bg(contact_data: Dict[str, Any], thread_id: str) -> None:
    """
    Runs the LangGraph workflow in a thread-pool executor so the event loop
    stays free.  Captures each node's output into _steps[thread_id] for the
    /steps and /stream endpoints.
    """
    config = _make_config(thread_id)
    _steps[thread_id] = []
    _runs[thread_id]["status"] = "running"

    def _stream_sync() -> None:
        """Blocking: stream LangGraph node-by-node and record outputs."""
        contact = Contact.model_validate(contact_data)
        init_state = _initial_state(contact, thread_id)

        t_prev = time.perf_counter()
        for chunk in compiled_graph.stream(init_state, config, stream_mode="updates"):
            for node_name, state_update in chunk.items():
                if node_name.startswith("__"):
                    continue
                t_now = time.perf_counter()
                duration_ms = round((t_now - t_prev) * 1000)
                t_prev = t_now

                # Pull per-call token stats from the node's state update
                node_llm_usage = state_update.get("llm_usage") or []

                step = {
                    "node": node_name,
                    "timestamp": _now_iso(),
                    "duration_ms": duration_ms,
                    "llm_calls": len(node_llm_usage),
                    "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in node_llm_usage),
                    "completion_tokens": sum(u.get("completion_tokens", 0) for u in node_llm_usage),
                    "output": _summarise_step(node_name, state_update),
                }
                _steps[thread_id].append(step)
                # keep run status reflecting the latest node
                _runs[thread_id]["status"] = f"running:{node_name}"
                storage.save_steps(thread_id, _steps[thread_id])
                storage.save_run(thread_id, _runs[thread_id])

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _stream_sync)
        _runs[thread_id]["status"] = "pending_review"
        state = _get_langgraph_state(thread_id)
        if state:
            storage.save_state(thread_id, state)
        storage.save_run(thread_id, _runs[thread_id])
    except Exception as exc:
        _runs[thread_id]["status"] = "error"
        _runs[thread_id]["error"] = str(exc)
        storage.save_run(thread_id, _runs[thread_id])


def _summarise_step(node: str, update: Dict[str, Any]) -> Dict[str, Any]:
    """Return a clean, human-readable summary of each step's output."""
    if node == "extract_signals":
        sig = update.get("profile_signals") or {}
        return {
            "strong_signals": sig.get("strong_signals", []),
            "weak_signals":   sig.get("weak_signals", []),
            "signals_to_avoid": sig.get("signals_to_avoid", []),
            "status": update.get("status"),
            "errors": update.get("errors", []),
        }
    if node == "search_products":
        products = update.get("products_considered") or []
        return {
            "queries_used": update.get("search_queries", []),
            "product_intents": (update.get("search_trace") or {}).get("product_intents", []),
            "total_products_found": len(products),
            "valid_ecommerce_products": sum(1 for p in products if p.get("is_valid")),
            "products_considered": [
                {
                    "title": p.get("title"),
                    "url": p.get("url"),
                    "domain": p.get("domain"),
                    "price": p.get("estimated_price"),
                    "valid": p.get("is_valid"),
                    "score": p.get("candidate_score"),
                    "source_query": p.get("source_query"),
                    "validation_reason": p.get("validation_reason"),
                }
                for p in products
            ],
            "status": update.get("status"),
            "errors": update.get("errors", []),
        }
    if node == "rank_gifts":
        gifts = update.get("recommended_gifts") or []
        return {
            "gifts_ranked": len(gifts),
            "summary": [
                {
                    "rank": g.get("rank"),
                    "gift_name": g.get("gift_name"),
                    "store": g.get("store"),
                    "price": g.get("estimated_price"),
                    "confidence": g.get("confidence_score"),
                    "risk_level": g.get("risk_level"),
                    "assumptions": g.get("assumptions", []),
                    "why_this_gift": g.get("why_this_gift"),
                    "personalisation_reasoning": g.get("personalisation_reasoning"),
                }
                for g in gifts
            ],
            "status": update.get("status"),
            "errors": update.get("errors", []),
        }
    if node == "generate_messages":
        gifts = update.get("recommended_gifts") or []
        return {
            "messages_generated": len(gifts),
            "messages": [
                {"rank": g.get("rank"), "gift_name": g.get("gift_name"), "message": g.get("personalised_message")}
                for g in gifts
            ],
            "status": update.get("status"),
            "errors": update.get("errors", []),
        }
    if node == "human_review":
        return {"action": update.get("human_action"), "status": update.get("status")}
    if node == "finalize":
        return {"status": update.get("status")}
    return {k: v for k, v in update.items() if k not in ("contact", "products_considered")}


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    """Check if the API is running."""
    return {
        "status": "ok",
        "timestamp": _now_iso(),
        "active_runs": len(_runs),
        "pending_review": sum(1 for r in _runs.values() if r.get("status") == "pending_review"),
        "ollama": {
            "base_url": config.OLLAMA_BASE_URL,
            "model": config.OLLAMA_MODEL,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": config.OLLAMA_NUM_PREDICT,
        },
        "search": {
            "duckduckgo_enabled": True,
            "tavily_enabled": bool(config.TAVILY_API_KEY),
            "tavily_search_depth": config.TAVILY_SEARCH_DEPTH,
        },
    }


# ── Workflow lifecycle ────────────────────────────────────────────────────────

@app.post(
    "/api/workflow/run",
    tags=["Workflow"],
    summary="Start workflow for one or more contacts",
    response_description="List of thread IDs — poll GET /api/workflow/{id} for results",
)
async def run_workflow(request: RunWorkflowRequest, background_tasks: BackgroundTasks):
    """
    Accepts an array of contacts and starts a background workflow for each.
    Returns immediately with thread IDs; use GET /api/workflow/{id} to poll.

    Each workflow goes through:
    1. Signal extraction  → GET /api/workflow/{id}/signals
    2. Product search     → GET /api/workflow/{id}/products
    3. Gift ranking       → GET /api/workflow/{id}/gifts
    4. Message generation → GET /api/workflow/{id}/gifts (with messages)
    5. Human review       → POST /api/workflow/{id}/review
    """
    result = []
    for contact in request.contacts:
        thread_id = str(uuid.uuid4())
        _runs[thread_id] = {
            "thread_id": thread_id,
            "contact_name": contact.name,
            "contact_role": contact.role,
            "contact_company": contact.company,
            "contact_location": contact.location,
            "status": "queued",
            "created_at": _now_iso(),
        }
        _steps[thread_id] = []
        storage.save_run(thread_id, _runs[thread_id])
        storage.save_steps(thread_id, _steps[thread_id])
        background_tasks.add_task(_run_workflow_bg, contact.model_dump(), thread_id)
        result.append({
            "thread_id": thread_id,
            "contact_name": contact.name,
            "status": "queued",
            "links": {
                "status":   f"/api/workflow/{thread_id}",
                "steps":    f"/api/workflow/{thread_id}/steps",
                "stream":   f"/api/workflow/{thread_id}/stream",
                "signals":  f"/api/workflow/{thread_id}/signals",
                "products": f"/api/workflow/{thread_id}/products",
                "gifts":    f"/api/workflow/{thread_id}/gifts",
                "trace":    f"/api/workflow/{thread_id}/trace",
                "review":   f"/api/workflow/{thread_id}/review",
            },
        })
    return result


@app.post(
    "/api/workflow/run-file",
    tags=["Workflow"],
    summary="Upload contacts.json and start workflows",
)
async def run_workflow_from_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Upload a contacts JSON file (array of contact objects) to start workflows for all contacts."""
    try:
        contents = await file.read()
        raw = json.loads(contents)
        if not isinstance(raw, list):
            raw = [raw]
        contacts = [Contact.model_validate(c) for c in raw]
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid contacts JSON: {exc}")

    return await run_workflow(RunWorkflowRequest(contacts=contacts), background_tasks)


@app.get(
    "/api/workflow/",
    tags=["Workflow"],
    summary="List all workflow runs",
)
async def list_runs():
    """Returns a summary of every run in this session."""
    return [
        {
            "thread_id": tid,
            "contact_name": r.get("contact_name"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "steps_completed": len(_steps.get(tid, [])),
        }
        for tid, r in _runs.items()
    ]


@app.get(
    "/api/workflow/{thread_id}",
    tags=["Workflow"],
    summary="Get full result for a run",
)
async def get_workflow(thread_id: str):
    """Returns the complete structured output including signals, gifts, review status, and errors."""
    _require_run(thread_id)
    run = _runs[thread_id]
    run_status = run.get("status", "unknown")

    # Still running — don't read partial LangGraph state; it would look like "pending_review"
    # even though recommended_gifts isn't populated yet.
    if run_status == "queued" or run_status == "running" or run_status.startswith("running:"):
        return {
            "thread_id": thread_id,
            "contact_name": run.get("contact_name"),
            "workflow_status": run_status,
            "error": None,
            "message": "Workflow is still running. Poll this endpoint or use /stream.",
        }

    if run_status == "error":
        return {
            "thread_id": thread_id,
            "contact_name": run.get("contact_name"),
            "workflow_status": "error",
            "error": run.get("error"),
        }

    # Workflow has finished (pending_review, approved, rejected, etc.) — read LangGraph state
    state = _get_langgraph_state(thread_id)
    if not state:
        return {
            "thread_id": thread_id,
            "contact_name": run.get("contact_name"),
            "workflow_status": run_status,
            "error": None,
            "message": "State unavailable — workflow may still be initialising.",
        }
    return _build_result(state, thread_id)


@app.post(
    "/api/workflow/{thread_id}/review",
    tags=["Workflow"],
    summary="Submit a human review decision",
)
async def review_workflow(thread_id: str, action: ReviewAction, background_tasks: BackgroundTasks):
    """
    Submit a review decision for a pending recommendation set.

    Actions:
    - **approve** — accept recommendations as-is
    - **reject**  — discard recommendations
    - **edit**    — replace gifts with `edited_gifts` array
    - **regenerate** — re-run the full workflow (optional `feedback` and `tone`)
    """
    _require_run(thread_id)

    if action.action == "regenerate":
        state = _get_langgraph_state(thread_id)
        if not state:
            raise HTTPException(status_code=409, detail="Cannot regenerate — state not found")
        contact = Contact.model_validate(state["contact"])
        new_id = str(uuid.uuid4())
        _runs[new_id] = {
            "thread_id": new_id,
            "contact_name": contact.name,
            "contact_role": contact.role,
            "status": "queued",
            "created_at": _now_iso(),
            "regenerated_from": thread_id,
        }
        _steps[new_id] = []
        storage.save_run(new_id, _runs[new_id])
        storage.save_steps(new_id, _steps[new_id])
        background_tasks.add_task(_run_workflow_bg, contact.model_dump(), new_id)
        return {
            "message": "Workflow regenerating",
            "new_thread_id": new_id,
            "links": {"status": f"/api/workflow/{new_id}", "stream": f"/api/workflow/{new_id}/stream"},
        }

    config = _make_config(thread_id)
    state_patch: Dict[str, Any] = {
        "human_action": action.action,
        "human_feedback": action.feedback,
        "tone": action.tone or "professional",
    }
    if action.action == "edit" and action.edited_gifts:
        state_patch["human_edits"] = action.edited_gifts

    try:
        compiled_graph.update_state(config, state_patch, as_node="human_review")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: compiled_graph.invoke(None, config))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Resume failed: {exc}")

    _runs[thread_id]["status"] = action.action
    final = _get_langgraph_state(thread_id)
    if final:
        storage.save_state(thread_id, final)
    storage.save_run(thread_id, _runs[thread_id])
    return {
        "message": f"Action '{action.action}' applied",
        "thread_id": thread_id,
        "result": _build_result(final, thread_id) if final else {},
    }


# ── Intermediate step inspection ──────────────────────────────────────────────

@app.get(
    "/api/workflow/{thread_id}/steps",
    tags=["Intermediate Steps"],
    summary="All node outputs in execution order",
)
async def get_steps(thread_id: str):
    """
    Returns every node's output as it completed.
    Useful for tracing the agent's reasoning step by step.

    Nodes: extract_signals → search_products → rank_gifts → generate_messages → human_review → finalize
    """
    _require_run(thread_id)
    run = _runs[thread_id]
    steps = _steps.get(thread_id, [])
    return {
        "thread_id": thread_id,
        "contact_name": run.get("contact_name"),
        "workflow_status": run.get("status"),
        "steps_completed": len(steps),
        "steps": steps,
    }


@app.get(
    "/api/workflow/{thread_id}/signals",
    tags=["Intermediate Steps"],
    summary="Extracted profile signals (Step 1)",
)
async def get_signals(thread_id: str):
    """
    Returns the profile signals the agent extracted from the LinkedIn data.
    Shows strong signals (direct evidence), weak signals (inferred), and
    sensitive attributes that were deliberately excluded.
    """
    _require_run(thread_id)
    state = _get_langgraph_state(thread_id)
    if not state or not state.get("profile_signals"):
        return {"thread_id": thread_id, "status": "not_yet_available", "message": "Signal extraction not complete yet"}
    signals = state["profile_signals"]
    return {
        "thread_id": thread_id,
        "contact_name": state["contact"].get("name"),
        "profile_signals": signals,
        "signal_counts": {
            "strong": len(signals.get("strong_signals", [])),
            "weak":   len(signals.get("weak_signals", [])),
            "avoided": len(signals.get("signals_to_avoid", [])),
        },
    }


@app.get(
    "/api/workflow/{thread_id}/products",
    tags=["Intermediate Steps"],
    summary="Products the agent considered (Step 2)",
)
async def get_products(thread_id: str):
    """
    Returns all products found via DuckDuckGo search, including:
    - The search queries used
    - Each product's URL, title, snippet, detected price
    - Whether the URL was from a trusted e-commerce domain
    """
    _require_run(thread_id)
    state = _get_langgraph_state(thread_id)
    if not state or not state.get("products_considered"):
        return {"thread_id": thread_id, "status": "not_yet_available", "message": "Product search not complete yet"}

    products = state["products_considered"] or []
    return {
        "thread_id": thread_id,
        "contact_name": state["contact"].get("name"),
        "search_queries": state.get("search_queries", []),
        "total_products_found": len(products),
        "valid_ecommerce_products": sum(1 for p in products if p.get("is_valid")),
        "products": [
            {
                "title":          p.get("title"),
                "url":            p.get("url"),
                "domain":         p.get("domain"),
                "estimated_price": p.get("estimated_price"),
                "is_valid_ecommerce": p.get("is_valid"),
                "validation_reason": p.get("validation_reason"),
                "source_query":   p.get("source_query"),
                "snippet":        p.get("snippet", "")[:200],
            }
            for p in products
        ],
    }


@app.get(
    "/api/workflow/{thread_id}/gifts",
    tags=["Intermediate Steps"],
    summary="Final ranked gift recommendations (Steps 3–4)",
)
async def get_gifts(thread_id: str):
    """
    Returns the top-3 ranked gift recommendations with:
    - Confidence scores and risk levels
    - Personalised messages (if message generation is complete)
    - Reasoning behind each recommendation
    - Assumptions made
    """
    _require_run(thread_id)
    state = _get_langgraph_state(thread_id)
    if not state or not state.get("recommended_gifts"):
        return {"thread_id": thread_id, "status": "not_yet_available", "message": "Gift ranking not complete yet"}

    gifts = state["recommended_gifts"] or []
    return {
        "thread_id": thread_id,
        "contact_name": state["contact"].get("name"),
        "gift_count": len(gifts),
        "recommended_gifts": gifts,
        "review_status": state.get("status"),
    }


@app.get(
    "/api/workflow/{thread_id}/trace",
    tags=["Intermediate Steps"],
    summary="Full agent trace with metadata",
)
async def get_trace(thread_id: str):
    """
    Full execution trace including:
    - Every node that ran and its output summary
    - Any errors encountered
    - Retry count
    - Current workflow status
    """
    _require_run(thread_id)
    run = _runs[thread_id]
    state = _get_langgraph_state(thread_id)
    steps = _steps.get(thread_id, [])

    total_wall_ms = sum(s.get("duration_ms", 0) for s in steps)
    return {
        "thread_id": thread_id,
        "contact_name": run.get("contact_name"),
        "created_at":   run.get("created_at"),
        "current_status": run.get("status"),
        "error": run.get("error"),
        "steps_completed": len(steps),
        "total_wall_ms": total_wall_ms,
        "step_trace": steps,
        "retry_count": (state or {}).get("retry_count", 0),
        "all_errors": [e for e in (state or {}).get("errors", []) if e],
        "token_usage": _token_summary(state or {}),
        "regenerated_from": run.get("regenerated_from"),
    }


# ── Streaming (SSE) ───────────────────────────────────────────────────────────

@app.get(
    "/api/workflow/{thread_id}/stream",
    tags=["Streaming"],
    summary="SSE stream — one event per node completion",
)
async def stream_workflow(thread_id: str):
    """
    Server-Sent Events stream.  Connect once and receive a JSON event
    for every node that completes.  Stream ends when the workflow
    reaches `pending_review`, `error`, `approved`, or `rejected`.

    Event types:
    - `step`   — a workflow node completed  (`{ type, node, output }`)
    - `status` — workflow status changed    (`{ type, status }`)
    - `done`   — stream is closing          (`{ type, status }`)
    - `error`  — workflow failed            (`{ type, message }`)

    Example (JavaScript):
    ```js
    const es = new EventSource('/api/workflow/<id>/stream')
    es.onmessage = e => console.log(JSON.parse(e.data))
    ```
    """
    _require_run(thread_id)

    async def event_gen() -> AsyncGenerator[str, None]:
        sent = 0
        terminal = {"pending_review", "error", "completed", "approved", "rejected"}

        while True:
            run = _runs.get(thread_id, {})
            steps = _steps.get(thread_id, [])

            # Emit any new steps
            for step in steps[sent:]:
                payload = json.dumps({"type": "step", "node": step["node"], "output": step["output"]})
                yield f"data: {payload}\n\n"
            sent = len(steps)

            status = run.get("status", "unknown")
            if run.get("error"):
                yield f"data: {json.dumps({'type': 'error', 'message': run['error']})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'status': 'error'})}\n\n"
                return

            if status in terminal:
                yield f"data: {json.dumps({'type': 'done', 'status': status})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'status', 'status': status})}\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Helper ───────────────────────────────────────────────────────────────────

def _require_run(thread_id: str) -> None:
    if thread_id not in _runs:
        raise HTTPException(status_code=404, detail=f"Run '{thread_id}' not found")


# ─── Static files ─────────────────────────────────────────────────────────────

_static_build = Path(__file__).parent / "static_build"
if _static_build.exists():
    app.mount("/", StaticFiles(directory=str(_static_build), html=True), name="static")
else:
    _frontend_dir = Path(__file__).parent.parent / "frontend"
    if (_frontend_dir / "index.html").exists():
        @app.get("/", include_in_schema=False)
        async def serve_frontend():
            return FileResponse(str(_frontend_dir / "index.html"))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
