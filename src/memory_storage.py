from __future__ import annotations

import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable


class MemoryStorageError(Exception):
    """Raised when a memory/intent/plan storage file contains unexpected data."""


def _make_temp_path(file_path: Path) -> Path:
    if file_path.suffix:
        return file_path.with_suffix(file_path.suffix + ".tmp")
    return file_path.parent / f"{file_path.name}.tmp"


_LOCKS: dict[Path, Lock] = {}
_LOCKS_LOCK = Lock()


def _get_lock(file_path: Path) -> Lock:
    normalized = file_path.resolve()
    with _LOCKS_LOCK:
        lock = _LOCKS.get(normalized)
        if lock is None:
            lock = Lock()
            _LOCKS[normalized] = lock
        return lock


def _read_entries_unlocked(
    file_path: Path, property_name: str, default_id_prefix: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    entries: list[dict[str, Any]] = []
    existing_payload: dict[str, Any] | None = None

    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        except json.JSONDecodeError as exc:
            raise MemoryStorageError(f"Corrupted JSON file {file_path}: {exc}") from exc

        if isinstance(loaded, dict):
            existing_payload = dict(loaded)
            raw_entries = loaded.get(property_name, [])
        elif isinstance(loaded, list):
            raw_entries = loaded
        else:
            raise MemoryStorageError(
                f"Unsupported JSON root type {type(loaded).__name__} in {file_path}"
            )
        entries = _normalize_entries(raw_entries, default_id_prefix)
    return entries, existing_payload


def _write_entries_unlocked(
    file_path: Path,
    property_name: str,
    entries: Iterable[dict[str, Any]],
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload_dict = dict(payload or {})
    payload_dict[property_name] = list(entries)

    temp_path = _make_temp_path(file_path)
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload_dict, handle, indent=2, ensure_ascii=False)
    temp_path.replace(file_path)


def load_property_entries(
    file_path: Path,
    property_name: str,
    *,
    default_id_prefix: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Load entries for the given property from a JSON file that may contain either a dict
    or a bare list. Returns the normalized list of entries (with IDs ensured) and the
    existing payload dictionary when present.
    """
    lock = _get_lock(file_path)
    with lock:
        return _read_entries_unlocked(file_path, property_name, default_id_prefix)


def mutate_property_entries(
    file_path: Path,
    property_name: str,
    *,
    default_id_prefix: str,
    mutator: Callable[
        [list[dict[str, Any]], dict[str, Any] | None],
        tuple[list[dict[str, Any]], dict[str, Any] | None],
    ],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Atomically mutate the entries list for a given property. The mutator receives the
    current entries and payload and must return the updated versions to be persisted.
    """
    lock = _get_lock(file_path)
    with lock:
        entries, payload = _read_entries_unlocked(file_path, property_name, default_id_prefix)
        new_entries, new_payload = mutator(entries, payload)
        _write_entries_unlocked(
            file_path,
            property_name,
            new_entries,
            payload=new_payload,
        )
        return new_entries, new_payload


def write_property_entries(
    file_path: Path,
    property_name: str,
    entries: Iterable[dict[str, Any]],
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Persist the provided entries list back to the JSON file, ensuring the desired
    property contains the updated list and existing top-level keys are preserved.
    """
    lock = _get_lock(file_path)
    with lock:
        _write_entries_unlocked(
            file_path,
            property_name,
            entries,
            payload=payload,
        )


def _normalize_entries(
    raw_entries: Any, default_id_prefix: str
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_entries, list):
        return normalized

    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        sanitized = {k: v for k, v in entry.items() if k != "kind"}
        entry_id = sanitized.get("id")
        if not entry_id or not str(entry_id).strip():
            sanitized["id"] = f"{default_id_prefix}-{uuid.uuid4().hex[:8]}"
        normalized.append(sanitized)
    return normalized


__all__ = [
    "MemoryStorageError",
    "load_property_entries",
    "mutate_property_entries",
    "write_property_entries",
]

