#!/usr/bin/env bash
# Claude Frog — one-time installer.
#
# Always: installs the `claude <THEME>` launcher by appending a `source` line to
# your shell rc (idempotent, safe to re-run, touches nothing else).
#
# With --with-frog: also wires the frog's statusline + hooks into
# ~/.claude/settings.json so you actually SEE him — preserving everything already
# there and backing the file up first.
#
# Usage:
#     ./install.sh                     # launcher only
#     ./install.sh --with-frog         # launcher + statusline frog + hooks
#     ./install.sh --with-frog --tap   # ...but keep your status bar (silent tap)
#     ./install.sh ~/.bashrc           # force which rc file to write
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
WRAPPER="$ROOT/install/claude-theme.sh"
FROG="$ROOT/claude_frog.py"
MARKER="claude-frog theme launcher"

WITH_FROG=0
SL_MODE="statusline"
RC=""
for a in "$@"; do
  case "$a" in
    --with-frog) WITH_FROG=1 ;;
    --tap)       SL_MODE="tap" ;;
    -h|--help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $a  (try --help)" >&2; exit 2 ;;
    *)  RC="$a" ;;
  esac
done

[ -f "$WRAPPER" ] || { echo "error: could not find $WRAPPER" >&2; exit 1; }

detect_rc() {
  case "${SHELL##*/}" in
    zsh)  printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash)
      if [ -f "$HOME/.bashrc" ]; then printf '%s\n' "$HOME/.bashrc"
      else printf '%s\n' "$HOME/.bash_profile"; fi ;;
    *)    printf '%s\n' "$HOME/.profile" ;;
  esac
}
RC="${RC:-$(detect_rc)}"

# --- 1. the launcher -------------------------------------------------------- #
if [ -f "$RC" ] && grep -qF "$MARKER" "$RC"; then
  echo "✅ Launcher already installed in $RC — nothing to do."
else
  {
    printf '\n# %s\n' "$MARKER"
    printf 'source "%s"\n' "$WRAPPER"
  } >> "$RC"
  echo "✅ Added the Claude Frog launcher to $RC"
fi

# --- 2. optional: the frog itself (statusline + hooks) ---------------------- #
if [ "$WITH_FROG" = 1 ]; then
  echo
  echo "🐸 Wiring up the frog (statusline + hooks)…"
  python3 "$FROG" install-settings --statusline-mode "$SL_MODE"
fi

echo
echo "🐸 Done. Open a new terminal (or run:  source \"$RC\"), then try:"
echo "       claude GBA        # no theme named → defaults to SNES"
if [ "$WITH_FROG" != 1 ]; then
  echo
  echo "   Want to actually SEE the frog? Re-run with --with-frog to wire up"
  echo "   his statusline + hooks, or follow the README."
fi
