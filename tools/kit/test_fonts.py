#!/usr/bin/env python3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fonts
import launcher

PAIRS = (
    ("percolator_expert_35_1000_000", "vamp_mainfont_35_1000_000", "vampirece2scheme.res:372"),
    ("percolator_expert_26_1000_000", "vamp_mainfont_26_1000_000", "vampirece2scheme.res:383"),
    ("copperplate_gothic_bt_18_1000_000", "vamp_small_18_1000_000", "vampirece2scheme.res:394"),
    ("dominican_22_1000_000", "vamp_plain_22_1000_000", "vampirece2scheme.res:405"),
)
SOURCES = {s: ("font:" + s).encode() for _, s, _ in PAIRS}


def make_vpk(path, files):
    blob = b""
    rows = b""
    for name, data in files.items():
        raw = name.encode("latin-1")
        rows += struct.pack("<I", len(raw)) + raw + struct.pack("<II", len(blob), len(data))
        blob += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob + rows + struct.pack("<IIB", len(files), len(blob), 0))


def game_with(root, sources):
    make_vpk(root / "Vampire/pack001.vpk", {fonts.FONT_DIR + s + ".fnt": d for s, d in sources.items()})
    return root


class Fonts(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.game = Path(t.name) / "game"
        self.overlay = Path(t.name) / "overlay"
        self.lines = []
        self.root = Path(t.name)
        self.out = self.overlay / "files/Vampire" / fonts.FONT_DIR

    def snapshot(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))

    def test_aliases_match_the_scheme_pairs(self):
        self.assertEqual(fonts.ALIASES, tuple((a, s) for a, s, _ in PAIRS))

    def test_dry_run_writes_no_font(self):
        game_with(self.game, SOURCES)
        launcher.main(["set", "--overlay", str(self.overlay), "--game", str(self.game)])
        saved = launcher.osascript
        self.addCleanup(setattr, launcher, "osascript", saved)
        launcher.osascript = lambda script: (0, "", "")
        bundle = self.root / "B.app"
        exe = bundle / "Contents/MacOS/bloodlines-native"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        launcher.main(["run", "--dry-run", "--overlay", str(self.overlay), "--bundle", str(bundle)])
        self.assertFalse(self.out.exists())

    def test_copies_land_under_the_alias_names(self):
        game_with(self.game, SOURCES)
        self.assertEqual(fonts.copy_aliases(self.game, self.overlay, self.lines.append), len(PAIRS))
        for alias, source, _ in PAIRS:
            self.assertEqual((self.out / (alias + ".fnt")).read_bytes(), SOURCES[source])
            self.assertIn("kit: font alias %s -> %s" % (alias, source), self.lines)

    def test_second_run_is_a_no_op(self):
        game_with(self.game, SOURCES)
        fonts.copy_aliases(self.game, self.overlay, self.lines.append)
        before = {p: p.stat().st_mtime_ns for p in self.out.iterdir()}
        self.lines.clear()
        self.assertEqual(fonts.copy_aliases(self.game, self.overlay, self.lines.append), 0)
        self.assertEqual(self.lines, [])
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in self.out.iterdir()})

    def test_changed_copy_is_replaced(self):
        game_with(self.game, SOURCES)
        fonts.copy_aliases(self.game, self.overlay, self.lines.append)
        alias = fonts.ALIASES[0][0]
        (self.out / (alias + ".fnt")).write_bytes(b"stale")
        self.assertEqual(fonts.copy_aliases(self.game, self.overlay, self.lines.append), 1)
        self.assertEqual((self.out / (alias + ".fnt")).read_bytes(), SOURCES[fonts.ALIASES[0][1]])

    def test_nothing_is_written_outside_the_overlay(self):
        game_with(self.game, SOURCES)
        before = [p for p in self.snapshot() if p.startswith("game")]
        fonts.copy_aliases(self.game, self.overlay, self.lines.append)
        self.assertEqual([p for p in self.snapshot() if p.startswith("game")], before)
        self.assertTrue(all(p.startswith(("game", "overlay")) for p in self.snapshot()))
        self.assertFalse(list(self.out.glob("*.tmp")))

    def test_missing_source_is_skipped_with_a_log_line(self):
        partial = dict(list(SOURCES.items())[1:])
        game_with(self.game, partial)
        self.assertEqual(fonts.copy_aliases(self.game, self.overlay, self.lines.append), len(fonts.ALIASES) - 1)
        alias, source = fonts.ALIASES[0]
        self.assertFalse((self.out / (alias + ".fnt")).exists())
        self.assertTrue(any(alias in l and "skipped" in l and source in l for l in self.lines))

    def test_no_packs_logs_and_writes_nothing(self):
        self.game.mkdir()
        self.assertEqual(fonts.copy_aliases(self.game, self.overlay, self.lines.append), 0)
        self.assertEqual(len(self.lines), len(fonts.ALIASES))
        self.assertFalse(self.overlay.exists())

    def test_launcher_reads_the_fonts_from_the_bundle_before_the_game_starts(self):
        bundle = self.root / "B.app"
        game_with(bundle / "Contents/Resources/game", SOURCES)
        launcher.main(["set", "--overlay", str(self.overlay), "--game", str(self.game)])
        exe = bundle / "Contents/MacOS/bloodlines-native"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        saved = launcher.osascript
        self.addCleanup(setattr, launcher, "osascript", saved)
        launcher.osascript = lambda script: (0, "", "")
        launcher.main(["run", "--overlay", str(self.overlay), "--bundle", str(bundle)])
        self.assertTrue((self.out / (fonts.ALIASES[0][0] + ".fnt")).is_file())
        self.assertIn("kit: font alias", (self.overlay / "logs/launcher.log").read_text())


if __name__ == "__main__":
    unittest.main()
