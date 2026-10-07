"""
Quorum logic — Correction #4, N1 LOCKED.

Formula: max(2, ceil(2/3 × N))
Rule: At least 1 non-Codex agent must complete for quorum to count.
"""

from __future__ import annotations

import math


def compute_quorum_threshold(agents_requested: list[str]) -> int:
    """
    N1 LOCKED: max(2, ceil(2/3 × N))

    Examples:
      3 agents → 2
      4 agents → 3
      5 agents → 4
    """
    n = len(agents_requested)
    return max(2, math.ceil(2 / 3 * n))


def quorum_achieved(
    agents_requested: list[str],
    completed_agents: list[str],
    quorum_threshold: int,
    requires_non_codex: bool = True,
) -> bool:
    """
    Correction #4: Quorum NOT achieved if only Codex completed.
    Must have ≥1 non-Codex agent in the completed set.
    """
    threshold_met = len(completed_agents) >= quorum_threshold

    if requires_non_codex:
        non_codex_completed = [a for a in completed_agents if a != "codex"]
        non_codex_present = len(non_codex_completed) >= 1
        return threshold_met and non_codex_present

    return threshold_met


def partial_result_label(completed: list[str], total: list[str]) -> str:
    """
    Correction #4: Never use 'UNANIMOUS' for partial results.
    Returns e.g. 'PARTIAL COUNCIL RESULT (2/3 agents)'
    """
    n_completed = len(completed)
    n_total = len(total)
    if n_completed == n_total:
        return f"FULL COUNCIL RESULT ({n_completed}/{n_total} agents)"
    return f"PARTIAL COUNCIL RESULT ({n_completed}/{n_total} agents)"
