# src/admin_console/docs.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Document editor routes and functionality for the admin console.
"""

import logging
import shutil
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from config import CONFIG_DIRECTORIES
from admin_console.helpers import get_agent_by_name

logger = logging.getLogger(__name__)

# Create docs blueprint
docs_bp = Blueprint("docs", __name__)


def validate_config_dir(config_dir: str) -> bool:
    """
    Validate that config_dir is one of the allowed CONFIG_DIRECTORIES.
    
    Args:
        config_dir: Config directory path to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not config_dir:
        return False
    
    # Check against whitelist of allowed config directories
    return config_dir in CONFIG_DIRECTORIES


def validate_agent_config_name(agent_config_name: str | None) -> bool:
    """
    Validate that agent_config_name is safe (no path traversal).
    
    Args:
        agent_config_name: Agent config name to validate (can be None)
        
    Returns:
        True if valid, False otherwise
    """
    if agent_config_name is None:
        return True  # None is valid (for global docs)
    
    if not agent_config_name:
        return False
    
    # Prevent directory traversal (reject both forward and backslashes)
    if "/" in agent_config_name or "\\" in agent_config_name:
        return False
    
    # Prevent parent directory references
    if ".." in agent_config_name:
        return False
    
    # Basic validation: no null bytes, reasonable length
    if "\x00" in agent_config_name or len(agent_config_name) > 255:
        return False
    
    return True


def resolve_docs_path(config_dir: str, agent_config_name: str | None = None) -> Path:
    """
    Resolve a docs directory path.
    
    Args:
        config_dir: Config directory path (must be from CONFIG_DIRECTORIES whitelist)
        agent_config_name: Agent config name (without .md extension) for agent-specific docs, or None for global docs
        
    Returns:
        Path to the docs directory
        
    Raises:
        ValueError: If config_dir or agent_config_name are invalid
    """
    # Validate inputs
    if not validate_config_dir(config_dir):
        raise ValueError(f"Invalid config_dir: {config_dir} not in allowed CONFIG_DIRECTORIES")
    
    if not validate_agent_config_name(agent_config_name):
        raise ValueError(f"Invalid agent_config_name: contains path traversal characters")
    
    config_path = Path(config_dir)
    project_root = Path(__file__).parent.parent.parent
    
    # Resolve the base config directory path
    if config_path.is_absolute():
        base_path = config_path.resolve()
    else:
        # For relative paths, resolve relative to the project root (parent of src/)
        base_path = (project_root / config_dir).resolve()
    
    # Ensure the resolved path is actually within the intended config directory
    # This prevents path traversal even if somehow a malicious value passed validation
    allowed_paths = [
        (Path(d).resolve() if Path(d).is_absolute() else (project_root / d).resolve())
        for d in CONFIG_DIRECTORIES
    ]
    
    # Verify that base_path matches one of the allowed paths
    # Since we already validated config_dir is in CONFIG_DIRECTORIES, this should always pass,
    # but this provides defense-in-depth against path traversal
    base_path_resolved = base_path.resolve()
    is_valid = False
    for allowed_path in allowed_paths:
        allowed_path_resolved = allowed_path.resolve()
        # Check if paths match (handles symlinks and different representations)
        if base_path_resolved == allowed_path_resolved:
            is_valid = True
            break
    
    if not is_valid:
        raise ValueError(f"Resolved config_dir path {base_path_resolved} is not within allowed CONFIG_DIRECTORIES")
    
    if agent_config_name:
        # Agent-specific docs: {configdir}/agents/{agent_name}/docs/
        docs_path = base_path / "agents" / agent_config_name / "docs"
    else:
        # Global docs: {configdir}/docs/
        docs_path = base_path / "docs"
    
    # Final safety check: ensure the resolved docs_path is within the base_path
    try:
        docs_path.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise ValueError(f"Resolved docs_path {docs_path} is not within config directory {base_path}")
    
    return docs_path


def validate_filename(filename: str) -> bool:
    """
    Validate that a filename is safe (no directory traversal).
    
    Args:
        filename: The filename to validate
        
    Returns:
        True if valid, False otherwise
    """
    if not filename:
        return False
    # Prevent directory traversal (reject both forward and backslashes)
    if "/" in filename or "\\" in filename:
        return False
    # Ensure it ends with .md
    if not filename.endswith(".md"):
        return False
    # Basic validation: no null bytes, reasonable length
    if "\x00" in filename or len(filename) > 255:
        return False
    return True


@docs_bp.route("/api/docs", methods=["GET"])
def api_docs_list():
    """Get list of doc files in a directory or from all config directories if config_dir is not provided."""
    try:
        config_dir = request.args.get("config_dir")
        agent_config_name = request.args.get("agent_config_name")  # None for global docs
        
        if not validate_agent_config_name(agent_config_name):
            return jsonify({"error": "Invalid agent_config_name parameter"}), 400
        
        # If config_dir is not provided, list docs from all config directories
        if not config_dir:
            all_doc_files = []
            for cfg_dir in CONFIG_DIRECTORIES:
                try:
                    docs_dir = resolve_docs_path(cfg_dir, agent_config_name)
                    # Ensure directory exists
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    
                    # List all .md files from this config directory
                    for md_file in sorted(docs_dir.glob("*.md")):
                        if md_file.is_file():
                            all_doc_files.append({
                                "filename": md_file.name,
                                "path": str(md_file),
                                "config_dir": cfg_dir,
                            })
                except Exception as e:
                    # Log but continue with other directories
                    logger.warning(f"Error listing docs from {cfg_dir}: {e}")
                    continue
            
            # Sort by filename for consistent ordering
            all_doc_files.sort(key=lambda x: x["filename"])
            
            return jsonify({
                "docs": all_doc_files,
                "config_dir": None,  # Indicates all directories were searched
                "agent_config_name": agent_config_name,
            })
        
        # Single config directory case (existing behavior)
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        docs_dir = resolve_docs_path(config_dir, agent_config_name)
        
        # Ensure directory exists
        docs_dir.mkdir(parents=True, exist_ok=True)
        
        # List all .md files
        doc_files = []
        for md_file in sorted(docs_dir.glob("*.md")):
            if md_file.is_file():
                doc_files.append({
                    "filename": md_file.name,
                    "path": str(md_file),
                    "config_dir": config_dir,  # Include config_dir for consistency
                })
        
        return jsonify({
            "docs": doc_files,
            "config_dir": config_dir,
            "agent_config_name": agent_config_name,
            "docs_dir": str(docs_dir),
        })
    
    except Exception as e:
        logger.error(f"Error listing docs: {e}")
        return jsonify({"error": str(e)}), 500


@docs_bp.route("/api/docs/<filename>", methods=["GET"])
def api_get_doc(filename: str):
    """Get a specific doc file."""
    try:
        config_dir = request.args.get("config_dir")
        agent_config_name = request.args.get("agent_config_name")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_agent_config_name(agent_config_name):
            return jsonify({"error": "Invalid agent_config_name parameter"}), 400
        
        if not validate_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        docs_dir = resolve_docs_path(config_dir, agent_config_name)
        doc_path = docs_dir / filename
        
        if not doc_path.exists():
            return jsonify({"error": "Doc not found"}), 404
        
        content = doc_path.read_text(encoding="utf-8")
        
        return jsonify({
            "filename": filename,
            "content": content,
            "config_dir": config_dir,
            "agent_config_name": agent_config_name,
        })
    
    except Exception as e:
        logger.error(f"Error getting doc {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@docs_bp.route("/api/docs/<filename>", methods=["PUT"])
def api_update_doc(filename: str):
    """Update or create a doc file."""
    try:
        config_dir = request.args.get("config_dir")
        agent_config_name = request.args.get("agent_config_name")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_agent_config_name(agent_config_name):
            return jsonify({"error": "Invalid agent_config_name parameter"}), 400
        
        if not validate_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        data = request.json
        if data is None:
            return jsonify({"error": "Missing request body"}), 400
        
        content = data.get("content", "")
        # content is optional - if not provided, keep existing content or use empty string
        
        docs_dir = resolve_docs_path(config_dir, agent_config_name)
        docs_dir.mkdir(parents=True, exist_ok=True)
        
        doc_path = docs_dir / filename
        
        # Write content (create if doesn't exist)
        doc_path.write_text(content, encoding="utf-8")
        
        logger.info(f"Updated doc {filename} in {docs_dir}")
        return jsonify({"success": True, "filename": filename})
    
    except Exception as e:
        logger.error(f"Error updating doc {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@docs_bp.route("/api/docs/<filename>", methods=["DELETE"])
def api_delete_doc(filename: str):
    """Delete a doc file."""
    try:
        config_dir = request.args.get("config_dir")
        agent_config_name = request.args.get("agent_config_name")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_agent_config_name(agent_config_name):
            return jsonify({"error": "Invalid agent_config_name parameter"}), 400
        
        if not validate_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        docs_dir = resolve_docs_path(config_dir, agent_config_name)
        doc_path = docs_dir / filename
        
        if not doc_path.exists():
            return jsonify({"error": "Doc not found"}), 404
        
        doc_path.unlink()
        
        logger.info(f"Deleted doc {filename} from {docs_dir}")
        return jsonify({"success": True})
    
    except Exception as e:
        logger.error(f"Error deleting doc {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@docs_bp.route("/api/docs/<filename>/rename", methods=["POST"])
def api_rename_doc(filename: str):
    """Rename a doc file."""
    try:
        config_dir = request.args.get("config_dir")
        agent_config_name = request.args.get("agent_config_name")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_agent_config_name(agent_config_name):
            return jsonify({"error": "Invalid agent_config_name parameter"}), 400
        
        if not validate_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        data = request.json
        if data is None:
            return jsonify({"error": "Missing request body"}), 400
        
        new_filename = data.get("new_filename")
        if not new_filename:
            return jsonify({"error": "Missing new_filename"}), 400
        
        if not validate_filename(new_filename):
            return jsonify({"error": "Invalid new filename"}), 400
        
        docs_dir = resolve_docs_path(config_dir, agent_config_name)
        old_path = docs_dir / filename
        new_path = docs_dir / new_filename
        
        if not old_path.exists():
            return jsonify({"error": "Doc not found"}), 404
        
        if new_path.exists():
            return jsonify({"error": "Target filename already exists"}), 400
        
        old_path.rename(new_path)
        
        logger.info(f"Renamed doc {filename} to {new_filename} in {docs_dir}")
        return jsonify({"success": True, "filename": new_filename})
    
    except Exception as e:
        logger.error(f"Error renaming doc {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@docs_bp.route("/api/docs/<filename>/move", methods=["POST"])
def api_move_doc(filename: str):
    """Move a doc file to another directory (global or agent-specific)."""
    try:
        from_config_dir = request.args.get("from_config_dir")
        from_agent_config_name = request.args.get("from_agent_config_name")
        
        if not from_config_dir:
            return jsonify({"error": "Missing from_config_dir parameter"}), 400
        
        if not validate_config_dir(from_config_dir):
            return jsonify({"error": "Invalid from_config_dir parameter"}), 400
        
        if not validate_agent_config_name(from_agent_config_name):
            return jsonify({"error": "Invalid from_agent_config_name parameter"}), 400
        
        if not validate_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        data = request.json
        if data is None:
            return jsonify({"error": "Missing request body"}), 400
        
        to_config_dir = data.get("to_config_dir")
        to_agent_config_name = data.get("to_agent_config_name")  # None for global docs
        
        if not to_config_dir:
            return jsonify({"error": "Missing to_config_dir"}), 400
        
        if not validate_config_dir(to_config_dir):
            return jsonify({"error": "Invalid to_config_dir parameter"}), 400
        
        if not validate_agent_config_name(to_agent_config_name):
            return jsonify({"error": "Invalid to_agent_config_name parameter"}), 400
        
        # Resolve source and destination paths
        from_docs_dir = resolve_docs_path(from_config_dir, from_agent_config_name)
        to_docs_dir = resolve_docs_path(to_config_dir, to_agent_config_name)
        
        from_path = from_docs_dir / filename
        to_path = to_docs_dir / filename
        
        if not from_path.exists():
            return jsonify({"error": "Source doc not found"}), 404
        
        if to_path.exists():
            return jsonify({"error": "Target doc already exists"}), 400
        
        # Ensure destination directory exists
        to_docs_dir.mkdir(parents=True, exist_ok=True)
        
        # Move the file
        shutil.move(str(from_path), str(to_path))
        
        logger.info(
            f"Moved doc {filename} from {from_docs_dir} to {to_docs_dir}"
        )
        return jsonify({
            "success": True,
            "filename": filename,
            "to_config_dir": to_config_dir,
            "to_agent_config_name": to_agent_config_name,
        })
    
    except Exception as e:
        logger.error(f"Error moving doc {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@docs_bp.route("/api/config-directories", methods=["GET"])
def api_config_directories():
    """Get list of available config directories."""
    try:
        directories = []
        for config_dir in CONFIG_DIRECTORIES:
            config_path = Path(config_dir)
            if config_path.is_absolute():
                display_path = str(config_path)
            else:
                # Resolve relative to project root
                project_root = Path(__file__).parent.parent.parent
                resolved_path = (project_root / config_dir).resolve()
                display_path = str(resolved_path)
            
            directories.append({
                "path": config_dir,
                "display_path": display_path,
            })
        
        return jsonify({"directories": directories})
    
    except Exception as e:
        logger.error(f"Error getting config directories: {e}")
        return jsonify({"error": str(e)}), 500


