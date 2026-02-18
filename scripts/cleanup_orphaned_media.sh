#!/bin/bash
#
# Clean up orphaned media (files without metadata, metadata without files).
# DRY-RUN by default; pass --apply to delete.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Activate venv
source "$PROJECT_ROOT/venv/bin/activate"

# Load env (defines CINDY_AGENT_STATE_DIR, CINDY_AGENT_CONFIG_PATH, DB creds, etc.)
if [ -f "$PROJECT_ROOT/.env" ]; then
  source "$PROJECT_ROOT/.env"
fi

exec python "$PROJECT_ROOT/scripts/cleanup_orphaned_media.py" "$@"
