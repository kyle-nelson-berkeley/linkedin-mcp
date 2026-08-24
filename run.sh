#!/usr/bin/env bash
# =============================================================================
# run.sh — venv-bootstrapping launcher for the linkedin MCP server (stdio).
#
# Register in an MCP client as:  bash "/path/to/linkedin-mcp/run.sh"
# IDEMPOTENT: creates .venv once; pip install is stamp-gated and re-runs only
# when requirements.txt changes.
#
# NOTE: MCP stdio servers must not write to stdout outside the protocol —
# all bootstrap chatter goes to stderr.
# =============================================================================
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
STAMP="$VENV/.deps-stamp"

if [ ! -x "$VENV/bin/python" ]; then
  echo "linkedin-mcp: creating venv..." >&2
  python3 -m venv "$VENV" >&2
fi

if [ ! -f "$STAMP" ] || [ "$DIR/requirements.txt" -nt "$STAMP" ]; then
  echo "linkedin-mcp: installing deps..." >&2
  "$VENV/bin/pip" install -q -r "$DIR/requirements.txt" >&2
  touch "$STAMP"
fi

cd "$DIR"
exec "$VENV/bin/python" -m linkedin_mcp "$@"
