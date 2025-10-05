#!/bin/bash

# Wrapper script for Agent Server
# Delegates to the actual script in scripts/ directory

exec "$(dirname "$0")/scripts/run.sh" "$@"
