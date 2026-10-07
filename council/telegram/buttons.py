"""CEO decision buttons.

Telegram limits callback_data to 64 bytes, so buttons carry only the CEO review
id and a one-letter option: ``d:<review uuid>:<A|R|D>`` (40 bytes).
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

OPTION_CODES = {"A": "APPROVED", "R": "REJECTED", "D": "DEFERRED"}


def decision_callback_data(review_id: str, option: str) -> str:
    code = next(c for c, name in OPTION_CODES.items() if name == option)
    return f"d:{review_id}:{code}"


def parse_decision_callback(data: str) -> tuple[str, str] | None:
    """Return (review_id, option) or None if the data is not a decision button."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "d" or parts[2] not in OPTION_CODES:
        return None
    return parts[1], OPTION_CODES[parts[2]]


def decision_keyboard(review_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve", callback_data=decision_callback_data(review_id, "APPROVED")
                ),
                InlineKeyboardButton(
                    "❌ Reject", callback_data=decision_callback_data(review_id, "REJECTED")
                ),
                InlineKeyboardButton(
                    "⏸ Defer", callback_data=decision_callback_data(review_id, "DEFERRED")
                ),
            ]
        ]
    )
