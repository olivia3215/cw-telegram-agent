#!/usr/bin/env python3
# scripts/update_headers.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Script to update copyright headers in all Python files.
Ensures each file starts with:
1. A comment with the filename
2. A blank comment line
3. Copyright notice with 2025-2026
4. License notice
5. A blank comment line
"""

import os
import re
from pathlib import Path


def get_relative_path(filepath, base_dir):
    """Get the relative path from base directory."""
    try:
        return os.path.relpath(filepath, base_dir)
    except ValueError:
        return filepath


def update_file_header(filepath, base_dir):
    """Update the header of a Python file."""
    rel_path = get_relative_path(filepath, base_dir)
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    # Target header format
    target_header = [
        f"# {rel_path}",
        "#",
        "# Copyright (c) 2025-2026 Cindy's World LLC and contributors",
        "# Licensed under the MIT License. See LICENSE.md for details.",
        "#",
    ]
    
    # Find where the actual code starts (skip shebang and existing headers)
    code_start = 0
    for i, line in enumerate(lines):
        # Skip shebang
        if i == 0 and line.startswith('#!'):
            code_start = 1
            continue
        # Skip existing header comments
        if line.startswith('#') or line.strip() == '':
            continue
        # Found first non-comment, non-empty line
        code_start = i
        break
    
    # If file starts with shebang, preserve it
    if lines[0].startswith('#!'):
        new_content = [lines[0]] + target_header + lines[code_start:]
    else:
        new_content = target_header + lines[code_start:]
    
    # Write back
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_content))
    
    return True


def main():
    base_dir = Path(__file__).parent.parent
    
    # Find all Python files in src, tests, and scripts
    python_files = []
    for directory in ['src', 'tests', 'scripts']:
        dir_path = base_dir / directory
        if dir_path.exists():
            python_files.extend(dir_path.rglob('*.py'))
    
    updated_count = 0
    for filepath in sorted(python_files):
        try:
            update_file_header(filepath, base_dir)
            updated_count += 1
            print(f"Updated: {filepath.relative_to(base_dir)}")
        except Exception as e:
            print(f"Error updating {filepath}: {e}")
    
    print(f"\nTotal files updated: {updated_count}")


if __name__ == '__main__':
    main()
