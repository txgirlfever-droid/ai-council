from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from council.agents.base import AgentResponse, ProviderAdapter
from council.orchestrator.engine import CouncilEngine, DecisionGate, Discussion, DiscussionState
from council.schemas.council_response import CouncilResponse, DisagreementItem
from council.webhook.service import IntakeLedger, decision_action_key
from council.worker.lease import Lease


class MockAdapter(ProviderAdapter):
    def __init__(self, agent_id: str, position: str):
        self.agent_id = agent_id
        self.position = position

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000) -> AgentResponse:
        parsed = CouncilResponse(
            summary=f"{self.agent_id} summary",
            position=self.position,
            disagreements=[]
            if self.position == "OPTION_A"
            else [DisagreementItem(agent_id="codex", point="choice", reason="tradeoff")],
        )
        return AgentResponse("{}", parsed, True, 10, 10, 0.001, model, self.agent_id)

    def estimate_cost(self, prompt: str, model: str) -> float:
        return 0.001


class SlowAdapter(MockAdapter):
    async def complete(self, prompt: str, model: str, max_tokens: int = 4000) -> AgentResponse:
        await asyncio.sleep(0.05)
        return await super().complete(prompt, model, max_tokens)


def test_full_webhook_to_ceo_decision_flow():
    async def scenario():
        ledger = IntakeLedger()
        update = {"update_id": 101, "message": {"text": "/council choose A or B"}}
        assert ledger.accept(update)
        assert not ledger.accept(update)  # Telegram retry is idempotent

        discussion = Discussion(
            id="DISC-001",
            question="choose A or B",
            agents=["codex", "claude", "gemini"],
            context={"architecture": "0.2.1"},
        )
        engine = CouncilEngine(
            {
                "codex": MockAdapter("openai", "OPTION_A"),
                "claude": MockAdapter("anthropic", "OPTION_A"),
                "gemini": MockAdapter("google", "OPTION_B"),
            }
        )
        await engine.run_to_ceo_review(discussion)
        assert discussion.state == DiscussionState.CEO_REVIEW
        assert discussion.disagreements[0]["cycles_completed"] == 3
        assert discussion.synthesis

        gate = DecisionGate()
        key = decision_action_key(discussion.id, "CEO_REVIEW_V1")
        accepted, option = await gate.decide(key, "APPROVED")
        assert accepted and option == "APPROVED"
        discussion.state = DiscussionState.APPROVED
        return discussion

    result = asyncio.run(scenario())
    assert result.state == DiscussionState.APPROVED
    assert [r["type"] for r in result.rounds] == [
        "INDEPENDENT_REVIEW",
        "CROSS_REVIEW",
        "DEBATE",
        "DEBATE",
        "DEBATE",
        "SYNTHESIS",
    ]


def test_double_approval_race_has_one_winner():
    async def race():
        gate = DecisionGate()
        return await asyncio.gather(
            gate.decide("decision:DISC-1:R1", "OPTION_A"),
            gate.decide("decision:DISC-1:R1", "OPTION_B"),
        )

    results = asyncio.run(race())
    assert sum(accepted for accepted, _ in results) == 1
    assert results[0][1] == results[1][1]


def test_provider_timeout_is_visible_but_quorum_can_complete():
    async def scenario():
        discussion = Discussion("DISC-2", "question", ["codex", "claude", "gemini"], {})
        engine = CouncilEngine(
            {
                "codex": MockAdapter("openai", "OPTION_A"),
                "claude": MockAdapter("anthropic", "OPTION_A"),
                "gemini": SlowAdapter("google", "OPTION_A"),
            },
            agent_timeout_s=0.01,
        )
        await engine.run_to_ceo_review(discussion)
        return discussion

    result = asyncio.run(scenario())
    assert result.state == DiscussionState.CEO_REVIEW
    assert all(r["partial"] for r in result.rounds)
    assert any(x.status == "TIMEOUT" for x in result.rounds[0]["results"])


def test_worker_heartbeat_and_stale_lease_recovery():
    now = datetime.now(UTC)
    lease = Lease()
    assert lease.claim("worker-1", 30, now)
    assert lease.heartbeat("worker-1", 30, now + timedelta(seconds=20))
    assert not lease.recover(now + timedelta(seconds=40))
    assert lease.recover(now + timedelta(seconds=51))
    assert lease.status == "PENDING"
    assert lease.claim("worker-2", 30, now + timedelta(seconds=52))


def test_stale_lease_at_max_attempts_fails():
    now = datetime.now(UTC)
    lease = Lease(
        status="RUNNING",
        locked_by="dead",
        heartbeat_at=now - timedelta(seconds=60),
        lease_expires_at=now - timedelta(seconds=1),
        attempt_count=3,
        max_attempts=3,
    )
    assert lease.recover(now)
    assert lease.status == "FAILED"
