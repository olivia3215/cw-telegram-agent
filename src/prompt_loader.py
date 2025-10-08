# prompt_loader.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from pathlib import Path

from config import CONFIG_DIRECTORIES


def load_system_prompt(prompt_name: str, agent_name: str | None = None):
    """
    Loads a single system prompt file by name from the prompts directories.

    Args:
        prompt_name: Name of the prompt file (without .md extension)
        agent_name: Optional agent name for agent-specific prompt loading
    """
    # Search all config directories
    config_path = CONFIG_DIRECTORIES

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
