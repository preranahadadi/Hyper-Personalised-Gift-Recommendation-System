from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional

from typing_extensions import TypedDict


class WorkflowState(TypedDict):
    # ── Input ────────────────────────────────────────────────────────────────
    contact: Dict[str, Any]                  # serialised Contact model

    # ── Step outputs ─────────────────────────────────────────────────────────
    profile_signals: Optional[Dict[str, Any]]           # ProfileSignals dict
    search_queries: Optional[List[str]]
    products_considered: Optional[List[Dict[str, Any]]] # List[ProductCandidate] dicts
    recommended_gifts: Optional[List[Dict[str, Any]]]   # List[GiftRecommendation] dicts

    # ── Human review inputs ───────────────────────────────────────────────────
    human_action: Optional[str]              # approve | reject | edit | regenerate
    human_edits: Optional[List[Dict[str, Any]]]
    human_feedback: Optional[str]
    tone: Optional[str]                      # professional | warm | formal

    # ── Metadata ──────────────────────────────────────────────────────────────
    workflow_id: str
    status: str
    errors: Annotated[List[str], operator.add]        # appended across nodes
    llm_usage: Annotated[List[Dict[str, Any]], operator.add]  # per-call token + latency log
    retry_count: int
    search_trace: Optional[Dict[str, Any]]
