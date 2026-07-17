#!/usr/bin/env python3
"""Claude Frog — a little pixel frog who dances while Claude Code is thinking.

One file, standard library only. Two jobs:

  * `dance`       — the tmux-pane daemon: a smooth pixel frog who dances while
                    your turn is running and idles between turns.
  * `tap`         — the statusLine command: reads the token payload Claude Code
                    hands the status bar, publishes the gauge for the pane, and
                    prints nothing. (`statusline`, the old in-bar mood frog, is
                    deprecated and now behaves exactly like `tap`.)

He is also a gauge. The more context you've burned, the goofier he gets, and
past ~150k tokens he starts to shake — an honest "you're deep in it, quality's
about to soften" tell. Calm below ~40k, mostly unhinged by ~100k, full chaos by
~120k. He also changes color: green when fresh, fading toward Claude pink as
context fills, fully pink by 200k tokens.

He renders in four pixel-art styles — `snes` (default, smooth 16-bit shading),
`genesis` (punchy, dithered Mega Drive), `gba` (4-tone monochrome Game Boy LCD),
and `terraria` (high-fidelity warm, painterly indie). Pick one per session with
`--theme` or `CLAUDE_FROG_THEME`; each keeps the green->pink context gauge in its
own idiom.

Design discipline: the tap and hook paths NEVER crash and always exit 0
— a broken frog must never break your prompt. Imports stay light (stdlib only).

See README.md for install. Everything below is tunable via the constants block.
"""

from __future__ import annotations

import colorsys
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

# Color fade: fresh green at 0 tokens, fully Claude pink at/above this. Linear in
# between (see pinkness / palette_for). Starts from the very first token so the
# blush is a continuous, always-on readout of how full the window is.
PINK_FULL_TOKENS = 200_000

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
DEFAULT_LAYOUT = "top"

# Fallback goofiness when no token data is available (pane-only friend with no
# tap feeding tokens): ramp on turn count instead — unhinged by turn 4.
FALLBACK_UNHINGED_TURNS = 4

# Environment / flora: each user prompt sprouts one random prop (flower, cloud,
# rock, tree, or fallen log) that animates in and settles around the frog. Props
# live only in the dance daemon's memory, so they accumulate through a session
# and reset when the pane respawns. Set CLAUDE_FROG_FLORA=0 to turn it off.
FLORA_ENABLED = os.environ.get("CLAUDE_FROG_FLORA", "1").lower() not in (
    "0", "false", "off", "no", "")
ENTRANCE_FRAMES = 10          # frames a prop takes to grow/drop/roll/drift in
FLORA_MAX = 400               # runaway backstop only — props are a running tally
                              # that accumulates all session, so this sits far
                              # above any real prompt count (not a visible cap)
GROUND_PITCH = 9              # column spacing between ground props (> widest prop)
TIER_PITCH = 7                # rows between stacked ground rows (== tallest prop,
                              # so even trees stack exactly touching, never over-
                              # lapping; shorter props just leave a shelf gap)
CLOUD_PITCH = 8               # column spacing between parked clouds in the sky

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "claude-frog",
)

# --------------------------------------------------------------------------- #
# Palette                                                                      #
# --------------------------------------------------------------------------- #
# The frog ships in four pixel-art rendering styles (see THEMES below). The
# default "SNES" frog: instead of the flat two-green NES look, a top-lit shading
# ramp (highlight -> light -> mid -> shadow -> deep-shadow) gives the head
# volume, a specular glint lifts the eyes, and the grin gets a lit/shadowed
# cream so it reads as a real cavity. None == transparent (terminal bg).
#
# Every palette maps the SAME set of sprite keys, so one sprite renders in any
# theme — a theme is purely a recolor (plus, for Genesis, a dither). And every
# theme keeps the green->pink context gauge: each has a base (fresh) palette and
# a `pink` fade target, blended by palette_for() as the window fills.

RGB = {
    "O": (0x24, 0x3a, 0x17),   # outline (deep leaf green)
    "H": (0xd0, 0xea, 0x74),   # highlight — top of the head catching light
    "L": (0xb4, 0xd8, 0x54),   # light green (upper face)
    "B": (0x9d, 0xc8, 0x3b),   # body midtone (the signature yellow-green)
    "D": (0x74, 0x9e, 0x2f),   # shadow green (jaw, side rims)
    "S": (0x57, 0x7e, 0x24),   # deep shadow (under the chin)
    "P": (0x26, 0x26, 0x2b),   # eyes / nostrils (near-black)
    "W": (0xf2, 0xf6, 0xe6),   # eye specular (the glint)
    "N": (0xf7, 0xec, 0xd2),   # open-mouth interior, lit (warm cream)
    "R": (0xd6, 0xbf, 0x97),   # open-mouth interior, shadowed (mouth depth)
    "M": (0x24, 0x3a, 0x17),   # closed-eye / mouth line (== outline)
    " ": None,
    ".": None,
}

# Where the frog is headed: "Claude pink". As context fills, every green key in
# the shading ramp fades toward its counterpart here (see palette_for), so the
# whole frog blushes from fresh-leaf green to full Claude pink by 200k tokens.
# The ramp order (highlight -> deep shadow) is preserved so he keeps his volume.
# Keys with no entry here (eyes P/W, mouth cream N/R, transparent) never shift.
PINK = {
    "O": (0x52, 0x24, 0x38),   # outline — deep rose
    "H": (0xfb, 0xdc, 0xe8),   # highlight — pale pink catching light
    "L": (0xf7, 0xbd, 0xd2),   # light pink (upper face)
    "B": (0xf0, 0x9c, 0xbc),   # body midtone — the signature Claude pink
    "D": (0xd2, 0x77, 0x9c),   # shadow pink (jaw, side rims)
    "S": (0xab, 0x57, 0x79),   # deep shadow (under the chin)
    "M": (0x52, 0x24, 0x38),   # closed-eye / mouth line (== outline)
}

# --- Sega Genesis / Mega Drive -------------------------------------------- #
# 16-bit Sega look: a smaller, harder, oversaturated ramp (electric lime down to
# a near-black outline) with a bright specular — the punchy "blast processing"
# palette. Fewer perceived shades than the SNES, and the body midtones get
# cross-hatch DITHERED (see THEMES "dither" + _colorize) to fake extra shading
# the way the Genesis's limited palette did. Fades to a hot magenta-pink.
GENESIS = {
    "O": (0x0f, 0x1e, 0x10),   # outline — hard near-black green
    "H": (0x9b, 0xf2, 0x3a),   # highlight — electric lime
    "L": (0x6c, 0xd8, 0x2a),   # light green
    "B": (0x3f, 0xb5, 0x2a),   # body midtone — saturated (dithered)
    "D": (0x22, 0x82, 0x2c),   # shadow green
    "S": (0x14, 0x55, 0x24),   # deep shadow
    "P": (0x10, 0x12, 0x18),   # eyes / nostrils
    "W": (0xea, 0xff, 0xf0),   # eye specular — bright
    "N": (0xf6, 0xe7, 0x9c),   # open-mouth interior, lit
    "R": (0xc8, 0x8a, 0x3a),   # open-mouth interior, shadowed
    "M": (0x0f, 0x1e, 0x10),   # closed-eye / mouth line (== outline)
    " ": None,
    ".": None,
}
GENESIS_PINK = {
    "O": (0x38, 0x0c, 0x22),   # outline — deep magenta
    "H": (0xff, 0x9a, 0xd4),   # highlight — bright pink
    "L": (0xf7, 0x5c, 0xb0),   # light pink
    "B": (0xe8, 0x2a, 0x8c),   # body midtone — hot magenta-pink
    "D": (0xb0, 0x1e, 0x6e),   # shadow pink
    "S": (0x74, 0x14, 0x4a),   # deep shadow
    "M": (0x38, 0x0c, 0x22),   # closed-eye / mouth line (== outline)
}

# --- Game Boy Advance ------------------------------------------------------ #
# The iconic 4-tone monochrome Game Boy LCD (the classic pea-green DMG screen).
# Many sprite keys collapse onto just four greens, flattening the shading into
# the blocky Game Boy look. The gauge survives as a TINT shift: as context
# fills, the whole LCD slides from green toward a dusky rose (like a red-tinted
# screen), so every tone — eyes and mouth included — blushes together.
_GBA_DARKEST, _GBA_DARK = (0x0f, 0x38, 0x0f), (0x30, 0x62, 0x30)
_GBA_LIGHT, _GBA_LIGHTEST = (0x8b, 0xac, 0x0f), (0x9b, 0xbc, 0x0f)
GBA = {
    "O": _GBA_DARKEST,         # outline
    "H": _GBA_LIGHTEST,        # highlight
    "L": _GBA_LIGHT,           # light face
    "B": _GBA_LIGHT,           # body midtone
    "D": _GBA_DARK,            # shadow
    "S": _GBA_DARKEST,         # deep shadow
    "P": _GBA_DARKEST,         # eyes / nostrils
    "W": _GBA_LIGHTEST,        # eye specular
    "N": _GBA_LIGHT,           # open-mouth interior, lit
    "R": _GBA_DARK,            # open-mouth interior, shadowed
    "M": _GBA_DARKEST,         # closed-eye / mouth line
    " ": None,
    ".": None,
}
_GBR_DARKEST, _GBR_DARK = (0x2e, 0x0c, 0x18), (0x6b, 0x28, 0x3e)
_GBR_LIGHT, _GBR_LIGHTEST = (0xc2, 0x63, 0x86), (0xe6, 0x9d, 0xba)
GBA_PINK = {
    "O": _GBR_DARKEST, "H": _GBR_LIGHTEST, "L": _GBR_LIGHT, "B": _GBR_LIGHT,
    "D": _GBR_DARK, "S": _GBR_DARKEST, "P": _GBR_DARKEST, "W": _GBR_LIGHTEST,
    "N": _GBR_LIGHT, "R": _GBR_DARK, "M": _GBR_DARKEST,
}

# --- Terraria -------------------------------------------------------------- #
# The high-fidelity "32-bit" indie look: Terraria's (Re-Logic) hand-painted 2D
# sandbox art. Where the SNES ramp is cool and smooth, this one is warmer and
# richer — a fuller earthy grass-green ramp with deep DESATURATED outlines (not
# pure black, the way Terraria rims its sprites) and creamy warm highlights. The
# whole lit midrange (light, midtone, shadow) gets a heavy cross-hatch DITHER (see
# THEMES "dither" + _colorize) to fake the painterly gradient shading Terraria
# layers by hand — only the brightest highlight and the specular stay clean.
# Fades from fresh jungle green to a warm Claude rose.
TERRARIA = {
    "O": (0x20, 0x2c, 0x18),   # outline — deep desaturated forest (warm, not black)
    "H": (0xcf, 0xdc, 0x82),   # highlight — warm pale yellow-green catching light
    "L": (0xa6, 0xc0, 0x58),   # light green (upper face) — dithered
    "B": (0x7a, 0x9c, 0x3e),   # body midtone — warm grass green (dithered)
    "D": (0x54, 0x74, 0x2e),   # shadow green (dithered)
    "S": (0x38, 0x52, 0x24),   # deep shadow
    "P": (0x1b, 0x18, 0x14),   # eyes / nostrils — warm near-black
    "W": (0xf4, 0xf1, 0xd8),   # eye specular — warm glint
    "N": (0xf1, 0xd7, 0xa4),   # open-mouth interior, lit — warm cream
    "R": (0xbe, 0x8f, 0x58),   # open-mouth interior, shadowed
    "M": (0x20, 0x2c, 0x18),   # closed-eye / mouth line (== outline)
    " ": None,
    ".": None,
}
TERRARIA_PINK = {
    "O": (0x3e, 0x1c, 0x2b),   # outline — deep warm rose
    "H": (0xf7, 0xd2, 0xe1),   # highlight — warm pale pink
    "L": (0xef, 0xab, 0xc8),   # light pink
    "B": (0xdd, 0x82, 0xa8),   # body midtone — warm Claude rose
    "D": (0xb2, 0x5f, 0x86),   # shadow pink
    "S": (0x7e, 0x42, 0x5e),   # deep shadow
    "M": (0x3e, 0x1c, 0x2b),   # closed-eye / mouth line (== outline)
}

# Theme registry. Each theme is (base palette, pink fade target, dither keys).
# `dither` is the set of palette keys that get cross-hatch shading (Genesis);
# empty for the smooth-shaded SNES and the flat-LCD GBA. DEFAULT_THEME keeps the
# original green SNES frog for anyone who never picks one.
THEMES = {
    "snes":     {"base": RGB,      "pink": PINK,          "dither": ()},
    "genesis":  {"base": GENESIS,  "pink": GENESIS_PINK,  "dither": ("B", "L")},
    "gba":      {"base": GBA,      "pink": GBA_PINK,      "dither": ()},
    "terraria": {"base": TERRARIA, "pink": TERRARIA_PINK, "dither": ("L", "B", "D")},
}
DEFAULT_THEME = "snes"

# Friendly spellings a human might type at the terminal (`claude SEGA`) or set in
# CLAUDE_FROG_THEME. Canonical names map to themselves via THEMES; everything
# here is an alias for one. Matching is case- and punctuation-insensitive (see
# resolve_theme), so "Game Boy", "gameboy", and "GBA" all land on gba.
THEME_ALIASES = {
    "supernintendo": "snes", "nintendo": "snes", "super": "snes", "16bit": "snes",
    "sega": "genesis", "megadrive": "genesis", "mega": "genesis", "md": "genesis",
    "gameboy": "gba", "gameboyadvance": "gba", "gameboyadvanced": "gba",
    "advance": "gba", "gb": "gba", "dmg": "gba",
    "relogic": "terraria", "terra": "terraria", "32bit": "terraria",
}


def resolve_theme(name):
    """Canonical theme name for any accepted spelling, or None if unrecognized.

    Case- and punctuation-insensitive: "Game Boy", "gameboy", "GBA" -> "gba".
    Returns None (not the default) for junk, so callers can tell "no theme
    named" apart from "use the default" — the shell launcher relies on that.
    """
    if not name:
        return None
    key = "".join(ch for ch in str(name).lower() if ch.isalnum())
    if key in THEMES:
        return key
    return THEME_ALIASES.get(key)


def theme_spec(theme):
    """Resolve a theme name to its spec, falling back to the default."""
    return THEMES.get(theme, THEMES[DEFAULT_THEME])


# --------------------------------------------------------------------------- #
# Sprites (authored ragged; padded to a rectangle at load time)               #
# --------------------------------------------------------------------------- #
# Emoji-frog spirit: two eye bumps riding on a wide round head, dark inset eyes,
# nostril dots, and a big open grin. No seams — he's a frog, not a plushie. The
# shading ramp runs top (H) to bottom (S) so a single top light gives him depth.

_FROG_SRC = [
    "  OOOO       OOOO  ",   # tops of the two eye bumps
    " OHWPLO     OHWPLO ",   # dark inset eyes with a specular glint (W)
    " OHPPBOOOOOOOHPPBO ",   # bumps settle onto a wide head
    "OHHHHHHHHHHHHHHHHHO",   # brow — brightest, catching the light
    "OLLLLPLLLLLLLPLLLLO",   # upper face + nostrils
    "OBBBBBBBBBBBBBBBBBO",   # cheeks — midtone
    "ODBOOOOOOOOOOOOOBDO",   # grin: top lip, side rims fall into shadow
    "ODBONNNNNNNNNNNOBDO",   # open mouth: lit cream
    "ODBORRRRRRRRRRROBDO",   # open mouth: shadowed cream (depth)
    " OSDDDDDDDDDDDDDSO ",   # jaw / body in shadow
    "  ODBO       OBDO  ",   # legs
    "  OOO         OOO  ",   # feet
]

# Blink overlay: the eyes squeeze shut to happy little arcs (lids in highlight).
_FROG_BLINK = {
    1: " OHHHHO     OHHHHO ",
    2: " OH__BOOOOOOOH__BO ",
}

# The frog from behind — the one pose that can't be squeezed out of the front
# sprite by shear/mirror/flip, because it needs geometry the front view doesn't
# have: no face, and a rump. Same width and height as FROG so he swaps in
# cleanly mid-move (see the `back` param in pose). The eye bumps still ride above
# the crown — you're seeing their backs — and the shading ramp runs the same way,
# top-lit, except the cheeks get their own round highlight below the waist.
_FROG_BACK_SRC = [
    "  OOOO       OOOO  ",   # backs of the two eye bumps
    " OHHHHO     OHHHHO ",   # no eyes on this side
    " OHHHBOOOOOOOHHHBO ",   # bumps settle onto a wide head
    "OHHHHHHHHHHHHHHHHHO",   # crown — brightest, catching the light
    "OLLLLLLLLLLLLLLLLLO",   # nape
    " OBBBBBBBBBBBBBBBO ",   # back — midtone, tapering to the waist
    "   OLLLLLOLLLLLO   ",   # rump: two round cheeks, lit, split by a seam
    "   OBBBBBOBBBBBO   ",   # cheeks fall to midtone
    "   ODBBBDODBBBDO   ",   # side rims fall into shadow
    "    OSDDSOSDDSO    ",   # undersides of the cheeks — deep shadow
    "  ODBO       OBDO  ",   # legs
    "  OOO         OOO  ",   # feet
]
# The rump is drawn narrower than the head on purpose: it leaves three columns of
# clearance either side, which is exactly the travel hip_shift needs at full
# amplitude. Widen the cheeks and the shake clips against the sprite's edge.

Pixel = tuple  # (r, g, b) or None


def _load(src):
    """Ragged rows -> rectangular grid of palette keys (space-padded)."""
    w = max(len(r) for r in src)
    return [list(r.ljust(w)) for r in src]


FROG = _load(_FROG_SRC)
FROG_BACK = _load(_FROG_BACK_SRC)


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


def _colorize(grid, palette=RGB, dither=()):
    """Palette-key grid -> pixel grid of (r,g,b)|None.

    `dither` is a set of palette keys that get cross-hatch shading: on every
    other pixel (checkerboard by x+y parity) the color is darkened, faking an
    extra shade the way the Sega Genesis's limited palette did. Empty by default
    so the smooth-shaded themes pay nothing.
    """
    if not dither:
        return [[palette.get(ch) for ch in row] for row in grid]
    dset = set(dither)
    out = []
    for y, row in enumerate(grid):
        line = []
        for x, ch in enumerate(row):
            col = palette.get(ch)
            if col is not None and ch in dset and (x + y) % 2:
                col = (int(col[0] * 0.72), int(col[1] * 0.72), int(col[2] * 0.72))
            line.append(col)
        out.append(line)
    return out


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


# The rump band of the back sprite, as a fraction of its height: everything
# below the waist and above the legs. Expressed as fractions, not row indices,
# so redrawing FROG_BACK at another size doesn't silently shift what shakes.
HIP_BAND = (0.5, 0.84)


def hip_shift(grid, amount):
    """Slide only the rump rows sideways — the shake, with the feet planted.

    shear() can't do this: its lever is anchored at the feet and grows toward the
    head, so it swings the wrong end of the frog. This moves the cheeks alone and
    leaves head, legs and feet where they are.
    """
    dx = int(round(amount))
    if not dx:
        return grid
    h = len(grid)
    w = len(grid[0])
    top = int(h * HIP_BAND[0])
    bot = int(h * HIP_BAND[1])
    out = []
    for y, row in enumerate(grid):
        if not (top <= y < bot):
            out.append(row)
            continue
        shifted = [None] * w
        for x in range(w):
            nx = x + dx
            if 0 <= nx < w:
                shifted[nx] = row[x]
        out.append(shifted)
    return out


def turn_squeeze(grid, scale):
    """Compress a grid horizontally toward its centerline (1.0 = full width).

    Sampling the source at spread positions reads as the sprite rotating about
    its vertical axis: drive `scale` from 1 down to ~0 and it goes edge-on, a
    one-column sliver. Swap sprites at the sliver and widen back out and the eye
    reads a turn, not a teleport. Only the twerk uses this (see `_m_twerk`).
    """
    if scale >= 0.999:
        return grid
    h = len(grid)
    w = len(grid[0])
    c = (w - 1) / 2.0
    s = max(scale, 0.08)                 # keep a sliver so he never fully vanishes
    out = [[None] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            sx = int(round(c + (x - c) / s))
            if 0 <= sx < w:
                out[y][x] = grid[y][sx]
    return out


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


def pinkness(tokens):
    """0..1 how far the frog has faded from green toward Claude pink.

    Linear from the first token to PINK_FULL_TOKENS. Unknown token count (a
    pane-only friend with no tap feeding the gauge) stays green.
    """
    if tokens is None:
        return 0.0
    return _clamp(tokens / max(1, PINK_FULL_TOKENS))


def _blend(base, target, t):
    """Blend two RGB colors in HLS space so the fade stays vivid.

    A straight RGB lerp between green and pink sags through a muddy tan at the
    midpoint. Blending hue/lightness/saturation instead — and taking the SHORT
    hue arc, which for green->pink runs the warm way (chartreuse -> orange ->
    coral -> pink) — keeps saturation up the whole way across.
    """
    bh, bl, bs = colorsys.rgb_to_hls(*(c / 255.0 for c in base))
    th, tl, ts = colorsys.rgb_to_hls(*(c / 255.0 for c in target))
    dh = th - bh                      # shortest way around the hue wheel
    if dh > 0.5:
        dh -= 1.0
    elif dh < -0.5:
        dh += 1.0
    h = (bh + dh * t) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, bl + (tl - bl) * t, bs + (ts - bs) * t)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def palette_for(tokens, theme=DEFAULT_THEME):
    """The theme's base palette blended toward its pink target by token usage.

    Returns the base (fresh) palette unchanged at zero tokens (or when tokens
    are unknown) — identity, so nothing downstream pays for the common case — a
    fully faded palette at/above PINK_FULL_TOKENS, and a vivid HLS blend (see
    _blend) in between. Keys absent from the theme's pink target (e.g. the SNES
    eyes / mouth cream, transparent) pass through untouched.
    """
    spec = theme_spec(theme)
    base_palette, target_palette = spec["base"], spec["pink"]
    t = pinkness(tokens)
    if t <= 0.0:
        return base_palette
    out = {}
    for key, base in base_palette.items():
        target = target_palette.get(key)
        if base is None or target is None:
            out[key] = base
        else:
            out[key] = _blend(base, target, t)
    return out


# --------------------------------------------------------------------------- #
# Choreographer — picks moves and emits per-frame pose params                  #
# --------------------------------------------------------------------------- #

# A "move" is (name, base_frames, fn) where fn(t, g) -> dict of pose params:
#   dx, dy    : integer stage offset (booping / hopping)
#   shear     : horizontal lean
#   drop      : rows to squash (crouch)
#   mirror    : face the other way
#   flip      : upside down (specials only)
#   back      : turn his back to you (swaps in the back sprite)
#   hips      : slide the rump sideways (only means anything with `back`)
#   turn      : horizontal squeeze 0..1 (edge-on..full) for the twerk's pivot
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


# Twerk timing: a lenticular pivot in, the shake, then a pivot back out. The
# frame counts live here so the SPECIALS entry and the move body can't drift
# apart — they must agree, or the phase boundaries land on the wrong frames.
TWERK_TURN = 7                              # frames per pivot, each way (odd -> a
                                            # clean edge-on middle frame)
TWERK_SHAKE = 24                            # frames of actual shaking
TWERK_FRAMES = TWERK_TURN * 2 + TWERK_SHAKE


def _m_twerk(t, g):
    """He pivots around, shakes it at you, and pivots back. Shameless in g.

    Three phases across the move: he squeezes edge-on and swaps front->back at
    the sliver (`turn`), shakes with the hips leading and the body a beat behind
    on `dy`, then pivots back out the same way. The sprite swap is hidden inside
    the edge-on frame, so the turn reads as a turn, not a teleport.

    `beats` must stay well under half of TWERK_SHAKE: at exactly half, every shake
    frame samples a zero crossing and he just stands there with his back turned.
    Goofiness buys amplitude, not speed.
    """
    tin = TWERK_TURN / TWERK_FRAMES         # pivot-away ends here
    tout = (TWERK_TURN + TWERK_SHAKE) / TWERK_FRAMES   # pivot-back starts here
    # span the pivot's frames across u in [0, 1] so the *middle* frame lands at
    # u = 0.5 — edge-on, where the sprite swap hides. (t/tin alone tops out at
    # (TWERK_TURN-1)/TWERK_TURN and skips right over the sliver.)
    piv = TWERK_TURN / (TWERK_TURN - 1.0)
    if t < tin:                             # pivot away: front squeezes, swaps, widens
        u = (t / tin) * piv
        return {"back": u >= 0.5, "turn": abs(math.cos(u * math.pi)), "hips": 0.0}
    if t >= tout:                           # pivot back: back squeezes, swaps, widens
        u = ((t - tout) / (1.0 - tout)) * piv
        return {"back": u < 0.5, "turn": abs(math.cos(u * math.pi)), "hips": 0.0}
    s = (t - tin) / (tout - tin)            # 0..1 across the shake, fully turned
    beats = 3 + 2 * g                       # pops per shake — Nyquist says <12
    amp = 1 + 2 * g                         # how far the cheeks travel
    swing = math.sin(s * math.pi * 2 * beats)
    ramp = max(0.0, min(1.0, s / 0.2, (1.0 - s) / 0.2))   # ease in/out at the seams
    return {"back": True, "turn": 1.0,
            "hips": amp * ramp * swing,
            "dy": -abs(int(round((0.6 + 1.4 * g) * ramp * swing))),
            "shear": 0.5 * g * ramp * swing}


IDLE_MOVES = [(_m_idle_breathe, 24), (_m_idle_sit, 16), (_m_idle_breathe, 30)]
ACTIVE_MOVES = [
    (_m_bob, 12), (_m_sway, 16), (_m_hop, 14), (_m_wiggle, 12), (_m_nod, 10),
    (_m_boop(1), 18), (_m_boop(-1), 18),
]
SPECIALS = [(_m_bigjump, 16), (_m_backflip, 18), (_m_spinout, 20),
            (_m_twerk, TWERK_FRAMES)]


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


def pose(base, blink_overlay, params, palette=RGB, dither=(), back=None):
    """Build a colorized pixel sprite for a frame from base grid + params.

    `palette` is the (possibly pink-shifted) color map to paint with; it
    defaults to the base green RGB so callers that don't care about the token
    fade — previews, tests — get the plain frog. `dither` is the theme's
    cross-hatch key set (see _colorize), empty for smooth-shaded themes.

    `back` is the turned-around grid (FROG_BACK), swapped in for the `back` param
    — the only pose that isn't a transform of `base`. Callers with no back view
    pass none and simply never turn around; blinking is skipped while he's
    facing away, since his eyes are on the other side.
    """
    turned = params.get("back") and back is not None
    grid = back if turned else base
    if params.get("blink") and not turned:
        grid = _apply_blink(grid, blink_overlay)
    else:
        grid = [row[:] for row in grid]
    if params.get("mirror"):
        grid = flip_h(grid)
    if params.get("flip"):
        grid = flip_v(grid)
    px = _colorize(grid, palette, dither)
    drop = params.get("drop", 0)
    if drop:
        px = squash(px, drop)
    if turned:
        px = hip_shift(px, params.get("hips", 0.0))
    px = shear(px, params.get("shear", 0.0))
    px = turn_squeeze(px, params.get("turn", 1.0))
    return px


# --------------------------------------------------------------------------- #
# Environment — props that sprout around the frog, one per user prompt          #
# --------------------------------------------------------------------------- #
# A little diorama that fills in as you work: every prompt the dance pane sprouts
# one random prop, animated in and then left standing. Pane-only eye candy,
# held purely in the daemon's memory so the scene grows
# through a session and resets when the pane respawns. Props are painted BEHIND
# the frog so he always stands in the foreground.
#
# Props use a fixed natural palette regardless of the frog's console theme — a
# rock is grey in any decade. Flower petals are the exception: each bloom is
# recolored to a random hue. None == transparent (terminal bg / whatever's
# behind it on the stage).
FLORA = {
    "x": (0x5a, 0x8f, 0x2e),   # stem / leaf green
    "v": (0x3c, 0x63, 0x1f),   # leaf shadow
    "g": (0x4f, 0x9d, 0x3a),   # tree foliage
    "f": (0x33, 0x6e, 0x28),   # tree foliage shadow
    "k": (0x7a, 0x53, 0x2f),   # bark / trunk / log wood
    "j": (0x53, 0x37, 0x1e),   # bark shadow
    "e": (0xcf, 0xb0, 0x86),   # cut-log end grain (cream)
    "r": (0x9a, 0x9d, 0xa3),   # rock, lit
    "q": (0x63, 0x66, 0x6d),   # rock, shadow
    "c": (0xf2, 0xf5, 0xfb),   # cloud
    "d": (0xcf, 0xd8, 0xe6),   # cloud, underside
    "*": (0xff, 0x6d, 0x9a),   # flower petal   (overridden per bloom)
    "o": (0xff, 0xe0, 0x7a),   # flower center  (overridden per bloom)
    " ": None,
    ".": None,
}

# Prop sprites (authored bottom-anchored: the last row is the one that meets the
# floor, so growth animations reveal from the bottom up). Each char is one pixel.
_FLOWER_SRC = [
    ".*.",
    "*o*",
    ".*.",
    ".x.",
    ".x.",
]
_TREE_SRC = [
    "  ggg  ",
    " ggfgg ",
    "gggfggg",
    " ggfgg ",
    "  gkg  ",
    "   k   ",
    "  kjk  ",
]
_ROCK_SRC = [
    " rrr ",
    "rrrrq",
    "qqqqq",
]
_LOG_SRC = [
    "ekkkkk",
    "ejkkjk",
    "ekkkkk",
]
_CLOUD_SRC = [
    " cccc ",
    "cccccc",
    " dddd ",
]

FLOWER = _load(_FLOWER_SRC)
TREE = _load(_TREE_SRC)
ROCK = _load(_ROCK_SRC)
LOG = _load(_LOG_SRC)
CLOUD = _load(_CLOUD_SRC)

# The theme-independent props colorize once; flowers vary per bloom (below).
_PROP_PIX = {
    "tree": _colorize(TREE, FLORA),
    "rock": _colorize(ROCK, FLORA),
    "log": _colorize(LOG, FLORA),
    "cloud": _colorize(CLOUD, FLORA),
}

PROP_KINDS = ("flower", "tree", "rock", "log", "cloud")


def _flower_palette(hue):
    """A FLORA palette with the petal/center recolored to a random-hued bloom."""
    pr, pg, pb = colorsys.hls_to_rgb(hue, 0.62, 0.85)          # vivid petal
    cr, cg, cb = colorsys.hls_to_rgb((hue + 0.08) % 1.0, 0.74, 0.9)  # warm eye
    pal = dict(FLORA)
    pal["*"] = (int(pr * 255), int(pg * 255), int(pb * 255))
    pal["o"] = (int(cr * 255), int(cg * 255), int(cb * 255))
    return pal


def _prop_sprite(prop):
    """The (r,g,b)|None pixel grid for a prop (flowers colorize per bloom)."""
    if prop["kind"] == "flower":
        return _colorize(FLOWER, _flower_palette(prop["hue"]))
    return _PROP_PIX[prop["kind"]]


class Scene:
    """The frog's accumulating diorama, held in the dance daemon's memory.

    `spawn` adds one prop per user prompt and nothing ever removes them — the
    scene is a running per-session tally of prompts. Ground props (flower/tree/
    rock/log) alternate left/right of the frog and step outward; when a row runs
    out of room they wrap up into a new tier stacked above, so a long session
    fills the pane like a growing garden. Clouds drift in once and then park in
    the sky, filling it left-to-right. `blits` is pure: given the current frame
    and the frog's resting footprint it returns (sprite, x, y) tuples to paint,
    applying each prop's entrance animation and its resting tier/parked slot.
    Nothing here does I/O or can raise on bad input — the daemon still guards it,
    but it aims never to need it.
    """

    def __init__(self, rng=None):
        self.props = []
        self.rng = rng or random
        self._left = 0       # ground props placed on each side so far...
        self._right = 0      # ...used as the monotonic outward step index
        self._clouds = 0     # clouds parked so far (drives sky packing)

    def spawn(self, frame, cols):
        kind = self.rng.choice(PROP_KINDS)
        prop = {"kind": kind, "birth": frame, "hue": self.rng.random(),
                "phase": self.rng.random() * math.tau}
        if kind == "cloud":
            prop["cidx"] = self._clouds       # sky slot (packed in blits)
            prop["dir"] = self.rng.choice((-1, 1))   # entrance drift direction
            self._clouds += 1
        else:
            # Alternate sides by how many ground props exist so the garden grows
            # symmetrically regardless of how clouds interleave.
            side = -1 if (self._left + self._right) % 2 == 0 else 1
            prop["side"] = side
            if side < 0:
                prop["slot"] = self._left
                self._left += 1
            else:
                prop["slot"] = self._right
                self._right += 1
        self.props.append(prop)
        # Props are meant to remain (a tally), so this only guards runaway memory
        # on an implausibly long session — well above any real prompt count.
        if len(self.props) > FLORA_MAX:
            self.props.pop(0)

    def blits(self, frame, cols, stage_h, frog_x, frog_w):
        """(sprite, x, y) paints for this frame, entrance animations applied."""
        gap = 1
        out = []
        for p in self.props:
            spr = _prop_sprite(p)
            ph, pw = len(spr), len(spr[0])
            prog = max(0.0, min(1.0, (frame - p["birth"]) / max(1, ENTRANCE_FRAMES)))
            if p["kind"] == "cloud":
                out.append(self._cloud_blit(p, spr, prog, frame, cols))
                continue
            # Ground props stand on the floor, stepping outward from the frog on
            # a fixed column pitch (wider than any prop) so neighbours of
            # different widths never collide. Once a row fills the available
            # half-width they wrap up into a new tier stacked above.
            if p["side"] < 0:
                per_row = max(1, (frog_x - gap) // GROUND_PITCH)
            else:
                per_row = max(1, (cols - frog_x - frog_w - gap) // GROUND_PITCH)
            tier, col = divmod(p["slot"], per_row)
            if p["side"] < 0:
                x = frog_x - gap - col * GROUND_PITCH - pw
            else:
                x = frog_x + frog_w + gap + col * GROUND_PITCH
            floor = stage_h - tier * TIER_PITCH     # row the prop's feet rest on
            y = floor - ph
            if p["kind"] in ("flower", "tree"):
                # grow: reveal from the bottom up, then a gentle breeze
                rows = max(1, int(round(ph * prog)))
                spr = spr[ph - rows:]
                y = floor - rows
                if prog >= 1.0 and p["kind"] == "flower":
                    x += int(round(math.sin(frame * 0.12 + p["phase"])))
            elif p["kind"] == "rock":
                y -= int(round((1.0 - prog) * 6))   # drop in and settle
            elif p["kind"] == "log":
                off = int(round((1.0 - prog) * (pw + 4)))
                x += off if p["side"] > 0 else -off  # roll in from outside
            out.append((spr, x, y))
        return out

    def _cloud_blit(self, p, spr, prog, frame, cols):
        """A cloud drifts in from off-edge to its parked sky slot, then holds."""
        cw = len(spr[0])
        per_sky = max(1, cols // CLOUD_PITCH)
        row, col = divmod(p["cidx"], per_sky)
        parked_x = 1 + col * CLOUD_PITCH
        parked_y = row % 3                       # keep clouds up in the sky band
        entry_x = float(-cw - 2) if p["dir"] > 0 else float(cols + 2)
        x = entry_x + (parked_x - entry_x) * prog
        if prog >= 1.0:                          # parked: a gentle idle sway
            x = parked_x + math.sin(frame * 0.05 + p["phase"])
        return (spr, int(round(x)), parked_y)


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
    theme = opts.get("theme", DEFAULT_THEME)
    dither = theme_spec(theme)["dither"]
    out = sys.stdout
    chor = Choreographer()
    scene = Scene() if FLORA_ENABLED else None
    # Don't backfill props for turns that already happened before this pane
    # started (e.g. a mid-session toggle) — only sprout on prompts from here on.
    # The baseline comes from `--since` (captured in the spawning hook, before the
    # pane booted) so a fast first prompt can't slip in before we read it here.
    last_turns = opts["since"] if opts.get("since") is not None \
        else _read_think(session)[1]
    frame = 0

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
            # party maxes everything, so blush him fully pink too
            palette = palette_for(PINK_FULL_TOKENS if party else tokens, theme)

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
            sprite = pose(FROG, _FROG_BLINK, params, palette, dither, FROG_BACK)
            sh_, sw_ = len(sprite), len(sprite[0])

            rest_x = (cols - sw_) // 2         # frog's resting center (props plant here)
            base_x = rest_x + params.get("dx", 0)
            base_y = stage_h - sh_ + params.get("dy", 0)
            if sk:
                base_x += random.randint(-int(sk), int(sk))
                base_y += random.randint(-int(sk), int(sk))
            base_x = max(-2, min(cols - sw_ + 2, base_x))
            base_y = max(-2, min(stage_h - 2, base_y))

            # Environment: sprout a prop per new prompt, then paint the scene
            # behind the frog. Guarded so a prop bug can never stop him dancing.
            if scene is not None:
                try:
                    for _ in range(max(0, turns - last_turns)):
                        scene.spawn(frame, cols)
                    last_turns = turns
                    for spr, px, py in scene.blits(frame, cols, stage_h,
                                                   rest_x, sw_):
                        blit(stage, spr, px, py)
                except Exception:
                    pass

            blit(stage, sprite, base_x, base_y)

            # No trailing newline: emitting one on the bottom row scrolls the
            # pane, which would lift him a row off the floor he stands on.
            frame_rows = [r + "\x1b[K" for r in render_pixels(stage)[:rows]]
            out.write("\x1b[H" + "\n".join(frame_rows))
            out.flush()

            frame += 1
            fps = FPS_ACTIVE if active else FPS_IDLE
            time.sleep(1.0 / fps)
    except SystemExit:
        raise
    except Exception:
        cleanup()


# --------------------------------------------------------------------------- #
# Mode: tap (silent token gauge — the statusLine command)                      #
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
    """Read the statusLine payload and publish the token gauge to session state.

    The statusLine is the only surface Claude Code hands token usage to — hooks
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

    The only surface Claude Code hands token usage to is the statusLine, so the
    dancing pane's goofiness / shake / pink fade all depend on this being wired
    there. It prints nothing — your status bar stays yours (or empty).

    The old `statusline` mode (a mood frog drawn in the status bar itself) is
    deprecated: it now lands here too, so existing settings.json wirings keep
    feeding the pane and simply stop drawing in the bar.
    """
    _tap()
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


def _spawn_pane(session, layout=DEFAULT_LAYOUT, theme=DEFAULT_THEME):
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
    # theme is baked into the daemon's command so it stays fixed for the life of
    # the pane, even if the env changes later in the session.
    # `--since` captures the turn count *now*, in the hook process, so the diorama
    # baseline is fixed before the pane exists. Reading it inside the daemon after
    # it boots would race a fast first UserPromptSubmit and eat the first prop.
    since = _read_think(session)[1]
    cmd = (f"exec {py} {here} dance --session {session} "
           f"--theme {theme} --since {since}")
    # -b puts the new pane *before* the current one: above it for a vertical
    # split, left of it for a horizontal one.
    axis, size = LAYOUTS.get(layout, LAYOUTS[DEFAULT_LAYOUT])
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
        _spawn_pane(session, opts.get("layout", DEFAULT_LAYOUT),
                    opts.get("theme", DEFAULT_THEME))
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
        _spawn_pane(session, opts.get("layout", DEFAULT_LAYOUT),
                    opts.get("theme", DEFAULT_THEME))
    sys.exit(0)


def mode_pane(opts):
    session = opts.get("session") or "default"
    _write_json(_paths(session)[0], {"state": "idle", "turns": 0, "ts": time.time()})
    _spawn_pane(session, opts.get("layout", DEFAULT_LAYOUT),
                opts.get("theme", DEFAULT_THEME))
    sys.exit(0)


def mode_cleanup(opts):
    session = opts.get("session")
    if session:
        _cleanup_session(session)
    else:
        _prune_stale()
    sys.exit(0)


_SHADE = {"O": "#", "H": "^", "L": "+", "B": "@", "D": "o", "S": "=",
          "P": ".", "W": "*", "N": "%", "R": ":", "M": "-", "_": "-",
          " ": " ", ".": " "}


def mode_preview(opts):
    """Dev aid: print the sprite as plain ASCII so you can eyeball the silhouette."""
    theme = opts.get("theme", DEFAULT_THEME)
    spec = theme_spec(theme)
    src = FROG
    print(f"--- frog silhouette ({len(src[0])}w x {len(src)}h px) ---")
    for row in src:
        print("".join(_SHADE.get(ch, "?") for ch in row))
    print(f"\n--- {theme} render (ANSI; may show as blocks) ---")
    for line in render_pixels(_colorize(src, spec["base"], spec["dither"])):
        sys.stdout.write(line + _RESET + "\n")
    sys.exit(0)


# Events the frog hooks into (see install/settings-hooks.json for the shape).
FROG_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")

# The comment install.sh writes above the launcher `source` line; doctor greps
# for it to confirm the launcher is installed. Keep in sync with install.sh.
MARKER = "claude-frog theme launcher"


def _frog_cmd(kind):
    """The command string baked into settings.json for `kind` (hook/tap)."""
    return f"python3 {os.path.abspath(__file__)} {kind}"


def _is_frog_cmd(cmd):
    return isinstance(cmd, str) and "claude_frog.py" in cmd


def _event_has_frog_hook(groups):
    """True if this event's hook list already runs the frog (any group)."""
    if not isinstance(groups, list):
        return False
    for g in groups:
        for h in (g or {}).get("hooks", []) if isinstance(g, dict) else []:
            if _is_frog_cmd((h or {}).get("command")):
                return True
    return False


def mode_install_settings(opts):
    """Merge the frog's statusLine tap + hooks into ~/.claude/settings.json.

    Deliberately conservative: preserves everything already in the file, backs
    it up first, and is idempotent (re-running changes nothing). An existing
    non-frog statusLine is left untouched — Claude Code allows only one, so we
    won't clobber yours; the message points you at the compose wrapper instead.
    A frog statusLine still on the deprecated `statusline` mode is migrated to
    `tap`. Unlike the tap/hook paths this is an explicit action, so it may fail
    loudly rather than swallowing errors.
    """
    path = opts.get("settings") or os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
        "settings.json",
    )
    # "statusline" (the deprecated in-bar frog) and anything unrecognized both
    # land on tap; "none" skips the statusLine entirely.
    sl_mode = opts.get("statusline_mode") or "tap"
    if sl_mode != "none":
        sl_mode = "tap"

    # Load existing settings, refusing to clobber a file we can't parse.
    data = {}
    existed = os.path.exists(path)
    if existed:
        with open(path) as f:
            text = f.read()
        if text.strip():
            try:
                data = json.loads(text)
            except ValueError as e:
                sys.stderr.write(
                    f"✗ {path} isn't valid JSON ({e}); leaving it untouched.\n"
                    f"  Fix or move it, then re-run.\n")
                sys.exit(1)
        if not isinstance(data, dict):
            sys.stderr.write(f"✗ {path} isn't a JSON object; leaving it alone.\n")
            sys.exit(1)

    changed, notes = [], []
    hook_cmd = _frog_cmd("hook")

    # statusLine (only one allowed) — add if absent, never overwrite yours.
    if sl_mode != "none":
        sl = data.get("statusLine")
        cmd = (sl or {}).get("command") if isinstance(sl, dict) else None
        if not sl:
            data["statusLine"] = {"type": "command", "command": _frog_cmd("tap")}
            changed.append("statusLine → tap (token feed)")
        elif _is_frog_cmd(cmd):
            if cmd.rstrip().endswith(" statusline"):
                data["statusLine"] = {"type": "command",
                                      "command": _frog_cmd("tap")}
                changed.append("statusLine: statusline → tap "
                               "(the in-bar frog is deprecated)")
            else:
                notes.append("statusLine already taps the frog — left as-is")
        else:
            notes.append(
                "you already have a statusLine — left as-is. Make sure it "
                "pipes the payload to `claude_frog.py tap` (see "
                "install/statusline-compose.sh) or the pane loses its gauge")

    # hooks — append a frog group per event, skipping any already present.
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        sys.stderr.write("✗ settings 'hooks' isn't an object; leaving it alone.\n")
        sys.exit(1)
    for ev in FROG_HOOK_EVENTS:
        groups = hooks.setdefault(ev, [])
        if not isinstance(groups, list):
            notes.append(f"hooks.{ev} isn't a list — skipped")
            continue
        if _event_has_frog_hook(groups):
            continue
        groups.append({"hooks": [{"type": "command", "command": hook_cmd}]})
        changed.append(f"hook {ev}")

    if not changed:
        print(f"✅ {path} already wired for the frog — nothing to change.")
        for n in notes:
            print(f"   • {n}")
        return

    # Back up the original, then write atomically.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if existed:
        try:
            with open(path + ".bak", "w") as f:
                f.write(text)
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)

    print(f"✅ Wired the frog into {path}:")
    for c in changed:
        print(f"   + {c}")
    for n in notes:
        print(f"   • {n}")
    if existed:
        print(f"   (backed up your previous settings to {path}.bak)")
    print("   Start a new Claude Code session to see him.")


def _settings_path(opts):
    """Where ~/.claude/settings.json lives (honoring --settings / CLAUDE_CONFIG_DIR)."""
    return opts.get("settings") or os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
        "settings.json",
    )


def mode_uninstall_settings(opts):
    """Remove ONLY the frog's statusLine + hooks from settings.json.

    The mirror of install-settings: backs the file up, drops the frog's own
    statusLine (never someone else's) and any frog hook groups, prunes emptied
    event lists, and leaves everything else exactly as it was. Idempotent.
    """
    path = _settings_path(opts)
    if not os.path.exists(path):
        print(f"Nothing to remove — {path} doesn't exist.")
        return
    with open(path) as f:
        text = f.read()
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        sys.stderr.write(f"✗ {path} isn't valid JSON; leaving it untouched.\n")
        sys.exit(1)
    if not isinstance(data, dict):
        sys.stderr.write(f"✗ {path} isn't a JSON object; leaving it alone.\n")
        sys.exit(1)

    removed = []
    sl = data.get("statusLine")
    if _is_frog_cmd((sl or {}).get("command")):
        del data["statusLine"]
        removed.append("statusLine")

    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for ev in FROG_HOOK_EVENTS:
            groups = hooks.get(ev)
            if not isinstance(groups, list):
                continue
            kept = []
            for g in groups:
                cmds = (g or {}).get("hooks", []) if isinstance(g, dict) else []
                if any(_is_frog_cmd((h or {}).get("command")) for h in cmds):
                    continue  # drop this frog group
                kept.append(g)
            if len(kept) != len(groups):
                removed.append(f"hook {ev}")
                if kept:
                    hooks[ev] = kept
                else:
                    del hooks[ev]
        if not hooks:
            del data["hooks"]

    if not removed:
        print(f"✅ No frog settings found in {path} — nothing to remove.")
        return

    with open(path + ".bak", "w") as f:
        f.write(text)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    print(f"✅ Removed the frog from {path}:")
    for r in removed:
        print(f"   - {r}")
    print(f"   (backed up your previous settings to {path}.bak)")


def mode_doctor(opts):
    """A green/amber checkup so a first-timer KNOWS it worked.

    Verifies the five things that make the frog appear — python3, the launcher
    line, the token-feed (tap) wiring, the dance hooks, a resolvable theme —
    plus a non-critical note on tmux (where the frog actually lives). Exits
    non-zero only if a *critical* piece is missing, so callers can gate on it;
    the tmux note never fails the check.
    """
    C_OK = "\033[38;2;120;200;120m"
    C_WARN = "\033[38;2;230;180;90m"
    R = "\033[0m"
    rows = []       # (label, ok, critical, detail)

    rows.append(("Python 3", True, True, "%d.%d.%d" % sys.version_info[:3]))

    # Launcher line in a shell rc (use --rc if the installer told us which one).
    rc = opts.get("rc")
    candidates = [rc] if rc else [
        os.path.expanduser(p)
        for p in ("~/.zshrc", "~/.bashrc", "~/.bash_profile", "~/.profile")]
    found_rc = None
    for p in candidates:
        if p and os.path.exists(p):
            try:
                with open(p) as f:
                    if MARKER in f.read():
                        found_rc = p
                        break
            except OSError:
                pass
    rows.append(("Launcher (claude SEGA)", found_rc is not None, True,
                 f"in {found_rc}" if found_rc
                 else "not found in your shell rc — run install.sh"))

    # settings.json: token feed (tap) + hooks. In --minimal mode the user
    # deliberately skipped these, so they're informational, not failures.
    minimal = bool(opts.get("minimal"))
    path = _settings_path(opts)
    sl_ok = hooks_ok = False
    foreign_sl = False
    detail = "not wired — run install.sh"
    data = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                t = f.read()
            data = json.loads(t) if t.strip() else {}
        except ValueError:
            detail = f"{path} isn't valid JSON"
    if isinstance(data, dict):
        sl_cmd = (data.get("statusLine") or {}).get("command")
        sl_ok = _is_frog_cmd(sl_cmd)
        foreign_sl = bool(sl_cmd) and not sl_ok
        hk = data.get("hooks") or {}
        hooks_ok = isinstance(hk, dict) and all(
            _event_has_frog_hook(hk.get(ev)) for ev in FROG_HOOK_EVENTS)
    if minimal and not sl_ok:
        rows.append(("Token feed (tap)", True, False, "skipped (--minimal)"))
        rows.append(("Dance hooks", True, False, "skipped (--minimal)"))
    else:
        if sl_ok:
            rows.append(("Token feed (tap)", True, True, f"wired into {path}"))
        elif foreign_sl:
            # Your own statusLine — can't verify it taps, so warn without
            # failing the checkup.
            rows.append(("Token feed (tap)", False, False,
                         "you have your own statusLine — make sure it pipes "
                         "the payload to `claude_frog.py tap`"))
        else:
            rows.append(("Token feed (tap)", False, True, detail))
        rows.append(("Dance hooks", hooks_ok, False,
                     "all 4 events wired" if hooks_ok
                     else "some hooks missing — re-run install.sh"))

    raw = os.environ.get("CLAUDE_FROG_THEME")
    theme = resolve_theme(raw) or DEFAULT_THEME
    rows.append(("Theme", True, False,
                 theme + ("" if raw else " (default)")))

    in_tmux = bool(os.environ.get("TMUX"))
    rows.append(("Dancing pane (tmux)", in_tmux, False,
                 "in tmux — you get the full show" if in_tmux
                 else "not in tmux — the frog lives in a tmux pane, so "
                      "you won't see him (add tmux + WezTerm)"))

    crit_ok = all(ok for _, ok, critical, _ in rows if critical)

    print("🐸 Claude Frog — checkup\n")
    for label, ok, _critical, det in rows:
        mark = (C_OK + "✅" + R) if ok else (C_WARN + "⚠️ " + R)
        print("  %s %-24s %s" % (mark, label, det))
    print()
    if crit_ok:
        print(C_OK + "All set." + R
              + "  Open a NEW terminal (or `source` your rc), then:  claude SEGA")
    else:
        print(C_WARN + "Some things need attention" + R
              + " — fix the ⚠️  above, then re-run:  python3 "
              + f"{os.path.abspath(__file__)} doctor")
    sys.exit(0 if crit_ok else 1)


def mode_resolve_theme(argv):
    """Print the canonical theme for a spelling and exit 0; exit 1 if unknown.

    The `claude` shell launcher (install/claude-theme.sh) calls this to turn a
    first arg like "SEGA" into "genesis", and — via the exit code — to decide
    whether that first arg names a theme at all (vs. a real prompt to pass on).
    """
    token = argv[1] if len(argv) > 1 else ""
    canon = resolve_theme(token)
    if canon:
        sys.stdout.write(canon)
        sys.exit(0)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Entry                                                                        #
# --------------------------------------------------------------------------- #


def _parse(argv):
    mode = argv[0] if argv else "tap"
    opts = {"session": None, "layout": None, "theme": None, "always": False,
            "party": False, "event": None,
            "settings": None, "statusline_mode": "tap", "rc": None,
            "minimal": False, "since": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--session", "-s"):
            i += 1; opts["session"] = argv[i]
        elif a == "--layout":
            i += 1; opts["layout"] = argv[i]
        elif a == "--theme":
            i += 1; opts["theme"] = argv[i]
        elif a == "--event":
            i += 1; opts["event"] = argv[i]
        elif a == "--settings":
            i += 1; opts["settings"] = argv[i]
        elif a == "--statusline-mode":
            i += 1; opts["statusline_mode"] = argv[i]
        elif a == "--rc":
            i += 1; opts["rc"] = argv[i]
        elif a == "--since":
            i += 1
            try:
                opts["since"] = int(argv[i])
            except (ValueError, IndexError):
                opts["since"] = None
        elif a == "--minimal":
            opts["minimal"] = True
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
        opts["layout"] = os.environ.get("CLAUDE_FROG_LAYOUT") or DEFAULT_LAYOUT
    if opts["layout"] not in LAYOUTS:
        opts["layout"] = DEFAULT_LAYOUT
    # env lets the SessionStart hook and the tmux toggle keybind agree on a
    # theme without threading --theme through each. Accept friendly aliases
    # ("SEGA", "Game Boy") from either source.
    raw_theme = opts["theme"] or os.environ.get("CLAUDE_FROG_THEME")
    opts["theme"] = resolve_theme(raw_theme) or DEFAULT_THEME
    return mode, opts


def main():
    mode, opts = _parse(sys.argv[1:])
    try:
        if mode == "dance":
            if not opts["session"]:
                opts["session"] = "default"
            mode_dance(opts)
        elif mode in ("tap", "statusline"):
            # "statusline" (the retired in-bar mood frog) is a deprecated
            # alias: existing settings.json wirings keep feeding the gauge.
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
        elif mode == "resolve-theme":
            mode_resolve_theme(sys.argv[1:])
        elif mode == "install-settings":
            mode_install_settings(opts)
        elif mode == "uninstall-settings":
            mode_uninstall_settings(opts)
        elif mode == "doctor":
            mode_doctor(opts)
        else:
            sys.stderr.write(f"unknown mode: {mode}\n")
            sys.exit(2)
    except SystemExit:
        raise
    except Exception:
        # never crash the tap / hook paths ("statusline" is the tap alias)
        if mode in ("statusline", "tap", "hook"):
            sys.exit(0)
        raise


if __name__ == "__main__":
    main()
