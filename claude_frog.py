#!/usr/bin/env python3
"""Claude Frog — a little pixel frog who dances while Claude Code is thinking.

One file, standard library only. Two jobs:

  * `dance`       — the tmux-pane daemon: a smooth pixel frog who dances while
                    your turn is running and idles between turns.
  * `statusline`  — a compact 3-row "mood frog" for the Claude Code status bar
                    (drop-in and shareable; friends need only this file).

He is also a gauge. The more context you've burned, the goofier he gets, and
past ~150k tokens he starts to shake — an honest "you're deep in it, quality's
about to soften" tell. Calm below ~40k, mostly unhinged by ~100k, full chaos by
~120k.

Design discipline: the statusline and hook paths NEVER crash and always exit 0
— a broken frog must never break your prompt. Imports stay light (stdlib only).

See README.md for install. Everything below is tunable via the constants block.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import time

# --------------------------------------------------------------------------- #
# Tunables                                                                     #
# --------------------------------------------------------------------------- #

# Goofiness ramp, anchored in ABSOLUTE context tokens (not % — works on a 200k
# or a 1M window alike, because long-context quality decline is about absolute
# length). goofiness is a 0..1 scalar scaling hop height, sway, and specials.
CALM_TOKENS = 40_000          # at/below this: composed, professional frog
UNHINGED_TOKENS = 120_000     # at/above this: full chaos (goofiness == 1.0)

# Screen shake (pane-content jitter only — never the whole terminal). Zero below
# the floor, then grows continuously with token count, capped for legibility.
SHAKE_START_TOKENS = 150_000  # first jitter appears here
SHAKE_FULL_TOKENS = 320_000   # jitter amplitude saturates here
SHAKE_MAX_PX = 3              # max jitter in pixels (kept subtle/readable)

# Framerates.
FPS_ACTIVE = 12.0             # dancing (a turn is running)
FPS_IDLE = 4.0                # idling (between turns)

# Pane layouts: name -> (tmux split axis, size). Vertical splits are sized in
# lines, horizontal ones in columns. `top`/`left` place the pane before the
# current one; `bottom`/`right` after it. He always stands on the pane's floor,
# so a top pane puts him directly above your prompt, facing down at your work.
LAYOUTS = {
    "bottom": ("-v", 7),
    "top": ("-v", 7),
    "right": ("-h", 24),
    "left": ("-h", 24),
}

# Fallback goofiness when no token data is available (pane-only friend with no
# statusline feeding tokens): ramp on turn count instead — unhinged by turn 4.
FALLBACK_UNHINGED_TURNS = 4

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "claude-frog",
)

# --------------------------------------------------------------------------- #
# Palette                                                                      #
# --------------------------------------------------------------------------- #
# Emoji-frog green with dark inset eyes. None == transparent (terminal bg).

RGB = {
    "O": (0x2f, 0x4a, 0x1e),   # outline / deep leaf green
    "B": (0x9d, 0xc8, 0x3b),   # body (the signature yellow-green)
    "P": (0x2b, 0x2b, 0x2f),   # eyes / nostrils (near-black)
    "N": (0xf5, 0xe9, 0xcf),   # open-mouth interior (warm cream)
    "M": (0x2f, 0x4a, 0x1e),   # closed-eye / mouth line (== outline)
    " ": None,
    ".": None,
}

# --------------------------------------------------------------------------- #
# Sprites (authored ragged; padded to a rectangle at load time)               #
# --------------------------------------------------------------------------- #
# Emoji-frog spirit: two eye bumps riding on a wide round head, dark inset eyes,
# nostril dots, and a big open grin. No seams — he's a frog, not a plushie.

_FROG_SRC = [
    "  OOOO       OOOO  ",   # tops of the two eye bumps
    " OBPPBO     OBPPBO ",   # dark inset eyes
    " OBPPBOOOOOOOBPPBO ",   # bumps settle onto a wide head
    "OBBBBBBBBBBBBBBBBBO",   # brow
    "OBBBBPBBBBBBBPBBBBO",   # nostrils
    "OBBBBBBBBBBBBBBBBBO",   # cheeks
    "OBBOOOOOOOOOOOOOBBO",   # grin: top lip
    "OBBONNNNNNNNNNNOBBO",   # open mouth
    "OBBBOOOOOOOOOOOBBBO",   # grin: bottom lip curves up
    " OBBBBBBBBBBBBBBBO ",   # jaw / body
    "  OBBO       OBBO  ",   # legs
    "  OOO         OOO  ",   # feet
]

# Blink overlay: the eyes squeeze shut to happy little arcs.
_FROG_BLINK = {
    1: " OBBBBO     OBBBBO ",
    2: " OB__BOOOOOOOB__BO ",
}

# Compact "mood frog" for the statusline (3 char-rows == 6px tall).
_CHIBI_SRC = [
    " OOOO     OOOO ",   # eye bumps
    " OPPO     OPPO ",   # eyes
    "OBPPBOOOOOBPPBO",   # head bridge
    "OBBOOOOOOOOOBBO",   # top lip
    "OBBBONNNNNOBBBO",   # open mouth
    " OBBBOOOOOBBBO ",   # bottom lip / jaw
]
_CHIBI_BLINK = {
    1: " OBBO     OBBO ",
    2: "OB__BOOOOOB__BO",
}

Pixel = tuple  # (r, g, b) or None


def _load(src):
    """Ragged rows -> rectangular grid of palette keys (space-padded)."""
    w = max(len(r) for r in src)
    return [list(r.ljust(w)) for r in src]


FROG = _load(_FROG_SRC)
CHIBI = _load(_CHIBI_SRC)


def _apply_blink(grid, overlay):
    g = [row[:] for row in grid]
    w = len(g[0])
    for y, line in overlay.items():
        if 0 <= y < len(g):
            for x, ch in enumerate(line[:w]):
                if ch == "_" or ch == "-":
                    g[y][x] = "M"       # closed-eye line
                elif ch != " ":
                    g[y][x] = ch
    return g


def _colorize(grid):
    """Palette-key grid -> pixel grid of (r,g,b)|None."""
    return [[RGB.get(ch) for ch in row] for row in grid]


# --------------------------------------------------------------------------- #
# Grid transforms — motion is squeezed out of ONE base sprite                  #
# --------------------------------------------------------------------------- #


def shear(grid, amount):
    """Horizontal shear with the feet planted (bottom row fixed).

    amount > 0 leans the head to the right. Fractional amounts are fine.
    """
    if not amount:
        return grid
    h = len(grid)
    w = len(grid[0])
    out = [[None] * w for _ in range(h)]
    for y in range(h):
        # 0 at the feet, 1 at the head
        lever = (h - 1 - y) / max(1, (h - 1))
        dx = int(round(amount * lever))
        for x in range(w):
            nx = x + dx
            if 0 <= nx < w:
                out[y][nx] = grid[y][x]
    return out


def squash(grid, drop):
    """Crouch: remove `drop` interior body rows (frog compresses down)."""
    if drop <= 0:
        return grid
    h = len(grid)
    # remove rows just above the legs (mid-body) so face+feet stay put
    remove = set()
    mid = h - 3
    for i in range(drop):
        r = mid - i
        if 0 < r < h - 2:
            remove.add(r)
    return [row for i, row in enumerate(grid) if i not in remove]


def flip_h(grid):
    return [list(reversed(row)) for row in grid]


def flip_v(grid):
    return list(reversed(grid))


# --------------------------------------------------------------------------- #
# Half-block renderer (2 vertical pixels per character cell, truecolor)        #
# --------------------------------------------------------------------------- #

_UPPER = "▀"   # ▀
_LOWER = "▄"   # ▄
_RESET = "\x1b[0m"


def _cell(top, bot):
    """Render one character cell from its top/bottom pixel colors."""
    if top is None and bot is None:
        return " "
    if top is not None and bot is not None:
        return (f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m"
                f"\x1b[48;2;{bot[0]};{bot[1]};{bot[2]}m{_UPPER}\x1b[0m")
    if top is not None:
        return f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m{_UPPER}\x1b[0m"
    return f"\x1b[38;2;{bot[0]};{bot[1]};{bot[2]}m{_LOWER}\x1b[0m"


def render_pixels(pixels):
    """(r,g,b)|None pixel grid -> list of ANSI char rows (half its pixel height)."""
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    rows = []
    for y in range(0, h, 2):
        top = pixels[y]
        bot = pixels[y + 1] if y + 1 < h else [None] * w
        rows.append("".join(_cell(top[x], bot[x]) for x in range(w)))
    return rows


def blit(stage, sprite, x, y):
    """Paint sprite pixels onto stage pixels at (x, y); transparent = skip."""
    sh, sw = len(sprite), len(sprite[0])
    H, W = len(stage), len(stage[0])
    for j in range(sh):
        sy = y + j
        if 0 <= sy < H:
            row = stage[sy]
            srow = sprite[j]
            for i in range(sw):
                px = srow[i]
                if px is not None:
                    sx = x + i
                    if 0 <= sx < W:
                        row[sx] = px


# --------------------------------------------------------------------------- #
# Gauges                                                                       #
# --------------------------------------------------------------------------- #


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def goofiness(tokens, turns):
    """0..1 how unhinged the frog is. Token-driven; turn-count fallback."""
    if tokens is not None:
        g = (tokens - CALM_TOKENS) / max(1, (UNHINGED_TOKENS - CALM_TOKENS))
    else:
        g = turns / max(1, FALLBACK_UNHINGED_TURNS)
    # slight ease-in so the middle feels lively without maxing early
    return _clamp(g) ** 0.85


def shake_px(tokens):
    """Pane-content jitter amplitude in pixels. Continuous above the floor."""
    if tokens is None or tokens <= SHAKE_START_TOKENS:
        return 0.0
    frac = (tokens - SHAKE_START_TOKENS) / max(
        1, (SHAKE_FULL_TOKENS - SHAKE_START_TOKENS)
    )
    return _clamp(frac) * SHAKE_MAX_PX


# --------------------------------------------------------------------------- #
# Choreographer — picks moves and emits per-frame pose params                  #
# --------------------------------------------------------------------------- #

# A "move" is (name, base_frames, fn) where fn(t, g) -> dict of pose params:
#   dx, dy    : integer stage offset (booping / hopping)
#   shear     : horizontal lean
#   drop      : rows to squash (crouch)
#   mirror    : face the other way
#   flip      : upside down (specials only)
# t runs 0..1 across the move; g is goofiness 0..1.


def _m_idle_breathe(t, g):
    return {"drop": 1 if math.sin(t * math.pi * 2) > 0.4 else 0}


def _m_idle_sit(t, g):
    return {}


def _m_bob(t, g):
    amp = 1 + int(round(2 * g))
    return {"dy": -abs(int(round(amp * math.sin(t * math.pi * 2))))}


def _m_sway(t, g):
    amp = 1 + 3 * g
    return {"shear": amp * math.sin(t * math.pi * 2)}


def _m_hop(t, g):
    amp = 2 + int(round(4 * g))
    return {"dy": -int(round(amp * math.sin(t * math.pi))), "drop": 1 if t > 0.85 else 0}


def _m_wiggle(t, g):
    amp = 1 + 2 * g
    return {"shear": amp * math.sin(t * math.pi * 6)}


def _m_nod(t, g):
    return {"drop": 1 if math.sin(t * math.pi * 3) > 0 else 0}


def _m_boop(direction):
    def fn(t, g):
        span = 4 + int(round(10 * g))
        return {"dx": int(round(direction * span * math.sin(t * math.pi))),
                "dy": -abs(int(round((1 + g) * math.sin(t * math.pi * 2))))}
    return fn


# specials (rare; only fire when goofy)
def _m_bigjump(t, g):
    span = 6 + int(round(14 * g))
    return {"dx": int(round((random.choice([-1, 1])) * span * (t))),
            "dy": -int(round((6 + 8 * g) * math.sin(t * math.pi))),
            "shear": 2 * math.sin(t * math.pi * 4)}


def _m_backflip(t, g):
    return {"flip": 0.2 < t < 0.8,
            "dy": -int(round((5 + 6 * g) * math.sin(t * math.pi))),
            "mirror": t > 0.5}


def _m_spinout(t, g):
    return {"mirror": int(t * 8) % 2 == 0, "shear": 3 * math.sin(t * math.pi * 8),
            "dx": int(round(6 * g * math.sin(t * math.pi * 2)))}


IDLE_MOVES = [(_m_idle_breathe, 24), (_m_idle_sit, 16), (_m_idle_breathe, 30)]
ACTIVE_MOVES = [
    (_m_bob, 12), (_m_sway, 16), (_m_hop, 14), (_m_wiggle, 12), (_m_nod, 10),
    (_m_boop(1), 18), (_m_boop(-1), 18),
]
SPECIALS = [(_m_bigjump, 16), (_m_backflip, 18), (_m_spinout, 20)]


class Choreographer:
    def __init__(self):
        self.fn = _m_idle_sit
        self.frames = 1
        self.t = 0
        self.blink_until = 0
        self.frame_no = 0

    def _pick(self, active, g):
        if active:
            # specials get more likely as he gets goofier
            if random.random() < 0.02 + 0.10 * g:
                self.fn, self.frames = random.choice(SPECIALS)
            else:
                self.fn, self.frames = random.choice(ACTIVE_MOVES)
                # goofier -> shorter moves, so he switches faster / frantically
                self.frames = max(6, int(self.frames * (1.0 - 0.4 * g)))
        else:
            self.fn, self.frames = random.choice(IDLE_MOVES)
        self.t = 0

    def step(self, active, g):
        self.frame_no += 1
        if self.t >= self.frames:
            self._pick(active, g)
        t = self.t / max(1, self.frames)
        params = self.fn(t, g)
        self.t += 1
        # random blinks, more often when active
        if self.frame_no >= self.blink_until and random.random() < (0.05 if active else 0.02):
            self.blink_until = self.frame_no + 2
        params["blink"] = self.frame_no < self.blink_until
        return params


def pose(base, blink_overlay, params):
    """Build a colorized pixel sprite for a frame from base grid + params."""
    grid = base
    if params.get("blink"):
        grid = _apply_blink(grid, blink_overlay)
    else:
        grid = [row[:] for row in grid]
    if params.get("mirror"):
        grid = flip_h(grid)
    if params.get("flip"):
        grid = flip_v(grid)
    px = _colorize(grid)
    px = shear(px, params.get("shear", 0.0))
    drop = params.get("drop", 0)
    if drop:
        px = squash([[RGB.get(ch) for ch in row] for row in grid], drop)
        px = shear(px, params.get("shear", 0.0))
    return px


# --------------------------------------------------------------------------- #
# State files (per session)                                                    #
# --------------------------------------------------------------------------- #


def _paths(session):
    base = os.path.join(CACHE_DIR, session)
    return base + ".think", base + ".ctx", base + ".pane"


def _read_think(session):
    try:
        with open(_paths(session)[0]) as f:
            d = json.load(f)
        return d.get("state", "idle"), int(d.get("turns", 0))
    except Exception:
        return "idle", 0


def _read_ctx(session):
    try:
        with open(_paths(session)[1]) as f:
            d = json.load(f)
        t = d.get("tokens")
        return int(t) if t is not None else None
    except Exception:
        return None


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Mode: dance  (the tmux-pane daemon)                                          #
# --------------------------------------------------------------------------- #


def _term_size():
    try:
        import shutil
        s = shutil.get_terminal_size(fallback=(40, 7))
        return max(8, s.columns), max(3, s.lines)
    except Exception:
        return 40, 7


def mode_dance(opts):
    session = opts["session"]
    always = opts["always"]
    party = opts["party"]
    out = sys.stdout
    chor = Choreographer()

    def cleanup(*_):
        out.write("\x1b[?25h\x1b[0m\x1b[2J\x1b[H")
        out.flush()
        raise SystemExit(0)

    try:
        import signal
        signal.signal(signal.SIGTERM, cleanup)
        signal.signal(signal.SIGHUP, cleanup)
        signal.signal(signal.SIGINT, cleanup)
    except Exception:
        pass

    out.write("\x1b[?25l\x1b[2J")   # hide cursor, clear once
    out.flush()
    ticks_missing = 0

    try:
        while True:
            state, turns = _read_think(session)
            tokens = _read_ctx(session)
            active = party or always or (state == "thinking")
            g = 1.0 if party else goofiness(tokens, turns)
            sk = shake_px(tokens) if not party else float(SHAKE_MAX_PX)

            # self-exit if this session's state has vanished (session ended and
            # cleanup ran, or files pruned) — no orphan frogs.
            if not os.path.exists(_paths(session)[0]):
                ticks_missing += 1
                if ticks_missing > 40:
                    cleanup()
            else:
                ticks_missing = 0

            cols, rows = _term_size()
            stage_h = rows * 2
            stage = [[None] * cols for _ in range(stage_h)]

            params = chor.step(active, g)
            sprite = pose(FROG, _FROG_BLINK, params)
            sh_, sw_ = len(sprite), len(sprite[0])

            base_x = (cols - sw_) // 2 + params.get("dx", 0)
            base_y = stage_h - sh_ + params.get("dy", 0)
            if sk:
                base_x += random.randint(-int(sk), int(sk))
                base_y += random.randint(-int(sk), int(sk))
            base_x = max(-2, min(cols - sw_ + 2, base_x))
            base_y = max(-2, min(stage_h - 2, base_y))

            blit(stage, sprite, base_x, base_y)

            # No trailing newline: emitting one on the bottom row scrolls the
            # pane, which would lift him a row off the floor he stands on.
            frame_rows = [r + "\x1b[K" for r in render_pixels(stage)[:rows]]
            out.write("\x1b[H" + "\n".join(frame_rows))
            out.flush()

            fps = FPS_ACTIVE if active else FPS_IDLE
            time.sleep(1.0 / fps)
    except SystemExit:
        raise
    except Exception:
        cleanup()


# --------------------------------------------------------------------------- #
# Modes: tap (silent token gauge) + statusline (mood frog on top of the tap)   #
# --------------------------------------------------------------------------- #


def _extract_tokens(payload):
    cw = payload.get("context_window") or {}
    up = cw.get("used_percentage")
    size = cw.get("context_window_size") or cw.get("context_window") or 200_000
    if up is not None:
        try:
            return int(round(float(up) / 100.0 * float(size)))
        except Exception:
            pass
    for k in ("total_input_tokens", "used_tokens"):
        if cw.get(k) is not None:
            try:
                return int(cw[k])
            except Exception:
                pass
    cu = cw.get("current_usage") or {}
    tot = 0
    got = False
    for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        if cu.get(k) is not None:
            tot += int(cu[k]); got = True
    return tot if got else None


def _tap(payload=None):
    """Read the statusline payload and publish the token gauge to session state.

    The statusline is the only surface Claude Code hands token usage to — hooks
    are token-blind — so this is the sole source of the pane daemon's gauge.
    Returns (session, tokens); tokens is None if the payload didn't carry any.
    """
    if payload is None:
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {}

    session = (payload.get("session_id") or payload.get("sessionId") or "default")
    try:
        tokens = _extract_tokens(payload)
    except Exception:
        tokens = None

    if tokens is not None:
        _write_json(_paths(session)[1], {"tokens": tokens, "ts": time.time()})
    return session, tokens


def mode_tap():
    """Feed the gauge, render nothing.

    For a pane-only setup: keep the frog out of your status bar while the
    dancing pane still gets honest token-driven goofiness and shake. Wire it in
    as a statusLine command whose output you discard (or append to your own).
    """
    _tap()
    sys.exit(0)


def mode_statusline():
    session, tokens = _tap()

    state, turns = _read_think(session)
    active = state == "thinking"
    g = goofiness(tokens, turns)

    # time-based frame (statusline is stateless / re-invoked each refresh, so we
    # derive the current pose from the wall clock rather than a counter file).
    frame = int(time.time() * (FPS_ACTIVE if active else FPS_IDLE))
    params = {
        # a compact "mood frog": upright, leaning harder the goofier he gets
        "shear": (1 + 3 * g) * math.sin(frame * 0.7) if active else 0.0,
        "blink": (frame % 20) == 0,
    }

    sprite = pose(CHIBI, _CHIBI_BLINK, params)
    rows = render_pixels(sprite)

    sk = shake_px(tokens)
    if sk:
        pad = " " * random.randint(0, int(sk) + 1)
        rows = [pad + r for r in rows]

    sys.stdout.write("\n".join(rows) + _RESET + "\n")
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Mode: hook  (dispatch on hook_event_name; drives think-state + pane life)    #
# --------------------------------------------------------------------------- #


def _tmux(*args):
    import subprocess
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True,
                              timeout=3)
    except Exception:
        return None


def _in_tmux():
    return bool(os.environ.get("TMUX"))


def _spawn_pane(session, layout="bottom"):
    if not _in_tmux():
        return
    think_path, _, pane_path = _paths(session)
    # already have a live pane?
    try:
        if os.path.exists(pane_path):
            pid = open(pane_path).read().strip()
            r = _tmux("list-panes", "-a", "-F", "#{pane_id}")
            if r and pid and pid in (r.stdout or "").split():
                return
    except Exception:
        pass
    py = sys.executable or "python3"
    here = os.path.abspath(__file__)
    cmd = f"exec {py} {here} dance --session {session}"
    # -b puts the new pane *before* the current one: above it for a vertical
    # split, left of it for a horizontal one.
    axis, size = LAYOUTS.get(layout, LAYOUTS["bottom"])
    before = ["-b"] if layout in ("top", "left") else []
    split = ["split-window", axis, *before, "-l", str(size), "-d",
             "-P", "-F", "#{pane_id}", cmd]
    r = _tmux(*split)
    if r and r.returncode == 0:
        _write_json_raw(pane_path, (r.stdout or "").strip())


def _write_json_raw(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
    except Exception:
        pass


def _kill_pane(session):
    _, _, pane_path = _paths(session)
    try:
        if os.path.exists(pane_path):
            pid = open(pane_path).read().strip()
            if pid:
                _tmux("kill-pane", "-t", pid)
            os.remove(pane_path)
    except Exception:
        pass


def _prune_stale():
    """Remove state for sessions whose pane is gone (best-effort)."""
    try:
        r = _tmux("list-panes", "-a", "-F", "#{pane_id}")
        live = set((r.stdout or "").split()) if r else set()
        for fn in os.listdir(CACHE_DIR):
            if fn.endswith(".pane"):
                p = os.path.join(CACHE_DIR, fn)
                pid = open(p).read().strip()
                if pid and pid not in live:
                    sess = fn[:-5]
                    _cleanup_session(sess)
    except Exception:
        pass


def _cleanup_session(session):
    _kill_pane(session)
    for p in _paths(session):
        try:
            os.remove(p)
        except Exception:
            pass


def mode_hook(opts):
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    event = payload.get("hook_event_name") or opts.get("event") or ""
    session = (payload.get("session_id") or payload.get("sessionId")
               or opts.get("session") or "default")
    think_path = _paths(session)[0]

    if event == "SessionStart":
        _prune_stale()
        _, turns = _read_think(session)
        _write_json(think_path, {"state": "idle", "turns": 0, "ts": time.time()})
        _spawn_pane(session, opts.get("layout", "bottom"))
    elif event == "UserPromptSubmit":
        _, turns = _read_think(session)
        _write_json(think_path, {"state": "thinking", "turns": turns + 1,
                                 "ts": time.time()})
    elif event == "Stop":
        _, turns = _read_think(session)
        _write_json(think_path, {"state": "idle", "turns": turns, "ts": time.time()})
    elif event in ("SessionEnd", "Cleanup"):
        _cleanup_session(session)
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Mode: toggle / pane / cleanup / preview                                      #
# --------------------------------------------------------------------------- #


def _current_session_guess():
    """For a tmux keybind we don't have a session id; toggle the pane bound to
    the current tmux window's other pane if we tracked one, else the newest."""
    try:
        panes = os.listdir(CACHE_DIR)
    except Exception:
        return None
    panes = [p for p in panes if p.endswith(".pane")]
    if not panes:
        return None
    panes.sort(key=lambda p: os.path.getmtime(os.path.join(CACHE_DIR, p)))
    return panes[-1][:-5]


def mode_toggle(opts):
    session = opts.get("session") or _current_session_guess()
    if not session:
        sys.exit(0)
    _, _, pane_path = _paths(session)
    if os.path.exists(pane_path):
        _kill_pane(session)
    else:
        _spawn_pane(session, opts.get("layout", "bottom"))
    sys.exit(0)


def mode_pane(opts):
    session = opts.get("session") or "default"
    _write_json(_paths(session)[0], {"state": "idle", "turns": 0, "ts": time.time()})
    _spawn_pane(session, opts.get("layout", "bottom"))
    sys.exit(0)


def mode_cleanup(opts):
    session = opts.get("session")
    if session:
        _cleanup_session(session)
    else:
        _prune_stale()
    sys.exit(0)


_SHADE = {"O": "#", "B": "@", "P": ".", "N": "%", "M": "-", " ": " ", ".": " "}


def mode_preview(opts):
    """Dev aid: print sprites as plain ASCII so you can eyeball the silhouette."""
    which = opts.get("which", "frog")
    src = FROG if which != "chibi" else CHIBI
    print(f"--- {which} silhouette ({len(src[0])}w x {len(src)}h px) ---")
    for row in src:
        print("".join(_SHADE.get(ch, "?") for ch in row))
    print("\n--- half-block render (ANSI; may show as blocks) ---")
    for line in render_pixels(_colorize(src)):
        sys.stdout.write(line + _RESET + "\n")
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Entry                                                                        #
# --------------------------------------------------------------------------- #


def _parse(argv):
    mode = argv[0] if argv else "statusline"
    opts = {"session": None, "layout": None, "always": False,
            "party": False, "which": "frog", "event": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--session", "-s"):
            i += 1; opts["session"] = argv[i]
        elif a == "--layout":
            i += 1; opts["layout"] = argv[i]
        elif a == "--event":
            i += 1; opts["event"] = argv[i]
        elif a == "--which":
            i += 1; opts["which"] = argv[i]
        elif a in ("--always", "--always-dance"):
            opts["always"] = True
        elif a == "--party":
            opts["party"] = True
        i += 1
    if opts["session"] is None:
        opts["session"] = os.environ.get("CLAUDE_FROG_SESSION")
    if opts["layout"] is None:
        # env lets the hook and the tmux toggle keybind agree on a layout
        # without threading --layout through both call sites
        opts["layout"] = os.environ.get("CLAUDE_FROG_LAYOUT") or "bottom"
    if opts["layout"] not in LAYOUTS:
        opts["layout"] = "bottom"
    return mode, opts


def main():
    mode, opts = _parse(sys.argv[1:])
    try:
        if mode == "dance":
            if not opts["session"]:
                opts["session"] = "default"
            mode_dance(opts)
        elif mode == "statusline":
            mode_statusline()
        elif mode == "tap":
            mode_tap()
        elif mode == "hook":
            mode_hook(opts)
        elif mode == "toggle":
            mode_toggle(opts)
        elif mode == "pane":
            mode_pane(opts)
        elif mode == "cleanup":
            mode_cleanup(opts)
        elif mode == "preview":
            mode_preview(opts)
        else:
            sys.stderr.write(f"unknown mode: {mode}\n")
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        # never crash the statusline / hook paths
        if mode in ("statusline", "tap", "hook"):
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
