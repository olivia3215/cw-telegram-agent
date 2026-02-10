# tests/test_runtime_config.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import importlib


def test_media_model_runtime_update(monkeypatch):
    import config

    original_media_model = config.MEDIA_MODEL
    try:
        config.MEDIA_MODEL = "gemini-initial"
        import llm.media_helper as media_helper

        importlib.reload(media_helper)

        called = []

        def fake_create_llm_from_name(name):
            called.append(name)
            return f"llm:{name}"

        monkeypatch.setattr(media_helper, "create_llm_from_name", fake_create_llm_from_name)

        media_helper.get_media_llm()
        config.MEDIA_MODEL = "grok-updated"
        media_helper.get_media_llm()

        assert called == ["gemini-initial", "grok-updated"]
    finally:
        config.MEDIA_MODEL = original_media_model
