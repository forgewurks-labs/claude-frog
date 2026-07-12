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


class TestThemes(unittest.TestCase):
    def test_every_theme_resolves_all_sprite_keys(self):
        # a theme missing a key would paint a transparent hole in the frog.
        keys = {ch for grid in (cf.FROG, cf.CHIBI) for row in grid for ch in row}
        for name, spec in cf.THEMES.items():
            for k in keys:
                if k in (" ", "."):
                    continue
                self.assertIsNotNone(
                    spec["base"].get(k), f"{name} base missing key {k!r}")

    def test_default_theme_is_registered(self):
        self.assertIn(cf.DEFAULT_THEME, cf.THEMES)

    def test_palette_for_fades_each_theme(self):
        for name, spec in cf.THEMES.items():
            # identity at zero tokens (the common, hot path)
            self.assertIs(cf.palette_for(0, name), spec["base"])
            self.assertIs(cf.palette_for(None, name), spec["base"])
            # fully faded body at/above the pink-full mark
            full = cf.palette_for(cf.PINK_FULL_TOKENS, name)
            self.assertEqual(full["B"], spec["pink"]["B"])
            # a genuine blend in between (not either endpoint)
            mid = cf.palette_for(cf.PINK_FULL_TOKENS // 2, name)["B"]
            self.assertNotIn(mid, (spec["base"]["B"], spec["pink"]["B"]))

    def test_unknown_theme_falls_back_to_default(self):
        self.assertIs(cf.theme_spec("bogus"), cf.THEMES[cf.DEFAULT_THEME])
        self.assertIs(cf.palette_for(0, "bogus"),
                      cf.THEMES[cf.DEFAULT_THEME]["base"])

    def test_defaults_to_snes_when_no_theme_selected(self):
        # The contingency: no flag, no/blank/junk env, or a junk --theme all
        # land on SNES — the frog is never left themeless.
        import os
        self.assertEqual(cf.DEFAULT_THEME, "snes")
        old = os.environ.pop("CLAUDE_FROG_THEME", None)
        try:
            self.assertEqual(cf._parse(["dance"])[1]["theme"], "snes")
            self.assertEqual(cf._parse(["dance", "--theme", "xyz"])[1]["theme"],
                             "snes")
            for junk in ("", "playstation"):
                os.environ["CLAUDE_FROG_THEME"] = junk
                self.assertEqual(cf._parse(["dance"])[1]["theme"], "snes",
                                 f"env={junk!r}")
        finally:
            os.environ.pop("CLAUDE_FROG_THEME", None)
            if old is not None:
                os.environ["CLAUDE_FROG_THEME"] = old

    def test_dither_darkens_alternating_pixels(self):
        # Genesis cross-hatches its body midtone; a solid B block must come out
        # two-toned, and a non-dithered theme must not.
        block = [["B", "B"], ["B", "B"]]
        gen = cf.theme_spec("genesis")
        px = cf._colorize(block, gen["base"], gen["dither"])
        self.assertEqual(len({c for row in px for c in row}), 2)
        flat = cf._colorize(block, cf.THEMES["snes"]["base"],
                            cf.THEMES["snes"]["dither"])
        self.assertEqual(len({c for row in flat for c in row}), 1)

    def test_theme_selection_flag_env_and_fallback(self):
        import os
        old = os.environ.pop("CLAUDE_FROG_THEME", None)
        try:
            self.assertEqual(cf._parse(["dance", "--theme", "gba"])[1]["theme"],
                             "gba")
            os.environ["CLAUDE_FROG_THEME"] = "genesis"
            self.assertEqual(cf._parse(["dance"])[1]["theme"], "genesis")
            os.environ["CLAUDE_FROG_THEME"] = "nope"
            self.assertEqual(cf._parse(["dance"])[1]["theme"], cf.DEFAULT_THEME)
            # friendly aliases resolve from both the flag and the env var
            os.environ.pop("CLAUDE_FROG_THEME", None)
            self.assertEqual(cf._parse(["dance", "--theme", "SEGA"])[1]["theme"],
                             "genesis")
            os.environ["CLAUDE_FROG_THEME"] = "Game Boy"
            self.assertEqual(cf._parse(["dance"])[1]["theme"], "gba")
        finally:
            os.environ.pop("CLAUDE_FROG_THEME", None)
            if old is not None:
                os.environ["CLAUDE_FROG_THEME"] = old

    def test_resolve_theme_aliases(self):
        cases = {
            "snes": "snes", "SNES": "snes", "Nintendo": "snes", "super": "snes",
            "genesis": "genesis", "SEGA": "genesis", "Mega Drive": "genesis",
            "md": "genesis",
            "gba": "gba", "GBA": "gba", "Game Boy": "gba", "gameboy": "gba",
            "gb": "gba",
        }
        for spelling, canon in cases.items():
            self.assertEqual(cf.resolve_theme(spelling), canon, spelling)
        # canonical names are always themselves
        for name in cf.THEMES:
            self.assertEqual(cf.resolve_theme(name), name)
        # junk / empty -> None (distinct from "use the default")
        for junk in ("", None, "playstation", "xyz"):
            self.assertIsNone(cf.resolve_theme(junk), junk)


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

    def test_preview_every_theme(self):
        for theme in cf.THEMES:
            r = self._run(["preview", "--theme", theme])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(theme, r.stdout)

    def test_statusline_survives_each_theme(self):
        p = json.dumps({"session_id": "t",
                        "context_window": {"used_percentage": 62}})
        for theme in cf.THEMES:
            r = self._run(["statusline", "--theme", theme], stdin=p)
            self.assertEqual(r.returncode, 0, f"{theme}: {r.stderr}")

    def test_resolve_theme_mode_prints_canon_and_exit_code(self):
        # the shell launcher keys off both stdout and the exit code.
        r = self._run(["resolve-theme", "SEGA"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "genesis")
        # unknown first word (a real prompt) -> nonzero, so the wrapper leaves it
        for token in ("fix", "playstation", ""):
            r = self._run(["resolve-theme", token])
            self.assertEqual(r.returncode, 1, f"{token!r} should be unresolved")
            self.assertEqual(r.stdout, "")

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
