import json
import logging
import time
from dataclasses import dataclass, field

from core import llm

from .models import AssistantCall
from .prompt import ERROR_MESSAGE, FALLBACK_MESSAGE, OFF_TOPIC_MESSAGE, build_system_prompt
from .tools import ToolContext, build_tool_schemas, execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


def _get_client():
    """The OpenAI-SDK client (OpenAI or DeepSeek); unused when LLM_PROVIDER=anthropic. See core/llm.py."""
    return llm.openai_client(timeout=30, max_retries=1)


@dataclass
class AskResult:
    answer: str
    outcome: str
    tool_rounds: int = 0
    tools_called: list = field(default_factory=list)
    empty_result: bool = False
    ambiguous: bool = False
    error_type: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0


_LIST_KEYS = ("prices", "suppliers", "products", "customers")


def _is_empty(result: dict) -> bool:
    if result.get("order_count") == 0 or result.get("returned") == 0:
        return True
    if result.get("error") == "order not found":
        return True
    return any(key in result and not result[key] for key in _LIST_KEYS)


def ask(ctx: ToolContext, message: str, history: list) -> AskResult:
    """
    Answer one question. The model only ever sees tool results, never the DB,
    and the tools themselves are scoped to ctx.user. Nothing here logs or
    stores the question, the answer or tool arguments.
    """
    started = time.monotonic()
    result = AskResult(answer=FALLBACK_MESSAGE, outcome=AssistantCall.Outcome.ROUND_LIMIT)
    tool_results = []

    try:
        chat = llm.chat_session(
            build_system_prompt(ctx), history, message, build_tool_schemas(ctx.is_admin),
            openai_client_factory=_get_client,
        )
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            reply = chat.send()
            result.prompt_tokens += reply.prompt_tokens
            result.completion_tokens += reply.completion_tokens

            if not reply.tool_calls:
                answer = reply.text
                if OFF_TOPIC_MESSAGE in answer:
                    result.answer, result.outcome = OFF_TOPIC_MESSAGE, AssistantCall.Outcome.OFF_TOPIC
                elif answer:
                    result.answer, result.outcome = answer, AssistantCall.Outcome.ANSWERED
                break
            if round_no == MAX_TOOL_ROUNDS:
                break

            result.tool_rounds += 1
            outputs = []
            for call in reply.tool_calls:
                output = execute_tool(ctx, call.name, call.arguments)
                tool_results.append(output)
                result.tools_called.append(call.name)
                outputs.append((call.id, json.dumps(output, ensure_ascii=False)))
            chat.add_tool_results(reply, outputs)
    except Exception as exc:
        logger.error("assistant request failed: %s", type(exc).__name__)
        result.answer = ERROR_MESSAGE
        result.outcome = AssistantCall.Outcome.ERROR
        result.error_type = type(exc).__name__

    result.empty_result = bool(tool_results) and all(_is_empty(r) for r in tool_results)
    result.ambiguous = any("ambiguous_products" in r for r in tool_results)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result
