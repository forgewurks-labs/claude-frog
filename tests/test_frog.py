#!/usr/bin/env python3
"""Sanity tests for claude_frog — the kind of breakage a sprite edit causes.

Two layers:
  * in-process checks of the pure sprite / render internals, and
  * subprocess checks that the never-crash CLI modes really exit 0.

Stdlib only (unittest), to match the project. Run: python3 -m unittest -v
(from the repo root) or via `python3 tests/test_frog.py`.
"""

import json
import math
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "claude_frog.py")
sys.path.insert(0, ROOT)

import claude_frog as cf  # noqa: E402


class TestSprites(unittest.TestCase):
    def test_sprites_are_rectangular(self):
        for name, grid in (("FROG", cf.FROG), ("CHIBI", cf.CHIBI)):
            widths = {len(row) for row in grid}
            self.assertEqual(len(widths), 1, f"{name} rows are ragged: {widths}")

    def test_expected_dimensions(self):
        # motion/pane sizing assume these; a stray row would drift the floor.
        self.assertEqual((len(cf.FROG), len(cf.FROG[0])), (12, 19))
        self.assertEqual((len(cf.CHIBI), len(cf.CHIBI[0])), (6, 15))

    def test_every_palette_key_resolves(self):
        used = {ch for grid in (cf.FROG, cf.CHIBI) for row in grid for ch in row}
        missing = used - set(cf.RGB)
        self.assertFalse(missing, f"sprite uses keys absent from RGB: {missing}")

    def test_shade_map_covers_palette(self):
        # preview mode maps every palette key to an ASCII glyph.
        missing = set(cf.RGB) - set(cf._SHADE)
        self.assertFalse(missing, f"_SHADE missing keys: {missing}")


class TestBlink(unittest.TestCase):
    def test_blink_overlays_are_in_bounds(self):
        for base, overlay in ((cf.FROG, cf._FROG_BLINK),
                              (cf.CHIBI, cf._CHIBI_BLINK)):
            h, w = len(base), len(base[0])
            for y, line in overlay.items():
                self.assertTrue(0 <= y < h, f"blink row {y} out of range")
                self.assertLessEqual(len(line), w, f"blink row {y} too wide")

    def test_blink_frames_render(self):
        for base, overlay in ((cf.FROG, cf._FROG_BLINK),
                              (cf.CHIBI, cf._CHIBI_BLINK)):
            px = cf.pose(base, overlay, {"blink": True})
            self.assertTrue(cf.render_pixels(px))


class TestRenderPipeline(unittest.TestCase):
    def test_choreography_never_raises(self):
        # every move, across the goofiness range, active and idle.
        import random
        random.seed(1)
        chor = cf.Choreographer()
        for i in range(600):
            g = (i % 11) / 10.0
            params = chor.step(active=bool(i % 2), g=g)
            px = cf.pose(cf.FROG, cf._FROG_BLINK, params)
            cf.render_pixels(px)  # must not raise

    def test_render_height_halves_pixels(self):
        px = cf.pose(cf.FROG, cf._FROG_BLINK, {})
        self.assertEqual(len(cf.render_pixels(px)), math.ceil(len(px) / 2))

    def test_transforms_preserve_rectangularity(self):
        px = cf._colorize(cf.FROG)
        for grid in (cf.shear(px, 3.0), cf.flip_h(px), cf.flip_v(px),
                     cf.squash(px, 2)):
            widths = {len(r) for r in grid}
            self.assertEqual(len(widths), 1)


class TestGauges(unittest.TestCase):
    def test_goofiness_is_clamped(self):
        for tok in (0, cf.CALM_TOKENS, cf.UNHINGED_TOKENS, 10 ** 9, None):
            g = cf.goofiness(tok, turns=3)
            self.assertTrue(0.0 <= g <= 1.0, f"goofiness out of range for {tok}")

    def test_shake_starts_at_floor_and_saturates(self):
        self.assertEqual(cf.shake_px(0), 0.0)
        self.assertEqual(cf.shake_px(cf.SHAKE_START_TOKENS), 0.0)
        self.assertEqual(cf.shake_px(10 ** 9), float(cf.SHAKE_MAX_PX))


class TestCliModesExitZero(unittest.TestCase):
    """The statusline / tap / hook / preview paths must never break a prompt."""

    def _run(self, args, stdin=""):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], input=stdin,
            capture_output=True, text=True, timeout=15,
        )

    def test_preview(self):
        for which in ("frog", "chibi"):
            r = self._run(["preview", "--which", which])
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_statusline_and_tap_survive_junk(self):
        payloads = ["", "not json", "{}",
                    json.dumps({"session_id": "t",
                                "context_window": {"used_percentage": 62}})]
        for mode in ("statusline", "tap"):
            for p in payloads:
                r = self._run([mode], stdin=p)
                self.assertEqual(r.returncode, 0, f"{mode} <- {p!r}: {r.stderr}")

    def test_hook_events_survive_junk(self):
        for p in ("", "garbage",
                  json.dumps({"hook_event_name": "Stop", "session_id": "t"})):
            r = self._run(["hook"], stdin=p)
            self.assertEqual(r.returncode, 0, f"hook <- {p!r}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
