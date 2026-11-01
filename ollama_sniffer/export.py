from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import zipfile

from .decoding import decode_completed_transaction
from .model import Transaction


SCHEMA_VERSION = 1
ARCHIVE_SCHEMA_VERSION = 1


class CaptureImportError(ValueError):
    pass


def _body_payload(body: bytes) -> dict[str, str]:
    try:
        return {"encoding": "utf-8", "content": body.decode("utf-8")}
    except UnicodeDecodeError:
        return {
            "encoding": "base64",
            "content": base64.b64encode(body).decode("ascii"),
        }


def capture_payload(transaction: Transaction) -> dict[str, object]:
    """Return a byte-faithful fixture for one completed transaction."""
    if transaction.completed_at is None:
        raise ValueError("only completed captures can be exported")

    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "capture": {
            "id": transaction.id,
            "method": transaction.method,
            "path_qs": transaction.path_qs,
            "started_at": transaction.started_at.isoformat(),
            "completed_at": transaction.completed_at.isoformat(),
            "response_status": transaction.response_status,
            "error": transaction.error,
        },
        "request_body": _body_payload(transaction.request_body),
        "response_body": _body_payload(transaction.response_body),
    }
    if transaction.forwarded_request_body is not None:
        if transaction.developer_role_rewrite_count < 1:
            raise ValueError("forwarded request requires a positive rewrite count")
        payload["forwarded_request_body"] = _body_payload(
            transaction.forwarded_request_body
        )
        payload["request_rewrite"] = {
            "developer_role_to_system": transaction.developer_role_rewrite_count,
        }
    return payload


def _capture_filename(transaction: Transaction) -> str:
    started_at = transaction.started_at.astimezone(timezone.utc)
    return f"capture-{started_at:%Y%m%dT%H%M%S.%fZ}-{transaction.id}.json"


def export_capture(transaction: Transaction, export_dir: Path) -> Path:
    """Write a new selected-capture fixture without replacing an existing file."""
    if not export_dir.is_dir():
        raise ValueError(f"export directory is not available: {export_dir}")

    output_path = export_dir / _capture_filename(transaction)
    _write_payload(capture_payload(transaction), output_path)
    return output_path


def _write_payload(payload: dict[str, object], output_path: Path) -> None:
    with output_path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def _endpoint_label(transaction: Transaction) -> str:
    path = transaction.path_qs.partition("?")[0].strip("/")
    return path.replace("/", "-") or "root"


def _archive_label(label: str) -> tuple[str, str]:
    display_label = label.strip()
    if not display_label:
        raise ValueError("archive label cannot be empty")
    safe_label = re.sub(r"[^a-z0-9]+", "-", display_label.lower()).strip("-")
    if not safe_label:
        raise ValueError("archive label must contain a letter or number")
    created_at = datetime.now(timezone.utc)
    return display_label, f"{safe_label}-{created_at:%Y%m%dT%H%M%S.%fZ}.zip"


def _archive_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export_archive(
    label: str,
    captures: list[Transaction],
    export_dir: Path,
) -> Path:
    """Write an explicitly selected, portable evidence package without replacing one."""
    if not export_dir.is_dir():
        raise ValueError(f"save directory is not available: {export_dir}")
    if not captures:
        raise ValueError("select one or more captures before saving an archive")
    if len({capture.id for capture in captures}) != len(captures):
        raise ValueError("selected captures must have distinct IDs")

    display_label, filename = _archive_label(label)
    ordered_captures = sorted(captures, key=lambda capture: (capture.started_at, capture.id))
    payloads = [capture_payload(capture) for capture in ordered_captures]
    members: list[dict[str, object]] = []
    archive_members: list[tuple[str, dict[str, object]]] = []
    for sequence, (capture, payload) in enumerate(
        zip(ordered_captures, payloads, strict=True),
        start=1,
    ):
        member_path = (
            f"captures/{sequence:03d}-{capture.method.lower()}-"
            f"{_endpoint_label(capture)}.json"
        )
        archive_members.append((member_path, payload))
        members.append(
            {
                "sequence": sequence,
                "file": member_path,
                "id": capture.id,
                "method": capture.method,
                "path_qs": capture.path_qs,
                "started_at": capture.started_at.isoformat(),
                "completed_at": capture.completed_at.isoformat(),
                "response_status": capture.response_status,
                "error": capture.error,
            }
        )
    archive_path = export_dir / filename
    manifest = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "archive": {
            "label": display_label,
            "capture_count": len(members),
        },
        "captures": members,
    }
    output = archive_path.open("xb")
    try:
        with output, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", _archive_json(manifest))
            for member_path, payload in archive_members:
                archive.writestr(member_path, _archive_json(payload))
    except BaseException:
        # Exclusive creation succeeded; remove only this attempt's partial file.
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def save_value_text(value: str, output_path: Path) -> Path:
    """Write the displayed value as UTF-8 without replacing an existing file."""
    output = output_path.open("x", encoding="utf-8", newline="")
    try:
        with output:
            output.write(value)
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def _restore_body(value: object, *, label: str) -> bytes:
    if not isinstance(value, dict):
        raise CaptureImportError(f"{label} must be an object")
    encoding = value.get("encoding")
    content = value.get("content")
    if not isinstance(encoding, str) or not isinstance(content, str):
        raise CaptureImportError(f"{label} requires string encoding and content")
    if encoding == "utf-8":
        return content.encode("utf-8")
    if encoding == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CaptureImportError(f"{label} contains invalid base64") from exc
    raise CaptureImportError(f"{label} uses unsupported encoding: {encoding}")


def _restore_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise CaptureImportError(f"{label} must be an ISO-8601 timestamp")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CaptureImportError(f"{label} must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise CaptureImportError(f"{label} must include a timezone")
    return timestamp


def _capture_field(capture: dict[str, object], name: str, expected: type) -> object:
    value = capture.get(name)
    if not isinstance(value, expected):
        raise CaptureImportError(f"capture.{name} must be a {expected.__name__}")
    return value


def _import_capture_payload(payload: object) -> Transaction:
    if not isinstance(payload, dict):
        raise CaptureImportError("capture fixture must be a JSON object")
    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise CaptureImportError(
            f"unsupported capture schema: {payload.get('schema_version')!r}"
        )
    capture = payload.get("capture")
    if not isinstance(capture, dict):
        raise CaptureImportError("capture must be an object")

    capture_id = _capture_field(capture, "id", str)
    method = _capture_field(capture, "method", str)
    path_qs = _capture_field(capture, "path_qs", str)
    if not capture_id or not method or not path_qs.startswith("/"):
        raise CaptureImportError("capture id, method, and path_qs must be non-empty")
    started_at = _restore_timestamp(capture.get("started_at"), label="capture.started_at")
    completed_at = _restore_timestamp(
        capture.get("completed_at"), label="capture.completed_at"
    )
    if completed_at < started_at:
        raise CaptureImportError("capture.completed_at cannot precede capture.started_at")
    response_status = capture.get("response_status")
    if response_status is not None and (
        isinstance(response_status, bool)
        or not isinstance(response_status, int)
        or not 100 <= response_status <= 599
    ):
        raise CaptureImportError("capture.response_status must be an HTTP status or null")
    error = capture.get("error")
    if error is not None and not isinstance(error, str):
        raise CaptureImportError("capture.error must be a string or null")

    transaction = Transaction(
        id=capture_id,
        method=method,
        path_qs=path_qs,
        request_body=_restore_body(payload.get("request_body"), label="request_body"),
        started_at=started_at,
        response_status=response_status,
        error=error,
    )
    has_forwarded_body = "forwarded_request_body" in payload
    has_rewrite = "request_rewrite" in payload
    if has_forwarded_body or has_rewrite:
        if not has_forwarded_body or not has_rewrite:
            raise CaptureImportError(
                "forwarded_request_body and request_rewrite must appear together"
            )
        rewrite = payload["request_rewrite"]
        if not isinstance(rewrite, dict):
            raise CaptureImportError("request_rewrite must be an object")
        rewrite_count = rewrite.get("developer_role_to_system")
        if (
            isinstance(rewrite_count, bool)
            or not isinstance(rewrite_count, int)
            or rewrite_count < 1
        ):
            raise CaptureImportError(
                "request_rewrite.developer_role_to_system must be a positive integer"
            )
        transaction.forwarded_request_body = _restore_body(
            payload["forwarded_request_body"], label="forwarded_request_body"
        )
        transaction.developer_role_rewrite_count = rewrite_count
    transaction.append_response_chunk(
        _restore_body(payload.get("response_body"), label="response_body")
    )
    transaction.completed_at = completed_at
    decode_completed_transaction(transaction)
    return transaction


def import_capture(input_path: Path) -> Transaction:
    """Restore one completed version-1 export for read-only inspection."""

    if not input_path.is_file():
        raise CaptureImportError(f"capture fixture is not a file: {input_path}")
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureImportError(f"unable to read capture fixture: {input_path}") from exc
    return _import_capture_payload(payload)


def _archive_members(manifest: object) -> list[dict[str, object]]:
    if not isinstance(manifest, dict):
        raise CaptureImportError("archive manifest must be a JSON object")
    if (
        type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != ARCHIVE_SCHEMA_VERSION
    ):
        raise CaptureImportError(
            f"unsupported archive schema: {manifest.get('schema_version')!r}"
        )
    archive_info = manifest.get("archive")
    if not isinstance(archive_info, dict):
        raise CaptureImportError("archive metadata must be an object")
    label = archive_info.get("label")
    capture_count = archive_info.get("capture_count")
    if not isinstance(label, str) or not label.strip():
        raise CaptureImportError("archive label must be a non-empty string")
    if isinstance(capture_count, bool) or not isinstance(capture_count, int):
        raise CaptureImportError("archive capture_count must be an integer")
    members = manifest.get("captures")
    if not isinstance(members, list) or not members:
        raise CaptureImportError("archive captures must be a non-empty list")
    if capture_count != len(members):
        raise CaptureImportError("archive capture_count does not match members")

    validated: list[dict[str, object]] = []
    member_paths: set[str] = set()
    member_ids: set[str] = set()
    for sequence, member in enumerate(members, start=1):
        if not isinstance(member, dict):
            raise CaptureImportError("archive member must be an object")
        if member.get("sequence") != sequence:
            raise CaptureImportError("archive members must have consecutive sequence")
        member_path = member.get("file")
        capture_id = member.get("id")
        if (
            not isinstance(member_path, str)
            or not member_path.startswith("captures/")
            or not member_path.endswith(".json")
            or ".." in member_path.split("/")
            or not isinstance(capture_id, str)
            or not capture_id
        ):
            raise CaptureImportError("archive member path or ID is invalid")
        if member_path in member_paths or capture_id in member_ids:
            raise CaptureImportError("archive member paths and IDs must be distinct")
        member_paths.add(member_path)
        member_ids.add(capture_id)
        validated.append(member)
    return validated


def _manifest_matches_capture(
    member: dict[str, object], transaction: Transaction
) -> bool:
    return all(
        member.get(key) == value
        for key, value in {
            "id": transaction.id,
            "method": transaction.method,
            "path_qs": transaction.path_qs,
            "started_at": transaction.started_at.isoformat(),
            "completed_at": transaction.completed_at.isoformat()
            if transaction.completed_at is not None
            else None,
            "response_status": transaction.response_status,
            "error": transaction.error,
        }.items()
    )


def import_archive(input_path: Path) -> list[Transaction]:
    """Restore a version-1 selected-capture ZIP without extracting or replaying it."""

    if not input_path.is_file():
        raise CaptureImportError(f"capture archive is not a file: {input_path}")
    try:
        with zipfile.ZipFile(input_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise CaptureImportError("archive contains duplicate member paths")
            if "manifest.json" not in names:
                raise CaptureImportError("archive is missing manifest.json")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CaptureImportError("unable to read archive manifest") from exc
            members = _archive_members(manifest)
            member_paths = [member["file"] for member in members]
            assert all(isinstance(path, str) for path in member_paths)
            expected_names = {"manifest.json", *member_paths}
            if set(names) != expected_names:
                raise CaptureImportError("archive members do not match manifest")

            captures: list[Transaction] = []
            for member in members:
                member_path = member["file"]
                assert isinstance(member_path, str)
                try:
                    payload = json.loads(archive.read(member_path))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CaptureImportError(
                        f"unable to read archive member: {member_path}"
                    ) from exc
                transaction = _import_capture_payload(payload)
                if not _manifest_matches_capture(member, transaction):
                    raise CaptureImportError(
                        f"archive manifest does not match member: {member_path}"
                    )
                captures.append(transaction)
            return captures
    except (OSError, zipfile.BadZipFile) as exc:
        raise CaptureImportError(f"unable to read capture archive: {input_path}") from exc
