import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))

import devpaths

CANON = {
    1: "Overlays",
    2: "Picture mode",
}
DEAD_KEYS = (3, 4, 5, 6, 7, 8)
DROPPED = ("Frame interpolation", "Edge smoothing", "HDR", "Texture filtering", "next launch", "Controller", "Developer", "Aspect", "Settings", "Close")
BAD_ENCODING = re.compile(r"stringWithCString|initWithCString|cStringUsingEncoding|stringWithFormat:@\"[^\"]*%s")
MOJIBAKE = re.compile("Ã|â€|�|Â")
DEFAULT_NAMES = {"RT_OVL_ALL": 0, "RT_OVL_FPS": 1, "RT_OVL_OFF": 2, "CUSTOM": 4}


def exe():
    for p in (os.environ.get("M9_EXE"), devpaths.build("stack", "meson", "bloodlines-native"),
              devpaths.build("all", "meson", "bloodlines-native")):
        if p and os.path.isfile(p):
            return p
    return None


def run_t(case, *extra):
    x = exe()
    assert x, "no bloodlines-native build found; set M9_EXE"
    o = tempfile.mkdtemp(prefix="overlay-audit-")
    p = subprocess.run([x, "--m9-selftest", case, *extra, "--overlay", o], capture_output=True, text=True, errors="replace", timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    return o, p.stdout + p.stderr


def reminder_lines(o):
    out = {}
    for state in ("all", "all2"):
        with open(os.path.join(o, "m9", "overlay-%s.tsv" % state), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"⌘(\d) ([^:]+): ", line)
                if m:
                    out[int(m.group(1))] = m.group(2)
    return out


def chord_names(log):
    return {int(m.group(1)): (m.group(2), int(m.group(3))) for m in re.finditer(r"^m9 input chord(\d) name=(\S+) toggles=(\d+)", log, re.M)}


def cycles_with_engine(log):
    seqs = re.findall(r'^m9 aspect cycle .*?from_start="([^"]*)"', log, re.M)
    seqs += re.findall(r"^m9 aspect cycle from_(?:engine|custom)=(\S+)", log, re.M)
    return [s for s in seqs if "engine" in s.split()] or ([] if seqs else ["no cycle rows"])


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def body(src, name):
    m = re.search(r"^[\w \*\-]+\b" + re.escape(name) + r"\([^)]*\)\s*\{", src, re.M)
    if not m:
        return None
    depth, i = 1, m.end()
    while depth and i < len(src):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def menu_keys(src=None):
    src = src or read("runtime/toggles.c")
    block = src[src.index("} k_menu[] = {"):]
    block = block[:block.index("};")]
    return {int(d): i.lower() for i, d in re.findall(r"\{RT_TOG_([A-Z]+), (\d)\}", block)}


def dropped_in(text):
    return [w for w in DROPPED if w in text]


def bad_encoding(src):
    return BAD_ENCODING.findall(src)


def developer_info_anywhere(hud, devmenu, toggles_h):
    return [w for w, t in (("hud Developer", "Developer" in hud), ("hud dev field", "->dev" in hud), ("hud TAG_DEV", "TAG_DEV" in hud),
                           ("devmenu Developer", "Developer" in devmenu), ("devmenu toggle target", "RTDevMenuTarget" in devmenu),
                           ("devmenu cvar", "DEV_CVAR" in devmenu), ("rt_dev_enabled", "rt_dev_enabled" in toggles_h),
                           ("hud_info dev", re.search(r"const char \*[^;]*\bdev\b", toggles_h) is not None)) if t]


def dev_chord_is_dead(devmenu):
    chord = body(devmenu, "rt_dev_chord_key") or "x"
    request = body(devmenu, "rt_dev_request") or "x"
    take = body(devmenu, "rt_dev_take") or "x"
    apply = body(devmenu, "rt_dev_apply")
    return "SDL_SCANCODE" not in chord and re.search(r"return\s+0\s*;", chord) is not None and "s_pending" not in request + take and apply is not None and \
        not apply.strip()


def fps_menu_readout(hud):
    return [w for w in ("hud_fps_menu", "s_fps_menu", "s_fps_item", "rt_fps_text", "Live frame rate") if w in hud]


def dead_sdl_items_removed(devmenu):
    drop = body(devmenu, "is_dead_item") or ""
    init = body(devmenu, "rt_devmenu_init") or ""
    return '"performClose:"' in drop and '"Settings"' in drop and "drop_dead_items(bar)" in init and \
        init.index("rt_devmenu_window_items()") < init.index("drop_dead_items(bar)")


def keys_unhandled(listed, handled):
    return [int(k) for k, _ in listed if k and int(k) not in handled]


def live_consumers(fx, inp, hud):
    return {
        "PICTURE": "rt_fx_mode_on()" in (body(fx, "rt_fx_frame") or "") and "rt_toggle_get(RT_TOG_PICTURE)" in (body(fx, "rt_fx_mode_on") or ""),
        "OVERLAYS": "rt_toggle_get(RT_TOG_OVERLAYS)" in hud[hud.index("hud_current(void)"):hud.index("int rt_hud_live_lines")],
    }


def toggle_table():
    src = read("runtime/toggles.c")
    block = src[src.index("k_tog[RT_TOG_COUNT]"):]
    block = block[:block.index("};")]
    rows = []
    for m in re.finditer(r'\{"(\w+)", "([^"]+)", (\d+), \{([^}]*)\}, \{([^}]*)\}, (\w+), (\w+)\}', block):
        val = re.findall(r'"([^"]*)"', m.group(4))
        lab = re.findall(r'"([^"]*)"', m.group(5))
        d = [DEFAULT_NAMES.get(x, None) if x in DEFAULT_NAMES else int(x) for x in (m.group(6), m.group(7))]
        rows.append({"name": m.group(1), "title": m.group(2), "n": int(m.group(3)), "val": val, "lab": lab, "def": d[0], "def_player": d[1]})
    return rows


def toggle_ids():
    src = read("runtime/toggles.h")
    return re.findall(r"RT_TOG_([A-Z]+),", src[src.index("enum {"):src.index("RT_TOG_COUNT")])


def masks():
    ids = toggle_ids()
    out = {}
    pat = re.compile(r"(?:atomic_store\(&s_mask\[RT_TOG_([A-Z]+)\],\s*(\d+)u?\)|rt_toggle_set_available\(RT_TOG_([A-Z]+),\s*(\d+)u?)")
    files = glob.glob(os.path.join(REPO, "runtime", "*.[cm]")) + glob.glob(os.path.join(REPO, "shims", "*.c"))
    for p in files:
        if p.endswith("_selftest.c"):
            continue
        with open(p, encoding="utf-8") as f:
            text = f.read()
        for m in pat.finditer(text):
            tid, v = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if tid in ids and int(v):
                out[tid] = int(v)
    return out


def listed_rows():
    src = read("runtime/hud.m")
    return re.findall(r'\{@"(?:⌘(\d) )?",\s*@"([^"]+)"', src)


def handled_keys():
    keys = set()
    chord = body(read("runtime/toggles.c"), "rt_toggle_chord")
    if chord and "k_menu" in chord and "SDL_SCANCODE_1 + (uint32_t)k_menu[i].digit - 1" in chord:
        keys |= set(menu_keys())
    return keys


class OverlayAudit(unittest.TestCase):
    def test_a_every_listed_key_has_a_handler(self):
        handled = handled_keys()
        missing = [int(k) for k, _ in listed_rows() if k and int(k) not in handled]
        self.assertEqual(missing, [])

    def test_a_chords_are_one_and_two(self):
        self.assertEqual(menu_keys(), {1: "overlays", 2: "picture"})
        self.assertEqual({int(k): label for k, label in listed_rows() if k}, CANON)

    def test_t_reminder_lines_use_the_approved_labels(self):
        o, _ = run_t("overlay")
        rows = reminder_lines(o)
        self.assertEqual(set(rows), set(CANON), rows)
        for key, label in rows.items():
            self.assertEqual(label, CANON[key], key)

    def test_t_every_listed_key_drives_its_toggle(self):
        o, _ = run_t("overlay")
        _, log = run_t("input")
        keys = chord_names(log)
        names = menu_keys()
        for key in reminder_lines(o):
            self.assertIn(key, keys, "key %d" % key)
            self.assertEqual(keys[key], (names[key], 1), "key %d" % key)

    def test_t_engine_is_not_in_the_aspect_cycle(self):
        _, log = run_t("aspect")
        self.assertEqual(cycles_with_engine(log), [])

    def test_t_catches_engine_back_in_the_cycle(self):
        bad = 'm9 aspect cycle desktop=2056x1329 fit_in_cycle=1 from_start="21:9 engine 16:9 fit"\nm9 aspect cycle from_engine=16:9\n'
        self.assertEqual(cycles_with_engine(bad), ["21:9 engine 16:9 fit"])
        self.assertEqual(cycles_with_engine("m9 aspect cycle from_engine=engine\n"), ["engine"])
        self.assertEqual(cycles_with_engine(""), ["no cycle rows"])

    def test_a_dispatch_reaches_the_toggle_handler(self):
        src = read("runtime/input.c")
        for call in ("rt_toggle_chord(", "rt_toggle_request("):
            self.assertIn(call, src)

    def test_b_handlers_are_not_stubs(self):
        for fn in ("rt_toggle_request", "rt_toggles_apply", "rt_toggle_chord"):
            b = body(read("runtime/toggles.c"), fn)
            self.assertIsNotNone(b, fn)
            self.assertTrue(re.sub(r"rt_log\([^;]*\);|\breturn\s*;", "", b).strip(), fn + " is a stub")

    def test_c_every_handled_key_is_listed(self):
        listed = {int(k) for k, _ in listed_rows() if k}
        self.assertEqual(sorted(handled_keys() - listed), [])

    def test_c_unavailable_rows_are_left_out_of_the_reminder(self):
        hud = read("runtime/hud.m")
        lines = body(hud, "hud_lines") or hud[hud.index("hud_lines("):hud.index("hud_line_top")]
        self.assertRegex(lines, r'strcmp\([^)]*label,\s*"unavailable"\)')
        label = body(read("runtime/toggles.c"), "rt_toggle_label")
        self.assertRegex(label, r's_mask\[id\][^?]*\?\s*"unavailable"|!atomic_load\(&s_mask\[id\]\) \? "unavailable"')
        live = body(hud, "hud_live_info")
        for tid in menu_keys().values():
            self.assertIn("RT_TOG_" + tid.upper(), live)
        titles = {t["title"] for t in toggle_table()}
        self.assertTrue({label for _, label in listed_rows()} <= titles)

    def test_c_available_toggles_are_known(self):
        self.assertTrue({"OVERLAYS", "ASPECT"} <= set(masks()))

    def test_c_pad_input_cannot_reach_a_toggle(self):
        pad = read("runtime/pad.c")
        self.assertNotIn("rt_toggle_request", pad)
        self.assertNotIn("rt_toggle_chord", pad)
        self.assertRegex(pad, r"SDL_SCANCODE_LGUI \|\| k->sdl == SDL_SCANCODE_RGUI")

    def test_c_picture_mask_follows_metalfx_support(self):
        fx = read("runtime/fx.m")
        self.assertRegex(body(fx, "probe"), r"s_sup_spatial = \[MTLFXSpatialScalerDescriptor supportsDevice:s_dev\]")
        av = body(fx, "avail")
        self.assertRegex(av, r"if \(s_sup_spatial\) rt_toggle_set_available\(RT_TOG_PICTURE, 5u \| \(RT_ENGINE_OVERRIDE \? 1u << RT_PIC_NATIVE : 0u\), \"\"\);"
                             r"\s*else rt_toggle_set_available\(RT_TOG_PICTURE, 0, s_why_sp\);")
        self.assertEqual(masks().get("PICTURE"), 5)
        self.assertRegex(read("runtime/fx.h"), r"extern const int rt_engine_override;\s*#define RT_ENGINE_OVERRIDE rt_engine_override\b")
        self.assertRegex(read("runtime/g2b_engine.c"), r"const int rt_engine_override __attribute__\(\(weak\)\) = 0;")
        self.assertRegex(read("tools/recomp/build_all.py"),
                         r"if not a\.no_engine_aspect:\s*f\.write\(\"const int rt_engine_override = 1;\\n\"\)")
        picture = toggle_table()[toggle_ids().index("PICTURE")]
        self.assertEqual(picture["title"], CANON[2])
        self.assertEqual(picture["val"], ["metalfx", "native", "off"])
        self.assertEqual((picture["def"], picture["def_player"]), (2, 2))

    def test_c_native_step_is_outside_the_runtime_cycle(self):
        self.assertRegex(body(read("runtime/toggles.c"), "rt_toggles_apply"), r"if \(id == RT_TOG_PICTURE\) mask &= ~\(1u << RT_PIC_NATIVE\);")

    def test_t_picture_row_appears_only_when_metalfx_works(self):
        o, log = run_t("fx")
        m = re.search(r"^m9 fx support spatial=([01])", log, re.M)
        self.assertIsNotNone(m, log)
        cyc = re.search(r'^m9 fx cycle mask=(\d+) start=(\S+) seq="([^"]*)" back=([01]) native=([01])', log, re.M)
        self.assertIsNotNone(cyc, log)
        if m.group(1) == "1":
            want = ("7", "metalfx off", "1", "1") if cyc.group(5) == "1" else ("5", "metalfx off", "1", "0")
            self.assertEqual((cyc.group(1), cyc.group(3), cyc.group(4), cyc.group(5)), want)
        else:
            self.assertEqual((cyc.group(1), cyc.group(4)), ("0", "1"))
        self.assertEqual(cyc.group(2), "off")
        o, _ = run_t("menu")
        with open(os.path.join(o, "m9", "menu.tsv"), encoding="utf-8") as f:
            item = [l.split("\t") for l in f if l.startswith("item\tPicture mode")]
        self.assertEqual(len(item), 1, item)
        self.assertEqual(item[0][4], m.group(1))

    def test_d_labels_match_the_approved_text(self):
        rows = {int(k): label for k, label in listed_rows() if k}
        self.assertEqual(rows, CANON)
        titles = {t["name"]: t["title"] for t in toggle_table()}
        for key, name in menu_keys().items():
            self.assertEqual(titles[name], CANON[key])

    def test_e_aspect_starts_at_4_3(self):
        aspect = toggle_table()[toggle_ids().index("ASPECT")]
        self.assertEqual((aspect["def"], aspect["def_player"]), (DEFAULT_NAMES["CUSTOM"], DEFAULT_NAMES["CUSTOM"]))
        self.assertRegex(read("runtime/toggles.c"), r'#define DEF_CUSTOM "4:3"\n')
        self.assertNotIn("4:3", aspect["val"])

    def test_e_every_cycle_returns_to_its_start(self):
        avail = masks()
        bad = []
        for tid, t in zip(toggle_ids(), toggle_table()):
            self.assertEqual(len(t["val"]), t["n"])
            self.assertEqual(len(t["lab"]), t["n"])
            self.assertEqual(len(set(t["val"])), t["n"])
            mask = avail.get(tid)
            if not mask:
                continue
            for start in {t["def"], t["def_player"]}:
                if start >= t["n"]:
                    first = next(((t["n"] - 1 + k) % t["n"] for k in range(1, t["n"] + 1) if mask >> ((t["n"] - 1 + k) % t["n"]) & 1), None)
                    if first is None:
                        bad.append((t["name"], "custom start has no step"))
                    continue
                cur, seen = start, []
                for _ in range(t["n"] + 1):
                    nxt = next(((cur + k) % t["n"] for k in range(1, t["n"] + 1) if mask >> ((cur + k) % t["n"]) & 1), cur)
                    seen.append(nxt)
                    cur = nxt
                    if cur == start:
                        break
                if cur != start:
                    bad.append((t["name"], t["val"][start]))
        self.assertEqual(bad, [])


class CleanMenu(unittest.TestCase):
    def test_a_no_dropped_feature_or_next_launch_text_in_the_menu_sources(self):
        hud = read("runtime/hud.m")
        shown = [t["title"] for t in toggle_table() if t["name"] in menu_keys().values()] + \
                [x for t in toggle_table() if t["name"] in menu_keys().values() for x in t["lab"]]
        labels = re.findall(r'@"([^"]*)"', hud) + shown
        self.assertEqual([x for x in labels if dropped_in(x)], [])
        for rel in ("runtime/toggles.h", "runtime/fx.m"):
            self.assertNotRegex(read(rel), r"RT_TOG_(INTERP|SMOOTHING|HDR|CONTROLLER|ANISO)")

    def test_a_toggle_set_is_exactly_the_kept_ones(self):
        self.assertEqual(toggle_ids(), ["OVERLAYS", "PICTURE", "ASPECT"])
        self.assertEqual([t["name"] for t in toggle_table()], ["overlays", "picture", "aspect"])
        self.assertEqual(set(menu_keys().values()), {"overlays", "picture"})
        self.assertNotIn("MENU_ASPECT", read("runtime/toggles.c"))

    def test_a_dropped_keys_are_still_parsed_and_ignored(self):
        self.assertRegex(read("runtime/toggles.c"), r'k_dropped\[\] = \{"interp", "smoothing", "hdr", "controller", "aniso", "mouse"\}')

    def test_a_controller_and_aniso_have_no_runtime_effect_left(self):
        self.assertNotIn("RT_TOG_", read("runtime/pad.c"))
        cfg = body(read("runtime/toggles.c"), "rt_dxvk_config")
        self.assertNotIn("Anisotropy", cfg)
        self.assertNotIn("RT_TOG_", cfg)
        self.assertNotIn("rt_pad_kind", body(read("runtime/toggles.c"), "rt_toggle_label"))

    def test_a_menu_rows_are_exactly_the_live_rows(self):
        self.assertEqual(sorted(k for k, _ in listed_rows() if k), sorted(str(k) for k in CANON))

    def test_a_every_kept_row_has_a_live_consumer(self):
        got = live_consumers(read("runtime/fx.m"), read("runtime/input.c"), read("runtime/hud.m"))
        self.assertEqual([k for k, v in got.items() if not v], [])

    def test_a_no_developer_info_in_any_mode(self):
        self.assertEqual(developer_info_anywhere(read("runtime/hud.m"), read("runtime/devmenu.c"), read("runtime/toggles.h")), [])
        self.assertTrue(dev_chord_is_dead(read("runtime/devmenu.c")))

    def test_a_no_fps_readout_in_the_menu_bar(self):
        self.assertEqual(fps_menu_readout(read("runtime/hud.m")), [])

    def test_a_dead_sdl_items_are_removed(self):
        self.assertTrue(dead_sdl_items_removed(read("runtime/devmenu.c")))
        self.assertNotIn("performClose", read("runtime/winstate.h"))

    def test_a_full_screen_hud_sits_below_the_menu_bar(self):
        hud = read("runtime/hud.m")
        place = hud[hud.index("- (void)place"):hud.index("@end", hud.index("- (void)place"))]
        self.assertIn("NSWindowStyleMaskFullScreen", place)
        self.assertIn("menuBarHeight", place)
        self.assertIn("safeAreaRect", place)

    def test_a_text_is_built_with_utf8_strings(self):
        for rel in ("runtime/hud.m", "runtime/toggles.c", "runtime/devmenu.c"):
            self.assertEqual(bad_encoding(read(rel)), [], rel)
        self.assertIn("stringWithUTF8String:label", read("runtime/hud.m"))

    def test_t_live_rows_change_in_a_running_instance(self):
        for extra in ((), ("--player",)):
            _, log = run_t("live", *extra)
            rows = dict(re.findall(r"^m9 live (\S+) .*?(?:changed|ok)=([01])", log, re.M))
            for name in menu_keys().values():
                self.assertEqual(rows.get(name), "1", (name, log))
            for gone in ("developer", "controller", "aspect"):
                self.assertNotIn(gone, rows)
            self.assertEqual(rows.get("dead-keys"), "1")
            self.assertIn("m9 live result bad=0", log)

    def test_t_menu_and_overlay_have_no_developer_info_in_any_mode(self):
        for extra in ((), ("--player",)):
            o, _ = run_t("menu", *extra)
            with open(os.path.join(o, "m9", "menu.tsv"), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(dropped_in(text), [])
            self.assertNotRegex(text, r"menu\t\d+(\+\d+)? FPS")
            o, _ = run_t("overlay", *extra)
            for st in ("all", "all2"):
                with open(os.path.join(o, "m9", "overlay-%s.tsv" % st), encoding="utf-8") as f:
                    text = f.read()
                self.assertNotIn("Developer", text)
                self.assertNotRegex(text, r"⌘[4-8]")

    def test_t_texts_are_clean(self):
        o, _ = run_t("overlay")
        m, _ = run_t("menu")
        for rel in ("overlay-all.tsv", "overlay-all2.tsv"):
            with open(os.path.join(o, "m9", rel), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(dropped_in(text), [])
            self.assertIsNone(MOJIBAKE.search(text))
        with open(os.path.join(m, "m9", "menu.tsv"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(dropped_in(text), [])
        self.assertIsNone(MOJIBAKE.search(text))

    def test_t_every_menu_key_works_and_the_rest_do_nothing(self):
        _, log = run_t("input")
        keys = chord_names(log)
        for key in menu_keys():
            self.assertEqual(keys[key][1], 1, key)
        for key in DEAD_KEYS:
            self.assertEqual(keys[key][1], 0, key)
            dev = re.search(r"^m9 input chord%d .* dev=(\d)" % key, log, re.M)
            self.assertEqual(dev and dev.group(1), "0", key)


class Mutations(unittest.TestCase):
    def test_each_check_fails_on_its_mutation(self):
        hud, toggles, devmenu, toggles_h = read("runtime/hud.m"), read("runtime/toggles.c"), read("runtime/devmenu.c"), read("runtime/toggles.h")
        self.assertEqual([x for x in re.findall(r'@"([^"]*)"', hud) if dropped_in(x)], [])
        self.assertTrue(dropped_in(hud.replace('@"Overlays"', '@"Frame interpolation"')))
        self.assertTrue(dropped_in(hud.replace('@"Overlays"', '@"HDR"')))
        self.assertTrue(dropped_in(hud.replace('@"Overlays"', '@"Controller type"')))
        self.assertTrue(dropped_in(hud.replace('@"Overlays"', '@"Aspect"')))
        self.assertTrue(dropped_in(hud.replace('@"Overlays"', '@"Developer info"')))
        self.assertTrue(dropped_in(toggles.replace('"MetalFX", "native"', '"MetalFX", "native (next launch)"')))
        self.assertTrue(dropped_in("Texture filtering: 16×"))
        self.assertFalse(bad_encoding(hud))
        self.assertTrue(bad_encoding(hud.replace("stringWithUTF8String:label", "stringWithCString:label encoding:NSMacOSRomanStringEncoding")))
        self.assertTrue(MOJIBAKE.search("Texture filtering: 16Ã—"))
        self.assertEqual(developer_info_anywhere(hud, devmenu, toggles_h), [])
        self.assertTrue(developer_info_anywhere(hud + '@"Developer info"', devmenu, toggles_h))
        self.assertTrue(developer_info_anywhere(hud.replace("i->picture", "i->dev"), devmenu, toggles_h))
        self.assertTrue(developer_info_anywhere(hud, devmenu + 'nsstr("Developer info")', toggles_h))
        self.assertTrue(developer_info_anywhere(hud, devmenu, toggles_h + "int rt_dev_enabled(void);"))
        self.assertTrue(dev_chord_is_dead(devmenu))
        self.assertFalse(dev_chord_is_dead(devmenu.replace("(void)scancode;\n    return 0;", "return scancode == SDL_SCANCODE_4;")))
        self.assertFalse(dev_chord_is_dead(devmenu.replace("void rt_dev_apply(void) {}", "void rt_dev_apply(void) { rt_dev_take(); }")))
        self.assertEqual(fps_menu_readout(hud), [])
        self.assertTrue(fps_menu_readout(hud + "static NSMenuItem *hud_fps_menu(void);"))
        self.assertTrue(dead_sdl_items_removed(devmenu))
        self.assertFalse(dead_sdl_items_removed(devmenu.replace('"performClose:"', '"performZoom:"')))
        self.assertFalse(dead_sdl_items_removed(devmenu.replace('"Settings"', '"Preferences"')))
        self.assertFalse(dead_sdl_items_removed(devmenu.replace("    int dropped = drop_dead_items(bar);\n", "")))
        keys = menu_keys(toggles)
        self.assertEqual(keys, {1: "overlays", 2: "picture"})
        self.assertNotEqual(menu_keys(toggles.replace("{RT_TOG_PICTURE, 2}", "{RT_TOG_PICTURE, 3}")), keys)
        self.assertNotEqual(menu_keys(toggles.replace("{RT_TOG_OVERLAYS, 1}", "{RT_TOG_OVERLAYS, 7}")), keys)
        self.assertEqual(keys_unhandled([("1", "x"), ("2", "y")], set(keys)), [])
        self.assertEqual(keys_unhandled([("6", "Controller type")], set(keys)), [6])
        self.assertEqual(keys_unhandled([(str(k), "x") for k in keys], set(list(keys)[1:])), [list(keys)[0]])
        fx, inp = read("runtime/fx.m"), read("runtime/input.c")
        self.assertEqual([k for k, v in live_consumers(fx, inp, hud).items() if not v], [])
        self.assertFalse(live_consumers(fx.replace("int on = rt_fx_mode_on();", "int on = 1;"), inp, hud)["PICTURE"])
        self.assertFalse(live_consumers(fx, inp, hud.replace("int s = rt_toggle_get(RT_TOG_OVERLAYS);", "int s = 0;"))["OVERLAYS"])


if __name__ == "__main__":
    unittest.main()
