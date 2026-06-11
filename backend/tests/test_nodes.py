"""
Tests for workflow/nodes.py — JSON parsing, signal extraction fallback,
ranking fallback, and message generation fallback.

All LLM calls are mocked so these tests run without Ollama.
"""
import pytest
from unittest.mock import MagicMock, patch

from workflow.nodes import (
    _apply_gift_guardrails,
    _parse_json,
    extract_signals_node,
    rank_gifts_node,
    generate_messages_node,
)


# ── _parse_json ────────────────────────────────────────────────────────────────

class TestParseJson:
    def test_clean_json_object(self):
        result = _parse_json('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_clean_json_array(self):
        result = _parse_json('[{"a": 1}, {"b": 2}]')
        assert result == [{"a": 1}, {"b": 2}]

    def test_markdown_fence_json(self):
        text = '```json\n{"strong_signals": ["cricket"]}\n```'
        result = _parse_json(text)
        assert result == {"strong_signals": ["cricket"]}

    def test_markdown_fence_no_lang(self):
        text = '```\n{"key": "val"}\n```'
        result = _parse_json(text)
        assert result == {"key": "val"}

    def test_json_embedded_in_prose(self):
        text = 'Here is the analysis: {"strong_signals": ["hiking"]} — end of response'
        result = _parse_json(text)
        assert result == {"strong_signals": ["hiking"]}

    def test_whitespace_before_after(self):
        result = _parse_json('   \n{"a": 1}\n   ')
        assert result == {"a": 1}

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            _parse_json("this is definitely not JSON at all")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _parse_json("")


# ── Shared fixtures ────────────────────────────────────────────────────────────

SAMPLE_CONTACT = {
    "name": "Aarav Mehta",
    "role": "VP Sales",
    "company": "Acme Corp",
    "location": "Bengaluru, India",
    "linkedin_profile": {
        "headline": "VP Sales at Acme Corp | Enterprise SaaS",
        "about": "Building SaaS teams.",
        "experience": [{"title": "VP Sales", "company": "Acme Corp", "description": "GTM expansion"}],
        "recent_posts": ["Great cricket match yesterday!"],
        "recent_comments": ["Cricket teaches leadership."],
        "engaged_topics": ["Cricket", "SaaS GTM", "Revenue leadership"],
    },
    "relationship_context": {
        "relationship_type": "Prospective customer",
        "last_interaction": "Positive discovery call last week",
        "business_goal": "Nurture relationship before follow-up meeting",
    },
    "gift_context": {
        "occasion": "Post-meeting thank you",
        "budget_min": 3000,
        "budget_max": 5000,
        "currency": "INR",
        "country": "India",
    },
}

BASE_STATE = {
    "contact": SAMPLE_CONTACT,
    "profile_signals": None,
    "search_queries": None,
    "products_considered": None,
    "recommended_gifts": None,
    "human_action": None,
    "human_edits": None,
    "human_feedback": None,
    "tone": "professional",
    "workflow_id": "test-thread-001",
    "status": "started",
    "errors": [],
    "llm_usage": [],
    "retry_count": 0,
    "search_trace": None,
}


def _make_mock_response(content: str, prompt_tokens: int = 100, completion_tokens: int = 150) -> MagicMock:
    """Build a fake LangChain AIMessage with Ollama-style response_metadata."""
    mock = MagicMock()
    mock.content = content
    mock.response_metadata = {
        "model": "llama3.2",
        "prompt_eval_count": prompt_tokens,
        "eval_count": completion_tokens,
        "total_duration": 2_000_000_000,  # 2 seconds in nanoseconds
    }
    return mock


# ── extract_signals_node ──────────────────────────────────────────────────────

class TestExtractSignalsNode:

    def test_success_path_returns_signals(self):
        good_response = _make_mock_response(
            '{"strong_signals": ["Interested in cricket"], '
            '"weak_signals": ["May appreciate leadership books"], '
            '"signals_to_avoid": ["Do not infer religion, politics, health, family status"]}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = good_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = extract_signals_node({**BASE_STATE})

        assert result["status"] == "signals_extracted"
        assert "Interested in cricket" in result["profile_signals"]["strong_signals"]
        assert result["errors"] == []

    def test_success_path_records_token_usage(self):
        good_response = _make_mock_response(
            '{"strong_signals": ["Cricket"], "weak_signals": [], '
            '"signals_to_avoid": ["Do not infer sensitive attributes"]}',
            prompt_tokens=120, completion_tokens=80,
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = good_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = extract_signals_node({**BASE_STATE})

        assert len(result["llm_usage"]) == 1
        usage = result["llm_usage"][0]
        assert usage["node"] == "extract_signals"
        assert usage["prompt_tokens"] == 120
        assert usage["completion_tokens"] == 80
        assert usage["model"] == "llama3.2"

    def test_guardrail_always_injected(self):
        """signals_to_avoid must always contain the sensitive-attribute guardrail."""
        good_response = _make_mock_response(
            '{"strong_signals": ["Cricket"], "weak_signals": [], "signals_to_avoid": []}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = good_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = extract_signals_node({**BASE_STATE})

        avoid = result["profile_signals"]["signals_to_avoid"]
        assert any("religion" in s.lower() or "sensitive" in s.lower() for s in avoid)

    def test_llm_failure_uses_fallback(self):
        with patch("workflow.nodes._get_llm", side_effect=RuntimeError("Ollama not running")):
            result = extract_signals_node({**BASE_STATE})

        assert result["status"] == "signals_extracted_fallback"
        signals = result["profile_signals"]
        # Fallback uses engaged_topics as strong signals
        assert len(signals["strong_signals"]) > 0
        assert any("Cricket" in s or "SaaS GTM" in s or "Revenue leadership" in s
                   for s in signals["strong_signals"])

    def test_fallback_empty_llm_usage(self):
        with patch("workflow.nodes._get_llm", side_effect=RuntimeError("down")):
            result = extract_signals_node({**BASE_STATE})
        assert result["llm_usage"] == []

    def test_fallback_populates_error(self):
        with patch("workflow.nodes._get_llm", side_effect=RuntimeError("Ollama not running")):
            result = extract_signals_node({**BASE_STATE})
        assert len(result["errors"]) > 0
        assert "fallback" in result["errors"][0].lower()


# ── rank_gifts_node ────────────────────────────────────────────────────────────

class TestRankGiftsNode:

    def _state_with_products(self, products):
        return {
            **BASE_STATE,
            "profile_signals": {
                "strong_signals": ["Interested in cricket"],
                "weak_signals": ["May appreciate leadership books"],
                "signals_to_avoid": ["Do not infer religion or sensitive attributes"],
            },
            "products_considered": products,
            "status": "products_searched",
        }

    def test_empty_candidates_returns_empty_gifts(self):
        result = rank_gifts_node(self._state_with_products([]))
        assert result["recommended_gifts"] == []
        assert result["status"] == "ranking_failed_no_products"
        assert result["llm_usage"] == []

    def test_success_path_returns_three_gifts(self):
        raw_candidates = [
            {"title": f"Cricket Gift {i}", "url": f"https://amazon.in/p{i}",
             "snippet": f"₹{3000 + i*500}", "estimated_price": f"₹{3000 + i*500}",
             "domain": "amazon.in", "is_valid": True, "source_query": "cricket gift india",
             "validation_reason": "Recognised e-commerce domain"}
            for i in range(5)
        ]
        ranked_response = _make_mock_response(
            '{"ranked_gifts": ['
            '{"rank": 1, "candidate_index": 0, "gift_name": "SG Cricket Bat", "store": "Amazon.in", '
            '"estimated_price": "₹3,999", "why_this_gift": "Matches cricket interest.", '
            '"personalisation_reasoning": "Strong signal: cricket", "confidence_score": 0.9, '
            '"risk_level": "low", "assumptions": []},'
            '{"rank": 2, "candidate_index": 1, "gift_name": "Cricket Ball Set", "store": "Amazon.in", '
            '"estimated_price": "₹3,500", "why_this_gift": "Good gift.", '
            '"personalisation_reasoning": "Cricket signal", "confidence_score": 0.8, '
            '"risk_level": "low", "assumptions": []},'
            '{"rank": 3, "candidate_index": 2, "gift_name": "Cricket Gloves", "store": "Amazon.in", '
            '"estimated_price": "₹4,000", "why_this_gift": "Practical gift.", '
            '"personalisation_reasoning": "Cricket signal", "confidence_score": 0.75, '
            '"risk_level": "low", "assumptions": []}'
            ']}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = ranked_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = rank_gifts_node(self._state_with_products(raw_candidates))

        assert result["status"] == "gifts_ranked"
        assert len(result["recommended_gifts"]) == 3
        assert result["recommended_gifts"][0]["rank"] == 1

    def test_success_path_records_token_usage(self):
        raw_candidates = [
            {"title": "Gift", "url": "https://amazon.in/p1", "snippet": "₹3,999",
             "estimated_price": "₹3,999", "domain": "amazon.in", "is_valid": True,
             "source_query": "q", "validation_reason": "ok"}
        ]
        ranked_response = _make_mock_response(
            '{"ranked_gifts": [{"rank": 1, "candidate_index": 0, "gift_name": "Gift", '
            '"store": "Amazon.in", "estimated_price": "₹3,999", "why_this_gift": "good", '
            '"personalisation_reasoning": "signal", "confidence_score": 0.8, "risk_level": "low", '
            '"assumptions": []}]}',
            prompt_tokens=200, completion_tokens=150,
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = ranked_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = rank_gifts_node(self._state_with_products(raw_candidates))

        assert len(result["llm_usage"]) == 1
        assert result["llm_usage"][0]["node"] == "rank_gifts"
        assert result["llm_usage"][0]["prompt_tokens"] == 200

    def test_llm_failure_uses_valid_ecommerce_fallback(self):
        raw_candidates = [
            {"title": f"Product {i}", "url": f"https://amazon.in/p{i}",
             "snippet": "₹3,999", "estimated_price": "₹3,999",
             "domain": "amazon.in", "is_valid": True,
             "source_query": "q", "validation_reason": "ok"}
            for i in range(4)
        ]
        with patch("workflow.nodes._get_llm", side_effect=RuntimeError("down")):
            result = rank_gifts_node(self._state_with_products(raw_candidates))

        assert result["status"] == "gifts_ranked_fallback"
        assert len(result["recommended_gifts"]) <= 3
        # Fallback should flag for review
        assert result["recommended_gifts"][0]["risk_level"] == "high"
        assert result["llm_usage"] == []


# ── generate_messages_node ────────────────────────────────────────────────────

    def test_guardrails_penalise_fallback_marketplace_urls(self):
        gift = {
            "rank": 1,
            "gift_name": "Marketplace search for cricket gifts",
            "product_url": "https://www.amazon.in/s?k=cricket+gift",
            "store": "amazon.in",
            "estimated_price": "Under 5000 INR - verify price",
            "why_this_gift": "Matches cricket interest.",
            "personalisation_reasoning": "Strong signal: cricket",
            "personalised_message": "",
            "confidence_score": 0.9,
            "risk_level": "low",
            "assumptions": [],
        }
        candidate = {
            "title": "Marketplace search for: cricket gift",
            "validation_reason": "Fallback marketplace search URL; exact product requires human review",
        }

        guarded = _apply_gift_guardrails(
            gift,
            candidate,
            {"strong_signals": ["Cricket"], "weak_signals": []},
        )

        assert guarded["confidence_score"] <= 0.55
        assert guarded["risk_level"] == "medium"
        assert any("verify" in a.lower() for a in guarded["assumptions"])

    def test_guardrails_flag_sensitive_or_over_personal_recommendations(self):
        gift = {
            "rank": 1,
            "gift_name": "Romantic gift set",
            "product_url": "https://amazon.in/p1",
            "store": "amazon.in",
            "estimated_price": "3999 INR",
            "why_this_gift": "Mentions family status and personal life.",
            "personalisation_reasoning": "Speculative personal claim",
            "personalised_message": "",
            "confidence_score": 0.9,
            "risk_level": "low",
            "assumptions": [],
        }

        guarded = _apply_gift_guardrails(
            gift,
            {"title": "Romantic gift set", "validation_reason": "Recognised e-commerce domain"},
            {"strong_signals": ["Cricket"], "weak_signals": []},
        )

        assert guarded["confidence_score"] <= 0.35
        assert guarded["risk_level"] == "high"
        assert any("guardrail" in a.lower() for a in guarded["assumptions"])


class TestGenerateMessagesNode:

    GIFTS = [
        {"rank": 1, "gift_name": "SG Cricket Bat", "why_this_gift": "Cricket interest", "personalised_message": ""},
        {"rank": 2, "gift_name": "Leadership Book", "why_this_gift": "Role fit", "personalised_message": ""},
        {"rank": 3, "gift_name": "Desk Organiser", "why_this_gift": "Professional", "personalised_message": ""},
    ]

    def _state_with_gifts(self, gifts):
        return {
            **BASE_STATE,
            "profile_signals": {
                "strong_signals": ["Cricket"], "weak_signals": [], "signals_to_avoid": ["no religion"]
            },
            "recommended_gifts": gifts,
            "status": "gifts_ranked",
        }

    def test_empty_gifts_skipped(self):
        result = generate_messages_node(self._state_with_gifts([]))
        assert result["recommended_gifts"] == []
        assert result["status"] == "messages_skipped"
        assert result["llm_usage"] == []

    def test_success_path_fills_messages(self):
        msg_response = _make_mock_response(
            '{"messages": ['
            '{"rank": 1, "message": "Dear Aarav, enjoy this cricket bat!"},'
            '{"rank": 2, "message": "Dear Aarav, this book is for you."},'
            '{"rank": 3, "message": "Dear Aarav, hope this helps your desk."}'
            ']}'
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = msg_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = generate_messages_node(self._state_with_gifts(self.GIFTS))

        assert result["status"] == "messages_generated"
        gifts = result["recommended_gifts"]
        assert all(g["personalised_message"] != "" for g in gifts)
        assert "Aarav" in gifts[0]["personalised_message"]

    def test_success_path_records_token_usage(self):
        msg_response = _make_mock_response(
            '{"messages": [{"rank": 1, "message": "Hi Aarav!"}, '
            '{"rank": 2, "message": "Hi!"}, {"rank": 3, "message": "Hi!"}]}',
            prompt_tokens=90, completion_tokens=60,
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = msg_response

        with patch("workflow.nodes._get_llm", return_value=mock_llm):
            result = generate_messages_node(self._state_with_gifts(self.GIFTS))

        assert len(result["llm_usage"]) == 1
        assert result["llm_usage"][0]["node"] == "generate_messages"
        assert result["llm_usage"][0]["completion_tokens"] == 60

    def test_llm_failure_uses_generic_fallback_message(self):
        with patch("workflow.nodes._get_llm", side_effect=RuntimeError("down")):
            result = generate_messages_node(self._state_with_gifts(self.GIFTS))

        assert result["status"] == "messages_generated_fallback"
        gifts = result["recommended_gifts"]
        # Every gift must still have a non-empty message
        assert all(g["personalised_message"] != "" for g in gifts)
        # Fallback message should mention the contact name
        assert "Aarav" in gifts[0]["personalised_message"]
        assert result["llm_usage"] == []
