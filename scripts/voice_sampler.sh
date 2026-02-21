#!/bin/bash
#
# Start the voice sampler web utility for experimenting with ElevenLabs TTS.
# UI at http://localhost:7890
# Requires ELEVENLABS_API_KEY in .env or environment.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv
source "$PROJECT_ROOT/venv/bin/activate"

# Load env (ELEVENLABS_API_KEY for TTS)
if [ -f "$PROJECT_ROOT/.env" ]; then
  source "$PROJECT_ROOT/.env"
fi

exec python "$PROJECT_ROOT/scripts/voice_sampler.py" "$@"
