import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anthropic
from anthropic import AsyncAnthropic

from .knowledge import KnowledgeBase

log = logging.getLogger(__name__)

# Сколько раз модель может вызвать инструмент за один ответ клиенту.
MAX_STEPS = 3

INSTRUCTIONS = """\
Ты — вежливый консультант в Telegram-чате компании. Отвечай клиентам на русском языке, \
коротко и по делу: обычно 1–4 предложения, список — только если перечисляешь несколько пунктов.

Отвечай только на основе базы знаний ниже. Если нужного факта (цены, срока, адреса, условия) \
в базе нет, не придумывай его: скажи, что уточнишь у сотрудника, и вызови escalate_to_operator. \
Так же передавай диалог оператору, если клиент просит живого человека, жалуется, \
хочет вернуть деньги или ситуация требует решения сотрудника.

На вопросы, не связанные с компанией и её услугами, вежливо ответь, что помогаешь только по ним.

Пиши обычным текстом без Markdown: Telegram покажет звёздочки и решётки как есть.\
"""

ESCALATE_TOOL = {
    "name": "escalate_to_operator",
    "description": (
        "Передать диалог живому оператору. Оператор получит суть вопроса и ответит клиенту в этом же чате. "
        "Вызывай, когда ответа нет в базе знаний, клиент просит человека, жалуется или нужен сотрудник."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Почему нужен оператор, одной фразой"},
            "summary": {"type": "string", "description": "Суть вопроса клиента в 1–3 предложениях для оператора"},
        },
        "required": ["reason", "summary"],
        "additionalProperties": False,
    },
}

ERROR_TEXT = "Извините, сейчас не получается ответить. Попробуйте через минуту или нажмите «Позвать оператора»."
REFUSAL_TEXT = "С этим вопросом я помочь не могу. Если нужен сотрудник, нажмите «Позвать оператора»."

EscalateFn = Callable[[str, str], Awaitable[None]]


@dataclass
class Reply:
    text: str
    escalated: bool = False


class SupportAI:
    def __init__(self, model: str, effort: str, knowledge: KnowledgeBase):
        self.client = AsyncAnthropic()  # ключ берётся из ANTHROPIC_API_KEY
        self.model = model
        self.effort = effort
        self.knowledge = knowledge

    def _system(self) -> list[dict]:
        # База знаний — неизменный префикс запроса, поэтому кэшируется: повторные вопросы
        # оплачиваются по цене чтения из кэша, а не полной стоимости входных токенов.
        return [
            {"type": "text", "text": INSTRUCTIONS},
            {
                "type": "text",
                "text": f"<knowledge_base>\n{self.knowledge.text}\n</knowledge_base>",
                "cache_control": {"type": "ephemeral"},
            },
        ]

    async def reply(self, history: list[dict], on_escalate: EscalateFn) -> Reply:
        """history — текстовая переписка вида [{"role": "user"|"assistant", "content": str}, ...],
        последним идёт новое сообщение клиента."""
        messages = list(history)
        escalated = False

        try:
            for _ in range(MAX_STEPS):
                response = await self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=self._system(),
                    tools=[ESCALATE_TOOL],
                    messages=messages,
                    output_config={"effort": self.effort},
                    # при отказе модели запрос автоматически переигрывается на запасной модели
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
                usage = response.usage
                log.info(
                    "Claude: stop=%s in=%s cache_read=%s cache_write=%s out=%s",
                    response.stop_reason, usage.input_tokens, usage.cache_read_input_tokens,
                    usage.cache_creation_input_tokens, usage.output_tokens,
                )

                if response.stop_reason == "refusal":
                    return Reply(REFUSAL_TEXT, escalated)
                if response.stop_reason != "tool_use":
                    break

                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    if block.name == ESCALATE_TOOL["name"] and not escalated:
                        await on_escalate(block.input["reason"], block.input["summary"])
                        escalated = True
                        result = "Оператор уведомлён и ответит клиенту в этом чате."
                    elif block.name == ESCALATE_TOOL["name"]:
                        result = "Оператор уже уведомлён."
                    else:
                        result = f"Неизвестный инструмент {block.name}"
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                messages.append({"role": "user", "content": results})

        except anthropic.RateLimitError:
            log.warning("Claude: превышен лимит запросов")
            return Reply(ERROR_TEXT, escalated)
        except anthropic.APIStatusError as exc:
            log.error("Claude: ошибка API %s: %s", exc.status_code, exc.message)
            return Reply(ERROR_TEXT, escalated)
        except anthropic.APIConnectionError:
            log.error("Claude: нет соединения с API")
            return Reply(ERROR_TEXT, escalated)

        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            text = "Передал ваш вопрос оператору, он скоро ответит здесь." if escalated else ERROR_TEXT
        return Reply(text, escalated)
