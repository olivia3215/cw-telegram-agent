#!/bin/bash
#
# Start the Image generator web utility for generating images with Gemini
# (Nano Banana, Nano Banana Pro, Nano Banana 2) or Grok. UI at http://localhost:7891
# Requires GOOGLE_GEMINI_API_KEY in .env or environment (and GROK_API_KEY for Grok).
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

exec python "$PROJECT_ROOT/scripts/image_generator.py" "$@"
