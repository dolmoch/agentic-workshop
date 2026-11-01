"""Inspect native Ollama chat traffic through a local reverse proxy."""

from typing import Any

from .model import CaptureStore, Transaction

__all__ = ["CaptureStore", "Transaction", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .proxy import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
