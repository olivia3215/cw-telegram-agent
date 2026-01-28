# admin_console/llms.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Admin console routes for LLM model management.
"""

import asyncio
import logging
import re
from typing import Any

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from db.available_llms import (
    add_llm,
    delete_llm,
    get_all_llms,
    get_llm_by_id,
    get_llm_by_model_id,
    reorder_llms,
    update_llm,
    _determine_provider,
)
from config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

# Create blueprint
llms_bp = Blueprint("llms", __name__)


@llms_bp.route("/api/global/llms", methods=["GET"])
def api_get_llms():
    """Get all available LLMs from the database."""
    try:
        llms = get_all_llms()
        return jsonify({"llms": llms})
    except Exception as e:
        logger.error(f"Error getting LLMs: {e}")
        return jsonify({"error": str(e)}), 500


@llms_bp.route("/api/global/llms", methods=["POST"])
def api_add_llm():
    """Add a new LLM to the database."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        model_id = data.get("model_id")
        if not model_id:
            return jsonify({"error": "model_id is required"}), 400
        
        name = data.get("name", model_id)
        description = data.get("description")
        prompt_price = float(data.get("prompt_price", 0.0))
        completion_price = float(data.get("completion_price", 0.0))
        provider = data.get("provider")
        display_order = data.get("display_order")
        
        # If model_id contains "/", try to fetch from OpenRouter
        if "/" in model_id and OPENROUTER_API_KEY:
            try:
                from admin_console.openrouter_scraper import scrape_roleplay_models
                openrouter_models = asyncio.run(scrape_roleplay_models())
                for model in openrouter_models:
                    if model.get("value") == model_id:
                        # Use OpenRouter data if available, but allow overrides
                        if not name or name == model_id:
                            from db.available_llms import _extract_name_from_label
                            name = _extract_name_from_label(model.get("label", model_id))
                        if not description:
                            description = model.get("description") or f"Model via OpenRouter"
                        if prompt_price == 0.0 and completion_price == 0.0:
                            from db.available_llms import _parse_price_from_label
                            prompt_price, completion_price = _parse_price_from_label(model.get("label", ""))
                        break
            except Exception as e:
                logger.warning(f"Could not fetch model data from OpenRouter for {model_id}: {e}")
        
        # Determine provider if not provided
        if not provider:
            provider = _determine_provider(model_id)
        
        db_id = add_llm(
            model_id=model_id,
            name=name,
            description=description,
            prompt_price=prompt_price,
            completion_price=completion_price,
            provider=provider,
            display_order=display_order,
        )
        
        # Return the newly created LLM
        new_llm = get_llm_by_id(db_id)
        if not new_llm:
            logger.error(f"Failed to retrieve newly created LLM with id {db_id}")
            return jsonify({"error": f"LLM was created but could not be retrieved (id={db_id})"}), 500
        return jsonify(new_llm), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error_msg = str(e) if e else "Unknown error"
        error_type = type(e).__name__
        logger.error(f"Error adding LLM: {error_type}: {error_msg}", exc_info=True)
        return jsonify({"error": error_msg}), 500


@llms_bp.route("/api/global/llms/<int:llm_id>", methods=["PUT"])
def api_update_llm(llm_id: int):
    """Update an existing LLM in the database."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        model_id = data.get("model_id")
        name = data.get("name")
        description = data.get("description")
        prompt_price = data.get("prompt_price")
        completion_price = data.get("completion_price")
        provider = data.get("provider")
        
        # If model_id is being updated and contains "/", try to fetch from OpenRouter
        if model_id and "/" in model_id and OPENROUTER_API_KEY:
            try:
                from admin_console.openrouter_scraper import scrape_roleplay_models
                openrouter_models = asyncio.run(scrape_roleplay_models())
                for model in openrouter_models:
                    if model.get("value") == model_id:
                        # Auto-fill missing fields from OpenRouter
                        if name is None:
                            from db.available_llms import _extract_name_from_label
                            name = _extract_name_from_label(model.get("label", model_id))
                        if description is None:
                            description = model.get("description") or f"Model via OpenRouter"
                        if prompt_price is None or completion_price is None:
                            from db.available_llms import _parse_price_from_label
                            or_prompt, or_completion = _parse_price_from_label(model.get("label", ""))
                            if prompt_price is None:
                                prompt_price = or_prompt
                            if completion_price is None:
                                completion_price = or_completion
                        break
            except Exception as e:
                logger.warning(f"Could not fetch model data from OpenRouter for {model_id}: {e}")
        
        # Determine provider if model_id changed
        if model_id and not provider:
            provider = _determine_provider(model_id)
        
        update_llm(
            db_id=llm_id,
            model_id=model_id,
            name=name,
            description=description,
            prompt_price=float(prompt_price) if prompt_price is not None else None,
            completion_price=float(completion_price) if completion_price is not None else None,
            provider=provider,
        )
        
        # Return the updated LLM
        updated_llm = get_llm_by_id(llm_id)
        return jsonify(updated_llm)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating LLM {llm_id}: {e}")
        return jsonify({"error": str(e)}), 500


@llms_bp.route("/api/global/llms/<int:llm_id>", methods=["DELETE"])
def api_delete_llm(llm_id: int):
    """Delete an LLM from the database."""
    try:
        delete_llm(llm_id)
        return jsonify({"success": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.error(f"Error deleting LLM {llm_id}: {e}")
        return jsonify({"error": str(e)}), 500


@llms_bp.route("/api/global/llms/reorder", methods=["PUT"])
def api_reorder_llms():
    """Update display_order for multiple LLMs."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        order_mapping = data.get("order")
        if not order_mapping or not isinstance(order_mapping, dict):
            return jsonify({"error": "order must be a dictionary mapping id to display_order"}), 400
        
        # Convert string keys to int
        order_mapping_int = {int(k): int(v) for k, v in order_mapping.items()}
        
        reorder_llms(order_mapping_int)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error reordering LLMs: {e}")
        return jsonify({"error": str(e)}), 500


@llms_bp.route("/api/global/llms/openrouter-models", methods=["GET"])
def api_get_openrouter_models():
    """Get OpenRouter models for the 'add' pulldown menu."""
    try:
        if not OPENROUTER_API_KEY:
            return jsonify({"error": "OPENROUTER_API_KEY not set"}), 400
        
        from admin_console.openrouter_scraper import scrape_roleplay_models
        openrouter_models = asyncio.run(scrape_roleplay_models())
        
        # Format for pulldown menu
        formatted_models = []
        for model in openrouter_models:
            formatted_models.append({
                "value": model.get("value"),
                "label": model.get("label"),
                "description": model.get("description"),  # Include description for frontend
            })
        
        return jsonify({"models": formatted_models})
    except Exception as e:
        logger.error(f"Error fetching OpenRouter models: {e}")
        return jsonify({"error": str(e)}), 500
