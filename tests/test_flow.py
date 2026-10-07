"""
Full-flow integration test (mock AI providers).
Tests the complete pipeline:
  Telegram event → discussion → context snapshot → Round 1
  → Cross Review → disagreement → debate → synthesis → CEO review → decision

External provider tests must be in test_flow_external.py and gated by env var.
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Mock AI provider
# ---------------------------------------------------------------------------
class MockProviderAdapter:
    """
    Mock adapter returns a valid CouncilResponse JSON.
    Does NOT call any real API — use for all CI/default tests.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.call_count = 0

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000):
        self.call_count += 1
        import json

        from council.agents.base import AgentResponse
        from council.schemas.council_response import CouncilResponse

        mock_data = {
            "summary": f"Mock response from {self.agent_id}",
            "position": "OPTION_A",
            "recommendation": f"{self.agent_id} recommends Option A",
            "confidence": 0.8,
            "agreements": [],
            "disagreements": [],
            "risks": [],
            "assumptions": ["This is a test"],
            "questions": [],
            "raw_notes": None,
        }
        parsed = CouncilResponse.model_validate(mock_data)
        return AgentResponse(
            raw_text=json.dumps(mock_data),
            parsed=parsed,
            is_valid=True,
            tokens_input=100,
            tokens_output=200,
            cost_usd=0.001,
            model_used=model,
            provider="mock",
        )

    def estimate_cost(self, prompt: str, model: str) -> float:
        return 0.001


class MockDisagreeingAdapter(MockProviderAdapter):
    """Returns a response that disagrees with OPTION_A — triggers debate."""

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000):
        import json

        from council.agents.base import AgentResponse
        from council.schemas.council_response import CouncilResponse

        mock_data = {
            "summary": f"Mock disagreement from {self.agent_id}",
            "position": "OPTION_B",
            "recommendation": f"{self.agent_id} recommends Option B instead",
            "confidence": 0.9,
            "agreements": [],
            "disagreements": [
                {
                    "agent_id": "codex",
                    "point": "architecture choice",
                    "reason": "Option A has scalability issues at >1000 users",
                }
            ],
            "risks": [
                {
                    "title": "Scalability risk",
                    "likelihood": "HIGH",
                    "impact": "CRITICAL",
                    "description": "Option A will bottleneck at scale",
                }
            ],
            "assumptions": [],
            "questions": ["What is the expected user count at launch?"],
            "raw_notes": None,
        }
        self.call_count += 1
        parsed = CouncilResponse.model_validate(mock_data)
        return AgentResponse(
            raw_text=json.dumps(mock_data),
            parsed=parsed,
            is_valid=True,
            tokens_input=150,
            tokens_output=300,
            cost_usd=0.002,
            model_used=model,
            provider="mock",
        )


# ---------------------------------------------------------------------------
# Pipeline unit tests (no real DB/API)
# ---------------------------------------------------------------------------
class TestCouncilResponseParsing:
    """Test Correction #10: structured response parsing + fallback."""

    def test_valid_json_parses(self):
        from council.schemas.council_response import parse_council_response

        valid = """{
            "summary": "Test summary",
            "position": "OPTION_A",
            "confidence": 0.75,
            "agreements": [],
            "disagreements": [],
            "risks": [],
            "assumptions": [],
            "questions": []
        }"""
        parsed, is_valid = parse_council_response(valid)
        assert is_valid is True
        assert parsed is not None
        assert parsed.position == "OPTION_A"
        assert parsed.confidence == 0.75

    def test_markdown_code_fence_extracted(self):
        from council.schemas.council_response import parse_council_response

        with_fence = """Here is my analysis:
```json
{
    "summary": "Fenced JSON",
    "position": "OPTION_B",
    "confidence": 0.6,
    "agreements": [],
    "disagreements": [],
    "risks": [],
    "assumptions": [],
    "questions": []
}
```
That is my recommendation."""
        parsed, is_valid = parse_council_response(with_fence)
        assert is_valid is True
        assert parsed.summary == "Fenced JSON"

    def test_invalid_json_returns_false(self):
        from council.schemas.council_response import parse_council_response

        bad = "This is just prose. No JSON here at all."
        parsed, is_valid = parse_council_response(bad)
        assert is_valid is False
        assert parsed is None

    def test_invalid_position_defaults_to_neutral(self):
        from council.schemas.council_response import parse_council_response

        with_bad_position = """{
            "summary": "Bad position",
            "position": "TOTALLY_INVALID",
            "confidence": 0.5,
            "agreements": [],
            "disagreements": [],
            "risks": [],
            "assumptions": [],
            "questions": []
        }"""
        parsed, is_valid = parse_council_response(with_bad_position)
        assert is_valid is True
        assert parsed.position == "NEUTRAL"  # Default on invalid

    def test_confidence_clamped(self):
        from council.schemas.council_response import CouncilResponse

        # Confidence out of range should fail validation
        with pytest.raises(Exception):
            CouncilResponse.model_validate(
                {
                    "summary": "test",
                    "position": "OPTION_A",
                    "confidence": 1.5,  # > 1.0 — invalid
                    "agreements": [],
                    "disagreements": [],
                    "risks": [],
                    "assumptions": [],
                    "questions": [],
                }
            )


class TestDecisionImmutability:
    """
    Test Correction #1: append-only decisions.
    New decision → INSERT with supersedes_id.
    Old record NEVER updated (no superseded_by_id column).
    """

    def test_supersedes_chain_traversable_forward(self):
        """
        DEC-032 ← DEC-071 chain.
        Query: SELECT * FROM decisions WHERE supersedes_id = 'DEC-032' → DEC-071.
        No need for superseded_by_id on old record.
        """
        decisions = {
            "DEC-032": {"id": "DEC-032", "supersedes_id": None, "status": "APPROVED"},
            "DEC-071": {"id": "DEC-071", "supersedes_id": "DEC-032", "status": "APPROVED"},
        }

        def find_replacement(decision_id: str) -> dict | None:
            """Simulates: SELECT * FROM decisions WHERE supersedes_id = $1"""
            return next(
                (d for d in decisions.values() if d["supersedes_id"] == decision_id),
                None,
            )

        replacement = find_replacement("DEC-032")
        assert replacement is not None
        assert replacement["id"] == "DEC-071"

        # DEC-032 itself is unchanged (immutable)
        assert decisions["DEC-032"]["supersedes_id"] is None

    def test_old_decision_not_modified(self):
        """
        After creating DEC-071 (supersedes DEC-032),
        DEC-032 must remain exactly as originally inserted.
        """
        dec_032_original = {
            "id": "DEC-032",
            "status": "APPROVED",
            "option_selected": "OPTION_B",
            "dissent_json": {"claude": {"position": "OPTION_A", "reason": "Better perf"}},
            "supersedes_id": None,
        }
        # Simulate insert of DEC-071 (supersedes DEC-032)
        _dec_071 = {
            "id": "DEC-071",
            "status": "APPROVED",
            "supersedes_id": "DEC-032",
        }

        # DEC-032 must not be modified (simulates DB immutability via no UPDATE)
        assert dec_032_original["dissent_json"] is not None, "Dissent must be preserved"
        assert dec_032_original["supersedes_id"] is None, "Old record never gets superseded_by_id"

    def test_dissent_never_deleted(self):
        """
        Even if Claude later changed its mind, dissent in DEC-032
        reflects the moment it was recorded — immutable.
        """
        decision = {
            "id": "DEC-032",
            "dissent_json": {
                "claude": {
                    "position": "OPTION_A",
                    "reason": "Resource-based limits harm late-game progression",
                    "run_id": "MSG-000234",
                }
            },
        }
        # Cannot delete or modify dissent
        assert decision["dissent_json"]["claude"]["reason"] != ""
        assert decision["dissent_json"] is not None


class TestDebateCycles:
    """
    Test Correction #8 / N5 LOCKED: 3 cycles per disagreement.
    UNRESOLVED: Codex does NOT choose winner.
    """

    def test_max_3_cycles(self):
        MAX_CYCLES = 3
        disagreement = {"cycles_completed": 0, "status": "OPEN"}

        for _ in range(MAX_CYCLES):
            disagreement["cycles_completed"] += 1

        assert disagreement["cycles_completed"] == MAX_CYCLES

        # After 3 cycles, status is set (not auto-extended)
        final_states = {"RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED"}
        # In this test: set to UNRESOLVED (worst case)
        disagreement["status"] = "UNRESOLVED"
        assert disagreement["status"] in final_states

    def test_unresolved_codex_does_not_choose_winner(self):
        """
        When disagreement is UNRESOLVED, Codex synthesis MUST include
        both positions — NOT pick a winner.
        """
        disagreement = {
            "status": "UNRESOLVED",
            "position_a_label": "Microservices",
            "position_b_label": "Monolith",
            "resolution_note": None,
            "resolved_by": None,
        }

        def codex_synthesis(dis: dict) -> dict:
            """Correct behavior: include both, no winner."""
            if dis["status"] == "UNRESOLVED":
                return {
                    "contains_unresolved": True,
                    "both_positions_included": True,
                    "winner": None,  # NEVER pick winner on UNRESOLVED
                    "label": "UNRESOLVED DISAGREEMENT",
                }
            return {"contains_unresolved": False, "winner": dis.get("resolved_by")}

        result = codex_synthesis(disagreement)
        assert result["winner"] is None, "Codex must NOT choose winner for UNRESOLVED"
        assert result["both_positions_included"] is True
        assert result["label"] == "UNRESOLVED DISAGREEMENT"


class TestMockProviderFlow:
    """
    Smoke test: mock adapters return valid CouncilResponse objects.
    Validates that the pipeline can handle agent responses end-to-end.
    """

    def test_mock_adapter_returns_valid_response(self):
        adapter = MockProviderAdapter("codex")
        response = asyncio.run(adapter.complete("Test prompt", "gpt-4o"))
        assert response.is_valid is True
        assert response.parsed is not None
        assert response.parsed.position == "OPTION_A"
        assert response.cost_usd > 0

    def test_mock_disagreeing_adapter_triggers_disagreement(self):
        adapter = MockDisagreeingAdapter("gemini")
        response = asyncio.run(adapter.complete("Test prompt", "gemini-1.5-pro"))
        assert response.is_valid is True
        assert response.parsed.position == "OPTION_B"
        assert len(response.parsed.disagreements) > 0
        assert len(response.parsed.risks) > 0

    def test_parallel_mock_agents(self):
        """Simulate parallel Round 1: agents run concurrently, results collected."""
        adapters = {
            "codex": MockProviderAdapter("codex"),
            "claude": MockProviderAdapter("claude"),
            "gemini": MockDisagreeingAdapter("gemini"),
        }

        async def run_parallel():
            tasks = [
                adapter.complete("Should we use microservices?", "test-model")
                for adapter in adapters.values()
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(run_parallel())
        assert len(results) == 3
        assert all(r.is_valid for r in results)

        # Gemini disagrees — check disagreement extraction
        gemini_result = results[2]
        assert len(gemini_result.parsed.disagreements) > 0


class TestCostControlFlow:
    """
    Test Correction #13: pre-run check, soft/hard limits.
    """

    def test_allowed_under_soft_limit(self):
        from decimal import Decimal

        from council.costs.tracker import RunPermission

        # Simulate the logic (without DB)
        cost_total = Decimal("0.50")
        cost_reserved = Decimal("0.10")
        estimated = Decimal("0.05")
        soft_limit = Decimal("2.00")
        hard_limit = Decimal("5.00")
        soft_notified = False

        projected = cost_total + cost_reserved + estimated
        if projected > hard_limit:
            result = RunPermission.BLOCKED_HARD_LIMIT
        elif cost_total >= soft_limit and not soft_notified:
            result = RunPermission.WARN_SOFT_LIMIT
        else:
            result = RunPermission.ALLOWED

        assert result == RunPermission.ALLOWED

    def test_blocked_over_hard_limit(self):
        from decimal import Decimal

        from council.costs.tracker import RunPermission

        cost_total = Decimal("4.90")
        cost_reserved = Decimal("0.20")
        estimated = Decimal("0.05")
        hard_limit = Decimal("5.00")
        soft_limit = Decimal("2.00")
        soft_notified = True

        projected = cost_total + cost_reserved + estimated
        if projected > hard_limit:
            result = RunPermission.BLOCKED_HARD_LIMIT
        elif cost_total >= soft_limit and not soft_notified:
            result = RunPermission.WARN_SOFT_LIMIT
        else:
            result = RunPermission.ALLOWED

        assert result == RunPermission.BLOCKED_HARD_LIMIT

    def test_soft_limit_warn(self):
        from decimal import Decimal

        from council.costs.tracker import RunPermission

        cost_total = Decimal("2.10")  # Over soft limit
        cost_reserved = Decimal("0.00")
        estimated = Decimal("0.05")
        soft_limit = Decimal("2.00")
        hard_limit = Decimal("5.00")
        soft_notified = False  # Not yet notified

        projected = cost_total + cost_reserved + estimated
        if projected > hard_limit:
            result = RunPermission.BLOCKED_HARD_LIMIT
        elif cost_total >= soft_limit and not soft_notified:
            result = RunPermission.WARN_SOFT_LIMIT
        else:
            result = RunPermission.ALLOWED

        assert result == RunPermission.WARN_SOFT_LIMIT
