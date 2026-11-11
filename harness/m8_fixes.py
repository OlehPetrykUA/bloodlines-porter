import argparse
import re
import sys

SWITCH = re.compile(r"rt: up fix (\S[^\n]*?) (on|off|not supported)$", re.M)
ROW = re.compile(r"rt: up fixrow (\w+) (on \(build\)|built in, switch off|not in this build|bytes differ)$", re.M)
STOP = re.compile(r"UP build needs -game Unofficial_Patch")

EXPECTED_ROWS = ["fix_paths_a", "fix_paths_b", "floats_female", "model_lods", "save_speed_a", "save_speed_b", "save_speed_c",
                 "ragdolls", "no_tracers", "protean_warform_b", "histories", "coronas_lights", "claws_stats", "shadows_rebuild", "fov_reset_parse",
                 "fov_reset_store", "door_fps_b", "door_fps_a", "hist_stat_a", "hist_stat_c", "hist_stat_b",
                 "model_loader", "console_open", "console_cmd", "console_close", "console_menu",
                 "tw_nodoor", "tw_drop", "tw_stack_find", "tw_stack_pick", "tw_stack_skip",
                 "tw_altf4", "tw_corpse_think", "tw_corpse_fade", "tw_corpse_mark", "tw_corpse_clear",
                 "cs_hide", "cs_give", "cs_owned", "cs_keys", "cs_barter"]


ROW_SWITCHES = {"no_tracers": ["Tweaks/No Bullet Tracers", "vtmb-tweaks/bNoTracers"], "protean_warform_b": ["protean-improved/Warform Frenzy"],
                "tw_nodoor": ["vtmb-tweaks/bNoDoorAutoClose"], "tw_altf4": ["vtmb-tweaks/bEnableAltF4"],
                **{r: ["vtmb-tweaks/bNoCorpseDespawn"] for r in ("tw_corpse_think", "tw_corpse_fade", "tw_corpse_mark", "tw_corpse_clear")},
                **{r: ["container-stack-fix/Enabled"] for r in ("cs_hide", "cs_give", "cs_owned", "cs_keys", "cs_barter")}}


def row_ok(row, state, sw):
    if state == "on (build)":
        return True
    return state == "built in, switch off" and row in ROW_SWITCHES and not any(sw.get(k) == "on" for k in ROW_SWITCHES[row])


def check_m(log, build):
    out = []
    sw = dict(SWITCH.findall(log))
    out.append(("one switch line per key (69)", len(SWITCH.findall(log)) == 69 and len(sw) == 69, f"{len(sw)} keys"))
    rows = dict(ROW.findall(log))
    out.append(("every C1 row logs a state", sorted(rows) == sorted(EXPECTED_ROWS), f"missing {sorted(set(EXPECTED_ROWS) - set(rows))}"))
    if build == "up":
        bad = {k: v for k, v in rows.items() if not row_ok(k, v, sw)}
        out.append(("every row is on (build) in the UP build", not bad, str(bad)))
    else:
        bad = {k: v for k, v in rows.items() if v != "not in this build"}
        out.append(("no row is built in the vanilla build", not bad, str(bad)))
    return out


def check_v(log):
    return [("vanilla run logs no fix line", not SWITCH.search(log) and not ROW.search(log), "")]


def check_stop(log):
    return [("UP build without -game stops with exit 5", bool(STOP.search(log)), "")]


def selftest():
    sw = "".join(f"rt: up fix S/K{i} on\n" for i in range(69))
    up = sw + "".join(f"rt: up fixrow {r} on (build)\n" for r in EXPECTED_ROWS)
    van = sw + "".join(f"rt: up fixrow {r} not in this build\n" for r in EXPECTED_ROWS)
    cases = [
        ("up log passes", all(ok for _, ok, _ in check_m(up, "up"))),
        ("vanilla-build log passes", all(ok for _, ok, _ in check_m(van, "vanilla"))),
        ("built in, switch off passes when the switch is off or unsupported", all(ok for _, ok, _ in check_m(
            up.replace("fixrow no_tracers on (build)", "fixrow no_tracers built in, switch off")
              .replace("S/K1 on", "Tweaks/No Bullet Tracers not supported")
              .replace("fixrow protean_warform_b on (build)", "fixrow protean_warform_b built in, switch off"), "up"))),
        ("built in, switch off fails when that row's switch is on", not all(ok for _, ok, _ in check_m(
            up.replace("fixrow no_tracers on (build)", "fixrow no_tracers built in, switch off")
              .replace("S/K1 on", "Tweaks/No Bullet Tracers on"), "up"))),
        ("built in, switch off fails for a row without a switch mapping", not all(ok for _, ok, _ in check_m(
            up.replace("fixrow histories on (build)", "fixrow histories built in, switch off"), "up"))),
        ("a missing row fails", not all(ok for _, ok, _ in check_m(up.replace("fixrow histories", "fixrow x"), "up"))),
        ("a row not built fails in the UP build", not all(ok for _, ok, _ in check_m(van, "up"))),
        ("a built row fails in the vanilla build", not all(ok for _, ok, _ in check_m(up, "vanilla"))),
        ("a missing switch line fails", not all(ok for _, ok, _ in check_m(up.replace("rt: up fix S/K0 on\n", ""), "up"))),
        ("V with a fix line fails", not all(ok for _, ok, _ in check_v("rt: up fix Bugs/Fix Paths on\n"))),
        ("V without fix lines passes", all(ok for _, ok, _ in check_v("rt: mod Vampire\n"))),
        ("the stop line is found", all(ok for _, ok, _ in check_stop("x\nUP build needs -game Unofficial_Patch (row a)\n"))),
    ]
    for n, ok in cases:
        print(("PASS " if ok else "FAIL ") + n)
    fails = sum(not ok for _, ok in cases)
    print(f"selftest: {len(cases) - fails} PASS, {fails} FAIL")
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    s = ap.add_subparsers(dest="cmd", required=True)
    s.add_parser("selftest")
    c = s.add_parser("check")
    c.add_argument("log")
    c.add_argument("--mode", choices=["m-up", "m-vanilla", "v", "stop"], required=True)
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return selftest()
    log = open(a.log, encoding="utf-8", errors="replace").read()
    res = {"m-up": lambda: check_m(log, "up"), "m-vanilla": lambda: check_m(log, "vanilla"), "v": lambda: check_v(log),
           "stop": lambda: check_stop(log)}[a.mode]()
    for n, ok, d in res:
        print(("PASS " if ok else "FAIL ") + n + ("" if ok else f": {d}"))
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main())
