"""Point Telegram at this server: ``python -m council.webhook.register``.

Uses WEBHOOK_URL and WEBHOOK_SECRET from .env, so Telegram sends the secret in
the X-Telegram-Bot-Api-Secret-Token header that the webhook checks.
"""

from __future__ import annotations

import asyncio

from council.config import settings


async def register() -> None:
    from telegram import Bot

    if not settings.telegram_bot_token or not settings.webhook_url:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and WEBHOOK_URL in .env first.")
    bot = Bot(settings.telegram_bot_token)
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.webhook_secret or None,
        allowed_updates=["message", "callback_query"],
    )
    info = await bot.get_webhook_info()
    print(f"Webhook set to {info.url} (pending updates: {info.pending_update_count})")


def main() -> None:
    asyncio.run(register())


if __name__ == "__main__":
    main()
