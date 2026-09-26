"""Tests for the parts that have to be right.

The engine module is Qt-free and curses-free on purpose, so all of this runs
headless. Where a test needs a library it builds one on disk rather than
depending on what happens to be installed — but `test_real_library` runs the
scanner over whatever library this machine actually has, because a parser that
only survives its own fixtures isn't worth much.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Every test below that isn't about host detection wants the layer-shell path,
# whatever desktop the suite happens to run on — never plasmashell or gsettings.
os.environ["FOSSYPAPER_HOST"] = "layer"

from fossypaper import config, engine, properties, sources, theme  # noqa: E402


def make_library(root: Path, wallpapers: dict) -> None:
    for wid, (meta, files) in wallpapers.items():
        d = root / wid
        d.mkdir(parents=True)
        (d / "project.json").write_text(json.dumps(meta))
        for name in files:
            (d / name).write_bytes(b"\0" * 16)


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        self.root.mkdir()
        make_library(self.root, {
            "1001": ({"title": "A scene", "type": "scene", "file": "scene.json",
                      "preview": "preview.gif",
                      "general": {"supportsaudioprocessing": True, "properties": {}}},
                     ["scene.pkg", "preview.gif"]),
            "1002": ({"title": "A gif scene", "type": "scene", "file": "gifscene.json",
                      "preview": "preview.gif"},
                     ["gifscene.pkg", "preview.gif"]),
            "1003": ({"title": "A video", "type": "video", "file": "clip with spaces.mp4",
                      "preview": "preview.jpg"},
                     ["clip with spaces.mp4", "preview.jpg"]),
            "1004": ({"title": "A still", "type": "image", "file": "wallpaper.png",
                      "preview": "preview.png"},
                     ["wallpaper.png", "preview.png"]),
            "1005": ({"title": "A web one", "type": "web", "file": "index.html"},
                     ["index.html"]),
            "1006": ({"title": "No entry file named", "type": "scene"}, ["scene.pkg"]),
            "1007": ({"title": "Corrupted download", "preview": "preview.gif"},
                     ["preview.gif"]),
            "1007b": ({"title": "Manifest arrived, payload didn't", "type": "scene",
                       "file": "scene.pkg", "preview": "preview.gif"},
                      ["preview.gif"]),
            "1008": ({"title": "Preset, base missing", "dependency": "9999",
                      "preset": {"city1": "REDGRAVE"}, "preview": "preview.gif"},
                     ["preview.gif"]),
            "1009": ({"title": "Preset, base present", "dependency": "1001",
                      "preset": {"custombgimage": "files/glow.gif", "rate": 100}},
                     []),
            "2001": ({"title": "Base with a photo slot", "type": "scene", "file": "scene.json",
                      "general": {"properties": {
                          "photoslot": {"type": "scenetexture", "value": ""}}}},
                     ["scene.pkg"]),
            "2002": ({"title": "Preset, unfillable photo slot", "dependency": "2001",
                      "preset": {"photoslot": "files/pic.png", "tint": "1 0 0"}},
                     []),
        })
        (self.root / "1009" / "files").mkdir()
        (self.root / "1009" / "files" / "glow.gif").write_bytes(b"\0" * 16)
        (self.root / "2002" / "files").mkdir()
        (self.root / "2002" / "files" / "pic.png").write_bytes(b"\0" * 16)
        (self.root / "not-a-wallpaper").mkdir()
        self._env = os.environ.get("FOSSYPAPER_LIBRARY")
        os.environ["FOSSYPAPER_LIBRARY"] = str(self.root)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("FOSSYPAPER_LIBRARY", None)
        else:
            os.environ["FOSSYPAPER_LIBRARY"] = self._env
        self.tmp.cleanup()

    def test_scan_skips_folders_without_a_manifest(self):
        ids = {w.id for w in engine.scan_library()}
        self.assertEqual(ids, {"1001", "1002", "1003", "1004", "1005", "1006",
                                "1007", "1007b", "1008", "1009", "2001", "2002"})

    def test_entry_file_follows_the_manifest(self):
        """project.json names scene.json; the shipped bundle is the .pkg."""
        self.assertEqual(engine.find("1001").entry.name, "scene.pkg")
        self.assertEqual(engine.find("1002").entry.name, "gifscene.pkg")

    def test_video_filename_with_spaces(self):
        self.assertEqual(engine.find("1003").entry.name, "clip with spaces.mp4")
        self.assertTrue(engine.find("1003").video)

    def test_entry_falls_back_to_a_scan(self):
        self.assertEqual(engine.find("1006").entry.name, "scene.pkg")

    def test_backend_routing(self):
        self.assertEqual(engine.backend_for(engine.find("1001"))[0], "wpe")
        self.assertEqual(engine.backend_for(engine.find("1002"))[0], "wpe")
        self.assertEqual(engine.backend_for(engine.find("1003"))[0], "mpvpaper")
        self.assertIn(engine.backend_for(engine.find("1004"))[0], ("swww", "mpvpaper"))

    def test_web_wallpapers_are_refused_not_faked(self):
        wp = engine.find("1005")
        self.assertFalse(engine.backend_for(wp)[1])
        self.assertIn("web server", engine.why_unsupported(wp))
        ok, msg = engine.start("1005", config.opts(dict(config.DEFAULTS)))
        self.assertFalse(ok)
        self.assertIn("web server", msg)

    def test_corrupted_download_is_refused_not_silently_run(self):
        """No entry file and no `dependency` to blame it on — a partial or
        corrupted Workshop download. Must not fall through to 'wpe' just
        because a renderer happens to be on PATH: there is nothing to draw."""
        wp = engine.find("1007")
        self.assertEqual(wp.type, "missing_assets")
        self.assertFalse(wp.supported)
        be, ok = engine.backend_for(wp)
        self.assertEqual(be, "missing_assets")
        self.assertFalse(ok)
        self.assertIn("corrupted", engine.why_unsupported(wp))
        ok, msg = engine.start("1007", config.opts(dict(config.DEFAULTS)))
        self.assertFalse(ok)
        self.assertIn("corrupted", msg)

    def test_declared_type_does_not_override_a_missing_payload(self):
        """project.json (small, arrives first from Steam) says 'scene' and
        names scene.pkg, but scene.pkg (large, arrives later) never showed up.
        The manifest's own say-so must not be trusted over what's on disk."""
        wp = engine.find("1007b")
        self.assertEqual(wp.type, "missing_assets")
        self.assertFalse(engine.backend_for(wp)[1])

    def test_preset_with_unresolvable_dependency_names_the_missing_item(self):
        wp = engine.find("1008")
        self.assertEqual(wp.type, "missing_dependency")
        self.assertEqual(wp.depends_on, "9999")
        self.assertFalse(engine.backend_for(wp)[1])
        self.assertIn("9999", engine.why_unsupported(wp))

    def test_preset_with_local_dependency_renders_through_the_base(self):
        """A preset addon (no scene of its own, just a `dependency` id and
        override values) should render via the base wallpaper it customizes,
        with its own values layered on as --set-property."""
        wp = engine.find("1009")
        self.assertEqual(wp.type, "scene")               # inherited from 1001
        self.assertTrue(wp.supported)
        self.assertEqual(wp.render_id, "1001")
        self.assertEqual(wp.preset_overrides["rate"], "100")
        self.assertTrue(wp.preset_overrides["custombgimage"].endswith("1009/files/glow.gif"))
        self.assertTrue(Path(wp.preset_overrides["custombgimage"]).is_absolute())
        self.assertEqual(engine.backend_for(wp)[0], "wpe")

        argv = engine.build_argv(wp.render_id,
                                  {**config.opts(dict(config.DEFAULTS)),
                                   "properties": wp.preset_overrides})
        self.assertEqual(argv[argv.index("--bg") + 1], "1001")
        self.assertIn(f"custombgimage={wp.preset_overrides['custombgimage']}", argv)

    def test_scenetexture_overrides_are_not_forwarded(self):
        """linux-wallpaperengine's texture cache only ever resolves a scene
        texture against the wallpaper's own compiled package — it has no code
        path for loading an external file, whatever path we hand it. Forwarding
        one just buys a silent renderer-side failure and a blank panel, so this
        must be skipped and surfaced, not sent."""
        wp = engine.find("2002")
        self.assertEqual(wp.type, "scene")
        self.assertEqual(wp.skipped_textures, ["photoslot"])
        self.assertNotIn("photoslot", wp.preset_overrides)
        self.assertEqual(wp.preset_overrides["tint"], "1 0 0")   # untouched, not scenetexture
        note = engine.render_note(wp)
        self.assertIn("photoslot", note)
        self.assertIn("2001", note)

    def test_audio_processing_auto_follows_the_manifest(self):
        o = config.opts(dict(config.DEFAULTS))
        self.assertEqual(o["audio_processing"], "auto")
        self.assertTrue(engine.wants_audio(engine.find("1001"), o))   # declares it
        self.assertFalse(engine.wants_audio(engine.find("1002"), o))  # doesn't
        self.assertTrue(engine.wants_audio(engine.find("1002"), {**o, "audio_processing": "always"}))
        self.assertFalse(engine.wants_audio(engine.find("1001"), {**o, "audio_processing": "never"}))

    def test_unknown_id_is_reported_not_crashed(self):
        ok, msg = engine.start("nope", config.opts(dict(config.DEFAULTS)))
        self.assertFalse(ok)
        self.assertIn("nope", msg)

    def test_start_resolves_a_preset_through_its_dependency(self):
        """End-to-end through start(): the wallpaper actually launched has to
        be the dependency's, carrying the preset's own overrides — not the
        preset's own (assetless) id."""
        from unittest.mock import patch
        with patch.object(engine, "have_renderer", return_value=True), \
             patch.object(engine, "stop"), \
             patch.object(engine, "STATE", self.root), \
             patch.object(engine, "RENDER_LOG", self.root / "renderer.log"), \
             patch.object(engine, "_spawn") as spawn:
            ok, msg = engine.start("1009", config.opts(dict(config.DEFAULTS)))
        self.assertTrue(ok, msg)
        argv = spawn.call_args[0][0]
        self.assertEqual(argv[argv.index("--bg") + 1], "1001")
        self.assertTrue(any(a.startswith("custombgimage=") for a in argv))

    def test_a_renderer_that_dies_at_once_is_a_failure_not_applied(self):
        """start() used to say 'applied' the moment it spawned, with the
        renderer's stderr in /dev/null. A renderer that exits on its first frame
        has to come back as a failure that quotes its own last words."""
        from unittest.mock import patch
        fake = [sys.executable, "-c", "import sys; print('no GL context for you'); sys.exit(3)"]
        with patch.object(engine, "have_renderer", return_value=True), \
             patch.object(engine, "stop"), \
             patch.object(engine, "STATE", self.root), \
             patch.object(engine, "RENDER_LOG", self.root / "renderer.log"), \
             patch.object(engine, "build_argv", return_value=fake):
            ok, msg = engine.start("1001", config.opts(dict(config.DEFAULTS)))
        self.assertFalse(ok)
        self.assertIn("no GL context for you", msg)

    def test_a_renderer_that_stays_up_is_applied(self):
        from unittest.mock import patch
        fake = [sys.executable, "-c", "import time; time.sleep(5)"]
        with patch.object(engine, "have_renderer", return_value=True), \
             patch.object(engine, "stop"), \
             patch.object(engine, "STATE", self.root), \
             patch.object(engine, "RENDER_LOG", self.root / "renderer.log"), \
             patch.object(engine, "build_argv", return_value=fake):
            ok, msg = engine.start("1001", {**config.opts(dict(config.DEFAULTS)), "verify_grace": 0.4})
        for proc in engine._spawned:
            proc.kill(); proc.wait()
        self.assertTrue(ok, msg)


class NoticeTest(unittest.TestCase):
    def test_prompt_boxes_go_off_and_marketing_gets_hidden(self):
        from unittest.mock import patch
        props = [properties.Property("promptbox", "bool", "🔘提示框 prompt box", "true"),
                 properties.Property("brhidemarketingwords", "bool", "Hide marketing words", "false"),
                 properties.Property("rain", "bool", "Rain", "true"),
                 properties.Property("theme", "combo", "Prompt box colour", "1")]
        wp = engine.Wallpaper("9", "t", "scene", False, None, Path("/nonexistent"))
        with patch.object(engine, "list_properties", return_value=props):
            self.assertEqual(engine.quiet_overrides(wp),
                             {"promptbox": "false", "brhidemarketingwords": "true"})
            # what the user set by hand still wins, and the switch turns it all off
            self.assertEqual(engine.effective_properties(wp, {"properties": {"promptbox": "true"}})
                             ["promptbox"], "true")
            self.assertEqual(engine.effective_properties(wp, {"hide_author_notices": False}), {})

    def test_plasma_gets_typed_props(self):
        self.assertIs(engine._typed("true"), True)
        self.assertEqual(engine._typed("0.5"), 0.5)
        self.assertEqual(engine._typed("3"), 3)
        self.assertEqual(engine._typed("1 0.5 0"), "1 0.5 0")


class PreflightTest(unittest.TestCase):
    def test_a_cached_verdict_skips_the_nested_compositor(self):
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory()
        d = Path(tmp.name)
        pkg = d / "scene.pkg"; pkg.write_bytes(b"x")
        wp = engine.Wallpaper("5", "t", "scene", False, None, d, entry=pkg)
        key = f"5:{int(pkg.stat().st_mtime)}:{{}}"
        (d / "pf.json").write_text(json.dumps({key: {"ok": False, "why": "hangs"}}))
        with patch.object(engine, "_PREFLIGHT", d / "pf.json"), \
             patch.object(engine, "find", return_value=wp), \
             patch.object(engine, "which", return_value="/bin/true"), \
             patch.object(engine.subprocess, "Popen", side_effect=AssertionError("spawned")):
            self.assertEqual(engine.plasma_preflight(wp, {}), (False, "hangs"))
        tmp.cleanup()


class SceneVideoTest(unittest.TestCase):
    def test_the_mp4_inside_a_video_texture_comes_out_whole(self):
        import struct
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory()
        d = Path(tmp.name)
        mp4 = b"\x00\x00\x00\x18ftypisom" + b"V" * 70000
        tex = b"TEXV0005\x00TEXI0001" + b"\x00" * 60 + struct.pack("<I", len(mp4)) + mp4 + b"TRAIL"
        files = [("scene.json", b"{}"), ("materials/clip.tex", tex)]
        head = b"".join(struct.pack("<I", len(n)) + n.encode() + struct.pack("<II", 0, 0)
                        for n, _ in files)
        body, entries, off = b"", b"", 0
        for n, data in files:
            entries += struct.pack("<I", len(n)) + n.encode() + struct.pack("<II", off, len(data))
            body += data; off += len(data)
        pkg = d / "scene.pkg"
        pkg.write_bytes(struct.pack("<I", 8) + b"PKGV0022" + struct.pack("<I", len(files))
                        + entries + body)
        wp = engine.Wallpaper("77", "t", "scene", True, None, d, entry=pkg)
        with patch.object(engine, "SCENE_VIDEOS", d / "out"), \
             patch.object(engine, "find", return_value=wp):
            out = engine.scene_video(wp)
        self.assertIsNotNone(out)
        self.assertEqual(out.read_bytes(), mp4)
        tmp.cleanup()


class ArgvTest(unittest.TestCase):
    def base(self, **over):
        return {**config.opts(dict(config.DEFAULTS)), "output": "eDP-1", **over}

    def test_scaling_and_clamp_follow_their_screen(self):
        argv = engine.build_argv("42", self.base(scaling="fill", clamp="border"))
        i = argv.index("--screen-root")
        self.assertEqual(argv[i:i + 8],
                         ["--screen-root", "eDP-1", "--bg", "42",
                          "--scaling", "fill", "--clamp", "border"])

    def test_span_replaces_per_screen_roots(self):
        argv = engine.build_argv("42", self.base(span=["DP-1", "DP-2"]))
        self.assertIn("--screen-span", argv)
        self.assertEqual(argv[argv.index("--screen-span") + 1], "DP-1,DP-2")
        self.assertNotIn("--screen-root", argv)

    def test_fullscreen_pause_switches_are_exclusive(self):
        on = engine.build_argv("42", self.base(fullscreen_pause=True))
        self.assertNotIn("--no-fullscreen-pause", on)
        off = engine.build_argv("42", self.base(fullscreen_pause=False))
        self.assertIn("--no-fullscreen-pause", off)
        only = engine.build_argv("42", self.base(fullscreen_pause=True, pause_only_active=True))
        self.assertIn("--fullscreen-pause-only-active", only)
        self.assertNotIn("--no-fullscreen-pause", only)

    def test_silent_and_volume_do_not_both_appear(self):
        quiet = engine.build_argv("42", self.base(silent=True))
        self.assertIn("--silent", quiet)
        self.assertNotIn("--volume", quiet)
        loud = engine.build_argv("42", self.base(silent=False, volume=40, no_automute=True))
        self.assertNotIn("--silent", loud)
        self.assertEqual(loud[loud.index("--volume") + 1], "40")
        self.assertIn("--noautomute", loud)

    def test_properties_become_set_property_pairs(self):
        argv = engine.build_argv("42", self.base(properties={"theme": "4", "bar": "0.5"}))
        self.assertIn("theme=4", argv)
        self.assertIn("bar=0.5", argv)


class PropertyTest(unittest.TestCase):
    SCHEMA = {"general": {"properties": {
        "grp":    {"type": "group", "text": "Look", "order": 0, "value": ""},
        "theme":  {"type": "combo", "text": "Theme", "value": "1", "order": 1,
                   "options": [{"label": "Dark", "value": "0"},
                               {"label": "Light", "value": "1"}]},
        "custom": {"type": "color", "text": "Colour", "value": "0.5 0.25 1",
                   "order": 2, "condition": "theme.value == 1"},
        "size":   {"type": "slider", "text": "Size", "value": 0.02, "min": 0.01,
                   "max": 0.05, "step": 0.001, "precision": 4, "order": 3},
        "on":     {"type": "bool", "text": "On", "value": True, "order": 4},
        "hotkey": {"type": "usershortcut", "text": "Key", "value": "F1", "order": 5},
        "broken": "not a dict",
    }}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        (self.folder / "project.json").write_text(json.dumps(self.SCHEMA))
        self.props = properties.from_project(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unsupported_and_malformed_entries_are_dropped(self):
        keys = [p.key for p in self.props]
        self.assertNotIn("hotkey", keys)      # Windows-only hotkey binding
        self.assertNotIn("broken", keys)
        self.assertEqual(keys, ["grp", "theme", "custom", "size", "on"])

    def test_types_and_ranges_survive(self):
        by = {p.key: p for p in self.props}
        self.assertEqual(by["theme"].options, [("Dark", "0"), ("Light", "1")])
        self.assertAlmostEqual(by["size"].mx, 0.05)
        self.assertEqual(by["size"].precision, 4)
        self.assertEqual(by["on"].value, "true")      # JSON bool -> WE's wire value
        self.assertFalse(by["grp"].editable)

    def test_conditions_hide_and_reveal(self):
        by = {p.key: p for p in self.props}
        values = properties.defaults(self.props)
        self.assertTrue(properties.visible(by["custom"], values))   # theme == 1
        self.assertFalse(properties.visible(by["custom"], {**values, "theme": "0"}))
        self.assertTrue(properties.visible(by["theme"], values))    # no condition

    def test_unparseable_conditions_show_rather_than_hide(self):
        p = properties.Property("x", "bool", "x", "true", condition="weird(nonsense)")
        self.assertTrue(properties.visible(p, {}))
        p.condition = "missingkey.value == 3"
        self.assertTrue(properties.visible(p, {}))

    def test_labels_lose_markup_and_ui_keys(self):
        self.assertEqual(properties.label("<br></br><u><h4>Colour Settings:</h4></u></h5>", "k"),
                         "Colour Settings")
        self.assertEqual(properties.label("Notes<sup><abbr title='x'>[info]</abbr></sup>", "k"), "Notes")
        self.assertEqual(properties.label("ui_browse_properties_scheme_color", "k"), "Scheme colour")
        self.assertEqual(properties.label("ui_custom_glow_amount", "k"), "Custom glow amount")
        self.assertEqual(properties.label("ui_mine", "k", {"ui_mine": "Mine!"}), "Mine!")
        self.assertEqual(properties.label("", "fallback_key"), "Fallback key")

    def test_scheme_colour_is_not_offered_as_a_knob(self):
        (self.folder / "project.json").write_text(json.dumps({"general": {"properties": {
            "schemecolor": {"type": "color", "text": "ui_browse_properties_scheme_color",
                            "value": "0 0 0"}}}}))
        self.assertEqual(properties.from_project(self.folder), [])

    def test_colours_round_trip_in_we_wire_format(self):
        self.assertEqual(properties.color_to_rgb("0.5 0.25 1"), (0.5, 0.25, 1.0))
        self.assertEqual(properties.color_to_rgb("0.5,0.25,1"), (0.5, 0.25, 1.0))
        self.assertEqual(properties.color_to_rgb(""), (0.0, 0.0, 0.0))
        self.assertEqual(properties.color_to_rgb("2 -1 x"), (1.0, 0.0, 0.0))
        self.assertEqual(properties.rgb_to_color(1, 0, 0.5).split(), ["1.000000", "0.000000", "0.500000"])


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self._old = getattr(config, "CONFIG")
        config.CONFIG = self.path

    def tearDown(self):
        config.CONFIG = self._old
        self.tmp.cleanup()

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load()["layer"], "auto")

    def test_unknown_keys_survive_a_round_trip(self):
        cfg = config.load()
        cfg["something_new"] = 7
        config.save(cfg)
        self.assertEqual(config.load()["something_new"], 7)

    def test_corrupt_file_falls_back_instead_of_raising(self):
        self.path.write_text("{ this is not json")
        self.assertEqual(config.load()["fps"], config.DEFAULTS["fps"])

    def test_old_audio_flag_is_carried_forward(self):
        self.path.write_text(json.dumps({"no_audio_processing": True}))
        self.assertEqual(config.load()["audio_processing"], "never")
        self.path.write_text(json.dumps({"no_audio_processing": False}))
        self.assertEqual(config.load()["audio_processing"], "auto")

    def test_coerce_matches_the_schema(self):
        self.assertIs(config.coerce("silent", "false"), False)
        self.assertEqual(config.coerce("fps", "144"), 144)
        self.assertEqual(config.coerce("span", "DP-1, DP-2"), ["DP-1", "DP-2"])
        with self.assertRaises(ValueError):
            config.coerce("fps", "fast")


class ThemeTest(unittest.TestCase):
    def test_every_theme_defines_every_role(self):
        for name in theme.THEMES:
            pal = theme.palette(name)
            self.assertEqual(set(pal), set(theme.ROLES))

    def test_no_gradients_in_the_stylesheet(self):
        qss = theme.stylesheet(theme.palette())
        for banned in ("qlineargradient", "qradialgradient", "qconicalgradient"):
            self.assertNotIn(banned, qss.lower())

    def test_following_a_wallpaper_stays_readable(self):
        pal = theme.palette(follow=["#101010", "#151515", "#1a1a1a"])
        self.assertGreater(theme._lum(pal["text"]) - theme._lum(pal["bg"]), 89)

    def test_a_bad_accent_is_ignored(self):
        self.assertEqual(theme.palette("obsidian", "not a colour")["accent"],
                         theme.THEMES["obsidian"][5])


class SourcesTest(unittest.TestCase):
    def test_plain_http_is_refused(self):
        with self.assertRaises(sources.SourceError):
            sources._get("http://example.com/")

    def test_workshop_rows_reject_other_apps_and_banned_items(self):
        self.assertIsNone(sources._workshop_row(
            {"consumer_app_id": 480, "publishedfileid": "1", "title": "not WE"}))
        self.assertIsNone(sources._workshop_row(
            {"consumer_app_id": 431960, "publishedfileid": "2", "title": "x", "banned": True}))
        row = sources._workshop_row({
            "consumer_app_id": 431960, "publishedfileid": "3", "title": "Scene one",
            "tags": [{"tag": "Video"}, {"tag": "1920 x 1080"}], "file_size": "1048576",
            "subscriptions": 12})
        self.assertEqual((row.id, row.kind), ("3", "video"))
        self.assertIn("1920 x 1080", row.meta)

    def test_local_ids_do_not_collide_between_sources(self):
        wh = sources.Listing("wallhaven", "abc123", "t", "", "")
        ws = sources.Listing("workshop", "abc123", "t", "", "")
        self.assertNotEqual(wh.local_id, ws.local_id)


class RealLibraryTest(unittest.TestCase):
    """Runs against whatever library this machine actually has. Skips when
    there isn't one, so it is safe in CI and useful on a real desktop."""

    def setUp(self):
        os.environ.pop("FOSSYPAPER_LIBRARY", None)
        self.lib = engine.scan_library()
        if not self.lib:
            self.skipTest("no wallpaper library on this machine")

    def test_every_wallpaper_resolves_an_entry_and_a_backend(self):
        for w in self.lib:
            with self.subTest(wallpaper=w.title):
                self.assertTrue(w.id and w.title)
                backend, _ok = engine.backend_for(w)
                self.assertTrue(backend)
                if w.supported:
                    self.assertIsNotNone(w.entry, f"{w.id} has no entry file")

    def test_property_schemas_parse_and_conditions_evaluate(self):
        for w in self.lib:
            with self.subTest(wallpaper=w.title):
                props = engine.list_properties(w.id)
                values = properties.defaults(props)
                for p in props:
                    properties.visible(p, values)      # must not raise
                    if p.kind == "color":
                        r, g, b = properties.color_to_rgb(p.value)
                        self.assertTrue(all(0.0 <= c <= 1.0 for c in (r, g, b)))

    def test_argv_is_built_for_every_scene(self):
        opts = config.opts(dict(config.DEFAULTS))
        for w in self.lib:
            if engine.backend_for(w)[0] != "wpe":
                continue
            argv = engine.build_argv(w.id, opts)
            self.assertEqual(argv[0].split("/")[-1], "linux-wallpaperengine")
            # one --bg per screen, and the id is what it points at
            self.assertEqual(argv.count("--bg"), max(1, argv.count("--screen-root")))
            for i, tok in enumerate(argv):
                if tok == "--bg":
                    self.assertEqual(argv[i + 1], w.id)


class HostTest(unittest.TestCase):
    """Who owns the desktop background decides how a wallpaper is applied."""

    def setUp(self):
        from unittest.mock import patch
        self.patch = patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        engine.forget_tools()

    def tearDown(self):
        engine.forget_tools()
        self.tmp.cleanup()

    def env(self, **kv):
        base = {k: "" for k in ("FOSSYPAPER_HOST", "HYPRLAND_INSTANCE_SIGNATURE", "NIRI_SOCKET",
                                "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "DISPLAY",
                                "WAYLAND_DISPLAY")}
        return self.patch.dict(os.environ, {**base, **kv})

    def test_detects_each_desktop(self):
        cases = [({"XDG_CURRENT_DESKTOP": "KDE", "WAYLAND_DISPLAY": "wayland-0"}, "plasma"),
                 ({"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "x11"}, "plasma"),
                 ({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "gnome"),
                 ({"HYPRLAND_INSTANCE_SIGNATURE": "x", "XDG_CURRENT_DESKTOP": "Hyprland"}, "layer"),
                 ({"NIRI_SOCKET": "/run/niri", "XDG_CURRENT_DESKTOP": "niri"}, "layer"),
                 ({"XDG_CURRENT_DESKTOP": "sway"}, "layer"),
                 ({"XDG_CURRENT_DESKTOP": "i3", "XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}, "x11"),
                 ({"XDG_CURRENT_DESKTOP": "river", "WAYLAND_DISPLAY": "wayland-1"}, "layer")]
        for env, want in cases:
            with self.subTest(env=env), self.env(**env):
                self.assertEqual(engine.host({"host": "auto"}), want)

    def test_explicit_choice_beats_env_beats_detection(self):
        with self.env(XDG_CURRENT_DESKTOP="KDE", FOSSYPAPER_HOST="gnome"):
            self.assertEqual(engine.host({"host": "auto"}), "gnome")
            self.assertEqual(engine.host({"host": "layer"}), "layer")
            self.assertEqual(engine.host({"host": "nonsense"}), "gnome")

    def test_auto_layer_steps_over_a_shell_backdrop(self):
        with self.patch.object(engine, "backdrop_shell", return_value=""):
            self.assertEqual(engine.resolved_layer({"layer": "auto"}), "background")
        with self.patch.object(engine, "backdrop_shell", return_value="qs"):
            self.assertEqual(engine.resolved_layer({"layer": "auto"}), "bottom")
            self.assertEqual(engine.resolved_layer({"layer": "top"}), "top")

    def test_argv_carries_the_resolved_layer_and_none_on_x11(self):
        o = {**config.opts(dict(config.DEFAULTS)), "output": "eDP-1"}
        with self.patch.object(engine, "backdrop_shell", return_value=""):
            argv = engine.build_argv("42", {**o, "host": "layer"})
            self.assertEqual(argv[argv.index("--layer") + 1], "background")
            self.assertNotIn("--layer", engine.build_argv("42", {**o, "host": "x11"}))

    def make_wp(self, name, kind):
        d = self.root / "lib" / "77"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(b"\0")
        (d / "project.json").write_text(json.dumps({"title": 'Q"uote', "type": kind, "file": name}))
        return engine.read_wallpaper(d)

    def test_plasma_and_gnome_draw_it_themselves(self):
        wp = self.make_wp("clip.mp4", "video")
        with self.patch.object(engine, "which", return_value=None):
            with self.env(FOSSYPAPER_HOST="layer"):
                self.assertFalse(engine.backend_for(wp)[1])      # needs mpvpaper
            for h in ("plasma", "gnome"):
                with self.env(FOSSYPAPER_HOST=h):
                    self.assertTrue(engine.backend_for(wp)[1], h)

    def test_plasma_script_registers_and_remembers(self):
        wp = self.make_wp("scene.pkg", "scene")
        cfg = engine.plasma_config(wp, {"fps": 60, "scaling": "fit"}, None)
        self.assertEqual((cfg["Kind"], cfg["Fps"], cfg["Fit"]), ("scene", 60, "fit"))
        js = engine.plasma_script(cfg)
        self.assertIn('d.wallpaperPlugin = "org.fossypaper.wallpaper"', js)
        self.assertIn('d.writeConfig("Title", "Q\\"uote")', js)       # escaped, not injected
        self.assertIn("print(JSON.stringify(prev))", js)

    def test_plasma_apply_saves_the_old_wallpaper_once_and_stop_restores(self):
        wp = self.make_wp("pic.png", "image")
        prev = self.root / "plasma-previous.json"
        calls = []

        def fake_eval(script):
            calls.append(script)
            return True, '{"1": "org.kde.image"}' if "writeConfig" in script else ""
        with self.patch.object(engine, "_PLASMA_PREV", prev), \
             self.patch.object(engine, "STATE", self.root), \
             self.patch.object(engine, "plasma_plugin_installed", return_value=True), \
             self.patch.object(engine, "plasma_eval", side_effect=fake_eval):
            ok, msg = engine._start_plasma(wp, {})
            self.assertTrue(ok, msg)
            self.assertEqual(json.loads(prev.read_text()), {"1": "org.kde.image"})
            engine.stop(keep="plasma")                  # re-applying: leave it be
            self.assertTrue(prev.is_file())
            engine.stop()
            self.assertFalse(prev.is_file())
            self.assertIn('"org.kde.image"', calls[-1])

    def test_plasma_refusal_is_reported(self):
        wp = self.make_wp("pic.png", "image")
        with self.patch.object(engine, "_PLASMA_PREV", self.root / "p.json"), \
             self.patch.object(engine, "plasma_plugin_installed", return_value=True), \
             self.patch.object(engine, "plasma_eval", return_value=(False, "Widgets are locked")):
            ok, msg = engine._start_plasma(wp, {})
        self.assertFalse(ok)
        self.assertIn("Widgets are locked", msg)

    def test_gnome_sets_both_uris_and_restores(self):
        wp = self.make_wp("pic.png", "image")
        sets = []

        def fake(*args):
            if args[0] == "get":
                return True, "'file:///old.png'"
            if args[0] == "set":
                sets.append(args[1:])
            return True, ""
        with self.patch.object(engine, "_GNOME_PREV", self.root / "g.json"), \
             self.patch.object(engine, "STATE", self.root), \
             self.patch.object(engine, "_gsettings", side_effect=fake):
            ok, msg = engine._start_gnome(wp, {})
            self.assertTrue(ok, msg)
            keys = {k for _schema, k, _v in sets}
            self.assertTrue({"picture-uri", "picture-uri-dark"} <= keys)
            sets.clear()
            engine._gnome_restore()
            self.assertIn(("org.gnome.desktop.background", "picture-uri", "'file:///old.png'"), sets)


class SceneEffectsTest(unittest.TestCase):
    """The Plasma renderer's static-pass cache melts scenes with effect chains
    (each frame's effects run on the previous frame), so those run uncached."""

    def pkg(self, root: Path, scene: dict) -> Path:
        import struct
        d = root / "5150"
        d.mkdir(parents=True, exist_ok=True)
        files = {"scene.json": json.dumps(scene).encode(), "models/a.json": b"{}"}
        head, body = [b"PKGV0001"], b""
        idx = b""
        for name, data in files.items():
            n = name.encode()
            idx += struct.pack("<I", len(n)) + n + struct.pack("<II", len(body), len(data))
            body += data
        blob = struct.pack("<I", 8) + head[0] + struct.pack("<I", len(files)) + idx + body
        (d / "scene.pkg").write_bytes(blob)
        (d / "project.json").write_text(json.dumps({"title": "t", "type": "scene", "file": "scene.json"}))
        return d

    def test_effects_turn_the_cache_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = [({"objects": [{"image": "a", "effects": [{"file": "effects/shake/effect.json"}]}]}, True),
                     ({"objects": [{"image": "a", "effects": [{"file": "x", "visible": False}]}]}, False),
                     ({"objects": [{"image": "a"}]}, False)]
            for scene, want in cases:
                with self.subTest(scene=scene):
                    import shutil
                    shutil.rmtree(root / "5150", ignore_errors=True)
                    wp = engine.read_wallpaper(self.pkg(root, scene))
                    self.assertEqual(engine.scene_has_effects(wp), want)
                    self.assertEqual(engine.plasma_config(wp, {}, None)["CachePasses"], not want)


if __name__ == "__main__":
    unittest.main(verbosity=2)
