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
import concurrent.futures
import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
from telethon import TelegramClient
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

# Add src to path to import from the main codebase
sys.path.insert(0, str(Path(__file__).parent / "src"))

from agent import all_agents
from media_budget import reset_description_budget
from media_source import (
    CompositeMediaSource,
    DirectoryMediaSource,
    get_default_media_source_chain,
)
from mime_utils import detect_mime_type_from_bytes
from prompt_loader import get_config_directories
from register_agents import register_all_agents
from telegram_download import download_media_bytes
from telegram_media import get_unique_id
from telegram_util import get_telegram_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global state
_available_directories: list[dict[str, str]] = []
_current_directory: Path | None = None
_telegram_client = None
_agent_for_client = None


def scan_media_directories() -> list[dict[str, str]]:
    """Scan CINDY_AGENT_CONFIG_PATH for all media directories and agents."""
    directories = []
    all_agents = set()

    # First, collect all agents from all config directories
    for config_dir in get_config_directories():
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

        # Collect all agents from this config directory
        agents_dir = config_path / "agents"
        if agents_dir.exists() and agents_dir.is_dir():
            for agent_dir in agents_dir.iterdir():
                if agent_dir.is_dir() and not agent_dir.name.startswith("."):
                    all_agents.add((agent_dir.name, config_path))

    # Also get agents from the registration system
    try:
        register_all_agents()
        registered_agents = list(all_agents())
        for agent in registered_agents:
            # Find the config directory that contains this agent
            agent_found = False
            for config_dir in get_config_directories():
                config_path = Path(config_dir)
                agent_dir = config_path / "agents" / agent.name
                if agent_dir.exists():
                    all_agents.add((agent.name, config_path))
                    agent_found = True
                    break

            # If agent not found in any config directory, use the first one
            if not agent_found and get_config_directories():
                first_config = Path(get_config_directories()[0])
                all_agents.add((agent.name, first_config))
                logger.info(
                    f"Agent {agent.name} not found in config dirs, using {first_config}"
                )
    except Exception as e:
        logger.warning(f"Failed to get registered agents: {e}")

    # Now add all agents, creating media directories in their respective config directories
    for agent_name, config_path in all_agents:
        agent_media = config_path / "agents" / agent_name / "media"
        directories.append(
            {
                "path": str(agent_media),
                "name": f"Agent: {agent_name}",
                "type": "agent",
            }
        )
        logger.info(f"Added agent: {agent_name} (config: {config_path.name})")

    # Add state/media directory for AI cache editing
    state_media_dir = Path("state/media")
    if state_media_dir.exists() and state_media_dir.is_dir():
        directories.append(
            {
                "path": str(state_media_dir),
                "name": "AI Cache (state/media)",
                "type": "cache",
            }
        )
        logger.info(f"Added AI cache directory: {state_media_dir}")
    else:
        # Add it even if it doesn't exist, so it can be created
        directories.append(
            {
                "path": str(state_media_dir),
                "name": "AI Cache (state/media)",
                "type": "cache",
            }
        )
        logger.info(f"Added AI cache directory (will be created): {state_media_dir}")

    logger.info(f"Total media directories found: {len(directories)}")
    return directories


async def get_telegram_client_for_downloads(
    target_directory: str = None,
) -> tuple[Any, Any]:
    """Get a Telegram client using the appropriate agent based on target directory."""
    global _telegram_client, _agent_for_client

    # Register all agents to get the list
    register_all_agents()
    agents = list(all_agents())  # Convert dict_values to list

    if not agents:
        raise RuntimeError("No agents found. Please configure at least one agent.")

    # If target directory is specified, try to find the matching agent
    agent = None
    if target_directory:
        target_path = Path(target_directory)
        for a in agents:
            # Check if this is an agent-specific media directory
            if f"/agents/{a.name}/media" in str(target_path):
                agent = a
                logger.info(
                    f"Found matching agent '{a.name}' for directory: {target_directory}"
                )
                break

    # Fall back to first agent if no match found
    if agent is None:
        agent = agents[0]
        logger.info(
            f"Using default agent '{agent.name}' for directory: {target_directory}"
        )

    # Use the existing authenticated client from the main agent system

    try:
        # Get the existing authenticated client for this agent
        client = get_telegram_client(agent.name, agent.phone)
        logger.info(f"Using existing authenticated client for agent '{agent.name}'")

        _telegram_client = client
        _agent_for_client = agent

        return client, agent

    except Exception as e:
        logger.error(f"Failed to get existing client for agent '{agent.name}': {e}")
        # Fall back to creating a new client (this might not work for private sticker sets)

        api_id = os.environ.get("TELEGRAM_API_ID")
        api_hash = os.environ.get("TELEGRAM_API_HASH")
        session_root = os.environ.get("CINDY_AGENT_STATE_DIR", "state")

        # Use the main agent's session file directly
        session_path = os.path.join(session_root, agent.name, "telegram.session")

        client = TelegramClient(session_path, int(api_id), api_hash)
        client.session_user_phone = agent.phone

        _telegram_client = client
        _agent_for_client = agent

        return client, agent


# Template is now in templates/media_editor.html file


@app.route("/")
def index():
    """Main page with directory selection and media browser."""
    return render_template("media_editor.html", directories=_available_directories)


@app.route("/api/directories")
def api_directories():
    """Get list of available media directories."""
    # Rescan directories to get current state
    global _available_directories
    _available_directories = scan_media_directories()
    return jsonify(_available_directories)


@app.route("/api/media")
def api_media_list():
    """Get list of media files in a directory."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = Path(directory_path)
        if not media_dir.exists():
            # Try to create the directory if it's an agent media directory
            if "/agents/" in directory_path and "/media" in directory_path:
                try:
                    media_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created missing media directory: {media_dir}")
                except Exception as e:
                    logger.error(f"Failed to create directory {media_dir}: {e}")
                    return (
                        jsonify(
                            {
                                "error": f"Directory not found and could not be created: {e}"
                            }
                        ),
                        404,
                    )
            else:
                return jsonify({"error": "Directory not found"}), 404

        media_files = []

        # Find all JSON files (descriptions)
        for json_file in media_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                unique_id = json_file.stem

                # Look for associated media file
                media_file = None
                for ext in [".webp", ".tgs", ".png", ".jpg", ".jpeg", ".gif", ".mp4"]:
                    potential_file = media_dir / f"{unique_id}{ext}"
                    if potential_file.exists():
                        media_file = str(potential_file)
                        break

                # Group by sticker set for organization
                sticker_set = data.get("sticker_set_name") or "Unknown"

                media_files.append(
                    {
                        "unique_id": unique_id,
                        "json_file": str(json_file),
                        "media_file": media_file,
                        "description": data.get("description"),
                        "kind": data.get("kind", "unknown"),
                        "sticker_set_name": sticker_set,
                        "sticker_name": data.get("sticker_name", ""),
                        "status": data.get("status", "unknown"),
                        "failure_reason": data.get("failure_reason"),
                        "mime_type": data.get("mime_type"),
                    }
                )

            except Exception as e:
                logger.error(f"Error reading {json_file}: {e}")
                continue

        # Group by sticker set
        grouped_media = {}
        for media in media_files:
            sticker_set = media["sticker_set_name"]
            if sticker_set not in grouped_media:
                grouped_media[sticker_set] = []
            grouped_media[sticker_set].append(media)

        return jsonify(
            {
                "media_files": media_files,
                "grouped_media": grouped_media,
                "directory": directory_path,
            }
        )

    except Exception as e:
        logger.error(f"Error listing media files: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/media/<unique_id>")
def api_media_file(unique_id: str):
    """Serve a media file."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = Path(directory_path)

        # Try different extensions with proper MIME types
        for ext in [".webp", ".tgs", ".png", ".jpg", ".jpeg", ".gif", ".mp4"]:
            media_file = media_dir / f"{unique_id}{ext}"
            if media_file.exists():
                # Set appropriate MIME type for TGS files
                if ext == ".tgs":
                    return send_file(media_file, mimetype="application/gzip")
                elif ext == ".webp":
                    return send_file(media_file, mimetype="image/webp")
                elif ext == ".png":
                    return send_file(media_file, mimetype="image/png")
                elif ext in [".jpg", ".jpeg"]:
                    return send_file(media_file, mimetype="image/jpeg")
                elif ext == ".gif":
                    return send_file(media_file, mimetype="image/gif")
                elif ext == ".mp4":
                    return send_file(media_file, mimetype="video/mp4")
                else:
                    return send_file(media_file)

        return jsonify({"error": "Media file not found"}), 404

    except Exception as e:
        logger.error(f"Error serving media file {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/media/<unique_id>/description", methods=["PUT"])
def api_update_description(unique_id: str):
    """Update a media description."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = Path(directory_path)
        json_file = media_dir / f"{unique_id}.json"

        if not json_file.exists():
            return jsonify({"error": "Media record not found"}), 404

        # Load existing data
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)

        # Update description
        new_description = request.json.get("description", "").strip()
        data["description"] = new_description if new_description else None

        # Clear error fields if description is provided
        if new_description:
            data.pop("failure_reason", None)
            data["status"] = "curated"  # Mark as curated when user edits description

        # Save back
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error updating description for {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/media/<unique_id>/refresh-ai", methods=["POST"])
def api_refresh_from_ai(unique_id: str):
    """Refresh description using AI pipeline."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = Path(directory_path)
        json_file = media_dir / f"{unique_id}.json"

        if not json_file.exists():
            return jsonify({"error": "Media record not found"}), 404

        # Load existing data
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)

        # Get the agent for this directory
        agent = None
        target_path = Path(directory_path)
        register_all_agents()
        agents = list(all_agents())

        for a in agents:
            # Check if this is an agent-specific media directory
            if f"/agents/{a.name}/media" in str(target_path):
                agent = a
                logger.info(
                    f"Found matching agent '{a.name}' for AI refresh: {directory_path}"
                )
                break

        # If no agent-specific directory found, use the first available agent
        # This handles cases like state/media where any agent can be used
        if not agent and agents:
            agent = agents[0]
            logger.info(
                f"Using default agent '{agent.name}' for AI refresh: {directory_path}"
            )

        if not agent:
            return jsonify({"error": "Could not determine agent for AI refresh"}), 400

        # Use the media pipeline to regenerate description
        logger.info(
            f"Refreshing AI description for {unique_id} using agent '{agent.name}'"
        )

        # For refresh, we want to bypass cached results and force fresh AI generation
        # Create a custom media source chain that excludes the state/media directory
        # Get the default chain components

        default_chain = get_default_media_source_chain()

        # Filter out the state/media directory from the chain
        filtered_sources = []
        for source in default_chain.sources:
            # Skip DirectoryMediaSource that points to state/media
            if isinstance(source, DirectoryMediaSource):
                source_path = str(source.directory)
                state_media_path = str(Path("state/media").resolve())
                # Check if this source points to state/media (handle different path formats)
                if "state/media" not in source_path and source_path != state_media_path:
                    filtered_sources.append(source)
                else:
                    logger.info(
                        f"Filtering out state/media directory source: {source_path}"
                    )
            else:
                # Keep all other sources (including AIGeneratingMediaSource)
                filtered_sources.append(source)

        # Create a new chain without state/media directory
        media_chain = CompositeMediaSource(filtered_sources)

        # Create a mock document for the media pipeline
        class MockDoc:
            def __init__(self, data):
                self.id = unique_id
                self.mime_type = data.get("mime_type")
                self.file_name = data.get("sticker_name", f"{unique_id}.sticker")

        doc = MockDoc(data)

        # Process using the media source chain to get fresh description
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            record = loop.run_until_complete(
                media_chain.get(
                    unique_id=unique_id,
                    agent=agent,
                    doc=doc,
                    kind=data.get("kind", "sticker"),
                    sticker_set_name=data.get("sticker_set_name"),
                    sticker_name=data.get("sticker_name"),
                    sender_id=None,
                    sender_name=None,
                    channel_id=None,
                    channel_name=None,
                    media_ts=None,
                )
            )
        finally:
            loop.close()

        if record:
            # Use the fresh description from the media pipeline
            new_description = record.get("description")
            new_status = record.get("status", "ok")
            logger.info(
                f"Got fresh AI description for {unique_id}: {new_description[:50] if new_description else 'None'}..."
            )
        else:
            new_description = None
            new_status = "pending_description"
            logger.warning(f"No AI description generated for {unique_id}")

        # Update the record with the new AI-generated description
        data["description"] = new_description
        data["status"] = new_status
        data.pop("failure_reason", None)  # Clear any previous errors

        # Save back
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return jsonify(
            {"success": True, "description": new_description, "status": new_status}
        )

    except Exception as e:
        logger.error(f"Error refreshing AI description for {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/media/<unique_id>/move", methods=["POST"])
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

        from_dir = Path(from_directory)
        to_dir = Path(to_directory)

        # Ensure target directory exists
        to_dir.mkdir(parents=True, exist_ok=True)

        # Find the media files
        json_file_from = from_dir / f"{unique_id}.json"

        if not json_file_from.exists():
            return jsonify({"error": "Media record not found"}), 404

        # Load the media record
        with open(json_file_from, encoding="utf-8") as f:
            media_data = json.load(f)

        # Find the media file (could be .webp, .tgs, etc.)
        media_file_from = None
        for ext in [".webp", ".tgs", ".gif", ".mp4", ".jpg", ".png"]:
            potential_file = from_dir / f"{unique_id}{ext}"
            if potential_file.exists():
                media_file_from = potential_file
                break

        # Move JSON file
        json_file_to = to_dir / f"{unique_id}.json"
        json_file_from.rename(json_file_to)

        # Move media file if it exists
        if media_file_from:
            media_file_to = to_dir / media_file_from.name
            media_file_from.rename(media_file_to)
            # Update the media_data to reflect the new file location
            media_data["media_file"] = media_file_to.name

        # Save updated media data
        with open(json_file_to, "w", encoding="utf-8") as f:
            json.dump(media_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Moved media {unique_id} from {from_directory} to {to_directory}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error moving media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/media/<unique_id>/delete", methods=["DELETE"])
def api_delete_media(unique_id: str):
    """Delete a media item and its description."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = Path(directory_path)
        json_file = media_dir / f"{unique_id}.json"

        if not json_file.exists():
            return jsonify({"error": "Media record not found"}), 404

        # Load the media record to find the media file
        with open(json_file, encoding="utf-8") as f:
            media_data = json.load(f)

        # Delete JSON file
        json_file.unlink()

        # Delete media file if it exists
        media_file_name = media_data.get("media_file")
        if media_file_name:
            media_file = media_dir / media_file_name
            if media_file.exists():
                media_file.unlink()

        logger.info(f"Deleted media {unique_id} from {directory_path}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error deleting media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/download/<unique_id>", methods=["POST"])
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


@app.route("/api/import-sticker-set", methods=["POST"])
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

        # Run the async import operation in a separate thread with its own event loop
        logger.info("Flask route: About to run async import in thread")

        def run_async_import():
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    _import_sticker_set_async(sticker_set_name, target_directory)
                )
            finally:
                loop.close()

        # Run in a separate thread to avoid event loop conflicts
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_async_import)
            result = future.result(timeout=300)  # 5 minute timeout

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
        client, agent = await get_telegram_client_for_downloads(target_directory)
        logger.info(f"Got telegram client and agent: {agent.name if agent else 'None'}")
    except Exception as e:
        logger.error(f"Failed to get telegram client: {e}")
        return {"success": False, "error": f"Failed to get telegram client: {e}"}

    # Connect to Telegram if not already connected
    if not client.is_connected():
        await client.connect()

    target_dir = Path(target_directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    imported_count = 0
    skipped_count = 0

    try:
        # Download sticker set using the same pattern as run.py
        logger.info(f"Requesting sticker set: {sticker_set_name}")

        # Try with InputStickerSetShortName first
        try:
            logger.info(
                f"Attempting to get sticker set with short name: '{sticker_set_name}'"
            )
            result = await client(
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
                    result = await client(
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

        # Reset budget for this import session (once per import, not per sticker)
        reset_description_budget(10)  # Allow 10 AI descriptions per import
        logger.info("Reset description budget to 10 for sticker import")

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
                json_file = target_dir / f"{unique_id}.json"
                if json_file.exists():
                    skipped_count += 1
                    continue

                # Download the media file
                try:
                    media_bytes = await download_media_bytes(client, doc)

                    # Determine file extension
                    mime_type = getattr(doc, "mime_type", None)
                    if mime_type in ["application/gzip", "application/x-tgsticker"]:
                        file_ext = ".tgs"
                    elif mime_type == "image/webp":
                        file_ext = ".webp"
                    elif mime_type == "image/png":
                        file_ext = ".png"
                    else:
                        file_ext = ".webp"

                    # Save media file
                    media_file = target_dir / f"{unique_id}{file_ext}"
                    media_file.write_bytes(media_bytes)

                    # Use the existing media pipeline to get description
                    logger.info(
                        f"Getting description for sticker {unique_id} using media pipeline"
                    )

                    # Get the agent's media source chain
                    media_chain = agent.get_media_source()

                    # Process using the media source chain to get description
                    record = await media_chain.get(
                        unique_id=unique_id,
                        agent=agent,
                        doc=doc,
                        kind="sticker",
                        sticker_set_name=sticker_set_name,
                        sticker_name=sticker_name,
                        sender_id=None,
                        sender_name=None,
                        channel_id=None,
                        channel_name=None,
                        media_ts=None,
                    )

                    if record:
                        # Use the description from the media pipeline
                        description = record.get("description")
                        status = record.get("status", "ok")
                        logger.info(
                            f"Got description for {unique_id}: {description[:50] if description else 'None'}..."
                        )
                    else:
                        description = None
                        status = "pending_description"
                        logger.warning(f"No description found for {unique_id}")

                    # Detect actual MIME type from file bytes for accurate processing
                    detected_mime_type = detect_mime_type_from_bytes(media_bytes)

                    # Create JSON record with description from media pipeline
                    media_record = {
                        "unique_id": unique_id,
                        "kind": "sticker",
                        "sticker_set_name": sticker_set_name,
                        "sticker_name": sticker_name,
                        "description": description,
                        "status": status,
                        "ts": datetime.now(UTC).isoformat(),
                        "mime_type": detected_mime_type,  # Use detected MIME type, not Telegram's
                    }

                    # Save JSON record
                    json_file.write_text(
                        json.dumps(media_record, indent=2), encoding="utf-8"
                    )

                    imported_count += 1

                except Exception as e:
                    logger.error(f"Failed to download sticker {unique_id}: {e}")
                    # Create error record
                    error_record = {
                        "unique_id": unique_id,
                        "kind": "sticker",
                        "sticker_set_name": sticker_set_name,
                        "sticker_name": sticker_name,
                        "description": None,
                        "status": "error",
                        "failure_reason": f"Download failed: {str(e)}",
                        "ts": datetime.now(UTC).isoformat(),
                        "mime_type": getattr(doc, "mime_type", None),
                    }
                    json_file.write_text(
                        json.dumps(error_record, indent=2), encoding="utf-8"
                    )
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
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
