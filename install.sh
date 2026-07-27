#!/usr/bin/env bash
# Claude Frog — one-time installer.
#
# By DEFAULT it sets up the whole thing so you actually SEE him:
#   1. the `claude <THEME>` launcher (a source line in your shell rc), and
#   2. the token feed (a silent statusLine `tap`) + dance hooks (merged into
#      ~/.claude/settings.json, preserving everything already there and backing
#      the file up first). The frog himself dances in a tmux pane.
# It shows you exactly what it will touch and asks once before editing.
#
# Usage:
#     ./install.sh                 # the full frog (launcher + tap + hooks + keybind)
#     ./install.sh --minimal       # ONLY the `claude <THEME>` launcher, no settings edits
#     ./install.sh --yes           # don't prompt — assume yes (for automation)
#     ./install.sh --no-wizard     # skip the pick-your-frog questions
#     ./install.sh --uninstall     # remove everything this installer added
#     ./install.sh ~/.bashrc       # force which rc file to write
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
WRAPPER="$ROOT/install/claude-theme.sh"
FROG="$ROOT/claude_frog.py"
MARKER="claude-frog theme launcher"   # keep in sync with MARKER in claude_frog.py

MINIMAL=0
ASSUME_YES=0
UNINSTALL=0
NO_WIZARD=0
RC=""
for a in "$@"; do
  case "$a" in
    --minimal)      MINIMAL=1 ;;
    --tap)          : ;;   # back-compat no-op: tap is now the only statusLine mode
    --yes|-y)       ASSUME_YES=1; NO_WIZARD=1 ;;   # automation can't answer questions
    --no-wizard)    NO_WIZARD=1 ;;
    --uninstall)    UNINSTALL=1 ;;
    --with-frog)    : ;;   # back-compat no-op: the full frog is now the default
    -h|--help)
      sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
  # 3. the tmux keybind.
  python3 "$FROG" uninstall-keybind
  # 4. your saved settings (theme/layout/flora).
  CFG="$(python3 "$FROG" config-path 2>/dev/null || true)"
  if [ -n "$CFG" ] && [ -f "$CFG" ]; then
    rm -f "$CFG"
    echo "   - settings removed ($CFG)"
  else
    echo "   • no settings file to remove"
  fi
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

TMUX_CONF="$(python3 "$FROG" tmux-conf-path 2>/dev/null || echo "$HOME/.tmux.conf")"

echo "🐸 Claude Frog will:"
echo "   • add the launcher line to  $RC        (so \`claude SEGA\` works)"
if [ "$MINIMAL" != 1 ]; then
  echo "   • wire the token feed (a silent statusLine tap) + dance hooks into  $SETTINGS"
  echo "     (preserves everything already there; backs it up first)"
  echo "   • add the  prefix + F  toggle keybind to  $TMUX_CONF"
  [ "$NO_WIZARD" != 1 ] && echo "   • ask you a couple of questions to pick your frog's look"
  if [ "$in_tmux" = 1 ]; then
    echo "   • you're in tmux → you get the dancing pane frog 🕺"
  else
    echo "   • you're not in tmux → the frog dances in a tmux pane, so you"
    echo "     won't see him yet. Add tmux + WezTerm for the show (see README)."
  fi
fi
echo
confirm "Proceed?" || { echo "No changes made."; exit 0; }
echo

# We track what ACTUALLY changed so the receipt reports the truth on a re-run.
LAUNCHER_CHANGED=0
SETTINGS_CHANGED=0
KEYBIND_CHANGED=0

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

# --- 2. the frog itself (token feed + hooks), unless --minimal -------------- #
if [ "$MINIMAL" != 1 ]; then
  echo
  echo "🐸 Wiring up the frog (token feed + hooks)…"
  # Capture so we can tell "wired something" from an idempotent no-op.
  out="$(python3 "$FROG" install-settings)"
  printf '%s\n' "$out"
  case "$out" in *"Wired the frog"*) SETTINGS_CHANGED=1 ;; esac

  # --- 2b. the tmux toggle keybind --------------------------------------- #
  # This used to be a snippet you were told to hand-paste with the path swapped
  # in yourself, so in practice almost nobody had the keybind we advertised.
  kb="$(python3 "$FROG" install-keybind)"
  printf '%s\n' "$kb"
  case "$kb" in *"added to"*) KEYBIND_CHANGED=1 ;; esac
fi

# --- 2c. pick your frog ----------------------------------------------------- #
if [ "$MINIMAL" != 1 ] && [ "$NO_WIZARD" != 1 ]; then
  python3 "$FROG" setup || true
fi

# --- 3. prove it worked ----------------------------------------------------- #
echo
DOCTOR_ARGS=(doctor --rc "$RC")
[ "$MINIMAL" = 1 ] && DOCTOR_ARGS+=(--minimal)
python3 "$FROG" "${DOCTOR_ARGS[@]}" || true

# --- 4. the receipt + the one unavoidable step ------------------------------ #
echo
echo "────────────────────────────────────────────────────────────"
if [ "$LAUNCHER_CHANGED" = 1 ] || [ "$SETTINGS_CHANGED" = 1 ] || [ "$KEYBIND_CHANGED" = 1 ]; then
  echo "What I changed:"
  [ "$LAUNCHER_CHANGED" = 1 ] && \
    echo "   • $RC — added the launcher (look for the '$MARKER' comment)"
  if [ "$SETTINGS_CHANGED" = 1 ]; then
    echo "   • $SETTINGS — added the token feed (tap) + hooks"
    echo "     (your previous file is saved at $SETTINGS.bak)"
  fi
  [ "$KEYBIND_CHANGED" = 1 ] && \
    echo "   • $TMUX_CONF — added the prefix + F toggle keybind"
else
  echo "What I changed:  nothing — you were already set up (idempotent re-run)."
fi
echo "Undo any time:   $ROOT/install.sh --uninstall"
echo "────────────────────────────────────────────────────────────"
echo
echo "🐸 One last step (a shell can't reach into this terminal for you):"
echo "      close this terminal and open a new one"
echo "      — or run:  source \"$RC\""
echo "   then start a session:"
echo "      claude             # he wears whatever you picked"
echo "      claude SEGA        # or override the look for one session"
echo
echo "   Change your mind later — no dotfile editing:"
echo "      python3 $FROG config              # what he's using, and why"
echo "      python3 $FROG config theme snes   # change it"
