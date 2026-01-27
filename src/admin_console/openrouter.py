# admin_console/openrouter.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Admin console routes for OpenRouter model management.
"""

import asyncio
import logging
from datetime import UTC, datetime

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]

from admin_console.openrouter_scraper import (
    get_roleplay_models,
    load_cached_models,
    CACHE_FILE,
)

logger = logging.getLogger(__name__)

# Create blueprint
openrouter_bp = Blueprint("openrouter", __name__)


@openrouter_bp.route("/api/admin/openrouter/models-status", methods=["GET"])
def api_openrouter_models_status():
    """Get status of cached OpenRouter models."""
    try:
        cached = load_cached_models()
        if cached is None:
            return jsonify({
                "cached": False,
                "model_count": 0,
                "last_refresh": None,
            })
        
        # Get cache file modification time
        if CACHE_FILE.exists():
            mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=UTC)
            return jsonify({
                "cached": True,
                "model_count": len(cached),
                "last_refresh": mtime.isoformat(),
            })
        else:
            return jsonify({
                "cached": False,
                "model_count": 0,
                "last_refresh": None,
            })
    except Exception as e:
        logger.error(f"Error getting OpenRouter models status: {e}")
        return jsonify({"error": str(e)}), 500


@openrouter_bp.route("/api/admin/openrouter/refresh-models", methods=["POST"])
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
