import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    operator_chat_id: int | None
    model: str
    effort: str
    knowledge_dir: str
    history_messages: int
    rate_limit_per_minute: int
    db_path: str


def _parse_ids(raw: str) -> frozenset[int]:
    return frozenset(int(part) for part in raw.replace(" ", "").split(",") if part)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан: скопируйте .env.example в .env и заполните его")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY не задан: получите ключ на platform.claude.com")

    operator_chat = os.getenv("OPERATOR_CHAT_ID", "").strip()
    return Config(
        bot_token=token,
        admin_ids=_parse_ids(os.getenv("ADMIN_IDS", "")),
        operator_chat_id=int(operator_chat) if operator_chat else None,
        model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
        effort=os.getenv("CLAUDE_EFFORT", "low"),
        knowledge_dir=os.getenv("KNOWLEDGE_DIR", "knowledge"),
        history_messages=int(os.getenv("HISTORY_MESSAGES", "20")),
        rate_limit_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "10")),
        db_path=os.getenv("DB_PATH", "aibot.db"),
    )
