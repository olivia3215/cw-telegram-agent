# prompt_loader.py
import os
from pathlib import Path

_system_preamble = None

def load_raw_system_prompt_preamble(prompts_dir="./prompts"):
    global _system_preamble
    if _system_preamble is not None:
        return _system_preamble

    path = Path(prompts_dir)
    if not path.exists():
        raise RuntimeError(f"Prompt directory not found: {prompts_dir}")

    parts = []
    for file in sorted(path.glob("*.md")):
        parts.append(file.read_text().strip())

    _system_preamble = "\n\n".join(parts)
    return _system_preamble
