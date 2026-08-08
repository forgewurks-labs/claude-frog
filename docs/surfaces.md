# Rendering surfaces

Contributor notes for the surface seam: where the frog ends and the thing
hosting his *pane* begins. This is the sibling of [adapters.md](adapters.md) —
that seam faces the coding agent (payloads, hooks, settings files); this one
faces the terminal multiplexer (panes, windows, splits). Everything here
should be verified against `claude_frog.py` before you rely on it — the code
is the source of truth.

## The seam

The frog's window bookkeeping is surface-agnostic. All it asks of the thing
hosting his pane is:

1. **A notion of "the window this process is in"** — the unit the
   one-frog-per-window invariant is scoped to.
2. **Pane life** — split a pane beside the user's shell running a command the
   frog hands over, and kill it later.
3. **Pane liveness** — enumerate the panes that exist right now; that is how
   claims are pruned (real liveness, not timeouts).

Everything that knows one surface *specifically* — its command vocabulary,
its window-id format, its split geometry, its pane-stamping trick — lives in
a `RenderSurface` subclass in the "Rendering surfaces" section of
`claude_frog.py`. Supporting a new surface means writing a new one and adding
it to the `SURFACES` registry, not sweeping through the file.

## The interface

`RenderSurface` (the abstract base — it *is* the contract):

| Member | What it answers |
| --- | --- |
| `name` | Registry key (`"tmux"`). |
| `display` | The surface's name as humans write it. |
| `inside()` | Is this process running under the surface? |
| `current_pane()` | The pane THIS process runs in (`None` if unknowable). |
| `window_id(pane)` | The window holding `pane` (or this process). `None` outside the surface — which is what makes every caller degrade to the paneless story without asking about the surface itself. |
| `valid_window(win)` | Is `win` well-formed? Window ids are derived from the environment but end up on command lines; this is where that assumption gets checked. |
| `window_token(win)` / `window_from_token(token)` | Window id ↔ the filename-safe token the window state files are named by (`win-<token>.json`). |
| `live_panes()` | Every pane id alive right now (claim liveness). |
| `frog_panes()` | `{pane_id: window}` for panes stamped as frogs. |
| `spawn_pane(win, near, cmd, layout)` | Create the frog's stamped pane running `cmd` — the only place a pane is ever born. |
| `kill_pane(pane)` | Tear one down. |
| `reap_legacy_panes(win, is_ours)` | Kill unstamped frog panes from before window-scoping. Defaults to "nothing to reap" — only tmux has that past. |

Conventions that keep the seam honest (mirroring the adapter seam):

- **The frog builds the command, the surface runs it.** `_spawn_win_pane`
  owns the dance command (interpreter, script path, theme, prop baseline);
  `SURFACE.spawn_pane` owns the split. Layout *names and sizes* (`LAYOUTS`)
  are the frog's vocabulary — how much room he needs; the name → split-axis
  translation (`-v`/`-h`) is the tmux surface's.
- **The `is_ours` predicate.** The legacy reaper takes the frog's own "is
  this pane start command mine?" test (`_is_frog_dance_cmd`) as a parameter —
  the surface carries the pane knowledge, the frog carries his own identity.
  Same split as `_is_frog_cmd` in the settings surgery.
- **Window ids never leak their format.** The `"@" + digits` shape is known
  only inside `TmuxSurface` (`valid_window`, `window_token`,
  `window_from_token`); the stale sweep reconstructs a window from a filename
  through the surface, not by string surgery.
- **Never-crash still holds.** Every surface call sits on the same
  never-crash / always-exit-0 paths as everything else; `_tmux` swallows its
  own failures and returns `None`.

Two pieces of tmux knowledge deliberately live *outside* the class, in their
own labelled sections, as tmux-backend integration (the way the opencode
adapter owns its plugin file):

- **The toggle keybind installer** (`install_keybind` & co) knows tmux.conf on
  purpose. A second surface would bring its own summon story, not reuse it.
- **Doctor's surface rows** route their *data* through `SURFACE` but keep
  tmux-specific advice text ("add tmux + WezTerm") — honest, since tmux is
  the only surface there is.

`_tmux` itself stays a module-level function: the tests stand a fake server
in it, and the keybind installer shares it.

## Surface #1: tmux

`TmuxSurface` is the reference implementation. The facts it encapsulates:
the tmux command vocabulary, the `@`+digits window-id format, the
layout → split-axis translation, and the `@claude_frog` pane option every
spawn stamps on so a frog pane can be recognised even when no window file
admits to owning him (an upgrade orphan, or a pane whose state was wiped
underneath it).

## Recorded decision: tmux is the sole supported surface (2026-08-07, FWL-549)

**This is declared scaffolding.** The seam exists; the registry holds one
entry; `detect_surface()` hard-lands on tmux. Outside tmux the frog degrades
exactly as he always has — no pane, the statusline gauge carries the whole
show.

Why hold here: a second backend (Zellij, kitty splits, Windows Terminal
panes, or a plain standalone-terminal mode) is the biggest lift on the
roadmap for the least certain payoff. The seam makes it a class plus a
registry entry *when demand shows up* — building one speculatively would be
exactly the scaffold-dictating-where-the-walls-go failure the org standards
warn about.

**The removal condition, named:** users actually asking for a non-tmux
surface (tracked on FWL-549, step 2). When that fires, `detect_surface()`
becomes the walk-the-registry probe `detect_agent()` already is, and the
second surface's real requirements get to bend the interface the way
adapter #2 bent `AgentAdapter` (`parse_settings`/`serialize_settings` were
added by evidence, not speculation). Until then: tmux, declared plainly, and
`doctor` says so.
