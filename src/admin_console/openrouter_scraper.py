# admin_console/openrouter_scraper.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Fetcher for OpenRouter roleplay models from the API.

Fetches roleplay models from https://openrouter.ai/api/v1/models?category=roleplay
with pricing information. Optionally uses rankings page to get popularity order.
"""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx  # pyright: ignore[reportMissingImports]

from config import OPENROUTER_API_KEY, STATE_DIRECTORY

logger = logging.getLogger(__name__)

# Cache file path
CACHE_FILE = Path(STATE_DIRECTORY) / "openrouter_roleplay_models.json"
CACHE_TTL_HOURS = 24  # Cache for 24 hours

# API endpoint for roleplay models
MODELS_API_URL = "https://openrouter.ai/api/v1/models?category=roleplay"
# Rankings URL for getting popularity order (optional)
RANKINGS_URL = "https://openrouter.ai/rankings?category=roleplay"


def _format_price(price_str: str) -> str:
    """
    Format price string from API (per token) to display format (per 1M tokens).
    
    The OpenRouter API returns prices per token (e.g., "0.0000003" per token).
    We convert to per 1M tokens for display: multiply by 1,000,000.
    
    Args:
        price_str: Price as string per token (e.g., "0.0000003")
        
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
        # API gives us per token, so multiply by 1,000,000 to get per 1M tokens
        price_per_m = price * 1_000_000
        return f"${price_per_m:.2f}"
    except (ValueError, TypeError):
        return "$0.00"


async def _fetch_rankings_page_with_playwright() -> str:
    """
    Fetch the rankings page using Playwright to handle JavaScript rendering.
    
    Returns:
        HTML content of the page
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is required for scraping OpenRouter models. "
            "Install with: pip install playwright && playwright install chromium"
        )
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            
            # Navigate to the rankings page
            # Use "domcontentloaded" instead of "networkidle" for more reliable loading
            # "networkidle" can timeout if the page has continuous activity
            await page.goto(RANKINGS_URL, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for dynamic content to load (rankings may be loaded via JS)
            await asyncio.sleep(3)
            
            # Try to wait for the rankings list to be visible
            try:
                await page.wait_for_selector("a[href*='/']", timeout=10000)
            except Exception:
                # If selector doesn't appear, continue anyway - we'll parse what we have
                pass
            
            # Get the page content
            content = await page.content()
            
            await browser.close()
            return content
    except Exception as e:
        logger.error(f"Error fetching rankings page with Playwright: {e}")
        raise


def _parse_models_from_html(html: str) -> list[dict[str, Any]]:
    """
    Parse model information from the rankings page HTML.
    
    The rankings page shows models in a numbered list format like:
    1. <a href="/anthropic/claude-4.5-sonnet-20250929">Claude Sonnet 4.5</a>
    
    Args:
        html: HTML content of the rankings page
        
    Returns:
        List of model dictionaries with 'name', 'link', and 'model_id' fields
        Ordered by popularity (as they appear on the page)
    """
    models = []
    
    # Pattern to match model links in the rankings list
    # Looking for numbered list items with model links
    # Format: <a href="/provider/model-slug">Model Name</a>
    # The rankings page uses specific structure, so we look for links within list items
    link_pattern = r'<a[^>]+href="/([^"]+)"[^>]*>([^<]+)</a>'
    
    # Find all model links
    matches = re.finditer(link_pattern, html, re.IGNORECASE)
    
    seen_models = set()
    for match in matches:
        slug = match.group(1)
        name = match.group(2).strip()
        
        # Skip if we've seen this model already
        if slug in seen_models:
            continue
        
        # Skip non-model links (like navigation, docs, etc.)
        # Model links typically have provider/model format and don't contain certain keywords
        skip_keywords = ["docs", "api", "models?", "rankings", "chat", "pricing", "enterprise", "about", "apps"]
        if any(keyword in slug.lower() for keyword in skip_keywords):
            continue
        
        # Model links should have provider/model format (contain "/")
        if "/" not in slug or slug.startswith("http"):
            continue
        
        # Skip if it looks like a navigation or category link
        if slug.count("/") > 2:  # Too many slashes, probably not a model
            continue
        
        # Skip links that are clearly not models (like provider pages)
        # Provider pages are usually just "provider" without a model name
        if not any(char.isdigit() or char == "-" for char in slug.split("/")[-1]):
            # Model slugs typically have version numbers or dates in them
            continue
        
        seen_models.add(slug)
        
        # Use the slug as model ID - OpenRouter API accepts slugs
        # Slugs are like "anthropic/claude-4.5-sonnet-20250929"
        # We'll use the canonical slug from the API response when fetching pricing
        model_id = slug
        
        models.append({
            "model_id": model_id,
            "name": name,
            "slug": slug,
        })
    
    return models


async def _get_popularity_order() -> list[str]:
    """
    Get model popularity order from rankings page (optional).
    
    Returns:
        List of model IDs/slugs in popularity order
    """
    try:
        html = await _fetch_rankings_page_with_playwright()
        models = _parse_models_from_html(html)
        # Return list of model IDs in order
        return [m["model_id"] for m in models]
    except Exception as e:
        logger.warning(f"Could not fetch popularity order from rankings page: {e}")
        return []


async def scrape_roleplay_models() -> list[dict[str, Any]]:
    """
    Fetch roleplay models from OpenRouter API.
    
    Uses the JSON API to get reliable model data with pricing.
    Optionally uses rankings page to get popularity order.
    
    Returns:
        List of model dictionaries with 'value', 'label', and 'provider' fields
        suitable for use in get_available_llms()
        Models are ordered by popularity if rankings data is available, otherwise by name
    """
    logger.info("Fetching OpenRouter roleplay models from API...")
    
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not set, cannot fetch models")
        return []
    
    try:
        # Fetch models from API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                MODELS_API_URL,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            )
            response.raise_for_status()
            data = response.json()
        
        api_models = data.get("data", [])
        if not api_models:
            logger.warning("No models returned from OpenRouter API")
            return []
        
        logger.info(f"Fetched {len(api_models)} roleplay models from API")
        
        # Optionally get popularity order from rankings page
        popularity_order = await _get_popularity_order()
        popularity_index = {model_id: idx for idx, model_id in enumerate(popularity_order)} if popularity_order else {}
        
        # Build result list from API data
        result = []
        for model in api_models:
            model_id = model.get("id")
            name = model.get("name", model_id)
            pricing = model.get("pricing", {})
            
            prompt_price_str = pricing.get("prompt", "0")
            completion_price_str = pricing.get("completion", "0")
            
            # Format label with pricing
            try:
                prompt_price = float(prompt_price_str) if prompt_price_str else 0.0
                completion_price = float(completion_price_str) if completion_price_str else 0.0
                
                if prompt_price == 0.0 and completion_price == 0.0:
                    # Check if this is explicitly a free model
                    if ":free" in model_id.lower() or "free" in name.lower():
                        label = f"{name} (free)"
                    else:
                        # Zero pricing - show without price
                        label = name
                else:
                    prompt_price_formatted = _format_price(prompt_price_str)
                    completion_price_formatted = _format_price(completion_price_str)
                    label = f"{name} ({prompt_price_formatted} / {completion_price_formatted})"
            except (ValueError, TypeError):
                logger.warning(f"Invalid pricing format for model {model_id}")
                label = name
            
            # Get popularity rank if available
            popularity_rank = popularity_index.get(model_id, len(api_models))
            
            result.append({
                "value": model_id,
                "label": label,
                "provider": "openrouter",
                "_popularity_rank": popularity_rank,  # For sorting
            })
        
        # Sort by popularity rank if available, otherwise by name
        result.sort(key=lambda x: (x["_popularity_rank"], x["label"]))
        
        # Remove internal sorting field
        for item in result:
            item.pop("_popularity_rank", None)
        
        logger.info(f"Successfully fetched {len(result)} roleplay models with pricing")
        return result
        
    except Exception as e:
        logger.error(f"Error fetching OpenRouter models from API: {e}")
        raise


def load_cached_models() -> list[dict[str, Any]] | None:
    """
    Load cached models from disk.
    
    Returns:
        List of models if cache is valid, None otherwise
    """
    if not CACHE_FILE.exists():
        return None
    
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Check if cache is still valid
        cached_time = datetime.fromisoformat(data.get("cached_at", ""))
        if datetime.now(UTC) - cached_time.replace(tzinfo=UTC) > timedelta(hours=CACHE_TTL_HOURS):
            logger.debug("OpenRouter models cache expired")
            return None
        
        return data.get("models", [])
    except Exception as e:
        logger.warning(f"Error loading cached models: {e}")
        return None


def save_cached_models(models: list[dict[str, Any]]) -> None:
    """
    Save models to cache file.
    
    Args:
        models: List of model dictionaries to cache
    """
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cached_at": datetime.now(UTC).isoformat(),
            "models": models,
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Cached {len(models)} OpenRouter models to {CACHE_FILE}")
    except Exception as e:
        logger.error(f"Error saving cached models: {e}")


async def get_roleplay_models(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    Get roleplay models, using cache if available and valid.
    
    Args:
        force_refresh: If True, force a fresh scrape even if cache is valid
        
    Returns:
        List of model dictionaries
    """
    if not force_refresh:
        cached = load_cached_models()
        if cached is not None:
            logger.debug(f"Using cached OpenRouter models ({len(cached)} models)")
            return cached
    
    # Scrape fresh models
    models = await scrape_roleplay_models()
    
    # Save to cache
    if models:
        save_cached_models(models)
    
    return models
