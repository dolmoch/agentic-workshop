from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Callable
from uuid import uuid4


LOGGER = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class NativeChat:
    model: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    content: str = ""
    thinking: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    done_reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    decode_errors: list[str] = field(default_factory=list)


@dataclass
class CompatibleChat(NativeChat):
    """A normalized summary for one observed OpenAI-compatible endpoint."""

    endpoint: str = ""
    input_text: str | None = None


@dataclass
class Transaction:
    method: str
    path_qs: str
    request_body: bytes
    forwarded_request_body: bytes | None = None
    developer_role_rewrite_count: int = 0
    id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=utc_now)
    response_status: int | None = None
    response_chunks: list[bytes] = field(default_factory=list)
    response_byte_count: int = 0
    completed_at: datetime | None = None
    error: str | None = None
    native_chat: NativeChat | None = None
    compatible_chat: CompatibleChat | None = None

    @property
    def response_body(self) -> bytes:
        return b"".join(self.response_chunks)

    def append_response_chunk(self, chunk: bytes) -> None:
        self.response_chunks.append(chunk)
        self.response_byte_count += len(chunk)

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()


CaptureCallback = Callable[[Transaction], None]


class CaptureStore:
    def __init__(
        self,
        max_captures: int = 200,
        on_change: CaptureCallback | None = None,
    ) -> None:
        if max_captures < 1:
            raise ValueError("max_captures must be at least 1")
        self._captures: deque[Transaction] = deque(maxlen=max_captures)
        self._evicted_count = 0
        self._on_change = on_change

    @property
    def max_captures(self) -> int:
        capacity = self._captures.maxlen
        assert capacity is not None
        return capacity

    @property
    def evicted_count(self) -> int:
        return self._evicted_count

    def add(self, transaction: Transaction) -> None:
        at_capacity = len(self._captures) == self.max_captures
        self._captures.append(transaction)
        if at_capacity:
            self._evicted_count += 1
        self.changed(transaction)

    def set_on_change(self, callback: CaptureCallback | None) -> None:
        self._on_change = callback

    def changed(self, transaction: Transaction) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(transaction)
        except Exception:
            LOGGER.exception("capture change callback failed")

    def complete(self, transaction: Transaction) -> None:
        transaction.completed_at = utc_now()
        self.changed(transaction)

    def snapshot(self) -> list[Transaction]:
        return list(self._captures)
