#!/usr/bin/env python3
"""Claude Frog — a little pixel frog who dances while Claude Code is thinking.

One file, standard library only. Two jobs:

  * `dance`       — the tmux-pane daemon: a smooth pixel frog who dances while
                    your turn is running and idles between turns.
  * `tap`         — the statusLine command: reads the token payload Claude Code
                    hands the status bar and publishes the gauge for the pane.
                    Prints nothing unless you set `config statusline frog`, in
                    which case it also draws a one-line frog + context gauge.
                    (`statusline` is the same mode under its older name.)

He is also a gauge. The more context you've burned, the goofier he gets, and
past ~150k tokens he starts to shake — an honest "you're deep in it, quality's
about to soften" tell. Calm below ~40k, mostly unhinged by ~100k, full chaos by
~120k. He also changes color: green when fresh, fading toward Claude pink as
context fills, fully pink by 200k tokens — `config fade off` keeps him his
theme's own green and leaves the dance and the shake to carry the gauge.

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
# blush is a continuous, always-on readout of how full the window is. Turn the
# colour channel off entirely with `config fade off` (see the SETTINGS table);
# the goofiness and shake ramps above are independent of it, so the gauge
# survives on motion alone.
PINK_FULL_TOKENS = 200_000

# Framerates.
FPS_ACTIVE = 12.0             # dancing (a turn is running)
FPS_IDLE = 4.0                # idling (between turns)

# Pane layouts: name -> pane size. Vertical layouts (top/bottom) are sized in
# lines, horizontal ones (left/right) in columns. `top`/`left` place the pane
# before the current one; `bottom`/`right` after it. He always stands on the
# pane's floor, so a top pane puts him directly above your prompt, facing down
# at your work. The names and sizes are the frog's vocabulary (how much room he
# needs); what a layout means in actual splits belongs to the rendering surface
# (TmuxSurface owns the split-axis translation).
LAYOUTS = {
    "bottom": 7,
    "top": 7,
    "right": 24,
    "left": 24,
}
DEFAULT_LAYOUT = "top"

# Fallback goofiness when no token data is available (pane-only friend with no
# tap feeding tokens): ramp on turn count instead — unhinged by turn 4.
FALLBACK_UNHINGED_TURNS = 4

# Environment / flora: each user prompt sprouts one random prop (flower, cloud,
# rock, tree, or fallen log) that animates in and settles around the frog. Props
# live only in the dance daemon's memory, so they accumulate through a session
# and reset when the pane respawns. Turn it off with `config flora off` (or
# CLAUDE_FROG_FLORA=0 for one session) — see the SETTINGS table below.
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

# Paneless session state (.think/.ctx from sessions outside tmux, or sessions
# hard-killed before SessionEnd could clean up) older than this is swept by
# _prune_stale. Live sessions rewrite their state constantly — the tap on
# every statusline refresh, the hooks on every prompt — so anything this old
# is genuinely dead.
STALE_STATE_SECS = 7 * 24 * 3600

# The frog is a property of the tmux WINDOW, not of a Claude session: exactly
# one pane per window, no matter how many sessions are running in it. Several
# sessions can share a window (a headless `claude -p` fired off by a subagent or
# a skill, a nested `claude`, a `/clear` that mints a fresh session id), so the
# window file reference-counts its claimants and the last one out kills the
# pane. LOCK_* bound the file lock that serializes concurrent SessionStarts —
# without it two sessions racing to claim an empty window both spawn.
WIN_LOCK_WAIT_SECS = 1.0      # how long to spin for the lock before giving up
WIN_LOCK_STALE_SECS = 10.0    # steal a lock older than this (holder died)

# A claim is live while the tmux pane its session runs in still exists — real
# liveness, not a timeout, so a crashed Claude can't hold a frog hostage. The
# timestamp below is only the fallback for claims whose pane we never resolved.
WIN_CLAIM_STALE_SECS = 12 * 3600

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
# Settings — one table, four surfaces                                          #
# --------------------------------------------------------------------------- #
# Changing how the frog looks used to mean editing your shell rc, because every
# knob was an env var read at import time and nothing persisted. Now there's a
# config file, and `config` / `setup` / `doctor` / the resolver all read the one
# table below so they can't drift apart. Adding a knob means adding a row.

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "claude-frog",
)


def _config_path():
    return os.path.join(CONFIG_DIR, "config.json")


def _onoff(v):
    """Parse a human on/off. None for junk, so the resolver falls through."""
    s = str(v).strip().lower()
    if s in ("1", "true", "on", "yes", "y"):
        return True
    if s in ("0", "false", "off", "no", "n"):
        return False
    return None


SETTINGS = {
    "theme": {
        "env": "CLAUDE_FROG_THEME",
        "default": DEFAULT_THEME,
        "choices": tuple(THEMES),
        "parse": resolve_theme,
        "show": str,
        "help": "which pixel style he's rendered in",
    },
    "layout": {
        "env": "CLAUDE_FROG_LAYOUT",
        "default": DEFAULT_LAYOUT,
        "choices": tuple(LAYOUTS),
        "parse": lambda v: str(v) if str(v) in LAYOUTS else None,
        "show": str,
        "help": "which edge of the window his pane takes",
    },
    "flora": {
        "env": "CLAUDE_FROG_FLORA",
        "default": True,
        "choices": ("on", "off"),
        "parse": _onoff,
        "show": lambda v: "on" if v else "off",
        "help": "sprout a diorama prop on every prompt",
    },
    "fade": {
        "env": "CLAUDE_FROG_FADE",
        "default": True,
        "choices": ("on", "off"),
        "parse": _onoff,
        "show": lambda v: "on" if v else "off",
        "help": "blush from green toward Claude pink as context fills",
    },
    "statusline": {
        "env": "CLAUDE_FROG_STATUSLINE",
        # Default off: an upgrade must never start drawing in somebody's status
        # bar unasked. The wizard offers it; this is the opt-in.
        "default": "off",
        "choices": ("off", "frog"),
        "parse": lambda v: str(v).strip().lower()
                 if str(v).strip().lower() in ("off", "frog") else None,
        "show": str,
        "help": "a one-line frog + context gauge in your status bar",
    },
}


def _read_config():
    try:
        with open(_config_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _setting(key, flag=None, config=None):
    """Resolve one setting to (value, source).

    Precedence, highest first: an explicit `--flag`, the `CLAUDE_FROG_*` env var
    (which is how `claude SEGA` overrides a single session), the config file,
    then the built-in default. Anything unparseable is skipped rather than
    honoured, so a typo in one layer falls through to the next instead of
    leaving the frog themeless.
    """
    spec = SETTINGS[key]
    cfg = _read_config() if config is None else config
    for raw, src in ((flag, "flag"),
                     (os.environ.get(spec["env"]), "env"),
                     (cfg.get(key), "config")):
        if raw is None or raw == "":
            continue
        val = spec["parse"](raw)
        if val is not None:
            return val, src
    return spec["default"], "default"


def _write_config(data):
    """Persist the config file, creating its directory. Returns True on success."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        path = _config_path()
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


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

# The status-bar frog: 2px tall, which is EXACTLY one character row through the
# half-block renderer — so he costs a single line no matter how cramped your bar
# is. That's the whole design constraint. What survives at this size is a
# silhouette: two glinting eyes over a wide grin, green cheeks either side. He
# wears the session's theme and the same green->pink context fade as the pane
# frog, so the two never disagree about how deep you are.
# The mouth deliberately spans every eye column, so both pupils sit over the
# same colour — at 11px wide an off-by-one there reads as a lopsided face.
_MICRO_SRC = [
    "OHWPHHHWPHO",   # eye bumps: specular glint + pupil, twice, lit from the left
    "OBNNNNNNNBO",   # the grin, cheeks falling to body midtone either side
]

# Blink: the eyes squeeze shut to a dark line, same idea as the big frog's.
_MICRO_BLINK = {0: "OHMMHHHMMHO"}

Pixel = tuple  # (r, g, b) or None


def _load(src):
    """Ragged rows -> rectangular grid of palette keys (space-padded)."""
    w = max(len(r) for r in src)
    return [list(r.ljust(w)) for r in src]


FROG = _load(_FROG_SRC)
FROG_BACK = _load(_FROG_BACK_SRC)
MICRO = _load(_MICRO_SRC)


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


def _jitter(amp):
    """One axis of shake: an integer pixel offset from a fractional amplitude.

    Plain int truncation muted any amplitude below 1.0 entirely — on a 200k
    window shake_px tops out at ~0.88, so the shake never fired at all. Carry
    the fractional part as a probability instead: amp 0.5 shakes ±1 on about
    half the frames, so the onset really is continuous from SHAKE_START_TOKENS
    and grows into steady multi-pixel jitter as amp climbs.
    """
    if amp <= 0:
        return 0
    mag = int(amp)
    if random.random() < amp - mag:
        mag += 1
    return random.randint(-mag, mag) if mag else 0


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


def palette_for(tokens, theme=DEFAULT_THEME, fade=True):
    """The theme's base palette blended toward its pink target by token usage.

    Returns the base (fresh) palette unchanged at zero tokens (or when tokens
    are unknown) — identity, so nothing downstream pays for the common case — a
    fully faded palette at/above PINK_FULL_TOKENS, and a vivid HLS blend (see
    _blend) in between. Keys absent from the theme's pink target (e.g. the SNES
    eyes / mouth cream, transparent) pass through untouched.

    `fade=False` (the `fade` setting, off) pins him to the base palette forever:
    every colored surface routes through here, so that one flag is the whole
    opt-out. The dance/shake ramps are untouched — they read tokens directly —
    so the gauge still reads, just through motion alone.
    """
    spec = theme_spec(theme)
    base_palette, target_palette = spec["base"], spec["pink"]
    t = pinkness(tokens) if fade else 0.0
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
def _m_bigjump(direction):
    """A directed leap. A factory (like _m_boop) so the direction is fixed for
    the whole move — choosing it per frame made him teleport side to side
    mid-air instead of leaping one way."""
    def fn(t, g):
        span = 6 + int(round(14 * g))
        return {"dx": int(round(direction * span * t)),
                "dy": -int(round((6 + 8 * g) * math.sin(t * math.pi))),
                "shear": 2 * math.sin(t * math.pi * 4)}
    return fn


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
SPECIALS = [(_m_bigjump(1), 16), (_m_bigjump(-1), 16), (_m_backflip, 18),
            (_m_spinout, 20), (_m_twerk, TWERK_FRAMES)]


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


def _safe_session(session):
    """Tame a session id for use as a filename and on a tmux command line.

    Session ids come in from an external payload (and the env) but end up
    joined onto CACHE_DIR and interpolated into the shell command the dance
    pane is spawned with — so a stray "../" or a space must never survive.
    Real Claude Code ids are UUIDs, which pass through untouched.
    """
    s = "".join(ch for ch in str(session) if ch.isalnum() or ch in "-_")
    return s[:64] or "default"


def _paths(session):
    base = os.path.join(CACHE_DIR, _safe_session(session))
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
    win = opts.get("window")
    always = opts["always"]
    party = opts["party"]
    theme = opts.get("theme", DEFAULT_THEME)
    dither = theme_spec(theme)["dither"]
    out = sys.stdout
    chor = Choreographer()
    scene = Scene() if _setting("flora")[0] else None
    fade = _setting("fade")[0]
    # Don't backfill props for turns that already happened before this pane
    # started (e.g. a mid-session toggle) — only sprout on prompts from here on.
    # The baseline comes from `--since` (captured in the spawning hook, before the
    # pane booted) so a fast first prompt can't slip in before we read it here.
    # Kept per session, because a window's frog follows whichever session is
    # working: without it, switching to a session with a lower turn count would
    # bank a burst of props and fire them all when it switched back.
    seen_turns = {}
    if session and opts.get("since") is not None:
        seen_turns[_safe_session(session)] = opts["since"]
    frame = 0

    def _who():
        """The session this frog is currently showing.

        A window-scoped frog follows the window's active claimant; a
        `--session` frog (manual `dance`, tests) is pinned to one.
        """
        if not win:
            return session
        return _read_win(win).get("active") or session or "default"

    def cleanup(*_):
        # Guarded: if the pane's tty is already gone (tmux killed it out from
        # under us) the restore write raises EPIPE/EIO — exit quietly anyway
        # instead of dying a second time inside the exception handler.
        try:
            out.write("\x1b[?25h\x1b[0m\x1b[2J\x1b[H")
            out.flush()
        except Exception:
            pass
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
            sess = _who()
            state, turns = _read_think(sess)
            tokens = _read_ctx(sess)
            active = party or always or (state == "thinking")
            g = 1.0 if party else goofiness(tokens, turns)
            sk = shake_px(tokens) if not party else float(SHAKE_MAX_PX)
            # party maxes everything, so blush him fully pink too — unless the
            # fade is off, in which case nothing is allowed to recolor him.
            palette = palette_for(PINK_FULL_TOKENS if party else tokens,
                                  theme, fade)

            # self-exit once the state we exist for has vanished (last claimant
            # released the window, session ended and cleanup ran, or files were
            # pruned) — no orphan frogs.
            watched = _win_path(win) if win else _paths(sess)[0]
            if not os.path.exists(watched):
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
                base_x += _jitter(sk)
                base_y += _jitter(sk)
            base_x = max(-2, min(cols - sw_ + 2, base_x))
            base_y = max(-2, min(stage_h - 2, base_y))

            # Environment: sprout a prop per new prompt, then paint the scene
            # behind the frog. Guarded so a prop bug can never stop him dancing.
            if scene is not None:
                try:
                    key = _safe_session(sess)
                    # First sight of a session: adopt its count as the baseline
                    # rather than backfilling a prop per turn it ran elsewhere.
                    base = seen_turns.get(key, turns)
                    seen_turns[key] = turns
                    for _ in range(max(0, turns - base)):
                        scene.spawn(frame, cols)
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
# Agent adapters — every agent-specific fact lives behind this seam            #
# --------------------------------------------------------------------------- #
# The frog itself is agent-agnostic. All it asks of the coding agent hosting it
# is: a token gauge fed from somewhere, four lifecycle moments (session starts,
# prompt lands, turn ends, session ends), and a config file it can wire itself
# into. Everything that knows one agent *specifically* — payload shapes, hook
# event names, the settings-file schema — is an AgentAdapter. Supporting a new
# agent means writing a new adapter, not touching the frog.
#
# The seam stays this small because the frog never reads transcripts: all state
# arrives through two doorways (the statusline payload and the hook payloads),
# and both doorways go through the adapter.
#
# mode_hook dispatches on CANONICAL lifecycle events, never on native names:
#   "session-start"  — a session began (claim the window's frog)
#   "prompt"         — the user submitted a prompt (a turn is starting)
#   "stop"           — the turn finished (back to idling)
#   "session-end"    — the session is over (release the claim)
# Adapters translate their native event names to these.
#
# See docs/adapters.md for the interface contract and the recorded decision to
# keep adapters as sections of this one file rather than a module split.


class AgentAdapter:
    """The interface an agent integration implements. Claude Code is adapter #1.

    Subclasses provide:
      name                — registry key ("claude-code")
      display             — the agent's name as humans write it ("Claude Code")
      HOOK_EVENTS         — native event names the installer wires up
      detect()            — does this agent appear to be on this machine?
      settings_path()     — the agent's own wiring artifact (honoring an override)
      hook_event()        — native event name out of a hook payload
      canonical_event()   — native event name -> canonical lifecycle event
      session_id()        — session id out of any payload (hook or statusline)
      extract_tokens() /
      extract_window_size() — the token gauge, from the statusline payload
      parse_settings() /
      serialize_settings() — text <-> the parsed form the wiring methods take.
                            The default is the JSON dance (parse errors raised
                            as ValueError); an adapter whose wiring artifact
                            isn't JSON overrides both. serialize_settings may
                            return None, which tells the caller the artifact
                            should be REMOVED rather than written.
      install_wiring() /
      uninstall_wiring() /
      wiring_status()     — schema surgery on the agent's PARSED settings, so
                            install / uninstall / doctor share one copy of the
                            schema knowledge instead of three

    USES_SHELL_LAUNCHER says whether the `claude <THEME>` shell launcher story
    applies to this agent (doctor skips that check when it doesn't), and
    INSTALL_HINT is what doctor tells the user to run when wiring is missing.
    """

    name = ""
    display = ""
    HOOK_EVENTS = ()
    USES_SHELL_LAUNCHER = True
    INSTALL_HINT = "run install.sh"

    def parse_settings(self, text):
        """Artifact text (None if the file is absent) -> the wiring methods' form.

        The default speaks JSON — the common case for agent config files —
        raising ValueError on text that can't be honored. Adapters whose wiring
        artifact isn't a JSON file override this (and serialize_settings).
        """
        if text is None or not text.strip():
            return {}
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"isn't valid JSON ({e})")
        if not isinstance(data, dict):
            raise ValueError("isn't a JSON object")
        return data

    def serialize_settings(self, data):
        """The wiring methods' form -> artifact text, or None for "remove it"."""
        return json.dumps(data, indent=2) + "\n"

    def detect(self):
        raise NotImplementedError

    def settings_path(self, override=None):
        raise NotImplementedError

    def hook_event(self, payload):
        raise NotImplementedError

    def canonical_event(self, name):
        raise NotImplementedError

    def session_id(self, payload):
        raise NotImplementedError

    def extract_tokens(self, payload):
        raise NotImplementedError

    def extract_window_size(self, payload):
        raise NotImplementedError

    def install_wiring(self, data, tap_cmd, hook_cmd, is_ours, statusline=True):
        raise NotImplementedError

    def uninstall_wiring(self, data, is_ours):
        raise NotImplementedError

    def wiring_status(self, data, is_ours):
        raise NotImplementedError


class ClaudeCodeAdapter(AgentAdapter):
    """Claude Code — the reference adapter.

    Token usage arrives on the statusLine payload (hooks are token-blind), the
    lifecycle arrives as hook events, and the wiring lives in
    `~/.claude/settings.json` (one statusLine command; hooks as
    {event: [{"hooks": [{"type", "command"}, ...]}, ...]}). Those three facts
    are this class's whole reason to exist — nothing outside it knows them.
    """

    name = "claude-code"
    display = "Claude Code"

    # Hook events the installer wires (see install/settings-hooks.json).
    HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")

    # Native event name -> canonical lifecycle event. "Cleanup" is accepted as
    # a session-end synonym (legacy invocations used it).
    _EVENTS = {
        "SessionStart": "session-start",
        "UserPromptSubmit": "prompt",
        "Stop": "stop",
        "SessionEnd": "session-end",
        "Cleanup": "session-end",
    }

    def _config_dir(self):
        return (os.environ.get("CLAUDE_CONFIG_DIR")
                or os.path.expanduser("~/.claude"))

    def detect(self):
        return os.path.isdir(self._config_dir())

    def settings_path(self, override=None):
        """Where settings.json lives (honoring --settings / CLAUDE_CONFIG_DIR)."""
        return override or os.path.join(self._config_dir(), "settings.json")

    # ------------------------------------------------- payloads -> readings --

    def hook_event(self, payload):
        return payload.get("hook_event_name") or ""

    def canonical_event(self, name):
        return self._EVENTS.get(name)

    def session_id(self, payload):
        return payload.get("session_id") or payload.get("sessionId") or None

    def extract_tokens(self, payload):
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
        for k in ("input_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens"):
            if cu.get(k) is not None:
                tot += int(cu[k]); got = True
        return tot if got else None

    def extract_window_size(self, payload):
        """The session's context window size, if the payload says. Else None.

        Only used for the "% full" readout — the frog's *mood* stays anchored
        in absolute tokens (see PINK_FULL_TOKENS), because that's what actually
        tracks when long-context quality softens, whatever size your window is.
        """
        cw = payload.get("context_window") or {}
        for k in ("context_window_size", "context_window"):
            try:
                v = int(cw.get(k))
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        return None

    # -------------------------------------- settings.json schema surgery -----
    # `is_ours` is the frog's own "is this command mine?" predicate
    # (_is_frog_cmd), passed in so the adapter carries the SCHEMA knowledge and
    # the frog carries its own identity.

    def event_has_hook(self, groups, is_ours):
        """True if this event's hook list already runs a matching command."""
        if not isinstance(groups, list):
            return False
        for g in groups:
            for h in (g or {}).get("hooks", []) if isinstance(g, dict) else []:
                if is_ours((h or {}).get("command")):
                    return True
        return False

    def install_wiring(self, data, tap_cmd, hook_cmd, is_ours, statusline=True):
        """Merge the wiring into parsed settings. Mutates `data` in place.

        Returns (changed, notes) — human-readable lines for the installer to
        print. Conservative on purpose: a statusLine that isn't ours is never
        overwritten (Claude Code allows only one), and hook groups already
        present are skipped, so re-running changes nothing. Raises ValueError
        if `hooks` exists but isn't an object — the caller owns erroring out.
        """
        changed, notes = [], []
        if statusline:
            sl = data.get("statusLine")
            cmd = (sl or {}).get("command") if isinstance(sl, dict) else None
            if not sl:
                data["statusLine"] = {"type": "command", "command": tap_cmd}
                changed.append("statusLine → tap (token feed)")
            elif is_ours(cmd):
                if cmd.rstrip().endswith(" statusline"):
                    data["statusLine"] = {"type": "command", "command": tap_cmd}
                    changed.append("statusLine: statusline → tap "
                                   "(the in-bar frog is deprecated)")
                else:
                    notes.append("statusLine already taps the frog — left as-is")
            else:
                notes.append(
                    "you already have a statusLine — left as-is. Make sure it "
                    "pipes the payload to `claude_frog.py tap` (see "
                    "install/statusline-compose.sh) or the pane loses its gauge")

        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("settings 'hooks' isn't an object")
        for ev in self.HOOK_EVENTS:
            groups = hooks.setdefault(ev, [])
            if not isinstance(groups, list):
                notes.append(f"hooks.{ev} isn't a list — skipped")
                continue
            if self.event_has_hook(groups, is_ours):
                continue
            groups.append({"hooks": [{"type": "command", "command": hook_cmd}]})
            changed.append(f"hook {ev}")
        return changed, notes

    def uninstall_wiring(self, data, is_ours):
        """Remove ONLY the matching wiring from parsed settings; mutate in place.

        The mirror of install_wiring: drops our statusLine (never someone
        else's) and any hook group of ours, prunes emptied event lists, leaves
        everything else exactly as it was. Returns the removed lines.
        """
        removed = []
        sl = data.get("statusLine")
        if is_ours((sl or {}).get("command")):
            del data["statusLine"]
            removed.append("statusLine")

        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            for ev in self.HOOK_EVENTS:
                groups = hooks.get(ev)
                if not isinstance(groups, list):
                    continue
                kept = []
                for g in groups:
                    cmds = (g or {}).get("hooks", []) if isinstance(g, dict) else []
                    if any(is_ours((h or {}).get("command")) for h in cmds):
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
        return removed

    def wiring_status(self, data, is_ours):
        """(statusline_ok, foreign_statusline, hooks_ok) — doctor's view."""
        sl_cmd = (data.get("statusLine") or {}).get("command")
        sl_ok = is_ours(sl_cmd)
        hk = data.get("hooks") or {}
        hooks_ok = isinstance(hk, dict) and all(
            self.event_has_hook(hk.get(ev), is_ours) for ev in self.HOOK_EVENTS)
        return sl_ok, bool(sl_cmd) and not sl_ok, hooks_ok


class OpencodeAdapter(AgentAdapter):
    """opencode (opencode.ai) — adapter #2, the seam's first outside consumer.

    The facts this class owns (verified against anomalyco/opencode 1.18.x,
    2026-08):

    - opencode has NO statusline and no shell-command hook schema. Its whole
      extension surface is a JS plugin: an ES module auto-loaded from
      `~/.config/opencode/plugin{,s}/*.js`, run in-process under Bun. So the
      frog's wiring artifact is ONE generated plugin file this adapter fully
      owns — parse/serialize are text passthrough, and serialize_settings
      returning None means "remove the file".
    - The generated plugin is the adapter's arm inside opencode: it translates
      the runtime surface into the frog's two doorways. Lifecycle moments are
      piped to `hook` under the native names in HOOK_EVENTS (`chat.message` is
      opencode's own name for "a user message arrived"; the plugin also
      re-fires `session.deleted` from its `dispose` hook so quitting opencode
      releases the window claim instead of leaking it).
    - Tokens are REAL here, not degraded: assistant `message.updated` events
      carry {input, output, reasoning, cache:{read, write}}, and the model's
      context size (Model.limit.context, seen by the plugin's `chat.params`
      hook) rides along in the tap payload. The accounting mirrors the Claude
      Code adapter — input + cache read + cache write of the last request.
      Any schema drift degrades to None: a calm green frog whose goofiness
      ramps on turn count instead (FALLBACK_UNHINGED_TURNS), which is the
      honest fallback, not a crash.
    """

    name = "opencode"
    display = "opencode"
    USES_SHELL_LAUNCHER = False
    INSTALL_HINT = "run: claude-frog install-settings --agent opencode"

    # Native names the generated plugin reports — three bus events plus
    # opencode's own `chat.message` hook name for a user prompt.
    HOOK_EVENTS = ("session.created", "chat.message",
                   "session.idle", "session.deleted")

    _EVENTS = {
        "session.created": "session-start",
        "chat.message": "prompt",
        "session.idle": "stop",
        "session.deleted": "session-end",
    }

    # First line of the generated plugin; identity + docs pointer in one.
    MARKER = "claude-frog opencode plugin"

    def _config_dir(self):
        return (os.environ.get("OPENCODE_CONFIG_DIR")
                or os.path.join(
                    os.environ.get("XDG_CONFIG_HOME")
                    or os.path.expanduser("~/.config"), "opencode"))

    def detect(self):
        from shutil import which
        return bool(which("opencode")) or os.path.isdir(self._config_dir())

    def settings_path(self, override=None):
        """The frog's own plugin file, in whichever plugin dir already exists.

        opencode globs both `plugin/` and `plugins/`; joining an existing dir
        keeps the user's layout instead of imposing a second spelling.
        """
        if override:
            return override
        base = self._config_dir()
        for d in ("plugin", "plugins"):
            p = os.path.join(base, d)
            if os.path.isdir(p):
                return os.path.join(p, "claude-frog.js")
        return os.path.join(base, "plugin", "claude-frog.js")

    # ------------------------------------------------- payloads -> readings --

    def hook_event(self, payload):
        return payload.get("type") or ""

    def canonical_event(self, name):
        return self._EVENTS.get(name)

    def session_id(self, payload):
        """Tap payloads carry sessionID at the top; bus events nest it under
        properties.sessionID or properties.info.{sessionID,id}."""
        props = payload.get("properties")
        props = props if isinstance(props, dict) else {}
        info = props.get("info")
        info = info if isinstance(info, dict) else {}
        sid = (payload.get("sessionID") or props.get("sessionID")
               or info.get("sessionID") or info.get("id"))
        return sid if isinstance(sid, str) and sid else None

    def extract_tokens(self, payload):
        """input + cache.read + cache.write of the last assistant request —
        the same "what's occupying the window" accounting as Claude Code
        (output isn't added: it becomes the NEXT request's input). Any drift
        from that shape returns None and the gauge degrades to turn count."""
        tok = payload.get("tokens")
        if not isinstance(tok, dict):
            props = payload.get("properties")
            info = props.get("info") if isinstance(props, dict) else None
            tok = info.get("tokens") if isinstance(info, dict) else None
        if not isinstance(tok, dict):
            return None
        cache = tok.get("cache")
        cache = cache if isinstance(cache, dict) else {}
        tot, got = 0, False
        for v in (tok.get("input"), cache.get("read"), cache.get("write")):
            if v is None:
                continue
            try:
                tot += int(v)
                got = True
            except (TypeError, ValueError):
                pass
        return tot if got else None

    def extract_window_size(self, payload):
        lim = payload.get("limit")
        if isinstance(lim, dict):
            lim = lim.get("context")
        try:
            v = int(lim)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    # ------------------------------- the wiring artifact: our plugin file ----
    # "Parsed settings" for this adapter is just {"text": <file text or None>}:
    # the file is ours outright, so surgery is generate / compare / remove,
    # never merge. The mode functions still own all file I/O.

    def parse_settings(self, text):
        return {"text": text}

    def serialize_settings(self, data):
        return data.get("text")

    def _plugin_js(self, tap_cmd, hook_cmd):
        """The whole plugin, generated with the frog's argv baked in.

        Same never-crash discipline as the Python side, extended into Bun:
        every handler swallows its errors, and sends are fire-and-forget
        detached spawns — a broken frog must never break an opencode turn.
        """
        import shlex
        tap = json.dumps(shlex.split(tap_cmd))
        hook = json.dumps(shlex.split(hook_cmd))
        return f"""\
// {self.MARKER} — generated by `claude-frog install-settings --agent opencode`.
// Managed by claude-frog: re-running install-settings regenerates this file
// and uninstall-settings removes it. Hand edits will be overwritten.
import {{ spawn }} from "node:child_process";

const TAP = {tap};
const HOOK = {hook};

function send(argv, payload) {{
  try {{
    const child = spawn(argv[0], argv.slice(1), {{
      stdio: ["pipe", "ignore", "ignore"],
      detached: true,
    }});
    child.on("error", () => {{}});
    child.stdin.on("error", () => {{}});
    child.stdin.end(JSON.stringify(payload));
    child.unref();
  }} catch {{}}
}}

export const ClaudeFrogPlugin = async () => {{
  const sessions = new Set(); // top-level sessions we've seen start
  const limits = new Map();   // sessionID -> the model's context window size
  // `sessions` gates events to sessions we saw start (subagent child sessions
  // stay frogless); an empty set means the plugin missed the start (reloaded
  // mid-session), so everything passes rather than going silent.
  const known = (id) => !sessions.size || sessions.has(id);
  return {{
    event: async ({{ event }}) => {{
      try {{
        const t = event?.type;
        const p = event?.properties ?? {{}};
        if (t === "session.created") {{
          if (p.info?.parentID) return;
          sessions.add(p.info?.id);
          send(HOOK, event);
        }} else if (t === "session.idle") {{
          if (known(p.sessionID)) send(HOOK, event);
        }} else if (t === "session.deleted") {{
          sessions.delete(p.info?.id);
          send(HOOK, event);
        }} else if (t === "message.updated") {{
          const info = p.info;
          if (info?.role === "assistant" && info.tokens && known(info.sessionID))
            send(TAP, {{ sessionID: info.sessionID, tokens: info.tokens,
                        limit: {{ context: limits.get(info.sessionID) }} }});
        }}
      }} catch {{}}
    }},
    "chat.message": async (input) => {{
      try {{
        if (known(input?.sessionID))
          send(HOOK, {{ type: "chat.message",
                       properties: {{ sessionID: input?.sessionID }} }});
      }} catch {{}}
    }},
    "chat.params": async (input) => {{
      try {{
        const ctx = input?.model?.limit?.context;
        if (ctx) limits.set(input.sessionID, ctx);
      }} catch {{}}
    }},
    dispose: async () => {{
      // opencode fires no session-end when it simply exits; releasing the
      // window claims here is what keeps quit-without-deleting from leaking
      // frogs (the Python side also ages out stale claims as a backstop).
      try {{
        for (const id of sessions)
          send(HOOK, {{ type: "session.deleted",
                       properties: {{ info: {{ id }} }} }});
      }} catch {{}}
    }},
  }};
}};
"""

    def install_wiring(self, data, tap_cmd, hook_cmd, is_ours, statusline=True):
        """Write-or-refresh our plugin file. Mutates `data` in place.

        A file that exists but isn't ours is refused (ValueError) — unlike a
        shared settings file there is nothing to merge into, and silently
        replacing someone's plugin is exactly the clobbering install-settings
        promises not to do. The `statusline` flag has nothing to skip here:
        the gauge rides the same plugin as the hooks.
        """
        current = data.get("text")
        generated = self._plugin_js(tap_cmd, hook_cmd)
        notes = []
        if not statusline:
            notes.append("opencode has no statusline — the token gauge rides "
                         "the same plugin as the hooks, so there was nothing "
                         "to skip")
        if current and current.strip():
            if self.MARKER not in current and not is_ours(current):
                raise ValueError(
                    "already exists and isn't the frog's plugin — move it "
                    "aside, then re-run")
            if current == generated:
                return [], notes
            data["text"] = generated
            return ["opencode plugin refreshed (paths or plugin code "
                    "had drifted)"], notes
        data["text"] = generated
        return ["opencode plugin (dance hooks + token feed)"], notes

    def uninstall_wiring(self, data, is_ours):
        current = data.get("text")
        if current and (self.MARKER in current or is_ours(current)):
            data["text"] = None      # serialize_settings(None) -> remove file
            return ["opencode plugin (claude-frog.js)"]
        return []

    def wiring_status(self, data, is_ours):
        """One plugin carries both doorways, so gauge and hooks stand or fall
        together. A foreign file at our path never reads as a foreign
        statusline (opencode has none) — it reads as "not wired", and
        install-settings is where the refusal-with-explanation lives."""
        current = data.get("text") or ""
        ours = self.MARKER in current and is_ours(current)
        return ours, False, ours


# Every supported agent, keyed by adapter name; detection walks registry order.
ADAPTERS = {a.name: a for a in (ClaudeCodeAdapter(), OpencodeAdapter())}
DEFAULT_AGENT = ClaudeCodeAdapter.name


def detect_agent():
    """The adapter for whichever agent this machine appears to run.

    First adapter whose detect() answers wins; Claude Code is the fallback, so
    an empty machine still gets a working default. With one registered adapter
    this always lands on Claude Code — the seam exists so adapter #2 is a class
    plus a registry entry, not another sweep through the file.
    """
    for a in ADAPTERS.values():
        if a.detect():
            return a
    return ADAPTERS[DEFAULT_AGENT]


ADAPTER = detect_agent()


# --------------------------------------------------------------------------- #
# Mode: tap (silent token gauge — the statusLine command)                      #
# --------------------------------------------------------------------------- #


def _tap(payload=None):
    """Read the statusLine payload and publish the token gauge to session state.

    The statusLine is the only surface Claude Code hands token usage to — hooks
    are token-blind — so this is the sole source of the pane daemon's gauge.
    Returns (session, tokens, payload); tokens is None if the payload carried none.
    """
    if payload is None:
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    session = ADAPTER.session_id(payload) or "default"
    try:
        tokens = ADAPTER.extract_tokens(payload)
    except Exception:
        tokens = None

    if tokens is not None:
        _write_json(_paths(session)[1], {"tokens": tokens, "ts": time.time()})
    return session, tokens, payload


# --------------------------------------------------------------------------- #
# The status-bar line                                                          #
# --------------------------------------------------------------------------- #
# Two channels, deliberately: the bar's LENGTH is how full your context window
# is, and its COLOUR is how cooked Claude is (the same absolute-token fade the
# pane frog wears). A 1M window at 200k reads "a fifth full, and he's gone
# pink" — which is the honest summary, and one number couldn't say it.

_BAR_CELLS = 8


def _fmt_tokens(n):
    if n is None:
        return "–"
    if n >= 10_000:
        return f"{n // 1000}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _gauge_bar(tokens, size, theme, fade=True):
    """The context bar: length = window fill, colour = the frog's current fade.

    With `fade` off the colour channel goes quiet and the bar carries the fill
    on length alone — which is the readout the bar was always primarily making.
    """
    pal = palette_for(tokens, theme, fade)
    fill_rgb = pal.get("B") or (0x9d, 0xc8, 0x3b)
    dim_rgb = pal.get("S") or (0x3a, 0x4a, 0x28)
    if tokens is None:
        frac = 0.0
    elif size:
        frac = _clamp(tokens / float(size))
    else:
        # No window size in the payload: fall back to the mood ramp so the bar
        # still means something rather than sitting empty.
        frac = _clamp(tokens / float(PINK_FULL_TOKENS))
    lit = int(round(frac * _BAR_CELLS))
    out = []
    for i in range(_BAR_CELLS):
        r, g, b = fill_rgb if i < lit else dim_rgb
        out.append(f"\033[38;2;{r};{g};{b}m" + ("▓" if i < lit else "░"))
    return "".join(out) + _RESET


def _statusline_text(session, tokens, size, theme, fade=True):
    """The whole bar as one line, no trailing newline (so it composes)."""
    state, turns = _read_think(session)
    active = state == "thinking"
    # The bar is stateless — re-invoked on every refresh — so the pose comes off
    # the wall clock rather than a counter we'd have to persist.
    frame = int(time.time() * (FPS_ACTIVE if active else FPS_IDLE))
    grid = MICRO
    if (frame % 47) == 0:                    # an occasional blink
        grid = _apply_blink(grid, _MICRO_BLINK)
    spec = theme_spec(theme)
    rows = render_pixels(
        _colorize(grid, palette_for(tokens, theme, fade), spec["dither"]))
    frog = (rows[0] if rows else "") + _RESET

    parts = [frog, _gauge_bar(tokens, size, theme, fade), _fmt_tokens(tokens)]
    if size and tokens is not None:
        parts.append(f"· {int(round(100.0 * tokens / size))}%")
    line = " ".join(parts)
    # Deep in the window he gets the shakes — one column of jitter, which is all
    # a status bar can carry without turning into noise.
    if shake_px(tokens) and random.random() < 0.5:
        line = " " + line
    return line


def mode_tap():
    """Feed the pane's gauge, and draw the status-bar frog if you asked for one.

    The only surface Claude Code hands token usage to is the statusLine, so the
    dancing pane's goofiness / shake / pink fade all depend on this being wired
    there. Whether anything is *drawn* is the `statusline` setting, not the mode
    name — `tap` and `statusline` behave identically, so an existing wiring of
    either keeps working and neither starts drawing until you opt in.
    """
    session, tokens, payload = _tap()
    if _setting("statusline")[0] == "frog":
        try:
            sys.stdout.write(_statusline_text(
                session, tokens, ADAPTER.extract_window_size(payload),
                _setting("theme")[0], _setting("fade")[0]))
        except Exception:
            pass                # a broken frog must never break your prompt
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Rendering surfaces — every pane/multiplexer fact lives behind this seam      #
# --------------------------------------------------------------------------- #
# The frog's window bookkeeping is surface-agnostic. All it asks of the thing
# hosting his pane is: a way to tell which "window" this process is in, to
# split a pane beside the user's shell (and later kill it), and to enumerate
# the panes that exist right now — liveness is how claims are pruned.
# Everything that knows one surface *specifically* — its command vocabulary,
# window-id format, split geometry, the pane-stamping trick — is a
# RenderSurface. Supporting a new surface means writing a new one and adding
# it to the SURFACES registry, not sweeping through the file.
#
# ⚠️ Declared scaffolding (FWL-549): tmux is the SOLE supported surface, and
# detect_surface() hard-lands on it. The seam exists so a second backend
# (Zellij, kitty splits, a standalone-terminal mode, …) is a class plus a
# registry entry — but building one is demand-gated: hold until users ask.
# Outside tmux the frog degrades exactly as he always has: window_id() is
# None, so no pane spawns and the statusline gauge carries the whole show.
# Contract and the recorded decision: docs/surfaces.md.


def _tmux(*args):
    """Run one tmux command — the tmux surface's plumbing.

    Module-level on purpose: the tests stand a fake server in here, and the
    toggle-keybind installer (tmux-backend integration, further down) shares it.
    """
    import subprocess
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True,
                              timeout=3)
    except Exception:
        return None


class RenderSurface:
    """The interface a rendering surface implements. tmux is surface #1.

    Subclasses provide:
      name              — registry key ("tmux")
      display           — the surface's name as humans write it
      inside()          — is this process running under the surface?
      current_pane()    — the pane THIS process runs in (None if unknowable)
      window_id()       — the window holding a pane (or this process); None
                          when outside the surface, which is what makes every
                          caller degrade to the paneless story
      valid_window()    — is a window id well-formed? Ids are derived from the
                          environment but end up on command lines, so this is
                          where that assumption gets checked
      window_token() /
      window_from_token() — window id <-> the filename-safe token the window
                          state files are named by (win-<token>.json)
      live_panes()      — every pane id alive right now (claim liveness)
      frog_panes()      — {pane_id: window} for panes stamped as frogs
      spawn_pane()      — create the frog's stamped pane; the one place a
                          frog pane is ever born
      kill_pane()       — tear one down

    reap_legacy_panes() has a default because only tmux has a past to clean
    up (pre-window-scoping frogs); younger surfaces have nothing to reap.
    """

    name = ""
    display = ""

    def inside(self):
        raise NotImplementedError

    def current_pane(self):
        raise NotImplementedError

    def window_id(self, pane=None):
        raise NotImplementedError

    def valid_window(self, win):
        raise NotImplementedError

    def window_token(self, win):
        raise NotImplementedError

    def window_from_token(self, token):
        raise NotImplementedError

    def live_panes(self):
        raise NotImplementedError

    def frog_panes(self):
        raise NotImplementedError

    def spawn_pane(self, win, near, cmd, layout):
        raise NotImplementedError

    def kill_pane(self, pane):
        raise NotImplementedError

    def reap_legacy_panes(self, win, is_ours):
        return []


class TmuxSurface(RenderSurface):
    """tmux — the reference surface (and, for now, the only supported one).

    The facts this class owns: the tmux command vocabulary, the "@"+digits
    window-id format, the layout-name -> split-axis translation, and the
    `@claude_frog` pane option every spawn stamps on so a frog pane can be
    recognised even when no window file admits to owning him.
    """

    name = "tmux"
    display = "tmux"

    # Layout name -> split axis: -v stacks (top/bottom), -h sits side-by-side.
    SPLIT_AXIS = {"bottom": "-v", "top": "-v", "right": "-h", "left": "-h"}

    def inside(self):
        return bool(os.environ.get("TMUX"))

    def current_pane(self):
        return os.environ.get("TMUX_PANE")

    def window_id(self, pane=None):
        """The tmux window this process is running in, e.g. "@16".

        Hooks are spawned by the agent, which inherits TMUX_PANE from the pane
        it was launched in — so the window resolves without the caller having
        to know it. With no pane to go on (a `run-shell` keybind), tmux's own
        idea of the current window is the right answer.
        """
        if not self.inside():
            return None
        target = pane or self.current_pane()
        args = ["display-message", "-p"]
        if target:
            args += ["-t", target]
        args.append("#{window_id}")
        r = _tmux(*args)
        win = (r.stdout or "").strip() if r else ""
        return win or None

    def valid_window(self, win):
        """tmux window ids are "@" + digits — nothing else reaches a command
        line."""
        return bool(win) and win[0] == "@" and win[1:].isdigit()

    def window_token(self, win):
        # _safe_session strips the "@", leaving the digits: "@16" -> "16".
        return _safe_session(win)

    def window_from_token(self, token):
        return "@" + token

    def live_panes(self):
        """Every pane id tmux currently knows about, across all its sessions."""
        r = _tmux("list-panes", "-a", "-F", "#{pane_id}")
        return set((r.stdout or "").split()) if r else set()

    def frog_panes(self):
        """Frog panes tmux is showing right now: {pane_id: window it claims},
        read off the `@claude_frog` pane option every spawn stamps on."""
        r = _tmux("list-panes", "-a", "-F", "#{pane_id} #{@claude_frog}")
        out = {}
        for line in (r.stdout or "").splitlines() if r else []:
            bits = line.split(None, 1)
            if len(bits) == 2 and bits[1].strip():
                out[bits[0]] = bits[1].strip()
        return out

    def spawn_pane(self, win, near, cmd, layout):
        """Split a stamped pane running `cmd` into `win`; return its pane id
        (None if the split failed). `near` is the pane to split off, so the
        frog lands beside the session that summoned him rather than beside
        whatever the window happened to have focused.
        """
        axis = self.SPLIT_AXIS.get(layout, self.SPLIT_AXIS[DEFAULT_LAYOUT])
        size = LAYOUTS.get(layout, LAYOUTS[DEFAULT_LAYOUT])
        # -b puts the new pane *before* the target: above it for a vertical
        # split, left of it for a horizontal one.
        before = ["-b"] if layout in ("top", "left") else []
        r = _tmux("split-window", axis, *before, "-l", str(size), "-d",
                  "-t", near or win, "-P", "-F", "#{pane_id}", cmd)
        if not (r and r.returncode == 0):
            return None
        pid = (r.stdout or "").strip()
        if pid:
            _tmux("set-option", "-p", "-t", pid, "@claude_frog", win)
        return pid or None

    def kill_pane(self, pane):
        _tmux("kill-pane", "-t", pane)

    def reap_legacy_panes(self, win, is_ours):
        """Kill any pre-window-scoping frog still dancing in `win`.

        Frogs used to be spawned per session (`dance --session`) and carry no
        `@claude_frog` stamp, so the window bookkeeping is blind to them.
        Upgrading mid-session would otherwise leave one of those standing next
        to the new window-scoped frog — two frogs in one window, showing up
        precisely when someone first tests the fix. They answer to a session
        that no longer drives them, so there is nothing to preserve. `is_ours`
        is the frog's own identity test for a pane's start command — the
        surface carries the pane knowledge, the frog carries what "ours"
        means (the same split as the settings-surgery predicate).
        """
        r = _tmux("list-panes", "-t", win, "-F",
                  "#{pane_id}\t#{@claude_frog}\t#{pane_start_command}")
        killed = []
        for line in (r.stdout or "").splitlines() if r else []:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            pid, tag, cmd = parts
            if not tag.strip() and is_ours(cmd):
                self.kill_pane(pid)
                killed.append(pid)
        return killed


SURFACES = {s.name: s for s in (TmuxSurface(),)}
DEFAULT_SURFACE = "tmux"


def detect_surface():
    """The surface hosting this process.

    With tmux the sole supported surface (see the scaffolding note above)
    this always lands on tmux; outside a tmux server every call then degrades
    to "no window", which is exactly the paneless, statusline-only behavior
    sessions outside tmux have always had. When a second backend earns its
    way in, this becomes the walk-the-registry probe detect_agent() already
    is for adapters.
    """
    return SURFACES[DEFAULT_SURFACE]


SURFACE = detect_surface()


# --------------------------------------------------------------------------- #
# Mode: hook  (lifecycle events, via the adapter; think-state + pane life)     #
# --------------------------------------------------------------------------- #


def _win_path(win):
    return os.path.join(CACHE_DIR, "win-" + SURFACE.window_token(win) + ".json")


class _win_lock(object):
    """A cheap cross-process lock around one window file.

    O_CREAT|O_EXCL is atomic everywhere we run, which is all this needs: it
    serializes the read-modify-write in _win_claim so two sessions racing to
    claim the same empty window can't both spawn a frog. A lock older than
    WIN_LOCK_STALE_SECS is stolen — its holder was killed mid-update, and a
    window that can never spawn again is worse than a rare double-update.
    Unlockable (read-only cache dir) or contended past the deadline, we proceed
    anyway: the frog is decoration, and must never wedge a hook.
    """

    def __init__(self, win):
        self.path = _win_path(win) + ".lock"
        self.fd = None

    def __enter__(self):
        deadline = time.time() + WIN_LOCK_WAIT_SECS
        while True:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.fd = os.open(self.path,
                                  os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                return self
            except FileExistsError:
                pass
            except Exception:
                return self
            try:
                if time.time() - os.path.getmtime(self.path) > WIN_LOCK_STALE_SECS:
                    os.remove(self.path)
                    continue
            except Exception:
                pass
            if time.time() >= deadline:
                return self
            time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
                os.remove(self.path)
            except Exception:
                pass
        return False


def _read_win(win):
    """The window's frog record, normalised so callers never guard on shape."""
    try:
        with open(_win_path(win)) as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("not an object")
    except Exception:
        d = {}
    d.setdefault("pane", None)
    d.setdefault("active", None)
    if not isinstance(d.get("sessions"), dict):
        d["sessions"] = {}
    return d


def _newest_claim(sessions):
    if not sessions:
        return None
    return max(sessions.items(),
               key=lambda kv: float((kv[1] or {}).get("ts") or 0))[0]


def _prune_claims(state, live):
    """Drop claims whose Claude session is demonstrably gone.

    A claim records the pane the agent itself runs in, so liveness is a fact
    we can check rather than a timeout we have to guess: pane gone, session
    gone. Only claims we never resolved a pane for (a session started outside
    the surface, somehow) fall back to ageing out.
    """
    cutoff = time.time() - WIN_CLAIM_STALE_SECS
    keep = {}
    for sid, rec in state["sessions"].items():
        rec = rec if isinstance(rec, dict) else {}
        pane = rec.get("pane")
        if pane:
            if pane in live:
                keep[sid] = rec
        elif float(rec.get("ts") or 0) > cutoff:
            keep[sid] = rec
    state["sessions"] = keep
    if state.get("active") not in keep:
        state["active"] = _newest_claim(keep)
    return state


def _is_frog_dance_cmd(cmd):
    """Is this pane start command one of ours? Handed to the surface's
    legacy-pane reaper: the surface carries the pane knowledge, the frog
    carries his own identity (same split as `_is_frog_cmd` for settings)."""
    return "claude_frog.py" in cmd and " dance" in cmd


def _python():
    """The interpreter baked into every generated command string.

    sys.executable, so the pane daemon, settings.json wiring, and the tmux
    keybind all run on the same interpreter that installed them — including a
    pipx venv, where a bare `python3` from PATH would be a different Python
    (or missing). PATH `python3` only as a last resort (sys.executable can be
    empty in odd embeddings).
    """
    return sys.executable or "python3"


def _spawn_win_pane(win, near, session, layout=DEFAULT_LAYOUT,
                    theme=DEFAULT_THEME):
    """Build the dance command and have the surface split it into a pane.

    Returns the pane id (None if it failed). This is the frog's half of the
    spawn — interpreter, script path, theme, prop baseline — the surface owns
    the split itself (SURFACE.spawn_pane is the only place a pane is born).
    """
    import shlex
    py = _python()
    here = os.path.abspath(__file__)
    # theme is baked into the daemon's command so it stays fixed for the life of
    # the pane, even if the env changes later in the session.
    # `--since` captures the turn count *now*, in the hook process, so the diorama
    # baseline is fixed before the pane exists. Reading it inside the daemon after
    # it boots would race a fast first UserPromptSubmit and eat the first prop.
    since = _read_think(session)[1]
    # The surface runs this through a shell: quote the paths (a checkout under
    # a directory with a space would otherwise break the spawn silently). `win`
    # has already passed valid_window.
    cmd = (f"exec {shlex.quote(py)} {shlex.quote(here)} dance "
           f"--window {win} --theme {theme} --since {since}")
    return SURFACE.spawn_pane(win, near, cmd, layout)


def _win_claim(session, layout=DEFAULT_LAYOUT, theme=DEFAULT_THEME):
    """Register `session` as a claimant of its window's frog; spawn if needed.

    This function *is* the one-frog-per-window guarantee. A frog pane is only
    ever born here, only when the window has none, and only under the window
    lock — so however many Claude sessions a window ends up holding (a headless
    `claude -p` from a subagent, a nested `claude`, a `/clear` that mints a new
    session id), the first one spawns the frog and the rest just join the
    reference count. Returns the window id, or None when there's nothing to
    claim (no surface to render on).
    """
    win = SURFACE.window_id()
    if not SURFACE.valid_window(win):
        return None
    mine = SURFACE.current_pane()
    sid = _safe_session(session)
    with _win_lock(win):
        live = SURFACE.live_panes()
        st = _prune_claims(_read_win(win), live)
        if st.get("pane") not in live:
            # upgrade path: never stack on an old frog
            SURFACE.reap_legacy_panes(win, _is_frog_dance_cmd)
            st["pane"] = _spawn_win_pane(win, mine, session, layout, theme)
            st["theme"], st["layout"] = theme, layout
        st["sessions"][sid] = {"ts": time.time(), "pane": mine}
        st["active"] = sid
        _write_json(_win_path(win), st)
    return win


def _win_touch(session):
    """Mark `session` as the one currently working in its window.

    The frog shows whatever is working in the window he lives in, which is the
    only honest reading when a window holds more than one session.
    """
    win = SURFACE.window_id()
    if not SURFACE.valid_window(win):
        return
    sid = _safe_session(session)
    with _win_lock(win):
        st = _read_win(win)
        if not st["sessions"] and not st.get("pane"):
            return                      # no frog here; nothing to steer
        rec = st["sessions"].get(sid)
        rec = rec if isinstance(rec, dict) else {}
        rec["ts"] = time.time()
        if not rec.get("pane"):
            rec["pane"] = SURFACE.current_pane()
        st["sessions"][sid] = rec
        st["active"] = sid
        _write_json(_win_path(win), st)


def _win_release(session):
    """Drop this session's claim. The last claimant out kills the frog."""
    win = SURFACE.window_id()
    if not SURFACE.valid_window(win):
        return
    sid = _safe_session(session)
    with _win_lock(win):
        st = _prune_claims(_read_win(win), SURFACE.live_panes())
        st["sessions"].pop(sid, None)
        if st["sessions"]:
            if st.get("active") == sid:
                st["active"] = _newest_claim(st["sessions"])
            _write_json(_win_path(win), st)
        else:
            _kill_win_pane(st)
            try:
                os.remove(_win_path(win))
            except Exception:
                pass


def _kill_win_pane(st):
    pid = (st or {}).get("pane")
    if pid:
        SURFACE.kill_pane(pid)


def _kill_pane(session):
    """Legacy: tear down a pre-window-scoping per-session pane.

    Frogs are owned by windows now, but a session that was already running when
    the upgrade landed still has its old `<session>.pane` file and its own pane.
    Keeping this means such a session cleans up after itself on SessionEnd
    instead of leaving a frog nobody claims.
    """
    _, _, pane_path = _paths(session)
    try:
        if os.path.exists(pane_path):
            with open(pane_path) as f:
                pid = f.read().strip()
            if pid:
                SURFACE.kill_pane(pid)
            os.remove(pane_path)
    except Exception:
        pass


def _prune_stale():
    """Remove state for dead sessions and windows (best-effort).

    Three sweeps. First, window records: a window whose every claimant is gone
    loses its frog, and one whose frog pane died loses the record. Second,
    legacy per-session panes from before frogs were window-scoped. Third,
    session files with no pane at all: sessions run outside tmux, or hard-killed
    (crashed terminal, kill-server, OOM) before the SessionEnd hook could clean
    up, never trip the pane sweeps, so they used to pile up in CACHE_DIR
    forever. Those are aged out once they're older than STALE_STATE_SECS (this
    also catches orphaned .tmp files from interrupted writes). Every file is
    guarded on its own so one unreadable entry can't stop the rest of the sweep.
    """
    try:
        names = os.listdir(CACHE_DIR)
    except Exception:
        return
    live = SURFACE.live_panes()
    tracked = set()          # sessions that still have a live pane

    for fn in names:
        if not (fn.startswith("win-") and fn.endswith(".json")):
            continue
        try:
            win = SURFACE.window_from_token(fn[4:-5])
            with _win_lock(win):
                st = _prune_claims(_read_win(win), live)
                if not st["sessions"]:
                    _kill_win_pane(st)
                    os.remove(_win_path(win))
                else:
                    if st.get("pane") not in live:
                        st["pane"] = None
                    _write_json(_win_path(win), st)
                    tracked.update(st["sessions"])
        except Exception:
            pass

    for fn in names:
        if not fn.endswith(".pane"):
            continue
        try:
            with open(os.path.join(CACHE_DIR, fn)) as f:
                pid = f.read().strip()
            if pid and pid in live:
                tracked.add(fn[:-5])
            else:
                _cleanup_session(fn[:-5])
        except Exception:
            pass

    cutoff = time.time() - STALE_STATE_SECS
    for fn in names:
        if (fn.endswith(".pane") or fn.split(".")[0] in tracked
                or (fn.startswith("win-") and fn.endswith(".json"))):
            continue
        try:
            p = os.path.join(CACHE_DIR, fn)
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
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
    event = ADAPTER.hook_event(payload) or opts.get("event") or ""
    session = (ADAPTER.session_id(payload)
               or opts.get("session") or "default")
    think_path = _paths(session)[0]

    # Dispatch on the CANONICAL lifecycle event — the adapter owns what its
    # native event names mean, this function owns what the frog does about it.
    action = ADAPTER.canonical_event(event)
    if action == "session-start":
        _prune_stale()
        _write_json(think_path, {"state": "idle", "turns": 0, "ts": time.time()})
        # Joins this window's frog, spawning him only if the window has none.
        _win_claim(session, opts.get("layout", DEFAULT_LAYOUT),
                   opts.get("theme", DEFAULT_THEME))
    elif action == "prompt":
        _, turns = _read_think(session)
        _write_json(think_path, {"state": "thinking", "turns": turns + 1,
                                 "ts": time.time()})
        _win_touch(session)
    elif action == "stop":
        _, turns = _read_think(session)
        _write_json(think_path, {"state": "idle", "turns": turns, "ts": time.time()})
        _win_touch(session)
    elif action == "session-end":
        _win_release(session)
        _cleanup_session(session)
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Mode: toggle / pane / cleanup / preview                                      #
# --------------------------------------------------------------------------- #


def mode_toggle(opts):
    """tmux keybind: hide / summon the frog for the CURRENT WINDOW.

    Window-scoped, so the keybind means the same thing in every window — the
    old version guessed at the most recently touched session in the whole cache
    and so toggled a frog in some other window as often as not.

    Keys off pane LIVENESS, not the mere presence of a record: after a
    hand-killed pane the stale record used to make the first keypress a silent
    no-op, so summoning him back took two presses.
    """
    win = opts.get("window") or SURFACE.window_id()
    if not SURFACE.valid_window(win):
        sys.exit(0)
    with _win_lock(win):
        live = SURFACE.live_panes()
        st = _prune_claims(_read_win(win), live)
        if st.get("pane") in live:
            _kill_win_pane(st)
            # hiding must hide every frog in the window
            SURFACE.reap_legacy_panes(win, _is_frog_dance_cmd)
            st["pane"] = None
        elif SURFACE.reap_legacy_panes(win, _is_frog_dance_cmd):
            # Nothing tracked, but a pre-upgrade frog was on screen. The keypress
            # means "hide the frog I can see" — reaping it IS the hide. Spawning
            # a replacement here would make F look like it did nothing.
            st["pane"] = None
        else:
            session = st.get("active") or _newest_claim(st["sessions"]) or "default"
            st["pane"] = _spawn_win_pane(
                win, SURFACE.current_pane(), session,
                opts.get("layout", DEFAULT_LAYOUT),
                opts.get("theme", DEFAULT_THEME))
        _write_json(_win_path(win), st)
    sys.exit(0)


def mode_pane(opts):
    """Summon the frog into this window by hand (no Claude session required)."""
    session = opts.get("session") or "default"
    _write_json(_paths(session)[0], {"state": "idle", "turns": 0, "ts": time.time()})
    _win_claim(session, opts.get("layout", DEFAULT_LAYOUT),
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
    fade = _setting("fade")[0]
    print(f"\n--- {theme} status bar, fresh -> full window "
          f"(fade {'on' if fade else 'off'}) ---")
    for tok in (0, 60_000, 120_000, 180_000):
        sys.stdout.write(
            _statusline_text("preview", tok, 200_000, theme, fade) + "\n")
    sys.exit(0)


# Events the frog hooks into — the Claude Code adapter's list, re-exported
# under its historical module-level name (tests and external callers use it).
FROG_HOOK_EVENTS = ClaudeCodeAdapter.HOOK_EVENTS

# The comment install.sh writes above the launcher `source` line; doctor greps
# for it to confirm the launcher is installed. Keep in sync with install.sh.
MARKER = "claude-frog theme launcher"

# The same trick for the tmux keybind: a marker comment so the line can be found
# again to update or remove it, without touching anything else in tmux.conf.
TMUX_MARKER = "claude-frog toggle keybind"


# --------------------------------------------------------------------------- #
# Mode: config  (show / set / unset the persisted settings)                     #
# --------------------------------------------------------------------------- #

_C_OK = "\033[38;2;120;200;120m"
_C_WARN = "\033[38;2;230;180;90m"
_C_DIM = "\033[38;2;140;140;150m"
_C_PINK = "\033[38;2;240;156;188m"
_R = "\033[0m"


def _config_rows():
    """Every setting as (key, shown value, source, spec) in table order."""
    cfg = _read_config()
    rows = []
    for key, spec in SETTINGS.items():
        val, src = _setting(key, None, cfg)
        rows.append((key, spec["show"](val), src, spec))
    return rows


def _print_config():
    """Show what the frog is actually using, and *where each answer came from*.

    The source column is the point. A theme pinned by an old `export
    CLAUDE_FROG_THEME=` line in a shell rc silently outranks the config file,
    and without this you have to go hunting through dotfiles to find out why the
    frog won't change.
    """
    rows = _config_rows()
    print(f"{_C_PINK}🐸 Claude Frog — settings{_R}\n")
    width = max(len(k) for k in SETTINGS)
    shadowed = False
    for key, shown, src, spec in rows:
        note = {
            "env": f"{_C_WARN}from ${spec['env']}{_R}",
            "config": f"{_C_DIM}from {_config_path()}{_R}",
            "default": f"{_C_DIM}default{_R}",
            "flag": f"{_C_DIM}from a flag{_R}",
        }[src]
        print(f"  {key.ljust(width)}  {shown.ljust(9)} {note}")
        if src == "env" and key in _read_config():
            shadowed = True
    print(f"\n{_C_DIM}  choices: " + ";  ".join(
        f"{k} = {'|'.join(str(c) for c in s['choices'])}"
        for k, s in SETTINGS.items()) + _R)
    print(f"{_C_DIM}  set with:  claude_frog.py config <key> <value>{_R}")
    if shadowed:
        print(f"\n{_C_WARN}  ⚠️  An environment variable is overriding your config "
              f"file.{_R}\n     It's usually an `export CLAUDE_FROG_*` line left in "
              "your shell rc.\n     Remove it, or keep it if you meant to pin that "
              "session.")


def mode_config(argv):
    """`config` shows everything; `config <key> <value>` / `config unset <key>` set.

    An explicit user action, so unlike the tap/hook paths it reports failure
    rather than swallowing it.
    """
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        _print_config()
        sys.exit(0)

    unset = args[0] == "unset"
    if unset:
        args = args[1:]
    if not args or args[0] not in SETTINGS:
        sys.stderr.write(
            f"unknown setting: {args[0] if args else '(none)'}\n"
            f"known settings: {', '.join(SETTINGS)}\n")
        sys.exit(2)
    key = args[0]
    spec = SETTINGS[key]
    cfg = _read_config()
    # Report what's STORED, not what's in effect: with an env var shadowing the
    # file, "unchanged" would be a lie about a write that did happen.
    before = cfg.get(key, "(unset)")

    if unset:
        cfg.pop(key, None)
    else:
        if len(args) < 2:
            sys.stderr.write(f"usage: config {key} <{'|'.join(str(c) for c in spec['choices'])}>\n")
            sys.exit(2)
        val = spec["parse"](args[1])
        if val is None:
            sys.stderr.write(
                f"{args[1]!r} isn't a valid {key} — pick one of: "
                f"{', '.join(str(c) for c in spec['choices'])}\n")
            sys.exit(2)
        cfg[key] = spec["show"](val)

    if not _write_config(cfg):
        sys.stderr.write(f"could not write {_config_path()}\n")
        sys.exit(1)

    saved = _read_config()
    after = saved.get(key, "(unset)")
    if before == after:
        print(f"{key}: {after} (unchanged)")
    else:
        print(f"{_C_OK}{key}: {before} → {after}{_R}   ({_config_path()})")
    # The write can succeed and still not be what the frog uses. Say so, rather
    # than letting it look like a broken setting.
    effective, src = _setting(key, None, saved)
    if src == "env":
        print(f"{_C_WARN}⚠️  ${spec['env']} pins {key} to "
              f"{spec['show'](effective)} for new sessions — unset it in your "
              f"shell rc for this to take effect.{_R}")
    sys.exit(0)


# --------------------------------------------------------------------------- #
# Mode: setup  (the first-run wizard)                                          #
# --------------------------------------------------------------------------- #


def _open_tty():
    """The human's terminal, or None if there isn't one.

    Read from /dev/tty rather than stdin so the wizard still works when the
    whole installer was piped to bash (curl … | bash puts the *script* on
    stdin).
    """
    try:
        return open("/dev/tty")
    except Exception:
        return None


def _ask(tty, prompt, choices, current, preview=None):
    """One numbered multiple-choice question. Returns a choice."""
    try:
        print(f"\n{_C_PINK}{prompt}{_R}")
        for i, c in enumerate(choices, 1):
            mark = " ←  current" if c == current else ""
            print(f"  {i}. {c}{_C_DIM}{mark}{_R}")
            if preview:
                for line in preview(c):
                    print("     " + line)
        while True:
            sys.stdout.write(f"\nPick 1–{len(choices)} [{current}]: ")
            sys.stdout.flush()
            raw = (tty.readline() or "").strip()
            if not raw:
                return current
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]
            if raw in choices:
                return raw
            print(f"{_C_WARN}  not one of the options{_R}")
    except Exception:
        return current


def _theme_preview(theme):
    """Two rows of the real frog, in that theme, for the wizard to show."""
    try:
        spec = theme_spec(theme)
        rows = render_pixels(_colorize(FROG, spec["base"], spec["dither"]))
        return [r + _R for r in rows[:4]]
    except Exception:
        return []


def mode_setup(opts):
    """Interactive first-run wizard: pick a look, write it to the config file.

    Exists because the alternative was editing a shell rc, which is a strange
    thing to ask of someone who just wanted a frog.
    """
    tty = _open_tty()
    if tty is None:
        # Nobody to ask. Persisting the values we happen to resolve right now
        # would bake an env var's answer into the config file as though the user
        # had chosen it — so write nothing and say so.
        print(f"{_C_DIM}🐸 No terminal to ask on — skipping setup. "
              f"Run `setup` later, or `config <key> <value>`.{_R}")
        sys.exit(0)
    try:
        print(f"{_C_PINK}🐸 Let's set up your frog.{_R}")
        print(f"{_C_DIM}   Enter keeps what's there. Everything is changeable "
              f"later with `config`.{_R}")
        cfg = _read_config()
        theme = _ask(tty, "Which style?", list(SETTINGS["theme"]["choices"]),
                     _setting("theme", None, cfg)[0], _theme_preview)
        # Previewed deep in a window, where the two answers differ most: the
        # same frog, blushing or not. Both still dance and shake by then.
        fade = _ask(tty, "Should he blush toward Claude pink as context fills?",
                    list(SETTINGS["fade"]["choices"]),
                    SETTINGS["fade"]["show"](_setting("fade", None, cfg)[0]),
                    lambda c: [_statusline_text("setup-preview", 170_000,
                                                200_000, theme, c == "on")])
        layout = _ask(tty, "Where should his pane go?",
                      list(SETTINGS["layout"]["choices"]),
                      _setting("layout", None, cfg)[0])
        flora = _ask(tty, "Sprout a diorama prop on every prompt?",
                     list(SETTINGS["flora"]["choices"]),
                     SETTINGS["flora"]["show"](_setting("flora", None, cfg)[0]))
        statusline = _ask(
            tty, "A one-line frog + context gauge in your status bar too?",
            list(SETTINGS["statusline"]["choices"]),
            _setting("statusline", None, cfg)[0],
            lambda c: [_statusline_text("setup-preview", 78_000, 200_000, theme,
                                        fade == "on")]
            if c == "frog" else ["(status bar left alone)"])
    finally:
        try:
            tty.close()
        except Exception:
            pass
    cfg.update({"theme": theme, "fade": fade, "layout": layout, "flora": flora,
                "statusline": statusline})
    if not _write_config(cfg):
        sys.stderr.write(f"could not write {_config_path()}\n")
        sys.exit(1)
    print(f"\n{_C_OK}✅ Saved to {_config_path()}{_R}")
    _print_config()
    sys.exit(0)


# --------------------------------------------------------------------------- #
# The tmux toggle keybind                                                      #
# --------------------------------------------------------------------------- #
# Previously this was a snippet you were told to hand-paste into tmux.conf with
# the path swapped in yourself — so in practice nobody had the keybind the
# README advertised. The installer writes it now.
#
# This whole section is tmux-BACKEND integration, not frog logic: it knows
# tmux.conf on purpose, the way the opencode adapter knows its plugin file. A
# second rendering surface would bring its own summon story, not reuse this.


def _tmux_conf_path():
    """Where this machine keeps tmux.conf: whichever exists, else the classic."""
    for p in ("~/.tmux.conf", "~/.config/tmux/tmux.conf"):
        full = os.path.expanduser(p)
        if os.path.exists(full):
            return full
    return os.path.expanduser("~/.tmux.conf")


def _keybind_line():
    return f'bind F run-shell "{_python()} {os.path.abspath(__file__)} toggle"'


def _is_frog_bind(line):
    """True for any line that binds a key to the frog's toggle.

    Catches hand-written bindings as well as ours: plenty of people pasted the
    README snippet in themselves, and appending a second `bind F` next to
    theirs would leave two bindings fighting over one key.
    """
    s = line.strip()
    return (s.startswith("bind") and "claude_frog.py" in s and "toggle" in s)


def _keybind_installed(path=None):
    try:
        with open(path or _tmux_conf_path()) as f:
            text = f.read()
    except Exception:
        return False
    return TMUX_MARKER in text or any(_is_frog_bind(ln) for ln in text.splitlines())


def install_keybind(path=None):
    """Append the marker-guarded `prefix + F` binding. Returns (changed, path).

    Idempotent: an existing block is rewritten in place, so moving the checkout
    updates the path rather than stacking a second binding.
    """
    path = path or _tmux_conf_path()
    block = f"\n# {TMUX_MARKER} — prefix + F hides / summons the frog\n{_keybind_line()}\n"
    try:
        lines = []
        if os.path.exists(path):
            with open(path) as f:
                lines = f.read().splitlines(True)
        kept, skip = [], False
        for line in lines:
            if TMUX_MARKER in line:
                skip = True                 # drop the marker and its bind line
                continue
            if skip:
                skip = False
                if line.lstrip().startswith("bind"):
                    continue
            if _is_frog_bind(line):         # adopt a hand-written binding
                continue
            kept.append(line)
        old = "".join(lines)
        new = "".join(kept).rstrip("\n") + "\n" + block if kept else block.lstrip("\n")
        if old == new:
            return False, path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(new)
        # Load it now so the keybind works without a reload — but ONLY when we
        # wrote this machine's real tmux.conf and a server is actually up.
        # Sourcing an arbitrary --tmux-conf would apply a scratch file's every
        # setting to the user's live session.
        r = _tmux("has-session")
        if path == _tmux_conf_path() and r is not None and r.returncode == 0:
            _tmux("source-file", path)
        return True, path
    except Exception:
        return False, path


def uninstall_keybind(path=None):
    """Remove the marker-guarded binding, leaving the rest of tmux.conf alone."""
    path = path or _tmux_conf_path()
    try:
        if not os.path.exists(path):
            return False, path
        with open(path) as f:
            lines = f.read().splitlines(True)
        kept, skip, dropped = [], False, False
        for line in lines:
            if TMUX_MARKER in line:
                skip, dropped = True, True
                continue
            if skip:
                skip = False
                if line.lstrip().startswith("bind"):
                    continue
            if _is_frog_bind(line):
                dropped = True
                continue
            kept.append(line)
        if not dropped:
            return False, path
        # Our block is preceded by a blank separator line; drop that too, so an
        # uninstall leaves tmux.conf byte-identical to how we found it.
        text = "".join(kept).rstrip("\n")
        with open(path, "w") as f:
            f.write(text + "\n" if text else "")
        return True, path
    except Exception:
        return False, path


def mode_config_path(opts):
    """Print where settings live, so shell callers don't reimplement XDG rules."""
    sys.stdout.write(_config_path() + "\n")
    sys.exit(0)


def mode_tmux_conf_path(opts):
    """Print which tmux.conf the keybind would be written to."""
    sys.stdout.write(_tmux_conf_path() + "\n")
    sys.exit(0)


def mode_install_keybind(opts):
    changed, path = install_keybind(opts.get("tmux_conf"))
    print(f"✅ tmux keybind {'added to' if changed else 'already in'} {path}"
          f"   (prefix + F)")
    sys.exit(0)


def mode_uninstall_keybind(opts):
    changed, path = uninstall_keybind(opts.get("tmux_conf"))
    print(f"   {'-' if changed else '•'} tmux keybind "
          f"{'removed from' if changed else 'not found in'} {path}")
    sys.exit(0)


def _frog_cmd(kind):
    """The command string baked into the agent's wiring for `kind` (hook/tap).

    A non-default agent is pinned right in the command — the wired invocation
    must land on the adapter that wrote it, whatever detection would say."""
    cmd = f"{_python()} {os.path.abspath(__file__)} {kind}"
    if ADAPTER.name != DEFAULT_AGENT:
        cmd += f" --agent {ADAPTER.name}"
    return cmd


def _is_frog_cmd(cmd):
    return isinstance(cmd, str) and "claude_frog.py" in cmd


def _event_has_frog_hook(groups):
    """True if this event's hook list already runs the frog (any group)."""
    return ADAPTER.event_has_hook(groups, _is_frog_cmd)


def mode_install_settings(opts):
    """Merge the frog's statusLine tap + hooks into ~/.claude/settings.json.

    Deliberately conservative: preserves everything already in the file, backs
    it up first, and is idempotent (re-running changes nothing). An existing
    non-frog statusLine is left untouched — Claude Code allows only one, so we
    won't clobber yours; the message points you at the compose wrapper instead.
    A frog statusLine still on the deprecated `statusline` mode is migrated to
    `tap`. Unlike the tap/hook paths this is an explicit action, so it may fail
    loudly rather than swallowing errors.

    The file I/O, backup, and reporting live here; everything that knows the
    settings-file SCHEMA lives in the adapter (install_wiring).
    """
    path = ADAPTER.settings_path(opts.get("settings"))
    # "statusline" (the deprecated in-bar frog) and anything unrecognized both
    # land on tap; "none" skips the statusLine entirely.
    sl_mode = opts.get("statusline_mode") or "tap"
    if sl_mode != "none":
        sl_mode = "tap"

    # Load the existing artifact, refusing to clobber one we can't honor.
    text = None
    existed = os.path.exists(path)
    if existed:
        with open(path) as f:
            text = f.read()
    try:
        data = ADAPTER.parse_settings(text)
    except ValueError as e:
        sys.stderr.write(f"✗ {path} {e}; leaving it untouched.\n"
                         f"  Fix or move it, then re-run.\n")
        sys.exit(1)

    try:
        changed, notes = ADAPTER.install_wiring(
            data, tap_cmd=_frog_cmd("tap"), hook_cmd=_frog_cmd("hook"),
            is_ours=_is_frog_cmd, statusline=sl_mode != "none")
    except ValueError as e:
        sys.stderr.write(f"✗ {path} {e}; leaving it alone.\n")
        sys.exit(1)

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
        f.write(ADAPTER.serialize_settings(data))
    os.replace(tmp, path)

    print(f"✅ Wired the frog into {path}:")
    for c in changed:
        print(f"   + {c}")
    for n in notes:
        print(f"   • {n}")
    if existed:
        print(f"   (backed up your previous settings to {path}.bak)")
    print(f"   Start a new {ADAPTER.display} session to see him.")


def mode_uninstall_settings(opts):
    """Remove ONLY the frog's statusLine + hooks from settings.json.

    The mirror of install-settings: backs the file up, drops the frog's own
    statusLine (never someone else's) and any frog hook groups, prunes emptied
    event lists, and leaves everything else exactly as it was. Idempotent.
    Schema knowledge lives in the adapter (uninstall_wiring).
    """
    path = ADAPTER.settings_path(opts.get("settings"))
    if not os.path.exists(path):
        print(f"Nothing to remove — {path} doesn't exist.")
        return
    with open(path) as f:
        text = f.read()
    try:
        data = ADAPTER.parse_settings(text)
    except ValueError as e:
        sys.stderr.write(f"✗ {path} {e}; leaving it untouched.\n")
        sys.exit(1)

    removed = ADAPTER.uninstall_wiring(data, is_ours=_is_frog_cmd)

    if not removed:
        print(f"✅ No frog settings found in {path} — nothing to remove.")
        return

    with open(path + ".bak", "w") as f:
        f.write(text)
    out = ADAPTER.serialize_settings(data)
    if out is None:
        # The wiring artifact was wholly ours (e.g. opencode's plugin file):
        # uninstalling it means the file goes away, not that it empties.
        os.remove(path)
    else:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(out)
        os.replace(tmp, path)
    print(f"✅ Removed the frog from {path}:")
    for r in removed:
        print(f"   - {r}")
    print(f"   (backed up your previous settings to {path}.bak)")


def mode_doctor(opts):
    """A green/amber checkup so a first-timer KNOWS it worked.

    Verifies the five things that make the frog appear — python3 (3.9+), the
    launcher line, the token-feed (tap) wiring, the dance hooks, a resolvable
    theme — plus non-critical notes on the terminal (truecolor, NO_COLOR,
    UTF-8 half-blocks) and on tmux (where the frog actually lives). Exits
    non-zero only if a *critical* piece is missing, so callers can gate on it;
    the tmux note never fails the check.
    """
    C_OK = "\033[38;2;120;200;120m"
    C_WARN = "\033[38;2;230;180;90m"
    R = "\033[0m"
    rows = []       # (label, ok, critical, detail)

    py_ok = sys.version_info >= (3, 9)
    rows.append(("Python 3", py_ok, True,
                 "%d.%d.%d" % sys.version_info[:3]
                 + ("" if py_ok else " — below the declared floor (3.9+)")))

    # Launcher line in a shell rc (use --rc if the installer told us which
    # one). Only agents with a shell-launcher story get judged on it.
    if ADAPTER.USES_SHELL_LAUNCHER:
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
    else:
        rows.append(("Launcher", True, False,
                     f"n/a for {ADAPTER.display} — pick a theme with "
                     "`claude-frog config theme <name>`"))

    # settings.json: token feed (tap) + hooks. In --minimal mode the user
    # deliberately skipped these, so they're informational, not failures.
    minimal = bool(opts.get("minimal"))
    path = ADAPTER.settings_path(opts.get("settings"))
    sl_ok = hooks_ok = False
    foreign_sl = False
    detail = f"not wired — {ADAPTER.INSTALL_HINT}"
    data = None
    if os.path.exists(path):
        try:
            with open(path) as f:
                t = f.read()
            data = ADAPTER.parse_settings(t)
        except ValueError as e:
            detail = f"{path} {e}"
    if data is not None:
        sl_ok, foreign_sl, hooks_ok = ADAPTER.wiring_status(data, _is_frog_cmd)
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
                     else f"some hooks missing — {ADAPTER.INSTALL_HINT}"))

    # Settings, and — the useful part — where each answer is coming from. An
    # `export CLAUDE_FROG_THEME=` left in a shell rc silently outranks the
    # config file, and this is where you find that out.
    have_config = os.path.exists(_config_path())
    rows.append(("Settings file", True, False,
                 _config_path() if have_config
                 else "none yet — run `setup`, or `config <key> <value>`"))
    for key, shown, src, spec in _config_rows():
        note = {"env": f"pinned by ${spec['env']}",
                "config": "from your settings file",
                "flag": "from a flag",
                "default": "default"}[src]
        shadowed = src == "env" and key in _read_config()
        rows.append((key.capitalize(), not shadowed, False,
                     f"{shown}  ({note})"
                     + (" — this is overriding your settings file" if shadowed else "")))

    # Terminal requirements, declared honestly: the palettes emit 24-bit
    # escapes with no 256-color fallback, and the frog is drawn in Unicode
    # half-blocks — so these are requirements, not niceties.
    colorterm = os.environ.get("COLORTERM", "")
    truecolor = colorterm.lower() in ("truecolor", "24bit")
    rows.append(("Truecolor", truecolor, False,
                 f"COLORTERM={colorterm}" if truecolor else
                 "COLORTERM isn't truecolor/24bit — the frog paints 24-bit "
                 "color with no 256-color fallback, so his palette may come "
                 "out wrong (use WezTerm, iTerm2, or Kitty)"))
    if os.environ.get("NO_COLOR"):
        rows.append(("NO_COLOR", False, False,
                     "set, but the frog doesn't honor it — he renders in "
                     "color anyway"))
    enc_utf8 = "utf8" in (sys.stdout.encoding or "").lower().replace("-", "")
    rows.append(("Half-blocks (▀▄)", enc_utf8, False,
                 "UTF-8 out — just make sure your font draws ▀/▄" if enc_utf8
                 else f"stdout encoding is {sys.stdout.encoding or 'unknown'}, "
                      "not UTF-8 — the frog is drawn in ▀/▄ half-blocks"))

    in_surface = SURFACE.inside()
    rows.append((f"Dancing pane ({SURFACE.name})", in_surface, False,
                 f"in {SURFACE.display} — you get the full show" if in_surface
                 else f"not in {SURFACE.display} — the frog lives in a "
                      f"{SURFACE.display} pane, so you won't see him "
                      "(add tmux + WezTerm)"))

    kb = _keybind_installed()
    rows.append(("Toggle keybind", kb, False,
                 f"prefix + F, in {_tmux_conf_path()}" if kb
                 else "not installed — run install.sh (or `install-keybind`)"))

    # One frog per window is the contract; surface it if reality disagrees.
    if in_surface:
        per_win = {}
        for _pane, win in SURFACE.frog_panes().items():
            per_win[win] = per_win.get(win, 0) + 1
        crowded = sorted(w for w, n in per_win.items() if n > 1)
        rows.append(("Frogs on screen", not crowded, False,
                     f"{sum(per_win.values())} in {len(per_win)} window(s)"
                     if not crowded else
                     f"more than one in {', '.join(crowded)} — run `cleanup`"))

    crit_ok = all(ok for _, ok, critical, _ in rows if critical)

    print("🐸 Claude Frog — checkup\n")
    for label, ok, _critical, det in rows:
        mark = (C_OK + "✅" + R) if ok else (C_WARN + "⚠️ " + R)
        print("  %s %-24s %s" % (mark, label, det))
    print()
    if crit_ok:
        print(C_OK + "All set." + R
              + ("  Open a NEW terminal (or `source` your rc), then:  claude SEGA"
                 if ADAPTER.USES_SHELL_LAUNCHER
                 else f"  Start a new {ADAPTER.display} session to see him."))
    else:
        print(C_WARN + "Some things need attention" + R
              + f" — fix the ⚠️  above, then re-run:  {_python()} "
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
    opts = {"session": None, "window": None, "layout": None, "theme": None,
            "always": False, "party": False, "event": None,
            "settings": None, "statusline_mode": "tap", "rc": None,
            "minimal": False, "since": None, "tmux_conf": None, "agent": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--session", "-s"):
            i += 1; opts["session"] = argv[i]
        elif a in ("--window", "-w"):
            i += 1; opts["window"] = argv[i]
        elif a == "--layout":
            i += 1; opts["layout"] = argv[i]
        elif a == "--theme":
            i += 1; opts["theme"] = argv[i]
        elif a == "--event":
            i += 1; opts["event"] = argv[i]
        elif a == "--settings":
            i += 1; opts["settings"] = argv[i]
        elif a == "--agent":
            i += 1; opts["agent"] = argv[i]
        elif a == "--statusline-mode":
            i += 1; opts["statusline_mode"] = argv[i]
        elif a == "--rc":
            i += 1; opts["rc"] = argv[i]
        elif a == "--tmux-conf":
            i += 1; opts["tmux_conf"] = argv[i]
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
    if opts["window"] is not None and not SURFACE.valid_window(opts["window"]):
        opts["window"] = None
    # Both resolve through SETTINGS (flag > env > config file > default), so the
    # SessionStart hook and the tmux toggle keybind agree on an answer without
    # threading --layout / --theme through every call site. Friendly theme
    # spellings ("SEGA", "Game Boy") are accepted from any layer.
    cfg = _read_config()
    opts["layout"] = _setting("layout", opts["layout"], cfg)[0]
    opts["theme"] = _setting("theme", opts["theme"], cfg)[0]
    return mode, opts


def main():
    mode, opts = _parse(sys.argv[1:])
    # --agent pins the adapter for this invocation (wired commands carry it so
    # they land where they were installed); detection stays the default. An
    # unknown name is a loud error on explicit modes — but the tap/hook paths
    # never crash, so there it falls back to detection instead.
    global ADAPTER
    if opts.get("agent"):
        picked = ADAPTERS.get(opts["agent"])
        if picked is not None:
            ADAPTER = picked
        elif mode not in ("tap", "statusline", "hook"):
            sys.stderr.write(f"unknown agent: {opts['agent']} "
                             f"(supported: {', '.join(ADAPTERS)})\n")
            sys.exit(2)
    try:
        if mode == "dance":
            if not opts["session"] and not opts["window"]:
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
        elif mode == "config":
            mode_config(sys.argv[1:])
        elif mode == "setup":
            mode_setup(opts)
        elif mode == "config-path":
            mode_config_path(opts)
        elif mode == "tmux-conf-path":
            mode_tmux_conf_path(opts)
        elif mode == "install-keybind":
            mode_install_keybind(opts)
        elif mode == "uninstall-keybind":
            mode_uninstall_keybind(opts)
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
