#!/usr/bin/env python3
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
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file

# Add src to path to import from the main codebase
sys.path.insert(0, str(Path(__file__).parent / "src"))

from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

from agent import all_agents
from prompt_loader import get_config_directories
from register_agents import register_all_agents
from telegram_download import download_media_bytes
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
    """Scan CINDY_AGENT_CONFIG_PATH for all media directories."""
    directories = []

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

        # Agent-specific media directories - include ALL agents, even without media dirs
        agents_dir = config_path / "agents"
        if agents_dir.exists() and agents_dir.is_dir():
            logger.info(f"Scanning agents directory: {agents_dir}")
            for agent_dir in agents_dir.iterdir():
                if agent_dir.is_dir() and not agent_dir.name.startswith("."):
                    agent_media = agent_dir / "media"
                    if agent_media.exists() and agent_media.is_dir():
                        directories.append(
                            {
                                "path": str(agent_media),
                                "name": f"Agent: {agent_dir.name}",
                                "type": "agent",
                            }
                        )
                        logger.info(f"Found agent media directory: {agent_media}")
                    else:
                        # Include agent even if no media directory exists
                        directories.append(
                            {
                                "path": str(agent_media),
                                "name": f"Agent: {agent_dir.name}",
                                "type": "agent",
                            }
                        )
                        logger.info(
                            f"Found agent without media directory: {agent_dir.name}"
                        )
        else:
            logger.debug(f"No agents directory found in {config_path}")

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
        import os

        from telethon import TelegramClient

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


@app.route("/")
def index():
    """Main page with directory selection and media browser."""
    return render_template("index.html", directories=_available_directories)


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
                sticker_set = data.get("sticker_set_name", "Unknown")

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
            data["status"] = "ok"

        # Save back
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error updating description for {unique_id}: {e}")
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

        import concurrent.futures

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
        import traceback

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

        for doc in documents:
            try:
                # Get sticker name using the same pattern as run.py
                sticker_name = next(
                    (a.alt for a in doc.attributes if hasattr(a, "alt")),
                    f"sticker_{imported_count + 1}",
                )

                # Get unique ID and other metadata
                from telegram_media import get_unique_id

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
                    if mime_type == "application/gzip":
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

                    # Create JSON record with description from media pipeline
                    media_record = {
                        "unique_id": unique_id,
                        "kind": "sticker",
                        "sticker_set_name": sticker_set_name,
                        "sticker_name": sticker_name,
                        "description": description,
                        "status": status,
                        "ts": "2025-01-27T00:00:00+00:00",
                        "mime_type": mime_type,
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
                        "ts": "2025-01-27T00:00:00+00:00",
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


def create_templates():
    """Create HTML templates for the web interface."""
    templates_dir = Path("templates")
    templates_dir.mkdir(exist_ok=True)

    # Create index.html
    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Media Editor</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .header {
            background: #2c3e50;
            color: white;
            padding: 20px;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
        }
        .controls {
            padding: 20px;
            border-bottom: 1px solid #eee;
        }
        .directory-selector {
            margin-bottom: 20px;
        }
        .directory-selector select {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            min-width: 300px;
        }
        .import-section {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
        }
        .import-section input {
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            margin-right: 10px;
        }
        .import-section button {
            padding: 8px 16px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        .import-section button:hover {
            background: #0056b3;
        }
        .media-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            padding: 20px;
        }
        .media-item {
            border: 1px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
            background: white;
        }
        .media-preview {
            height: 200px;
            background: #f8f9fa;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }
        .media-preview img, .media-preview video {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
        .media-info {
            padding: 15px;
        }
        .media-info h3 {
            margin: 0 0 10px 0;
            font-size: 16px;
            color: #333;
        }
        .media-info p {
            margin: 5px 0;
            font-size: 14px;
            color: #666;
        }
        .description-edit {
            margin-top: 10px;
        }
        .description-edit textarea {
            width: 100%;
            min-height: 60px;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            resize: vertical;
        }
        .description-edit button {
            margin-top: 8px;
            padding: 6px 12px;
            background: #28a745;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
        }
        .description-edit button:hover {
            background: #1e7e34;
        }
        .error {
            color: #dc3545;
            font-size: 12px;
            margin-top: 5px;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Media Editor</h1>
        </div>

        <div class="controls">
            <div class="directory-selector">
                <label for="directory-select">Select Media Directory:</label><br>
                <select id="directory-select">
                    <option value="">Choose a directory...</option>
                </select>
            </div>

            <div class="import-section">
                <h3>Import Sticker Set</h3>
                <input type="text" id="sticker-set-name" placeholder="Sticker set name (e.g., WendyDancer)">
                <button onclick="importStickerSet()">Import Set</button>
                <div id="import-status"></div>
            </div>
        </div>

        <div id="media-container">
            <div class="loading">Select a directory to view media files</div>
        </div>
    </div>

    <script>
        let currentDirectory = '';

        // Load directories on page load
        fetch('/api/directories')
            .then(response => response.json())
            .then(directories => {
                const select = document.getElementById('directory-select');
                directories.forEach(dir => {
                    const option = document.createElement('option');
                    option.value = dir.path;
                    option.textContent = dir.name;
                    select.appendChild(option);
                });
            });

        // Handle directory selection
        document.getElementById('directory-select').addEventListener('change', function(e) {
            currentDirectory = e.target.value;
            if (currentDirectory) {
                loadMediaFiles(currentDirectory);
            } else {
                document.getElementById('media-container').innerHTML =
                    '<div class="loading">Select a directory to view media files</div>';
            }
        });

        function loadMediaFiles(directoryPath) {
            document.getElementById('media-container').innerHTML =
                '<div class="loading">Loading media files...</div>';

            const encodedPath = encodeURIComponent(directoryPath);
            fetch(`/api/media?directory=${encodedPath}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        document.getElementById('media-container').innerHTML =
                            `<div class="error">Error: ${data.error}</div>`;
                        return;
                    }

                    displayMediaFiles(data.grouped_media);
                })
                .catch(error => {
                    document.getElementById('media-container').innerHTML =
                        `<div class="error">Error loading media files: ${error}</div>`;
                });
        }

        function displayMediaFiles(groupedMedia) {
            const container = document.getElementById('media-container');

            if (Object.keys(groupedMedia).length === 0) {
                container.innerHTML = '<div class="loading">No media files found</div>';
                return;
            }

            let html = '';

            for (const [stickerSet, mediaFiles] of Object.entries(groupedMedia)) {
                html += `<h2 style="padding: 0 20px; margin: 20px 0 10px 0; color: #2c3e50;">${stickerSet}</h2>`;

                html += '<div class="media-grid">';
                mediaFiles.forEach(media => {
                    html += createMediaItemHTML(media);
                });
                html += '</div>';
            }

            container.innerHTML = html;
        }

        function createMediaItemHTML(media) {
            const encodedDir = encodeURIComponent(currentDirectory);
            const mediaUrl = media.media_file ?
                `/api/media/${media.unique_id}?directory=${encodedDir}` : null;

            const isAnimated = media.media_file && (media.media_file.endsWith('.tgs') || media.media_file.endsWith('.gif') || media.media_file.endsWith('.mp4'));

            let mediaElement = '';
            if (mediaUrl) {
                if (isAnimated) {
                    // Enhanced video controls for animated content
                    mediaElement = `<video controls preload="metadata" style="max-width: 100%; max-height: 100%;">
                        <source src="${mediaUrl}" type="video/mp4">
                        <source src="${mediaUrl}" type="video/webm">
                        Your browser does not support the video tag.
                    </video>`;
                } else if (media.media_file && media.media_file.endsWith('.tgs')) {
                    // TGS files - Lottie animations, show with download link
                    mediaElement = `<div style="display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; color: #666;">
                        <div style="font-size: 24px; margin-bottom: 10px;">🎭</div>
                        <div style="text-align: center; font-size: 12px; margin-bottom: 10px;">TGS Animated Sticker</div>
                        <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none; font-size: 12px;">Download TGS File</a>
                    </div>`;
                } else {
                    mediaElement = `<img src="${mediaUrl}" alt="${media.sticker_name || media.unique_id}">`;
                }
            } else {
                mediaElement = '<div style="color: #666;">No media file</div>';
            }

            return `
                <div class="media-item">
                    <div class="media-preview">
                        ${mediaElement}
                    </div>
                    <div class="media-info">
                        <h3>${media.sticker_name || media.unique_id}</h3>
                        <p><strong>Type:</strong> ${media.kind}</p>
                        <p><strong>Set:</strong> ${media.sticker_set_name}</p>
                        <p><strong>Status:</strong> ${media.status}</p>
                        ${media.failure_reason ? `<p class="error">${media.failure_reason}</p>` : ''}

                        <div class="description-edit">
                            <textarea id="desc-${media.unique_id}" placeholder="Enter description...">${media.description || ''}</textarea>
                            <button onclick="updateDescription('${media.unique_id}')">Save Description</button>
                        </div>
                    </div>
                </div>
            `;
        }

        function updateDescription(uniqueId) {
            const textarea = document.getElementById(`desc-${uniqueId}`);
            const description = textarea.value.trim();
            const encodedDir = encodeURIComponent(currentDirectory);

            fetch(`/api/media/${uniqueId}/description?directory=${encodedDir}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ description: description })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error updating description: ' + data.error);
                } else {
                    // Visual feedback
                    const button = textarea.nextElementSibling;
                    const originalText = button.textContent;
                    button.textContent = 'Saved!';
                    button.style.background = '#28a745';
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.style.background = '#28a745';
                    }, 1000);
                }
            })
            .catch(error => {
                alert('Error updating description: ' + error);
            });
        }

        function importStickerSet() {
            const stickerSetName = document.getElementById('sticker-set-name').value.trim();
            const statusDiv = document.getElementById('import-status');

            if (!stickerSetName) {
                statusDiv.innerHTML = '<div class="error">Please enter a sticker set name</div>';
                return;
            }

            if (!currentDirectory) {
                statusDiv.innerHTML = '<div class="error">Please select a directory first</div>';
                return;
            }

            statusDiv.innerHTML = '<div>Importing sticker set...</div>';

            fetch('/api/import-sticker-set', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    sticker_set_name: stickerSetName,
                    target_directory: currentDirectory
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    statusDiv.innerHTML = `<div class="error">Error: ${data.error}</div>`;
                } else {
                    statusDiv.innerHTML = '<div style="color: #28a745;">Import completed!</div>';
                    // Reload media files
                    loadMediaFiles(currentDirectory);
                }
            })
            .catch(error => {
                statusDiv.innerHTML = `<div class="error">Error: ${error}</div>`;
            });
        }
    </script>
</body>
</html>"""

    with open(templates_dir / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Media Editor Utility for cw-telegram-agent"
    )
    parser.add_argument(
        "--port", type=int, default=5000, help="Port to run the web server on"
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

    # Create templates
    create_templates()

    # Start the web server
    logger.info(f"Starting Media Editor on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
