# prompt_loader.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import os
from pathlib import Path

# Cache for storing loaded prompts to avoid redundant file I/O
_prompt_cache = {}


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


def load_system_prompt(prompt_name: str):
    """
    Loads a single system prompt file by name from the prompts directories.
    The prompt is cached in memory after the first read.

    Args:
        prompt_name: Name of the prompt file (without .md extension)
    """
    if prompt_name in _prompt_cache:
        return _prompt_cache[prompt_name]

    # Search all config directories
    config_path = get_config_directories()

    for config_dir in config_path:
        path = Path(config_dir)
        if not path.exists() or not path.is_dir():
            continue

        prompts_dir_path = path / "prompts"
        if not prompts_dir_path.exists() or not prompts_dir_path.is_dir():
            continue

        file_path = prompts_dir_path / f"{prompt_name}.md"
        if file_path.exists():
            prompt_content = file_path.read_text().strip()
            _prompt_cache[prompt_name] = prompt_content
            return prompt_content

    # If we get here, the prompt wasn't found in any config directory
    searched_dirs = [str(Path(d) / "prompts") for d in config_path]
    raise RuntimeError(
        f"Prompt file '{prompt_name}.md' not found in any of the following directories: {searched_dirs}"
    )
