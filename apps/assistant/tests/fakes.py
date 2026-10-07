import json
from types import SimpleNamespace
from unittest.mock import MagicMock


def reply(content=None, tool_calls=None, prompt=10, completion=5):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def tool_call(call_id, name, args):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def fake_client(*responses):
    client = MagicMock()
    client.chat.completions.create.side_effect = list(responses)
    return client
