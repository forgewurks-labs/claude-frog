#!/usr/bin/env bash
# Keep your own statusline AND keep the frog fed (and, if you want him, shown).
#
# Claude Code allows only ONE statusLine command, and that statusLine is the
# only surface it hands token usage to. The frog's `tap` reads that payload and
# publishes the token gauge for the dancing pane. With `statusline` set to
# `frog` it also prints a one-line frog + context gauge; with `off` (the
# default) it prints nothing at all.
#
# So if you already run your own statusline, point statusLine at THIS script:
# it taps the frog first, then renders your bar — on one line, frog first.
#
#   "statusLine": { "type": "command",
#                   "command": "/path/to/claude-frog/install/statusline-compose.sh" }
#
# Turn the visible frog on or off any time, without touching this file:
#   python3 /path/to/claude-frog/claude_frog.py config statusline frog
#   python3 /path/to/claude-frog/claude_frog.py config statusline off
#
# stdin (the Claude Code statusline JSON) can only be read once, so we capture
# it and feed the same payload to both. Everything is best-effort and never
# fails — a broken segment must not break your status bar.

set -o pipefail 2>/dev/null || true

FROG="/path/to/claude-frog/claude_frog.py"

# Set this to your existing statusline command (it receives the same JSON on
# stdin). Leave empty to let the frog have the bar to himself.
YOUR_STATUSLINE=""

payload="$(cat)"

# The frog: feeds the pane's gauge always, prints a segment only when you've set
# `config statusline frog`.
frog_seg="$(printf '%s' "$payload" | python3 "$FROG" tap 2>/dev/null || true)"

your_seg=""
if [ -n "$YOUR_STATUSLINE" ]; then
  your_seg="$(printf '%s' "$payload" | $YOUR_STATUSLINE 2>/dev/null || true)"
fi

# Join with a separator only when both sides actually produced something, so
# neither an absent frog nor an absent bar leaves stray padding behind.
if [ -n "$frog_seg" ] && [ -n "$your_seg" ]; then
  printf '%s  %s\n' "$frog_seg" "$your_seg"
elif [ -n "$frog_seg" ]; then
  printf '%s\n' "$frog_seg"
elif [ -n "$your_seg" ]; then
  printf '%s\n' "$your_seg"
fi

exit 0
