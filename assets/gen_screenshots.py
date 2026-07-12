#!/usr/bin/env python3
"""Render a PNG screenshot of the frog in each theme, for the README.

Stdlib only (zlib + struct hand-roll a PNG) to match the project — no Pillow.
Pulls colors straight from claude_frog's palettes, so the screenshots always
match what the terminal actually draws. Regenerate with:

    python3 assets/gen_screenshots.py
"""
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import claude_frog as cf  # noqa: E402

SCALE = 12          # pixels per sprite cell
PAD = 20            # border around the art
GAP = 10            # space between the frog and its fade bar
BAR_H = 16          # height (sprite cells worth is separate) of the fade strip
BG = (0x15, 0x17, 0x1e)     # dark charcoal, reads on GitHub light + dark
FRAME = (0x2f, 0x34, 0x41)  # subtle 1px inner frame around the art


def _png(path, width, height, pixels):
    """pixels: flat list of (r,g,b) rows -> write a truecolor PNG."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            raw += bytes(pixels[y][x])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def render_theme(theme, out):
    spec = cf.theme_spec(theme)
    # the signature "fresh" frog
    grid = cf._colorize(cf.FROG, cf.palette_for(0, theme), spec["dither"])
    sh, sw = len(grid), len(grid[0])
    art_w, art_h = sw * SCALE, sh * SCALE

    W = PAD * 2 + art_w
    H = PAD * 2 + art_h + GAP + BAR_H
    canvas = [[BG for _ in range(W)] for _ in range(H)]

    def put(px, py, color):
        if 0 <= px < W and 0 <= py < H:
            canvas[py][px] = color

    # frog, nearest-neighbor upscaled; transparent cells keep the bg
    for cy in range(sh):
        for cx in range(sw):
            col = grid[cy][cx]
            if col is None:
                continue
            for dy in range(SCALE):
                for dx in range(SCALE):
                    put(PAD + cx * SCALE + dx, PAD + cy * SCALE + dy, col)

    # fade bar: the body midtone sampled green->pink across the whole window,
    # so each theme shows the exact gauge it rides.
    bar_top = PAD + art_h + GAP
    for i in range(art_w):
        t = int(cf.PINK_FULL_TOKENS * i / max(1, art_w - 1))
        col = cf.palette_for(t, theme)["B"]
        for dy in range(BAR_H):
            put(PAD + i, bar_top + dy, col)

    # thin frame around the frog art
    for x in range(PAD - 1, PAD + art_w + 1):
        put(x, PAD - 1, FRAME); put(x, PAD + art_h, FRAME)
    for y in range(PAD - 1, PAD + art_h + 1):
        put(PAD - 1, y, FRAME); put(PAD + art_w, y, FRAME)

    _png(out, W, H, canvas)
    print(f"wrote {out}  ({W}x{H})")


SCENE_COLS = 70      # stage width in sprite cells (wide enough to flank the frog)
SCENE_ROWS = 7       # a default top/bottom pane is 7 char-rows tall


def render_scene(out, theme=cf.DEFAULT_THEME):
    """The dance pane's accumulating diorama — frog flanked by his props.

    A curated, fully-grown scene (deterministic, so the asset regenerates
    identically) rendered through the real Scene.blits + blit path, so it shows
    exactly what the pane draws: one of each prop kind around a fresh frog, plus
    a couple of drifting clouds. Only the frog wears `theme`; the props keep their
    fixed natural palette (a rock is grey in any decade), exactly as in the pane.
    """
    import random
    stage_h = SCENE_ROWS * 2
    sc = cf.Scene(rng=random.Random(0))

    def ground(kind, side, slot, hue=0.0):
        return {"kind": kind, "birth": 0, "side": side, "slot": slot,
                "hue": hue, "phase": 0.0}

    def cloud(x, y):
        return {"kind": "cloud", "birth": 0, "dir": 1, "speed": 0.0,
                "y": y, "x": float(x), "hue": 0.0, "phase": 0.0}

    sc.props = [
        ground("tree", -1, 0),
        ground("rock", -1, 1),
        ground("flower", -1, 2, hue=0.92),   # magenta bloom
        ground("log", 1, 0),
        ground("flower", 1, 1, hue=0.13),     # gold bloom
        ground("tree", 1, 2),
        cloud(4, 0),
        cloud(SCENE_COLS - 10, 1),
    ]

    frog = cf.pose(cf.FROG, cf._FROG_BLINK, {},          # fresh, upright, eyes open
                   cf.palette_for(0, theme), cf.theme_spec(theme)["dither"])
    fw = len(frog[0])
    rest_x = (SCENE_COLS - fw) // 2
    stage = [[None] * SCENE_COLS for _ in range(stage_h)]
    frame = 50   # past every entrance animation, so the diorama is settled
    for spr, sx, sy in sc.blits(frame, SCENE_COLS, stage_h, rest_x, fw):
        cf.blit(stage, spr, sx, sy)
    cf.blit(stage, frog, rest_x, stage_h - len(frog))

    art_w, art_h = SCENE_COLS * SCALE, stage_h * SCALE
    W, H = PAD * 2 + art_w, PAD * 2 + art_h
    canvas = [[BG for _ in range(W)] for _ in range(H)]

    def put(px, py, color):
        if 0 <= px < W and 0 <= py < H:
            canvas[py][px] = color

    for cy in range(stage_h):
        for cx in range(SCENE_COLS):
            col = stage[cy][cx]
            if col is None:
                continue
            for dy in range(SCALE):
                for dx in range(SCALE):
                    put(PAD + cx * SCALE + dx, PAD + cy * SCALE + dy, col)

    for x in range(PAD - 1, PAD + art_w + 1):
        put(x, PAD - 1, FRAME); put(x, PAD + art_h, FRAME)
    for y in range(PAD - 1, PAD + art_h + 1):
        put(PAD - 1, y, FRAME); put(PAD + art_w, y, FRAME)

    _png(out, W, H, canvas)
    print(f"wrote {out}  ({W}x{H})")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for theme in cf.THEMES:
        render_theme(theme, os.path.join(here, f"frog-{theme}.png"))
    render_scene(os.path.join(here, "frog-scene.png"))
    render_scene(os.path.join(here, "frog-scene-terraria.png"), theme="terraria")
