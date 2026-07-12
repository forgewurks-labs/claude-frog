# Claude Frog — pick your theme when you start a session.
#
# Source this from ~/.zshrc or ~/.bashrc (works in both):
#
#   source /path/to/claude-frog/install/claude-theme.sh
#
# Then name a console as the FIRST word after `claude` and that session's frog
# wears it:
#
#   claude SNES      # smooth 16-bit frog   (also: nintendo, super)
#   claude SEGA      # dithered Genesis frog (also: genesis, megadrive, md)
#   claude GBA       # mono Game Boy frog    (also: gameboy, "game boy", gb)
#
# Anything else behaves exactly like plain `claude` — the wrapper only steps in
# when that first word actually names a theme, and passes every other argument
# straight through. The choice is scoped to that one launch (nothing lingers in
# your shell).

# Absolute path to claude_frog.py — replace with your real path.
CLAUDE_FROG="/path/to/claude-frog/claude_frog.py"

# The real Claude Code binary. Override if yours is named differently.
: "${CLAUDE_BIN:=claude}"

claude() {
  local theme=""
  # Only probe when the first arg could be a bare theme word — skip when there
  # are no args, or the first arg is a flag (so `claude -r`, `claude --help`,
  # and a bare `claude` never pay for the lookup).
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
    command "$CLAUDE_BIN" "$@"
  fi
}
