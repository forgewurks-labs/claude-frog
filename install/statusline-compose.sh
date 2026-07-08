#!/usr/bin/env bash
# Compose an existing statusline with the Claude Frog mood-frog.
#
# Claude Code only allows ONE statusLine command, so if you already run your own
# statusline, point statusLine at THIS script instead of at either tool:
#
#   "statusLine": { "type": "command",
#                   "command": "/path/to/claude-frog/install/statusline-compose.sh" }
#
# Layout: your existing statusline on top, the 3-row frog beneath it.
#
# stdin (the Claude Code statusline JSON) can only be read once, so we capture
# it and feed the same payload to both renderers. Everything is best-effort and
# never fails — a broken segment must not break your status bar.

set -o pipefail 2>/dev/null || true

FROG="/path/to/claude-frog/claude_frog.py"

# Set this to whatever your existing statusline command is (it will receive the
# same JSON on stdin). Leave empty to show only the frog.
YOUR_STATUSLINE=""

payload="$(cat)"

# --- top line(s): your existing statusline, if any ---
if [ -n "$YOUR_STATUSLINE" ]; then
  printf '%s' "$payload" | $YOUR_STATUSLINE 2>/dev/null || true
  printf '\n'
fi

# --- below: the frog ---
printf '%s' "$payload" | python3 "$FROG" statusline 2>/dev/null || true

exit 0
