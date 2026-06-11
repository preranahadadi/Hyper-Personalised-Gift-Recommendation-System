"""
LangGraph StateGraph definition for the gift recommendation workflow.

Flow:
  extract_signals → search_products → rank_gifts → generate_messages
                                                            ↓
                                                   [interrupt_before]
                                                            ↓
                                                     human_review
                                                            ↓
                                                        finalize → END

Human-in-the-loop:
  The graph is compiled with interrupt_before=["human_review"].
  After generate_messages, the workflow pauses and the state is available
  for the caller to inspect. The caller then:
    1. compiled.update_state(config, {"human_action": "approve|reject|edit|regenerate", ...})
    2. compiled.invoke(None, config)   # resume
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from workflow.nodes import (
    extract_signals_node,
    finalize_node,
    generate_messages_node,
    human_review_node,
    rank_gifts_node,
    search_products_node,
)
from workflow.state import WorkflowState


def _should_retry_search(state: WorkflowState) -> str:
    """Route back to search if we got too few valid products and haven't hit the retry cap."""
    import config
    candidates = state.get("products_considered", [])
    valid_count = sum(1 for c in candidates if c.get("is_valid"))
    retry_count = state.get("retry_count", 0)
    if valid_count < config.MIN_PRODUCTS_NEEDED and retry_count < config.MAX_RETRY_COUNT:
        return "retry"
    return "proceed"


def build_graph() -> StateGraph:
    g = StateGraph(WorkflowState)

    g.add_node("extract_signals", extract_signals_node)
    g.add_node("search_products", search_products_node)
    g.add_node("rank_gifts", rank_gifts_node)
    g.add_node("generate_messages", generate_messages_node)
    g.add_node("human_review", human_review_node)
    g.add_node("finalize", finalize_node)

    g.set_entry_point("extract_signals")
    g.add_edge("extract_signals", "search_products")
    g.add_conditional_edges(
        "search_products",
        _should_retry_search,
        {"retry": "search_products", "proceed": "rank_gifts"},
    )
    g.add_edge("rank_gifts", "generate_messages")
    g.add_edge("generate_messages", "human_review")
    g.add_edge("human_review", "finalize")
    g.add_edge("finalize", END)

    return g


# Singleton compiled graph + in-memory checkpointer
_memory = MemorySaver()

compiled_graph = build_graph().compile(
    checkpointer=_memory,
    interrupt_before=["human_review"],  # pause after generate_messages
)
