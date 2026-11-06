import asyncio
import time
from collections import defaultdict, deque
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.utils.chat_action import ChatActionSender

from ..config import Config
from ..db import Database
from ..handoff import escalate
from ..llm import SupportAI

router = Router(name="user")
# Клиентская часть работает только в личке, чтобы бот не отвечал на сообщения в чате операторов.
router.message.filter(F.chat.type == "private")

BTN_OPERATOR = "🙋 Позвать оператора"
BTN_RESET = "🔄 Новый диалог"
TELEGRAM_LIMIT = 4000

MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_OPERATOR), KeyboardButton(text=BTN_RESET)]],
    resize_keyboard=True,
    input_field_placeholder="Задайте вопрос…",
)


class RateLimiter:
    """Не больше N сообщений в минуту от одного пользователя — защита бюджета на API."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        hits = self._hits[user_id]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True


# Сообщения одного пользователя обрабатываются по очереди, чтобы ответы не перепутались.
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    await db.upsert_user(message.from_user)
    await message.answer(
        f"Здравствуйте, {escape(message.from_user.first_name)}! 👋\n\n"
        "Я ИИ-консультант мастерской «Ремонт за день». Спросите про цены, сроки, гарантию или адрес — "
        "отвечу сразу. Если вопрос сложный, подключу живого оператора.",
        reply_markup=MENU,
    )


@router.message(Command("reset"))
@router.message(F.text == BTN_RESET)
async def cmd_reset(message: Message, db: Database) -> None:
    await db.reset_history(message.from_user.id)
    await message.answer("Начнём заново. Чем могу помочь?", reply_markup=MENU)


@router.message(Command("operator"))
@router.message(F.text == BTN_OPERATOR)
async def cmd_operator(message: Message, db: Database, config: Config, bot: Bot) -> None:
    await db.upsert_user(message.from_user)
    history = await db.history(message.from_user.id, 4)
    last_question = next((m["content"] for m in reversed(history) if m["role"] == "user"), "—")
    delivered = await escalate(
        bot, db, config, message.from_user.id,
        reason="Клиент нажал «Позвать оператора»",
        summary=f"Последний вопрос клиента: {last_question}",
    )
    if delivered:
        await message.answer("Позвал оператора — он ответит здесь же. А пока можете продолжать спрашивать меня.")
    else:
        await message.answer("Не получилось связаться с оператором. Позвоните нам: +7 (900) 000-00-00.")


@router.message(F.text)
async def chat(message: Message, db: Database, config: Config, bot: Bot, ai: SupportAI, limiter: RateLimiter) -> None:
    user_id = message.from_user.id
    if not limiter.allow(user_id):
        await message.answer("Слишком много сообщений подряд. Подождите минуту, пожалуйста.")
        return

    async with _locks[user_id]:
        await db.upsert_user(message.from_user)
        await db.add_message(user_id, "user", message.text[:4000])
        history = await db.history(user_id, config.history_messages)

        async def on_escalate(reason: str, summary: str) -> None:
            await escalate(bot, db, config, user_id, reason, summary)

        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            reply = await ai.reply(history, on_escalate)

        await db.add_message(user_id, "assistant", reply.text)
        # Ответ модели — обычный текст, HTML-разметку отключаем, чтобы символы < и & не ломали сообщение.
        for chunk in split_text(reply.text):
            await message.answer(chunk, parse_mode=None)


@router.message()
async def unsupported(message: Message) -> None:
    await message.answer("Я понимаю только текстовые сообщения. Напишите вопрос словами 🙂")
