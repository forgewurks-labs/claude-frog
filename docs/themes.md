# Themes & rendering architecture

Contributor notes for how Claude Frog is drawn, how the console themes work, and
how the `claude SNES` / `claude SEGA` / `claude GBA` launcher fits together. For
user-facing setup, see the [README](../README.md); this doc is about the
internals. Everything here should be verified against `claude_frog.py` before you
rely on it — the code is the source of truth.

## How the frog is drawn

- **One sprite, many poses.** The frog's motion is squeezed out of a *single*
  base sprite via the `shear` / `squash` / `flip_*` grid transforms plus a
  `Choreographer` that picks moves and emits per-frame pose params. There are
  two sprites: `FROG` (the tmux pane frog) and `CHIBI` (the compact statusline
  frog). Their dimensions are asserted in the tests — `FROG` is 12 rows × 19
  cols, `CHIBI` is 6 × 15 — because motion and pane sizing assume them.
- **Look = palette.** A sprite is a grid of single-char *palette keys* (`O`
  outline, `H` highlight, `B` body midtone, `P` eyes, …). Colorizing maps each
  key to an `(r, g, b)` or `None` (transparent) via a palette dict. So the whole
  look of the frog is just which colors those keys resolve to.
- **Rendering** is Unicode half-blocks (`▀` / `▄`) in 24-bit truecolor — two
  pixels per character cell — so he's real pixel art, not ASCII. Needs a
  truecolor terminal.

## Gauges

Everything reactive is a token-driven `0..1` scalar:

- `goofiness(tokens, turns)` — scales hop height, sway, and how often "specials"
  fire.
- `shake_px(tokens)` — pane-content jitter above a floor.
- `pinkness(tokens)` / `palette_for(tokens, theme)` — the green→pink color fade,
  fully pink by `PINK_FULL_TOKENS` (200k).

Tokens come **only** from the statusline `_tap` payload — hooks are token-blind.
A pane-only setup with no statusline falls back to a turn-count ramp for
goofiness and simply stays fresh-colored.

## Themes

A **theme** is purely a recolor (plus, for one theme, a dither). The registry:

```python
THEMES = {
  "snes":     {"base": RGB,      "pink": PINK,          "dither": ()},
  "genesis":  {"base": GENESIS,  "pink": GENESIS_PINK,  "dither": ("B", "L")},
  "gba":      {"base": GBA,      "pink": GBA_PINK,      "dither": ()},
  "terraria": {"base": TERRARIA, "pink": TERRARIA_PINK, "dither": ("B", "D")},
}
DEFAULT_THEME = "snes"
```

- **`snes`** (default) — the original smooth 16-bit shading ramp. `RGB`/`PINK`
  are kept as its palettes so pre-theme call sites and tests still work.
- **`genesis`** — a punchier, oversaturated Mega Drive palette. Its `dither`
  keys get cross-hatch shading: `_colorize` darkens those keys on a checkerboard
  (by `x+y` parity), faking extra shades the way the Genesis's limited palette
  did.
- **`gba`** — the iconic 4-tone monochrome Game Boy LCD. Many sprite keys
  collapse onto just four greens, flattening the shading into the blocky Game
  Boy look.
- **`terraria`** — the high-fidelity, warm & painterly indie look. A fuller
  earthy grass-green ramp with deep *desaturated* outlines (not pure black) and
  creamy highlights; its `dither` keys (`B`/`D`, the midtone and its shadow) get
  the cross-hatch, faking the hand-layered gradient shading of Terraria sprites.

Every theme keeps the green→pink **context gauge**: each carries a `base` (fresh)
palette and a `pink` fade target, and `palette_for(tokens, theme)` blends between
them (in HLS space, via `_blend`, so the fade stays vivid instead of sagging
through a muddy midpoint). For `gba` the gauge survives as a *tint* shift — the
whole LCD slides from green toward rose — because eyes and mouth are in its
`pink` map too.

Key invariant: **all palettes map the same set of sprite keys.** A key missing
from a theme's `base` would render as a transparent hole in the frog.

### Adding a theme

1. Define a `base` palette with **every** sprite key (`O H L B D S P W N R M`
   plus `" "`/`"."` → `None`).
2. Add a `pink` fade target — at minimum the green ramp keys; include more (eyes,
   mouth) if you want them to blush too.
3. Register it in `THEMES` with its `dither` key tuple (empty for smooth/flat
   looks).
4. Add a test that every sprite key resolves in the new `base` (guards against
   the transparent-hole bug), and extend the fade/selection tests.
5. Regenerate the README screenshots (below).

## Selecting a theme

Resolution is case- and punctuation-insensitive via `resolve_theme()` +
`THEME_ALIASES`, so `SNES`/`nintendo`, `SEGA`/`"Mega Drive"`/`md`,
`GBA`/`"Game Boy"`/`gb`, and `TERRARIA`/`relogic`/`32bit` all canonicalize. This
is honored **everywhere** a theme
is named:

- **`--theme <name>`** on any invocation.
- **`CLAUDE_FROG_THEME`** env var (mirrors `CLAUDE_FROG_LAYOUT`). The statusline
  reads it each refresh; the `SessionStart` hook bakes `--theme` into the
  spawned dance daemon so it stays fixed for that session's pane.
- **`claude SNES` / `claude SEGA` / `claude GBA`** — the launcher, below.

Whatever the route, an **unset or unrecognized theme falls back to
`DEFAULT_THEME` (`snes`)** — `_parse` does `resolve_theme(raw) or DEFAULT_THEME`,
and both `theme_spec()` and `palette_for()` fall back too, so the frog is never
left themeless.

### The `claude <THEME>` launcher

`claude` is Claude Code's own binary, not ours, so a literal `claude SNES` can't
be handled inside the Python alone. `install/claude-theme.sh` is a **sourced
shell wrapper** (bash + zsh) that defines a `claude()` function:

- If the first arg names a theme, it sets `CLAUDE_FROG_THEME` for **that one
  launch** (scoped, not exported — nothing lingers) and shifts it off; every
  other arg passes straight through to the real binary.
- It decides "is this first word a theme?" by calling the **`resolve-theme` CLI
  mode**, which prints the canonical name and exits `0`, or exits `1` if the
  word isn't a theme. So `claude "fix the bug"`, `claude -r`, and bare `claude`
  are untouched.
- It skips the lookup entirely for a bare invocation or a flag first-arg, so
  there's no overhead on the common path.

The wrapper must be sourced once (from `~/.zshrc` / `~/.bashrc`). Until then,
only the `CLAUDE_FROG_THEME` / `--theme` routes select a theme. `install.sh` (at
the repo root) automates that: it auto-detects the rc file, appends the `source`
line idempotently (guarded by a marker), and touches nothing else. The wrapper
in turn locates `claude_frog.py` relative to its own path (via `BASH_SOURCE` /
zsh `%x`), so there's nothing to hand-edit; export `CLAUDE_FROG` before sourcing
to override.

### `install.sh --with-frog` — wire up the visible frog too

The launcher only sets *which theme*; you still need the frog's statusline +
hooks installed to see anything. `./install.sh --with-frog` handles that by
calling the **`install-settings` CLI mode**, which merges into
`~/.claude/settings.json` (path overridable with `--settings`, or the
`CLAUDE_CONFIG_DIR` env var):

- Adds a `statusLine` running `… statusline` (or `… tap` with `--tap`) — but
  only if you don't already have one; an existing statusLine is never clobbered
  (Claude Code allows just one), and you're pointed at `statusline-compose.sh`.
- Appends a frog hook group to each of `FROG_HOOK_EVENTS` (`SessionStart`,
  `UserPromptSubmit`, `Stop`, `SessionEnd`), skipping any event that already has
  one.
- Preserves everything else in the file, refuses to touch invalid JSON, backs up
  to `settings.json.bak`, writes atomically, and is idempotent. No theme is
  baked into these commands — theme still comes from the env/launcher at
  runtime.

## README screenshots

`assets/frog-<theme>.png` are generated by `assets/gen_screenshots.py` — a
stdlib-only PNG writer (`zlib` + `struct`, no Pillow, matching the project's
no-deps rule) that reads the **live palettes**, so the images can't drift from
what the terminal draws. Each shows the fresh frog with a strip beneath tracing
that theme's green→pink fade. Regenerate after any palette or sprite edit:

```sh
python3 assets/gen_screenshots.py
```

## Hard rules

- The **statusline / tap / hook paths never crash and always exit 0** — a broken
  frog must never break your prompt. New color/animation work must preserve
  this.
- **Standard library only** — no third-party deps, anywhere (including the
  screenshot generator).
- Tests live in `tests/test_frog.py` (`python3 -m unittest discover -s tests`);
  CI runs them on Python 3.9 / 3.11 / 3.13. Add coverage for new behavior.
