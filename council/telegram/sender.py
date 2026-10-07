"""Telegram output boundary.

All bot output goes through a ``TelegramClient`` so tests (and dry runs) can
swap in a recording client with ``set_client``. Messages are sent as plain text
(no parse mode), so agent output can never break Telegram formatting.
"""

from __future__ import annotations

from typing import Any, Protocol

from council.config import settings

MAX_MESSAGE_CHARS = 4000  # Telegram's hard limit is 4096


def clip(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TelegramClient(Protocol):
    async def send_message(
        self, chat_id: int, text: str, *, topic_id: int | None = None, markup: Any = None
    ) -> int: ...

    async def answer_callback(self, callback_query_id: str, text: str) -> None: ...

    async def clear_buttons(self, chat_id: int, message_id: int) -> None: ...


class BotClient:
    """Real client backed by python-telegram-bot."""

    def __init__(self, token: str):
        from telegram import Bot

        self._bot = Bot(token)

    async def send_message(self, chat_id, text, *, topic_id=None, markup=None) -> int:
        message = await self._bot.send_message(
            chat_id=chat_id,
            text=clip(text),
            message_thread_id=topic_id,
            reply_markup=markup,
        )
        return message.message_id

    async def answer_callback(self, callback_query_id, text) -> None:
        await self._bot.answer_callback_query(callback_query_id, text=clip(text, 190))

    async def clear_buttons(self, chat_id, message_id) -> None:
        await self._bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )


_client: TelegramClient | None = None


def get_client() -> TelegramClient:
    global _client
    if _client is None:
        if not settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        _client = BotClient(settings.telegram_bot_token)
    return _client


def set_client(client: TelegramClient | None) -> None:
    """Override the client (tests, dry runs). ``None`` restores the default."""
    global _client
    _client = client


async def send_message(chat_id: int, text: str, *, topic_id: int | None = None, markup=None) -> int:
    return await get_client().send_message(chat_id, text, topic_id=topic_id, markup=markup)
