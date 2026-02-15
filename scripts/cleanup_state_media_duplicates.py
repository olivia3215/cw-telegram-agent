#!/usr/bin/env python3
# scripts/cleanup_state_media_duplicates.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Remove duplicated media from state/media when the same unique_id exists in any
config directory's media folder (curated).

This script:
- DOES NOT delete anything from config directories.
- Deletes files in state/media for matching unique_ids.
- Deletes corresponding MySQL rows in media_metadata for those unique_ids.

It is DRY-RUN by default; pass --apply to actually delete.

Expected usage:
    ./scripts/cleanup_state_media_duplicates.sh --dry-run
    ./scripts/cleanup_state_media_duplicates.sh --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

# Add src directory to path so we can import modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db.connection import get_db_connection  # noqa: E402


def _iter_unique_ids_from_media_dir(media_dir: Path) -> set[str]:
    unique_ids: set[str] = set()
    if not media_dir.exists() or not media_dir.is_dir():
        return unique_ids

    for p in media_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() == ".json":
            continue
        stem = p.stem.strip()
        if stem:
            unique_ids.add(stem)
    return unique_ids


def _iter_state_files_for_unique_id(state_media_dir: Path, unique_id: str) -> list[Path]:
    # Delete any file that starts with unique_id. (Includes media + any JSON sidecars.)
    return [p for p in state_media_dir.glob(f"{unique_id}.*") if p.is_file()]


def _chunked(it: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for x in it:
        batch.append(x)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _delete_mysql_media_metadata(unique_ids: list[str], dry_run: bool) -> int:
    if not unique_ids:
        return 0
    if dry_run:
        return len(unique_ids)

    deleted_rows = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            for batch in _chunked(unique_ids, 500):
                placeholders = ", ".join(["%s"] * len(batch))
                cursor.execute(
                    f"DELETE FROM media_metadata WHERE unique_id IN ({placeholders})",
                    tuple(batch),
                )
                deleted_rows += cursor.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
    return deleted_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Delete duplicates from state/media when also present in any configdir media."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete files and MySQL rows (default: dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of unique_ids to process (0 = no limit).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each unique_id and files to be deleted.",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    config_path = os.environ.get("CINDY_AGENT_CONFIG_PATH", "").strip()
    if not config_path:
        print(
            "ERROR: CINDY_AGENT_CONFIG_PATH is not set. Run via "
            "'./scripts/cleanup_state_media_duplicates.sh' or 'source .env' first.",
            file=sys.stderr,
        )
        return 2

    state_dir = os.environ.get("CINDY_AGENT_STATE_DIR", "state").strip()
    state_dir_path = Path(state_dir).expanduser()
    if not state_dir_path.is_absolute():
        state_dir_path = (Path(__file__).parent.parent / state_dir_path).resolve()
    state_media_dir = (state_dir_path / "media").resolve()

    config_dirs = [Path(p).expanduser().resolve() for p in config_path.split(":") if p.strip()]
    config_media_dirs = [(d / "media").resolve() for d in config_dirs]

    curated_unique_ids: set[str] = set()
    for media_dir in config_media_dirs:
        curated_unique_ids |= _iter_unique_ids_from_media_dir(media_dir)

    state_unique_ids = _iter_unique_ids_from_media_dir(state_media_dir)

    duplicates = sorted(state_unique_ids.intersection(curated_unique_ids))
    if args.limit and args.limit > 0:
        duplicates = duplicates[: args.limit]

    files_to_delete: list[Path] = []
    for uid in duplicates:
        files_to_delete.extend(_iter_state_files_for_unique_id(state_media_dir, uid))

    print("State/media duplicate cleanup")
    print(f"- Mode: {'DRY-RUN' if dry_run else 'APPLY'}")
    print(f"- state/media: {state_media_dir}")
    print(f"- Config media dirs ({len(config_media_dirs)}):")
    for d in config_media_dirs:
        print(f"  - {d}")
    print(f"- Curated unique_ids: {len(curated_unique_ids)}")
    print(f"- State unique_ids: {len(state_unique_ids)}")
    print(f"- Duplicates to remove from state: {len(duplicates)}")
    print(f"- Files to delete from state/media: {len(files_to_delete)}")

    if args.verbose and duplicates:
        for uid in duplicates:
            uid_files = _iter_state_files_for_unique_id(state_media_dir, uid)
            print(f"\n{uid}")
            for p in uid_files:
                print(f"  - {p}")

    # Delete files
    deleted_files = 0
    if not dry_run:
        for p in files_to_delete:
            try:
                p.unlink(missing_ok=True)
                deleted_files += 1
            except Exception as e:
                print(f"WARNING: failed to delete {p}: {e}", file=sys.stderr)

    # Delete MySQL rows
    deleted_rows = _delete_mysql_media_metadata(duplicates, dry_run=dry_run)

    print("\nResults")
    print(f"- Deleted state/media files: {deleted_files if not dry_run else '(dry-run)'}")
    print(f"- Deleted MySQL media_metadata rows: {deleted_rows if not dry_run else '(dry-run)'}")

    if dry_run:
        print("\nTo apply deletions, re-run with: --apply")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

