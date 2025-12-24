#!/usr/bin/env python3
"""
Fix incorrect mime_type values in state/media records.

Some TGS files were saved with mime_type="video/mp4" (from conversion)
instead of the original TGS mime type. This script fixes those records.
"""

import json
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from media.mime_utils import is_tgs_mime_type, normalize_mime_type


def fix_tgs_mime_types(directory: Path) -> int:
    """Fix TGS mime_type values in all JSON files in the directory."""
    fixed_count = 0
    
    for json_file in directory.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                record = json.load(f)
            
            mime_type = record.get("mime_type")
            original_mime_type = record.get("original_mime_type")
            media_file = record.get("media_file", "")
            
            # Check if this record needs fixing
            needs_fix = False
            correct_mime_type = None
            
            # Case 1: Has original_mime_type that is TGS, but mime_type is video/mp4
            if (
                original_mime_type
                and is_tgs_mime_type(original_mime_type)
                and mime_type == "video/mp4"
            ):
                needs_fix = True
                correct_mime_type = normalize_mime_type(original_mime_type)
            
            # Case 2: media_file ends with .tgs but mime_type is video/mp4
            elif media_file.lower().endswith(".tgs") and mime_type == "video/mp4":
                needs_fix = True
                # Use original_mime_type if available, otherwise use standard TGS mime type
                if original_mime_type and is_tgs_mime_type(original_mime_type):
                    correct_mime_type = normalize_mime_type(original_mime_type)
                else:
                    # Default TGS mime type
                    correct_mime_type = "application/x-tgsticker"
            
            if needs_fix:
                print(
                    f"Fixing {json_file.name}: "
                    f"mime_type={mime_type} -> {correct_mime_type}"
                )
                record["mime_type"] = correct_mime_type
                
                # Write back to file
                temp_file = json_file.with_name(f"{json_file.name}.tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(record, f, indent=2, ensure_ascii=False)
                temp_file.replace(json_file)
                
                fixed_count += 1
        
        except Exception as e:
            print(f"Error processing {json_file}: {e}", file=sys.stderr)
            continue
    
    return fixed_count


def main():
    """Main entry point."""
    from config import STATE_DIRECTORY
    
    media_dir = Path(STATE_DIRECTORY) / "media"
    
    if not media_dir.exists():
        print(f"Media directory not found: {media_dir}")
        sys.exit(1)
    
    print(f"Scanning {media_dir} for TGS records with incorrect mime_type...")
    fixed_count = fix_tgs_mime_types(media_dir)
    
    if fixed_count > 0:
        print(f"\nFixed {fixed_count} record(s)")
    else:
        print("\nNo records needed fixing")


if __name__ == "__main__":
    main()

