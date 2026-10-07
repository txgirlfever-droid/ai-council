"""Timeout/retry boundary for external provider calls."""

from __future__ import annotations

import asyncio

from council.agents.base import AgentResponse, ProviderAdapter


async def run_with_timeout(
    adapter: ProviderAdapter,
    prompt: str,
    model: str,
    max_tokens: int,
    timeout_s: float,
    retries: int = 2,
) -> AgentResponse:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with asyncio.timeout(timeout_s):
                return await adapter.complete(prompt, model, max_tokens)
        except TimeoutError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(min(2**attempt, 4))
    assert last_error is not None
    raise last_error
