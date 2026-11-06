import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import load_config
from .db import Database
from .handlers import build_router
from .handlers.user import RateLimiter
from .knowledge import load_knowledge
from .llm import SupportAI

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="operator", description="Позвать оператора"),
    BotCommand(command="reset", description="Начать диалог заново"),
]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config()
    knowledge = load_knowledge(config.knowledge_dir)
    logging.info("База знаний: %s файлов, %s символов", len(knowledge.files), knowledge.size)

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(
        db=db,
        config=config,
        ai=SupportAI(config.model, config.effort, knowledge),
        limiter=RateLimiter(config.rate_limit_per_minute),
    )
    dp.include_router(build_router())

    await bot.set_my_commands(COMMANDS)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
