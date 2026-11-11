import argparse
import concurrent.futures
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))

import devpaths

PY = [sys.executable, "-I", "-B"]
GAME = devpaths.GAME
CRASHES = os.path.expanduser("~/Library/Logs/DiagnosticReports")
ALLOWED = {"simd-trap", "fault-as-original", "hint"}
FALLBACK_PINNED = os.path.join(HERE, "xflags_fallbacks.txt")
RUN_OUTPUTS = {"summary.json", "stubs.tsv", "build.log", "ccache.txt"}
ASPECT_SET = os.path.join(REPO, "tools", "m9", "aspect.toml")


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, **kw).returncode


def out_of(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def translate_one(cmd, d):
    os.makedirs(d, exist_ok=True)
    extra = os.path.join(d, "extra-entries.txt")
    if not os.path.exists(extra):
        open(extra, "w").close()
    t = time.time()
    p = subprocess.run(cmd + ["--out", d, "--extra-entries", extra], capture_output=True, text=True)
    last = ((p.stdout + p.stderr).strip().splitlines() or [""])[-1]
    return p.returncode, last, time.time() - t


def clear_patch_state(out, modules):
    shutil.rmtree(os.path.join(out, "patched"), ignore_errors=True)
    if os.path.exists(os.path.join(out, "up.sha256")):
        os.remove(os.path.join(out, "up.sha256"))
    for name in modules:
        extra = os.path.join(out, name, "extra-entries.txt")
        if os.path.exists(extra) and os.path.getsize(extra):
            for p in (extra, os.path.join(out, name, "gen", "recomp_stubs_unresolved.c"), os.path.join(out, name, "inputs.sha256")):
                if os.path.exists(p):
                    os.remove(p)


UNICORN_MODES = ("conform", "xflags", "mmx")


def jobs_for(out, plan, patched=()):
    jobs = []
    for name, m in sorted(plan["modules"].items(), key=lambda kv: -kv[1]["code_size"]):
        src = os.path.join(out, "patched", m["path"]) if name in patched else os.path.join(GAME, m["path"])
        cmd = PY + [os.path.join(HERE, "translate.py"), src, "--plan",
                    os.path.join(out, "plan.json"), "--module", name]
        if name in plan["build"]["chunk1"]:
            cmd += ["--chunk", "1"]
        jobs.append((name, cmd, os.path.join(out, name)))
    st = os.path.join(out, "selftest")
    for name, layout in (("conform", "layout.toml"), ("simd", "layout.toml"),
                         ("m3", "m3-layout.toml"), ("xflags", "xflags-layout.toml")):
        jobs.append((name, PY + [os.path.join(HERE, "translate.py"), os.path.join(st, f"{name}.dll"), "--layout",
                                 os.path.join(st, layout), "--module", name], os.path.join(st, name)))
    return jobs


def translate_all(jobs, n):
    bad = []
    with concurrent.futures.ThreadPoolExecutor(n) as pool:
        futs = {pool.submit(translate_one, cmd, d): name for name, cmd, d in jobs}
        for f in concurrent.futures.as_completed(futs):
            rc, last, secs = f.result()
            print(f"  {secs:6.1f}s {last}" if rc == 0 else f"  FAILED {futs[f]}: {last}", flush=True)
            if rc:
                bad.append(futs[f])
    return bad


def disallowed(reports):
    return [f"{r['module']} {u['va']} {u['mnemonic']} ({u['category']})"
            for r in reports for u in r["untranslated"] if u["category"] not in ALLOWED]


def snapshot(out):
    snap = {}
    for root, dirs, files in os.walk(out):
        dirs[:] = [d for d in dirs if d not in ("meson", "ghidra")]
        for f in files:
            if f not in RUN_OUTPUTS:
                p = os.path.join(root, f)
                snap[p] = os.stat(p).st_mtime_ns
    return snap


def meson_setup(src, build, options):
    info = os.path.join(build, "meson-info", "meson-info.json")
    if os.path.exists(os.path.join(build, "build.ninja")) and os.path.exists(info):
        if os.path.realpath(json.load(open(info))["directories"]["source"]) == os.path.realpath(src):
            return 0
        return run(["meson", "setup", "--wipe", build, src] + options, stdout=subprocess.DEVNULL)
    return run(["meson", "setup", build, src] + options, stdout=subprocess.DEVNULL)


def source_info(repo):
    if os.path.exists(os.path.join(repo, ".git")):
        rc, o = out_of(["git", "-C", repo, "rev-parse", "HEAD"])
        if rc == 0 and o:
            rc, st = out_of(["git", "-C", repo, "status", "--porcelain"])
            return {"sha": o, "from": "git", "dirty": rc == 0 and bool(st)}
    for d in (repo, os.path.join(repo, "..")):
        try:
            with open(os.path.join(d, "SOURCE")) as f:
                sha = f.read().strip()
        except OSError:
            continue
        if sha:
            return {"sha": sha, "from": "SOURCE"}
    return None


def source_sha(repo):
    info = source_info(repo)
    return info and info["sha"]


def prebuilt_options(prebuilt):
    return ["-Dprebuilt=true", f"-Dprebuilt_prefix={prebuilt}"] if prebuilt else []


def meson_build(src, build, options, log):
    rc = meson_setup(src, build, options)
    if rc:
        return rc, ""
    p = subprocess.run(["ninja", "-C", build], capture_output=True, text=True)
    open(log, "a").write(p.stdout + p.stderr)
    return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]


def du(paths):
    return sum(os.path.getsize(p) for p in paths if os.path.isfile(p))


def link_checks(x):
    checks = []
    rc, o = out_of(["file", x])
    checks.append(("file", "arm64" in o and "x86_64" not in o and "i386" not in o, o))
    rc, o = out_of(["lipo", "-info", x])
    checks.append(("lipo -info", "Non-fat file" in o and o.endswith("is architecture: arm64"), o))
    rc, o = out_of(["otool", "-L", x])
    libs = [l.strip().split(" ")[0] for l in o.splitlines()[1:]]
    sdl = [l for l in libs if re.fullmatch(r".*/libSDL3\.0\.dylib", l)]
    av = [l for l in libs if re.fullmatch(r".*/lib(avformat|avcodec|avutil)\.\d+\.dylib", l)]
    apple = [l for l in libs if re.fullmatch(r"/System/Library/Frameworks/(AppKit|CoreFoundation|CoreGraphics|CoreText|Foundation|ImageIO|Metal|MetalFX|QuartzCore)\.framework/.*|/usr/lib/libobjc\.A\.dylib", l)]
    checks.append(("otool -L", sorted(libs) == sorted(["/usr/lib/libSystem.B.dylib"] + sdl + av + apple) and len(sdl) <= 1
                   and len({os.path.basename(l).split(".")[0] for l in av}) == len(av), ", ".join(libs)))
    rc, o = out_of(["nm", "-m", "-u", x])
    lines = o.splitlines()
    foreign = [l for l in lines if not re.search(r"\(from (libSystem|libSDL3|libavformat|libavcodec|libavutil|libobjc|AppKit|CoreFoundation|CoreGraphics|CoreText|Foundation|ImageIO|Metal|MetalFX|QuartzCore)(\.\d+)?\)$", l)]
    checks.append(("nm -m -u", rc == 0 and not foreign, f"{len(lines)} undefined, all from libSystem, libSDL3, FFmpeg or the Apple frameworks"
                   if not foreign else "; ".join(foreign[:5])))
    return checks


def probes(x):
    before = set(glob.glob(os.path.join(CRASHES, "bloodlines-native*")))
    res = {}
    rc, o = out_of([x])
    m = re.search(r"mapped (\d+) images.*?(\d+) code, (\d+) data, (\d+) shim, (\d+) stub, (\d+) cell, (\d+) unbound", o)
    res["main"] = {"exit": rc, "line": o.splitlines()[-1] if o else "",
                   "ok": rc == 0 and bool(m) and m.group(1) == "23" and m.group(7) == "0"}
    if m:
        res["main"]["slots"] = dict(zip(("code", "data", "shim", "stub", "cell", "unbound"), map(int, m.groups()[1:])))
    rc, o = out_of([x, "--list-imports"])
    stubs = [l.split(" ")[0] for l in o.splitlines() if l.endswith(" stub")]
    dll, name = stubs[0].split("!", 1) if stubs else ("?", "?")
    rc, o = out_of([x, "--probe-stub", dll, name, "0x11111111", "0x22222222", "0x33333333", "0x44444444"])
    res["probe_stub"] = {"exit": rc, "line": o, "import": f"{dll}!{name}", "ok": rc == 10 and f"{dll}!{name}" in o and all(
        w in o for w in ("0x11111111", "0x22222222", "0x33333333", "0x44444444"))}
    rc, o = out_of([x, "--probe-read", "0xFFFFFFFE"])
    res["probe_read"] = {"exit": rc, "line": o, "ok": rc == 4 and (
        "past arena end (arena offset 0x" in o or "(arena offset 0x0FFFFFFFE)" in o)}
    rc, o = out_of([x, "--list-imports"])
    res["imports"] = o.splitlines()
    new = set(glob.glob(os.path.join(CRASHES, "bloodlines-native*"))) - before
    res["crash_reports"] = sorted(new)
    return res


def harness(out, skip=()):
    st = os.path.join(out, "selftest")
    res = []
    for name, mode in (("conform", "conform"), ("simd", "trap"), ("simd", "mmx"),
                       ("m3", "cases"), ("xflags", "xflags")):
        if mode in skip:
            continue
        d = os.path.join(st, name)
        tool = (os.path.join(HERE, "test_mmx.py") if mode == "mmx"
                else os.path.join(REPO, "harness", "m3.py" if name == "m3" else "run.py"))
        rc, o = out_of(PY + [tool, mode, "--lib",
                             os.path.join(d, "meson", f"lib{name}.dylib"), "--module", os.path.join(d, "module.json")]
                       + (["--game", GAME] if name == "m3" else []))
        print(o)
        res.append({"mode": mode, "exit": rc, "lines": o.splitlines()})
    return res


def inventory_check(plan, inv):
    names = set()
    for f in glob.glob(os.path.join(inv, "imports-*.txt")):
        for line in open(f, encoding="utf-8"):
            if "!" in line:
                dll, name = line.strip().split("!", 1)
                names.add((dll.lower(), name))
    return [f"{s['dll']}!{s['name']}" for s in plan["stubs"] if (s["dll"].lower(), s["name"]) not in names]


SHIM_ROW = re.compile(r'\{"([^"]+)", "([^"]+)", (\d+), (\w+)')


def shim_pops():
    rows = {}
    for f in sorted(glob.glob(os.path.join(REPO, "shims", "*.c"))):
        for dll, name, pop, fn in SHIM_ROW.findall(open(f, encoding="utf-8").read()):
            if fn != "0":
                rows[(dll.lower(), name)] = (int(pop), os.path.basename(f))
    return rows


def pop_check(plan):
    rows = shim_pops()
    bad = []
    for s in plan["stubs"]:
        r = rows.get((s["dll"].lower(), s["name"]))
        if r and s["pop"] is not None and 4 * r[0] != s["pop"]:
            bad.append(f"{s['dll']}!{s['name']} pops {4 * r[0]} in {r[1]}, plan {s['pop']} ({s['pop_source']})")
    return bad


def lift_counts(reports):
    total = {}
    for r in reports:
        for cat, kinds in (r.get("lift_counts") or {}).items():
            for k, v in kinds.items():
                total.setdefault(cat, {}).setdefault(k, 0)
                total[cat][k] += v
    return total


def pinned_fallbacks():
    with open(FALLBACK_PINNED) as f:
        return [l for l in f.read().splitlines() if l]


def cross_flags(reports):
    c = {r["module"]: r["cross_flags"] for r in reports}
    rows = [dict(f, module=m) for m, x in c.items() for f in x["fallbacks"]]
    actual = sorted(f"{r['module']} {r['va']} {r['category']}" for r in rows)
    diff = list(difflib.unified_diff(pinned_fallbacks(), actual, "xflags_fallbacks.txt", "this build",
                                     lineterm="", n=0))
    imported = {}
    for r in reports:
        for i in r["imports"]:
            if i["bind"] == "code":
                imported.setdefault((i["exporter"], f"0x{i['va']:08X}"), set()).add(r["module"])
    exported = []
    for r in reports:
        names = {row["va"]: row["export"] for row in r["rows"] if row["export"]}
        x = r["cross_flags"]
        for kind in ("consumers", "flag_ret"):
            for va, flags in x[kind].items():
                if va in names:
                    exported.append({"module": r["module"], "va": va, "export": names[va], "kind": kind, "flags": flags,
                                     "imported_by": sorted(imported.get((r["module"], va), ()))})
    return {
        "fallbacks": len(rows),
        "by_category": {k: sum(r["category"] == k for r in rows) for k in sorted({r["category"] for r in rows})},
        "fallback_diff": diff,
        "fallback_list": rows,
        "exported": exported,
        "exported_imported": [e for e in exported if e["imported_by"]],
        "straight_line_consumers": sum(x["straight_line_consumers"] for x in c.values()),
        "consumers": sum(len(x["consumers"]) for x in c.values()),
        "direct_flag_ret": sum(x["direct_flag_ret"] for x in c.values()),
        "flag_ret": sum(len(x["flag_ret"]) for x in c.values()),
        "publish_sites": sum(len(x["publish"]) for x in c.values()),
        "cf_loads": sum(len(x["cf_loads"]) for x in c.values()),
        "missing": [v for x in c.values() for v in x["missing"]],
    }


def ccache(*args):
    if not shutil.which("ccache"):
        return ""
    return out_of(["ccache", *args])[1]


def image_word(out, plan, module, va):
    with open(os.path.join(out, module, "image.bin"), "rb") as f:
        f.seek(va - plan["modules"][module]["base"])
        return int.from_bytes(f.read(4), "little")


def summarize(out, plan, reports):
    mods = []
    for r in reports:
        mods.append({"module": r["module"], "seeded": r["functions"], "translated": r["translated"],
                     "skipped": r["skipped"], "intrinsic": r["intrinsic"], "untranslated": r["untranslated_counts"],
                     "seeds": r["seeds"], "data_imports": len(r.get("data_imports", [])),
                     "ordinal_imports": r.get("ordinal_imports", 0), "forwarders": len(r.get("forwarders", {})),
                     "relocated_words_outside_image": r["relocated_words_outside_image"],
                     "unbounded_jump_tables": len(r["unbounded_jump_tables"]),
                     "writes_into_code": len(r["writes_into_code"]),
                     "stubs_used": sum(s["bind"] == "stub" for s in r["imports"])})
        assert r["translated"] + r["skipped"] == r["functions"] == len(r["rows"]), r["module"]
        assert all(row["reason"] for row in r["rows"] if row["status"] == "skipped"), r["module"]
    kinds = {}
    for r in reports:
        for u in r["untranslated"]:
            k = (u["category"], u["mnemonic"].split(" ")[0])
            kinds.setdefault(k, {}).setdefault(r["module"], 0)
            kinds[k][r["module"]] += 1
    return {
        "modules": mods,
        "totals": {k: sum(m[k] for m in mods) for k in ("seeded", "translated", "skipped", "intrinsic")},
        "untranslated_kinds": [{"category": c, "kind": k, "per_module": v, "count": sum(v.values())}
                               for (c, k), v in sorted(kinds.items())],
        "skipped": [{"module": r["module"], "va": row["va"], "reason": row["reason"]}
                    for r in reports for row in r["rows"] if row["status"] == "skipped"],
        "data_imports": [{"importer": r["module"], "slot": f"0x{s['iat']:08X}", "name": f"{s['dll']}!{s['name']}",
                          "va": f"0x{s['va']:08X}", "exporter": s["exporter"],
                          "image_word_matches": image_word(out, plan, r["module"], s["iat"])
                          == plan["modules"][s["exporter"]]["exports"][s["name"]]["va"]}
                         for r in reports for s in r.get("data_imports", [])],
        "stubs": len(plan["stubs"]), "cells": len(plan["cells"]),
        "init_order": plan["init_order"], "cycle": plan["cycle"],
        "tls": {n: m["tls"] for n, m in plan["modules"].items() if m["tls"]},
    }


def main(argv=None):
    global FALLBACK_PINNED, GAME
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=devpaths.build("all"))
    ap.add_argument("--layout", default=os.path.join(REPO, "layout.toml"))
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--inventory", default=os.path.join(REPO, "docs", "inventory"))
    ap.add_argument("--patch-set")
    ap.add_argument("--no-engine-aspect", action="store_true")
    ap.add_argument("--up-ini")
    ap.add_argument("--fallbacks")
    ap.add_argument("--game", default=GAME)
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--prebuilt", help="folder holding prebuilt libraries (lib/, include/) used instead of the Homebrew prefix")
    ap.add_argument("--pgo-profile", help="clang .profdata to build the game with profile-guided optimisation (-Db_pgo=use)")
    a = ap.parse_args(argv)
    pre_opts = prebuilt_options(os.path.abspath(os.path.expanduser(a.prebuilt)) if a.prebuilt else None)
    GAME = os.path.abspath(os.path.expanduser(a.game))
    if a.fallbacks:
        FALLBACK_PINNED = os.path.abspath(a.fallbacks)
    t0 = time.time()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    before = snapshot(out)
    log = os.path.join(out, "build.log")
    open(log, "w").close()
    if run(PY + [os.path.join(HERE, "plan.py"), a.layout, "--game", GAME, "--out", out]):
        print("translate error: layout or plan")
        return 2
    if (run(PY + [os.path.join(HERE, "test_discover.py")]) or
            run(PY + [os.path.join(HERE, "test_plan.py"), os.path.join(out, "selftest")]) or
            run(PY + [os.path.join(HERE, "test_xflags.py"), os.path.join(out, "selftest", "xflagtest")]) or
            run(PY + [os.path.join(REPO, "harness", "m3.py"), "write", "--out", os.path.join(out, "selftest")])):
        print("translate error: self-check failed")
        return 2
    plan = json.load(open(os.path.join(out, "plan.json")))
    t1 = time.time()
    patched = ()
    clear_patch_state(out, plan["modules"])
    sets = ([] if a.no_engine_aspect else [ASPECT_SET]) + ([os.path.abspath(a.patch_set)] if a.patch_set else [])
    if sets:
        cmd =PY + [os.path.join(HERE, "uppatch.py")] + [x for s in sets for x in ("--patch-set", s)] + ["--game", GAME,
                    "--layout", a.layout, "--out", out]
        if a.up_ini:
            ini_dir = os.path.dirname(os.path.abspath(a.up_ini))
            cmd += ["--up-ini", os.path.abspath(a.up_ini)]
            for f in sorted(glob.glob(os.path.join(ini_dir, "*.ini"))):
                if os.path.basename(f).lower() not in ("loader.ini", "localization.ini", "uhd-list-fix.ini"):
                    cmd += ["--up-ini", f]
        if run(cmd):
            print("translate error: uppatch")
            return 2
        patched = {m for m in plan["modules"] if os.path.exists(os.path.join(out, "patched", plan["modules"][m]["path"]))}
        with open(os.path.join(out, "link.c"), "a") as f:
            if os.path.exists(os.path.join(out, "up.sha256")):
                sha = open(os.path.join(out, "up.sha256")).read().strip()
                f.write(f'const char rt_up_sha256_embedded[] = "{sha}";\n')
            if not a.no_engine_aspect:
                f.write("const int rt_engine_override = 1;\n")
    bad = translate_all(jobs_for(out, plan, patched), a.jobs)
    if bad:
        print(f"translate error: {', '.join(bad)}")
        return 2
    t2 = time.time()
    reports = [json.load(open(os.path.join(out, n, "report.json"))) for n in plan["modules"]]
    summary = summarize(out, plan, reports)
    info = source_info(REPO)
    if info:
        summary["source"] = info
    summary["lift_counts"] = lift_counts(reports)
    summary["lift_counts_per_module"] = {r["module"]: r.get("lift_counts") or {} for r in reports}
    summary["inventory_missing"] = inventory_check(plan, a.inventory)
    summary["shim_pop_mismatch"] = pop_check(plan)
    dis = disallowed(reports)
    summary["disallowed"] = dis
    if dis:
        json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
        print("disallowed instruction: " + "; ".join(dis[:20]))
        return 3
    xf = summary["cross_flags"] = cross_flags(reports)
    print(f"flag fallbacks: {xf['fallbacks']} {xf['by_category']}; "
          f"consumers {xf['consumers']} ({xf['straight_line_consumers']} straight-line), "
          f"flag-returning {xf['flag_ret']} ({xf['direct_flag_ret']} called), {xf['publish_sites']} publish sites; "
          f"exported {len(xf['exported'])}, imported by another module {len(xf['exported_imported'])}")
    if xf["fallback_diff"] or xf["missing"] or xf["exported_imported"]:
        json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
        print("flag fallback tripwire: " + "; ".join(xf["missing"][:20]))
        for e in xf["exported_imported"]:
            print(f"exported flag function imported by another module: {e['module']} {e['va']} {e['export']} "
                  f"{e['kind']} {','.join(e['flags'])} <- {','.join(e['imported_by'])}")
        print("\n".join(xf["fallback_diff"][:80]))
        return 3
    ccache("--zero-stats")
    link_opts = [f"-Dgen_root={out}"] + pre_opts
    if a.pgo_profile:
        build = os.path.join(out, "meson")
        os.makedirs(build, exist_ok=True)
        shutil.copyfile(a.pgo_profile, os.path.join(build, "default.profdata"))
        link_opts.append("-Db_pgo=use")
        os.environ["CCACHE_DISABLE"] = "1"
        if os.path.exists(os.path.join(build, "build.ninja")) and run(["meson", "configure", build, "-Db_pgo=use"], stdout=subprocess.DEVNULL):
            return 3
    rc, last = meson_build(os.path.join(REPO, "link"), os.path.join(out, "meson"), link_opts, log)
    os.environ.pop("CCACHE_DISABLE", None)
    ninja_last = last[0] if last else ""
    for name in ("conform", "simd", "m3", "xflags"):
        d = os.path.join(out, "selftest", name)
        rc |= meson_build(os.path.join(REPO, "runtime"), os.path.join(d, "meson"),
                          [f"-Dgen_dir={os.path.join(d, 'gen')}", f"-Dmodule={name}"] + pre_opts, log)[0]
    stats = ccache("--print-stats")
    open(os.path.join(out, "ccache.txt"), "w").write(stats)
    t3 = time.time()
    if rc:
        print("link error: see " + log)
        print(open(log).read()[-4000:])
        return 4
    x = os.path.join(out, "meson", "bloodlines-native")
    if os.path.exists(os.path.join(out, "up.sha256")):
        want = open(os.path.join(out, "up.sha256")).read().strip()
        listing = subprocess.run([x, "--up-list-fixes"], capture_output=True, text=True, timeout=120).stdout
        if f"build sha256 {want}\n" not in listing:
            print(f"link error: the embedded up.sha256 does not match {want}")
            return 4
    checks = link_checks(x)
    pr = probes(x)
    skipped = list(UNICORN_MODES) if a.no_oracle else []
    hs = harness(out, skipped)
    imports = {l.split(" ")[0]: l.split(" ")[2] for l in pr.pop("imports") if l.count(" ") == 2}
    tsv = ["dll!name\tva\tpop\tpop_source\tbinding\timporters"]
    for s in plan["stubs"]:
        key = f"{s['dll']}!{s['name']}"
        tsv.append(f"{key}\t0x{s['va']:08X}\t{'?' if s['pop'] is None else s['pop']}\t{s['pop_source']}\t"
                   f"{imports.get(key, 'missing')}\t{','.join(s['importers'])}")
    open(os.path.join(out, "stubs.tsv"), "w").write("\n".join(tsv) + "\n")
    gen_c = [p for p in glob.glob(os.path.join(out, "*", "gen", "*.c")) + glob.glob(os.path.join(out, "stubs", "*.c"))]
    objs = glob.glob(os.path.join(out, "meson", "**", "*.o"), recursive=True)
    cc = {k: int(v) for k, v in (l.split("\t") for l in stats.splitlines() if l.count("\t") == 1 and l.split("\t")[1].isdigit())}
    hits = cc.get("direct_cache_hit", 0) + cc.get("preprocessed_cache_hit", 0)
    lookups = hits + cc.get("cache_miss", 0)
    after = snapshot(out)
    rewritten = sorted(p for p in after if before.get(p) != after[p])
    summary.update({
        "link_checks": [{"check": c, "ok": ok, "output": o} for c, ok, o in checks],
        "probes": pr, "harness": hs, "harness_skipped": skipped,
        "stub_list": {"lines": len(tsv) - 1, "shim": sum(v == "shim" for v in imports.values()),
                      "stub": sum(v == "stub" for v in imports.values()),
                      "missing": sum(imports.get(f"{s['dll']}!{s['name']}") is None for s in plan["stubs"])},
        "ninja": ninja_last,
        "time_s": {"total": round(time.time() - t0, 1), "translate": round(t2 - t1, 1), "build": round(t3 - t2, 1)},
        "bytes": {"generated_c": du(gen_c), "objects": du(objs), "executable": os.path.getsize(x)},
        "ccache": {"hits": hits, "lookups": lookups, "hit_rate": round(100 * hits / lookups, 1) if lookups else None},
        "generated_files_rewritten": len(rewritten),
    })
    ok = (all(d["image_word_matches"] for d in summary["data_imports"]) and all(c[1] for c in checks) and all(v["ok"] for k, v in pr.items() if isinstance(v, dict))
          and not pr["crash_reports"] and all(h["exit"] == 0 for h in hs) and summary["stub_list"]["missing"] == 0
          and summary["stub_list"]["lines"] == len(plan["stubs"]) and not summary["inventory_missing"]
          and not summary["shim_pop_mismatch"] and summary["lift_counts"].get("lock", {}).get("plain", 0) == 0)
    summary["state"] = "pass" if ok else "check failed"
    for k in ("inventory_missing", "shim_pop_mismatch"):
        for v in summary[k]:
            print(f"{k.replace('_', ' ')}: {v}")
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    sl = pr["main"].get("slots", {})
    print(f"{summary['state']}: {len(reports)} modules, {summary['totals']['translated']} of "
          f"{summary['totals']['seeded']} functions translated, {summary['totals']['skipped']} skipped; "
          f"untranslated only {sorted({k['category'] for k in summary['untranslated_kinds']} - {'hint', 'fault-as-original'})}; "
          f"slots {sl}; {len(plan['stubs'])} stubs; ninja: {ninja_last}; {len(rewritten)} generated files rewritten; "
          f"{summary['time_s']['total']}s; executable {summary['bytes']['executable']} bytes")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
