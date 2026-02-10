# src/admin_console/openrouter.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Admin console routes for OpenRouter model management.
"""

import asyncio
import logging

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]

from admin_console.openrouter_scraper import get_roleplay_models

logger = logging.getLogger(__name__)

# Create blueprint
openrouter_bp = Blueprint("openrouter", __name__)


@openrouter_bp.route("/api/openrouter/refresh-models", methods=["POST"])
def api_openrouter_refresh_models():
    """Trigger a refresh of OpenRouter roleplay models."""
    try:
        # Run async function in a thread pool
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            models = loop.run_until_complete(get_roleplay_models(force_refresh=True))
            return jsonify({
                "success": True,
                "model_count": len(models),
                "message": f"Successfully refreshed {len(models)} models",
            })
        finally:
            loop.close()
    except Exception as e:
        logger.error(f"Error refreshing OpenRouter models: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500
