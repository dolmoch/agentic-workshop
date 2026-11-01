from __future__ import annotations

import argparse
from pathlib import Path

from .export import CaptureImportError, import_archive, import_capture
from .model import CaptureStore, Transaction
from .tui import SnifferApp


def parse_listen(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("listen address must be HOST:PORT")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("listen port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("listen port must be between 1 and 65535")
    return host, port


def parse_export_dir(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parse_import_capture(value: str) -> Transaction:
    input_path = Path(value).expanduser()
    try:
        return import_capture(input_path)
    except CaptureImportError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_import_source(value: str) -> list[Transaction]:
    input_path = Path(value).expanduser()
    try:
        if input_path.suffix.lower() == ".zip":
            return import_archive(input_path)
        return [import_capture(input_path)]
    except CaptureImportError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ollama-sniffer",
        description="Inspect Ollama traffic through a local proxy or saved fixtures.",
    )
    parser.add_argument(
        "--listen",
        type=parse_listen,
        default=parse_listen("127.0.0.1:11435"),
        metavar="HOST:PORT",
        help="proxy listen address (default: 127.0.0.1:11435)",
    )
    parser.add_argument(
        "--upstream",
        default="http://127.0.0.1:11434",
        help="Ollama server origin (default: http://127.0.0.1:11434)",
    )
    parser.add_argument(
        "--max-captures",
        type=int,
        default=200,
        help="maximum retained transactions; oldest evicted at limit, including marked/live (default: 200)",
    )
    parser.add_argument(
        "--export-dir",
        type=parse_export_dir,
        metavar="PATH",
        help="initial directory for Save (default: working directory; editable in the dialog)",
    )
    parser.add_argument(
        "--import-capture",
        type=parse_import_source,
        action="append",
        metavar="PATH",
        help="open a version-1 capture fixture or evidence ZIP offline (repeatable; disables proxy)",
    )
    parser.add_argument(
        "--clamp-developer-role",
        action="store_true",
        help=(
            "forward direct developer messages as system for observed chat "
            "requests; original and forwarded bodies are both captured"
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = CaptureStore(max_captures=args.max_captures)
    imported_captures = [
        capture for source in args.import_capture or [] for capture in source
    ]
    imported_ids = [capture.id for capture in imported_captures]
    if len(set(imported_ids)) != len(imported_ids):
        build_parser().error("--import-capture fixtures must have distinct capture IDs")
    for capture in imported_captures:
        store.add(capture)
    app = SnifferApp(
        listen=args.listen,
        upstream=args.upstream,
        capture_store=store,
        export_dir=args.export_dir,
        start_proxy=not imported_captures,
        clamp_developer_role=args.clamp_developer_role,
    )
    app.run()


if __name__ == "__main__":
    main()
