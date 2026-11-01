from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .model import NativeChat


METRIC_FIELDS = {
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
}


@dataclass(frozen=True)
class JsonLine:
    raw: bytes
    value: dict[str, Any] | None
    error: str | None = None
    done: bool = False


class NDJSONFramer:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[JsonLine]:
        self._buffer.extend(chunk)
        lines: list[JsonLine] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if raw.strip():
                lines.append(self._decode(raw))
        return lines

    def finish(self) -> list[JsonLine]:
        if not self._buffer:
            return []
        raw = bytes(self._buffer)
        self._buffer.clear()
        if not raw.strip():
            return []
        return [self._decode(raw)]

    @staticmethod
    def _decode(raw: bytes) -> JsonLine:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return JsonLine(raw=raw, value=None, error=str(exc))
        if not isinstance(value, dict):
            return JsonLine(
                raw=raw,
                value=None,
                error=f"expected JSON object, got {type(value).__name__}",
            )
        return JsonLine(raw=raw, value=value)


def decode_chat_request(body: bytes) -> tuple[NativeChat, str | None]:
    chat = NativeChat()
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return chat, str(exc)

    if not isinstance(payload, dict):
        return chat, f"expected JSON object, got {type(payload).__name__}"

    model = payload.get("model")
    if isinstance(model, str):
        chat.model = model

    messages = payload.get("messages")
    if isinstance(messages, list):
        chat.messages = [item for item in messages if isinstance(item, dict)]

    tools = payload.get("tools")
    if isinstance(tools, list):
        chat.tools = [item for item in tools if isinstance(item, dict)]

    return chat, None


class NativeChatReducer:
    def __init__(self, chat: NativeChat) -> None:
        self.chat = chat

    def consume_line(self, line: JsonLine) -> None:
        if line.done:
            return
        if line.error is not None:
            self.chat.decode_errors.append(line.error)
            return
        assert line.value is not None
        self.consume_event(line.value)

    def consume_event(self, event: dict[str, Any]) -> None:
        self.chat.events.append(event)

        model = event.get("model")
        if isinstance(model, str):
            self.chat.model = model

        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                self.chat.content += content

            thinking = message.get("thinking")
            if isinstance(thinking, str):
                self.chat.thinking += thinking

            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                self.chat.tool_calls.extend(
                    item for item in tool_calls if isinstance(item, dict)
                )

        done = event.get("done")
        if isinstance(done, bool):
            self.chat.done = done

        done_reason = event.get("done_reason")
        if isinstance(done_reason, str):
            self.chat.done_reason = done_reason

        error = event.get("error")
        if isinstance(error, str):
            self.chat.decode_errors.append(error)

        for field_name in METRIC_FIELDS:
            if field_name in event:
                self.chat.metrics[field_name] = event[field_name]
