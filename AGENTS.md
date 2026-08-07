# claude-frog

A pixel **Clyde-Frog terminal mascot** that dances while Claude Code is thinking and quietly
warns when you're burning too much context — plus an honest context/token gauge.

Repo context for coding agents (any brand — this file is canonical; `CLAUDE.md` imports it):

- Everything ships in `claude_frog.py`, one stdlib-only Python file — no third-party
  dependencies, in code or tests. The constraint is deliberate; change it via a recorded
  decision, not incidentally (the standing decision + revisit trigger: `docs/adapters.md`).
- Load-bearing invariants: **one frog per tmux window** (lock file + refcount across
  sessions), and the tap/hook paths **never crash and always exit 0** — a broken frog must
  never break the user's prompt.
- Everything agent-specific (payload shapes, hook event names, settings-file schema) lives
  behind the `AgentAdapter` seam in the "Agent adapters" section — nothing outside it may
  know those facts. Claude Code is the reference adapter; opencode is adapter #2 (wired
  via a generated plugin file, selected with `--agent opencode`); see `docs/adapters.md`.
- Tests: `python3 -m unittest discover -s tests`. CI runs the same plus a byte-compile gate.
- Design notes: `docs/themes.md` (themes + launcher architecture), `docs/adapters.md`
  (the agent-adapter seam), and the README's "How it works" material.
