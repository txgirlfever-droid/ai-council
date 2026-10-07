from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def decision_keyboard(discussion_id: str, review_id: str) -> InlineKeyboardMarkup:
    prefix = f"decision:{discussion_id}:{review_id}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Approve", callback_data=f"{prefix}:APPROVED"),
                InlineKeyboardButton("Reject", callback_data=f"{prefix}:REJECTED"),
                InlineKeyboardButton("Defer", callback_data=f"{prefix}:DEFERRED"),
            ]
        ]
    )
