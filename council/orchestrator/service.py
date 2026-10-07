"""Worker job handlers: PostgreSQL-backed council runs and CEO decisions.

Job types (fixed by the ``job_queue`` CHECK constraint):

* ``COMPLETE_ROUND`` — a ``/council`` command: create the discussion, run every
  round, then post the synthesis with Approve / Reject / Defer buttons.
* ``NOTIFY_CEO`` — any other CEO update: decision button presses, ``/status``,
  ``/help``.

Every business effect is guarded so a retried job never duplicates it.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from council.agents import create_adapter
from council.audit.log import log as audit_log
from council.config import settings
from council.costs.tracker import (
    RunPermission,
    can_start_agent_run,
    mark_soft_limit_notified,
    record_actual_cost,
    reserve_cost,
)
from council.db import get_engine
from council.orchestrator.engine import (
    MODERATOR_ID,
    CouncilEngine,
    Discussion,
    DiscussionState,
    QuorumNotReached,
    RunResult,
    positions_of,
    snapshot_id,
)
from council.orchestrator.prompts import AgentProfile, preamble_hash, role_preamble
from council.telegram import format as fmt
from council.telegram.buttons import decision_keyboard, parse_decision_callback
from council.telegram.sender import get_client
from council.webhook.service import decision_action_key
from council.worker.worker import register_handler

logger = logging.getLogger(__name__)

AGENT_TIMEOUT_S = 180
PROVIDER_RETRIES = 2
DEFAULT_PROJECT_NAME = "Default project"
PROVIDER_KEYS = {
    "openai": lambda: settings.openai_api_key,
    "anthropic": lambda: settings.anthropic_api_key,
    "google": lambda: settings.google_api_key,
}
FINISHED_STATES = {"CEO_REVIEW", "APPROVED", "REJECTED", "DEFERRED", "CANCELLED"}
_COMMAND = re.compile(r"^/(\w+)(?:@\w+)?\s*(.*)$", re.DOTALL)

# Overridable in tests: provider name -> adapter instance.
adapter_factory = create_adapter


def parse_command(message_text: str) -> tuple[str, str]:
    """``'/council@MyBot  Use X?'`` -> ``('council', 'Use X?')``."""
    match = _COMMAND.match(message_text.strip())
    if not match:
        return "", message_text.strip()
    return match.group(1).lower(), match.group(2).strip()


def _chat(update: dict) -> tuple[int, int | None]:
    message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id") or settings.telegram_group_id)
    return chat_id, message.get("message_thread_id")


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
async def ensure_ceo_user(conn: AsyncConnection, telegram_id: int, username: str | None) -> str:
    row = await conn.execute(
        text("""
        INSERT INTO users (telegram_id, username, role) VALUES (:tid, :username, 'CEO')
        ON CONFLICT (telegram_id) DO UPDATE SET username=COALESCE(EXCLUDED.username,
            users.username)
        RETURNING id
    """),
        {"tid": telegram_id, "username": username},
    )
    return str(row.scalar_one())


async def ensure_default_project(conn: AsyncConnection) -> str:
    existing = await conn.execute(
        text("SELECT id FROM projects WHERE name=:name ORDER BY created_at LIMIT 1"),
        {"name": DEFAULT_PROJECT_NAME},
    )
    project_id = existing.scalar_one_or_none()
    if project_id is None:
        project_id = (
            await conn.execute(
                text("INSERT INTO projects (name) VALUES (:name) RETURNING id"),
                {"name": DEFAULT_PROJECT_NAME},
            )
        ).scalar_one()
    return str(project_id)


async def load_agents(conn: AsyncConnection) -> list[dict]:
    rows = await conn.execute(
        text("""
        SELECT id, display_name, role_name, provider, model_normal, model_escalated,
               escalation_policy, max_tokens, responsibilities, restrictions
          FROM agents WHERE enabled AND NOT is_specialist
         ORDER BY CASE id WHEN 'codex' THEN 0 ELSE 1 END, id
    """)
    )
    agents = [dict(r) for r in rows.mappings()]
    for agent in agents:  # optional env overrides, e.g. CLAUDE_MODEL_NORMAL
        for tier in ("normal", "escalated"):
            override = getattr(settings, f"{agent['id']}_model_{tier}", None)
            if override:
                agent[f"model_{tier}"] = override
    return agents


def profile_of(agent: dict) -> AgentProfile:
    return AgentProfile(
        agent_id=agent["id"],
        display_name=agent["display_name"],
        role_name=agent["role_name"],
        responsibilities=tuple(agent["responsibilities"] or ()),
        restrictions=tuple(agent["restrictions"] or ()),
    )


async def ensure_prompt_version(conn: AsyncConnection, profile: AgentProfile) -> str:
    """Record the agent's role prompt immutably; a changed prompt gets a new version."""
    digest = preamble_hash(profile)
    current = (
        (
            await conn.execute(
                text("""
            SELECT id, prompt_hash, version FROM agent_prompt_versions
             WHERE agent_id=:agent AND is_current
        """),
                {"agent": profile.agent_id},
            )
        )
        .mappings()
        .first()
    )
    if current and current["prompt_hash"] == digest:
        return str(current["id"])
    if current:
        await conn.execute(
            text("UPDATE agent_prompt_versions SET is_current=FALSE WHERE id=:id"),
            {"id": current["id"]},
        )
    new_id = (
        await conn.execute(
            text("""
            INSERT INTO agent_prompt_versions (agent_id, version, prompt_text, prompt_hash)
            VALUES (:agent, :version, :prompt, :hash) RETURNING id
        """),
            {
                "agent": profile.agent_id,
                "version": (current["version"] + 1) if current else 1,
                "prompt": role_preamble(profile),
                "hash": digest,
            },
        )
    ).scalar_one()
    await conn.execute(
        text("UPDATE agents SET current_prompt_version_id=:pv, updated_at=NOW() WHERE id=:agent"),
        {"pv": new_id, "agent": profile.agent_id},
    )
    return str(new_id)


async def take_context_snapshot(conn: AsyncConnection, project_id: str) -> tuple[str, dict]:
    rows = await conn.execute(
        text("""
        SELECT id, entry_type, entry_key, version, content FROM project_context_entries
         WHERE project_id=:project AND is_current ORDER BY entry_type, entry_key
    """),
        {"project": project_id},
    )
    entries = [dict(r) for r in rows.mappings()]
    context = {
        f"{e['entry_type']}:{e['entry_key']}": {"version": e["version"], "content": e["content"]}
        for e in entries
    }
    snap_id = snapshot_id({"project": project_id, **context})
    await conn.execute(
        text("""
        INSERT INTO context_snapshots (id, project_id, snapshot_data, entry_ids)
        VALUES (:id, :project, CAST(:data AS JSONB), CAST(:entries AS UUID[]))
        ON CONFLICT (id) DO NOTHING
    """),
        {
            "id": snap_id,
            "project": project_id,
            "data": json.dumps(context, ensure_ascii=False),
            "entries": [str(e["id"]) for e in entries],
        },
    )
    return snap_id, context


# ---------------------------------------------------------------------------
# PostgreSQL recorder for the engine
# ---------------------------------------------------------------------------
class DbRecorder:
    def __init__(
        self,
        discussion_id: str,
        agents: dict[str, dict],
        prompt_versions: dict[str, str],
        snapshot: str,
        chat: tuple[int, int | None],
    ):
        self.discussion_id = discussion_id
        self.agents = agents
        self.prompt_versions = prompt_versions
        self.snapshot = snapshot
        self.chat = chat

    async def state_changed(self, discussion: Discussion, state: DiscussionState) -> None:
        async with get_engine().begin() as conn:
            await conn.execute(
                text("UPDATE discussions SET base_state=:s, updated_at=NOW() WHERE id=:id"),
                {"s": str(state), "id": self.discussion_id},
            )

    async def round_started(self, discussion, round_type, round_number, threshold) -> str:
        async with get_engine().begin() as conn:
            number = (
                await conn.execute(
                    text("""
                SELECT COALESCE(MAX(round_number), 0) + 1 FROM rounds WHERE discussion_id=:id
            """),
                    {"id": self.discussion_id},
                )
            ).scalar_one()
            round_id = (
                await conn.execute(
                    text("""
                INSERT INTO rounds (discussion_id, round_number, round_type, state,
                    agents_requested, completion_policy, quorum_threshold,
                    agent_execution_timeout_s, started_at)
                VALUES (:id, :n, :type, 'ACTIVE', :agents, :policy, :threshold, :timeout, NOW())
                RETURNING id
            """),
                    {
                        "id": self.discussion_id,
                        "n": number,
                        "type": round_type,
                        "agents": list(discussion.agents),
                        "policy": "ALL_RESPONDED"
                        if discussion.criticality == "CRITICAL"
                        else "QUORUM",
                        "threshold": threshold,
                        "timeout": AGENT_TIMEOUT_S,
                    },
                )
            ).scalar_one()
            await conn.execute(
                text("UPDATE discussions SET round_current=:n, updated_at=NOW() WHERE id=:id"),
                {"n": number, "id": self.discussion_id},
            )
        return str(round_id)

    async def before_agent(self, discussion, round_ref, run: RunResult) -> bool:
        agent = self.agents[run.agent_id]
        warn = False
        async with get_engine().begin() as conn:
            permission = await can_start_agent_run(conn, self.discussion_id, run.estimated_cost)
            allowed = permission != RunPermission.BLOCKED_HARD_LIMIT
            if permission == RunPermission.WARN_SOFT_LIMIT:
                await mark_soft_limit_notified(conn, self.discussion_id)
                warn = True
            if allowed:
                await reserve_cost(conn, self.discussion_id, run.estimated_cost)
            seq = (await conn.execute(text("SELECT nextval('agent_run_seq')"))).scalar_one()
            run.run_ref = f"MSG-{seq:06d}"
            await conn.execute(
                text("""
                INSERT INTO agent_runs (id, seq_number, round_id, discussion_id, agent_id,
                    provider, model_used, is_escalated, prompt_version_id, context_snapshot_id,
                    status, error_message, max_retries, audit_payload, started_at)
                VALUES (:id, :seq, :round, :disc, :agent, :provider, :model, :escalated,
                    :pv, :snap, :status, :error, :retries, CAST(:audit AS JSONB),
                    CASE WHEN :status = 'RUNNING' THEN NOW() END)
            """),
                {
                    "id": run.run_ref,
                    "seq": seq,
                    "round": round_ref,
                    "disc": self.discussion_id,
                    "agent": run.agent_id,
                    "provider": agent["provider"],
                    "model": run.model,
                    "escalated": run.model != agent["model_normal"],
                    "pv": self.prompt_versions.get(run.agent_id),
                    "snap": self.snapshot,
                    "status": "RUNNING" if allowed else "SKIPPED",
                    "error": None if allowed else "cost hard limit reached",
                    "retries": PROVIDER_RETRIES,
                    "audit": json.dumps(
                        {"prompt": run.prompt, "estimated_cost_usd": run.estimated_cost}
                    ),
                },
            )
            if not allowed:
                await audit_log(
                    conn,
                    actor="system",
                    action="AGENT_RUN_BLOCKED_HARD_LIMIT",
                    entity_type="agent_run",
                    entity_id=run.run_ref,
                )
        if warn:
            await _safe_send(
                self.chat,
                f"💸 {self.discussion_id} passed its soft cost limit. "
                "The council continues until the hard limit.",
            )
        return allowed

    async def agent_finished(self, discussion, round_ref, run: RunResult) -> None:
        if run.status == "SKIPPED":
            return
        response = run.response
        async with get_engine().begin() as conn:
            await conn.execute(
                text("""
                UPDATE agent_runs SET status=:status, error_message=:error,
                    response_structured=CAST(:structured AS JSONB), response_raw=:raw,
                    response_valid=:valid, model_used=COALESCE(:model, model_used),
                    completed_at=NOW()
                WHERE id=:id
            """),
                {
                    "id": run.run_ref,
                    "status": run.status,
                    "error": run.error,
                    "structured": response.parsed.model_dump_json()
                    if response and response.parsed
                    else None,
                    "raw": response.raw_text if response else None,
                    "valid": response.is_valid if response else None,
                    "model": response.model_used if response else None,
                },
            )
            if response is not None:
                await record_actual_cost(
                    conn,
                    self.discussion_id,
                    run.run_ref,
                    run.agent_id,
                    self.agents[run.agent_id]["provider"],
                    response.model_used,
                    response.tokens_input,
                    response.tokens_output,
                    response.cost_usd,
                    run.estimated_cost,
                )
            else:  # failed/timed out: release the reservation (cost of failed call unknown)
                await conn.execute(
                    text("""
                    UPDATE discussions
                       SET cost_reserved_usd=GREATEST(0, cost_reserved_usd - :amount)
                     WHERE id=:id
                """),
                    {"amount": Decimal(str(run.estimated_cost)), "id": self.discussion_id},
                )

    async def round_finished(self, discussion, round_ref, results, quorum_ok) -> None:
        completed = sum(r.status == "COMPLETED" for r in results)
        if not quorum_ok:
            state = "FAILED"
        elif completed == len(results):
            state = "COMPLETE"
        else:
            state = "PARTIAL_COMPLETE"
        async with get_engine().begin() as conn:
            await conn.execute(
                text("UPDATE rounds SET state=:state, completed_at=NOW() WHERE id=:id"),
                {"state": state, "id": round_ref},
            )

    async def disagreement_updated(self, discussion, disagreement: dict) -> None:
        latest = discussion.rounds[-1]["results"] if discussion.rounds else []
        by_position: dict[str, RunResult] = {}
        for r in latest:
            if r.status == "COMPLETED" and r.response and r.response.parsed:
                by_position.setdefault(r.response.parsed.position, r)
        sides = list(by_position.items())[:2]
        async with get_engine().begin() as conn:
            if "db_id" not in disagreement:
                seq = (await conn.execute(text("SELECT nextval('disagreement_seq')"))).scalar_one()
                disagreement["db_id"] = f"DIS-{seq:03d}"
                (a_label, a_run), (b_label, b_run) = (
                    sides if len(sides) == 2 else (sides + [(None, None)] * 2)[:2]
                )
                await conn.execute(
                    text("""
                    INSERT INTO disagreements (id, seq_number, discussion_id, round_id, claim,
                        position_a_agent, position_a_label, position_a_run_id,
                        position_b_agent, position_b_label, position_b_run_id,
                        status, related_run_ids)
                    VALUES (:id, :seq, :disc,
                        (SELECT id FROM rounds WHERE discussion_id=:disc
                          ORDER BY round_number DESC LIMIT 1),
                        :claim, :a_agent, :a_label, :a_run, :b_agent, :b_label, :b_run,
                        'DEBATING', :runs)
                """),
                    {
                        "id": disagreement["db_id"],
                        "seq": seq,
                        "disc": self.discussion_id,
                        "claim": "Council positions differ: "
                        + ", ".join(disagreement["positions"]),
                        "a_agent": a_run.agent_id if a_run else None,
                        "a_label": a_label,
                        "a_run": a_run.run_ref if a_run else None,
                        "b_agent": b_run.agent_id if b_run else None,
                        "b_label": b_label,
                        "b_run": b_run.run_ref if b_run else None,
                        "runs": [r.run_ref for r in latest if r.run_ref],
                    },
                )
            else:
                await conn.execute(
                    text("""
                    UPDATE disagreements SET cycles_completed=:cycles, status=:status,
                        updated_at=NOW(),
                        resolved_at=CASE WHEN :status='RESOLVED' THEN NOW() END
                    WHERE id=:id
                """),
                    {
                        "cycles": disagreement["cycles_completed"],
                        "status": disagreement["status"],
                        "id": disagreement["db_id"],
                    },
                )


async def _safe_send(chat: tuple[int, int | None], message: str, markup: Any = None) -> int | None:
    """Send to Telegram; a delivery failure is logged, never fatal to stored state."""
    try:
        return await get_client().send_message(chat[0], message, topic_id=chat[1], markup=markup)
    except Exception:
        logger.exception("Telegram send failed")
        return None


async def _mark_event(update_id: int, status: str, entity_id: str | None = None) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("""
            UPDATE telegram_events SET processing_status=:status, processed_at=NOW(),
                related_entity_type=COALESCE(:etype, related_entity_type),
                related_entity_id=COALESCE(:eid, related_entity_id)
            WHERE telegram_update_id=:uid
        """),
            {
                "status": status,
                "uid": update_id,
                "etype": "discussion" if entity_id else None,
                "eid": entity_id,
            },
        )


# ---------------------------------------------------------------------------
# /council → full council run
# ---------------------------------------------------------------------------
def build_review(discussion: Discussion) -> dict:
    """What the CEO is shown; stored immutably on the CEO review."""
    final = discussion.rounds[-1]["results"] if discussion.rounds else []
    before_synthesis = discussion.rounds[-2]["results"] if len(discussion.rounds) > 1 else final
    chosen = discussion.synthesis_result
    parsed = chosen.response.parsed if chosen and chosen.response else None
    dissent, risks, questions = [], [], []
    for r in final:
        if r.status == "COMPLETED" and r.response and r.response.parsed:
            dissent += [
                {**d.model_dump(), "raised_by": r.agent_id} for d in r.response.parsed.disagreements
            ]
            risks += [x.model_dump() for x in r.response.parsed.risks]
            questions += r.response.parsed.questions
    severity = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    risks.sort(key=lambda x: severity.get(x.get("impact", "LOW"), 4))
    return {
        "synthesis": discussion.synthesis,
        "recommendation": parsed.recommendation if parsed else None,
        "synthesized_by": chosen.agent_id if chosen else None,
        "recommendations": positions_of(before_synthesis),
        "dissent": dissent,
        "risks": risks,
        "questions": list(dict.fromkeys(questions)),
        "debate": discussion.disagreements[0] if discussion.disagreements else None,
        "requested_agents": list(discussion.agents),
        "completed_agents": [r.agent_id for r in final if r.status == "COMPLETED"],
        "options": ["APPROVED", "REJECTED", "DEFERRED"],
    }


@register_handler("COMPLETE_ROUND")
async def handle_council_command(payload: dict) -> None:
    update = payload["update"]
    update_id = int(update["update_id"])
    message = update.get("message") or {}
    chat = _chat(update)
    command, question = parse_command(str(message.get("text", "")))
    if command not in {"council", "council_critical"}:
        await _safe_send(chat, fmt.HELP_TEXT)
        await _mark_event(update_id, "DONE")
        return
    if not question:
        await _safe_send(chat, "Please add a question, e.g. /council Postgres or MongoDB?")
        await _mark_event(update_id, "DONE")
        return
    criticality = "CRITICAL" if command == "council_critical" else "NORMAL"
    sender = message.get("from") or {}

    async with get_engine().begin() as conn:
        existing = (
            await conn.execute(
                text("""
            SELECT d.id, d.base_state FROM telegram_events e
              JOIN discussions d ON d.id = e.related_entity_id
             WHERE e.telegram_update_id=:uid AND e.related_entity_type='discussion'
        """),
                {"uid": update_id},
            )
        ).first()
        if existing and existing.base_state in FINISHED_STATES:
            return  # retried job: the council already reported for this command
        all_agents = await load_agents(conn)
        agents = [a for a in all_agents if PROVIDER_KEYS[a["provider"]]()]
        missing = [a["id"] for a in all_agents if a not in agents]
        ceo_id = await ensure_ceo_user(conn, int(sender.get("id", 0)), sender.get("username"))
        project_id = await ensure_default_project(conn)
        snap_id, context = await take_context_snapshot(conn, project_id)
        prompt_versions = {
            a["id"]: await ensure_prompt_version(conn, profile_of(a)) for a in agents
        }
        if existing:
            discussion_id, is_new = existing.id, False
        else:
            seq = (await conn.execute(text("SELECT nextval('discussion_seq')"))).scalar_one()
            discussion_id, is_new = f"DISC-{seq:03d}", True
            await conn.execute(
                text("""
                INSERT INTO discussions (id, seq_number, project_id, title, question,
                    criticality, context_snapshot_id, telegram_topic_id, telegram_group_id,
                    cost_soft_limit_usd, cost_hard_limit_usd, agents_invited, created_by)
                VALUES (:id, :seq, :project, :title, :question, :crit, :snap, :topic, :chat,
                    :soft, :hard, :agents, :ceo)
            """),
                {
                    "id": discussion_id,
                    "seq": seq,
                    "project": project_id,
                    "title": question[:120],
                    "question": question,
                    "crit": criticality,
                    "snap": snap_id,
                    "topic": chat[1],
                    "chat": chat[0],
                    "soft": Decimal(str(settings.default_soft_limit_usd)),
                    "hard": Decimal(str(settings.default_hard_limit_usd)),
                    "agents": [a["id"] for a in agents],
                    "ceo": ceo_id,
                },
            )
            await conn.execute(
                text("""
                UPDATE telegram_events SET related_entity_type='discussion',
                    related_entity_id=:id, parsed_intent=:intent, processing_status='PROCESSING'
                WHERE telegram_update_id=:uid
            """),
                {"id": discussion_id, "intent": command.upper(), "uid": update_id},
            )
            await audit_log(
                conn,
                actor="CEO",
                actor_user_id=ceo_id,
                action="DISCUSSION_CREATED",
                entity_type="discussion",
                entity_id=discussion_id,
                detail={"question": question, "criticality": criticality, "snapshot": snap_id},
            )

    if len(agents) < 2 or not any(a["id"] != MODERATOR_ID for a in agents):
        reason = "fewer than two agents have API keys configured"
        if missing:
            reason += f" (missing: {', '.join(missing)})"
        await _pause(discussion_id, chat, reason)
        await _mark_event(update_id, "DONE", discussion_id)
        return

    if is_new:
        await _safe_send(
            chat, fmt.started_message(discussion_id, question, [a["id"] for a in agents])
        )

    by_id = {a["id"]: a for a in agents}
    models = {}
    for a in agents:
        escalate = criticality == "CRITICAL" and a["escalation_policy"] == "AUTO_ON_CRITICAL"
        models[a["id"]] = (escalate and a["model_escalated"]) or a["model_normal"]
    engine = CouncilEngine(
        {a["id"]: adapter_factory(a["provider"]) for a in agents},
        agent_timeout_s=AGENT_TIMEOUT_S,
        models=models,
        profiles={a["id"]: profile_of(a) for a in agents},
        recorder=DbRecorder(discussion_id, by_id, prompt_versions, snap_id, chat),
        retries=PROVIDER_RETRIES,
        max_tokens=max(a["max_tokens"] for a in agents),
    )
    discussion = Discussion(
        id=discussion_id,
        question=question,
        agents=[a["id"] for a in agents],
        context=context,
        criticality=criticality,
    )
    try:
        await engine.run_to_ceo_review(discussion)
    except QuorumNotReached as exc:
        await _pause(discussion_id, chat, str(exc))
        await _mark_event(update_id, "DONE", discussion_id)
        return

    review = build_review(discussion)
    review_id = str(uuid.uuid4())
    async with get_engine().begin() as conn:
        await conn.execute(
            text("""
            INSERT INTO ceo_reviews (id, decision_action_key, discussion_id, synthesis_run_id,
                options_presented)
            VALUES (:id, :key, :disc, :run, CAST(:options AS JSONB))
        """),
            {
                "id": review_id,
                "key": decision_action_key(discussion_id, review_id),
                "disc": discussion_id,
                "run": discussion.synthesis_result.run_ref if discussion.synthesis_result else None,
                "options": json.dumps(review, ensure_ascii=False),
            },
        )
        cost = (
            await conn.execute(
                text("""
            UPDATE discussions SET active_ceo_review_id=:review, base_state='CEO_REVIEW',
                updated_at=NOW()
            WHERE id=:id RETURNING cost_total_usd
        """),
                {"review": review_id, "id": discussion_id},
            )
        ).scalar_one()
        await audit_log(
            conn,
            actor="system",
            action="CEO_REVIEW_OPENED",
            entity_type="ceo_review",
            entity_id=review_id,
            detail={"discussion_id": discussion_id},
        )

    msg_id = await _safe_send(
        chat,
        fmt.ceo_review_message(discussion_id, question, review, float(cost)),
        markup=decision_keyboard(review_id),
    )
    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE ceo_reviews SET telegram_msg_id=:m WHERE id=:id"),
            {"m": msg_id, "id": review_id},
        )
    await _mark_event(update_id, "DONE", discussion_id)


async def _pause(discussion_id: str, chat: tuple[int, int | None], reason: str) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("""
            UPDATE discussions SET previous_base_state=base_state, base_state='PAUSED',
                cost_reserved_usd=0, updated_at=NOW()
            WHERE id=:id
        """),
            {"id": discussion_id},
        )
        await audit_log(
            conn,
            actor="system",
            action="DISCUSSION_PAUSED",
            entity_type="discussion",
            entity_id=discussion_id,
            detail={"reason": reason},
        )
    await _safe_send(chat, fmt.paused_message(discussion_id, reason))


# ---------------------------------------------------------------------------
# CEO decisions — the PostgreSQL decision gate
# ---------------------------------------------------------------------------
async def record_decision(
    review_id: str, option: str, ceo_telegram_id: int, telegram_msg_id: int | None = None
) -> tuple[bool, dict | None]:
    """Record the CEO's decision exactly once.

    The CEO review row is locked FOR UPDATE, so concurrent button presses are
    serialized: the first wins, later ones see status DECIDED. Returns
    (accepted, decision) where decision describes the winning decision.
    """
    async with get_engine().begin() as conn:
        review = (
            (
                await conn.execute(
                    text("""
                SELECT r.id, r.status, r.options_presented, r.discussion_id,
                       d.project_id, d.context_snapshot_id, d.title
                  FROM ceo_reviews r JOIN discussions d ON d.id = r.discussion_id
                 WHERE r.id = CAST(:id AS UUID)
                   FOR UPDATE OF r
            """),
                    {"id": review_id},
                )
            )
            .mappings()
            .first()
        )
        if review is None:
            return False, None
        if review["status"] != "OPEN":
            winner = (
                (
                    await conn.execute(
                        text("""
                    SELECT id, status, discussion_id FROM decisions
                     WHERE ceo_review_id=:id ORDER BY decided_at LIMIT 1
                """),
                        {"id": review["id"]},
                    )
                )
                .mappings()
                .first()
            )
            return False, dict(winner) if winner else None

        ceo_id = await ensure_ceo_user(conn, ceo_telegram_id, None)
        options = review["options_presented"]
        if isinstance(options, str):
            options = json.loads(options)
        seq = (await conn.execute(text("SELECT nextval('decision_seq')"))).scalar_one()
        decision_id = f"DEC-{seq:03d}"
        await conn.execute(
            text("""
            INSERT INTO decisions (id, seq_number, discussion_id, project_id,
                context_snapshot_id, ceo_review_id, title, status, option_selected,
                options_json, agent_recs_json, dissent_json, telegram_msg_id, decided_by)
            VALUES (:id, :seq, :disc, :project, :snap, :review, :title, :status, :status,
                CAST(:options AS JSONB), CAST(:recs AS JSONB), CAST(:dissent AS JSONB),
                :msg, :ceo)
        """),
            {
                "id": decision_id,
                "seq": seq,
                "disc": review["discussion_id"],
                "project": review["project_id"],
                "snap": review["context_snapshot_id"],
                "review": review["id"],
                "title": review["title"],
                "status": option,
                "options": json.dumps(options.get("options", [])),
                "recs": json.dumps(options.get("recommendations", []), ensure_ascii=False),
                "dissent": json.dumps(options.get("dissent", []), ensure_ascii=False),
                "msg": telegram_msg_id,
                "ceo": ceo_id,
            },
        )
        await conn.execute(
            text("UPDATE ceo_reviews SET status='DECIDED', decided_at=NOW() WHERE id=:id"),
            {"id": review["id"]},
        )
        await conn.execute(
            text("""
            UPDATE discussions SET base_state=:status, updated_at=NOW(),
                closed_at=CASE WHEN :status <> 'DEFERRED' THEN NOW() END
            WHERE id=:id
        """),
            {"status": option, "id": review["discussion_id"]},
        )
        await audit_log(
            conn,
            actor="CEO",
            actor_user_id=ceo_id,
            action=f"DECISION_{option}",
            entity_type="decision",
            entity_id=decision_id,
            detail={"discussion_id": review["discussion_id"], "ceo_review_id": review_id},
        )
        return True, {"id": decision_id, "status": option, "discussion_id": review["discussion_id"]}


async def _status_text() -> str:
    async with get_engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                SELECT id, base_state, title, cost_total_usd FROM discussions
                 WHERE base_state NOT IN ('APPROVED','REJECTED','CANCELLED')
                 ORDER BY created_at DESC LIMIT 10
            """)
                )
            )
            .mappings()
            .all()
        )
        queue = (
            (
                await conn.execute(
                    text("""
                SELECT COUNT(*) FILTER (WHERE status='PENDING') AS pending,
                       COUNT(*) FILTER (WHERE status='RUNNING') AS running
                  FROM job_queue
            """)
                )
            )
            .mappings()
            .one()
        )
    lines = [f"📊 Queue: {queue['pending']} waiting, {queue['running']} running"]
    if not rows:
        lines.append("No open discussions.")
    for r in rows:
        lines.append(f"{r['id']} [{r['base_state']}] ${r['cost_total_usd']:.4f} — {r['title']}")
    lines.append(
        f"Timeouts: agent {AGENT_TIMEOUT_S}s, round 600s. "
        f"Cost limits per discussion: ${settings.default_soft_limit_usd:.2f} soft, "
        f"${settings.default_hard_limit_usd:.2f} hard."
    )
    return "\n".join(lines)


@register_handler("NOTIFY_CEO")
async def handle_ceo_update(payload: dict) -> None:
    update = payload["update"]
    update_id = int(update["update_id"])
    chat = _chat(update)
    client = get_client()

    if callback := update.get("callback_query"):
        parsed = parse_decision_callback(callback.get("data", ""))
        if parsed is None:
            await client.answer_callback(callback["id"], "Unknown button")
            await _mark_event(update_id, "DONE")
            return
        review_id, option = parsed
        message = callback.get("message") or {}
        accepted, decision = await record_decision(
            review_id, option, int(callback["from"]["id"]), message.get("message_id")
        )
        if decision is None:
            await client.answer_callback(callback["id"], "This review no longer exists")
        elif accepted:
            await client.answer_callback(callback["id"], f"Recorded: {option.lower()}")
            if message.get("message_id"):
                try:
                    await client.clear_buttons(chat[0], int(message["message_id"]))
                except Exception:
                    logger.warning("Could not remove decision buttons", exc_info=True)
            await _safe_send(
                chat, fmt.decision_message(decision["discussion_id"], decision["id"], option)
            )
        else:
            await client.answer_callback(
                callback["id"], f"Already decided: {decision['status'].lower()} ({decision['id']})"
            )
        await _mark_event(update_id, "DONE")
        return

    message = update.get("message") or {}
    command, _ = parse_command(str(message.get("text", "")))
    if command == "status":
        await _safe_send(chat, await _status_text())
    elif command in {"help", "start"}:
        await _safe_send(chat, fmt.HELP_TEXT)
    await _mark_event(update_id, "DONE")
