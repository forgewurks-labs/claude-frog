# Claude Frog — pick your theme when you start a session.
#
# Install (one-time): from the repo root run
#
#     ./install.sh
#
# which appends the `source` line below to your shell rc. Or add it by hand to
# ~/.zshrc / ~/.bashrc (works in both shells):
#
#     source /path/to/claude-frog/install/claude-theme.sh
#
# Then name a console as the FIRST word after `claude` and that session's frog
# wears it:
#
#     claude SNES      # smooth 16-bit frog     (also: nintendo, super)
#     claude SEGA      # dithered Genesis frog   (also: genesis, megadrive, md)
#     claude GBA       # mono Game Boy frog      (also: gameboy, "game boy", gb)
#     claude TERRARIA  # painterly indie frog    (also: relogic, terra, 32bit)
#
# Anything else behaves exactly like plain `claude`. And if you never name a
# theme, the frog stays on the default SNES theme.

# Locate claude_frog.py relative to THIS file (install/ -> repo root), so there's
# no path to hand-edit. Override by exporting CLAUDE_FROG before sourcing.
if [ -z "${CLAUDE_FROG:-}" ]; then
  if [ -n "${BASH_SOURCE:-}" ]; then
    _cf_self="${BASH_SOURCE[0]}"       # bash
  elif [ -n "${ZSH_VERSION:-}" ]; then
    _cf_self="${(%):-%x}"              # zsh: path of the file being sourced
  else
    _cf_self="$0"
  fi
  _cf_dir="$(cd "$(dirname "$_cf_self")/.." 2>/dev/null && pwd)"
  CLAUDE_FROG="$_cf_dir/claude_frog.py"
  unset _cf_self _cf_dir
fi

# The real Claude Code binary. Override if yours is named differently.
: "${CLAUDE_BIN:=claude}"

claude() {
  local theme=""
  # Only probe when the first arg could be a bare theme word — skip when there
  # are no args, or the first arg is a flag (so `claude -r`, `claude --help`,
  # and a bare `claude` never pay for the lookup, and all default to SNES).
  case "${1:-}" in
    "" | -*) : ;;
    *) theme="$(python3 "$CLAUDE_FROG" resolve-theme "$1" 2>/dev/null)" ;;
  esac

  if [ -n "$theme" ]; then
    shift  # consume the theme word; the rest is for Claude Code
    # Confirm in Claude pink, on stderr so it never pollutes piped output.
    printf '\033[38;2;240;156;188m🐸 Claude Frog: %s\033[0m\n' "$theme" >&2
    CLAUDE_FROG_THEME="$theme" command "$CLAUDE_BIN" "$@"
  else
    # No theme named -> leave the env untouched; the frog falls back to SNES
    # (or to whatever CLAUDE_FROG_THEME you exported yourself).
    command "$CLAUDE_BIN" "$@"
  fi
}
