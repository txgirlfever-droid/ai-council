"""
Anthropic provider adapter.
"""

from __future__ import annotations

from council.agents.base import AgentResponse, ProviderAdapter
from council.config import settings


class AnthropicAdapter(ProviderAdapter):
    def __init__(self):
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000) -> AgentResponse:
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text if response.content else ""
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = self._estimate_from_usage(model, tokens_in, tokens_out)
        return self._parse_response(raw, model, "anthropic", tokens_in, tokens_out, cost)

    def estimate_cost(self, prompt: str, model: str) -> float:
        tokens = len(prompt) // 4
        return self._estimate_from_usage(model, tokens, 500)

    def _estimate_from_usage(self, model: str, tokens_in: int, tokens_out: int) -> float:
        rates = {
            "claude-sonnet-4-6": (0.003, 0.015),
            "claude-opus-4-5": (0.015, 0.075),
        }
        in_rate, out_rate = rates.get(model, (0.008, 0.024))
        return (tokens_in * in_rate + tokens_out * out_rate) / 1000
