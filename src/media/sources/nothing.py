# media/sources/nothing.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
A media source that always returns None.

Used when a directory doesn't exist, so we have something to cache
on the agent without needing special handling for missing directories.
"""

from typing import Any

from .base import MediaSource


class NothingMediaSource(MediaSource):
    """
    A media source that always returns None.

    Used when a directory doesn't exist, so we have something to cache
    on the agent without needing special handling for missing directories.
    """

    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        kind: str | None = None,
        sticker_set_name: str | None = None,
        sticker_name: str | None = None,
        **metadata,
    ) -> dict[str, Any] | None:
        return None

