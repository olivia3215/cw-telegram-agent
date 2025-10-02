# prompt_loader.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import os
from pathlib import Path


def get_config_directories():
    """
    Get configuration directories from environment variables.
    Supports multiple directories via CINDY_AGENT_CONFIG_PATH (colon-separated).
    """
    config_path = os.environ.get("CINDY_AGENT_CONFIG_PATH")
    if config_path:
        # Split by colon and strip whitespace
        dirs = [d.strip() for d in config_path.split(":") if d.strip()]
        # If we have valid directories after filtering, return them
        if dirs:
            return dirs

    # Default to samples directory if CINDY_AGENT_CONFIG_PATH is not set or contains only whitespace/separators
    return ["samples"]


def load_system_prompt(prompt_name: str, agent_name: str | None = None):
    """
    Loads a single system prompt file by name from the prompts directories.

    Args:
        prompt_name: Name of the prompt file (without .md extension)
        agent_name: Optional agent name for agent-specific prompt loading
    """
    # Search all config directories
    config_path = get_config_directories()

    for config_dir in config_path:
        path = Path(config_dir)
        if not path.exists() or not path.is_dir():
            continue

        # First, try agent-specific prompts (higher priority)
        if agent_name:
            agent_prompts_dir_path = path / "agents" / agent_name / "prompts"
            if agent_prompts_dir_path.exists() and agent_prompts_dir_path.is_dir():
                file_path = agent_prompts_dir_path / f"{prompt_name}.md"
                if file_path.exists():
                    return file_path.read_text().strip()

        # Then try global prompts
        prompts_dir_path = path / "prompts"
        if prompts_dir_path.exists() and prompts_dir_path.is_dir():
            file_path = prompts_dir_path / f"{prompt_name}.md"
            if file_path.exists():
                return file_path.read_text().strip()

    # If we get here, the prompt wasn't found in any config directory
    searched_dirs = []
    if agent_name:
        searched_dirs.extend(
            [str(Path(d) / "agents" / agent_name / "prompts") for d in config_path]
        )
    searched_dirs.extend([str(Path(d) / "prompts") for d in config_path])
    raise RuntimeError(
        f"Prompt file '{prompt_name}.md' not found in any of the following directories: {searched_dirs}"
    )
