# admin_console/global_parameters.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Global parameters editor routes and functionality for the admin console.
"""

import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]

import config

logger = logging.getLogger(__name__)

# Create global_parameters blueprint
global_parameters_bp = Blueprint("global_parameters", __name__)

# Map of parameter names to their metadata
PARAMETER_METADATA = {
    "MEDIA_MODEL": {
        "type": "string",
        "comment": "The model to use to analyze media",
        "default": None,
    },
    "TRANSLATION_MODEL": {
        "type": "string",
        "comment": "The model used to translate messages to English for the admin console",
        "default": None,
    },
    "START_TYPING_DELAY": {
        "type": "float",
        "comment": "The number of seconds that the agent takes to type the first character",
        "default": "2",
    },
    "TYPING_SPEED": {
        "type": "float",
        "comment": "The number of characters per second",
        "default": "60",
    },
    "SELECT_STICKER_DELAY": {
        "type": "float",
        "comment": "The amount of time to select a sticker",
        "default": "4",
    },
    "DEFAULT_AGENT_LLM": {
        "type": "string",
        "comment": "The default LLM to use for an agent that doesn't specify one",
        "default": "gemini",
    },
}


def get_env_file_path() -> Path:
    """Get the path to the .env file."""
    # Get project root (parent of src/)
    project_root = Path(__file__).parent.parent.parent
    return project_root / ".env"


def update_env_file(parameter_name: str, value: str) -> None:
    """
    Append a new export line to the .env file.
    
    Args:
        parameter_name: Name of the environment variable
        value: Value to set
    """
    env_file = get_env_file_path()
    metadata = PARAMETER_METADATA.get(parameter_name, {})
    comment = metadata.get("comment", "")
    
    # Append blank line, comment, and export line
    with env_file.open("a") as f:
        f.write("\n")
        if comment:
            f.write(f"# {comment}\n")
        f.write(f"export {parameter_name}={value}\n")


def update_runtime_config(parameter_name: str, value: str) -> None:
    """
    Update the runtime configuration (os.environ and config module constants).
    
    Args:
        parameter_name: Name of the environment variable
        value: Value to set
    """
    # Update os.environ
    os.environ[parameter_name] = value
    
    # Update config module constants based on parameter name
    if parameter_name == "MEDIA_MODEL":
        config.MEDIA_MODEL = value
    elif parameter_name == "TRANSLATION_MODEL":
        config.TRANSLATION_MODEL = value
    elif parameter_name == "START_TYPING_DELAY":
        try:
            config.START_TYPING_DELAY = float(value)
        except ValueError:
            config.START_TYPING_DELAY = 2.0
    elif parameter_name == "TYPING_SPEED":
        try:
            config.TYPING_SPEED = float(value)
        except ValueError:
            config.TYPING_SPEED = 60.0
    elif parameter_name == "SELECT_STICKER_DELAY":
        try:
            config.SELECT_STICKER_DELAY = float(value)
        except ValueError:
            config.SELECT_STICKER_DELAY = 4.0
    elif parameter_name == "DEFAULT_AGENT_LLM":
        config.DEFAULT_AGENT_LLM = value


def validate_parameter_value(parameter_name: str, value: str) -> tuple[bool, str | None]:
    """
    Validate a parameter value.
    
    Args:
        parameter_name: Name of the parameter
        value: Value to validate (will be converted to string)
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if parameter_name not in PARAMETER_METADATA:
        return False, f"Unknown parameter: {parameter_name}"
    
    metadata = PARAMETER_METADATA[parameter_name]
    param_type = metadata.get("type", "string")
    
    # Convert value to string for processing
    value_str = str(value) if value is not None else ""
    
    # Type validation
    if param_type == "float":
        try:
            float(value_str)
        except ValueError:
            return False, f"Invalid value for {parameter_name}: must be a number"
    
    # Special validation for DEFAULT_AGENT_LLM
    if parameter_name == "DEFAULT_AGENT_LLM":
        value_lower = value_str.strip().lower()
        if value_lower == "gemini" and not config.GEMINI_MODEL:
            return False, "DEFAULT_AGENT_LLM cannot be set to 'gemini' when GEMINI_MODEL is not set"
    
    return True, None


def get_current_parameter_values() -> dict[str, str | float]:
    """Get current values of all global parameters."""
    return {
        "MEDIA_MODEL": config.MEDIA_MODEL or "",
        "TRANSLATION_MODEL": config.TRANSLATION_MODEL or "",
        "START_TYPING_DELAY": config.START_TYPING_DELAY,
        "TYPING_SPEED": config.TYPING_SPEED,
        "SELECT_STICKER_DELAY": config.SELECT_STICKER_DELAY,
        "DEFAULT_AGENT_LLM": config.DEFAULT_AGENT_LLM,
    }


@global_parameters_bp.route("/api/global-parameters", methods=["GET"])
def api_global_parameters_get():
    """Get all global parameter values."""
    try:
        values = get_current_parameter_values()
        parameters = []
        for param_name, param_value in values.items():
            metadata = PARAMETER_METADATA[param_name]
            parameters.append({
                "name": param_name,
                "value": str(param_value),
                "type": metadata["type"],
                "comment": metadata.get("comment", ""),
                "default": metadata.get("default"),
            })
        return jsonify({"parameters": parameters})
    except Exception as e:
        logger.exception("Error getting global parameters")
        return jsonify({"error": str(e)}), 500


@global_parameters_bp.route("/api/global-parameters", methods=["POST"])
def api_global_parameters_update():
    """Update a global parameter value."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        parameter_name = data.get("name")
        value = data.get("value")
        
        if not parameter_name:
            return jsonify({"error": "Parameter name is required"}), 400
        
        if value is None:
            return jsonify({"error": "Parameter value is required"}), 400
        
        # Validate the value
        is_valid, error_msg = validate_parameter_value(parameter_name, str(value))
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        
        # Update .env file
        try:
            update_env_file(parameter_name, str(value))
        except Exception as e:
            logger.exception(f"Error updating .env file for {parameter_name}")
            return jsonify({"error": f"Failed to update .env file: {str(e)}"}), 500
        
        # Update runtime config
        try:
            update_runtime_config(parameter_name, str(value))
        except Exception as e:
            logger.exception(f"Error updating runtime config for {parameter_name}")
            return jsonify({"error": f"Failed to update runtime config: {str(e)}"}), 500
        
        logger.info(f"Updated global parameter {parameter_name} to {value}")
        return jsonify({"success": True, "message": f"Updated {parameter_name} to {value}"})
    
    except Exception as e:
        logger.exception("Error updating global parameter")
        return jsonify({"error": str(e)}), 500

