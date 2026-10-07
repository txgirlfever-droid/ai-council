"""
Google Gemini provider adapter.
"""

from __future__ import annotations

from council.agents.base import AgentResponse, ProviderAdapter
from council.config import settings


class GoogleAdapter(ProviderAdapter):
    def __init__(self):
        import google.generativeai as genai

        genai.configure(api_key=settings.google_api_key)
        self._genai = genai

    async def complete(self, prompt: str, model: str, max_tokens: int = 4000) -> AgentResponse:
        import asyncio

        gemini = self._genai.GenerativeModel(model)
        # google-generativeai sync → run in executor
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: gemini.generate_content(
                prompt,
                generation_config={"max_output_tokens": max_tokens},
            ),
        )
        raw = response.text if hasattr(response, "text") else ""
        tokens_in = getattr(response.usage_metadata, "prompt_token_count", 0)
        tokens_out = getattr(response.usage_metadata, "candidates_token_count", 0)
        cost = self._estimate_from_usage(model, tokens_in, tokens_out)
        return self._parse_response(raw, model, "google", tokens_in, tokens_out, cost)

    def estimate_cost(self, prompt: str, model: str) -> float:
        tokens = len(prompt) // 4
        return self._estimate_from_usage(model, tokens, 500)

    def _estimate_from_usage(self, model: str, tokens_in: int, tokens_out: int) -> float:
        rates = {
            # Unknown models fall back to the default rate below; add real
            # prices here when you know them.
        }
        in_rate, out_rate = rates.get(model, (0.002, 0.006))
        return (tokens_in * in_rate + tokens_out * out_rate) / 1000
