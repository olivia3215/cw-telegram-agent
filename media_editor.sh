#!/bin/bash

# Wrapper script for Media Editor
# Delegates to the actual script in scripts/ directory

exec "$(dirname "$0")/scripts/media_editor.sh" "$@"
