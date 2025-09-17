# media_cache.py
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


@dataclass
class _MemEntry:
    """In-memory cache entry (sliding TTL)."""

    value: dict[str, Any]  # full JSON record with at least "description": str
    expires_at: float


class MediaCache:
    """
    Disk-backed + in-memory (TTL) cache for media descriptions/metadata.

    - JSON stored on disk at: <state_dir>/media/<unique_id>.json
    - In-memory entries hold the **full record** (dict), not just description text.
    - Sliding TTL:
        * If an entry is present in memory, it is returned **even if** its
          expires_at is in the past, and we extend its life.
        * A periodic sweep removes entries that haven't been touched.
    - API:
        get(unique_id) -> dict | None
        put(unique_id, record: dict)  # record must contain non-empty "description": str
    """

    def __init__(
        self,
        state_dir: str | Path,
        ttl: float = 3600.0,
        sweep_interval: float | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.media_dir = self.state_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self.ttl = float(ttl)
        # default sweep every min(300s, ttl/2) to keep overhead tiny
        self._sweep_interval = (
            float(sweep_interval)
            if sweep_interval is not None
            else max(60.0, min(300.0, self.ttl / 2))
        )
        self._mem: dict[str, _MemEntry] = {}
        self._last_sweep = time.time()

    # ---------- internals ----------

    def _file_for(self, unique_id: str) -> Path:
        return self.media_dir / f"{unique_id}.json"

    def _sweep_if_needed(self) -> None:
        now = time.time()
        if now - self._last_sweep < self._sweep_interval:
            return
        expired = [k for k, e in self._mem.items() if e.expires_at < now]
        for k in expired:
            self._mem.pop(k, None)
        if expired:
            logger.debug(f"MEDIA CACHE SWEEP: removed {len(expired)} expired entries")
        self._last_sweep = now

    # ---------- public API ----------

    def get(self, unique_id: str) -> dict[str, Any] | None:
        """
        Return the full record dict if known, else None.
        Sliding TTL semantics: if present in memory, we return it and extend TTL.
        """
        self._sweep_if_needed()
        now = time.time()

        entry = self._mem.get(unique_id)
        if entry is not None:
            # Sliding TTL: extend on access regardless of old expiry
            entry.expires_at = now + self.ttl
            logger.debug(f"MEDIA CACHE HIT (mem) {unique_id}")
            return entry.value

        # Disk lookup
        path = self._file_for(unique_id)
        if not path.exists():
            logger.debug(f"MEDIA CACHE MISS (disk) {unique_id}")
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"MEDIA CACHE READ ERROR {unique_id}: {e}")
            return None

        if (
            isinstance(payload, dict)
            and isinstance(payload.get("description"), str)
            and payload["description"]
        ):
            self._mem[unique_id] = _MemEntry(value=payload, expires_at=now + self.ttl)
            logger.debug(f"MEDIA CACHE HIT (disk) {unique_id}")
            return payload

        logger.debug(f"MEDIA CACHE EMPTY/BAD RECORD {unique_id}")
        return None

    def put(self, unique_id: str, record: dict[str, Any]) -> None:
        """
        Save a full record (dict) to disk and memory. Must include 'description': str (non-empty).
        """
        desc = record.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise ValueError("record must include a non-empty 'description' string")

        text = json.dumps(record, ensure_ascii=False, indent=2)
        path = self._file_for(unique_id)
        _atomic_write_text(path, text)

        # Memoize full record and sweep occasionally
        self._mem[unique_id] = _MemEntry(
            value=record, expires_at=time.time() + self.ttl
        )
        self._sweep_if_needed()
        logger.debug(f"MEDIA CACHE WRITE {unique_id} -> {path}")


# ---------- singleton helper ----------
_GLOBAL_CACHE: MediaCache | None = None


def get_media_cache() -> MediaCache:
    """
    Return a process-wide MediaCache. Uses CINDY_AGENT_STATE_DIR if present,
    otherwise defaults to ./state.
    """
    global _GLOBAL_CACHE
    if _GLOBAL_CACHE is None:
        state_dir = Path(os.environ.get("CINDY_AGENT_STATE_DIR", "state"))
        _GLOBAL_CACHE = MediaCache(state_dir=state_dir)
    return _GLOBAL_CACHE
