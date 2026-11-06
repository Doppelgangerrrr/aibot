import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message, TelegramObject

from ..config import Config
from ..db import Database
from ..knowledge import load_knowledge
from ..llm import SupportAI

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, config: Config) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and user.id in config.admin_ids


class OperatorReply(BaseFilter):
    """Срабатывает на reply к карточке «Нужен оператор» и передаёт в хендлер id клиента."""

    async def __call__(self, message: Message, db: Database) -> bool | dict:
        if message.reply_to_message is None:
            return False
        client_id = await db.handoff_user(message.chat.id, message.reply_to_message.message_id)
        return {"client_id": client_id} if client_id else False


router = Router(name="admin")
router.message.filter(IsAdmin())

ADMIN_HELP = (
    "<b>Админ-панель</b>\n\n"
    "/stats — статистика\n"
    "/kb — какие файлы в базе знаний\n"
    "/reload — перечитать базу знаний с диска без перезапуска\n\n"
    "Чтобы ответить клиенту, сделайте reply на карточку «Нужен оператор»."
)


@router.message(OperatorReply(), F.text)
async def operator_reply(message: Message, client_id: int, db: Database, bot: Bot) -> None:
    try:
        await bot.send_message(client_id, f"👤 Оператор: {message.text}", parse_mode=None)
    except TelegramAPIError as exc:
        await message.reply(f"Не удалось доставить: {escape(str(exc))}")
        return
    await db.add_message(client_id, "assistant", message.text, source="operator")
    await message.reply("✅ Отправлено клиенту")


@router.message(Command("admin"))
async def admin_help(message: Message) -> None:
    await message.answer(ADMIN_HELP)


@router.message(Command("stats"))
async def stats(message: Message, db: Database) -> None:
    s = await db.stats()
    await message.answer(
        "<b>Статистика</b>\n\n"
        f"Пользователей: {s['users']}\n"
        f"Вопросов всего: {s['questions']}\n"
        f"Вопросов сегодня: {s['questions_today']}\n"
        f"Передано оператору: {s['handoffs']}\n"
        f"Ответов оператора: {s['operator_replies']}"
    )


@router.message(Command("kb"))
async def kb_info(message: Message, ai: SupportAI) -> None:
    files = "\n".join(f"• {escape(name)}" for name in ai.knowledge.files)
    await message.answer(f"<b>База знаний</b> ({ai.knowledge.size} символов)\n\n{files}")


@router.message(Command("reload"))
async def reload_kb(message: Message, ai: SupportAI, config: Config) -> None:
    try:
        ai.knowledge = load_knowledge(config.knowledge_dir)
    except RuntimeError as exc:
        await message.answer(f"Ошибка: {escape(str(exc))}")
        return
    log.info("База знаний перезагружена: %s файлов", len(ai.knowledge.files))
    await message.answer(f"✅ База знаний перечитана: {len(ai.knowledge.files)} файлов, {ai.knowledge.size} символов.")
