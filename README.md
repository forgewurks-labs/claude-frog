# 🐸 Claude Frog

A little pixel frog who dances while Claude Code is thinking — and quietly warns
you when you're burning too much context.

He starts composed and professional. The more of your context window you spend,
the goofier he gets. Past ~150k tokens he starts to shake. So his mood is an
honest, glanceable gauge: **calm = you're fine; unhinged = quality's about to
soften, wrap it up or `/compact` soon.**

He's the dusty-rose pixel guy from Claude Code, restyled into a stuffed frog in
the spirit of a certain pink plush amphibian. Bulgy eyes, wide grin, stitched
seams, tiny haunches.

It's a self-inflicted CPU tax. That's the point. He's worth it.

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
# prefix + f  →  hide / summon the frog     (F relocates the old find-window)
bind f run-shell "python3 /path/to/claude-frog/claude_frog.py toggle"
bind F command-prompt "find-window '%%'"
```

The pane version needs the token gauge from the statusline, so **install both**
to get the honest danger-zone signal in the pane. Pane-only (no statusline)
still works — he just falls back to ramping his goofiness on turn count instead
of tokens (unhinged by turn 4).

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
`--layout right` puts the pane in a tall side column instead of a bottom strip;
`--always-dance` dances regardless of turn state.

---

## How it works

```
UserPromptSubmit / Stop hooks ─┐
                               ├─► ~/.cache/claude-frog/<session>.think   (dance vs idle, turn count)
   statusline (each refresh) ──┼─► ~/.cache/claude-frog/<session>.ctx     (absolute context tokens)
                               │
        pane daemon (12fps) ◄──┘   reads both, renders the frog
```

- **Hooks** own the *think-state* (they can't see tokens).
- **The statusline** owns the *token gauge* (only it can see tokens) and writes
  them to a file the daemon reads.
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
