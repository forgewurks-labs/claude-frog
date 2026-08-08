# Agent adapters

Contributor notes for the adapter seam: where the frog ends and the coding
agent hosting him begins. (The sibling seam facing the terminal multiplexer —
where his *pane* lives — is [surfaces.md](surfaces.md).) For user-facing
setup, see the [README](../README.md); this doc is about the internals. Everything here should
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
| `display` | The agent's name as humans write it (`"Claude Code"`). |
| `HOOK_EVENTS` | Native event names the installer wires up. |
| `USES_SHELL_LAUNCHER` | Does the `claude <THEME>` shell-launcher story apply? (`doctor` skips that check when it doesn't.) |
| `INSTALL_HINT` | What `doctor` tells the user to run when wiring is missing. |
| `detect()` | Does this agent appear to be present on this machine? |
| `settings_path(override)` | Where the agent's wiring artifact lives. |
| `hook_event(payload)` | The native event name out of a hook payload. |
| `canonical_event(name)` | Native event name → canonical lifecycle event. |
| `session_id(payload)` | The session id out of any payload (hook or statusline). |
| `extract_tokens(payload)` | Token usage out of the statusline payload, or `None`. |
| `extract_window_size(payload)` | Context-window size out of the same payload, or `None`. |
| `parse_settings(text)` | Artifact text (`None` if absent) → the parsed form the wiring methods take. Default: the JSON dance, `ValueError` on unhonorable text. |
| `serialize_settings(data)` | Parsed form → artifact text — or `None`, meaning "the artifact should be *removed*". Default: pretty-printed JSON. |
| `install_wiring(data, tap_cmd, hook_cmd, is_ours, statusline)` | Merge the frog's wiring into the agent's *parsed* settings. |
| `uninstall_wiring(data, is_ours)` | Remove only the frog's wiring, reversibly. |
| `wiring_status(data, is_ours)` | `(statusline_ok, foreign_statusline, hooks_ok)` for `doctor`. |

`parse_settings` / `serialize_settings` exist because adapter #2 proved the
wiring artifact isn't always a JSON file to merge into — opencode's is a JS
plugin file the frog owns outright. The mode functions still own all file
I/O (read, backup, atomic write, and now removal when serialize says `None`);
the adapter owns what the bytes *mean*.

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

`detect_agent()` walks the registry (Claude Code first, so a machine with
both lands there) and falls back to Claude Code on an empty machine. The
`--agent` flag arrived with adapter #2, exactly as planned: every mode takes
`--agent <name>` to pin the adapter for that invocation, and the commands an
installer bakes into an agent's wiring carry the pin (`--agent opencode`) so
a wired invocation always lands on the adapter that wrote it, whatever
detection would say. An unknown name is a loud exit-2 error on explicit modes
(`install-settings`, `doctor`, …) but falls back to detection on the tap/hook
paths, which never crash.

## Adapter #2: opencode

`OpencodeAdapter` is the seam's first consumer beyond the reference
implementation (verified against `anomalyco/opencode` 1.18.x, 2026-08). The
facts it encapsulates:

- **Wiring artifact** — opencode has *no statusline and no shell-command hook
  schema*; its extension surface is a JS plugin (an ES module auto-loaded
  from `~/.config/opencode/plugin{,s}/`, run in-process under Bun). So the
  frog's wiring is **one generated plugin file the adapter owns outright**:
  `install-settings --agent opencode` writes `claude-frog.js` (refusing to
  clobber a foreign file at that path), re-running refreshes a stale one, and
  `uninstall-settings` deletes it. `parse_settings` / `serialize_settings`
  are text passthrough, with `None` from serialize meaning "remove the file".
- **The generated plugin is the adapter's arm inside opencode.** It bridges
  the runtime surface to the frog's two doorways: bus events and opencode's
  own `chat.message` hook become `hook` invocations under the native names
  `session.created` / `chat.message` / `session.idle` / `session.deleted`,
  and it re-fires `session.deleted` from its `dispose` hook so quitting
  opencode releases the window claim rather than leaking it (the stale-claim
  sweeps remain the backstop). Never-crash extends into Bun: every handler
  swallows its errors, and sends are fire-and-forget detached spawns.
- **Token payload** — real, not degraded: assistant `message.updated` events
  carry `tokens {input, output, reasoning, cache:{read, write}}`, and the
  model's context size (`Model.limit.context`, captured by the plugin's
  `chat.params` hook) rides along in the tap payload. The accounting mirrors
  Claude Code's — input + cache read + cache write of the last request. Any
  schema drift degrades to `None`: a calm green frog whose goofiness ramps on
  turn count (`FALLBACK_UNHINGED_TURNS`) — the honest fallback the seam
  guarantees every agent, token feed or not.

Caveat worth knowing: opencode's plugin API is versioned in lockstep with the
app and a v2 surface exists in-tree upstream; the generated plugin targets
the v1 `Hooks` shape. If upstream moves, the plugin's defensive guards mean
the frog goes quiet rather than breaking a turn — and regenerating via
`install-settings` is the upgrade path.

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

### Revisit (2026-08-07, FWL-548): the second adapter landed — the file stays

The named trigger fired: `OpencodeAdapter` is in. Verdict on the split:
**still no.** The evidence from actually writing adapter #2:

- The adapter cost ~300 lines (class + generated plugin template) and touched
  nothing outside its section beyond the two seam additions
  (`parse_settings`/`serialize_settings`) and the planned `--agent` flag —
  the "class plus a registry entry" promise held in practice.
- The install story argument got *stronger*, not weaker: the opencode plugin
  bakes in the same one absolute path, and a package split would now have two
  agents' worth of wired paths to keep resolvable.
- Navigation is fine at ~3.4k lines with the section map.

Next named trigger: a **third** adapter, or the file crossing the point where
section navigation stops working in practice — same constraints as above.
