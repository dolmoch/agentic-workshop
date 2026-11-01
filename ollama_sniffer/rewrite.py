"""Narrow, opt-in request transformations used for integration experiments."""

from __future__ import annotations

import json


_MESSAGE_CONTAINERS = {
    "/api/chat": "messages",
    "/v1/chat/completions": "messages",
    "/v1/responses": "input",
}


def rewrite_developer_roles(path_qs: str, body: bytes) -> tuple[bytes, int]:
    """Return an upstream body with direct developer roles changed to system.

    Only the observed top-level request containers participate.  A body which
    cannot be safely recognized, or which contains no matching role, is
    returned byte-for-byte so the caller can distinguish an inactive rewrite.
    """

    message_key = _MESSAGE_CONTAINERS.get(path_qs.partition("?")[0])
    if message_key is None:
        return body, 0
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, 0
    if not isinstance(payload, dict):
        return body, 0
    messages = payload.get(message_key)
    if not isinstance(messages, list):
        return body, 0

    rewrite_count = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "developer":
            continue
        message["role"] = "system"
        rewrite_count += 1
    if rewrite_count == 0:
        return body, 0
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        rewrite_count,
    )
