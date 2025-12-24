#!/usr/bin/env python3
"""
Remove JSON records that don't have corresponding media files.

Some records may have been saved without media files (e.g., budget exhaustion
before the fix). This script removes those orphaned records.
"""

import json
import sys
from pathlib import Path

# Media file extensions to check
MEDIA_FILE_EXTENSIONS = [
    ".webp",
    ".tgs",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
]


def cleanup_records_without_media(directory: Path, dry_run: bool = False) -> int:
    """Remove JSON records that don't have corresponding media files."""
    removed_count = 0
    
    for json_file in directory.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                record = json.load(f)
            
            unique_id = record.get("unique_id")
            if not unique_id:
                continue
            
            # Check if media file exists
            media_file_name = record.get("media_file")
            media_file_exists = False
            
            if media_file_name:
                # Check the specific file mentioned in the record
                media_file = directory / media_file_name
                if media_file.exists():
                    media_file_exists = True
            else:
                # Check for any media file with this unique_id
                for ext in MEDIA_FILE_EXTENSIONS:
                    media_file = directory / f"{unique_id}{ext}"
                    if media_file.exists():
                        media_file_exists = True
                        break
            
            # Remove record if no media file exists
            if not media_file_exists:
                if dry_run:
                    print(f"[DRY RUN] Would remove {json_file.name} (no media file)")
                else:
                    print(f"Removing {json_file.name} (no media file)")
                    json_file.unlink()
                
                removed_count += 1
        
        except Exception as e:
            print(f"Error processing {json_file}: {e}", file=sys.stderr)
            continue
    
    return removed_count


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Remove JSON records that don't have corresponding media files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without actually removing files",
    )
    args = parser.parse_args()
    
    # Add src to path for imports
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    
    from config import STATE_DIRECTORY
    
    media_dir = Path(STATE_DIRECTORY) / "media"
    
    if not media_dir.exists():
        print(f"Media directory not found: {media_dir}")
        sys.exit(1)
    
    print(f"Scanning {media_dir} for records without media files...")
    if args.dry_run:
        print("(DRY RUN - no files will be removed)")
    
    removed_count = cleanup_records_without_media(media_dir, dry_run=args.dry_run)
    
    if removed_count > 0:
        print(f"\n{'Would remove' if args.dry_run else 'Removed'} {removed_count} record(s)")
    else:
        print("\nNo records without media files found")


if __name__ == "__main__":
    main()

