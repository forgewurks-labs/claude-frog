# 🐸 Claude Frog

A little pixel frog who dances while Claude Code is thinking — and quietly warns
you when you're burning too much context.

He starts composed and professional. The more of your context window you spend,
the goofier he gets. Past ~150k tokens he starts to shake. So his mood is an
honest, glanceable gauge: **calm = you're fine; unhinged = quality's about to
soften, wrap it up or `/compact` soon.**

He's the 🐸 emoji as pixel art, wearing the dusty rose of the Claude Code guy:
two eye bumps riding on a wide round head, dark inset eyes, nostril dots, and a
big open grin.

It's a self-inflicted CPU tax. That's the point. He's worth it.

---

## Get started (one command)

**You need:** `python3` (3.x, already on macOS/Linux) and a truecolor terminal
(WezTerm, iTerm2, Kitty, or modern tmux). `git` too, for the one-liner. The
*dancing pane* additionally wants **tmux + WezTerm** — but you don't need it to
start; without it you still get the statusline frog.

### 1. Install

From nothing to a dancing frog:

```sh
curl -fsSL https://raw.githubusercontent.com/forgewurks-labs/claude-frog/main/bootstrap.sh | bash
```

That clones the repo to `~/.claude-frog`, then — after **showing you exactly what
it will touch and asking once** — sets up the whole frog: the `claude <THEME>`
launcher *and* the statusline frog + dance hooks, so you actually see him. It
preserves everything already in your `~/.claude/settings.json` and backs the file
up first (to `settings.json.bak`).

Prefer to read before you run? Same result, nothing piped to a shell:

```sh
git clone https://github.com/forgewurks-labs/claude-frog.git ~/.claude-frog
~/.claude-frog/install.sh
```

Not in tmux? You'll get the statusline frog (which is plenty). The *dancing
pane* needs tmux + WezTerm — add them any time for the full show.

### 2. Activate (the one unavoidable step)

No installer can reach into the terminal it's running in to load a new shell
command, so once it finishes:

```sh
# close this terminal and open a new one — or just run:
source ~/.zshrc          # (or ~/.bashrc)
```

### 3. Use it

Start a session and name a console as the first word — that's his theme for the
session:

```sh
claude SEGA              # or SNES, GBA, TERRARIA — name none and he wears SNES
```

Everything that isn't a theme name passes straight through, so `claude`,
`claude -r`, and `claude "fix the bug"` behave exactly as before. That's the
whole loop: **install → new terminal → `claude SEGA`.**

### Verify / troubleshoot

The installer ends by running a **checkup** so you know it worked before you open
that new terminal. Run it yourself any time:

```sh
python3 ~/.claude-frog/claude_frog.py doctor
```

It reports on `python3`, the launcher line, the statusline + hooks, your theme,
and tmux. See a ⚠️? Re-run `~/.claude-frog/install.sh` — it's idempotent and safe
to run again.

### Options, updating, and removal

Flags go **straight to `install.sh`**, or after `bash -s --` when piping
(`curl … | bash -s -- --minimal`):

```sh
~/.claude-frog/install.sh --minimal    # ONLY the `claude <THEME>` launcher, no settings edits
~/.claude-frog/install.sh --tap        # full frog, but keep your own status bar (silent tap)
~/.claude-frog/install.sh --yes        # skip the confirm prompt (for automation)
~/.claude-frog/install.sh --uninstall  # remove everything it added, restore your backups
```

**Update** to the latest by re-running the one-command install (it pulls, then
re-wires idempotently), or `git -C ~/.claude-frog pull`. **Remove** it completely
— launcher line *and* settings wiring, restoring your backups — with
`~/.claude-frog/install.sh --uninstall`.

---

## Two ways to run him

Both come from **one file, standard library only** — no `pip install`, no
dependencies. Pick either or run both.

### 🟢 Statusline frog (easiest to share)

A compact 3-row "mood frog" right in your Claude Code status bar. Zero setup
beyond one line of config. This is the one to send friends.

In `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /path/to/claude-frog/claude_frog.py statusline"
  }
}
```

That's it. He reads the token usage Claude Code hands the statusline and picks
his mood from it. (Statuslines only refresh ~1×/sec, so here he strikes *poses*
rather than dancing — for the full show, add the pane below.)

### 🕺 Dancing pane frog (tmux + WezTerm)

A dedicated tmux pane where he dances smoothly (~12 fps) for exactly as long as
Claude is working, then idles between turns — one frog per session, so a
parallel fan-out gives you a whole chorus line.

Add the hooks to `~/.claude/settings.json` (see
[`install/settings-hooks.json`](install/settings-hooks.json) for the full
block):

- `SessionStart` → spawns his pane (only if you're inside tmux)
- `UserPromptSubmit` → "a turn started, dance!" (+ counts turns)
- `Stop` → "turn's done, rest"
- `SessionEnd` → tears his pane down, no orphans

And the tmux toggle keybind (see
[`install/tmux.conf.snippet`](install/tmux.conf.snippet)):

```tmux
# prefix + F  →  hide / summon the frog   (capital F; find-window stays on f)
bind F run-shell "python3 /path/to/claude-frog/claude_frog.py toggle"
```

#### 🌷 A little diorama that grows as you work

<p align="center">
  <img src="assets/frog-scene.png" width="640" alt="Claude Frog flanked by trees, a rock, flowers, a fallen log, and drifting clouds">
</p>
<p align="center">
  <img src="assets/frog-scene-terraria.png" width="640" alt="The same diorama with the terraria-themed frog; the props keep their natural palette">
  <br>
  <sub>The same scene in the <code>terraria</code> style — the frog wears the theme, the props stay natural.</sub>
</p>

Every prompt you send, the dancing pane sprouts one random prop around the frog
— a random-colored flower, a cloud, a rock, a tree, or a fallen log — that
animates in (flowers and trees grow up, rocks drop and settle, logs roll in,
clouds drift across the sky) and then stays. It's a quiet, honest tally of how
long you've been at it: a bare patch of grass at the start, a whole scene by the
end of a long session. Props live only in the pane, so the diorama resets when a
new session starts. It's on by default — set `CLAUDE_FROG_FLORA=0` to turn it
off, or tune `ENTRANCE_FRAMES` / `FLORA_MAX` at the top of `claude_frog.py`.

### 🤫 Pane-only, but still honest (`tap`)

Only the statusline is handed your token usage — the hooks are blind to it. So
if you want the dancing pane *without* a frog sitting in your status bar, don't
just drop the statusline: he'd fall back to guessing from turn count and you'd
lose the shake entirely.

Use `tap` instead. It reads the same payload and publishes the token gauge for
the pane, and prints **nothing**:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /path/to/claude-frog/claude_frog.py tap"
  }
}
```

Already have a statusline of your own? Keep it, and set `FROG_MODE="tap"` in
[`install/statusline-compose.sh`](install/statusline-compose.sh) — your bar
renders exactly as before, and the pane frog stays fully calibrated.

---

## The gauge (all tunable at the top of `claude_frog.py`)

| Context tokens | Claude Frog |
|---|---|
| ≤ 40k | composed, professional little bobs |
| 40k → 100k | progressively goofier — you can *watch* the context fill |
| ~100k | mostly unhinged |
| ≥ 120k | full chaos, frequent specials (backflips, big jumps) |
| ≥ 150k | he starts to shake, and shakes harder the deeper you go (capped so he stays legible) |

Anchored in **absolute tokens**, not percentage — so it's calibrated to when
long-context quality actually softens, and reads the same whether your window is
200k or 1M.

Flags: `--party` pins him to max goofiness + shake (always dancing);
`--always-dance` dances regardless of turn state.

### Rendering styles (pick per session)

He renders in four pixel-art styles. All keep the green→pink context gauge —
each just expresses it in that style's idiom (the bar under each frog is that
theme's actual fade, fresh → full window):

| | Theme | Look |
|---|---|---|
| <img src="assets/frog-snes.png" width="220" alt="SNES frog"> | `snes` *(default)* | smooth 16-bit shading ramp, fading to Claude pink |
| <img src="assets/frog-genesis.png" width="220" alt="Genesis frog"> | `genesis` | punchy, oversaturated Mega Drive palette with cross-hatch **dithering**, fading to hot magenta |
| <img src="assets/frog-gba.png" width="220" alt="Game Boy frog"> | `gba` | the iconic 4-tone monochrome Game Boy LCD (pea-green), whose tint slides green→rose as context fills |
| <img src="assets/frog-terraria.png" width="220" alt="Terraria frog"> | `terraria` | high-fidelity, warm & painterly indie look — a fuller earthy ramp with desaturated outlines and **dithered** midtones, fading to a warm rose |

> Screenshots regenerate from the live palettes with `python3 assets/gen_screenshots.py`.
> How the themes and the launcher work under the hood — and how to add a theme —
> is in [`docs/themes.md`](docs/themes.md).

Choose one **when you start a Claude session**. The simplest way — just name the
console as the first word:

```sh
claude SNES      # smooth 16-bit frog
claude SEGA      # dithered Genesis frog
claude GBA       # mono Game Boy frog
claude TERRARIA  # painterly indie frog
```

That comes from a tiny shell wrapper
([`install/claude-theme.sh`](install/claude-theme.sh)). The
[one-command install](#get-started-one-command) sets it up along with the frog
himself; `./install.sh` from the repo root does the same locally. Want *only*
the theme command and no settings edits? Use `--minimal`:

```sh
./install.sh --minimal
```

That appends a `source` line to your `~/.zshrc` / `~/.bashrc` (it auto-detects
which), then open a new terminal. It's idempotent, edits nothing else, and the
wrapper finds `claude_frog.py` on its own — no paths to hand-edit. Prefer to do
it by hand? Add this one line yourself:

```sh
source /path/to/claude-frog/install/claude-theme.sh
```

The default `./install.sh` (no flags) does the whole thing — launcher **plus**
the statusline frog + hooks in `~/.claude/settings.json` so you actually *see*
him. It preserves everything already in your settings, backs the file up first,
won't overwrite an existing statusline, is idempotent, and can be fully undone
with `./install.sh --uninstall`.

The wrapper only steps in when that first word actually names a theme (case- and
spacing-insensitive — `SNES`, `nintendo`, `"Mega Drive"`, `gameboy` all work)
and passes everything else straight through, so plain `claude`, `claude -r`, and
`claude "fix the bug"` are untouched. **Name no theme and the frog stays on the
default SNES** — as it does for an unset or unrecognized value, so he's never
left themeless.

Under the hood it just sets the `CLAUDE_FROG_THEME` env var for that launch — so
if you'd rather not add a wrapper, set it yourself before starting Claude Code:

```sh
export CLAUDE_FROG_THEME=genesis   # or: gba, snes, terraria
```

Either way, both the statusline frog and the dancing pane read it (the pane
bakes the theme in at spawn, so it stays fixed for that session). You can also
pass `--theme` directly to any invocation. Preview them without installing
anything:

```sh
python3 claude_frog.py preview --theme genesis
python3 claude_frog.py preview --theme gba
python3 claude_frog.py dance --party --theme gba   # watch him lose it in mono
```

### Where the pane goes

`--layout top|bottom|left|right` (default `top`). `top`/`bottom` are 7-line
strips, `left`/`right` are 24-column side towers. He always stands on the pane's
floor, so the default `top` perches him directly above your prompt, looking down
at your work.

The pane is spawned by the `SessionStart` hook but toggled by the tmux keybind,
so rather than passing `--layout` to both, set it once:

```sh
export CLAUDE_FROG_LAYOUT=bottom
```

---

## How it works

```
UserPromptSubmit / Stop hooks ─┐
                               ├─► ~/.cache/claude-frog/<session>.think   (dance vs idle, turn count)
 statusline / tap (each ───────┼─► ~/.cache/claude-frog/<session>.ctx     (absolute context tokens)
   statusline refresh)         │
        pane daemon (12fps) ◄──┘   reads both, renders the frog
```

- **Hooks** own the *think-state* (they can't see tokens).
- **The statusline** owns the *token gauge* (only it can see tokens) and writes
  it to a file the daemon reads — `statusline` does that *and* draws a frog,
  `tap` does only the writing.
- Everything is keyed by session id, so multiple Claude Code sessions each get
  their own independent frog.
- The statusline and hook paths **never crash and always exit 0** — a broken
  frog can never break your prompt.

Rendering is Unicode half-blocks (`▀`/`▄`) with 24-bit truecolor: two pixels per
character cell, so he's real pixel art, not ASCII. Needs a truecolor terminal
(WezTerm, iTerm2, Kitty, modern tmux with `RGB`).

## Peek at him without installing anything

```sh
python3 claude_frog.py preview            # ASCII silhouette + color render
python3 claude_frog.py dance --party      # watch him lose it (Ctrl-C to stop)
```

## Composing with an existing statusline

Only one `statusLine` command is allowed, so if you already run one, wrap both.
See [`install/statusline-compose.sh`](install/statusline-compose.sh) for a small
wrapper that stacks your existing statusline on top of the frog.
