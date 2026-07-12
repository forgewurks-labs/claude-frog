#!/usr/bin/env bash
# Claude Frog — one-time installer.
#
# By DEFAULT it sets up the whole thing so you actually SEE him:
#   1. the `claude <THEME>` launcher (a source line in your shell rc), and
#   2. the statusline frog + dance hooks (merged into ~/.claude/settings.json,
#      preserving everything already there and backing the file up first).
# It shows you exactly what it will touch and asks once before editing.
#
# Usage:
#     ./install.sh                 # the full frog (launcher + statusline + hooks)
#     ./install.sh --minimal       # ONLY the `claude <THEME>` launcher, no settings edits
#     ./install.sh --tap           # full, but keep your own status bar (silent tap)
#     ./install.sh --yes           # don't prompt — assume yes (for automation)
#     ./install.sh --uninstall     # remove everything this installer added
#     ./install.sh ~/.bashrc       # force which rc file to write
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
WRAPPER="$ROOT/install/claude-theme.sh"
FROG="$ROOT/claude_frog.py"
MARKER="claude-frog theme launcher"   # keep in sync with MARKER in claude_frog.py

MINIMAL=0
SL_MODE="statusline"
ASSUME_YES=0
UNINSTALL=0
RC=""
for a in "$@"; do
  case "$a" in
    --minimal)      MINIMAL=1 ;;
    --tap)          SL_MODE="tap" ;;
    --yes|-y)       ASSUME_YES=1 ;;
    --uninstall)    UNINSTALL=1 ;;
    --with-frog)    : ;;   # back-compat no-op: the full frog is now the default
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Ask a yes/no on the controlling terminal. Works even when this script itself
# is piped to bash (curl … | bash) — that puts the *script* on stdin, so we read
# the human from /dev/tty instead. No tty (CI, non-interactive) → assume yes.
confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  local reply
  if [ -r /dev/tty ]; then
    printf '%s [Y/n] ' "$1" > /dev/tty
    read -r reply < /dev/tty || reply=""
  else
    return 0
  fi
  case "$reply" in n|N|no|NO|No) return 1 ;; *) return 0 ;; esac
}

# --------------------------------------------------------------------------- #
# Uninstall                                                                    #
# --------------------------------------------------------------------------- #
if [ "$UNINSTALL" = 1 ]; then
  echo "🐸 Removing Claude Frog…"
  # 1. the launcher line (marker comment + the following source line).
  if [ -f "$RC" ] && grep -qF "$MARKER" "$RC"; then
    tmp="$(mktemp)"
    # Drop the marker comment line and the single line right after it.
    awk -v m="$MARKER" '
      idx { idx=0; next }                 # skip the source line after the marker
      index($0, m) { idx=1; next }        # skip the marker comment itself
      { print }
    ' "$RC" > "$tmp"
    cp "$RC" "$RC.bak"
    mv "$tmp" "$RC"
    echo "   - launcher removed from $RC  (backup: $RC.bak)"
  else
    echo "   • no launcher line found in $RC"
  fi
  # 2. the settings.json wiring.
  python3 "$FROG" uninstall-settings
  echo
  echo "Done. The files above are the only things Claude Frog ever touched."
  echo "Open a new terminal for the shell change to take effect."
  exit 0
fi

# --------------------------------------------------------------------------- #
# Install — show the plan, ask once, then do it                               #
# --------------------------------------------------------------------------- #
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
in_tmux=0; [ -n "${TMUX:-}" ] && in_tmux=1

echo "🐸 Claude Frog will:"
echo "   • add the launcher line to  $RC        (so \`claude SEGA\` works)"
if [ "$MINIMAL" != 1 ]; then
  echo "   • wire the statusline frog + hooks into  $SETTINGS"
  echo "     (preserves everything already there; backs it up first)"
  if [ "$in_tmux" = 1 ]; then
    echo "   • you're in tmux → you also get the dancing pane frog 🕺"
  else
    echo "   • you're not in tmux → you'll get the statusline frog."
    echo "     Want the full dancing pane too? Add tmux + WezTerm (see README)."
  fi
fi
echo
confirm "Proceed?" || { echo "No changes made."; exit 0; }
echo

# We track what ACTUALLY changed so the receipt reports the truth on a re-run.
LAUNCHER_CHANGED=0
SETTINGS_CHANGED=0

# --- 1. the launcher -------------------------------------------------------- #
if [ -f "$RC" ] && grep -qF "$MARKER" "$RC"; then
  echo "✅ Launcher already installed in $RC — nothing to do."
else
  {
    printf '\n# %s\n' "$MARKER"
    printf 'source "%s"\n' "$WRAPPER"
  } >> "$RC"
  echo "✅ Added the Claude Frog launcher to $RC"
  LAUNCHER_CHANGED=1
fi

# --- 2. the frog itself (statusline + hooks), unless --minimal -------------- #
if [ "$MINIMAL" != 1 ]; then
  echo
  echo "🐸 Wiring up the frog (statusline + hooks)…"
  # Capture so we can tell "wired something" from an idempotent no-op.
  out="$(python3 "$FROG" install-settings --statusline-mode "$SL_MODE")"
  printf '%s\n' "$out"
  case "$out" in *"Wired the frog"*) SETTINGS_CHANGED=1 ;; esac
fi

# --- 3. prove it worked ----------------------------------------------------- #
echo
DOCTOR_ARGS=(doctor --rc "$RC")
[ "$MINIMAL" = 1 ] && DOCTOR_ARGS+=(--minimal)
python3 "$FROG" "${DOCTOR_ARGS[@]}" || true

# --- 4. the receipt + the one unavoidable step ------------------------------ #
echo
echo "────────────────────────────────────────────────────────────"
if [ "$LAUNCHER_CHANGED" = 1 ] || [ "$SETTINGS_CHANGED" = 1 ]; then
  echo "What I changed:"
  [ "$LAUNCHER_CHANGED" = 1 ] && \
    echo "   • $RC — added the launcher (look for the '$MARKER' comment)"
  if [ "$SETTINGS_CHANGED" = 1 ]; then
    echo "   • $SETTINGS — added the statusline frog + hooks"
    echo "     (your previous file is saved at $SETTINGS.bak)"
  fi
else
  echo "What I changed:  nothing — you were already set up (idempotent re-run)."
fi
echo "Undo any time:   $ROOT/install.sh --uninstall"
echo "────────────────────────────────────────────────────────────"
echo
echo "🐸 One last step (a shell can't reach into this terminal for you):"
echo "      close this terminal and open a new one"
echo "      — or run:  source \"$RC\""
echo "   then start a session with a theme:"
echo "      claude SEGA        # or SNES, GBA — name none and he wears SNES"
