"""
Quorum logic tests — N1 LOCKED.
Tests: formula correctness, non-Codex rule, partial result labeling.
"""

from council.orchestrator.quorum import (
    compute_quorum_threshold,
    partial_result_label,
    quorum_achieved,
)


class TestQuorumThreshold:
    def test_3_agents(self):
        assert compute_quorum_threshold(["codex", "claude", "gemini"]) == 2

    def test_4_agents(self):
        assert compute_quorum_threshold(["a", "b", "c", "d"]) == 3

    def test_5_agents(self):
        assert compute_quorum_threshold(["a", "b", "c", "d", "e"]) == 4

    def test_2_agents_minimum(self):
        # min 2 even with 2 agents (2/3*2 = 1.33 → ceil=2)
        assert compute_quorum_threshold(["a", "b"]) == 2

    def test_1_agent_still_minimum_2(self):
        # 1 agent: ceil(2/3) = 1, but max(2,1) = 2
        assert compute_quorum_threshold(["a"]) == 2


class TestQuorumAchieved:
    def test_quorum_met_with_non_codex(self):
        """Normal quorum: threshold met AND non-Codex present."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude", "gemini"],
                completed_agents=["codex", "claude"],
                quorum_threshold=2,
                requires_non_codex=True,
            )
            is True
        )

    def test_quorum_not_met_codex_only(self):
        """Correction #4: Codex alone cannot satisfy quorum."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude", "gemini"],
                completed_agents=["codex"],
                quorum_threshold=2,
                requires_non_codex=True,
            )
            is False
        )

    def test_quorum_not_met_count(self):
        """Threshold count not met."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude", "gemini"],
                completed_agents=["claude"],
                quorum_threshold=2,
                requires_non_codex=True,
            )
            is False
        )

    def test_quorum_met_all_non_codex(self):
        """All non-Codex completed → quorum met."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude", "gemini"],
                completed_agents=["claude", "gemini"],
                quorum_threshold=2,
                requires_non_codex=True,
            )
            is True
        )

    def test_quorum_without_non_codex_rule(self):
        """When non-Codex rule disabled, Codex can contribute to quorum."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude"],
                completed_agents=["codex", "claude"],
                quorum_threshold=2,
                requires_non_codex=False,
            )
            is True
        )

    def test_critical_all_responded(self):
        """CRITICAL discussions require ALL_RESPONDED (all 3 of 3)."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude", "gemini"],
                completed_agents=["codex", "claude", "gemini"],
                quorum_threshold=3,
                requires_non_codex=True,
            )
            is True
        )

    def test_critical_partial_fails(self):
        """CRITICAL with only 2/3 fails ALL_RESPONDED policy."""
        assert (
            quorum_achieved(
                agents_requested=["codex", "claude", "gemini"],
                completed_agents=["codex", "claude"],
                quorum_threshold=3,
                requires_non_codex=True,
            )
            is False
        )


class TestPartialResultLabel:
    def test_full_result(self):
        agents = ["codex", "claude", "gemini"]
        label = partial_result_label(agents, agents)
        assert "FULL COUNCIL RESULT" in label
        assert "3/3" in label

    def test_partial_result(self):
        label = partial_result_label(["claude", "gemini"], ["codex", "claude", "gemini"])
        assert "PARTIAL COUNCIL RESULT" in label
        assert "2/3" in label

    def test_never_says_unanimous_on_partial(self):
        label = partial_result_label(["claude"], ["codex", "claude", "gemini"])
        assert "UNANIMOUS" not in label
