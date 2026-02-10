# src/admin_console/prompts.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Role prompt editor routes and functionality for the admin console.
"""

import logging
import shutil
import re
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

from config import CONFIG_DIRECTORIES

logger = logging.getLogger(__name__)

# Create prompts blueprint
prompts_bp = Blueprint("prompts", __name__)


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


def resolve_prompts_path(config_dir: str) -> Path:
    """
    Resolve a prompts directory path.
    
    Args:
        config_dir: Config directory path (must be from CONFIG_DIRECTORIES whitelist)
        
    Returns:
        Path to the prompts directory
        
    Raises:
        ValueError: If config_dir is invalid
    """
    # Validate inputs
    if not validate_config_dir(config_dir):
        raise ValueError(f"Invalid config_dir: {config_dir} not in allowed CONFIG_DIRECTORIES")
    
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
    
    # Prompts are always in {config_dir}/prompts/
    prompts_path = base_path / "prompts"
    
    # Final safety check: ensure the resolved prompts_path is within the base_path
    try:
        prompts_path.resolve().relative_to(base_path.resolve())
    except ValueError:
        raise ValueError(f"Resolved prompts_path {prompts_path} is not within config directory {base_path}")
    
    return prompts_path


def validate_prompt_filename(filename: str) -> bool:
    """
    Validate that a prompt filename is safe and follows naming rules.
    
    Valid filenames must:
    - End with .md
    - Contain only alphanumerics, underscores, dashes, and spaces
    - Contain at least one non-space character
    - Be at most 50 characters (before .md extension)
    - Have no path traversal characters
    
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
    
    # Get the name without extension (must be <= 50 characters)
    name_without_ext = filename[:-3]
    if len(name_without_ext) > 50:
        return False
    
    # Check that it only contains allowed characters: alphanumerics, underscores, dashes, spaces
    if not re.match(r"^[a-zA-Z0-9_\- ]+$", name_without_ext):
        return False
    
    # Ensure at least one non-space character exists (prevent filenames with only spaces)
    if not re.search(r"[a-zA-Z0-9_\-]", name_without_ext):
        return False
    
    # Basic validation: no null bytes
    if "\x00" in filename:
        return False
    
    return True


@prompts_bp.route("/api/prompts", methods=["GET"])
def api_prompts_list():
    """Get list of role prompt files in a config directory or from all config directories if config_dir is not provided."""
    try:
        config_dir = request.args.get("config_dir")
        
        # If config_dir is not provided, list prompts from all config directories
        if not config_dir:
            all_prompt_files = []
            for cfg_dir in CONFIG_DIRECTORIES:
                try:
                    prompts_dir = resolve_prompts_path(cfg_dir)
                    # Ensure directory exists
                    prompts_dir.mkdir(parents=True, exist_ok=True)
                    
                    # List all .md files from this config directory
                    for md_file in sorted(prompts_dir.glob("*.md")):
                        if md_file.is_file():
                            all_prompt_files.append({
                                "filename": md_file.name,
                                "path": str(md_file),
                                "config_dir": cfg_dir,
                            })
                except Exception as e:
                    # Log but continue with other directories
                    logger.warning(f"Error listing prompts from {cfg_dir}: {e}")
                    continue
            
            # Sort by filename for consistent ordering
            all_prompt_files.sort(key=lambda x: x["filename"])
            
            return jsonify({
                "prompts": all_prompt_files,
                "config_dir": None,  # Indicates all directories were searched
            })
        
        # Single config directory case (existing behavior)
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        prompts_dir = resolve_prompts_path(config_dir)
        
        # Ensure directory exists
        prompts_dir.mkdir(parents=True, exist_ok=True)
        
        # List all .md files
        prompt_files = []
        for md_file in sorted(prompts_dir.glob("*.md")):
            if md_file.is_file():
                prompt_files.append({
                    "filename": md_file.name,
                    "path": str(md_file),
                    "config_dir": config_dir,  # Include config_dir for consistency
                })
        
        return jsonify({
            "prompts": prompt_files,
            "config_dir": config_dir,
            "prompts_dir": str(prompts_dir),
        })
    
    except Exception as e:
        logger.error(f"Error listing prompts: {e}")
        return jsonify({"error": str(e)}), 500


@prompts_bp.route("/api/prompts/<filename>", methods=["GET"])
def api_get_prompt(filename: str):
    """Get a specific role prompt file."""
    try:
        config_dir = request.args.get("config_dir")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_prompt_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        prompts_dir = resolve_prompts_path(config_dir)
        prompt_path = prompts_dir / filename
        
        if not prompt_path.exists():
            return jsonify({"error": "Prompt not found"}), 404
        
        content = prompt_path.read_text(encoding="utf-8")
        
        return jsonify({
            "filename": filename,
            "content": content,
            "config_dir": config_dir,
        })
    
    except Exception as e:
        logger.error(f"Error getting prompt {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@prompts_bp.route("/api/prompts/<filename>", methods=["PUT"])
def api_update_prompt(filename: str):
    """Update or create a role prompt file."""
    try:
        config_dir = request.args.get("config_dir")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_prompt_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        data = request.json
        if data is None:
            return jsonify({"error": "Missing request body"}), 400
        
        content = data.get("content", "")
        # content is optional - if not provided, keep existing content or use empty string
        
        prompts_dir = resolve_prompts_path(config_dir)
        prompts_dir.mkdir(parents=True, exist_ok=True)
        
        prompt_path = prompts_dir / filename
        
        # Write content (create if doesn't exist)
        prompt_path.write_text(content, encoding="utf-8")
        
        logger.info(f"Updated prompt {filename} in {prompts_dir}")
        return jsonify({"success": True, "filename": filename})
    
    except Exception as e:
        logger.error(f"Error updating prompt {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@prompts_bp.route("/api/prompts/<filename>", methods=["DELETE"])
def api_delete_prompt(filename: str):
    """Delete a role prompt file."""
    try:
        config_dir = request.args.get("config_dir")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_prompt_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        prompts_dir = resolve_prompts_path(config_dir)
        prompt_path = prompts_dir / filename
        
        if not prompt_path.exists():
            return jsonify({"error": "Prompt not found"}), 404
        
        prompt_path.unlink()
        
        logger.info(f"Deleted prompt {filename} from {prompts_dir}")
        return jsonify({"success": True})
    
    except Exception as e:
        logger.error(f"Error deleting prompt {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@prompts_bp.route("/api/prompts/<filename>/rename", methods=["POST"])
def api_rename_prompt(filename: str):
    """Rename a role prompt file."""
    try:
        config_dir = request.args.get("config_dir")
        
        if not config_dir:
            return jsonify({"error": "Missing config_dir parameter"}), 400
        
        if not validate_config_dir(config_dir):
            return jsonify({"error": "Invalid config_dir parameter"}), 400
        
        if not validate_prompt_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        data = request.json
        if data is None:
            return jsonify({"error": "Missing request body"}), 400
        
        new_filename = data.get("new_filename")
        if not new_filename:
            return jsonify({"error": "Missing new_filename"}), 400
        
        if not validate_prompt_filename(new_filename):
            return jsonify({"error": "Invalid new filename"}), 400
        
        prompts_dir = resolve_prompts_path(config_dir)
        old_path = prompts_dir / filename
        new_path = prompts_dir / new_filename
        
        if not old_path.exists():
            return jsonify({"error": "Prompt not found"}), 404
        
        if new_path.exists():
            return jsonify({"error": "Target filename already exists"}), 400
        
        old_path.rename(new_path)
        
        logger.info(f"Renamed prompt {filename} to {new_filename} in {prompts_dir}")
        return jsonify({"success": True, "filename": new_filename})
    
    except Exception as e:
        logger.error(f"Error renaming prompt {filename}: {e}")
        return jsonify({"error": str(e)}), 500


@prompts_bp.route("/api/prompts/<filename>/move", methods=["POST"])
def api_move_prompt(filename: str):
    """Move a role prompt file to another config directory."""
    try:
        from_config_dir = request.args.get("from_config_dir")
        
        if not from_config_dir:
            return jsonify({"error": "Missing from_config_dir parameter"}), 400
        
        if not validate_config_dir(from_config_dir):
            return jsonify({"error": "Invalid from_config_dir parameter"}), 400
        
        if not validate_prompt_filename(filename):
            return jsonify({"error": "Invalid filename"}), 400
        
        data = request.json
        if data is None:
            return jsonify({"error": "Missing request body"}), 400
        
        to_config_dir = data.get("to_config_dir")
        
        if not to_config_dir:
            return jsonify({"error": "Missing to_config_dir"}), 400
        
        if not validate_config_dir(to_config_dir):
            return jsonify({"error": "Invalid to_config_dir parameter"}), 400
        
        # Resolve source and destination paths
        from_prompts_dir = resolve_prompts_path(from_config_dir)
        to_prompts_dir = resolve_prompts_path(to_config_dir)
        
        from_path = from_prompts_dir / filename
        to_path = to_prompts_dir / filename  # Preserve filename
        
        if not from_path.exists():
            return jsonify({"error": "Source prompt not found"}), 404
        
        if to_path.exists():
            return jsonify({"error": "Target prompt already exists"}), 400
        
        # Ensure destination directory exists
        to_prompts_dir.mkdir(parents=True, exist_ok=True)
        
        # Move the file
        shutil.move(str(from_path), str(to_path))
        
        logger.info(
            f"Moved prompt {filename} from {from_prompts_dir} to {to_prompts_dir}"
        )
        return jsonify({
            "success": True,
            "filename": filename,
            "to_config_dir": to_config_dir,
        })
    
    except Exception as e:
        logger.error(f"Error moving prompt {filename}: {e}")
        return jsonify({"error": str(e)}), 500

