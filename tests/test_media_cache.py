# tests/test_media_cache.py

import json
from pathlib import Path

import pytest

import media_cache


def test_media_cache_sliding_ttl(monkeypatch, tmp_path):
    """
    Verifies:
      - get() returns the full JSON record (dict)
      - sliding TTL: access extends life even if past previous expires_at
      - sweep evicts only unused entries
      - disk fallback reloads and re-memoizes after mem eviction
    """
    # Controlled clock
    t = {"now": 1_000.0}
    def _now():
        return t["now"]
    monkeypatch.setattr(media_cache.time, "time", _now)

    # Small TTL and sweep
    cache = media_cache.MediaCache(state_dir=tmp_path, ttl=100.0, sweep_interval=10.0)

    # Put first record
    rec1 = {"description": "hello world", "kind": "sticker", "sticker_set": "HotCherry"}
    cache.put("u1", rec1)

    # get() returns full dict
    r = cache.get("u1")
    assert isinstance(r, dict)
    assert r["description"] == "hello world"
    assert "u1" in cache._mem

    # Advance beyond TTL, sliding TTL should still return and extend
    t["now"] += 150.0  # > ttl
    r2 = cache.get("u1")
    assert r2["description"] == "hello world"
    # expires_at should have been extended
    assert cache._mem["u1"].expires_at > t["now"]

    # Put second record but do not access it further
    rec2 = {"description": "unused", "kind": "photo"}
    cache.put("u2", rec2)

    # Stagger access so u1 stays fresh but u2 expires
    t["now"] += 80.0             # 1150 -> 1230
    _ = cache.get("u1")          # extend u1 to 1230 + ttl = 1330

    t["now"] += 50.0             # now 1280 (> u2's 1250), (< u1's 1330)
    cache._sweep_if_needed()
    assert "u1" in cache._mem, "touched entry must remain after sweep"
    assert "u2" not in cache._mem, "untouched entry should be evicted by sweep"

    # Let u1 expire in mem and be swept out
    t["now"] += 200.0
    cache._sweep_if_needed()
    assert "u1" not in cache._mem, "expired entry should be removed by sweep"

    # Disk fallback: get() should reload from disk and re-memoize
    r3 = cache.get("u1")
    assert isinstance(r3, dict) and r3["description"] == "hello world"
    assert "u1" in cache._mem


def test_media_cache_put_requires_description(tmp_path):
    cache = media_cache.MediaCache(state_dir=tmp_path, ttl=60.0)
    with pytest.raises(ValueError):
        cache.put("bad", {"kind": "photo"})  # missing description

    with pytest.raises(ValueError):
        cache.put("also_bad", {"description": "   "})  # empty after strip

    # Good record writes to disk
    cache.put("good", {"description": "ok", "kind": "photo"})
    p: Path = cache.media_dir / "good.json"
    assert p.exists()
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["description"] == "ok"
