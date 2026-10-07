"""Council state machine used by workers and deterministic integration tests.

The engine is storage-agnostic: it reports every round and agent run to a
``RoundRecorder``. The worker plugs in a PostgreSQL recorder (see
``council.orchestrator.service``); tests use the default no-op recorder.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from council.agents.base import AgentResponse, ProviderAdapter
from council.agents.runner import run_with_timeout
from council.orchestrator.prompts import AgentProfile, build_prompt
from council.orchestrator.quorum import compute_quorum_threshold, quorum_achieved

MAX_DEBATE_CYCLES = 3  # N5 LOCKED
MODERATOR_ID = "codex"


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
    PAUSED = "PAUSED"


class QuorumNotReached(RuntimeError):
    def __init__(self, round_type: str, completed: int, requested: int, threshold: int):
        super().__init__(
            f"quorum not reached in {round_type}: {completed}/{requested} completed, "
            f"{threshold} required"
        )
        self.round_type = round_type
        self.completed = completed
        self.requested = requested
        self.threshold = threshold


@dataclass(slots=True)
class RunResult:
    agent_id: str
    response: AgentResponse | None = None
    status: str = "PENDING"
    error: str | None = None
    model: str | None = None
    prompt: str | None = None
    estimated_cost: float = 0.0
    run_ref: Any = None


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
    criticality: str = "NORMAL"
    synthesis_result: RunResult | None = None


def snapshot_id(context: dict) -> str:
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return "CTX-" + hashlib.sha256(canonical.encode()).hexdigest()[:16].upper()


def positions_of(results: list[RunResult]) -> list[dict]:
    """Valid structured positions from one round, in agent order."""
    return [
        {
            "agent_id": r.agent_id,
            "position": r.response.parsed.position,
            "summary": r.response.parsed.summary,
            "recommendation": r.response.parsed.recommendation,
        }
        for r in results
        if r.status == "COMPLETED" and r.response and r.response.parsed
    ]


class RoundRecorder(Protocol):
    """Persistence hooks. Every method may be a no-op."""

    async def state_changed(self, discussion: Discussion, state: DiscussionState) -> None: ...

    async def round_started(
        self, discussion: Discussion, round_type: str, round_number: int, threshold: int
    ) -> Any: ...

    async def before_agent(self, discussion: Discussion, round_ref: Any, run: RunResult) -> bool:
        """Reserve cost and record the run. Return False to skip the agent."""
        ...

    async def agent_finished(
        self, discussion: Discussion, round_ref: Any, run: RunResult
    ) -> None: ...

    async def round_finished(
        self, discussion: Discussion, round_ref: Any, results: list[RunResult], quorum_ok: bool
    ) -> None: ...

    async def disagreement_updated(self, discussion: Discussion, disagreement: dict) -> None: ...


class NullRecorder:
    async def state_changed(self, discussion, state):
        return None

    async def round_started(self, discussion, round_type, round_number, threshold):
        return None

    async def before_agent(self, discussion, round_ref, run):
        return True

    async def agent_finished(self, discussion, round_ref, run):
        return None

    async def round_finished(self, discussion, round_ref, results, quorum_ok):
        return None

    async def disagreement_updated(self, discussion, disagreement):
        return None


class CouncilEngine:
    """Runs the approved MVP flow without hiding partial/timeout results."""

    def __init__(
        self,
        adapters: dict[str, ProviderAdapter],
        *,
        agent_timeout_s: float = 180,
        models: dict[str, str] | None = None,
        profiles: dict[str, AgentProfile] | None = None,
        recorder: RoundRecorder | None = None,
        retries: int = 0,
        max_tokens: int = 4000,
    ):
        self.adapters = adapters
        self.agent_timeout_s = agent_timeout_s
        self.models = models or {}
        self.profiles = profiles or {}
        self.recorder: RoundRecorder = recorder or NullRecorder()
        self.retries = retries
        self.max_tokens = max_tokens

    def _profile(self, agent_id: str) -> AgentProfile:
        return self.profiles.get(agent_id) or AgentProfile(agent_id=agent_id)

    async def _set_state(self, discussion: Discussion, state: DiscussionState) -> None:
        discussion.state = state
        await self.recorder.state_changed(discussion, state)

    async def _run_agent(self, discussion: Discussion, round_ref: Any, run: RunResult) -> RunResult:
        adapter = self.adapters[run.agent_id]
        if not await self.recorder.before_agent(discussion, round_ref, run):
            run.status = "SKIPPED"
            run.error = run.error or "cost hard limit reached"
            await self.recorder.agent_finished(discussion, round_ref, run)
            return run
        try:
            run.response = await run_with_timeout(
                adapter,
                run.prompt or "",
                run.model or "",
                self.max_tokens,
                self.agent_timeout_s,
                retries=self.retries,
            )
            if run.response.parsed is None:
                run.status = "FAILED"
                run.error = "response was not valid council JSON"
            else:
                run.status = "COMPLETED"
        except TimeoutError:
            run.status, run.error = "TIMEOUT", "provider timeout"
        except Exception as exc:  # provider failure is recorded, not swallowed
            run.status, run.error = "FAILED", str(exc) or exc.__class__.__name__
        await self.recorder.agent_finished(discussion, round_ref, run)
        return run

    async def _round(
        self, discussion: Discussion, round_type: str, positions: list[dict], cycle: int = 0
    ) -> list[RunResult]:
        if discussion.criticality == "CRITICAL":
            threshold = len(discussion.agents)  # critical: everyone must answer
        else:
            threshold = compute_quorum_threshold(discussion.agents)
        round_number = len(discussion.rounds) + 1
        round_ref = await self.recorder.round_started(
            discussion, round_type, round_number, threshold
        )
        runs = []
        for agent_id in discussion.agents:
            adapter = self.adapters[agent_id]
            model = self.models.get(agent_id, "test-model")
            prompt = build_prompt(
                round_type,
                self._profile(agent_id),
                discussion.question,
                discussion.context,
                positions,
                cycle,
            )
            try:
                estimate = float(adapter.estimate_cost(prompt, model))
            except Exception:
                estimate = 0.0
            runs.append(RunResult(agent_id, model=model, prompt=prompt, estimated_cost=estimate))

        results = list(
            await asyncio.gather(*(self._run_agent(discussion, round_ref, r) for r in runs))
        )
        completed = [r.agent_id for r in results if r.status == "COMPLETED"]
        ok = quorum_achieved(discussion.agents, completed, threshold, True)
        await self.recorder.round_finished(discussion, round_ref, results, ok)
        if not ok:
            raise QuorumNotReached(round_type, len(completed), len(discussion.agents), threshold)
        discussion.rounds.append(
            {
                "type": round_type,
                "number": round_number,
                "results": results,
                "partial": len(completed) < len(results),
            }
        )
        return results

    async def run_to_ceo_review(self, discussion: Discussion) -> Discussion:
        await self._set_state(discussion, DiscussionState.INDEPENDENT_REVIEW)
        first = await self._round(discussion, "INDEPENDENT_REVIEW", [])

        await self._set_state(discussion, DiscussionState.CROSS_REVIEW)
        latest = await self._round(discussion, "CROSS_REVIEW", positions_of(first))

        unique = sorted({p["position"] for p in positions_of(latest)})
        if len(unique) > 1:
            disagreement = {"positions": unique, "cycles_completed": 0, "status": "DEBATING"}
            discussion.disagreements.append(disagreement)
            await self.recorder.disagreement_updated(discussion, disagreement)
            await self._set_state(discussion, DiscussionState.DEBATE)
            for cycle in range(1, MAX_DEBATE_CYCLES + 1):
                latest = await self._round(discussion, "DEBATE", positions_of(latest), cycle)
                disagreement["cycles_completed"] = cycle
                unique = sorted({p["position"] for p in positions_of(latest)})
                disagreement["positions"] = unique
                if len(unique) <= 1:
                    disagreement["status"] = "RESOLVED"
                    break
            else:
                disagreement["status"] = "ESCALATED_TO_CEO"
            await self.recorder.disagreement_updated(discussion, disagreement)

        await self._set_state(discussion, DiscussionState.SYNTHESIS)
        synthesis_results = await self._round(discussion, "SYNTHESIS", positions_of(latest))
        valid = [r for r in synthesis_results if r.status == "COMPLETED" and r.response]
        chosen = next((r for r in valid if r.agent_id == MODERATOR_ID), None) or (
            valid[0] if valid else None
        )
        discussion.synthesis_result = chosen
        discussion.synthesis = (
            chosen.response.parsed.summary
            if chosen and chosen.response and chosen.response.parsed
            else "Partial synthesis available for CEO review"
        )
        await self._set_state(discussion, DiscussionState.CEO_REVIEW)
        return discussion


class DecisionGate:
    """In-memory model of the CEO decision gate: the first decision wins.

    Production uses the transactional PostgreSQL gate in
    ``council.orchestrator.service.record_decision``; this class keeps the same
    contract for fast unit tests.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._decisions: dict[str, str] = {}

    async def decide(self, action_key: str, option: str) -> tuple[bool, str]:
        async with self._lock:
            if action_key in self._decisions:
                return False, self._decisions[action_key]
            self._decisions[action_key] = option
            return True, option
