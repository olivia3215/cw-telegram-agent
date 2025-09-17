# prompt_loader.py
from pathlib import Path

# Cache for storing loaded prompts to avoid redundant file I/O
_prompt_cache = {}


def load_system_prompt(prompt_name: str, prompts_dir="./prompts"):
    """
    Loads a single system prompt file by name from the prompts directory.
    The prompt is cached in memory after the first read.
    """
    if prompt_name in _prompt_cache:
        return _prompt_cache[prompt_name]

    file_path = Path(prompts_dir) / f"{prompt_name}.md"
    if not file_path.exists():
        raise RuntimeError(f"Prompt file not found: {file_path}")

    # Read the prompt content and store it in the cache
    prompt_content = file_path.read_text().strip()
    _prompt_cache[prompt_name] = prompt_content

    return prompt_content
