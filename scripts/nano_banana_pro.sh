#!/bin/bash
#
# Start the Nano Banana Pro web utility for generating images with Gemini
# (gemini-3-pro-image-preview). UI at http://localhost:7891
# Requires GOOGLE_GEMINI_API_KEY in .env or environment.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv
source "$PROJECT_ROOT/venv/bin/activate"

# Load env (GOOGLE_GEMINI_API_KEY for Gemini)
if [ -f "$PROJECT_ROOT/.env" ]; then
  source "$PROJECT_ROOT/.env"
fi

exec python "$PROJECT_ROOT/scripts/nano_banana_pro.py" "$@"
