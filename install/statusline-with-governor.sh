#!/usr/bin/env bash
# Compose an existing statusline with the Claude Frog mood-frog.
#
# Claude Code only allows ONE statusLine command, so point statusLine at THIS
# script instead of at either tool directly:
#
#   "statusLine": { "type": "command",
#                   "command": "/path/to/claude-frog/install/statusline-with-governor.sh" }
#
# Layout: your gauge on top, the 3-row frog beneath it.
#
# stdin (the Claude Code statusline JSON) can only be read once, so we capture
# it and feed the same payload to both renderers. Everything is best-effort and
# never fails — a broken segment must not break your status bar.

set -o pipefail 2>/dev/null || true

FROG="/path/to/claude-frog/claude_frog.py"

payload="$(cat)"

# --- top line: your existing gauge (example: agent-governor's quota gauge) ---
printf '%s' "$payload" | agent-governor-statusline 2>/dev/null || true
printf '\n'

# --- below: the frog ---
printf '%s' "$payload" | python3 "$FROG" statusline 2>/dev/null || true

exit 0
