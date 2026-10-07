"""Council state machine used by workers and deterministic integration tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum

from council.agents.base import AgentResponse, ProviderAdapter
from council.orchestrator.quorum import compute_quorum_threshold, quorum_achieved


class DiscussionState(StrEnum):
    CREATED = "CREATED"
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"
    CROSS_REVIEW = "CROSS_REVIEW"
    DEBATE = "DEBATE"
    SYNTHESIS = "SYNTHESIS"
    CEO_REVIEW = "CEO_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


@dataclass(slots=True)
class RunResult:
    agent_id: str
    response: AgentResponse | None = None
    status: str = "PENDING"
    error: str | None = None


@dataclass(slots=True)
class Discussion:
    id: str
    question: str
    agents: list[str]
    context: dict
    state: DiscussionState = DiscussionState.CREATED
    rounds: list[dict] = field(default_factory=list)
    disagreements: list[dict] = field(default_factory=list)
    synthesis: str | None = None
    decision: str | None = None


def snapshot_id(context: dict) -> str:
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return "CTX-" + hashlib.sha256(canonical.encode()).hexdigest()[:16].upper()


class CouncilEngine:
    """Runs the approved MVP flow without hiding partial/timeout results."""

    def __init__(self, adapters: dict[str, ProviderAdapter], *, agent_timeout_s: float = 180):
        self.adapters = adapters
        self.agent_timeout_s = agent_timeout_s

    async def _run_agent(self, agent_id: str, prompt: str) -> RunResult:
        adapter = self.adapters[agent_id]
        try:
            async with asyncio.timeout(self.agent_timeout_s):
                response = await adapter.complete(prompt, "test-model", 4000)
            return RunResult(agent_id, response, "COMPLETED")
        except TimeoutError:
            return RunResult(agent_id, status="TIMEOUT", error="provider timeout")
        except Exception as exc:  # provider failure is recorded, not swallowed
            return RunResult(agent_id, status="FAILED", error=str(exc))

    async def _round(self, discussion: Discussion, round_type: str, prompt: str) -> list[RunResult]:
        results = await asyncio.gather(
            *(self._run_agent(agent, prompt) for agent in discussion.agents)
        )
        completed = [r.agent_id for r in results if r.status == "COMPLETED"]
        threshold = compute_quorum_threshold(discussion.agents)
        if not quorum_achieved(discussion.agents, completed, threshold, True):
            raise RuntimeError(f"quorum not reached: {len(completed)}/{len(discussion.agents)}")
        discussion.rounds.append(
            {"type": round_type, "results": results, "partial": len(completed) < len(results)}
        )
        return results

    async def run_to_ceo_review(self, discussion: Discussion) -> Discussion:
        discussion.state = DiscussionState.INDEPENDENT_REVIEW
        first = await self._round(discussion, "INDEPENDENT_REVIEW", discussion.question)

        discussion.state = DiscussionState.CROSS_REVIEW
        summaries = [r.response.parsed.position for r in first if r.response and r.response.parsed]
        await self._round(discussion, "CROSS_REVIEW", f"Cross-review these positions: {summaries}")

        unique = sorted(set(summaries))
        if len(unique) > 1:
            discussion.disagreements.append({"positions": unique, "cycles_completed": 0})
            discussion.state = DiscussionState.DEBATE
            for cycle in range(1, 4):
                await self._round(discussion, "DEBATE", f"Debate cycle {cycle}: {unique}")
                discussion.disagreements[0]["cycles_completed"] = cycle

        discussion.state = DiscussionState.SYNTHESIS
        synthesis_results = await self._round(
            discussion, "SYNTHESIS", "Synthesize the council result"
        )
        valid = [
            r.response.parsed.summary for r in synthesis_results if r.response and r.response.parsed
        ]
        discussion.synthesis = valid[0] if valid else "Partial synthesis available for CEO review"
        discussion.state = DiscussionState.CEO_REVIEW
        return discussion


class DecisionGate:
    """Serializes competing callbacks and preserves the first business effect."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._decisions: dict[str, str] = {}

    async def decide(self, action_key: str, option: str) -> tuple[bool, str]:
        async with self._lock:
            if action_key in self._decisions:
                return False, self._decisions[action_key]
            self._decisions[action_key] = option
            return True, option
