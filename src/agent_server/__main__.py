# agent_server/__main__.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Allow running the agent server as python -m agent_server."""
import asyncio

from .main import main

if __name__ == "__main__":
    asyncio.run(main())
