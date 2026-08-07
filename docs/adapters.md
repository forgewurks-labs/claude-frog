# Agent adapters

Contributor notes for the adapter seam: where the frog ends and the coding
agent hosting him begins. For user-facing setup, see the
[README](../README.md); this doc is about the internals. Everything here should
be verified against `claude_frog.py` before you rely on it — the code is the
source of truth.

## The seam

The frog is agent-agnostic. All he asks of the agent hosting him is:

1. **A token gauge fed from somewhere** — a payload his `tap` mode can read
   token usage (and, if available, the context-window size) out of.
2. **Four lifecycle moments** — session starts, prompt lands, turn ends,
   session ends — delivered to his `hook` mode.
3. **A config file he can wire himself into** — so `install-settings` /
   `uninstall-settings` / `doctor` can add, remove, and verify the wiring.

Everything that knows one agent *specifically* — its payload shapes, its hook
event names, its settings-file location and schema — lives in an
`AgentAdapter` subclass in the "Agent adapters" section of `claude_frog.py`.
Nothing outside that section knows any of those facts. Supporting a new agent
means writing a new adapter and adding it to the `ADAPTERS` registry, not
sweeping through the file.

The seam stays this small because the frog **never reads transcripts**: all
state arrives through two doorways (the statusline payload and the hook
payloads), and both doorways go through the adapter.

## The interface

`AgentAdapter` (the abstract base — it *is* the contract):

| Member | What it answers |
| --- | --- |
| `name` | Registry key (`"claude-code"`). |
| `HOOK_EVENTS` | Native event names the installer wires up. |
| `detect()` | Does this agent appear to be present on this machine? |
| `settings_path(override)` | Where the agent's own config file lives. |
| `hook_event(payload)` | The native event name out of a hook payload. |
| `canonical_event(name)` | Native event name → canonical lifecycle event. |
| `session_id(payload)` | The session id out of any payload (hook or statusline). |
| `extract_tokens(payload)` | Token usage out of the statusline payload, or `None`. |
| `extract_window_size(payload)` | Context-window size out of the same payload, or `None`. |
| `install_wiring(data, tap_cmd, hook_cmd, is_ours, statusline)` | Merge the frog's wiring into the agent's *parsed* settings. |
| `uninstall_wiring(data, is_ours)` | Remove only the frog's wiring, reversibly. |
| `wiring_status(data, is_ours)` | `(statusline_ok, foreign_statusline, hooks_ok)` for `doctor`. |

Conventions that keep the seam honest:

- **Canonical lifecycle events.** `mode_hook` dispatches on
  `"session-start"` / `"prompt"` / `"stop"` / `"session-end"` — never on native
  names. The adapter owns what its native names *mean*; `mode_hook` owns what
  the frog *does* about them.
- **The `is_ours` predicate.** The settings-surgery methods take the frog's own
  "is this command mine?" test (`_is_frog_cmd`) as a parameter. The adapter
  carries the *schema* knowledge; the frog carries its own identity. That's
  also why `install_wiring` returns `(changed, notes)` for the caller to print
  and raises `ValueError` instead of exiting — file I/O, backups, and process
  exit codes belong to the mode functions, which are shared across adapters.
- **Parsed data in, parsed data out.** The wiring methods mutate an
  already-parsed settings dict. Reading, JSON-parse failure handling, backup,
  and atomic write stay in `mode_install_settings` / `mode_uninstall_settings`
  / `mode_doctor`, written once.
- **Never-crash still holds.** The tap and hook paths wrap adapter calls in the
  same never-crash / always-exit-0 discipline as everything else; an adapter
  bug must never break the user's prompt.

## Adapter #1: Claude Code

`ClaudeCodeAdapter` is the reference implementation. The three facts it
encapsulates:

- **Token payload** — Claude Code hands token usage only to the statusLine
  command (hooks are token-blind). The parser is deliberately multi-key
  defensive: `used_percentage` × window size, then `total_input_tokens` /
  `used_tokens`, then summing `current_usage` — so a payload-schema drift
  degrades to `None` (a green, calm frog) instead of a crash.
- **Hook events** — `SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`
  (plus `Cleanup` accepted as a session-end synonym from legacy invocations).
- **Settings schema** — `~/.claude/settings.json` (honoring
  `CLAUDE_CONFIG_DIR`): exactly one `statusLine` command, and hooks as
  `{event: [{"hooks": [{"type", "command"}, …]}, …]}`. Before the seam, this
  schema was known in three places (install / uninstall / doctor); now it is
  known once.

`FROG_HOOK_EVENTS` remains as a module-level re-export of
`ClaudeCodeAdapter.HOOK_EVENTS` — tests and external callers use that name.

`detect_agent()` walks the registry and falls back to Claude Code. With one
registered adapter it always lands there; the function exists so adapter #2 is
a class plus a registry entry. There is deliberately **no** `--agent` CLI flag
yet: a flag with exactly one valid value is dead surface. It arrives with the
first second adapter, chosen by `detect()` by default and overridable
explicitly.

## Recorded decision: the single stdlib file survives (2026-08-07, FWL-547)

The question the extraction forced: does "everything ships in
`claude_frog.py`, one stdlib-only Python file" survive the adapter seam, or is
this the moment to split into a package (`claude_frog/adapters/…`)?

**Decision: the single sectioned file stays. The seam is the class interface,
not a file boundary.**

Why:

- **The file's absolute path is the install story.** The settings.json
  statusLine and hook command strings, the tmux keybind line, the pane spawn
  command, and the shell launcher all bake in `<python> <abspath>/claude_frog.py
  <mode>`. One file means wiring is a path, with nothing to resolve, package,
  or version at hook time. A package split would force either an installed
  console script (a different install story) or `sys.path` games inside hooks —
  the exact paths that must never crash.
- **The decoupling a split would buy, the interface already buys.** Adapter #2
  is written against `AgentAdapter` and registered; whether it lives in the
  same file or its own changes nothing about how entangled it is.
- **The never-crash invariant is easiest to audit in one artifact** with one
  import list (stdlib only, checked at a glance).

This is a decision about *this* size and shape of program, not a permanent
vow. The named revisit trigger: **when a second adapter actually lands** (real
requirements beat speculated ones), or if the file's growth starts hurting
navigation in practice, revisit a package split — with the constraint that
whatever ships must keep the one-path wiring story (e.g. a build step that
stitches or zipapps back to a single artifact). Change it via a recorded
decision here and in `AGENTS.md`, not incidentally.
