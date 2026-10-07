"""Plain-text messages the bot posts to the CEO. Concise by default."""

from __future__ import annotations

from collections.abc import Sequence

from council.orchestrator.quorum import partial_result_label

EMOJI = {"codex": "🤖", "claude": "🔬", "gemini": "🔭"}

HELP_TEXT = (
    "AI Council commands:\n"
    "/council <question> — ask the council (e.g. /council Postgres or MongoDB?)\n"
    "/council_critical <question> — every agent must answer\n"
    "/status — open discussions and queue\n"
    "/help — this message"
)


def started_message(discussion_id: str, question: str, agents: Sequence[str]) -> str:
    names = ", ".join(a.upper() for a in agents)
    return f"🏛 {discussion_id} started\nQ: {question}\nCouncil: {names}\nI will report back here."


def ceo_review_message(
    discussion_id: str,
    question: str,
    review: dict,
    cost_usd: float,
) -> str:
    """``review`` is the ``options_presented`` JSON stored on the CEO review."""
    lines = [
        f"🏛 {discussion_id} — CEO review",
        f"Q: {question}",
        partial_result_label(review["completed_agents"], review["requested_agents"]),
        "",
        f"Synthesis: {review['synthesis']}",
    ]
    if review.get("recommendation"):
        lines.append(f"Recommendation: {review['recommendation']}")

    lines += ["", "Positions:"]
    for p in review["recommendations"]:
        emoji = EMOJI.get(p["agent_id"], "•")
        lines.append(f"{emoji} {p['agent_id'].upper()} [{p['position']}] {p['summary']}")

    for d in review.get("dissent") or []:
        who = (d.get("raised_by") or "?").upper()
        lines.append(
            f"⚠️ {who} disagrees with {d['agent_id'].upper()}: {d['point']} — {d['reason']}"
        )
    if review.get("debate"):
        debate = review["debate"]
        lines.append(f"Debate: {debate['cycles_completed']} cycle(s), {debate['status'].lower()}")
    risks = review.get("risks") or []
    if risks:
        lines += ["", "Top risks:"]
        lines += [f"- {r['title']} ({r['likelihood']}/{r['impact']})" for r in risks[:3]]
    questions = review.get("questions") or []
    if questions:
        lines += ["", "Questions for you:"]
        lines += [f"- {q}" for q in questions[:3]]
    lines += ["", f"Cost so far: ${cost_usd:.4f}", "Your decision:"]
    return "\n".join(lines)


def decision_message(discussion_id: str, decision_id: str, option: str) -> str:
    icon = {"APPROVED": "✅", "REJECTED": "❌", "DEFERRED": "⏸"}.get(option, "•")
    return f"{icon} {decision_id}: CEO {option.lower()} {discussion_id}. Recorded permanently."


def paused_message(discussion_id: str, reason: str) -> str:
    return (
        f"⚠️ {discussion_id} paused: {reason}\n"
        "No result was invented. Check provider keys/credits, then ask again with /council."
    )
