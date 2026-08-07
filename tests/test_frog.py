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
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "claude_frog.py")
sys.path.insert(0, ROOT)

import claude_frog as cf  # noqa: E402

# Every CLI subprocess below runs with an isolated XDG_CACHE_HOME: the tap and
# hook modes write session state under the cache dir, and without this the
# test run pollutes the user's real ~/.cache/claude-frog (which it did).
_CACHE_TMP = tempfile.TemporaryDirectory(prefix="claude-frog-tests-")
ENV = {**os.environ, "XDG_CACHE_HOME": _CACHE_TMP.name}


def _fill_colour(bar):
    """The SGR code the gauge bar paints its LIT cells with."""
    lit = bar.index("▓")
    return bar[bar.rindex("\033[", 0, lit):lit]


class TestSprites(unittest.TestCase):
    def test_sprites_are_rectangular(self):
        for name, grid in (("FROG", cf.FROG), ("FROG_BACK", cf.FROG_BACK)):
            widths = {len(row) for row in grid}
            self.assertEqual(len(widths), 1, f"{name} rows are ragged: {widths}")

    def test_expected_dimensions(self):
        # motion/pane sizing assume these; a stray row would drift the floor.
        self.assertEqual((len(cf.FROG), len(cf.FROG[0])), (12, 19))

    def test_back_matches_front_dimensions(self):
        # he swaps to the back view mid-move; a size change would make him
        # jump off the floor (base_y is measured from the sprite's height).
        self.assertEqual((len(cf.FROG_BACK), len(cf.FROG_BACK[0])),
                         (len(cf.FROG), len(cf.FROG[0])))

    def test_back_view_has_no_face(self):
        # eyes (P), glint (W) and mouth cream (N/R) are front-only features.
        used = {ch for row in cf.FROG_BACK for ch in row}
        self.assertFalse(used & set("PWNR"), "the back view shows a face")

    def test_every_palette_key_resolves(self):
        used = {ch for grid in (cf.FROG, cf.FROG_BACK)
                for row in grid for ch in row}
        missing = used - set(cf.RGB)
        self.assertFalse(missing, f"sprite uses keys absent from RGB: {missing}")

    def test_shade_map_covers_palette(self):
        # preview mode maps every palette key to an ASCII glyph.
        missing = set(cf.RGB) - set(cf._SHADE)
        self.assertFalse(missing, f"_SHADE missing keys: {missing}")


class TestBlink(unittest.TestCase):
    def test_blink_overlays_are_in_bounds(self):
        base, overlay = cf.FROG, cf._FROG_BLINK
        h, w = len(base), len(base[0])
        for y, line in overlay.items():
            self.assertTrue(0 <= y < h, f"blink row {y} out of range")
            self.assertLessEqual(len(line), w, f"blink row {y} too wide")

    def test_blink_frames_render(self):
        px = cf.pose(cf.FROG, cf._FROG_BLINK, {"blink": True})
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
            px = cf.pose(cf.FROG, cf._FROG_BLINK, params, back=cf.FROG_BACK)
            cf.render_pixels(px)  # must not raise

    def test_render_height_halves_pixels(self):
        px = cf.pose(cf.FROG, cf._FROG_BLINK, {})
        self.assertEqual(len(cf.render_pixels(px)), math.ceil(len(px) / 2))

    def test_transforms_preserve_rectangularity(self):
        px = cf._colorize(cf.FROG)
        for grid in (cf.shear(px, 3.0), cf.flip_h(px), cf.flip_v(px),
                     cf.squash(px, 2), cf.hip_shift(cf._colorize(cf.FROG_BACK), 2),
                     cf.turn_squeeze(px, 0.3)):
            widths = {len(r) for r in grid}
            self.assertEqual(len(widths), 1)


class TestTwerk(unittest.TestCase):
    def test_back_param_swaps_the_sprite(self):
        front = cf.pose(cf.FROG, cf._FROG_BLINK, {}, back=cf.FROG_BACK)
        turned = cf.pose(cf.FROG, cf._FROG_BLINK, {"back": True}, back=cf.FROG_BACK)
        self.assertEqual(cf._colorize(cf.FROG_BACK), turned)
        self.assertNotEqual(front, turned)

    def test_caller_without_a_back_view_keeps_facing_front(self):
        # a caller that passes no back sprite must get a no-op `back` param.
        self.assertEqual(cf.pose(cf.FROG, cf._FROG_BLINK, {"back": True}),
                         cf.pose(cf.FROG, cf._FROG_BLINK, {}))

    def test_hip_shift_moves_only_the_rump(self):
        px = cf._colorize(cf.FROG_BACK)
        shifted = cf.hip_shift(px, 2)
        h = len(px)
        top, bot = int(h * cf.HIP_BAND[0]), int(h * cf.HIP_BAND[1])
        for y in range(h):
            if top <= y < bot:
                self.assertEqual(shifted[y][2:], px[y][:-2], f"row {y} didn't move")
            else:
                self.assertEqual(shifted[y], px[y], f"row {y} moved; head/feet must not")

    # the frame count the choreographer will actually run the move at — sample on
    # that grid, or a shake that aliases away to nothing at render time passes.
    N = dict((fn, n) for fn, n in cf.SPECIALS)[cf._m_twerk]

    def frames(self, g):
        return [cf._m_twerk(i / float(self.N), g) for i in range(self.N)]

    def test_twerk_pivots_around_and_shakes(self):
        for g in (0.0, 0.5, 1.0):
            frames = self.frames(g)
            # he pivots now, so he faces front during the turns and away for the
            # shake — not back-facing the whole move.
            self.assertTrue(any(f["back"] for f in frames)
                            and any(not f["back"] for f in frames),
                            f"he never actually turns around at g={g}")
            # every shake frame (the ones with a real shake) must be back-facing.
            shaking = [f for f in frames if abs(f["hips"]) > 1e-9]
            self.assertTrue(shaking and all(f["back"] for f in shaking),
                            f"he shakes while facing you at g={g}")
            # at Nyquist (beats == TWERK_SHAKE/2) every frame lands on a zero
            # crossing and he'd just stand there. Guard the whole range.
            self.assertTrue(any(abs(f["hips"]) >= 1.0 for f in frames),
                            f"the cheeks never actually move at g={g}")
            self.assertTrue(any(f["hips"] > 0 for f in frames)
                            and any(f["hips"] < 0 for f in frames),
                            f"he shakes only one way at g={g}")

    def test_twerk_pivot_goes_edge_on(self):
        # the illusion needs a near-edge-on frame in each pivot, where the sprite
        # swap hides. `turn` is the horizontal squeeze; ~0 is edge-on.
        turns = [f.get("turn", 1.0) for f in self.frames(0.5)]
        self.assertTrue(any(t < 0.2 for t in turns),
                        "the pivot never squeezes edge-on")

    def test_twerk_gets_bolder_with_goofiness(self):
        def peak(g):
            return max(abs(f["hips"]) for f in self.frames(g))
        self.assertGreater(peak(1.0), peak(0.0))


class TestThemes(unittest.TestCase):
    def test_every_theme_resolves_all_sprite_keys(self):
        # a theme missing a key would paint a transparent hole in the frog.
        keys = {ch for grid in (cf.FROG, cf.FROG_BACK)
                for row in grid for ch in row}
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

    def test_fade_off_pins_every_theme_to_its_base_palette(self):
        # `config fade off`: the colour channel goes quiet at every depth,
        # including the fully-pink mark and the party override's max value.
        for name, spec in cf.THEMES.items():
            for tok in (0, None, 60_000, cf.PINK_FULL_TOKENS, 10 ** 7):
                self.assertIs(cf.palette_for(tok, name, False), spec["base"],
                              f"{name} recoloured at {tok} tokens with fade off")

    def test_fade_off_leaves_the_motion_ramps_alone(self):
        # The whole point of the option: he still gets goofier and shakier as
        # the window fills, he just stops blushing. Motion reads tokens
        # directly and never consults the fade, so the two channels can't be
        # wired together by accident later.
        base = cf.THEMES[cf.DEFAULT_THEME]["base"]
        calm, cooked = 10_000, 300_000
        self.assertGreater(cf.goofiness(cooked, 0), cf.goofiness(calm, 0))
        self.assertGreater(cf.shake_px(cooked), cf.shake_px(calm))
        self.assertIs(cf.palette_for(calm, cf.DEFAULT_THEME, False), base)
        self.assertIs(cf.palette_for(cooked, cf.DEFAULT_THEME, False), base)

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
            "terraria": "terraria", "TERRARIA": "terraria",
            "Re-Logic": "terraria", "terra": "terraria", "32bit": "terraria",
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

    def test_zero_amplitude_never_jitters(self):
        self.assertTrue(all(cf._jitter(0.0) == 0 for _ in range(50)))

    def test_fractional_amplitude_actually_shakes(self):
        # THE regression: on a 200k window shake_px never exceeds ~0.88, and
        # int-truncating that muted the shake entirely — the "deep in it"
        # canary could never fire on the standard window.
        import random
        random.seed(7)
        sk = cf.shake_px(190_000)
        self.assertTrue(0.0 < sk < 1.0, f"expected a sub-pixel amplitude, got {sk}")
        self.assertTrue(any(cf._jitter(sk) != 0 for _ in range(300)),
                        "a sub-pixel amplitude never produced any jitter")

    def test_jitter_is_bounded_by_the_amplitude_ceiling(self):
        import random
        random.seed(8)
        for amp in (0.3, 0.9, 1.5, float(cf.SHAKE_MAX_PX)):
            for _ in range(200):
                self.assertLessEqual(abs(cf._jitter(amp)), math.ceil(amp))


class TestBigJump(unittest.TestCase):
    def test_direction_is_fixed_for_the_whole_move(self):
        # picking a fresh random direction every frame made the leap teleport
        # side to side mid-air instead of travelling one way.
        for direction in (1, -1):
            fn = cf._m_bigjump(direction)
            dxs = [fn(i / 16.0, 1.0)["dx"] for i in range(16)]
            self.assertTrue(all(dx * direction >= 0 for dx in dxs),
                            f"dx changed sign against direction={direction}: {dxs}")
            self.assertTrue(any(dx != 0 for dx in dxs), "he never travelled")

    def test_both_directions_are_in_the_specials_pool(self):
        signs = {1 if fn(1.0, 1.0)["dx"] > 0 else -1
                 for fn, _n in cf.SPECIALS
                 if fn.__qualname__.startswith("_m_bigjump")}
        self.assertEqual(signs, {1, -1})


class TestSessionState(unittest.TestCase):
    def test_uuid_session_ids_pass_through(self):
        u = "70dfd0a1-58f6-4d2f-b121-c11b1b1766c8"
        self.assertEqual(cf._safe_session(u), u)

    def test_hostile_session_ids_are_tamed(self):
        self.assertEqual(cf._safe_session("../../etc/passwd"), "etcpasswd")
        self.assertEqual(cf._safe_session(""), "default")
        self.assertNotIn(" ", cf._safe_session("a b; rm -rf ~"))

    def test_paths_stay_inside_cache_dir(self):
        for s in ("../evil", "a/b/c", "x y", "ok-session_1"):
            for p in cf._paths(s):
                self.assertEqual(os.path.dirname(p), cf.CACHE_DIR, p)


class TestPruneStale(unittest.TestCase):
    """_prune_stale must sweep dead sessions without touching live ones."""

    def setUp(self):
        import shutil
        self._old = cf.CACHE_DIR
        cf.CACHE_DIR = tempfile.mkdtemp(prefix="frog-prune-")
        self.addCleanup(setattr, cf, "CACHE_DIR", self._old)
        self.addCleanup(shutil.rmtree, cf.CACHE_DIR, True)

    def _touch(self, name, age_secs):
        p = os.path.join(cf.CACHE_DIR, name)
        with open(p, "w") as f:
            f.write("{}")
        old = time.time() - age_secs
        os.utime(p, (old, old))
        return p

    def test_paneless_state_is_aged_out(self):
        # a hard-killed (or non-tmux) session never gets a .pane file, so the
        # pane sweep can't see it — it used to sit in CACHE_DIR forever.
        week_plus = cf.STALE_STATE_SECS + 3600
        dead = [self._touch("dead.think", week_plus),
                self._touch("dead.ctx", week_plus),
                self._touch("dead.ctx.tmp", week_plus)]
        fresh = self._touch("live.think", 60)
        cf._prune_stale()
        for p in dead:
            self.assertFalse(os.path.exists(p), f"stale file survived: {p}")
        self.assertTrue(os.path.exists(fresh), "fresh state was swept")

    def test_dead_pane_session_is_cleaned_regardless_of_age(self):
        pane = os.path.join(cf.CACHE_DIR, "gone.pane")
        with open(pane, "w") as f:
            f.write("%99999")           # a pane id tmux won't know
        think = self._touch("gone.think", 60)
        cf._prune_stale()
        self.assertFalse(os.path.exists(pane))
        self.assertFalse(os.path.exists(think))

    def test_one_bad_file_does_not_stop_the_sweep(self):
        bad = os.path.join(cf.CACHE_DIR, "unreadable.pane")
        os.mkdir(bad)                   # open() on a directory raises
        stale = self._touch("dead.ctx", cf.STALE_STATE_SECS + 3600)
        cf._prune_stale()               # must not raise
        self.assertFalse(os.path.exists(stale), "sweep stopped at the bad file")


class TestStatusBarFrog(unittest.TestCase):
    """The one-line status-bar frog: opt-in, single line, never fatal."""

    def setUp(self):
        import shutil
        self.home = tempfile.mkdtemp(prefix="frog-slhome-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = {**ENV, "XDG_CONFIG_HOME": self.home}
        for s in cf.SETTINGS.values():
            self.env.pop(s["env"], None)
        self.payload = json.dumps({
            "session_id": "sl-test",
            "context_window": {"used_percentage": 39,
                               "context_window_size": 200000}})

    def _run(self, mode="statusline", stdin=None, env=None):
        return subprocess.run([sys.executable, SCRIPT, mode],
                              input=self.payload if stdin is None else stdin,
                              capture_output=True, text=True, timeout=15,
                              env=env or self.env)

    def _on(self):
        subprocess.run([sys.executable, SCRIPT, "config", "statusline", "frog"],
                       capture_output=True, text=True, env=self.env, timeout=15)

    # -- opt-in ------------------------------------------------------------ #

    def test_silent_until_you_ask_for_it(self):
        # An upgrade must never start drawing in somebody's status bar.
        for mode in ("tap", "statusline"):
            r = self._run(mode)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "", f"{mode} drew without being asked")

    def test_draws_once_enabled_under_either_mode_name(self):
        self._on()
        for mode in ("tap", "statusline"):
            r = self._run(mode)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.strip(), f"{mode} drew nothing")

    def test_the_gauge_is_still_fed_when_the_bar_is_silent(self):
        # The pane's shake and pink fade depend on this even with nothing drawn.
        cache = os.path.join(ENV["XDG_CACHE_HOME"], "claude-frog", "sl-test.ctx")
        self._run("tap")
        self.assertTrue(os.path.exists(cache), "tap stopped publishing the gauge")

    # -- shape ------------------------------------------------------------- #

    def test_it_really_is_one_line(self):
        # The whole point of a 2px sprite: one character row, so he costs
        # nothing vertically in a status bar.
        self._on()
        out = self._run().stdout
        self.assertEqual(out.count("\n"), 0, "the status bar frog wrapped a line")

    def test_the_micro_sprite_is_two_pixels_tall(self):
        self.assertEqual(len(cf.MICRO), 2)
        self.assertEqual(len(cf.render_pixels(cf._colorize(cf.MICRO))), 1)

    def test_it_reports_tokens_and_percent(self):
        self._on()
        out = self._run().stdout
        self.assertIn("78k", out)
        self.assertIn("39%", out)

    def test_percent_is_omitted_when_the_window_size_is_unknown(self):
        self._on()
        out = self._run(stdin=json.dumps(
            {"session_id": "t", "context_window": {"total_input_tokens": 5000}})).stdout
        self.assertIn("5.0k", out)
        self.assertNotIn("%", out)

    def test_the_bar_tracks_window_fill_not_the_mood_ramp(self):
        # Length = how full the window is; colour = how cooked Claude is. On a
        # 1M window, 200k tokens is a fifth full even though he's fully pink.
        wide = cf._gauge_bar(200_000, 1_000_000, "snes")
        narrow = cf._gauge_bar(200_000, 200_000, "snes")
        self.assertEqual(wide.count("▓"), 2, "bar ignored the real window size")
        self.assertEqual(narrow.count("▓"), 8)

    def test_fade_off_keeps_the_bar_length_but_drops_the_colour(self):
        # With the fade off the bar carries the fill on length alone — which is
        # the readout it was always primarily making. Colour stops moving.
        shallow = cf._gauge_bar(20_000, 200_000, "snes", False)
        deep = cf._gauge_bar(180_000, 200_000, "snes", False)
        self.assertEqual((shallow.count("▓"), deep.count("▓")), (1, 7),
                         "the bar stopped tracking window fill")
        self.assertEqual(_fill_colour(deep), _fill_colour(shallow))
        # …and with it on, that same depth really does recolour, so the
        # assertion above is measuring the setting and not a broken bar.
        self.assertNotEqual(_fill_colour(cf._gauge_bar(180_000, 200_000, "snes")),
                            _fill_colour(deep))

    def test_every_theme_renders(self):
        for theme in cf.THEMES:
            line = cf._statusline_text("t", 78_000, 200_000, theme)
            self.assertTrue(line.strip())
            self.assertNotIn("\n", line)

    # -- never fatal ------------------------------------------------------- #

    def test_survives_junk_with_the_bar_enabled(self):
        self._on()
        for p in ("", "not json", "{}", "[]", '{"context_window": "nope"}'):
            r = self._run(stdin=p)
            self.assertEqual(r.returncode, 0, f"statusline <- {p!r}: {r.stderr}")

    def test_a_broken_render_does_not_break_the_prompt(self):
        # The bar is drawn inside the tap path, which must always exit 0.
        # Enable via the env var, not the config file: this call runs in-process
        # and would otherwise read the developer's real settings and skip the
        # render entirely, passing without testing anything.
        import io
        saved_micro, saved_stdin = cf.MICRO, sys.stdin
        saved_stdout = sys.stdout           # keep the bar out of the test log
        prev = os.environ.get("CLAUDE_FROG_STATUSLINE")
        os.environ["CLAUDE_FROG_STATUSLINE"] = "frog"
        try:
            self.assertEqual(cf._setting("statusline")[0], "frog",
                             "guard: the render path wasn't actually enabled")
            cf.MICRO = "not a sprite"
            sys.stdin, sys.stdout = io.StringIO(self.payload), io.StringIO()
            with self.assertRaises(SystemExit) as e:
                cf.mode_tap()
            self.assertEqual(e.exception.code, 0)
        finally:
            cf.MICRO, sys.stdin = saved_micro, saved_stdin
            sys.stdout = saved_stdout
            if prev is None:
                os.environ.pop("CLAUDE_FROG_STATUSLINE", None)
            else:
                os.environ["CLAUDE_FROG_STATUSLINE"] = prev


class TestSettingsResolution(unittest.TestCase):
    """Settings resolve flag > env > config file > default, and say which.

    The bug behind all of this: every knob was an env var, so the only durable
    way to change one was editing a shell rc — and an `export CLAUDE_FROG_THEME=`
    left in there silently outranked everything with no way to see it.
    """

    def setUp(self):
        import shutil
        self._old = cf.CONFIG_DIR
        cf.CONFIG_DIR = tempfile.mkdtemp(prefix="frog-cfg-")
        self.addCleanup(setattr, cf, "CONFIG_DIR", self._old)
        self.addCleanup(shutil.rmtree, cf.CONFIG_DIR, True)
        self._saved = {s["env"]: os.environ.get(s["env"])
                       for s in cf.SETTINGS.values()}
        self.addCleanup(self._restore)
        for name in self._saved:
            os.environ.pop(name, None)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_when_nothing_is_set(self):
        self.assertEqual(cf._setting("theme"), (cf.DEFAULT_THEME, "default"))
        self.assertEqual(cf._setting("layout"), (cf.DEFAULT_LAYOUT, "default"))
        self.assertEqual(cf._setting("flora"), (True, "default"))

    def test_config_file_beats_default(self):
        cf._write_config({"theme": "gba"})
        self.assertEqual(cf._setting("theme"), ("gba", "config"))

    def test_env_beats_config_file(self):
        cf._write_config({"theme": "gba"})
        os.environ["CLAUDE_FROG_THEME"] = "terraria"
        self.assertEqual(cf._setting("theme"), ("terraria", "env"))

    def test_flag_beats_everything(self):
        cf._write_config({"theme": "gba"})
        os.environ["CLAUDE_FROG_THEME"] = "terraria"
        self.assertEqual(cf._setting("theme", "genesis"), ("genesis", "flag"))

    def test_friendly_spellings_resolve_from_any_layer(self):
        os.environ["CLAUDE_FROG_THEME"] = "Mega Drive"
        self.assertEqual(cf._setting("theme")[0], "genesis")
        os.environ.pop("CLAUDE_FROG_THEME")
        cf._write_config({"theme": "Game Boy"})
        self.assertEqual(cf._setting("theme")[0], "gba")

    def test_junk_falls_through_instead_of_winning(self):
        # A typo in a higher layer must not leave the frog themeless — it should
        # defer to the next layer down.
        cf._write_config({"theme": "gba"})
        os.environ["CLAUDE_FROG_THEME"] = "playstation"
        self.assertEqual(cf._setting("theme"), ("gba", "config"))
        os.environ["CLAUDE_FROG_LAYOUT"] = "sideways"
        self.assertEqual(cf._setting("layout"), (cf.DEFAULT_LAYOUT, "default"))

    def test_flora_off_is_honoured_not_treated_as_junk(self):
        # `False` is a real value, and must not be mistaken for "unset".
        cf._write_config({"flora": "off"})
        self.assertEqual(cf._setting("flora"), (False, "config"))
        os.environ["CLAUDE_FROG_FLORA"] = "0"
        self.assertEqual(cf._setting("flora"), (False, "env"))

    def test_fade_defaults_on_and_resolves_through_every_layer(self):
        # On by default: an upgrade must not silently stop an existing frog
        # from blushing. Then off from the file, and off for one session from
        # the env — same ladder as every other knob.
        self.assertEqual(cf._setting("fade"), (True, "default"))
        cf._write_config({"fade": "off"})
        self.assertEqual(cf._setting("fade"), (False, "config"))
        os.environ["CLAUDE_FROG_FADE"] = "on"
        self.assertEqual(cf._setting("fade"), (True, "env"))

    def test_unreadable_config_does_not_break_resolution(self):
        with open(cf._config_path(), "w") as f:
            f.write("{ not json")
        self.assertEqual(cf._setting("theme"), (cf.DEFAULT_THEME, "default"))

    def test_parse_applies_settings_to_opts(self):
        cf._write_config({"theme": "gba", "layout": "right"})
        _mode, opts = cf._parse(["dance"])
        self.assertEqual((opts["theme"], opts["layout"]), ("gba", "right"))


class TestConfigCli(unittest.TestCase):
    """`config` is the surface that replaces editing a shell rc."""

    def setUp(self):
        import shutil
        self.home = tempfile.mkdtemp(prefix="frog-cfghome-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = {**ENV, "XDG_CONFIG_HOME": self.home}
        self.env.pop("CLAUDE_FROG_THEME", None)

    def _run(self, args, env=None):
        return subprocess.run([sys.executable, SCRIPT, *args],
                              capture_output=True, text=True, timeout=15,
                              env=env or self.env)

    def _stored(self):
        with open(os.path.join(self.home, "claude-frog", "config.json")) as f:
            return json.load(f)

    def test_set_and_show(self):
        self.assertEqual(self._run(["config", "theme", "gba"]).returncode, 0)
        self.assertEqual(self._stored()["theme"], "gba")
        out = self._run(["config"]).stdout
        self.assertIn("gba", out)

    def test_unset_returns_to_default(self):
        self._run(["config", "theme", "gba"])
        self._run(["config", "unset", "theme"])
        self.assertNotIn("theme", self._stored())

    def test_rejects_a_bad_value_without_writing(self):
        r = self._run(["config", "theme", "playstation"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("isn't a valid theme", r.stderr)

    def test_rejects_an_unknown_key(self):
        self.assertEqual(self._run(["config", "sparkles", "on"]).returncode, 2)

    def test_reports_the_source_of_each_value(self):
        # The whole point of the source column: an env var pinning a setting is
        # visible instead of mysterious.
        env = {**self.env, "CLAUDE_FROG_THEME": "terraria"}
        out = self._run(["config"], env=env).stdout
        self.assertIn("CLAUDE_FROG_THEME", out)

    def test_warns_when_an_env_var_shadows_the_write(self):
        env = {**self.env, "CLAUDE_FROG_THEME": "terraria"}
        r = self._run(["config", "theme", "snes"], env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._stored()["theme"], "snes", "the write didn't happen")
        self.assertIn("pins theme", r.stdout, "the shadowing wasn't reported")

    def test_config_path_is_printable_for_shell_callers(self):
        r = self._run(["config-path"])
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip().endswith("config.json"))


class TestKeybindInstall(unittest.TestCase):
    """The prefix+F binding is installed for real, not left as a paste-this."""

    def setUp(self):
        import shutil
        self.dir = tempfile.mkdtemp(prefix="frog-tmuxconf-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.conf = os.path.join(self.dir, "tmux.conf")
        self.original = "# mine\nset -g mouse on\nbind r source-file ~/.tmux.conf\n"
        self._write(self.original)

    def _write(self, text):
        with open(self.conf, "w") as f:
            f.write(text)

    def _read(self):
        with open(self.conf) as f:
            return f.read()

    def _binds(self):
        return [ln for ln in self._read().splitlines() if "claude_frog.py" in ln]

    def test_installs_once_and_is_idempotent(self):
        self.assertTrue(cf.install_keybind(self.conf)[0])
        self.assertEqual(len(self._binds()), 1)
        self.assertFalse(cf.install_keybind(self.conf)[0], "re-run rewrote the file")
        self.assertEqual(len(self._binds()), 1)

    def test_adopts_a_hand_written_binding_instead_of_duplicating(self):
        # Plenty of people pasted the README snippet in themselves; appending a
        # second `bind F` would leave two bindings fighting over one key.
        self._write(self.original +
                    'bind F run-shell "python3 /old/claude-frog/claude_frog.py toggle"\n')
        self.assertTrue(cf._keybind_installed(self.conf))
        cf.install_keybind(self.conf)
        self.assertEqual(len(self._binds()), 1, "left a duplicate binding")
        self.assertIn(os.path.abspath(cf.__file__), self._binds()[0])

    def test_uninstall_restores_the_original_file(self):
        cf.install_keybind(self.conf)
        self.assertTrue(cf.uninstall_keybind(self.conf)[0])
        self.assertEqual(self._read(), self.original,
                         "uninstall did not leave tmux.conf as it found it")

    def test_uninstall_is_a_noop_when_absent(self):
        self.assertFalse(cf.uninstall_keybind(self.conf)[0])
        self.assertEqual(self._read(), self.original)

    def test_other_bindings_are_left_alone(self):
        cf.install_keybind(self.conf)
        self.assertIn("bind r source-file", self._read())
        self.assertIn("set -g mouse on", self._read())

    def test_a_missing_tmux_conf_is_created(self):
        fresh = os.path.join(self.dir, "nested", "tmux.conf")
        self.assertTrue(cf.install_keybind(fresh)[0])
        self.assertTrue(os.path.exists(fresh))


class _FakeTmux(object):
    """A tmux server just real enough to exercise window ownership.

    Stands in for cf._tmux, so the pane bookkeeping can be tested without an
    actual tmux server (and without splitting panes into the developer's own
    terminal, which is how this bug got found in the first place).
    """

    class _R(object):
        def __init__(self, stdout="", rc=0):
            self.stdout, self.returncode, self.stderr = stdout, rc, ""

    def __init__(self):
        self.panes = {}          # pane_id -> window_id
        self.opts = {}           # pane_id -> {option: value}
        self.cmds = {}           # pane_id -> pane_start_command
        self.spawned = []        # the commands split-window was asked to run
        self._next = 100

    def add_pane(self, win, cmd=""):
        pid = "%%%d" % self._next
        self._next += 1
        self.panes[pid] = win
        self.cmds[pid] = cmd
        return pid

    def _arg(self, args, flag):
        return args[args.index(flag) + 1] if flag in args else None

    def __call__(self, *args):
        args = list(args)
        cmd = args[0] if args else ""
        if cmd == "list-panes":
            fmt = self._arg(args, "-F") or "#{pane_id}"
            target = self._arg(args, "-t")   # None or "-a" => every pane
            lines = []
            for pid, win in self.panes.items():
                if target and target != win:
                    continue
                line = fmt.replace("#{pane_id}", pid)
                line = line.replace(
                    "#{@claude_frog}", self.opts.get(pid, {}).get("@claude_frog", ""))
                line = line.replace("#{pane_start_command}", self.cmds.get(pid, ""))
                lines.append(line)
            return self._R("\n".join(lines))
        if cmd == "display-message":
            target = self._arg(args, "-t")
            return self._R(self.panes.get(target, "") + "\n")
        if cmd == "split-window":
            target = self._arg(args, "-t")
            pid = self.add_pane(self.panes.get(target, target))
            self.spawned.append(args[-1])
            return self._R(pid + "\n")
        if cmd == "kill-pane":
            self.panes.pop(self._arg(args, "-t"), None)
            return self._R()
        if cmd == "set-option":
            self.opts.setdefault(self._arg(args, "-t"), {})[args[-2]] = args[-1]
            return self._R()
        return self._R()


class _WindowCase(unittest.TestCase):
    """Shared fixture: a fake tmux server with one Claude pane in window @1."""

    def setUp(self):
        import shutil
        self._old_cache, self._old_tmux = cf.CACHE_DIR, cf._tmux
        cf.CACHE_DIR = tempfile.mkdtemp(prefix="frog-win-")
        self.tmux = _FakeTmux()
        cf._tmux = self.tmux
        self.addCleanup(setattr, cf, "CACHE_DIR", self._old_cache)
        self.addCleanup(setattr, cf, "_tmux", self._old_tmux)
        self.addCleanup(shutil.rmtree, cf.CACHE_DIR, True)
        self.claude = self.tmux.add_pane("@1")
        self._saved = {k: os.environ.get(k) for k in ("TMUX", "TMUX_PANE")}
        self.addCleanup(self._restore_env)
        os.environ["TMUX"] = "/tmp/fake-tmux,1,0"
        os.environ["TMUX_PANE"] = self.claude

    def _restore_env(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _frogs(self, win="@1"):
        """Frog panes tmux is currently showing in `win`."""
        return [p for p, w in self.tmux.panes.items()
                if w == win and self.tmux.opts.get(p, {}).get("@claude_frog")]


class TestWindowSingleton(_WindowCase):
    """At most ONE frog pane per tmux window, however many sessions share it.

    The bug this locks down: pane life used to be keyed on session_id alone, so
    every extra Claude session started inside a window split *another* frog into
    it — a headless `claude -p` fired off by a subagent or a skill, a nested
    `claude`, a `/clear` that mints a fresh session id. The panes piled up.
    """

    # -- the invariant ----------------------------------------------------- #

    def test_extra_sessions_join_the_window_instead_of_spawning(self):
        cf._win_claim("session-a")
        self.assertEqual(len(self._frogs()), 1, "first session got no frog")
        for sid in ("session-b", "session-c", "session-d"):
            cf._win_claim(sid)
        self.assertEqual(len(self._frogs()), 1,
                         "a fan-out of sessions spawned extra frogs")
        self.assertEqual(len(cf._read_win("@1")["sessions"]), 4,
                         "the extra sessions were not counted as claimants")

    def test_concurrent_claims_still_spawn_only_one(self):
        # Two SessionStart hooks racing for an empty window is the case the
        # window lock exists for: without it both see "no pane" and both split.
        import threading
        threads = [threading.Thread(target=cf._win_claim, args=("s%d" % i,))
                   for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self._frogs()), 1,
                         "concurrent claims raced past the lock")

    def test_each_window_gets_its_own_frog(self):
        cf._win_claim("session-a")
        os.environ["TMUX_PANE"] = self.tmux.add_pane("@2")
        cf._win_claim("session-b")
        self.assertEqual(len(self._frogs("@1")), 1)
        self.assertEqual(len(self._frogs("@2")), 1)

    # -- teardown ---------------------------------------------------------- #

    def test_last_claimant_out_kills_the_frog(self):
        cf._win_claim("session-a")
        cf._win_claim("session-b")
        frog = self._frogs()[0]
        cf._win_release("session-a")
        self.assertIn(frog, self.tmux.panes,
                      "one session ending killed a frog another still needs")
        cf._win_release("session-b")
        self.assertNotIn(frog, self.tmux.panes, "the last session out left an orphan")
        self.assertFalse(os.path.exists(cf._win_path("@1")))

    def test_a_dead_claude_pane_releases_its_claim(self):
        # Claude hard-killed (crashed terminal, `kill -9`): no SessionEnd ever
        # fires, so liveness has to come from the pane it was running in.
        cf._win_claim("session-a")
        frog = self._frogs()[0]
        del self.tmux.panes[self.claude]
        cf._prune_stale()
        self.assertNotIn(frog, self.tmux.panes,
                         "a crashed session held its frog forever")

    # -- who the frog is showing ------------------------------------------- #

    def test_active_follows_whoever_just_worked(self):
        cf._win_claim("session-a")
        cf._win_claim("session-b")
        self.assertEqual(cf._read_win("@1")["active"], "session-b")
        cf._win_touch("session-a")
        self.assertEqual(cf._read_win("@1")["active"], "session-a",
                         "the frog did not follow the session that just worked")

    def test_release_hands_the_frog_to_a_survivor(self):
        cf._win_claim("session-a")
        cf._win_claim("session-b")
        cf._win_release("session-b")
        self.assertEqual(cf._read_win("@1")["active"], "session-a")

    # -- end to end through the real hook dispatch ------------------------- #

    def test_hook_events_keep_one_frog(self):
        import io

        def hook(event, session):
            saved = sys.stdin
            sys.stdin = io.StringIO(json.dumps(
                {"hook_event_name": event, "session_id": session}))
            try:
                with self.assertRaises(SystemExit):
                    cf.mode_hook({})
            finally:
                sys.stdin = saved

        hook("SessionStart", "sess-a")
        hook("SessionStart", "sess-b")
        self.assertEqual(len(self._frogs()), 1,
                         "two SessionStarts in one window spawned two frogs")
        hook("SessionEnd", "sess-a")
        self.assertEqual(len(self._frogs()), 1, "a live session lost its frog")
        hook("SessionEnd", "sess-b")
        self.assertEqual(len(self._frogs()), 0, "the frog outlived its last session")

    # -- the daemon is told which window it serves ------------------------- #

    def test_spawned_daemon_is_window_scoped(self):
        cf._win_claim("session-a")
        self.assertIn("--window @1", self.tmux.spawned[0])
        self.assertNotIn("--session", self.tmux.spawned[0])

    # -- upgrading from the per-session era -------------------------------- #

    _LEGACY = ('exec python3 /path/claude_frog.py dance '
               '--session abc --theme snes --since 0')

    def test_a_legacy_frog_is_reaped_rather_than_stacked_on(self):
        # Frogs spawned before window scoping run `dance --session` and carry no
        # @claude_frog tag, so the window bookkeeping is blind to them. Upgrading
        # mid-session must not leave one standing next to the new frog — that's
        # two frogs in a window, exactly when someone first tests the fix.
        legacy = self.tmux.add_pane("@1", self._LEGACY)
        cf._win_claim("session-a")
        self.assertNotIn(legacy, self.tmux.panes, "the old frog survived the upgrade")
        self.assertEqual(len(self._frogs()), 1)

    def test_reaping_leaves_other_windows_and_ordinary_panes_alone(self):
        legacy_here = self.tmux.add_pane("@1", self._LEGACY)
        legacy_there = self.tmux.add_pane("@2", self._LEGACY)
        shell = self.tmux.add_pane("@1", "zsh")
        cf._win_claim("session-a")
        self.assertNotIn(legacy_here, self.tmux.panes)
        self.assertIn(legacy_there, self.tmux.panes, "reaped another window's frog")
        self.assertIn(shell, self.tmux.panes, "reaped a plain shell pane")

    def test_toggle_hides_a_legacy_frog_rather_than_replacing_it(self):
        # prefix+F on an un-upgraded window means "hide the frog I can see".
        # Reaping it and spawning a replacement would make the key look dead.
        legacy = self.tmux.add_pane("@1", self._LEGACY)
        with self.assertRaises(SystemExit):
            cf.mode_toggle({})
        self.assertNotIn(legacy, self.tmux.panes, "toggle didn't hide the old frog")
        self.assertEqual(len(self._frogs()), 0, "toggle spawned a frog while hiding")
        with self.assertRaises(SystemExit):       # and F again brings him back
            cf.mode_toggle({})
        self.assertEqual(len(self._frogs()), 1)

    def test_window_ids_are_validated_before_reaching_a_command_line(self):
        for bad in ("", None, "@", "1", "@1; rm -rf ~", "@1 x", "../@1"):
            self.assertFalse(cf._valid_win(bad), bad)
        self.assertTrue(cf._valid_win("@1"))
        self.assertTrue(cf._valid_win("@1234"))


class TestToggleIsWindowScoped(_WindowCase):
    """prefix+F must mean the same thing in every window.

    It used to toggle whichever session in the whole cache had the newest .pane
    file, which in a multi-window setup is somebody else's frog.
    """

    def _toggle(self):
        with self.assertRaises(SystemExit):
            cf.mode_toggle({})

    def test_toggle_hides_then_summons_in_this_window(self):
        cf._win_claim("session-a")
        self._toggle()
        self.assertEqual(len(self._frogs()), 0, "toggle did not hide the frog")
        self._toggle()
        self.assertEqual(len(self._frogs()), 1, "toggle did not summon him back")

    def test_toggle_leaves_other_windows_alone(self):
        cf._win_claim("session-a")
        other = self.tmux.add_pane("@2")
        os.environ["TMUX_PANE"] = other
        cf._win_claim("session-b")
        self._toggle()                       # we are in @2
        self.assertEqual(len(self._frogs("@2")), 0)
        self.assertEqual(len(self._frogs("@1")), 1, "toggle reached into @1")

    def test_summoning_after_a_hand_killed_pane_takes_one_press(self):
        cf._win_claim("session-a")
        del self.tmux.panes[self._frogs()[0]]   # `tmux kill-pane` by hand
        self._toggle()
        self.assertEqual(len(self._frogs()), 1, "summoning back took two presses")


class TestCliModesExitZero(unittest.TestCase):
    """The statusline / tap / hook / preview paths must never break a prompt."""

    def _run(self, args, stdin=""):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], input=stdin,
            capture_output=True, text=True, timeout=15, env=ENV,
        )

    def test_preview(self):
        r = self._run(["preview"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_preview_every_theme(self):
        for theme in cf.THEMES:
            r = self._run(["preview", "--theme", theme])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(theme, r.stdout)

    def test_deprecated_statusline_alias_taps_silently(self):
        # `statusline` (the retired in-bar frog) must behave exactly like tap:
        # exit 0, draw nothing, and still publish the token gauge.
        p = json.dumps({"session_id": "t",
                        "context_window": {"used_percentage": 62}})
        for mode in ("statusline", "tap"):
            r = self._run([mode], stdin=p)
            self.assertEqual(r.returncode, 0, f"{mode}: {r.stderr}")
            self.assertEqual(r.stdout, "", f"{mode} drew in the status bar")

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


class TestAgentAdapter(unittest.TestCase):
    """The adapter seam: everything agent-specific answers through ADAPTER."""

    def test_registry_holds_the_default_and_detection_lands_on_one(self):
        self.assertIn(cf.DEFAULT_AGENT, cf.ADAPTERS)
        self.assertIsInstance(cf.detect_agent(), cf.AgentAdapter)
        self.assertIsInstance(cf.ADAPTER, cf.AgentAdapter)

    def test_hook_events_reexport_is_the_adapters_list(self):
        # The historical module-level name must stay identical to the Claude
        # Code adapter's list — installer, uninstaller, and doctor all key on it.
        self.assertEqual(cf.FROG_HOOK_EVENTS, cf.ClaudeCodeAdapter.HOOK_EVENTS)

    def test_every_wired_event_maps_to_a_canonical_lifecycle_event(self):
        a = cf.ClaudeCodeAdapter()
        canon = {"session-start", "prompt", "stop", "session-end"}
        for ev in a.HOOK_EVENTS:
            self.assertIn(a.canonical_event(ev), canon,
                          f"{ev} maps to nothing mode_hook dispatches on")
        # All four moments are reachable — a frog that can never end a session
        # (or never start one) leaks panes.
        self.assertEqual({a.canonical_event(ev) for ev in a.HOOK_EVENTS}, canon)

    def test_cleanup_is_a_session_end_synonym_and_junk_maps_to_none(self):
        a = cf.ClaudeCodeAdapter()
        self.assertEqual(a.canonical_event("Cleanup"), "session-end")
        self.assertIsNone(a.canonical_event("SomeFutureEvent"))
        self.assertIsNone(a.canonical_event(""))

    def test_session_id_reads_both_spellings_and_never_raises_on_junk(self):
        a = cf.ClaudeCodeAdapter()
        self.assertEqual(a.session_id({"session_id": "x"}), "x")
        self.assertEqual(a.session_id({"sessionId": "y"}), "y")
        self.assertIsNone(a.session_id({}))

    def test_settings_path_honors_override_then_config_dir_env(self):
        a = cf.ClaudeCodeAdapter()
        self.assertEqual(a.settings_path("/tmp/x.json"), "/tmp/x.json")
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/frog-conf"
        try:
            self.assertEqual(a.settings_path(),
                             os.path.join("/tmp/frog-conf", "settings.json"))
        finally:
            if old is None:
                del os.environ["CLAUDE_CONFIG_DIR"]
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_install_then_uninstall_wiring_round_trips_parsed_settings(self):
        a = cf.ClaudeCodeAdapter()
        data = {"model": "opus"}
        changed, _notes = a.install_wiring(
            data, tap_cmd=cf._frog_cmd("tap"), hook_cmd=cf._frog_cmd("hook"),
            is_ours=cf._is_frog_cmd)
        self.assertTrue(changed)
        self.assertTrue(cf._is_frog_cmd(data["statusLine"]["command"]))
        removed = a.uninstall_wiring(data, is_ours=cf._is_frog_cmd)
        self.assertTrue(removed)
        self.assertEqual(data, {"model": "opus"})

    def test_install_wiring_raises_on_a_hooks_shape_it_cannot_merge_into(self):
        a = cf.ClaudeCodeAdapter()
        with self.assertRaises(ValueError):
            a.install_wiring({"hooks": "nope"}, tap_cmd="t", hook_cmd="h",
                             is_ours=cf._is_frog_cmd)


class TestInstallSettings(unittest.TestCase):
    """`install-settings` must merge into settings.json without clobbering."""

    def _run(self, path, extra=()):
        return subprocess.run(
            [sys.executable, SCRIPT, "install-settings", "--settings", path, *extra],
            capture_output=True, text=True, timeout=15, env=ENV,
        )

    def _tmp(self, text=None):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = os.path.join(d, "settings.json")
        if text is not None:
            with open(p, "w") as f:
                f.write(text)
        return p

    @staticmethod
    def _read(p):
        with open(p) as f:
            return f.read()

    def _load(self, p):
        return json.loads(self._read(p))

    def test_fresh_adds_tap_statusline_and_all_hooks(self):
        p = self._tmp()
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self._load(p)
        self.assertIn("claude_frog.py", data["statusLine"]["command"])
        # the statusLine must be the silent tap, never the retired in-bar frog
        self.assertTrue(data["statusLine"]["command"].endswith(" tap"),
                        data["statusLine"]["command"])
        for ev in cf.FROG_HOOK_EVENTS:
            self.assertTrue(cf._event_has_frog_hook(data["hooks"][ev]), ev)

    def test_migrates_deprecated_statusline_mode_to_tap(self):
        p = self._tmp(json.dumps({"statusLine": {
            "type": "command",
            "command": "python3 /old/claude_frog.py statusline"}}))
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        cmd = self._load(p)["statusLine"]["command"]
        self.assertTrue(cmd.endswith(" tap"), cmd)

    def test_idempotent(self):
        p = self._tmp()
        self._run(p)
        first = self._read(p)
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._read(p), first, "second run should be a no-op")

    def test_preserves_config_and_does_not_clobber_statusline(self):
        p = self._tmp(json.dumps({
            "model": "claude-opus-4-8",
            "statusLine": {"type": "command", "command": "/usr/local/bin/my-bar"},
            "hooks": {"UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "/opt/my-hook"}]}]},
        }))
        r = self._run(p)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self._load(p)
        self.assertEqual(data["model"], "claude-opus-4-8")            # kept
        self.assertEqual(data["statusLine"]["command"], "/usr/local/bin/my-bar")  # not clobbered
        cmds = [h["command"] for g in data["hooks"]["UserPromptSubmit"]
                for h in g["hooks"]]
        self.assertIn("/opt/my-hook", cmds)                          # existing hook kept
        self.assertTrue(any("claude_frog.py" in c for c in cmds))    # frog added
        self.assertTrue(os.path.exists(p + ".bak"))                  # backed up

    def test_refuses_invalid_json(self):
        p = self._tmp("{ not json ")
        r = self._run(p)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._read(p), "{ not json ")               # untouched


class TestUninstallSettings(unittest.TestCase):
    """`uninstall-settings` must remove ONLY the frog, reversibly."""

    def _tmp(self, text=None):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = os.path.join(d, "settings.json")
        if text is not None:
            with open(p, "w") as f:
                f.write(text)
        return p

    def _run(self, mode, path, extra=()):
        return subprocess.run(
            [sys.executable, SCRIPT, mode, "--settings", path, *extra],
            capture_output=True, text=True, timeout=15, env=ENV,
        )

    def test_install_then_uninstall_round_trips(self):
        p = self._tmp(json.dumps({"model": "opus"}))
        self.assertEqual(self._run("install-settings", p).returncode, 0)
        self.assertEqual(self._run("uninstall-settings", p).returncode, 0)
        with open(p) as f:
            self.assertEqual(json.load(f), {"model": "opus"})

    def test_leaves_foreign_statusline_and_hooks(self):
        p = self._tmp(json.dumps({
            "statusLine": {"type": "command", "command": "/usr/local/bin/my-bar"},
            "hooks": {"Stop": [
                {"hooks": [{"type": "command", "command": "/opt/my-hook"}]}]},
        }))
        r = self._run("uninstall-settings", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(p) as f:
            data = json.load(f)
        self.assertEqual(data["statusLine"]["command"], "/usr/local/bin/my-bar")
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"],
                         "/opt/my-hook")

    def test_missing_file_is_noop(self):
        p = self._tmp()  # not created
        r = self._run("uninstall-settings", p)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDoctor(unittest.TestCase):
    """`doctor` exits non-zero only when a *critical* piece is missing."""

    def _tmp_dir(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    def _run(self, settings, rc, extra=()):
        return subprocess.run(
            [sys.executable, SCRIPT, "doctor",
             "--settings", settings, "--rc", rc, *extra],
            capture_output=True, text=True, timeout=15, env=ENV,
        )

    def test_fails_when_nothing_wired(self):
        d = self._tmp_dir()
        rc = os.path.join(d, "rc"); open(rc, "w").close()
        r = self._run(os.path.join(d, "settings.json"), rc)
        self.assertEqual(r.returncode, 1)

    def test_passes_when_fully_wired(self):
        d = self._tmp_dir()
        settings = os.path.join(d, "settings.json")
        subprocess.run([sys.executable, SCRIPT, "install-settings",
                        "--settings", settings], capture_output=True,
                       timeout=15, env=ENV)
        rc = os.path.join(d, "rc")
        with open(rc, "w") as f:
            f.write(f"# {cf.MARKER}\nsource whatever\n")
        r = self._run(settings, rc)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_minimal_passes_with_only_launcher(self):
        d = self._tmp_dir()
        rc = os.path.join(d, "rc")
        with open(rc, "w") as f:
            f.write(f"# {cf.MARKER}\nsource whatever\n")
        r = self._run(os.path.join(d, "settings.json"), rc, extra=("--minimal",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_foreign_statusline_warns_but_does_not_fail(self):
        # a user-owned statusLine may well tap the frog itself (we can't tell),
        # so it must not fail the checkup — just warn.
        d = self._tmp_dir()
        settings = os.path.join(d, "settings.json")
        subprocess.run([sys.executable, SCRIPT, "install-settings",
                        "--settings", settings], capture_output=True,
                       timeout=15, env=ENV)
        with open(settings) as f:
            data = json.load(f)
        data["statusLine"] = {"type": "command", "command": "/usr/local/bin/my-bar"}
        with open(settings, "w") as f:
            json.dump(data, f)
        rc = os.path.join(d, "rc")
        with open(rc, "w") as f:
            f.write(f"# {cf.MARKER}\nsource whatever\n")
        r = self._run(settings, rc)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("your own statusLine", r.stdout)

    def test_generated_commands_use_this_interpreter(self):
        # settings.json wiring and the tmux keybind must run on the same
        # interpreter that installed them — a pipx venv has no `python3` on
        # PATH, and PATH inside tmux/hooks differs from the install shell.
        self.assertIn(sys.executable, cf._frog_cmd("tap"))
        self.assertIn(sys.executable, cf._keybind_line())

    def _run_env(self, extra_env, d):
        rc = os.path.join(d, "rc"); open(rc, "w").close()
        return subprocess.run(
            [sys.executable, SCRIPT, "doctor",
             "--settings", os.path.join(d, "settings.json"), "--rc", rc],
            capture_output=True, text=True, timeout=15,
            env={**ENV, **extra_env})

    def test_names_terminal_requirements(self):
        # truecolor, NO_COLOR, and the half-block glyphs are requirements the
        # renderer can't degrade around — doctor must at least name them.
        r = self._run_env({"NO_COLOR": "1", "COLORTERM": ""}, self._tmp_dir())
        self.assertIn("Truecolor", r.stdout)
        self.assertIn("NO_COLOR", r.stdout)
        self.assertIn("Half-blocks", r.stdout)

    def test_terminal_notes_never_fail_the_checkup(self):
        # a hostile terminal warns but is non-critical: fully wired ⇒ exit 0.
        d = self._tmp_dir()
        settings = os.path.join(d, "settings.json")
        subprocess.run([sys.executable, SCRIPT, "install-settings",
                        "--settings", settings], capture_output=True,
                       timeout=15, env=ENV)
        rc = os.path.join(d, "rc")
        with open(rc, "w") as f:
            f.write(f"# {cf.MARKER}\nsource whatever\n")
        r = subprocess.run(
            [sys.executable, SCRIPT, "doctor", "--settings", settings,
             "--rc", rc],
            capture_output=True, text=True, timeout=15,
            env={**ENV, "NO_COLOR": "1", "COLORTERM": "dumb"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestEnvironment(unittest.TestCase):
    """The flora scene: props that sprout around the frog, one per prompt."""

    def test_prop_sprites_rectangular_and_keys_resolve(self):
        for name in ("FLOWER", "TREE", "ROCK", "LOG", "CLOUD"):
            grid = getattr(cf, name)
            widths = {len(row) for row in grid}
            self.assertEqual(len(widths), 1, f"{name} rows are ragged: {widths}")
            used = {ch for row in grid for ch in row}
            missing = used - set(cf.FLORA)
            self.assertFalse(missing, f"{name} uses keys absent from FLORA: {missing}")

    def test_spawn_adds_one_prop_of_a_known_kind(self):
        import random
        sc = cf.Scene(rng=random.Random(0))
        for i in range(1, 6):
            sc.spawn(i, 40)
            self.assertEqual(len(sc.props), i)
            self.assertIn(sc.props[-1]["kind"], cf.PROP_KINDS)

    def test_flower_hues_vary_and_palette_recolors_petals(self):
        # a flower's petal key is the random-hued bloom, never the FLORA default
        p1 = cf._flower_palette(0.1)
        p2 = cf._flower_palette(0.6)
        self.assertNotEqual(p1["*"], cf.FLORA["*"])
        self.assertNotEqual(p1["*"], p2["*"])

    def test_blits_never_raise_and_return_int_coords(self):
        import random
        for cols, rows in ((40, 7), (24, 4), (120, 10), (9, 3)):
            sc = cf.Scene(rng=random.Random(cols))
            for t in range(30):
                sc.spawn(t, cols)
            stage_h = rows * 2
            for f in range(40):          # spans entrance frames and settled ones
                for spr, x, y in sc.blits(f, cols, stage_h, (cols - 19) // 2, 19):
                    self.assertIsInstance(x, int)
                    self.assertIsInstance(y, int)
                    self.assertTrue(spr and spr[0])   # non-empty sprite

    def test_props_accumulate_up_to_the_backstop(self):
        # Props are a running tally: they remain until the runaway backstop.
        import random
        sc = cf.Scene(rng=random.Random(1))
        for t in range(cf.FLORA_MAX - 5):
            sc.spawn(t, 40)
        self.assertEqual(len(sc.props), cf.FLORA_MAX - 5)   # nothing dropped yet
        for t in range(20):
            sc.spawn(t, 40)
        self.assertEqual(len(sc.props), cf.FLORA_MAX)       # capped, not exceeded

    def test_clouds_park_and_remain(self):
        # Clouds drift in once, then stay put — never culled off-edge.
        import random
        sc = cf.Scene(rng=random.Random(2))
        sc.rng = type("R", (), {
            "choice": staticmethod(lambda seq: -1 if seq == (-1, 1) else "cloud"),
            "random": staticmethod(lambda: 0.0),
        })()
        for t in range(3):
            sc.spawn(t, 40)
        # every cloud is still on stage every frame (never culled)...
        for f in (0, 5, 50, 500):
            self.assertEqual(len(sc.blits(f, 40, 14, 10, 19)), 3)
        # ...and long after entrances they've parked on-screen, not sailed off
        for _spr, x, _y in sc.blits(500, 40, 14, 10, 19):
            self.assertTrue(0 <= x <= 40, f"parked cloud is off-screen at x={x}")
        self.assertEqual(sum(p["kind"] == "cloud" for p in sc.props), 3)

    def test_ground_props_wrap_into_stacked_tiers(self):
        # Once a side's row fills the half-width, further props tier upward.
        import random
        sc = cf.Scene(rng=random.Random(4))
        sc.rng = type("R", (), {
            "choice": staticmethod(lambda seq: "rock"),
            "random": staticmethod(lambda: 0.0),
            "randint": staticmethod(lambda a, b: a),
        })()
        cols, frog_x, frog_w = 40, 10, 19
        for t in range(30):
            sc.spawn(t, cols)
        ys = {y for _spr, _x, y in sc.blits(99, cols, 14, frog_x, frog_w)}
        self.assertGreater(len(ys), 1, "props never tiered onto a second row")

    def test_ground_props_alternate_sides(self):
        import random
        sc = cf.Scene(rng=random.Random(3))
        # force only ground props so side alternation is observable
        sc.rng = type("R", (), {
            "choice": staticmethod(lambda seq: "rock"),
            "random": staticmethod(lambda: 0.0),
            "randint": staticmethod(lambda a, b: a),
        })()
        for t in range(4):
            sc.spawn(t, 40)
        sides = [p["side"] for p in sc.props]
        self.assertEqual(sides, [-1, 1, -1, 1])


if __name__ == "__main__":
    unittest.main()
