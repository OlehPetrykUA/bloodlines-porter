import os
import struct
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import formats

BSP = b"VBSP" + struct.pack("<I", 17) + b"\0" * 8
MDL = b"IDST" + struct.pack("<I", 2531) + b"\0" * 8
TTH = b"TTH\0" + struct.pack("<H", 1) + b"\0" * 10
TTZ = zlib.compress(b"x" * 64)
VMT = b'"LightmappedGeneric"\n{\n\t"$basetexture" "a/b" // c {\n}\n'


def put(root, rel, data):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


class Tree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.u = os.path.join(self.tmp.name, "u")
        self.g = os.path.join(self.tmp.name, "g")
        for root in (self.u, os.path.join(self.g, "Vampire")):
            put(root, "maps/a.bsp", BSP)
            put(root, "models/a.mdl", MDL)
            put(root, "materials/a.tth", TTH)
            put(root, "materials/a.ttz", TTZ)
            put(root, "materials/a.vmt", VMT)

    def tearDown(self):
        self.tmp.cleanup()

    def run_fmt(self):
        return formats.run(self.u, self.g)

    def test_clean(self):
        lines, problems = self.run_fmt()
        self.assertEqual(problems, [])
        self.assertIn("formats .bsp 17 U=1 G=1", lines)
        self.assertIn("formats .mdl 2531 U=1 G=1", lines)
        self.assertIn("formats .tth 1 U=1 G=1", lines)
        self.assertIn("formats .ttz zlib U=1 G=1", lines)
        self.assertEqual(lines[-1], "formats total U=5 G=5 fail 0")

    def test_flipped_version_byte_fails(self):
        put(self.u, "maps/a.bsp", BSP[:4] + bytes([18]) + BSP[5:])
        _, problems = self.run_fmt()
        self.assertEqual(len(problems), 1)
        self.assertIn("version .bsp 18", problems[0])

    def test_flipped_ident_fails(self):
        put(self.u, "models/a.mdl", b"IDSX" + MDL[4:])
        _, problems = self.run_fmt()
        self.assertIn("version .mdl bad:", problems[0])

    def test_vtf_version(self):
        vtf = b"VTF\0" + struct.pack("<II", 7, 2) + b"\0" * 8
        put(self.u, "materials/b.vtf", vtf)
        put(os.path.join(self.g, "Vampire"), "materials/b.vtf", vtf)
        _, problems = self.run_fmt()
        self.assertEqual(problems, [])
        put(self.u, "materials/b.vtf", b"VTF\0" + struct.pack("<II", 7, 5) + b"\0" * 8)
        _, problems = self.run_fmt()
        self.assertIn("version .vtf 7.5", problems[0])

    def test_unbalanced_text(self):
        put(self.u, "materials/c.vmt", b'"UnlitGeneric"\n{\n')
        _, problems = self.run_fmt()
        self.assertTrue(any("text unbalanced: materials/c.vmt" in p for p in problems))

    def test_commented_brace_is_ignored(self):
        self.assertTrue(formats.text_ok_stripped(VMT))
        self.assertFalse(formats.text_ok(VMT))

    def test_unknown_shader(self):
        put(self.u, "materials/d.vmt", b'"SomeNewShader"\n{\n}\n')
        _, problems = self.run_fmt()
        self.assertTrue(any("vmt shader somenewshader" in p for p in problems))

    def test_case_pair_and_cp1252(self):
        put(self.u, "sound/Ж.wav", b"")
        paths = [r for r, _ in formats.scan_loose(self.u)]
        self.assertEqual(formats.case_pairs(paths + ["Materials/a.VMT"]), [("materials/a.vmt", "Materials/a.VMT")])
        self.assertEqual(formats.case_pairs(paths), [])
        self.assertEqual(formats.non_cp1252(paths), ["sound/Ж.wav"])
        _, problems = self.run_fmt()
        self.assertTrue(any(p.startswith("not CP1252") for p in problems))

    def test_cli_exit_codes(self):
        self.assertEqual(formats.main(["f", self.u, self.g]), 0)
        put(self.u, "maps/a.bsp", BSP[:4] + bytes([99]) + BSP[5:])
        self.assertEqual(formats.main(["f", self.u, self.g]), 1)
        self.assertEqual(formats.main(["f"]), 2)

    def test_ttz_bad_header(self):
        put(self.u, "materials/a.ttz", b"\0\0\0\0")
        _, problems = self.run_fmt()
        self.assertIn("version .ttz bad:", problems[0])


if __name__ == "__main__":
    unittest.main()
