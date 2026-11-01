from __future__ import annotations

from dataclasses import dataclass

from .compatible import (
    ChatCompletionsReducer,
    CompatibleResponseFramer,
    ResponsesReducer,
    decode_chat_completions_request,
    decode_responses_request,
)
from .model import Transaction
from .native import NDJSONFramer, NativeChatReducer, decode_chat_request


StreamFramer = NDJSONFramer | CompatibleResponseFramer
StreamReducer = NativeChatReducer | ChatCompletionsReducer | ResponsesReducer


@dataclass
class CaptureDecoder:
    """Attach endpoint-specific decoding to a transaction without owning its bytes."""

    framer: StreamFramer
    reducer: StreamReducer

    @classmethod
    def start(cls, transaction: Transaction) -> CaptureDecoder | None:
        path = transaction.path_qs.partition("?")[0]
        if path == "/api/chat":
            transaction.native_chat, error = decode_chat_request(transaction.request_body)
            if error is not None:
                transaction.native_chat.decode_errors.append(error)
            return cls(NDJSONFramer(), NativeChatReducer(transaction.native_chat))
        if path == "/v1/chat/completions":
            transaction.compatible_chat, error = decode_chat_completions_request(
                transaction.request_body
            )
            if error is not None:
                transaction.compatible_chat.decode_errors.append(error)
            return cls(CompatibleResponseFramer(), ChatCompletionsReducer(transaction.compatible_chat))
        if path == "/v1/responses":
            transaction.compatible_chat, error = decode_responses_request(
                transaction.request_body
            )
            if error is not None:
                transaction.compatible_chat.decode_errors.append(error)
            return cls(CompatibleResponseFramer(), ResponsesReducer(transaction.compatible_chat))
        return None

    def consume_chunk(self, chunk: bytes) -> None:
        for line in self.framer.feed(chunk):
            self.reducer.consume_line(line)

    def finish(self) -> None:
        for line in self.framer.finish():
            self.reducer.consume_line(line)


def decode_completed_transaction(transaction: Transaction) -> None:
    """Populate semantic detail from a completed transaction's captured response."""

    decoder = CaptureDecoder.start(transaction)
    if decoder is None:
        return
    decoder.consume_chunk(transaction.response_body)
    decoder.finish()
