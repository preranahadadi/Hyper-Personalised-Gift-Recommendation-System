"""
LangGraph workflow nodes.

Each node receives the full WorkflowState dict and returns a partial dict
of only the keys it updates.  LangGraph merges these into the running state.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from urllib.parse import quote_plus

import config
from models.schemas import ProfileSignals
from tools.search import build_store_search_queries, score_candidate, search_products, validate_budget
from workflow.state import WorkflowState


# ─── LLM helper ───────────────────────────────────────────────────────────────

def _get_llm():
    try:
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=config.OLLAMA_MODEL,
            base_url=config.OLLAMA_BASE_URL,
            format="json",
            temperature=config.OLLAMA_TEMPERATURE,
            num_ctx=config.OLLAMA_NUM_CTX,
            num_predict=config.OLLAMA_NUM_PREDICT,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Cannot connect to Ollama at {config.OLLAMA_BASE_URL}. "
            "Make sure Ollama is running and the model is pulled. "
            f"Original error: {exc}"
        )


def _invoke_json_llm(prompt: str, node_name: str, required_keys: List[str]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Invoke Ollama and parse a JSON object.  Some local models occasionally
    return prose or omit a top-level key even in JSON mode, so give them one
    focused repair attempt before falling back.
    """
    from langchain_core.messages import HumanMessage

    llm = _get_llm()
    response = llm.invoke([HumanMessage(content=prompt)])
    parsed = _parse_json(response.content)
    missing = [key for key in required_keys if key not in parsed]
    if missing:
        repair_prompt = (
            f"{prompt}\n\nYour previous response was missing these required "
            f"top-level JSON keys: {missing}. Return ONLY one valid JSON object "
            "with all required keys and no markdown."
        )
        response = llm.invoke([HumanMessage(content=repair_prompt)])
        parsed = _parse_json(response.content)
        missing = [key for key in required_keys if key not in parsed]
        if missing:
            raise ValueError(f"Ollama JSON response missing required keys: {missing}")
    return parsed, _extract_usage(response, node_name)


def _extract_usage(response: Any, node_name: str) -> Dict[str, Any]:
    """Pull token counts and Ollama-reported latency out of a LangChain AIMessage."""
    meta = getattr(response, "response_metadata", {}) or {}
    return {
        "node": node_name,
        "model": meta.get("model", config.OLLAMA_MODEL),
        "prompt_tokens": meta.get("prompt_eval_count", 0),
        "completion_tokens": meta.get("eval_count", 0),
        # Ollama reports durations in nanoseconds
        "total_llm_ms": round(meta.get("total_duration", 0) / 1_000_000),
    }


def _parse_json(text: str) -> Dict[str, Any]:
    """Robustly parse JSON from an LLM response that may include markdown fences."""
    # 1. direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2. extract from ```json ... ``` block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3. find first {...} blob
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    raise ValueError(f"Could not parse JSON from LLM response: {text[:300]}")


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _stringify_signal(value: Any) -> str:
    if isinstance(value, dict):
        label = value.get("label") or value.get("signal") or value.get("title")
        assumption = value.get("assumption") or value.get("reason") or value.get("evidence")
        if label and assumption:
            return f"{label} (inferred: {assumption})"
        if label:
            return str(label)
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_string_list(value: Any) -> List[str]:
    return [_stringify_signal(item) for item in _as_list(value) if item not in (None, "")]


def _safe_confidence(value: Any, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(1.0, score))


def _safe_candidate_index(value: Any, total: int) -> int:
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return 0
    return idx if 0 <= idx < total else 0


def _marketplace_search_url(domain: str, query: str) -> str:
    encoded = quote_plus(query)
    path_encoded = quote_plus(query).replace("+", "-")
    if "amazon." in domain:
        return f"https://www.{domain}/s?k={encoded}"
    if "flipkart.com" in domain:
        return f"https://www.flipkart.com/search?q={encoded}"
    if "nykaa.com" in domain:
        return f"https://www.nykaa.com/search/result/?q={encoded}"
    if "myntra.com" in domain:
        return f"https://www.myntra.com/{path_encoded}"
    if "ajio.com" in domain:
        return f"https://www.ajio.com/search/?text={encoded}"
    return f"https://www.{domain}/search?q={encoded}"


def _domains_for_fallback(queries: List[str], country: str) -> List[str]:
    trusted = config.ECOMMERCE_DOMAINS.get(country, config.DEFAULT_ECOMMERCE_DOMAINS)
    mentioned = [domain for domain in trusted if any(domain.lower() in q.lower() for q in queries)]
    ordered = mentioned + [domain for domain in trusted if domain not in mentioned]
    return ordered[: min(4, len(ordered))]


def _domain_for_query(query: str, fallback_domains: List[str]) -> str:
    for domain in fallback_domains:
        if domain.lower() in query.lower():
            return domain
    return fallback_domains[0] if fallback_domains else config.DEFAULT_ECOMMERCE_DOMAINS[0]


def _strip_domain_terms(query: str, domains: List[str]) -> str:
    cleaned = query
    for domain in domains:
        cleaned = re.sub(rf"\bsite:{re.escape(domain)}\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(rf"\b{re.escape(domain)}\b", "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _fallback_product_candidates(queries: List[str], country: str, budget_max: float, currency: str) -> List[Dict[str, Any]]:
    """
    Last-resort candidates for offline/rate-limited search. These are marketplace
    search URLs, so every resulting gift is intentionally marked for review.
    """
    domains = _domains_for_fallback(queries, country)
    candidates: List[Dict[str, Any]] = []
    seed_queries = queries[:3] or [f"professional gift {country} under {budget_max} {currency}"]

    for i, query in enumerate(seed_queries):
        domain = _domain_for_query(query, domains)
        clean_query = _strip_domain_terms(query, domains)
        url = _marketplace_search_url(domain, clean_query)

        candidates.append({
            "title": f"Marketplace search for: {clean_query}",
            "url": url,
            "snippet": (
                "Fallback marketplace search generated because live product search "
                "did not return enough results. Verify the exact product and price."
            ),
            "source_query": query,
            "estimated_price": f"Under {budget_max:g} {currency} - verify price",
            "domain": domain,
            "is_valid": False,
            "validation_reason": "Fallback marketplace search URL only; not a verified exact product page",
        })
    return candidates


def _fallback_candidate_for_gift(
    gift_name: str,
    queries: List[str],
    country: str,
    budget_max: float,
    currency: str,
) -> Dict[str, Any]:
    query = gift_name or (queries[0] if queries else f"professional gift {country} under {budget_max} {currency}")
    domains = _domains_for_fallback([query] + queries, country)
    domain = domains[0] if domains else config.DEFAULT_ECOMMERCE_DOMAINS[0]
    return {
        "title": f"Marketplace search for: {query}",
        "url": _marketplace_search_url(domain, query),
        "snippet": "Fallback link generated because the selected gift did not have a product URL.",
        "source_query": query,
        "estimated_price": f"Under {budget_max:g} {currency} - verify price",
        "domain": domain,
        "is_valid": False,
        "validation_reason": "Fallback marketplace search URL only; not a verified exact product page",
    }


def _gift_from_candidate(
    candidate: Dict[str, Any],
    rank: int,
    signals: Dict[str, Any],
    reason: str = "Selected from available product search results.",
) -> Dict[str, Any]:
    gift = {
        "rank": rank,
        "gift_name": candidate.get("title", "Review product option"),
        "product_url": candidate.get("url", ""),
        "store": candidate.get("domain", ""),
        "estimated_price": candidate.get("estimated_price") or "Price not found - check link",
        "why_this_gift": reason,
        "personalisation_reasoning": ", ".join(signals.get("strong_signals", [])[:2]),
        "personalised_message": "",
        "confidence_score": 0.45 if candidate.get("is_valid") else 0.35,
        "risk_level": "medium" if candidate.get("is_valid") else "high",
        "assumptions": ["Added as a fallback because the model did not return three complete linked gifts"],
    }
    return _apply_gift_guardrails(gift, candidate, signals)


_INAPPROPRIATE_GIFT_TERMS = [
    "romantic", "date night", "love", "valentine", "lingerie", "intimate",
    "spouse", "husband", "wife", "medical", "medicine", "supplement",
    "weight loss", "diet", "political", "religious",
]


def _contains_guardrail_term(text: str) -> bool:
    lowered = text.lower()
    terms = list(config.SENSITIVE_ATTRIBUTES) + _INAPPROPRIATE_GIFT_TERMS
    return any(re.search(rf"\b{re.escape(term.lower())}\b", lowered) for term in terms)


def _apply_gift_guardrails(
    gift: Dict[str, Any],
    candidate: Dict[str, Any],
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deterministic safety pass after LLM ranking/message generation.
    The prompts ask for these rules; this pass makes the important penalties
    explicit even when a local model is overconfident.
    """
    gift = dict(gift)
    assumptions = _as_string_list(gift.get("assumptions"))
    strong_signals = _as_string_list(signals.get("strong_signals"))
    weak_signals = _as_string_list(signals.get("weak_signals"))
    combined_text = " ".join(
        str(gift.get(key, ""))
        for key in (
            "gift_name",
            "why_this_gift",
            "personalisation_reasoning",
            "personalised_message",
        )
    )

    if _contains_guardrail_term(combined_text):
        gift["confidence_score"] = min(_safe_confidence(gift.get("confidence_score")), 0.35)
        gift["risk_level"] = "high"
        assumptions.append(
            "Guardrail review required: recommendation may touch sensitive or overly personal attributes"
        )

    validation_reason = str(candidate.get("validation_reason", ""))
    candidate_title = str(candidate.get("title", ""))
    validation_lower = validation_reason.lower()
    if (
        "fallback marketplace search" in validation_lower
        or "search/category page" in validation_lower
        or candidate_title.startswith("Marketplace search for:")
    ):
        gift["confidence_score"] = min(_safe_confidence(gift.get("confidence_score")), 0.55)
        gift["risk_level"] = "medium" if gift.get("risk_level") == "low" else gift.get("risk_level", "medium")
        assumptions.append(
            "Product is a marketplace search URL, not a verified exact product page; human should choose and verify the final product"
        )

    if not strong_signals:
        gift["confidence_score"] = min(_safe_confidence(gift.get("confidence_score")), 0.55)
        gift["risk_level"] = "medium" if gift.get("risk_level") == "low" else gift.get("risk_level", "medium")
        assumptions.append(
            "Profile context is weak because no direct strong gifting signal was available"
        )

    if weak_signals and not assumptions:
        assumptions.append("Some personalisation is inferred from weak professional signals")

    gift["confidence_score"] = _safe_confidence(gift.get("confidence_score"))
    gift["assumptions"] = list(dict.fromkeys(assumptions))
    return gift


# ─── Node 1: extract_signals ──────────────────────────────────────────────────

def extract_signals_node(state: WorkflowState) -> Dict[str, Any]:
    contact = state["contact"]
    profile = contact["linkedin_profile"]

    posts_str = "\n".join(f"  - {p}" for p in profile.get("recent_posts", []))
    comments_str = "\n".join(f"  - {c}" for c in profile.get("recent_comments", []))
    topics_str = ", ".join(profile.get("engaged_topics", []))
    exp_str = "\n".join(
        f"  - {e['title']} at {e['company']}: {e['description']}"
        for e in profile.get("experience", [])
    )

    prompt = f"""You are a professional relationship manager analysing a LinkedIn profile to identify gift signals.

Contact: {contact['name']}, {contact['role']} at {contact['company']}
Headline: {profile['headline']}
About: {profile['about']}
Experience:
{exp_str}
Recent posts:
{posts_str}
Recent comments:
{comments_str}
Engaged topics: {topics_str}

Extract gifting signals from this profile.

OUTPUT: respond with ONLY valid JSON matching this exact structure (no markdown, no explanation):
{{
  "strong_signals": ["explicitly stated interest or hobby from posts/comments/topics"],
  "weak_signals": ["inferred professional preference — label each as inferred"],
  "signals_to_avoid": ["Do not infer religion, politics, health, family status, ethnicity, gender, or other sensitive personal attributes"]
}}

Rules:
- strong_signals: backed by direct evidence (posts, comments, explicit topic mention)
- weak_signals: reasonable inference from role/industry — ALWAYS note the assumption
- signals_to_avoid: MUST always contain the guardrail statement above; add more if the profile triggers any sensitive inference
- Do NOT include age, health conditions, marital status, or anything sensitive
- Maximum 5 strong signals, 3 weak signals"""

    try:
        parsed, usage = _invoke_json_llm(
            prompt,
            "extract_signals",
            ["strong_signals", "weak_signals", "signals_to_avoid"],
        )

        # Ensure guardrail is always present
        avoid = _as_string_list(parsed.get("signals_to_avoid"))
        guardrail = "Do not infer religion, politics, health, family status, ethnicity, gender, or other sensitive personal attributes"
        if not any(guardrail.lower() in s.lower() for s in avoid):
            avoid.insert(0, guardrail)

        signals = {
            "strong_signals": _as_string_list(parsed.get("strong_signals")),
            "weak_signals": _as_string_list(parsed.get("weak_signals")),
            "signals_to_avoid": avoid,
        }
        return {"profile_signals": signals, "status": "signals_extracted", "errors": [], "llm_usage": [usage]}

    except Exception as exc:
        # Fallback: use engaged_topics as strong signals
        fallback_signals = {
            "strong_signals": profile.get("engaged_topics", [])[:5],
            "weak_signals": [f"Inferred from role: {contact['role']} at {contact['company']}"],
            "signals_to_avoid": [
                "Do not infer religion, politics, health, family status, ethnicity, gender, or other sensitive personal attributes"
            ],
        }
        return {
            "profile_signals": fallback_signals,
            "status": "signals_extracted_fallback",
            "errors": [f"Signal extraction LLM error (using fallback): {exc}"],
            "llm_usage": [],
        }


# ─── Node 2: search_products ──────────────────────────────────────────────────

def search_products_node(state: WorkflowState) -> Dict[str, Any]:
    contact = state["contact"]
    gift_ctx = contact["gift_context"]
    signals = state.get("profile_signals", {})
    retry_count = state.get("retry_count", 0)

    strong = signals.get("strong_signals", [])
    weak = signals.get("weak_signals", [])
    country = gift_ctx["country"]
    currency = gift_ctx["currency"]
    budget_max = gift_ctx["budget_max"]
    budget_min = gift_ctx["budget_min"]
    occasion = gift_ctx["occasion"]

    # Generate search queries via LLM
    signals_text = "\n".join(f"  - {s}" for s in (strong + weak))
    ecomm_sites = ", ".join(config.ECOMMERCE_DOMAINS.get(country, ["amazon.com"]))

    query_prompt = f"""You are a gift procurement specialist finding real purchasable products online.

Contact: {contact['name']}, {contact['role']} at {contact['company']}
Country: {country}
Occasion: {occasion}
Budget: {currency} {budget_min}–{budget_max}
Gifting signals:
{signals_text}
Preferred e-commerce sites: {ecomm_sites}

Generate {config.MAX_QUERIES_PER_CONTACT} targeted web search queries to find real, buyable gift products.

OUTPUT: respond with ONLY valid JSON:
{{
  "queries": [
    "query 1 — specific product name + country + price range",
    "query 2",
    "query 3",
    "query 4"
  ]
}}

Rules:
- Each query should target a specific product category matched to a gifting signal
- Include country or site name for localised results
- Include price range (e.g. "under {budget_max} {currency}")
- Keep queries concrete — avoid generic terms
- Example: "premium cricket bat gift buy India amazon under 5000 INR"
- Do NOT hallucinate product names; let the search return real ones
- Vary the signal each query focuses on"""

    query_prompt += """

Also include a top-level "product_intents" array. These must be reusable product categories, not invented product names.
Examples: "Rust programming book", "premium cricket accessory", "desk accessory for software engineer".
The backend will expand these intents across trusted stores and validate real product URLs."""

    queries: List[str] = []
    product_intents: List[str] = []
    search_errors: List[str] = []
    _query_llm_usage: List[Dict[str, Any]] = []
    try:
        parsed, usage = _invoke_json_llm(
            query_prompt,
            "search_products",
            ["queries"],
        )
        _query_llm_usage = [usage]
        queries = [str(q) for q in _as_list(parsed.get("queries")) if str(q).strip()]
        product_intents = [str(q) for q in _as_list(parsed.get("product_intents")) if str(q).strip()]
    except Exception as exc:
        search_errors.append(f"Search-query Ollama error (using fallback queries): {exc}")
        # Fallback queries from signals
        for sig in strong[:3]:
            sig_clean = sig.split(":")[0].strip().lower()
            product_intents.append(f"{sig_clean} gift")
            queries.append(
                f"buy {sig_clean} gift {country} under {budget_max} {currency}"
            )
        if not product_intents:
            product_intents.append(f"{contact['role']} professional gift")
        queries.append(
            f"{contact['role']} professional gift {country} under {budget_max} {currency}"
        )

    if not product_intents:
        product_intents = queries[:3] or [f"{contact['role']} professional gift"]

    store_queries = build_store_search_queries(product_intents, country, budget_max, currency)
    queries = list(dict.fromkeys(queries + store_queries))

    if not queries:
        queries = [f"professional gift {country} under {budget_max} {currency}"]

    candidates = []
    # Run DuckDuckGo searches
    try:
        raw_candidates = search_products(queries, country)
        for c in raw_candidates:
            candidates.append(c.model_dump())
    except ImportError as exc:
        search_errors.append(str(exc))
    except Exception as exc:
        search_errors.append(f"Search error: {exc}")

    # If we still have fewer than MIN_PRODUCTS valid e-comm results, build fallback
    valid_count = sum(1 for c in candidates if c.get("is_valid"))
    if valid_count < config.MIN_PRODUCTS_NEEDED and retry_count < config.MAX_RETRY_COUNT:
        # Widen search to generic e-commerce query on retry
        retry_queries = build_store_search_queries(
            product_intents + [f"{contact['role']} professional gift"],
            country,
            budget_max,
            currency,
            max_queries=10,
        )
        try:
            extra = search_products(retry_queries, country)
            for c in extra:
                if c.url not in {x["url"] for x in candidates}:
                    candidates.append(c.model_dump())
        except Exception:
            pass

    valid_count = sum(1 for c in candidates if c.get("is_valid"))
    if valid_count < config.MIN_PRODUCTS_NEEDED:
        fallback_candidates = _fallback_product_candidates(
            queries,
            country,
            budget_max,
            currency,
        )
        existing_urls = {c.get("url") for c in candidates}
        for c in fallback_candidates:
            if c["url"] not in existing_urls:
                candidates.append(c)
        search_errors.append(
            "Live search returned fewer than the minimum valid products; added fallback marketplace search URLs for review."
        )

    for c in candidates:
        c["candidate_score"] = score_candidate(c, country, budget_min, budget_max)
    candidates.sort(key=lambda c: c.get("candidate_score", 0), reverse=True)
    candidates_for_ranking = candidates[:20]

    search_trace = {
        "queries_used": queries,
        "product_intents": product_intents,
        "products_considered_count": len(candidates),
    }

    return {
        "search_queries": queries,
        "products_considered": candidates_for_ranking,
        "search_trace": search_trace,
        "retry_count": retry_count + 1,
        "status": "products_searched",
        "errors": search_errors,
        "llm_usage": _query_llm_usage,
    }


# ─── Node 3: rank_gifts ───────────────────────────────────────────────────────

def rank_gifts_node(state: WorkflowState) -> Dict[str, Any]:
    contact = state["contact"]
    gift_ctx = contact["gift_context"]
    signals = state.get("profile_signals", {})
    candidates = state.get("products_considered", [])

    if not candidates:
        return {
            "recommended_gifts": [],
            "status": "ranking_failed_no_products",
            "errors": ["No product candidates found to rank."],
            "llm_usage": [],
        }

    # Summarise candidates for the prompt (keep it token-efficient)
    candidates_text = ""
    for i, c in enumerate(candidates):
        candidates_text += (
            f"[{i}] {c['title']}\n"
            f"    URL: {c['url']}\n"
            f"    Price: {c.get('estimated_price', 'unknown')}\n"
            f"    Valid e-commerce: {c['is_valid']}\n"
            f"    Snippet: {c['snippet'][:120]}\n\n"
        )

    strong_str = "\n".join(f"  - {s}" for s in signals.get("strong_signals", []))
    weak_str = "\n".join(f"  - {s}" for s in signals.get("weak_signals", []))

    prompt = f"""You are a senior gift recommendation specialist. Select and rank the top 3 gifts from the candidates below.

Contact: {contact['name']}, {contact['role']} at {contact['company']}, {contact['location']}
Occasion: {gift_ctx['occasion']}
Relationship: {contact['relationship_context']['relationship_type']}
Budget: {gift_ctx['currency']} {gift_ctx['budget_min']}–{gift_ctx['budget_max']}

Strong gifting signals:
{strong_str}

Weak signals (inferred):
{weak_str}

Product candidates:
{candidates_text}

Select exactly 3 gifts. Prefer products from valid e-commerce domains. Favour price-within-budget products.

OUTPUT: respond with ONLY valid JSON:
{{
  "ranked_gifts": [
    {{
      "rank": 1,
      "candidate_index": 0,
      "gift_name": "clear product name (not just URL)",
      "store": "Amazon / Flipkart / etc.",
      "estimated_price": "as found or estimated",
      "why_this_gift": "1-2 sentences: why this matches the contact",
      "personalisation_reasoning": "which specific signals justify this choice",
      "confidence_score": 0.85,
      "risk_level": "low",
      "assumptions": ["assumption 1 if any"]
    }}
  ]
}}

Rules:
- rank 1 is the best match
- confidence_score: 0.9+ if strong signal match; 0.6-0.89 if inferred; below 0.6 if speculative
- risk_level: "low" = safe professional gift; "medium" = some assumptions; "high" = speculative
- Do NOT select gifts related to religion, politics, health, ethnicity, family status
- If fewer than 3 valid exact product pages exist, include best available with lower confidence and manual-review assumptions
- Use the product URL from the candidate — do not invent URLs"""

    try:
        parsed, usage = _invoke_json_llm(
            prompt,
            "rank_gifts",
            ["ranked_gifts"],
        )
        raw_ranked = _as_list(parsed.get("ranked_gifts"))

        # Map back to full product URL from candidates list
        gifts = []
        used_candidate_indexes = set()
        for g in raw_ranked[:3]:
            if not isinstance(g, dict):
                continue
            idx = _safe_candidate_index(g.get("candidate_index", 0), len(candidates))
            candidate = candidates[idx] if candidates else {}
            used_candidate_indexes.add(idx)
            url = candidate.get("url", "")
            domain = candidate.get("domain", "")
            if not url:
                candidate = _fallback_candidate_for_gift(
                    str(g.get("gift_name") or candidate.get("title") or ""),
                    state.get("search_queries") or [],
                    gift_ctx["country"],
                    gift_ctx["budget_max"],
                    gift_ctx["currency"],
                )
                url = candidate.get("url", "")
                domain = candidate.get("domain", "")
                g["confidence_score"] = min(_safe_confidence(g.get("confidence_score")), 0.45)
                g.setdefault("assumptions", []).append(
                    "Original ranked item did not include a usable product URL; fallback marketplace link added for review"
                )

            # Budget compliance check
            budget_ok = validate_budget(
                g.get("estimated_price") or candidate.get("estimated_price"),
                gift_ctx["budget_min"],
                gift_ctx["budget_max"],
            )
            if not budget_ok:
                g["confidence_score"] = max(0.1, _safe_confidence(g.get("confidence_score")) - 0.2)
                g.setdefault("assumptions", []).append(
                    "Price may be outside stated budget — verify before purchase"
                )

            gift = {
                "rank": g.get("rank", len(gifts) + 1),
                "gift_name": g.get("gift_name", candidate.get("title", "Unknown product")),
                "product_url": url,
                "store": g.get("store", domain),
                "estimated_price": (
                    g.get("estimated_price")
                    or candidate.get("estimated_price")
                    or "Price not found — check link"
                ),
                "why_this_gift": g.get("why_this_gift", ""),
                "personalisation_reasoning": g.get("personalisation_reasoning", ""),
                "personalised_message": "",  # filled in next node
                "confidence_score": _safe_confidence(g.get("confidence_score")),
                "risk_level": g.get("risk_level", "medium"),
                "assumptions": _as_list(g.get("assumptions")),
            }
            gift = _apply_gift_guardrails(gift, candidate, signals)
            gifts.append(gift)

        if len(gifts) < 3:
            ordered_candidates = sorted(
                enumerate(candidates),
                key=lambda item: (not item[1].get("is_valid"), item[0]),
            )
            for idx, candidate in ordered_candidates:
                if len(gifts) >= 3:
                    break
                if idx in used_candidate_indexes:
                    continue
                if not candidate.get("url"):
                    continue
                gifts.append(_gift_from_candidate(
                    candidate,
                    len(gifts) + 1,
                    signals,
                    "Added from search results because the ranking model returned fewer than three complete linked gifts.",
                ))

        if len(gifts) < 3:
            fallback_candidate = _fallback_candidate_for_gift(
                f"{contact['role']} professional gift {gift_ctx['country']} under {gift_ctx['budget_max']} {gift_ctx['currency']}",
                state.get("search_queries") or [],
                gift_ctx["country"],
                gift_ctx["budget_max"],
                gift_ctx["currency"],
            )
            while len(gifts) < 3:
                gifts.append(_gift_from_candidate(
                    fallback_candidate,
                    len(gifts) + 1,
                    signals,
                    "Fallback marketplace link added because product search did not provide enough complete linked options.",
                ))

        if not gifts:
            raise ValueError("Ollama returned no usable ranked_gifts")

        return {
            "recommended_gifts": gifts,
            "status": "gifts_ranked",
            "errors": [],
            "llm_usage": [usage],
        }

    except Exception as exc:
        # Emergency fallback: pick top 3 valid e-commerce results
        valid = [c for c in candidates if c.get("is_valid")][:3]
        if not valid:
            valid = candidates[:3]

        fallback_gifts = [
            {
                "rank": i + 1,
                "gift_name": c["title"],
                "product_url": c["url"],
                "store": c.get("domain", ""),
                "estimated_price": c.get("estimated_price") or "See link",
                "why_this_gift": "Selected based on search relevance (LLM ranking unavailable).",
                "personalisation_reasoning": ", ".join(signals.get("strong_signals", [])[:2]),
                "personalised_message": "",
                "confidence_score": 0.4,
                "risk_level": "high",
                "assumptions": ["LLM ranking failed — manual review strongly recommended"],
            }
            for i, c in enumerate(valid)
        ]
        fallback_gifts = [
            _apply_gift_guardrails(gift, candidate, signals)
            for gift, candidate in zip(fallback_gifts, valid)
        ]
        return {
            "recommended_gifts": fallback_gifts,
            "status": "gifts_ranked_fallback",
            "errors": [f"Ranking LLM error (using fallback): {exc}"],
            "llm_usage": [],
        }


# ─── Node 4: generate_messages ───────────────────────────────────────────────

def generate_messages_node(state: WorkflowState) -> Dict[str, Any]:
    contact = state["contact"]
    gifts = state.get("recommended_gifts", [])
    tone = state.get("tone", "professional")

    if not gifts:
        return {"recommended_gifts": [], "status": "messages_skipped", "errors": [], "llm_usage": []}

    tone_guide = {
        "professional": "professional, warm, and concise — appropriate for a business relationship",
        "warm": "warm, friendly, and personal — like a message from a trusted colleague",
        "formal": "formal and respectful — appropriate for a senior executive or new relationship",
    }.get(tone, "professional, warm, and concise")

    gifts_summary = "\n".join(
        f"Gift {g['rank']}: {g['gift_name']} — {g['why_this_gift']}"
        for g in gifts
    )

    prompt = f"""You are a professional relationship manager. Write a short, personalised gift note for each recommended gift.

Contact: {contact['name']}, {contact['role']} at {contact['company']}
Occasion: {contact['gift_context']['occasion']}
Relationship: {contact['relationship_context']['relationship_type']}
Business goal: {contact['relationship_context']['business_goal']}
Tone: {tone_guide}

Gifts to write notes for:
{gifts_summary}

OUTPUT: respond with ONLY valid JSON:
{{
  "messages": [
    {{"rank": 1, "message": "2-3 sentence personalised note"}},
    {{"rank": 2, "message": "2-3 sentence personalised note"}},
    {{"rank": 3, "message": "2-3 sentence personalised note"}}
  ]
}}

Rules:
- Each note: 2-3 sentences maximum
- Reference the occasion naturally
- Do NOT mention religion, politics, health, family, ethnicity, or sensitive attributes
- The note should feel personal but not intrusive
- Sign off warmly but professionally
- Do NOT invent facts about the contact not present in the profile"""

    try:
        parsed, usage = _invoke_json_llm(
            prompt,
            "generate_messages",
            ["messages"],
        )
        messages_map = {}
        for m in _as_list(parsed.get("messages")):
            if isinstance(m, dict) and "rank" in m and "message" in m:
                messages_map[m["rank"]] = str(m["message"])

        updated_gifts = []
        for g in gifts:
            g = dict(g)
            g["personalised_message"] = messages_map.get(
                g["rank"],
                f"Thank you for your time, {contact['name']}. I hope this small token reflects our appreciation. Looking forward to our continued partnership.",
            )
            g = _apply_gift_guardrails(g, {}, state.get("profile_signals", {}) or {})
            updated_gifts.append(g)

        return {
            "recommended_gifts": updated_gifts,
            "status": "messages_generated",
            "errors": [],
            "llm_usage": [usage],
        }

    except Exception as exc:
        # Fallback generic message
        updated_gifts = []
        for g in gifts:
            g = dict(g)
            g["personalised_message"] = (
                f"Dear {contact['name']}, thank you for your time. "
                f"I hope this small gift marks our {contact['gift_context']['occasion'].lower()} on a positive note. "
                "Looking forward to staying in touch."
            )
            updated_gifts.append(g)
        return {
            "recommended_gifts": updated_gifts,
            "status": "messages_generated_fallback",
            "errors": [f"Message generation LLM error (using fallback): {exc}"],
            "llm_usage": [],
        }


# ─── Node 5: human_review (interrupt point) ───────────────────────────────────

def human_review_node(state: WorkflowState) -> Dict[str, Any]:
    """
    This node is used as the interrupt point (interrupt_before in graph.py).
    When the workflow is resumed the human_action / human_edits / human_feedback
    keys will already be in the state (set via compiled.update_state).
    """
    action = state.get("human_action", "approve")

    if action == "approve":
        return {"status": "approved", "errors": []}

    if action == "reject":
        return {"status": "rejected", "recommended_gifts": [], "errors": []}

    if action == "edit" and state.get("human_edits"):
        return {
            "recommended_gifts": state["human_edits"],
            "status": "edited",
            "errors": [],
        }

    # regenerate — caller is expected to re-run the full pipeline with feedback
    return {"status": "regenerating", "errors": []}


# ─── Node 6: finalize ─────────────────────────────────────────────────────────

def finalize_node(state: WorkflowState) -> Dict[str, Any]:
    return {"status": "completed", "errors": []}
