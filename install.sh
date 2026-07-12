#!/usr/bin/env bash
# Claude Frog — one-time installer for the `claude <THEME>` launcher.
#
# Appends a line to your shell rc that sources install/claude-theme.sh, so you
# can start themed sessions with `claude SNES` / `claude SEGA` / `claude GBA`.
# Idempotent: safe to run twice. Does NOT touch your Claude Code settings — see
# the README for wiring up the frog's statusline / tmux pane themselves.
#
# Usage:
#     ./install.sh              # auto-detect your shell rc (~/.zshrc, ~/.bashrc)
#     ./install.sh ~/.bashrc    # or name the rc file explicitly
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
WRAPPER="$ROOT/install/claude-theme.sh"
MARKER="claude-frog theme launcher"

if [ ! -f "$WRAPPER" ]; then
  echo "error: could not find $WRAPPER" >&2
  exit 1
fi

detect_rc() {
  case "${SHELL##*/}" in
    zsh)  printf '%s\n' "${ZDOTDIR:-$HOME}/.zshrc" ;;
    bash)
      if [ -f "$HOME/.bashrc" ]; then printf '%s\n' "$HOME/.bashrc"
      else printf '%s\n' "$HOME/.bash_profile"; fi ;;
    *)    printf '%s\n' "$HOME/.profile" ;;
  esac
}

RC="${1:-$(detect_rc)}"

if [ -f "$RC" ] && grep -qF "$MARKER" "$RC"; then
  echo "✅ Already installed in $RC — nothing to do."
else
  {
    printf '\n# %s\n' "$MARKER"
    printf 'source "%s"\n' "$WRAPPER"
  } >> "$RC"
  echo "✅ Added the Claude Frog launcher to $RC"
fi

echo
echo "🐸 Done. Start a new terminal (or run:  source \"$RC\"), then try:"
echo "       claude GBA"
echo
echo "   No theme named -> the frog defaults to SNES."
echo "   (To see the frog at all, install its statusline / hooks — see README.)"
