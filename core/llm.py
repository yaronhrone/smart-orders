"""
Which AI model the system talks to, chosen in .env so models can be compared
without touching code:

    LLM_PROVIDER=openai     (default)  OPENAI_API_KEY     model gpt-4o-mini
    LLM_PROVIDER=anthropic             ANTHROPIC_API_KEY  model claude-sonnet-5-5
    LLM_PROVIDER=deepseek              DEEPSEEK_API_KEY   model deepseek-flash   (LLM_MODEL=deepseek-v4-pro for the bigger one)
    LLM_MODEL=...           optional, overrides the provider's default model

OpenAI and DeepSeek share the OpenAI SDK (DeepSeek's API is built to be called
with it, on its own base URL); Anthropic uses its own SDK. Callers never see
the difference: complete_json() for the parsers, chat_session() for the
assistant's tool-calling loop. Restart the web and worker containers after a change.
"""
import datetime
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PARSE_KINDS = ("order_parse", "modification_parse", "price_parse")

# USD per 1M tokens (input, output). ESTIMATES for the usage page, not an invoice: check the
# provider's price list and correct here. A model missing from the table is recorded with cost 0.
PRICES_PER_MILLION = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-sonnet-5-5": (2.00, 10.00),
    "deepseek-flash": (0.30, 1.20),  # peak-hour price; off-peak is half
    "deepseek-v4-pro": (1.32, 3.96),
}


def estimate_cost(model_name: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MILLION.get(model_name, (0, 0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def record_usage(kind: str, started: float, input_tokens: int, output_tokens: int, ok: bool = True) -> None:
    """One row per AI call (kind, model, tokens, estimated cost, seconds) for the admin usage page."""
    try:
        from core.models import AIUsage

        model_name = model()
        AIUsage.objects.create(
            kind=kind, provider=provider(), model=model_name,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=estimate_cost(model_name, input_tokens, output_tokens),
            duration_ms=int((time.monotonic() - started) * 1000), ok=ok,
        )
    except Exception:  # recording must never break the call it measures
        logger.warning("could not record AI usage", exc_info=True)

OPENAI, ANTHROPIC, DEEPSEEK = "openai", "anthropic", "deepseek"

DEFAULT_MODELS = {
    OPENAI: "gpt-4o-mini",
    ANTHROPIC: "claude-sonnet-5-5",
    DEEPSEEK: "deepseek-flash",
}
API_KEY_ENV = {OPENAI: "OPENAI_API_KEY", ANTHROPIC: "ANTHROPIC_API_KEY", DEEPSEEK: "DEEPSEEK_API_KEY"}
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# On a policy decline the API reruns the request on a fallback model inside the same call.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_clients = {}

# Tokens used by the latest complete_json() call, for core's compare_models command.
last_usage = {"input": 0, "output": 0}


def note_parse(kind: str, used_ai: bool, **detail) -> None:
    """
    Say where a message was understood: by the dictionary (exact / alias match, free and
    instant) or by the AI model (slow, costs money). One log line per parse, and a per-day
    counter that /health/ shows, to see how often the AI is really needed.
    """
    source = "ai" if used_ai else "dictionary"
    logger.info("PARSE %s via %s %s", kind, source, detail or "")
    try:
        from django.core.cache import cache
        key = f"parse_count:{datetime.date.today().isoformat()}:{kind}:{source}"
        cache.add(key, 0, timeout=172800)
        cache.incr(key)
    except Exception:  # counting must never break a parse
        pass


def parse_counts_today() -> dict:
    from django.core.cache import cache

    today = datetime.date.today().isoformat()
    counts = {}
    for kind in PARSE_KINDS:
        row = {source: cache.get(f"parse_count:{today}:{kind}:{source}") or 0 for source in ("dictionary", "ai")}
        if any(row.values()):
            counts[kind] = row
    return counts


def provider() -> str:
    name = os.environ.get("LLM_PROVIDER", OPENAI).strip().lower()
    if name not in DEFAULT_MODELS:
        raise ValueError(f"LLM_PROVIDER לא מוכר: {name!r} (openai / anthropic / deepseek)")
    return name


def model() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider()]


def api_key_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV[provider()]))


def openai_client(timeout=None, max_retries=None):
    """The OpenAI SDK client for the current provider: OpenAI itself, or DeepSeek on its base URL."""
    from openai import OpenAI

    name = provider()
    if name == ANTHROPIC:
        raise RuntimeError("openai_client() called while LLM_PROVIDER=anthropic")
    key = (name, timeout, max_retries)
    if key not in _clients:
        kwargs = {"api_key": os.environ[API_KEY_ENV[name]]}
        if name == DEEPSEEK:
            kwargs["base_url"] = DEEPSEEK_BASE_URL
        if timeout is not None:
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        _clients[key] = OpenAI(**kwargs)
    return _clients[key]


def anthropic_client(timeout=None, max_retries=None):
    import anthropic

    key = (ANTHROPIC, timeout, max_retries)
    if key not in _clients:
        kwargs = {"api_key": os.environ[API_KEY_ENV[ANTHROPIC]]}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        _clients[key] = anthropic.Anthropic(**kwargs)
    return _clients[key]


def _anthropic_create(client, **kwargs):
    return client.beta.messages.create(
        model=model(), betas=[ANTHROPIC_FALLBACK_BETA], fallbacks="default", **kwargs,
    )


def _anthropic_text(response) -> str:
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined the request")
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _parse_json(text: str):
    """The JSON object in a model's reply, tolerating a ```json fence around it."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0) if match else text)


def complete_json(prompt: str, openai_client_factory=None, kind: str = "other") -> dict:
    """
    Send one prompt that asks for a JSON object and return it parsed. Errors
    (network, a reply that isn't JSON) are raised for the caller to handle.
    `openai_client_factory`: the caller's own OpenAI-SDK client getter, so its
    tests can replace it; used for OpenAI and DeepSeek.
    """
    started = time.monotonic()
    recorded = False
    try:
        if provider() == ANTHROPIC:
            response = _anthropic_create(
                anthropic_client(),
                max_tokens=16000,
                # Extraction, not reasoning: low effort keeps it fast and cheap.
                output_config={"effort": "low"},
                system="Reply with a single JSON object only, no other text.",
                messages=[{"role": "user", "content": prompt}],
            )
            last_usage.update(input=response.usage.input_tokens or 0, output=response.usage.output_tokens or 0)
            record_usage(kind, started, last_usage["input"], last_usage["output"])
            recorded = True
            return _parse_json(_anthropic_text(response))

        client = (openai_client_factory or openai_client)()
        response = client.chat.completions.create(
            model=model(),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        usage = getattr(response, "usage", None)
        last_usage.update(
            input=int(getattr(usage, "prompt_tokens", 0) or 0), output=int(getattr(usage, "completion_tokens", 0) or 0),
        )
        record_usage(kind, started, last_usage["input"], last_usage["output"])
        recorded = True
        return json.loads(response.choices[0].message.content)
    except Exception:
        if not recorded:
            record_usage(kind, started, 0, 0, ok=False)
        raise


# ─── tool calling (the assistant) ───

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, as the tools expect it


@dataclass
class Reply:
    text: str
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class OpenAIChat:
    """A tool-calling conversation over the OpenAI SDK (OpenAI or DeepSeek)."""

    def __init__(self, client, system: str, history: list, message: str, tools: list):
        self.client, self.tools = client, tools
        self.messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]

    def send(self) -> Reply:
        started = time.monotonic()
        try:
            response = self.client.chat.completions.create(
                model=model(), temperature=0, messages=self.messages, tools=self.tools,
            )
        except Exception:
            record_usage("assistant", started, 0, 0, ok=False)
            raise
        usage = getattr(response, "usage", None)
        reply = response.choices[0].message
        self._last = reply
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        record_usage("assistant", started, prompt_tokens, completion_tokens)
        return Reply(
            text=(reply.content or "").strip(),
            tool_calls=[ToolCall(c.id, c.function.name, c.function.arguments) for c in (reply.tool_calls or [])],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def add_tool_results(self, reply: Reply, results: list) -> None:
        """`results`: [(tool_call_id, output_text)] for every call in `reply`."""
        self.messages.append({
            "role": "assistant",
            "content": self._last.content,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in reply.tool_calls
            ],
        })
        for call_id, output in results:
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": output})


class AnthropicChat:
    """A tool-calling conversation over the Anthropic SDK."""

    def __init__(self, client, system: str, history: list, message: str, tools: list):
        self.client, self.system = client, system
        self.tools = [
            {"name": t["function"]["name"], "description": t["function"].get("description", ""),
             "input_schema": t["function"].get("parameters") or {"type": "object", "properties": {}}}
            for t in tools
        ]
        # The conversation has to open with the user.
        while history and history[0].get("role") != "user":
            history = history[1:]
        self.messages = [*history, {"role": "user", "content": message}]

    def send(self) -> Reply:
        started = time.monotonic()
        try:
            response = _anthropic_create(
                self.client, max_tokens=16000, output_config={"effort": "medium"},
                system=self.system, messages=self.messages, tools=self.tools,
            )
        except Exception:
            record_usage("assistant", started, 0, 0, ok=False)
            raise
        record_usage("assistant", started, int(response.usage.input_tokens or 0), int(response.usage.output_tokens or 0))
        if response.stop_reason == "refusal":
            raise RuntimeError("the model declined the request")
        # The whole content goes back unchanged on the next turn (thinking blocks included).
        self._last = response.content
        return Reply(
            text="".join(b.text for b in response.content if b.type == "text").strip(),
            tool_calls=[
                ToolCall(b.id, b.name, json.dumps(b.input, ensure_ascii=False))
                for b in response.content if b.type == "tool_use"
            ],
            prompt_tokens=int(response.usage.input_tokens or 0),
            completion_tokens=int(response.usage.output_tokens or 0),
        )

    def add_tool_results(self, reply: Reply, results: list) -> None:
        self.messages.append({"role": "assistant", "content": self._last})
        # All results of one turn in a single user message.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": output} for call_id, output in results
        ]})


def chat_session(system: str, history: list, message: str, tools: list, openai_client_factory=None):
    """
    A conversation for the current provider. `tools` are in the OpenAI function
    format (converted for Anthropic). `history` is [{"role", "content": text}].
    """
    if provider() == ANTHROPIC:
        return AnthropicChat(anthropic_client(timeout=60, max_retries=1), system, history, message, tools)
    factory = openai_client_factory or (lambda: openai_client(timeout=30, max_retries=1))
    return OpenAIChat(factory(), system, history, message, tools)
