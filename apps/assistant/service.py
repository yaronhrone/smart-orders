import json
import logging
import os
import time
from dataclasses import dataclass, field

from openai import OpenAI

from .models import AssistantCall
from .prompt import ERROR_MESSAGE, FALLBACK_MESSAGE, OFF_TOPIC_MESSAGE, build_system_prompt
from .tools import ToolContext, build_tool_schemas, execute_tool

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
MAX_TOOL_ROUNDS = 5

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=30, max_retries=1)
    return _client


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
    messages = [
        {"role": "system", "content": build_system_prompt(ctx)},
        *history,
        {"role": "user", "content": message},
    ]
    schemas = build_tool_schemas(ctx.is_admin)

    try:
        client = _get_client()
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            response = client.chat.completions.create(
                model=MODEL, temperature=0, messages=messages, tools=schemas,
            )
            usage = getattr(response, "usage", None)
            result.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            result.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            reply = response.choices[0].message

            if not reply.tool_calls:
                answer = (reply.content or "").strip()
                if OFF_TOPIC_MESSAGE in answer:
                    result.answer, result.outcome = OFF_TOPIC_MESSAGE, AssistantCall.Outcome.OFF_TOPIC
                elif answer:
                    result.answer, result.outcome = answer, AssistantCall.Outcome.ANSWERED
                break
            if round_no == MAX_TOOL_ROUNDS:
                break

            result.tool_rounds += 1
            messages.append({
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {
                        "id": call.id, "type": "function",
                        "function": {"name": call.function.name, "arguments": call.function.arguments},
                    }
                    for call in reply.tool_calls
                ],
            })
            for call in reply.tool_calls:
                output = execute_tool(ctx, call.function.name, call.function.arguments)
                tool_results.append(output)
                result.tools_called.append(call.function.name)
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": json.dumps(output, ensure_ascii=False),
                })
    except Exception as exc:
        logger.error("assistant request failed: %s", type(exc).__name__)
        result.answer = ERROR_MESSAGE
        result.outcome = AssistantCall.Outcome.ERROR
        result.error_type = type(exc).__name__

    result.empty_result = bool(tool_results) and all(_is_empty(r) for r in tool_results)
    result.ambiguous = any("ambiguous_products" in r for r in tool_results)
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result
