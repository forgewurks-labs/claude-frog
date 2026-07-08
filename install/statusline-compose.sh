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

# "statusline" draws the 3-row mood frog here in your status bar.
# "tap" draws nothing — use it for a pane-only setup where the frog dances in
# tmux and you want your status bar left alone. Either way the token gauge gets
# published, because the statusline is the only place Claude Code reveals it.
FROG_MODE="statusline"

payload="$(cat)"

# --- top line(s): your existing statusline, if any ---
if [ -n "$YOUR_STATUSLINE" ] && [ "$FROG_MODE" != "tap" ]; then
  printf '%s' "$payload" | $YOUR_STATUSLINE 2>/dev/null || true
  printf '\n'
fi

# --- below: the frog (silent when FROG_MODE=tap) ---
printf '%s' "$payload" | python3 "$FROG" "$FROG_MODE" 2>/dev/null || true

# in tap mode nothing has been printed yet, so your statusline owns the bar
if [ -n "$YOUR_STATUSLINE" ] && [ "$FROG_MODE" = "tap" ]; then
  printf '%s' "$payload" | $YOUR_STATUSLINE 2>/dev/null || true
fi

exit 0
