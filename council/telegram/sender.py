"""Idempotent Telegram sender backed by the PostgreSQL job/result ledger."""

from __future__ import annotations

from telegram import Bot

from council.config import settings


async def send_message(chat_id: int, text: str, *, topic_id: int | None = None, markup=None) -> int:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    bot = Bot(settings.telegram_bot_token)
    message = await bot.send_message(
        chat_id=chat_id,
        text=text,
        message_thread_id=topic_id,
        reply_markup=markup,
    )
    return message.message_id
