from __future__ import annotations

import json
from typing import Any

from .model import CompatibleChat
from .native import JsonLine


class SSEFramer:
    """Frame JSON Server-Sent Events without assuming HTTP chunk boundaries."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._data_lines: list[bytes] = []

    def feed(self, chunk: bytes) -> list[JsonLine]:
        self._buffer.extend(chunk)
        events: list[JsonLine] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                return events
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            events.extend(self._consume_line(raw.removesuffix(b"\r")))

    def finish(self) -> list[JsonLine]:
        events: list[JsonLine] = []
        if self._buffer:
            raw = bytes(self._buffer)
            self._buffer.clear()
            events.extend(self._consume_line(raw.removesuffix(b"\r")))
        events.extend(self._flush_event())
        return events

    def _consume_line(self, line: bytes) -> list[JsonLine]:
        if not line:
            return self._flush_event()
        if not line.startswith(b"data:"):
            return []
        data = line[5:]
        if data.startswith(b" "):
            data = data[1:]
        self._data_lines.append(data)
        return []

    def _flush_event(self) -> list[JsonLine]:
        if not self._data_lines:
            return []
        raw = b"\n".join(self._data_lines)
        self._data_lines.clear()
        if raw.strip() == b"[DONE]":
            return [JsonLine(raw=raw, value=None, done=True)]
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [JsonLine(raw=raw, value=None, error=str(exc))]
        if not isinstance(value, dict):
            return [
                JsonLine(
                    raw=raw,
                    value=None,
                    error=f"expected JSON object, got {type(value).__name__}",
                )
            ]
        return [JsonLine(raw=raw, value=value)]


class CompatibleResponseFramer:
    """Select incremental SSE or one complete JSON value from response bytes."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._json = False
        self._sse: SSEFramer | None = None

    def feed(self, chunk: bytes) -> list[JsonLine]:
        if self._sse is not None:
            return self._sse.feed(chunk)
        self._buffer.extend(chunk)
        if self._json:
            return []
        prefix = bytes(self._buffer).lstrip()
        if not prefix:
            return []
        if prefix[0] in b'{[' or prefix.startswith(b'\xef'):
            self._json = True
            return []
        self._sse = SSEFramer()
        buffered = bytes(self._buffer)
        self._buffer.clear()
        return self._sse.feed(buffered)

    def finish(self) -> list[JsonLine]:
        if self._sse is not None:
            return self._sse.finish()
        raw = bytes(self._buffer)
        self._buffer.clear()
        if not raw.strip():
            return []
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [JsonLine(raw=raw, value=None, error=str(exc))]
        if not isinstance(value, dict):
            return [JsonLine(raw=raw, value=None, error=f"expected JSON object, got {type(value).__name__}")]
        return [JsonLine(raw=raw, value=value)]


def _decode_request(body: bytes, *, endpoint: str, key: str) -> tuple[CompatibleChat, str | None]:
    chat = CompatibleChat(endpoint=endpoint)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return chat, str(exc)
    if not isinstance(payload, dict):
        return chat, f"expected JSON object, got {type(payload).__name__}"

    model = payload.get("model")
    if isinstance(model, str):
        chat.model = model
    items = payload.get(key)
    if isinstance(items, list):
        chat.messages = [item for item in items if isinstance(item, dict)]
    elif key == "input" and isinstance(items, str):
        chat.input_text = items
    tools = payload.get("tools")
    if isinstance(tools, list):
        chat.tools = [item for item in tools if isinstance(item, dict)]
    return chat, None


def decode_chat_completions_request(body: bytes) -> tuple[CompatibleChat, str | None]:
    return _decode_request(
        body,
        endpoint="/v1/chat/completions",
        key="messages",
    )


def decode_responses_request(body: bytes) -> tuple[CompatibleChat, str | None]:
    return _decode_request(body, endpoint="/v1/responses", key="input")


class _CompatibleReducer:
    def __init__(self, chat: CompatibleChat) -> None:
        self.chat = chat

    def consume_line(self, line: JsonLine) -> None:
        if line.done:
            self.chat.done = True
            if self.chat.done_reason is None:
                self.chat.done_reason = "stream-end"
            self._finish_tool_calls()
            return
        if line.error is not None:
            self.chat.decode_errors.append(line.error)
            return
        assert line.value is not None
        self.consume_event(line.value)

    def consume_event(self, event: dict[str, Any]) -> None:
        raise NotImplementedError

    def _set_model_and_usage(self, value: dict[str, Any]) -> None:
        model = value.get("model")
        if isinstance(model, str):
            self.chat.model = model
        usage = value.get("usage")
        if isinstance(usage, dict):
            self.chat.metrics["usage"] = usage

    def _merge_tool_call(
        self,
        *,
        key: str | int,
        call_id: str | None = None,
        name: str | None = None,
        arguments: str | None = None,
        replace_name: bool = False,
        replace_arguments: bool = False,
    ) -> None:
        call = next(
            (item for item in self.chat.tool_calls if item.get("_key") == key),
            None,
        )
        if call is None:
            call = {"_key": key, "function": {}}
            self.chat.tool_calls.append(call)
        if call_id is not None:
            call["id"] = call_id
        function = call["function"]
        assert isinstance(function, dict)
        if name:
            if replace_name:
                function["name"] = name
            else:
                function["name"] = f"{function.get('name', '')}{name}"
        if arguments:
            if replace_arguments:
                function["arguments"] = arguments
            else:
                function["arguments"] = f"{function.get('arguments', '')}{arguments}"

    def _finish_tool_calls(self) -> None:
        for call in self.chat.tool_calls:
            call.pop("_key", None)

    def _record_response_error(self, response: dict[str, Any]) -> None:
        error = response.get("error")
        if isinstance(error, str):
            self.chat.decode_errors.append(error)
        elif isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                self.chat.decode_errors.append(message)


class ChatCompletionsReducer(_CompatibleReducer):
    def consume_event(self, event: dict[str, Any]) -> None:
        self.chat.events.append(event)
        self._set_model_and_usage(event)
        self._record_response_error(event)
        choices = event.get("choices")
        if not isinstance(choices, list):
            return
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = choice.get("message")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    self.chat.content += content
                reasoning = delta.get("reasoning")
                if not isinstance(reasoning, str):
                    reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str):
                    self.chat.thinking += reasoning
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for position, item in enumerate(tool_calls):
                        if not isinstance(item, dict):
                            continue
                        key = item.get("index")
                        if not isinstance(key, int):
                            key = position
                        call_id = item.get("id")
                        function = item.get("function")
                        name = None
                        arguments = None
                        if isinstance(function, dict):
                            raw_name = function.get("name")
                            raw_arguments = function.get("arguments")
                            name = raw_name if isinstance(raw_name, str) else None
                            arguments = (
                                raw_arguments if isinstance(raw_arguments, str) else None
                            )
                        self._merge_tool_call(
                            key=key,
                            call_id=call_id if isinstance(call_id, str) else None,
                            name=name,
                            arguments=arguments,
                        )
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str):
                self.chat.done = True
                self.chat.done_reason = finish_reason
                self._finish_tool_calls()


class ResponsesReducer(_CompatibleReducer):
    def consume_event(self, event: dict[str, Any]) -> None:
        self.chat.events.append(event)
        if "type" not in event:
            self._consume_response(event)
            return
        response = event.get("response")
        if isinstance(response, dict):
            self._set_model_and_usage(response)

        event_type = event.get("type")
        if not isinstance(event_type, str):
            return
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                self.chat.content += delta
        elif event_type == "response.reasoning_summary_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                self.chat.thinking += delta
        elif event_type == "response.output_text.done" and not self.chat.content:
            text = event.get("text")
            if isinstance(text, str):
                self.chat.content = text
        elif event_type == "response.reasoning_summary_text.done" and not self.chat.thinking:
            text = event.get("text")
            if isinstance(text, str):
                self.chat.thinking = text
        elif event_type == "response.function_call_arguments.delta":
            key = event.get("item_id")
            if not isinstance(key, str):
                key = event.get("output_index")
            if isinstance(key, (str, int)):
                delta = event.get("delta")
                self._merge_tool_call(
                    key=key,
                    call_id=key if isinstance(key, str) else None,
                    arguments=delta if isinstance(delta, str) else None,
                )
        elif event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            if isinstance(item, dict):
                self._consume_output_item(
                    item,
                    complete=event_type == "response.output_item.done",
                )
        elif event_type in {"response.completed", "response.incomplete", "response.failed"}:
            self.chat.done = True
            if isinstance(response, dict):
                status = response.get("status")
                if isinstance(status, str):
                    self.chat.done_reason = status
            if self.chat.done_reason is None:
                self.chat.done_reason = event_type.removeprefix("response.")
            if event_type == "response.failed" and isinstance(response, dict):
                self._record_response_error(response)
            self._finish_tool_calls()
        elif event_type == "error":
            error = event.get("error")
            if isinstance(error, str):
                self.chat.decode_errors.append(error)
            elif isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    self.chat.decode_errors.append(message)

    def _consume_output_item(self, item: dict[str, Any], *, complete: bool) -> None:
        if item.get("type") != "function_call":
            return
        key = item.get("id")
        if not isinstance(key, str):
            key = item.get("call_id")
        if not isinstance(key, str):
            return
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        self._merge_tool_call(
            key=key,
            call_id=call_id if isinstance(call_id, str) else key,
            name=name if isinstance(name, str) else None,
            arguments=arguments if isinstance(arguments, str) else None,
            replace_name=complete,
            replace_arguments=complete,
        )

    def _consume_response(self, response: dict[str, Any]) -> None:
        self._set_model_and_usage(response)
        self._record_response_error(response)
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                self._consume_output_item(item, complete=True)
                kind = item.get("type")
                if kind == "message":
                    parts = item.get("content")
                elif kind == "reasoning":
                    parts = item.get("summary")
                else:
                    continue
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                        continue
                    if kind == "message" and part.get("type") == "output_text":
                        self.chat.content += part["text"]
                    elif kind == "reasoning" and part.get("type") == "summary_text":
                        self.chat.thinking += part["text"]
        status = response.get("status")
        if isinstance(status, str):
            self.chat.done_reason = status
            self.chat.done = status in {"completed", "incomplete", "failed", "cancelled"}
        self._finish_tool_calls()
