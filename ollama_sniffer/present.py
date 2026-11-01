from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .model import Transaction


LIVE_REQUEST_PREVIEW_BYTES = 1_000
LIVE_RESPONSE_HEAD_CHARS = 600
LIVE_RESPONSE_TAIL_CHARS = 600
COMPLETED_SUMMARY_ITEM_CHARS = 180


@dataclass(frozen=True)
class TransactionRow:
    status: str
    method: str
    path: str
    model: str
    prompt: str
    reply: str
    tools: str

    def cells(self) -> tuple[str, ...]:
        return (
            self.status,
            self.method,
            self.path,
            self.model,
            self.prompt,
            self.reply,
            self.tools,
        )


@dataclass(frozen=True)
class SummaryItem:
    """One visible entry in a completed semantic summary.

    A readable item owns both its compact semantic label and the exact
    captured value that opens in the primary reader.
    """

    content: str
    id: str | None = None
    detail_content: str | None = None
    json_content: bool = False
    heading: bool = False
    original_content: str | None = None
    size_label: str | None = None


@dataclass(frozen=True)
class DetailSection:
    id: str
    title: str
    content: str
    collapsed: bool
    json_content: bool = False
    summary_items: tuple[SummaryItem, ...] = ()
    original_content: str | None = None
    size_label: str | None = None


def compact(value: Any, limit: int = 60) -> str:
    if isinstance(value, str):
        return _compact_text(value, limit)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _compact_text(text, limit)


def _compact_text(value: str, limit: int) -> str:
    """Collapse enough whitespace to fill a compact cell, not the whole stream."""

    if len(value) <= limit:
        return " ".join(value.split())

    budget = max(limit - 3, 1)
    text = ""
    for match in re.finditer(r"\S+", value):
        word = match.group()
        separator = " " if text else ""
        available = budget - len(text) - len(separator)
        if available <= 0:
            return f"{text}..."
        if len(word) > available:
            return f"{text}{separator}{word[:available]}..."
        text = f"{text}{separator}{word}"
    return text


def _message_content(message: dict[str, Any]) -> str:
    return _message_preview(message.get("content", ""), limit=60)


def _tool_name(tool: dict[str, Any]) -> str | None:
    function = tool.get("function")
    name = function.get("name") if isinstance(function, dict) else tool.get("name")
    return name if isinstance(name, str) else None


def _tool_call_name(tool_call: dict[str, Any]) -> str | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return name if isinstance(name, str) else None


def transaction_row(transaction: Transaction) -> TransactionRow:
    if transaction.error:
        status = "ERROR"
    elif transaction.completed_at is None:
        status = "LIVE"
    elif transaction.response_status is None:
        status = "DONE"
    else:
        status = str(transaction.response_status)

    chat = transaction.native_chat or transaction.compatible_chat
    if chat is None:
        return TransactionRow(
            status=status,
            method=transaction.method,
            path=transaction.path_qs,
            model="",
            prompt=compact(transaction.request_body.decode(errors="replace")),
            reply=compact(transaction.response_body.decode(errors="replace")),
            tools="",
        )

    prompt = ""
    for message in reversed(chat.messages):
        if message.get("role") == "user":
            prompt = _message_content(message)
            break
    if not prompt and getattr(chat, "input_text", None) is not None:
        prompt = _compact_text(chat.input_text, 60)
    if not prompt and chat.messages:
        prompt = _message_content(chat.messages[-1])

    tool_names = [name for tool in chat.tools if (name := _tool_name(tool))]
    tool_call_names = [
        name for tool_call in chat.tool_calls if (name := _tool_call_name(tool_call))
    ]
    reply = chat.content or chat.thinking
    if tool_call_names:
        call_summary = f"call: {', '.join(tool_call_names)}"
        reply = f"{compact(reply, 35)} | {call_summary}" if reply else call_summary

    tools = str(len(tool_names))
    if tool_names:
        tools = f"{len(tool_names)}: {', '.join(tool_names)}"

    return TransactionRow(
        status=status,
        method=transaction.method,
        path=transaction.path_qs,
        model=chat.model or "",
        prompt=compact(prompt),
        reply=compact(reply),
        tools=compact(tools, 35),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _bytes_preview(body: bytes, *, label: str) -> str:
    if len(body) <= LIVE_REQUEST_PREVIEW_BYTES:
        return body.decode(errors="replace") or "(empty)"
    preview = body[:LIVE_REQUEST_PREVIEW_BYTES].decode(errors="replace")
    return f"{preview}\n\n… full {label} will be available after completion."


def _streaming_text_preview(value: str, *, label: str) -> str:
    limit = LIVE_RESPONSE_HEAD_CHARS + LIVE_RESPONSE_TAIL_CHARS
    if len(value) <= limit:
        return value or "(empty)"
    omitted = len(value) - limit
    return (
        f"{value[:LIVE_RESPONSE_HEAD_CHARS]}\n\n"
        f"… {omitted} chars omitted; full {label} will be available after completion.\n\n"
        f"{value[-LIVE_RESPONSE_TAIL_CHARS:]}"
    )


def _completed_text_summary(value: str) -> str:
    return _compact_text(value, COMPLETED_SUMMARY_ITEM_CHARS) or "(empty)"


def _body_section(
    section_id: str, title: str, body: bytes, *,
    collapsed: bool = True, format_json: bool = True,
) -> DetailSection:
    original = body.decode(errors="replace")
    if format_json:
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            pass
        else:
            return DetailSection(
                id=section_id, title=title, content=_json(value),
                collapsed=collapsed, json_content=True, original_content=original,
            )
    return DetailSection(
        id=section_id, title=title, content=original or "(empty)", collapsed=collapsed,
    )


def _value_preview(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _compact_text(text, COMPLETED_SUMMARY_ITEM_CHARS)


def _is_text_part(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and part.get("type") in ("input_text", "output_text", "text")
        and isinstance(part.get("text"), str)
    )


def _value_size(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return f"{len(value):,} ch"
    if isinstance(value, list):
        if value and all(_is_text_part(part) for part in value):
            return f"{sum(len(part['text']) for part in value):,} ch"
        return f"{len(value):,} parts"
    return f"{len(_json(value)):,} JSON ch"


def _message_preview(value: Any, *, limit: int = COMPLETED_SUMMARY_ITEM_CHARS) -> str:
    if not isinstance(value, list):
        return _compact_text(_value_preview(value), limit)
    parts = []
    for part in value:
        if _is_text_part(part):
            parts.append(part["text"])
        else:
            parts.append(_value_preview(part))
    return _compact_text(" ".join(parts), limit)


def _message_summary_item(index: int, message: dict[str, Any]) -> SummaryItem:
    item_type = message.get("type")
    size_label = None
    if item_type == "function_call":
        name = message.get("name")
        label = name if isinstance(name, str) else "unnamed"
        content = f"{index}. Call · {label}"
        size_label = _tool_call_size(message)
        preview = _tool_call_preview(message)
        if preview:
            content = f"{content} — {preview}"
    elif item_type == "reasoning":
        summary = message.get("summary")
        summary_count = len(summary) if isinstance(summary, list) else 0
        summary_noun = "item" if summary_count == 1 else "items"
        encrypted = message.get("encrypted_content")
        encrypted_note = (
            f"; {len(encrypted)} encrypted chars retained"
            if isinstance(encrypted, str)
            else ""
        )
        content = f"{index}. Reasoning — {summary_count} summary {summary_noun}{encrypted_note}"
    elif item_type == "function_call_output":
        output = message.get("output")
        preview = _value_preview(output)
        size_label = _value_size(output)
        content = f"{index}. Result"
        if preview:
            content = f"{content} — {preview}"
    else:
        role = message.get("role")
        label = role if isinstance(role, str) else "unlabelled"
        preview = _message_preview(message.get("content"))
        size_label = _value_size(message.get("content"))
        kind = "" if item_type in (None, "message") else f"{item_type} · "
        content = f"{index}. {kind}{label}"
        calls = message.get("tool_calls")
        if role == "assistant" and isinstance(calls, list) and calls:
            noun = "call" if len(calls) == 1 else "calls"
            content += f" · {len(calls)} tool {noun}"
            names = ", ".join(
                (_tool_call_name(call) or "unnamed") if isinstance(call, dict) else "unknown"
                for call in calls
            )
            preview = _compact_text(
                f"{names}; {preview}" if preview else names,
                COMPLETED_SUMMARY_ITEM_CHARS,
            )
        if preview:
            content = f"{content} — {preview}"
    return SummaryItem(
        content,
        id=f"message-{index}",
        detail_content=_json(message),
        json_content=True,
        size_label=size_label,
    )


def _message_summary_items(messages: list[dict[str, Any]]) -> list[SummaryItem]:
    if not messages:
        return [SummaryItem("Messages — none captured", id="messages-empty")]
    return [
        _message_summary_item(index, message)
        for index, message in enumerate(messages, start=1)
    ]


def _tool_call_arguments(tool_call: dict[str, Any]) -> Any:
    function = tool_call.get("function")
    return (function if isinstance(function, dict) else tool_call).get("arguments")


def _tool_call_size(tool_call: dict[str, Any]) -> str | None:
    arguments = _tool_call_arguments(tool_call)
    if arguments is None:
        return None
    value = arguments if isinstance(arguments, str) else _json(arguments)
    return f"{len(value):,} args ch"


def _tool_call_preview(tool_call: dict[str, Any]) -> str:
    arguments = _tool_call_arguments(tool_call)
    if arguments is None:
        return ""
    return _completed_text_summary(arguments if isinstance(arguments, str) else _json(arguments))


def _tool_call_summary_items(tool_calls: list[dict[str, Any]]) -> list[SummaryItem]:
    items = []
    for index, tool_call in enumerate(tool_calls, start=1):
        value = _json(tool_call)
        preview = _tool_call_preview(tool_call) or _completed_text_summary(value)
        items.append(
            SummaryItem(
                f"{index}. {_tool_call_name(tool_call) or 'unnamed'} — {preview}",
                id=f"tool-call-{index}", detail_content=value, json_content=True,
                size_label=_tool_call_size(tool_call),
            )
        )
    return items


def _tool_summary_items(tools: list[dict[str, Any]]) -> list[SummaryItem]:
    """Make each declared schema independently readable from the outline."""

    items = []
    for index, tool in enumerate(tools, start=1):
        value = _json(tool)
        function = tool.get("function")
        description = (function if isinstance(function, dict) else tool).get("description")
        preview = _completed_text_summary(
            description if isinstance(description, str) and description.strip() else value
        )
        items.append(
            SummaryItem(
                f"{index}. {_tool_name(tool) or 'unnamed'} — {preview}",
                id=f"tool-{index}", detail_content=value, json_content=True,
                size_label=f"{len(value):,} JSON ch",
            )
        )
    return items


def _completed_semantic_groups(
    chat: Any,
) -> tuple[list[SummaryItem], list[SummaryItem]]:
    input_items = [
        SummaryItem(f"Messages ({len(chat.messages)})", heading=True),
        *_message_summary_items(chat.messages),
    ]
    if getattr(chat, "input_text", None) is not None:
        input_items = [SummaryItem(
            f"Input text — {_value_preview(chat.input_text) or '(empty)'}", id="input-text",
            detail_content=chat.input_text, size_label=_value_size(chat.input_text),
        )]
    if chat.tools:
        input_items.extend(
            [
                SummaryItem(f"Tools ({len(chat.tools)})", heading=True),
                *_tool_summary_items(chat.tools),
            ]
        )
    else:
        input_items.append(SummaryItem("Tools — none", id="tools-empty"))
    output_items = []
    if chat.content:
        output_items.append(
            SummaryItem(
                "Response — "
                f"{_completed_text_summary(chat.content)}",
                id="response-full",
                detail_content=chat.content,
                size_label=_value_size(chat.content),
            )
        )
    else:
        output_items.append(SummaryItem("Response — empty", id="response-full"))
    if chat.thinking:
        output_items.append(
            SummaryItem(
                "Thinking — "
                f"{_completed_text_summary(chat.thinking)}",
                id="thinking-full",
                detail_content=chat.thinking,
                size_label=_value_size(chat.thinking),
            )
        )
    else:
        output_items.append(SummaryItem("Thinking — empty", id="thinking-full"))
    if chat.tool_calls:
        output_items.extend(
            [
                SummaryItem(f"Tool calls ({len(chat.tool_calls)})", heading=True),
                *_tool_call_summary_items(chat.tool_calls),
            ]
        )
    else:
        output_items.append(SummaryItem("Tool calls — none", id="tool-calls-empty"))
    return input_items, output_items


def _tool_names(tools: list[dict[str, Any]]) -> str:
    names = [name for tool in tools if (name := _tool_name(tool))]
    return ", ".join(names) or "(none declared)"


def _tool_call_names(tool_calls: list[dict[str, Any]]) -> str:
    names = [name for tool_call in tool_calls if (name := _tool_call_name(tool_call))]
    return ", ".join(names) or "(none observed)"


def _outline_group(group_id: str, title: str, items: list[SummaryItem]) -> DetailSection:
    return DetailSection(
        id=group_id, title=title, content="\n".join(item.content for item in items),
        collapsed=False, summary_items=tuple(items),
    )


def _reader_item(section: DetailSection) -> SummaryItem:
    return SummaryItem(
        section.title, id=section.id, detail_content=section.content,
        json_content=section.json_content, original_content=section.original_content,
        size_label=section.size_label,
    )


def _completed_sections(transaction: Transaction, transport: str) -> list[DetailSection]:
    chat = transaction.native_chat or transaction.compatible_chat
    model = (chat.model or "Model not captured") if chat is not None else "Undecoded capture"
    context = [model, transaction.path_qs, transaction_row(transaction).status]
    if transaction.duration_seconds is not None:
        context.append(f"{transaction.duration_seconds:.3f} s")
    if transaction.forwarded_request_body is not None:
        context.append(f"developer → system × {transaction.developer_role_rewrite_count}")
    sections = [DetailSection("context", "Capture", " · ".join(context), False)]
    if transaction.error:
        sections.append(DetailSection(
            "capture-error", "Error", f"ERROR · {transaction.error}", False,
        ))
    if chat is not None and chat.decode_errors:
        sections.append(DetailSection(
            "capture-warnings", "Warnings",
            f"WARNING · {len(chat.decode_errors)} decoder messages · "
            f"{_compact_text(chat.decode_errors[0], COMPLETED_SUMMARY_ITEM_CHARS)}", False,
        ))

    if chat is not None:
        input_items, output_items = _completed_semantic_groups(chat)
    else:
        input_items = [SummaryItem("Semantic input — not decoded", id="input-undecoded")]
        output_items = [SummaryItem("Semantic output — not decoded", id="output-undecoded")]
    evidence = [_body_section(
        "raw-request", f"Raw request — {len(transaction.request_body)} bytes",
        transaction.request_body,
    )]
    if transaction.forwarded_request_body is not None:
        evidence.append(_body_section(
            "forwarded-request",
            f"Forwarded request — developer → system × {transaction.developer_role_rewrite_count}; "
            f"{len(transaction.forwarded_request_body)} bytes",
            transaction.forwarded_request_body,
        ))
    if chat is not None:
        evidence.append(DetailSection(
            "raw-events", f"Raw response events — {len(chat.events)} events",
            _json(chat.events), True, json_content=True,
        ))
    else:
        evidence.append(_body_section(
            "raw-response", f"Raw response — {transaction.response_byte_count} bytes",
            transaction.response_body,
        ))
    evidence.append(DetailSection("overview", "Transport details", transport, True))
    if chat is not None:
        evidence.append(DetailSection(
            "diagnostics", "Completion details",
            _json({
                "transaction_error": transaction.error,
                "decode_errors": chat.decode_errors,
                "done": chat.done,
                "done_reason": chat.done_reason,
                "metrics": chat.metrics,
            }), True, json_content=True,
        ))
    sections.extend([
        _outline_group("input", "Input", input_items),
        _outline_group("output", "Output", output_items),
        _outline_group("evidence", "Evidence", [_reader_item(value) for value in evidence]),
    ])
    return sections


def detail_sections(transaction: Transaction) -> list[DetailSection]:
    duration = transaction.duration_seconds
    overview = [
        f"ID: {transaction.id}",
        f"Request: {transaction.method} {transaction.path_qs}",
        f"Status: {transaction.response_status or 'pending'}",
        f"State: {'complete' if transaction.completed_at else 'streaming'}",
        f"Response bytes: {transaction.response_byte_count}",
    ]
    if transaction.forwarded_request_body is not None:
        count = transaction.developer_role_rewrite_count
        noun = "message" if count == 1 else "messages"
        overview.append(
            f"Upstream rewrite: developer → system ({count} {noun})"
        )
    if duration is not None:
        overview.append(f"Duration: {duration:.3f}s")

    if transaction.completed_at is not None:
        return _completed_sections(transaction, "\n".join(overview))

    sections = [
        DetailSection(
            id="overview",
            title="Overview",
            content="\n".join(overview),
            collapsed=False,
        )
    ]
    chat = transaction.native_chat or transaction.compatible_chat
    if chat is not None:
        if transaction.completed_at is None:
            event_label = "event" if len(chat.events) == 1 else "events"
            sections.extend(
                [
                    DetailSection(
                        id="messages",
                        title="Request preview (streaming)",
                        content=_bytes_preview(
                            transaction.request_body,
                            label="request",
                        ),
                        collapsed=False,
                    ),
                    DetailSection(
                        id="tools",
                        title=f"Available tools ({len(chat.tools)}; streaming summary)",
                        content=_tool_names(chat.tools),
                        collapsed=True,
                    ),
                    DetailSection(
                        id="response",
                        title=f"Response ({len(chat.content)} chars; streaming preview)",
                        content=_streaming_text_preview(
                            chat.content,
                            label="response",
                        ),
                        collapsed=False,
                    ),
                    DetailSection(
                        id="thinking",
                        title=f"Thinking ({len(chat.thinking)} chars; streaming preview)",
                        content=_streaming_text_preview(
                            chat.thinking,
                            label="thinking",
                        ),
                        collapsed=True,
                    ),
                    DetailSection(
                        id="tool-calls",
                        title=f"Tool calls ({len(chat.tool_calls)}; streaming summary)",
                        content=_tool_call_names(chat.tool_calls),
                        collapsed=not chat.tool_calls,
                    ),
                    DetailSection(
                        id="diagnostics",
                        title="Completion / diagnostics",
                        content=_json(
                            {
                                "transaction_error": transaction.error,
                                "decode_errors": chat.decode_errors,
                                "done": chat.done,
                                "done_reason": chat.done_reason,
                                "metrics": chat.metrics,
                            }
                        ),
                        collapsed=not transaction.error and not chat.decode_errors,
                        json_content=True,
                    ),
                    DetailSection(
                        id="raw-events",
                        title=f"Raw response events ({len(chat.events)})",
                        content=(
                            f"{len(chat.events)} raw response {event_label} captured. "
                            "Full raw events will be available after completion."
                        ),
                        collapsed=True,
                    ),
                    DetailSection(
                        id="raw-request",
                        title="Raw request (streaming)",
                        content=(
                            "The request preview above is bounded while streaming. "
                            "Full raw request will be available after completion."
                        ),
                        collapsed=True,
                    ),
                ]
            )
            return sections

    else:
        response_body = transaction.response_body
        sections.append(
            _body_section(
                "raw-response", f"Raw response ({len(response_body)} bytes)", response_body,
                collapsed=False,
                format_json=transaction.completed_at is not None,
            )
        )

    if transaction.forwarded_request_body is not None:
        sections.append(
            _body_section(
                "forwarded-request", (
                    "Forwarded request "
                    "(developer → system "
                    f"× {transaction.developer_role_rewrite_count}; "
                    f"{len(transaction.forwarded_request_body)} bytes)"
                ),
                transaction.forwarded_request_body,
                format_json=transaction.completed_at is not None,
            )
        )
    sections.append(
        _body_section(
            "raw-request", f"Raw request ({len(transaction.request_body)} bytes)",
            transaction.request_body, format_json=transaction.completed_at is not None,
        )
    )
    return sections
