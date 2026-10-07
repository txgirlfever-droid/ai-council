"""
Provider adapter base class and factory.
Correction: Role ≠ Model. Models are config, not constants.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from council.schemas.council_response import CouncilResponse, parse_council_response


@dataclass
class AgentResponse:
    raw_text: str
    parsed: CouncilResponse | None
    is_valid: bool
    tokens_input: int
    tokens_output: int
    cost_usd: float
    model_used: str
    provider: str


class ProviderAdapter(ABC):
    """
    Abstract base for all AI provider adapters.
    Role ≠ Model: get_model reads from DB config, never hard-codes.
    """

    @abstractmethod
    async def complete(self, prompt: str, model: str, max_tokens: int) -> AgentResponse: ...

    @abstractmethod
    def estimate_cost(self, prompt: str, model: str) -> float: ...

    def get_model(self, model_normal: str, model_escalated: str | None, is_escalated: bool) -> str:
        if is_escalated and model_escalated:
            return model_escalated
        return model_normal

    def _parse_response(
        self, raw: str, model: str, provider: str, tokens_in: int, tokens_out: int, cost: float
    ) -> AgentResponse:
        parsed, is_valid = parse_council_response(raw)
        return AgentResponse(
            raw_text=raw,
            parsed=parsed,
            is_valid=is_valid,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_usd=cost,
            model_used=model,
            provider=provider,
        )
