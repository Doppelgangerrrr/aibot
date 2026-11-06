from datetime import datetime

import aiosqlite
from aiogram.types import User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- Вся переписка. archived = 1 у сообщений до /reset: они остаются в статистике, но не попадают в контекст модели.
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'ai',  -- для assistant: 'ai' или 'operator'
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, archived, id);

-- Какое сообщение в чате операторов относится к какому клиенту: по reply на него ответ уходит клиенту.
CREATE TABLE IF NOT EXISTS handoffs (
    chat_id     INTEGER NOT NULL,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База не подключена: вызовите connect()")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_user(self, user: User) -> None:
        await self.conn.execute(
            """
            INSERT INTO users (id, username, full_name, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET username = excluded.username, full_name = excluded.full_name
            """,
            (user.id, user.username, user.full_name, _now()),
        )
        await self.conn.commit()

    async def get_user(self, user_id: int) -> dict | None:
        async with self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def add_message(self, user_id: int, role: str, text: str, source: str = "ai") -> None:
        await self.conn.execute(
            "INSERT INTO messages (user_id, role, text, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, role, text, source, _now()),
        )
        await self.conn.commit()

    async def history(self, user_id: int, limit: int) -> list[dict]:
        """Последние сообщения диалога в формате Messages API, от старых к новым, начиная с реплики клиента."""
        async with self.conn.execute(
            "SELECT role, text, source FROM messages WHERE user_id = ? AND archived = 0 ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = list(reversed(await cursor.fetchall()))
        while rows and rows[0]["role"] != "user":
            rows.pop(0)  # Messages API требует, чтобы диалог начинался с user
        return [
            {"role": row["role"], "content": f"[Ответ оператора] {row['text']}" if row["source"] == "operator" else row["text"]}
            for row in rows
        ]

    async def reset_history(self, user_id: int) -> None:
        await self.conn.execute("UPDATE messages SET archived = 1 WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def add_handoff(self, chat_id: int, message_id: int, user_id: int, reason: str) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO handoffs (chat_id, message_id, user_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, message_id, user_id, reason, _now()),
        )
        await self.conn.commit()

    async def handoff_user(self, chat_id: int, message_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT user_id FROM handoffs WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
        ) as cursor:
            row = await cursor.fetchone()
        return row["user_id"] if row else None

    async def stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")

        async def scalar(sql: str, *params) -> int:
            async with self.conn.execute(sql, params) as cursor:
                return (await cursor.fetchone())[0]

        return {
            "users": await scalar("SELECT COUNT(*) FROM users"),
            "questions": await scalar("SELECT COUNT(*) FROM messages WHERE role = 'user'"),
            "questions_today": await scalar(
                "SELECT COUNT(*) FROM messages WHERE role = 'user' AND created_at LIKE ?", f"{today}%"
            ),
            "operator_replies": await scalar("SELECT COUNT(*) FROM messages WHERE source = 'operator'"),
            "handoffs": await scalar("SELECT COUNT(*) FROM handoffs"),
        }
