#!/usr/bin/env python3
# scripts/cleanup_orphaned_media.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Clean up orphaned media: files without metadata, and metadata without files.

Supports:
- state/media: MySQL metadata + files on disk. Orphaned metadata = row with no
  corresponding file; orphaned media = file with no MySQL row.
- Config directories (e.g. configdir/media, samples/media): JSON metadata + files.
  Orphaned metadata = .json with no corresponding media file; orphaned media =
  media file with no .json.

Default is dry-run; pass --apply to delete.

Usage:
    ./scripts/cleanup_orphaned_media.sh --dry-run
    ./scripts/cleanup_orphaned_media.sh --apply
    ./scripts/cleanup_orphaned_media.sh --apply --state-only
    ./scripts/cleanup_orphaned_media.sh --apply --config-only
"""

from __future__ import annotations

import argparse
import glob as glob_module
import os
import sys
from pathlib import Path

# Add src directory to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.connection import get_db_connection  # noqa: E402
from media.file_resolver import find_media_file  # noqa: E402
from media.media_service import MediaService  # noqa: E402
from media.state_path import get_resolved_state_media_path  # noqa: E402


def _iter_media_unique_ids_on_disk(media_dir: Path) -> set[str]:
    """Set of unique_ids that have at least one media file (non-JSON) on disk."""
    unique_ids: set[str] = set()
    if not media_dir.exists() or not media_dir.is_dir():
        return unique_ids
    for p in media_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() == ".json":
            continue
        if p.name.endswith(".tmp"):
            continue
        stem = p.stem.strip()
        if stem:
            unique_ids.add(stem)
    return unique_ids


def _iter_metadata_unique_ids_config(media_dir: Path) -> set[str]:
    """Set of unique_ids that have a .json metadata file in a config directory."""
    unique_ids: set[str] = set()
    if not media_dir.exists() or not media_dir.is_dir():
        return unique_ids
    for p in media_dir.glob("*.json"):
        if not p.is_file():
            continue
        if p.name.endswith(".tmp"):
            continue
        stem = p.stem.strip()
        if stem:
            unique_ids.add(stem)
    return unique_ids


def _media_file_exists(media_dir: Path, unique_id: str, record: dict | None) -> bool:
    """True if a media file (non-JSON) exists for this unique_id."""
    if record and record.get("media_file"):
        candidate = media_dir / str(record["media_file"])
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
            return True
    return find_media_file(media_dir, unique_id) is not None


def _all_mysql_unique_ids() -> set[str]:
    """All unique_ids currently in media_metadata (state/media)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT unique_id FROM media_metadata")
            rows = cursor.fetchall()
            return {str(r["unique_id"]) for r in rows if r.get("unique_id")}
        finally:
            cursor.close()


def _collect_state_media_orphans(
    state_media_dir: Path,
    mysql_unique_ids: set[str],
    disk_media_unique_ids: set[str],
    load_record: callable,
) -> tuple[list[str], list[str]]:
    """
    Returns (orphaned_metadata_unique_ids, orphaned_media_unique_ids).
    Orphaned metadata = in MySQL but no media file on disk.
    Orphaned media = media file on disk but no MySQL row.
    """
    orphaned_metadata: list[str] = []
    for uid in mysql_unique_ids:
        record = load_record(uid)
        if not _media_file_exists(state_media_dir, uid, record):
            orphaned_metadata.append(uid)
    orphaned_media = sorted(disk_media_unique_ids - mysql_unique_ids)
    return (orphaned_metadata, orphaned_media)


def _collect_config_dir_orphans(
    media_dir: Path,
    meta_unique_ids: set[str],
    disk_media_unique_ids: set[str],
    get_record: callable,
) -> tuple[list[str], list[str]]:
    """
    Returns (orphaned_metadata_unique_ids, orphaned_media_unique_ids).
    Orphaned metadata = has .json but no media file; orphaned media = has media file but no .json.
    """
    orphaned_metadata: list[str] = []
    for uid in meta_unique_ids:
        record = get_record(uid)
        if not _media_file_exists(media_dir, uid, record):
            orphaned_metadata.append(uid)
    orphaned_media = sorted(disk_media_unique_ids - meta_unique_ids)
    return (orphaned_metadata, orphaned_media)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up orphaned media (files without metadata, metadata without files).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete; default is dry-run.",
    )
    parser.add_argument(
        "--state-only",
        action="store_true",
        help="Only process state/media (MySQL + disk).",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Only process config directories' media.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each unique_id and path affected.",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    do_state = not args.config_only
    do_config = not args.state_only

    if args.state_only and args.config_only:
        print("ERROR: use at most one of --state-only and --config-only.", file=sys.stderr)
        return 2

    state_media_dir = get_resolved_state_media_path()
    if do_state and (not state_media_dir or not state_media_dir.exists()):
        print(
            "ERROR: state/media not found. Set CINDY_AGENT_STATE_DIR and ensure state/media exists.",
            file=sys.stderr,
        )
        return 2

    config_path = os.environ.get("CINDY_AGENT_CONFIG_PATH", "").strip()
    if not config_path:
        config_path = "samples:configdir"
    config_dirs = [Path(p.strip()).expanduser().resolve() for p in config_path.split(":") if p.strip()]
    config_media_dirs = [d / "media" for d in config_dirs if (d / "media").exists()]

    print("Orphaned media cleanup")
    print(f"- Mode: {'DRY-RUN' if dry_run else 'APPLY'}")
    print(f"- Scope: state={do_state}, config={do_config}")

    total_meta_removed = 0
    total_media_removed = 0

    # --- State/media ---
    if do_state and state_media_dir:
        mysql_ids = _all_mysql_unique_ids()
        disk_ids = _iter_media_unique_ids_on_disk(state_media_dir)
        svc = MediaService(state_media_dir)

        def load_record(uid: str):
            return svc.get_record(uid)

        orphaned_meta, orphaned_media = _collect_state_media_orphans(
            state_media_dir, mysql_ids, disk_ids, load_record
        )

        print(f"\nstate/media: {state_media_dir}")
        print(f"  MySQL records: {len(mysql_ids)}")
        print(f"  Media files (by unique_id): {len(disk_ids)}")
        print(f"  Orphaned metadata (no file): {len(orphaned_meta)}")
        print(f"  Orphaned media (no MySQL): {len(orphaned_media)}")

        if args.verbose:
            for uid in orphaned_meta:
                print(f"    [metadata] {uid}")
            for uid in orphaned_media:
                for p in state_media_dir.glob(f"{glob_module.escape(uid)}.*"):
                    if p.is_file() and p.suffix.lower() != ".json":
                        print(f"    [media]    {p}")

        if not dry_run:
            for uid in orphaned_meta:
                try:
                    from db import media_metadata
                    media_metadata.delete_media_metadata(uid)
                    total_meta_removed += 1
                except Exception as e:
                    print(f"WARNING: failed to delete MySQL {uid}: {e}", file=sys.stderr)
            for uid in orphaned_media:
                files = [p for p in state_media_dir.glob(f"{glob_module.escape(uid)}.*") if p.is_file() and p.suffix.lower() != ".json"]
                total_media_removed += len(files)
                svc.delete_media_files(uid, record=None)
        elif orphaned_meta or orphaned_media:
            total_meta_removed += len(orphaned_meta)
            total_media_removed += sum(
                len([p for p in state_media_dir.glob(f"{glob_module.escape(uid)}.*") if p.is_file() and p.suffix.lower() != ".json"])
                for uid in orphaned_media
            )

    # --- Config directories ---
    for media_dir in config_media_dirs:
        if not do_config:
            break
        meta_ids = _iter_metadata_unique_ids_config(media_dir)
        disk_ids = _iter_media_unique_ids_on_disk(media_dir)
        svc = MediaService(media_dir)

        def get_record(uid: str):
            return svc.get_record(uid)

        orphaned_meta, orphaned_media = _collect_config_dir_orphans(
            media_dir, meta_ids, disk_ids, get_record
        )

        print(f"\nconfig: {media_dir}")
        print(f"  JSON metadata: {len(meta_ids)}")
        print(f"  Media files (by unique_id): {len(disk_ids)}")
        print(f"  Orphaned metadata (no file): {len(orphaned_meta)}")
        print(f"  Orphaned media (no JSON): {len(orphaned_media)}")

        if args.verbose:
            for uid in orphaned_meta:
                print(f"    [metadata] {media_dir / (uid + '.json')}")
            for uid in orphaned_media:
                for p in media_dir.glob(f"{glob_module.escape(uid)}.*"):
                    if p.is_file() and p.suffix.lower() != ".json":
                        print(f"    [media]    {p}")

        if not dry_run:
            for uid in orphaned_meta:
                try:
                    json_path = media_dir / f"{uid}.json"
                    if json_path.exists():
                        json_path.unlink()
                    total_meta_removed += 1
                except Exception as e:
                    print(f"WARNING: failed to delete {media_dir / (uid + '.json')}: {e}", file=sys.stderr)
            for uid in orphaned_media:
                files = [p for p in media_dir.glob(f"{glob_module.escape(uid)}.*") if p.is_file() and p.suffix.lower() != ".json"]
                total_media_removed += len(files)
                svc.delete_media_files(uid, record=None)
        elif orphaned_meta or orphaned_media:
            total_meta_removed += len(orphaned_meta)
            total_media_removed += sum(
                len([p for p in media_dir.glob(f"{glob_module.escape(uid)}.*") if p.is_file() and p.suffix.lower() != ".json"])
                for uid in orphaned_media
            )

    print("\n--- Summary ---")
    print(f"Orphaned metadata removed: {total_meta_removed}" + (" (dry-run)" if dry_run and (total_meta_removed or total_media_removed) else ""))
    print(f"Orphaned media files removed: {total_media_removed}" + (" (dry-run)" if dry_run and (total_meta_removed or total_media_removed) else ""))
    if dry_run and (total_meta_removed or total_media_removed):
        print("\nTo apply deletions, re-run with: --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())