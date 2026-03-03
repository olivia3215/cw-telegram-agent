#!/usr/bin/env python3
# scripts/add_admin.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Add an administrator so they can log in to the admin console via Google OAuth.

The email must match the Google account they will use. Name and avatar are
optional and can be updated on first login.

Usage:
    source .env   # or set PYTHONPATH and DB env vars
    python scripts/add_admin.py admin@example.com
    python scripts/add_admin.py admin@example.com --name "Jane Admin"
"""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add an administrator for the admin console (Google OAuth)."
    )
    parser.add_argument(
        "email",
        metavar="EMAIL",
        help="Email address (must match their Google account)",
    )
    parser.add_argument(
        "--name",
        metavar="NAME",
        default=None,
        help="Display name (optional; can be set on first login)",
    )
    args = parser.parse_args()

    email = args.email.strip()
    if not email:
        print("Error: email is required.", file=sys.stderr)
        return 1

    try:
        from db import administrators
    except ImportError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Run from repo root with: source .env && python scripts/add_admin.py EMAIL", file=sys.stderr)
        return 1

    try:
        existing = administrators.get_administrator(email)
        administrators.upsert_administrator(email, name=args.name or None)
        if existing:
            print(f"Updated administrator: {email}")
        else:
            print(f"Added administrator: {email}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
