#!/usr/bin/env bash
# Claude Frog — remote one-command bootstrap.
#
#   curl -fsSL https://raw.githubusercontent.com/forgewurks-labs/claude-frog/main/bootstrap.sh | bash
#
# It clones (or updates) the repo to a stable home and hands off to install.sh,
# which shows you exactly what it will touch and asks once before editing.
# Everything after the pipe is forwarded to install.sh, e.g.:
#
#   curl -fsSL …/bootstrap.sh | bash -s -- --minimal
#   curl -fsSL …/bootstrap.sh | bash -s -- --tap
#
# Prefer to read before you run? The transparent equivalent is:
#
#   git clone https://github.com/forgewurks-labs/claude-frog.git ~/.claude-frog
#   ~/.claude-frog/install.sh
set -euo pipefail

REPO="${CLAUDE_FROG_REPO:-https://github.com/forgewurks-labs/claude-frog.git}"
DIR="${CLAUDE_FROG_HOME:-$HOME/.claude-frog}"

command -v git >/dev/null 2>&1 || {
  echo "error: git is required for the one-command install." >&2
  echo "       Install git, or clone the repo by hand and run its install.sh." >&2
  exit 1
}

if [ -d "$DIR/.git" ]; then
  echo "🐸 Updating Claude Frog in ${DIR}…"
  git -C "$DIR" pull --ff-only --quiet || {
    echo "warn: couldn't fast-forward $DIR; using what's already there." >&2
  }
else
  echo "🐸 Fetching Claude Frog into ${DIR}…"
  git clone --depth 1 --quiet "$REPO" "$DIR"
fi

# Hand off. exec keeps our stdin, and install.sh reads the y/N from /dev/tty,
# so the confirm prompt still works even though this script arrived via a pipe.
exec "$DIR/install.sh" "$@"
