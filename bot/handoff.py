"""Передача диалога живому оператору."""
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

# Сколько последних сообщений диалога показать оператору в карточке.
CONTEXT_MESSAGES = 6


def _card(user: dict, reason: str, summary: str, history: list[dict]) -> str:
    username = f"@{user['username']}" if user.get("username") else "—"
    lines = []
    for msg in history[-CONTEXT_MESSAGES:]:
        who = "👤" if msg["role"] == "user" else "🤖"
        text = msg["content"] if len(msg["content"]) <= 300 else msg["content"][:300] + "…"
        lines.append(f"{who} {escape(text)}")
    dialog = "\n".join(lines) or "—"
    return (
        "🆘 <b>Нужен оператор</b>\n\n"
        f"Клиент: {escape(user['full_name'])} ({escape(username)}, id <code>{user['id']}</code>)\n"
        f"Причина: {escape(reason)}\n"
        f"Суть: {escape(summary)}\n\n"
        f"<b>Последние сообщения:</b>\n{dialog}\n\n"
        "↩️ Ответьте на это сообщение (reply), и ответ уйдёт клиенту."
    )


async def escalate(bot: Bot, db: Database, config: Config, user_id: int, reason: str, summary: str) -> bool:
    """Шлёт карточку в чат операторов (или каждому админу) и запоминает, к какому клиенту она относится."""
    user = await db.get_user(user_id)
    history = await db.history(user_id, CONTEXT_MESSAGES)
    text = _card(user, reason, summary, history)

    targets = [config.operator_chat_id] if config.operator_chat_id else sorted(config.admin_ids)
    delivered = False
    for chat_id in targets:
        try:
            sent = await bot.send_message(chat_id, text)
        except TelegramAPIError as exc:
            log.error("Не удалось передать диалог оператору %s: %s", chat_id, exc)
            continue
        await db.add_handoff(sent.chat.id, sent.message_id, user_id, reason)
        delivered = True
    if not targets:
        log.warning("Некому передать диалог: не заданы ни OPERATOR_CHAT_ID, ни ADMIN_IDS")
    return delivered
