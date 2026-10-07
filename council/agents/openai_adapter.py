"""
OpenAI provider adapter.
"""

from __future__ import annotations

from council.agents.base import AgentResponse, ProviderAdapter
from council.config import settings


class OpenAIAdapter(ProviderAdapter):
    def __init__(self):
        import openai

        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000) -> AgentResponse:
        response = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        raw = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        cost = self._estimate_from_usage(model, tokens_in, tokens_out)
        return self._parse_response(raw, model, "openai", tokens_in, tokens_out, cost)

    def estimate_cost(self, prompt: str, model: str) -> float:
        # Rough estimate: ~4 chars per token
        tokens = len(prompt) // 4
        return self._estimate_from_usage(model, tokens, 500)

    def _estimate_from_usage(self, model: str, tokens_in: int, tokens_out: int) -> float:
        # Approximate pricing (update as needed — not stored in code as permanent law)
        rates = {
            "gpt-4o": (0.005, 0.015),
            "o1": (0.015, 0.060),
        }
        in_rate, out_rate = rates.get(model, (0.01, 0.03))
        return (tokens_in * in_rate + tokens_out * out_rate) / 1000
