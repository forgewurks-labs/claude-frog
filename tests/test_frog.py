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
        peak = lambda g: max(abs(f["hips"]) for f in self.frames(g))
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


class TestCliModesExitZero(unittest.TestCase):
    """The statusline / tap / hook / preview paths must never break a prompt."""

    def _run(self, args, stdin=""):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], input=stdin,
            capture_output=True, text=True, timeout=15,
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


class TestInstallSettings(unittest.TestCase):
    """`install-settings` must merge into settings.json without clobbering."""

    def _run(self, path, extra=()):
        return subprocess.run(
            [sys.executable, SCRIPT, "install-settings", "--settings", path, *extra],
            capture_output=True, text=True, timeout=15,
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
            capture_output=True, text=True, timeout=15,
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
            capture_output=True, text=True, timeout=15,
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
                        "--settings", settings], capture_output=True, timeout=15)
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
                        "--settings", settings], capture_output=True, timeout=15)
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
