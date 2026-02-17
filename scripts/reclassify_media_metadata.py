#!/usr/bin/env python3
# scripts/reclassify_media_metadata.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Reclassify media metadata using byte sniffing as the primary signal.

For each media directory under CINDY_AGENT_CONFIG_PATH:
- detect MIME from media bytes
- classify media kind from detected MIME + existing metadata hints
- rename media file to match canonical extension when needed
- persist updated metadata through the proper backend:
  - state/media -> MySQL (via MediaService)
  - configdir media -> JSON files (via MediaService)

Dry-run by default. Use --apply to write changes.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Add src directory to path so we can import project modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.connection import get_db_connection  # noqa: E402
from media.media_service import get_media_service  # noqa: E402
from media.mime_utils import (  # noqa: E402
    classify_media_kind_from_mime_and_hint,
    detect_mime_type_from_bytes,
    get_file_extension_for_mime_type,
    normalize_mime_type,
)
from media.state_path import is_state_media_directory  # noqa: E402


@dataclass
class Change:
    unique_id: str
    old_kind: str | None
    new_kind: str
    old_mime: str | None
    new_mime: str
    old_media_file: str | None
    new_media_file: str | None
    media_dir: Path
    reason: str


@dataclass
class Stats:
    scanned: int = 0
    changed: int = 0
    skipped_no_record: int = 0
    skipped_no_file: int = 0
    skipped_unreadable_file: int = 0
    skipped_unsupported_ext: int = 0
    rename_conflicts: int = 0
    write_failures: int = 0


def _iter_state_unique_ids() -> list[str]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT unique_id FROM media_metadata ORDER BY unique_id")
            rows = cursor.fetchall()
            return [str(row["unique_id"]) for row in rows if row.get("unique_id")]
        finally:
            cursor.close()


def _iter_unique_ids_for_directory(media_dir: Path) -> list[str]:
    svc = get_media_service(media_dir)
    if svc.is_state_media:
        return _iter_state_unique_ids()

    ids: set[str] = set()
    for p in media_dir.glob("*.json"):
        if p.is_file() and p.stem:
            ids.add(p.stem)
    for p in media_dir.iterdir() if media_dir.exists() else []:
        if p.is_file() and p.suffix.lower() != ".json" and p.stem:
            ids.add(p.stem)
    return sorted(ids)


def _canonical_mime_from_file(media_file: Path, record_mime: str | None) -> tuple[str, str]:
    try:
        file_head = media_file.read_bytes()[:1024]
    except Exception as exc:
        raise RuntimeError(f"failed to read {media_file}: {exc}") from exc

    detected_mime = normalize_mime_type(detect_mime_type_from_bytes(file_head))
    existing_mime = normalize_mime_type(record_mime)

    if detected_mime == "application/gzip" and media_file.suffix.lower() == ".tgs":
        detected_mime = "application/x-tgsticker"

    if detected_mime and detected_mime != "application/octet-stream":
        return detected_mime, "byte-sniffed"
    if existing_mime:
        return existing_mime, "record-fallback"
    return "application/octet-stream", "unknown-fallback"


def _maybe_rename_media_file(
    media_dir: Path,
    unique_id: str,
    media_file: Path,
    new_mime: str,
    apply: bool,
) -> tuple[str | None, str]:
    ext = get_file_extension_for_mime_type(new_mime)
    if not ext or ext == "bin":
        return None, "no-canonical-extension"

    desired_name = f"{unique_id}.{ext}"
    if media_file.name == desired_name:
        return desired_name, "already-canonical"

    target = media_dir / desired_name
    if target.exists() and target.resolve() != media_file.resolve():
        return media_file.name, "rename-conflict"

    if apply:
        media_file.rename(target)
    return desired_name, "renamed"


def _process_unique_id(
    media_dir: Path,
    unique_id: str,
    *,
    apply: bool,
    stats: Stats,
) -> Change | None:
    svc = get_media_service(media_dir)
    record = svc.get_record(unique_id)
    if not record:
        stats.skipped_no_record += 1
        return None

    media_file = svc.resolve_media_file(unique_id, record)
    if not media_file or not media_file.exists() or not media_file.is_file():
        stats.skipped_no_file += 1
        return None

    old_kind = record.get("kind")
    old_mime = normalize_mime_type(record.get("mime_type"))
    old_media_file = record.get("media_file")

    try:
        new_mime, mime_reason = _canonical_mime_from_file(media_file, old_mime)
    except RuntimeError:
        stats.skipped_unreadable_file += 1
        return None

    hint_kind = old_kind if old_kind else None
    has_sticker_hint = bool(
        old_kind in {"sticker", "animated_sticker"}
        or record.get("sticker_set_name")
        or record.get("sticker_name")
    )
    new_kind = classify_media_kind_from_mime_and_hint(
        new_mime,
        hint_kind,
        has_sticker_attribute=has_sticker_hint,
    )

    new_media_file = old_media_file
    rename_reason = "not-attempted"
    if media_file.stem == unique_id:
        renamed_name, rename_reason = _maybe_rename_media_file(
            media_dir,
            unique_id,
            media_file,
            new_mime,
            apply,
        )
        if rename_reason == "rename-conflict":
            stats.rename_conflicts += 1
        elif rename_reason == "no-canonical-extension":
            stats.skipped_unsupported_ext += 1
        if renamed_name:
            new_media_file = renamed_name

    changed = (
        old_kind != new_kind
        or old_mime != new_mime
        or (new_media_file and old_media_file != new_media_file)
    )
    if not changed:
        return None

    record["kind"] = new_kind
    record["mime_type"] = new_mime
    if new_media_file:
        record["media_file"] = new_media_file

    if apply:
        try:
            svc.put_record(unique_id, record)
        except Exception:
            stats.write_failures += 1
            return None

    return Change(
        unique_id=unique_id,
        old_kind=old_kind,
        new_kind=new_kind,
        old_mime=old_mime,
        new_mime=new_mime,
        old_media_file=old_media_file,
        new_media_file=new_media_file,
        media_dir=media_dir,
        reason=f"mime={mime_reason}; rename={rename_reason}",
    )


def _resolve_config_media_dirs_from_env() -> list[Path]:
    config_path = os.environ.get("CINDY_AGENT_CONFIG_PATH", "").strip()
    if not config_path:
        raise ValueError(
            "CINDY_AGENT_CONFIG_PATH is not set. Source .env first or use wrapper script."
        )

    out: list[Path] = []
    for raw in config_path.split(":"):
        raw = raw.strip()
        if not raw:
            continue
        cfg_dir = Path(raw).expanduser().resolve()
        media_dir = (cfg_dir / "media").resolve()
        out.append(media_dir)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify media metadata and normalize media filenames."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default: dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit records processed per directory (0 = no limit).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every changed record.",
    )
    args = parser.parse_args()

    apply = args.apply
    stats = Stats()

    try:
        media_dirs = _resolve_config_media_dirs_from_env()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("Reclassify media metadata")
    print(f"- mode: {'APPLY' if apply else 'DRY-RUN'}")
    print(f"- config media dirs: {len(media_dirs)}")

    all_changes: list[Change] = []

    for media_dir in media_dirs:
        if not media_dir.exists() or not media_dir.is_dir():
            print(f"- skip missing directory: {media_dir}")
            continue

        backend = "MySQL(state/media)" if is_state_media_directory(media_dir) else "JSON(filesystem)"
        print(f"\nProcessing: {media_dir} [{backend}]")

        unique_ids = _iter_unique_ids_for_directory(media_dir)
        if args.limit and args.limit > 0:
            unique_ids = unique_ids[: args.limit]

        print(f"- candidate records: {len(unique_ids)}")

        for unique_id in unique_ids:
            stats.scanned += 1
            change = _process_unique_id(
                media_dir,
                unique_id,
                apply=apply,
                stats=stats,
            )
            if change:
                stats.changed += 1
                all_changes.append(change)
                if args.verbose:
                    print(
                        f"  * {change.unique_id}: "
                        f"kind {change.old_kind!r}->{change.new_kind!r}, "
                        f"mime {change.old_mime!r}->{change.new_mime!r}, "
                        f"file {change.old_media_file!r}->{change.new_media_file!r} "
                        f"[{change.reason}]"
                    )

    print("\nSummary")
    print(f"- scanned: {stats.scanned}")
    print(f"- changed: {stats.changed}")
    print(f"- skipped (no record): {stats.skipped_no_record}")
    print(f"- skipped (no media file): {stats.skipped_no_file}")
    print(f"- skipped (unreadable media file): {stats.skipped_unreadable_file}")
    print(f"- skipped (no canonical extension): {stats.skipped_unsupported_ext}")
    print(f"- rename conflicts: {stats.rename_conflicts}")
    print(f"- write failures: {stats.write_failures}")

    if not args.verbose and all_changes:
        print("\nSample changes (first 20)")
        for change in all_changes[:20]:
            print(
                f"- {change.unique_id} @ {change.media_dir.name}: "
                f"{change.old_kind!r}->{change.new_kind!r}, "
                f"{change.old_mime!r}->{change.new_mime!r}, "
                f"{change.old_media_file!r}->{change.new_media_file!r}"
            )

    if not apply:
        print("\nDry-run complete. Re-run with --apply to persist changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

