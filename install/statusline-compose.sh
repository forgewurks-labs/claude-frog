#!/usr/bin/env bash
# Keep your own statusline AND keep the frog's gauge fed.
#
# Claude Code allows only ONE statusLine command, and that statusLine is the
# only surface it hands token usage to. The frog's `tap` reads that payload and
# publishes the token gauge for the dancing pane — it prints nothing. So if you
# already run your own statusline, point statusLine at THIS script: it taps the
# frog first, then renders your bar exactly as before.
#
#   "statusLine": { "type": "command",
#                   "command": "/path/to/claude-frog/install/statusline-compose.sh" }
#
# stdin (the Claude Code statusline JSON) can only be read once, so we capture
# it and feed the same payload to both. Everything is best-effort and never
# fails — a broken segment must not break your status bar.

set -o pipefail 2>/dev/null || true

FROG="/path/to/claude-frog/claude_frog.py"

# Set this to your existing statusline command (it receives the same JSON on
# stdin). Leave empty for a bare tap: the gauge gets fed, the bar stays empty.
YOUR_STATUSLINE=""

payload="$(cat)"

# feed the frog's token gauge (prints nothing)
printf '%s' "$payload" | python3 "$FROG" tap 2>/dev/null || true

# your bar owns the statusline
if [ -n "$YOUR_STATUSLINE" ]; then
  printf '%s' "$payload" | $YOUR_STATUSLINE 2>/dev/null || true
fi

exit 0
