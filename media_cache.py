# media_cache.py

"""
Disk+TTL memory cache for media descriptions.

- One JSON file per media item: <STATE_DIR>/media/<unique_id>.json
- In-memory TTL (default 6h) to avoid disk churn on hot items.
- Thread-safety not provided (the agent loop is single-threaded).
- Schema is flexible, but typical record keys:
    {
      "description": "...",              # REQUIRED by convention
      "kind": "photo|sticker|gif|png|animation",
      "llm": "gemini-1.5-pro",
      "ts": "2025-09-14T23:02:10Z",
      "sticker_set": "WENDYAI",
      "sticker_name": "😀"
    }
"""

from __future__ import annotations
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Tuple, Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours
DEFAULT_SUBDIR = "media"

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")

def _sanitize_unique_id(unique_id: str) -> str:
    """
    Ensure the file name is safe. If not, hex-encode the UTF-8 bytes.
    (Telegram file_unique_id is typically safe already.)
    """
    if _SAFE_ID.match(unique_id or ""):
        return unique_id
    return (unique_id or "").encode("utf-8").hex()

def _atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically to `path` (create directories as needed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent)) as tf:
        tmp_name = tf.name
        tf.write(text)
    os.replace(tmp_name, path)

@dataclass
class _MemEntry:
    value: str
    expires_at: float

class MediaCache:
    """
    On-disk + in-memory-ttl cache for media descriptions.

    Usage:
        cache = MediaCache(state_dir="/path/to/state")
        desc = cache.get("unique_id")           # -> str | None
        cache.put("unique_id", record_dict)     # writes JSON to disk; caches description in memory
    """

    def __init__(self, state_dir: str, ttl_seconds: int = DEFAULT_TTL_SECONDS, subdir: str = DEFAULT_SUBDIR):
        self.state_dir = Path(state_dir)
        self.media_dir = self.state_dir / subdir
        self.ttl = ttl_seconds
        self._mem: Dict[str, _MemEntry] = {}

        # Ensure directory exists eagerly so first write doesn't race on mkdir.
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def _file_for(self, unique_id: str) -> Path:
        safe = _sanitize_unique_id(unique_id)
        return self.media_dir / f"{safe}.json"

    def get(self, unique_id: str) -> Optional[str]:
        """
        Return the cached description (string) if present in memory or on disk; otherwise None.
        Only the 'description' field is returned here (common use case for prompt assembly).
        """
        now = time.time()

        # Memory check
        me = self._mem.get(unique_id)
        if me and me.expires_at > now:
            logger.debug(f"MEDIA CACHE HIT (mem) {unique_id}")
            return me.value
        elif me:
            # expired
            self._mem.pop(unique_id, None)

        # Disk check
        path = self._file_for(unique_id)
        if not path.exists():
            logger.debug(f"MEDIA CACHE MISS (disk) {unique_id}")
            return None

        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            logger.error(f"MEDIA CACHE READ ERROR {unique_id}: {e}")
            return None

        desc = payload.get("description")
        if isinstance(desc, str) and desc:
            self._mem[unique_id] = _MemEntry(value=desc, expires_at=now + self.ttl)
            logger.debug(f"MEDIA CACHE HIT (disk) {unique_id}")
            return desc

        logger.debug(f"MEDIA CACHE EMPTY DESC {unique_id}")
        return None

    def put(self, unique_id: str, record: Dict[str, Any]) -> None:
        """
        Persist a record for this media item. The record should include at least:
            {"description": "...", ...}
        """
        desc = record.get("description")
        if not isinstance(desc, str) or not desc:
            raise ValueError("record must include a non-empty 'description' string")

        # Write to disk atomically
        text = json.dumps(record, ensure_ascii=False, indent=2)
        path = self._file_for(unique_id)
        try:
            _atomic_write_text(path, text)
            logger.debug(f"MEDIA CACHE WRITE {unique_id} -> {path}")
        finally:
            # Update mem regardless; if disk failed, we'll regenerate next time
            self._mem[unique_id] = _MemEntry(value=desc, expires_at=time.time() + self.ttl)

    # Optional convenience if callers ever need full record (not just description):
    def read_record(self, unique_id: str) -> Optional[Dict[str, Any]]:
        path = self._file_for(unique_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"MEDIA CACHE READ ERROR {unique_id}: {e}")
            return None

_media_cache_singleton: Optional[MediaCache] = None

def get_media_cache(state_dir: str = "state") -> MediaCache:
    """
    Return a process-wide MediaCache instance rooted at `state_dir`.
    Reuses the same instance across calls; if `state_dir` changes, re-create it.
    """
    global _media_cache_singleton
    if _media_cache_singleton is None:
        _media_cache_singleton = MediaCache(state_dir)
    else:
        if Path(state_dir) != _media_cache_singleton.state_dir:
            _media_cache_singleton = MediaCache(state_dir)
    return _media_cache_singleton
