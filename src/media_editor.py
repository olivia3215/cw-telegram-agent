# media_editor.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Media Editor Utility for cw-telegram-agent

A standalone web interface for editing curated media descriptions.
Allows browsing, editing, and importing media files with AI-generated descriptions.

Usage:
    python media_editor.py --port 5000
"""

import argparse
import asyncio
import json
import logging
import sys
import traceback
from datetime import UTC
from pathlib import Path
from typing import Any

# Add current directory to path to import from the main codebase
sys.path.insert(0, str(Path(__file__).parent))

from flask import Blueprint, Flask, jsonify, render_template, request, send_file
from admin_console.loop import run_on_agent_loop
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

from agent import all_agents as get_all_agents
from clock import clock
from config import CONFIG_DIRECTORIES, STATE_DIRECTORY
from media.media_source import (
    AIChainMediaSource,
    AIGeneratingMediaSource,
    BudgetExhaustedMediaSource,
    CompositeMediaSource,
    MediaStatus,
    UnsupportedFormatMediaSource,
    get_emoji_unicode_name,
)
from media.media_sources import get_directory_media_source
from media.mime_utils import detect_mime_type_from_bytes, is_tgs_mime_type
from register_agents import register_all_agents
from telegram_download import download_media_bytes
from telegram_media import get_unique_id
from telegram_util import get_telegram_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def find_media_file(media_dir: Path, unique_id: str) -> Path | None:
    """Find a media file for the given unique_id in the specified directory.

    Looks for any file with the unique_id prefix that is not a .json file.

    Args:
        media_dir: Directory to search in
        unique_id: Unique identifier for the media file

    Returns:
        Path to the media file if found, None otherwise
    """
    search_dirs: list[Path] = [media_dir]

    # Fallback to AI cache directory if media not present in curated directory
    if STATE_DIRECTORY:
        fallback_dir = Path(STATE_DIRECTORY) / "media"
        if fallback_dir != media_dir:
            search_dirs.append(fallback_dir)

    for directory in search_dirs:
        for file_path in directory.glob(f"{unique_id}.*"):
            if file_path.suffix.lower() != ".json":
                if directory != media_dir:
                    logger.debug(
                        "find_media_file: using fallback media directory %s for %s",
                        directory,
                        unique_id,
                    )
                return file_path

    return None


bp = Blueprint(
    "admin_console",
    __name__,
    template_folder=str(Path(__file__).parent.parent / "templates"),
)

# Global state
_available_directories: list[dict[str, str]] = []
_current_directory: Path | None = None


def resolve_media_path(directory_path: str) -> Path:
    """Resolve a media directory path relative to the project root."""
    # If it's an absolute path, use it as-is
    if Path(directory_path).is_absolute():
        return Path(directory_path)

    # For relative paths, resolve relative to the project root (parent of src/)
    project_root = Path(__file__).parent.parent
    resolved_path = project_root / directory_path
    # Ensure absolute path
    return resolved_path.resolve()


def scan_media_directories() -> list[dict[str, str]]:
    """Scan CINDY_AGENT_CONFIG_PATH for all media directories and agents."""
    directories = []

    # First, collect global media directories from config directories
    for config_dir in CONFIG_DIRECTORIES:
        config_path = Path(config_dir)
        if not config_path.exists():
            logger.warning(f"Config directory does not exist: {config_dir}")
            continue

        logger.info(f"Scanning config directory: {config_path}")

        # Global media directory
        global_media = config_path / "media"
        if global_media.exists() and global_media.is_dir():
            directories.append(
                {
                    "path": str(global_media),
                    "name": f"Global Media ({config_path.name})",
                    "type": "global",
                }
            )
            logger.info(f"Found global media directory: {global_media}")

    # Add AI cache directory from CINDY_AGENT_STATE_DIR
    state_dir = STATE_DIRECTORY
    if state_dir:
        state_media_dir = Path(state_dir) / "media"
        directories.append(
            {
                "path": str(state_media_dir.resolve()),
                "name": f"AI Cache ({state_media_dir.name})",
                "type": "cache",
            }
        )
        logger.info(f"Added AI cache directory: {state_media_dir}")
    else:
        logger.warning("CINDY_AGENT_STATE_DIR not set, skipping AI cache directory")

    logger.info(f"Total media directories found: {len(directories)}")
    return directories


def get_agent_for_directory(target_directory: str = None) -> Any:
    """Get an agent for the specified directory (always returns the first agent)."""
    # Register all agents to get the list
    register_all_agents()
    agents = list(get_all_agents())

    if not agents:
        raise RuntimeError("No agents found. Please configure at least one agent.")

    # Return the first agent (agent-specific media directories no longer exist)
    agent = agents[0]
    logger.info(f"Using agent '{agent.name}' for directory: {target_directory}")

    # Note: Client initialization is handled in the AI refresh function
    # where we have proper async context

    return agent


@bp.route("/")
def index():
    """Main page with directory selection and media browser."""
    return render_template("admin_console.html", directories=_available_directories)


@bp.route("/favicon.ico")
def favicon():
    """Serve the favicon."""
    favicon_path = Path(__file__).parent.parent / "favicon.ico"
    if not favicon_path.exists():
        return jsonify({"error": "Favicon not found"}), 404
    return send_file(favicon_path, mimetype="image/x-icon")


@bp.route("/api/directories")
def api_directories():
    """Get list of available media directories."""
    # Rescan directories to get current state
    global _available_directories
    _available_directories = scan_media_directories()
    return jsonify(_available_directories)


@bp.route("/api/media")
def api_media_list():
    """Get list of media files in a directory."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        if not media_dir.exists():
            return jsonify({"error": "Directory not found"}), 404

        # Use MediaSource API to read media descriptions
        # Create a chain with DirectoryMediaSource and UnsupportedFormatMediaSource
        # but without AIGeneratingMediaSource (no AI generation in listing)
        cache_source = get_directory_media_source(media_dir)
        unsupported_source = UnsupportedFormatMediaSource()

        media_chain = CompositeMediaSource(
            [
                cache_source,
                unsupported_source,
            ]
        )

        media_files = []

        # Find all JSON files to get unique IDs
        for json_file in media_dir.glob("*.json"):
            try:
                unique_id = json_file.stem

                # Use MediaSource chain to get the record (applies all transformations)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    record = loop.run_until_complete(
                        media_chain.get(unique_id=unique_id)
                    )
                finally:
                    loop.close()

                if not record:
                    logger.warning(f"No record found for {unique_id}")
                    continue

                # Look for associated media file
                media_file_path = find_media_file(media_dir, unique_id)
                media_file = str(media_file_path) if media_file_path else None

                # Group by sticker set for organization
                kind = record.get("kind", "unknown")
                if kind == "sticker":
                    sticker_set = record.get("sticker_set_name") or "Other Media"
                else:
                    sticker_set = "Other Media"

                # Add emoji description for sticker names
                sticker_name = record.get("sticker_name", "")
                emoji_description = ""
                if sticker_name and kind == "sticker":
                    try:
                        emoji_description = get_emoji_unicode_name(sticker_name)
                    except Exception:
                        emoji_description = ""

                media_files.append(
                    {
                        "unique_id": unique_id,
                        "json_file": str(json_file),
                        "media_file": media_file,
                        "description": record.get("description"),
                        "kind": kind,
                        "sticker_set_name": sticker_set,
                        "sticker_name": sticker_name,
                        "emoji_description": emoji_description,
                        "status": record.get("status", "unknown"),
                        "failure_reason": record.get("failure_reason"),
                        "mime_type": record.get("mime_type"),
                    }
                )

            except Exception as e:
                logger.error(f"Error processing {json_file}: {e}")
                continue

        # Group by sticker set
        grouped_media = {}
        for media in media_files:
            sticker_set = media["sticker_set_name"]
            if sticker_set not in grouped_media:
                grouped_media[sticker_set] = []
            grouped_media[sticker_set].append(media)

        response = jsonify(
            {
                "media_files": media_files,
                "grouped_media": grouped_media,
                "directory": directory_path,
            }
        )
        # Add cache-busting headers to ensure fresh data
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    except Exception as e:
        logger.error(f"Error listing media files: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/media/<unique_id>")
def api_media_file(unique_id: str):
    """Serve a media file."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)

        # Find the media file
        media_file = find_media_file(media_dir, unique_id)
        if media_file:
            # Use MIME sniffing to detect the correct MIME type
            try:
                with open(media_file, "rb") as f:
                    file_bytes = f.read(1024)  # Read first 1KB for MIME detection
                detected_mime_type = detect_mime_type_from_bytes(file_bytes)
                return send_file(media_file, mimetype=detected_mime_type)
            except Exception as e:
                logger.warning(
                    f"Failed to detect MIME type for {media_file}, falling back to default: {e}"
                )
                return send_file(media_file)

        return jsonify({"error": "Media file not found"}), 404

    except Exception as e:
        logger.error(f"Error serving media file {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/media/<unique_id>/description", methods=["PUT"])
def api_update_description(unique_id: str):
    """Update a media description."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        source = get_directory_media_source(media_dir)
        record = source.get_cached_record(unique_id)

        if not record:
            return jsonify({"error": "Media record not found"}), 404

        # Update description
        new_description = request.json.get("description", "").strip()
        record["description"] = new_description if new_description else None

        # Clear error fields if description is provided
        if new_description:
            record.pop("failure_reason", None)
            record["status"] = "curated"  # Mark as curated when user edits description

        source.put(unique_id, record)

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error updating description for {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/media/<unique_id>/refresh-ai", methods=["POST"])
def api_refresh_from_ai(unique_id: str):
    """Refresh description using AI pipeline."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        media_cache_source = get_directory_media_source(media_dir)
        data = media_cache_source.get_cached_record(unique_id)

        if not data:
            return jsonify({"error": "Media record not found"}), 404

        # Force the AI pipeline to regenerate a fresh description.
        logger.debug(
            "Refresh-from-AI: clearing cached description for %s in %s",
            unique_id,
            media_dir,
        )
        data["description"] = None
        data.pop("failure_reason", None)
        data["status"] = MediaStatus.TEMPORARY_FAILURE.value
        media_cache_source.put(unique_id, data)

        # Get the agent for this directory
        try:
            agent = get_agent_for_directory(directory_path)
        except Exception as e:
            logger.error(f"Failed to get agent for directory {directory_path}: {e}")
            return jsonify({"error": f"Could not determine agent: {e}"}), 400

        # Use the media pipeline to regenerate description
        logger.info(
            "Refreshing AI description for %s using agent '%s'",
            unique_id,
            agent.name,
        )

        # For refresh, we want to bypass cached results and force fresh AI generation
        # Create a minimal chain with just the AI generation source

        # Find the media file
        media_file = find_media_file(media_dir, unique_id)

        if not media_file:
            return jsonify({"error": "Media file not found"}), 404

        # Pass the media file Path directly as the doc parameter
        # download_media_bytes supports Path objects directly
        fake_doc = media_file

        # For refresh, use the full AI chain to ensure media files are downloaded
        # Create a proper chain with AIChainMediaSource that handles downloads
        ai_cache_dir = media_dir
        ai_cache_dir.mkdir(parents=True, exist_ok=True)

        # Create the same chain structure as the main application
        ai_cache_source = get_directory_media_source(ai_cache_dir)
        unsupported_source = UnsupportedFormatMediaSource()
        budget_source = BudgetExhaustedMediaSource()
        ai_source = AIGeneratingMediaSource(cache_directory=ai_cache_dir)

        media_chain = CompositeMediaSource(
            [
                AIChainMediaSource(
                    cache_source=ai_cache_source,
                    unsupported_source=unsupported_source,
                    budget_source=budget_source,
                    ai_source=ai_source,
                )
            ]
        )

        # Determine the correct media kind based on MIME type
        # This fixes the issue where cached records have wrong kind for animated stickers
        mime_type = data.get("mime_type")
        if is_tgs_mime_type(mime_type):
            media_kind = "animated_sticker"
        else:
            media_kind = data.get("kind", "sticker")

        # Initialize the agent's client before using it
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:

            # Ensure the agent's client is connected
            if agent.client is None:
                # Initialize and start the client
                client = get_telegram_client(agent.name, agent.phone)
                agent._client = client

                # Start the client if not already connected
                if not client.is_connected():
                    loop.run_until_complete(client.connect())

                # Check if authenticated
                if not loop.run_until_complete(client.is_user_authorized()):
                    raise RuntimeError(
                        f"Agent '{agent.name}' is not authenticated to Telegram. Please run './telegram_login.sh' to authenticate this agent."
                    )

            # For media editor, we don't have a real Telegram document
            # download_media_bytes supports Path objects directly
            record = loop.run_until_complete(
                media_chain.get(
                    unique_id=unique_id,
                    agent=agent,
                    doc=fake_doc,  # Path object - download_media_bytes handles this
                    kind=media_kind,
                    sticker_set_name=data.get("sticker_set_name"),
                    sticker_name=data.get("sticker_name"),
                    sender_id=None,
                    sender_name=None,
                    channel_id=None,
                    channel_name=None,
                    media_ts=None,
                    duration=data.get(
                        "duration"
                    ),  # Include duration for video/animated stickers
                    skip_fallback=True,
                )
            )
        finally:
            loop.close()

        if record:
            # AIChainMediaSource has already cached the result to disk
            new_description = record.get("description")
            new_status = record.get("status", "ok")
            logger.info(
                "Got fresh AI description for %s (status=%s): %s",
                unique_id,
                new_status,
                (new_description[:50] + "…") if new_description else "None",
            )
            logger.debug(
                "Refresh-from-AI: updated metadata for %s -> %s",
                unique_id,
                record,
            )
            return jsonify(
                {"success": True, "description": new_description, "status": new_status}
            )
        else:
            logger.warning(f"No AI description generated for {unique_id}")
            return jsonify({"error": "No AI description generated"}), 500

    except Exception as e:
        logger.error(f"Error refreshing AI description for {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/media/<unique_id>/move", methods=["POST"])
def api_move_media(unique_id: str):
    """Move a media item from one directory to another."""
    try:
        from_directory = request.args.get("from_directory")
        to_directory = request.args.get("to_directory")

        if not from_directory or not to_directory:
            return (
                jsonify({"error": "Missing from_directory or to_directory parameter"}),
                400,
            )

        from_dir = resolve_media_path(from_directory)
        to_dir = resolve_media_path(to_directory)

        from_source = get_directory_media_source(from_dir)
        to_source = get_directory_media_source(to_dir)

        try:
            from_source.move_record_to(unique_id, to_source)
        except KeyError:
            return jsonify({"error": "Media record not found"}), 404

        logger.info(f"Moved media {unique_id} from {from_directory} to {to_directory}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error moving media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/media/<unique_id>/delete", methods=["DELETE"])
def api_delete_media(unique_id: str):
    """Delete a media item and its description."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        source = get_directory_media_source(media_dir)

        record = source.get_cached_record(unique_id)
        if not record:
            return jsonify({"error": "Media record not found"}), 404

        source.delete_record(unique_id)

        logger.info(f"Deleted media {unique_id} from {directory_path}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error deleting media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/download/<unique_id>", methods=["POST"])
def api_download_media(unique_id: str):
    """Download missing media file using Telegram API."""
    try:
        # For now, this is a placeholder since we need the original document reference
        # to download by unique_id. This would require a more complex lookup system.
        return (
            jsonify(
                {
                    "error": "Download by unique_id not yet implemented. Use sticker set import instead."
                }
            ),
            501,
        )

    except Exception as e:
        logger.error(f"Error downloading media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/import-sticker-set", methods=["POST"])
def api_import_sticker_set():
    """Import all stickers from a sticker set."""
    try:
        data = request.json
        sticker_set_name = data.get("sticker_set_name")
        target_directory = data.get("target_directory")

        logger.info(
            f"Flask route: Starting import for {sticker_set_name} to {target_directory}"
        )

        if not sticker_set_name or not target_directory:
            return (
                jsonify({"error": "Missing sticker_set_name or target_directory"}),
                400,
            )

        logger.info("Flask route: Scheduling import on agent loop")
        import_coro = _import_sticker_set_async(
            sticker_set_name, target_directory
        )
        try:
            result = run_on_agent_loop(
                import_coro,
                timeout=300,
            )
        except RuntimeError as exc:
            import_coro.close()
            logger.error(
                "Agent loop not available for sticker import: %s", exc, exc_info=True
            )
            return (
                jsonify(
                    {
                        "error": "Agent loop is not running; cannot import sticker set."
                    }
                ),
                503,
            )

        logger.info("Flask route: async import completed successfully")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error importing sticker set: {e}")
        logger.error(f"Exception type: {type(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


async def _import_sticker_set_async(sticker_set_name: str, target_directory: str):
    """Async implementation of sticker set import."""
    logger.info(f"Starting sticker import for set: {sticker_set_name}")
    logger.info(f"Target directory: {target_directory}")
    try:
        agent = get_agent_for_directory(target_directory)
        client = await agent.get_client()

        # Check if the client is authenticated before proceeding
        if not await client.is_user_authorized():
            logger.error(f"Agent '{agent.name}' is not authenticated to Telegram.")
            return {
                "success": False,
                "error": f"Agent '{agent.name}' is not authenticated to Telegram. Please run './telegram_login.sh' to authenticate this agent.",
            }

        logger.info(f"Got authenticated agent: {agent.name}")

    except Exception as e:
        logger.error(f"Failed to get agent or connect client: {e}")
        return {
            "success": False,
            "error": f"Failed to get agent or connect client: {e}",
        }

    target_dir = Path(target_directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    imported_count = 0
    skipped_count = 0

    # Create a single DirectoryMediaSource instance outside the loop to enable in-memory caching
    cache_source = get_directory_media_source(target_dir)

    try:
        # Download sticker set using the same pattern as run.py
        logger.info(f"Requesting sticker set: {sticker_set_name}")

        # Try with InputStickerSetShortName first
        try:
            logger.info(
                f"Attempting to get sticker set with short name: '{sticker_set_name}'"
            )
            result = await agent.client(
                GetStickerSetRequest(
                    stickerset=InputStickerSetShortName(short_name=sticker_set_name),
                    hash=0,
                )
            )
            logger.info(
                f"Successfully got sticker set result with short name: {type(result)}"
            )
        except Exception as e:
            logger.warning(
                f"Failed to get sticker set with short name '{sticker_set_name}': {e}"
            )
            logger.warning(f"Exception type: {type(e)}")

            # Try case variations for debugging
            if sticker_set_name != sticker_set_name.lower():
                logger.info(f"Trying lowercase version: '{sticker_set_name.lower()}'")
                try:
                    result = await agent.client(
                        GetStickerSetRequest(
                            stickerset=InputStickerSetShortName(
                                short_name=sticker_set_name.lower()
                            ),
                            hash=0,
                        )
                    )
                    logger.info(
                        f"Successfully got sticker set result with lowercase name: {type(result)}"
                    )
                except Exception as e2:
                    logger.warning(f"Lowercase version also failed: {e2}")

            # Re-raise with more context but avoid double-wrapping
            error_msg = str(e)
            if "not registered in the system" in error_msg:
                raise Exception(
                    f"Sticker set '{sticker_set_name}' appears to be private or restricted. "
                    f"Private sticker sets cannot be imported via the API. "
                    f"If you have access to this sticker set, you may need to add the stickers manually "
                    f"or use a different import method. Original error: {error_msg}"
                ) from e
            else:
                raise Exception(
                    f"Sticker set '{sticker_set_name}' not found or not accessible. "
                    f"Make sure the sticker set exists and is public. "
                    f"Original error: {error_msg}"
                ) from e

        # Process each document using the same pattern as run.py
        # Convert result.documents to list to handle dict_values case
        logger.info(f"result.documents type: {type(result.documents)}")
        try:
            documents = list(result.documents)
            logger.info(
                f"Successfully converted to list with {len(documents)} documents"
            )
        except Exception as e:
            logger.error(f"Failed to convert result.documents to list: {e}")
            return {
                "success": False,
                "error": f"Failed to process sticker set documents: {e}",
            }

        for doc in documents:
            try:
                # Get sticker name using the same pattern as run.py
                sticker_name = next(
                    (a.alt for a in doc.attributes if hasattr(a, "alt")),
                    f"sticker_{imported_count + 1}",
                )

                # Get unique ID and other metadata
                unique_id = get_unique_id(doc)

                if not unique_id:
                    logger.warning(
                        f"No unique ID found for sticker in set {sticker_set_name}"
                    )
                    continue

                # Check if already exists
                existing_file = target_dir / f"{unique_id}.json"
                if existing_file.exists():
                    skipped_count += 1
                    continue

                # Download the media file
                try:
                    media_bytes = await download_media_bytes(agent.client, doc)

                    # Determine file extension
                    mime_type = getattr(doc, "mime_type", None)
                    if is_tgs_mime_type(mime_type):
                        file_ext = ".tgs"
                    elif mime_type == "image/webp":
                        file_ext = ".webp"
                    elif mime_type == "image/png":
                        file_ext = ".png"
                    else:
                        file_ext = ".webp"

                    # Detect actual MIME type from file bytes for accurate processing
                    detected_mime_type = detect_mime_type_from_bytes(media_bytes)

                    # All stickers use kind="sticker" (MIME type distinguishes static vs animated)
                    media_kind = "sticker"

                    # Create JSON record with empty description (AI will be used on-demand via refresh)
                    media_record = {
                        "unique_id": unique_id,
                        "kind": media_kind,
                        "sticker_set_name": sticker_set_name,
                        "sticker_name": sticker_name,
                        "description": None,  # Leave empty, use AI refresh when needed
                        "status": "pending_description",
                        "ts": clock.now(UTC).isoformat(),
                        "mime_type": detected_mime_type,  # Use detected MIME type, not Telegram's
                    }

                    # Save both media file and JSON record using DirectoryMediaSource
                    cache_source.put(unique_id, media_record, media_bytes, file_ext)

                    imported_count += 1

                except Exception as e:
                    logger.error(f"Failed to download sticker {unique_id}: {e}")
                    # Determine correct kind for error record
                    telegram_mime = getattr(doc, "mime_type", None)
                    # All stickers use kind="sticker" (MIME type distinguishes static vs animated)
                    error_kind = "sticker"

                    # Create error record
                    error_record = {
                        "unique_id": unique_id,
                        "kind": error_kind,
                        "sticker_set_name": sticker_set_name,
                        "sticker_name": sticker_name,
                        "description": None,
                        "status": "error",
                        "failure_reason": f"Download failed: {str(e)}",
                        "ts": clock.now(UTC).isoformat(),
                        "mime_type": telegram_mime,
                    }
                    # Save error record using DirectoryMediaSource (no media file for errors)
                    cache_source.put(unique_id, error_record, None, None)
                    imported_count += 1

            except Exception as e:
                logger.error(f"Error processing sticker in set {sticker_set_name}: {e}")
                continue

    except Exception as e:
        logger.error(f"Error importing sticker set {sticker_set_name}: {e}")
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "message": f"Imported {imported_count} stickers, skipped {skipped_count} existing ones",
    }


def create_admin_app() -> Flask:
    """Create and configure the admin console Flask application."""
    app = Flask(
        __name__, template_folder=str(Path(__file__).parent.parent / "templates")
    )
    app.register_blueprint(bp, url_prefix="/admin")
    return app


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Media Editor Utility for cw-telegram-agent"
    )
    parser.add_argument(
        "--port", type=int, default=5001, help="Port to run the web server on"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 for network access)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()

    # Scan for media directories
    global _available_directories
    _available_directories = scan_media_directories()

    if not _available_directories:
        logger.error("No media directories found. Check your CINDY_AGENT_CONFIG_PATH.")
        sys.exit(1)

    logger.info(f"Found {len(_available_directories)} media directories:")
    for dir_info in _available_directories:
        logger.info(f"  - {dir_info['name']}: {dir_info['path']}")

    # Templates are now in templates/media_editor.html file
    # create_templates()

    # Start the web server
    logger.info(f"Starting Media Editor on http://{args.host}:{args.port}")
    create_admin_app().run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
