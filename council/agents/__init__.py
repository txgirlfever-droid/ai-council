from council.agents.anthropic_adapter import AnthropicAdapter
from council.agents.google_adapter import GoogleAdapter
from council.agents.openai_adapter import OpenAIAdapter


def create_adapter(provider: str):
    factories = {
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
        "google": GoogleAdapter,
    }
    try:
        return factories[provider]()
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {provider}") from exc


__all__ = ["create_adapter", "OpenAIAdapter", "AnthropicAdapter", "GoogleAdapter"]
