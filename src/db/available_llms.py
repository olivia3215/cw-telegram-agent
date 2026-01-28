# db/available_llms.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for available LLM models.
"""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.connection import get_db_connection
from config import STATE_DIRECTORY

logger = logging.getLogger(__name__)


def _format_price(price_str: str) -> str:
    """
    Format price string from API (per token) to display as per 1M tokens.
    
    Args:
        price_str: Price per token as string (e.g., "0.0000003")
        
    Returns:
        Formatted price string per 1M tokens (e.g., "$0.30")
    """
    try:
        if not price_str or price_str == "0" or price_str == "0.0" or price_str == "0.00":
            return "$0.00"
        price = float(price_str)
        if price == 0.0:
            return "$0.00"
        # Convert from per token to per 1M tokens for display
        price_per_m = price * 1_000_000
        return f"${price_per_m:.2f}"
    except (ValueError, TypeError):
        return "$0.00"


def _parse_price_from_label(label: str) -> tuple[float, float]:
    """
    Parse prompt and completion prices from a label string.
    
    Examples:
        "gemini-3-flash-preview ($0.50 / $3.00)" -> (0.50, 3.00)
        "grok-4-1-fast-non-reasoning ($0.20 / $0.50)" -> (0.20, 0.50)
        "model-name" -> (0.0, 0.0)
    
    Args:
        label: Label string that may contain pricing in format "($X.XX / $Y.YY)"
        
    Returns:
        Tuple of (prompt_price, completion_price) per 1M tokens
    """
    # Pattern to match ($X.XX / $Y.YY) or ($X / $Y)
    price_pattern = r'\(\$([\d.]+)\s*/\s*\$([\d.]+)\)'
    match = re.search(price_pattern, label)
    if match:
        try:
            prompt_price = float(match.group(1))
            completion_price = float(match.group(2))
            return (prompt_price, completion_price)
        except (ValueError, TypeError):
            pass
    return (0.0, 0.0)


def _extract_name_from_label(label: str) -> str:
    """
    Extract model name from label, removing pricing information.
    
    Examples:
        "gemini-3-flash-preview ($0.50 / $3.00)" -> "gemini-3-flash-preview"
        "Anthropic: Claude Sonnet 4.5 ($3.00 / $15.00)" -> "Anthropic: Claude Sonnet 4.5"
        "model-name" -> "model-name"
    
    Args:
        label: Label string that may contain pricing
        
    Returns:
        Model name without pricing
    """
    # Remove pricing pattern
    name = re.sub(r'\(\$[\d.]+\s*/\s*\$[\d.]+\)', '', label).strip()
    # Remove trailing "(free)" if present
    name = re.sub(r'\s*\(free\)\s*$', '', name, flags=re.IGNORECASE).strip()
    return name


def _determine_provider(model_id: str) -> str:
    """
    Determine provider from model_id format.
    
    Args:
        model_id: Model identifier
        
    Returns:
        Provider name: "openrouter", "gemini", "grok", "openai", or "custom"
    """
    model_lower = model_id.lower()
    if "/" in model_id:
        return "openrouter"
    elif model_lower.startswith("gemini"):
        return "gemini"
    elif model_lower.startswith("grok"):
        return "grok"
    elif model_lower.startswith("gpt") or model_lower.startswith("openai"):
        return "openai"
    else:
        return "custom"


def get_all_llms() -> list[dict[str, Any]]:
    """
    Get all available LLMs from the database, ordered by display_order.
    
    Returns:
        List of LLM dictionaries with id, model_id, name, description, 
        prompt_price, completion_price, display_order, provider
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, model_id, name, description, prompt_price, completion_price,
                       display_order, provider, created_at, updated_at
                FROM available_llms
                ORDER BY display_order ASC, id ASC
            """)
            results = cursor.fetchall()
            return [dict(row) for row in results]
        finally:
            cursor.close()


def get_llm_by_id(db_id: int) -> dict[str, Any] | None:
    """
    Get a single LLM by database ID.
    
    Args:
        db_id: Database ID of the LLM
        
    Returns:
        LLM dictionary or None if not found
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, model_id, name, description, prompt_price, completion_price,
                       display_order, provider, created_at, updated_at
                FROM available_llms
                WHERE id = %s
            """, (db_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()


def get_llm_by_model_id(model_id: str) -> dict[str, Any] | None:
    """
    Get a single LLM by model_id (canonical name).
    
    Args:
        model_id: Model identifier (canonical name)
        
    Returns:
        LLM dictionary or None if not found
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, model_id, name, description, prompt_price, completion_price,
                       display_order, provider, created_at, updated_at
                FROM available_llms
                WHERE model_id = %s
            """, (model_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()


def add_llm(
    model_id: str,
    name: str,
    description: str | None,
    prompt_price: float,
    completion_price: float,
    provider: str,
    display_order: int | None = None,
) -> int:
    """
    Add a new LLM to the database.
    
    Args:
        model_id: Canonical model identifier
        name: Display name
        description: Optional description
        prompt_price: Price per 1M prompt tokens
        completion_price: Price per 1M completion tokens
        provider: Provider name (openrouter, gemini, grok, openai, custom)
        display_order: Optional display order (if None, appends to end)
        
    Returns:
        Database ID of the newly created LLM
        
    Raises:
        ValueError: If model_id already exists
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Check if model_id already exists
            existing = get_llm_by_model_id(model_id)
            if existing:
                raise ValueError(f"Model ID '{model_id}' already exists in database")
            
            # If display_order not specified, get the max and add 1
            if display_order is None:
                cursor.execute("SELECT COALESCE(MAX(display_order), -1) + 1 AS max_order FROM available_llms")
                result = cursor.fetchone()
                # DictCursor returns a dict, so access by column name
                display_order = result["max_order"] if result else 0
            
            cursor.execute("""
                INSERT INTO available_llms 
                (model_id, name, description, prompt_price, completion_price, display_order, provider)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (model_id, name, description, prompt_price, completion_price, display_order, provider))
            
            conn.commit()
            new_id = cursor.lastrowid
            if not new_id or new_id == 0:
                raise RuntimeError(f"INSERT failed: lastrowid is {new_id}. Model may not have been inserted.")
            logger.info(f"Added LLM to database: {model_id} (id={new_id})")
            return new_id
        except Exception as e:
            conn.rollback()
            error_msg = str(e) if e else "Unknown error"
            error_type = type(e).__name__
            
            # Check for PyMySQL IntegrityError (duplicate entry)
            try:
                import pymysql.err
                if isinstance(e, pymysql.err.IntegrityError):
                    # Extract the actual error message
                    if hasattr(e, 'args') and len(e.args) > 1:
                        mysql_error_msg = str(e.args[1]) if e.args[1] else error_msg
                    else:
                        mysql_error_msg = error_msg
                    if "Duplicate entry" in mysql_error_msg or "1062" in str(e.args[0] if e.args else ""):
                        raise ValueError(f"Model ID '{model_id}' already exists in database") from e
            except ImportError:
                pass  # pymysql.err not available, continue with generic handling
            
            # Check for PyMySQL-specific error attributes
            if hasattr(e, 'args') and e.args:
                error_details = f"{error_type}: {error_msg} (args: {e.args})"
            else:
                error_details = f"{error_type}: {error_msg}"
            logger.error(f"Failed to add LLM {model_id}: {error_details}", exc_info=True)
            
            # Check error message for duplicate entry even if not IntegrityError
            if "Duplicate entry" in error_msg or "1062" in error_msg or "UNIQUE constraint" in error_msg:
                raise ValueError(f"Model ID '{model_id}' already exists in database") from e
            
            raise
        finally:
            cursor.close()


def update_llm(
    db_id: int,
    model_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    prompt_price: float | None = None,
    completion_price: float | None = None,
    provider: str | None = None,
) -> None:
    """
    Update an existing LLM in the database.
    
    Args:
        db_id: Database ID of the LLM to update
        model_id: New model_id (if provided)
        name: New name (if provided)
        description: New description (if provided, use empty string to clear)
        prompt_price: New prompt price (if provided)
        completion_price: New completion price (if provided)
        provider: New provider (if provided)
        
    Raises:
        ValueError: If LLM not found or model_id conflict
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Check if LLM exists
            existing = get_llm_by_id(db_id)
            if not existing:
                raise ValueError(f"LLM with id {db_id} not found")
            
            # If model_id is being changed, check for conflicts
            if model_id is not None and model_id != existing["model_id"]:
                conflict = get_llm_by_model_id(model_id)
                if conflict and conflict["id"] != db_id:
                    raise ValueError(f"Model ID '{model_id}' already exists in database")
            
            # Build update query dynamically
            updates = []
            params = []
            
            if model_id is not None:
                updates.append("model_id = %s")
                params.append(model_id)
            if name is not None:
                updates.append("name = %s")
                params.append(name)
            if description is not None:
                updates.append("description = %s")
                params.append(description if description else None)
            if prompt_price is not None:
                updates.append("prompt_price = %s")
                params.append(prompt_price)
            if completion_price is not None:
                updates.append("completion_price = %s")
                params.append(completion_price)
            if provider is not None:
                updates.append("provider = %s")
                params.append(provider)
            
            if not updates:
                return  # Nothing to update
            
            params.append(db_id)
            query = f"UPDATE available_llms SET {', '.join(updates)} WHERE id = %s"
            cursor.execute(query, params)
            conn.commit()
            logger.info(f"Updated LLM id={db_id}")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update LLM id={db_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_llm(db_id: int) -> None:
    """
    Delete an LLM from the database.
    
    Args:
        db_id: Database ID of the LLM to delete
        
    Raises:
        ValueError: If LLM not found
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            existing = get_llm_by_id(db_id)
            if not existing:
                raise ValueError(f"LLM with id {db_id} not found")
            
            cursor.execute("DELETE FROM available_llms WHERE id = %s", (db_id,))
            conn.commit()
            logger.info(f"Deleted LLM id={db_id} (model_id={existing['model_id']})")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete LLM id={db_id}: {e}")
            raise
        finally:
            cursor.close()


def reorder_llms(order_mapping: dict[int, int]) -> None:
    """
    Update display_order for multiple LLMs.
    
    Args:
        order_mapping: Dictionary mapping database ID to new display_order
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            for db_id, new_order in order_mapping.items():
                cursor.execute(
                    "UPDATE available_llms SET display_order = %s WHERE id = %s",
                    (new_order, db_id)
                )
            conn.commit()
            logger.info(f"Reordered {len(order_mapping)} LLMs")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to reorder LLMs: {e}")
            raise
        finally:
            cursor.close()


def migrate_llm_data_to_database() -> None:
    """
    Migrate existing LLM data from hardcoded list, state file, agents, conversations, and global params.
    
    This function:
    1. Extracts hardcoded models from helpers.py
    2. Extracts models from state/openrouter_roleplay_models.json
    3. Queries all agents for their _llm_name
    4. Queries conversation_llm_overrides table
    5. Queries global parameters (DEFAULT_AGENT_LLM, MEDIA_MODEL, TRANSLATION_MODEL)
    6. Adds all unique models to the database
    
    If the table is already populated, this function will skip models that already exist.
    """
    logger.info("Starting LLM data migration to database...")
    
    # Collect all unique model IDs
    all_model_ids = set()
    model_data = {}  # model_id -> {name, description, prompt_price, completion_price, provider}
    
    # Step 1: Extract hardcoded models (from the original hardcoded list)
    try:
        hardcoded_llms = [
            # Gemini models
            {"value": "gemini-3-pro-preview", "label": "gemini-3-pro-preview ($2.00 / $12.00)", "provider": "gemini"},
            {"value": "gemini-2.5-pro", "label": "gemini-2.5-pro ($1.25 / $10.00)", "provider": "gemini"},
            {"value": "gemini-3-flash-preview", "label": "gemini-3-flash-preview ($0.50 / $3.00)", "provider": "gemini"},
            {"value": "gemini-2.5-flash-lite-preview-09-2025", "label": "gemini-2.5-flash-lite-preview-09-2025 ($0.10 / $0.40)", "provider": "gemini"},
            {"value": "gemini-2.0-flash", "label": "gemini-2.0-flash ($0.10 / $0.40)", "provider": "gemini"},
            {"value": "gemini-2.0-flash-lite", "label": "gemini-2.0-flash-lite ($0.07 / $0.30)", "provider": "gemini"},
            # Grok models
            {"value": "grok-4-1-fast-non-reasoning", "label": "grok-4-1-fast-non-reasoning ($0.20 / $0.50)", "provider": "grok"},
            {"value": "grok-4-0709", "label": "grok-4-0709 ($3.00 / $15.00)", "provider": "grok"},
            # OpenAI models
            {"value": "gpt-5.2", "label": "gpt-5.2 ($1.75 / $14.00)", "provider": "openai"},
            {"value": "gpt-5.1", "label": "gpt-5.1 ($1.50 / $10.00)", "provider": "openai"},
            {"value": "gpt-5-mini", "label": "gpt-5-mini ($0.25 / $2.00)", "provider": "openai"},
            {"value": "gpt-5-nano", "label": "gpt-5-nano ($0.05 / $0.40)", "provider": "openai"},
        ]
        
        for llm in hardcoded_llms:
            model_id = llm["value"]
            all_model_ids.add(model_id)
            prompt_price, completion_price = _parse_price_from_label(llm["label"])
            name = _extract_name_from_label(llm["label"])
            provider = llm.get("provider", _determine_provider(model_id))
            
            model_data[model_id] = {
                "name": name,
                "description": None,
                "prompt_price": prompt_price,
                "completion_price": completion_price,
                "provider": provider,
            }
        
        logger.info(f"Extracted {len(hardcoded_llms)} hardcoded models")
    except Exception as e:
        logger.warning(f"Failed to extract hardcoded models: {e}")
    
    # Step 2: Extract models from state/openrouter_roleplay_models.json
    try:
        cache_file = Path(STATE_DIRECTORY) / "openrouter_roleplay_models.json"
        if cache_file.exists():
            with cache_file.open() as f:
                cache_data = json.load(f)
                openrouter_models = cache_data.get("models", [])
                
                for llm in openrouter_models:
                    model_id = llm.get("value")
                    if not model_id:
                        continue
                    
                    all_model_ids.add(model_id)
                    prompt_price, completion_price = _parse_price_from_label(llm.get("label", ""))
                    name = _extract_name_from_label(llm.get("label", model_id))
                    provider = llm.get("provider", "openrouter")
                    
                    # Extract description if available (OpenRouter models might have more info)
                    description = None
                    if "/" in model_id:
                        # Try to extract provider name for description
                        provider_part = model_id.split("/")[0].title()
                        description = f"{provider_part} model via OpenRouter"
                    
                    model_data[model_id] = {
                        "name": name,
                        "description": description,
                        "prompt_price": prompt_price,
                        "completion_price": completion_price,
                        "provider": provider,
                    }
                
                logger.info(f"Extracted {len(openrouter_models)} models from state file")
    except Exception as e:
        logger.warning(f"Failed to extract models from state file: {e}")
    
    # Step 3: Query all agents for their _llm_name
    try:
        from agent.registry import all_agents
        agents = all_agents(include_disabled=True)
        
        for agent in agents:
            llm_name = agent._llm_name
            if llm_name:
                # Resolve provider identifiers to specific model names
                from llm.factory import resolve_llm_name_to_model
                try:
                    resolved_model = resolve_llm_name_to_model(llm_name)
                    all_model_ids.add(resolved_model)
                    
                    if resolved_model not in model_data:
                        provider = _determine_provider(resolved_model)
                        model_data[resolved_model] = {
                            "name": resolved_model,
                            "description": f"Used by agent: {agent.name}",
                            "prompt_price": 0.0,
                            "completion_price": 0.0,
                            "provider": provider,
                        }
                except Exception as e:
                    logger.debug(f"Could not resolve LLM name '{llm_name}' for agent {agent.name}: {e}")
        
        logger.info(f"Extracted LLM models from {len(agents)} agents")
    except Exception as e:
        logger.warning(f"Failed to extract models from agents: {e}")
    
    # Step 4: Query conversation_llm_overrides table
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT DISTINCT llm_model FROM conversation_llm_overrides WHERE llm_model IS NOT NULL AND llm_model != ''")
                rows = cursor.fetchall()
                for row in rows:
                    llm_model = row["llm_model"].strip()
                    if llm_model:
                        from llm.factory import resolve_llm_name_to_model
                        try:
                            resolved_model = resolve_llm_name_to_model(llm_model)
                            all_model_ids.add(resolved_model)
                            
                            if resolved_model not in model_data:
                                provider = _determine_provider(resolved_model)
                                model_data[resolved_model] = {
                                    "name": resolved_model,
                                    "description": "Used in conversation overrides",
                                    "prompt_price": 0.0,
                                    "completion_price": 0.0,
                                    "provider": provider,
                                }
                        except Exception as e:
                            logger.debug(f"Could not resolve LLM model '{llm_model}' from conversation overrides: {e}")
            finally:
                cursor.close()
        
        logger.info("Extracted LLM models from conversation overrides")
    except Exception as e:
        logger.warning(f"Failed to extract models from conversation overrides: {e}")
    
    # Step 5: Query global parameters
    try:
        from config import DEFAULT_AGENT_LLM, MEDIA_MODEL, TRANSLATION_MODEL
        from llm.factory import resolve_llm_name_to_model
        
        for param_name, param_value in [
            ("DEFAULT_AGENT_LLM", DEFAULT_AGENT_LLM),
            ("MEDIA_MODEL", MEDIA_MODEL),
            ("TRANSLATION_MODEL", TRANSLATION_MODEL),
        ]:
            if param_value:
                try:
                    resolved_model = resolve_llm_name_to_model(param_value)
                    all_model_ids.add(resolved_model)
                    
                    if resolved_model not in model_data:
                        provider = _determine_provider(resolved_model)
                        model_data[resolved_model] = {
                            "name": resolved_model,
                            "description": f"Used in global parameter: {param_name}",
                            "prompt_price": 0.0,
                            "completion_price": 0.0,
                            "provider": provider,
                        }
                except Exception as e:
                    logger.debug(f"Could not resolve LLM model '{param_value}' from {param_name}: {e}")
        
        logger.info("Extracted LLM models from global parameters")
    except Exception as e:
        logger.warning(f"Failed to extract models from global parameters: {e}")
    
    # Step 6: Add all unique models to database (skip if already exists)
    added_count = 0
    skipped_count = 0
    
    # Get existing models to avoid duplicates
    existing_models = {row["model_id"] for row in get_all_llms()}
    
    # Determine display order: hardcoded first, then OpenRouter, then discovered
    display_order = 0
    hardcoded_models = [
        "gemini-3-pro-preview", "gemini-2.5-pro", "gemini-3-flash-preview",
        "gemini-2.5-flash-lite-preview-09-2025", "gemini-2.0-flash", "gemini-2.0-flash-lite",
        "grok-4-1-fast-non-reasoning", "grok-4-0709",
        "gpt-5.2", "gpt-5.1", "gpt-5-mini", "gpt-5-nano",
    ]
    
    # Add hardcoded models first
    for model_id in hardcoded_models:
        if model_id in all_model_ids and model_id not in existing_models:
            data = model_data.get(model_id, {
                "name": model_id,
                "description": None,
                "prompt_price": 0.0,
                "completion_price": 0.0,
                "provider": _determine_provider(model_id),
            })
            try:
                add_llm(
                    model_id=model_id,
                    name=data["name"],
                    description=data["description"],
                    prompt_price=data["prompt_price"],
                    completion_price=data["completion_price"],
                    provider=data["provider"],
                    display_order=display_order,
                )
                added_count += 1
                display_order += 1
            except ValueError:
                skipped_count += 1
    
    # Add OpenRouter models (those with "/" in model_id)
    openrouter_models = [mid for mid in all_model_ids if "/" in mid and mid not in existing_models]
    for model_id in sorted(openrouter_models):
        data = model_data.get(model_id, {
            "name": model_id,
            "description": None,
            "prompt_price": 0.0,
            "completion_price": 0.0,
            "provider": "openrouter",
        })
        try:
            add_llm(
                model_id=model_id,
                name=data["name"],
                description=data["description"],
                prompt_price=data["prompt_price"],
                completion_price=data["completion_price"],
                provider=data["provider"],
                display_order=display_order,
            )
            added_count += 1
            display_order += 1
        except ValueError:
            skipped_count += 1
    
    # Add remaining discovered models
    remaining_models = [mid for mid in all_model_ids if mid not in existing_models and mid not in hardcoded_models and "/" not in mid]
    for model_id in sorted(remaining_models):
        data = model_data.get(model_id, {
            "name": model_id,
            "description": None,
            "prompt_price": 0.0,
            "completion_price": 0.0,
            "provider": _determine_provider(model_id),
        })
        try:
            add_llm(
                model_id=model_id,
                name=data["name"],
                description=data["description"],
                prompt_price=data["prompt_price"],
                completion_price=data["completion_price"],
                provider=data["provider"],
                display_order=display_order,
            )
            added_count += 1
            display_order += 1
        except ValueError:
            skipped_count += 1
    
    # Ensure at least google/gemini-3-flash-preview exists (as requested)
    if "google/gemini-3-flash-preview" not in existing_models:
        try:
            add_llm(
                model_id="google/gemini-3-flash-preview",
                name="Google: Gemini 3 Flash Preview",
                description="Google Gemini 3 Flash Preview via OpenRouter",
                prompt_price=0.50,
                completion_price=3.00,
                provider="openrouter",
                display_order=display_order,
            )
            added_count += 1
        except ValueError:
            pass  # Already exists
    
    logger.info(f"Migration complete: added {added_count} models, skipped {skipped_count} duplicates")