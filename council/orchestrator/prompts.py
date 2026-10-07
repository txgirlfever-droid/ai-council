"""Prompt templates for each council round.

Every prompt ends with the same structured-response contract (Correction #10),
so adapters can parse answers into ``CouncilResponse``. The role preamble is
versioned in ``agent_prompt_versions`` by its SHA-256 hash (Correction #9).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field

RESPONSE_CONTRACT = """\
Reply with ONE JSON object and nothing else (no prose before or after). Schema:
{
  "summary": "1-3 sentence statement of your position",
  "position": "OPTION_A | OPTION_B | ALTERNATIVE | NEUTRAL",
  "recommendation": "what the CEO should do, one sentence",
  "confidence": 0.0-1.0,
  "agreements": [{"agent_id": "...", "point": "..."}],
  "disagreements": [{"agent_id": "...", "point": "...", "reason": "..."}],
  "risks": [{"title": "...", "likelihood": "LOW|MEDIUM|HIGH",
             "impact": "LOW|MEDIUM|HIGH|CRITICAL", "description": "..."}],
  "assumptions": ["..."],
  "questions": ["questions you need the CEO to answer"]
}
Use OPTION_A for the first option the question names, OPTION_B for the second,
ALTERNATIVE for a different path, NEUTRAL if you cannot decide. Be concise."""


@dataclass(slots=True)
class AgentProfile:
    """The parts of an ``agents`` row the prompts need."""

    agent_id: str
    display_name: str = ""
    role_name: str = "Council member"
    responsibilities: Sequence[str] = field(default_factory=tuple)
    restrictions: Sequence[str] = field(default_factory=tuple)


def role_preamble(profile: AgentProfile) -> str:
    lines = [
        f"You are {profile.display_name or profile.agent_id.upper()}, "
        f"the {profile.role_name} on an AI advisory council.",
        "The CEO is the final authority: you analyze and recommend; you never decide.",
    ]
    if profile.responsibilities:
        lines.append("Your responsibilities: " + "; ".join(profile.responsibilities) + ".")
    if profile.restrictions:
        lines.append("You must not: " + "; ".join(profile.restrictions) + ".")
    return "\n".join(lines)


def preamble_hash(profile: AgentProfile) -> str:
    return hashlib.sha256(role_preamble(profile).encode()).hexdigest()


def _context_block(context: dict) -> str:
    if not context:
        return "Project context: none recorded yet."
    return "Project context (immutable snapshot):\n" + json.dumps(
        context, ensure_ascii=False, indent=1, sort_keys=True
    )


def _positions_block(positions: Sequence[dict]) -> str:
    if not positions:
        return "(no valid positions were returned)"
    return "\n".join(
        f"- {p['agent_id']}: [{p['position']}] {p['summary']}"
        + (f" Recommendation: {p['recommendation']}" if p.get("recommendation") else "")
        for p in positions
    )


def build_prompt(
    round_type: str,
    profile: AgentProfile,
    question: str,
    context: dict,
    positions: Sequence[dict] = (),
    cycle: int = 0,
) -> str:
    """Build the full prompt for one agent in one round."""
    if round_type == "INDEPENDENT_REVIEW":
        task = (
            "Independently review the CEO's question below. Do not assume what other "
            "council members think."
        )
    elif round_type == "CROSS_REVIEW":
        task = (
            "Here are the council's independent positions. Critique them: say where you "
            "agree, where you disagree and why. You may change your position.\n"
            + _positions_block(positions)
        )
    elif round_type == "DEBATE":
        task = (
            f"Debate cycle {cycle} of at most 3. The council still disagrees:\n"
            + _positions_block(positions)
            + "\nRespond to the strongest opposing argument. Change your position only if "
            "you are genuinely persuaded; otherwise defend it with specifics."
        )
    elif round_type == "SYNTHESIS":
        task = (
            "Write the council's synthesis for the CEO from these final positions. Put the "
            "majority view in 'summary' and 'recommendation', and keep every dissent in "
            "'disagreements' — never present a split council as unanimous.\n"
            + _positions_block(positions)
        )
    else:  # pragma: no cover - guarded by the round_type CHECK constraint
        raise ValueError(f"Unknown round type: {round_type}")

    return "\n\n".join(
        [
            role_preamble(profile),
            _context_block(context),
            f"CEO question: {question}",
            f"Round: {round_type}. {task}",
            RESPONSE_CONTRACT,
        ]
    )
