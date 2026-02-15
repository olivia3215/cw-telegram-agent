# src/media/agent_media.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Centralized helpers for agent-specific curated media directories.

Agent media is stored in: `{agent.config_directory}/media/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def get_agent_media_dir(agent: Any) -> Path:
    """
    Return the curated media directory for an agent.

    This is the canonical location for agent-curated media metadata + files.
    """
    config_dir = getattr(agent, "config_directory", None)
    if not config_dir or not str(config_dir).strip():
        raise ValueError("Agent has no config_directory; cannot resolve agent media dir")
    return (Path(str(config_dir)).expanduser().resolve() / "media").resolve()

