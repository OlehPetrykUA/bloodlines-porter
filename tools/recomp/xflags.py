import dataclasses
import inspect
import re

from capstone import CS_OP_IMM
from recomp import disasm as D
from recomp import lifter as L
from recomp import translator as T

import lifts

CC_FLAGS = {"o": "OF", "no": "OF", "b": "CF", "nae": "CF", "c": "CF", "ae": "CF", "nb": "CF", "nc": "CF",
            "e": "ZF", "z": "ZF", "ne": "ZF", "nz": "ZF", "be": "CF ZF", "na": "CF ZF", "a": "CF ZF",
            "nbe": "CF ZF", "s": "SF", "ns": "SF", "p": "PF", "pe": "PF", "np": "PF", "po": "PF",
            "l": "SF OF", "nge": "SF OF", "ge": "SF OF", "nl": "SF OF", "le": "ZF SF OF", "ng": "ZF SF OF",
            "g": "ZF SF OF", "nle": "ZF SF OF"}
FCMOV_CC = {"fcmovb": "b", "fcmove": "e", "fcmovbe": "be", "fcmovu": "p", "fcmovnb": "ae", "fcmovne": "ne",
            "fcmovnbe": "a", "fcmovnu": "np"}
OTHER_READS = {"cmc": "CF", "adc": "CF", "sbb": "CF", "rcl": "CF", "rcr": "CF", "salc": "CF", "into": "OF",
               "loope": "ZF", "loopne": "ZF", "lahf": "CF PF ZF SF", "pushf": "CF PF ZF SF OF",
               "pushfd": "CF PF ZF SF OF"}
ALL_FLAGS = frozenset({"CF", "PF", "ZF", "SF", "OF"})
WRITERS = T._FLAG_WRITERS | {"shld", "shrd", "div", "idiv"}
CARRY_WRITERS = {"adc", "sbb"}
STRING_COMPARES = {"cmpsb", "cmpsw", "cmpsd", "scasb", "scasw", "scasd"}
CF_ONLY, CF_OF = frozenset({"CF"}), frozenset({"CF", "OF"})
PARTIAL_KILLS = {"inc": ALL_FLAGS - CF_ONLY, "dec": ALL_FLAGS - CF_ONLY,
                 "bt": CF_ONLY, "bts": CF_ONLY, "btr": CF_ONLY, "btc": CF_ONLY, "stc": CF_ONLY, "clc": CF_ONLY,
                 "rol": CF_OF, "ror": CF_OF, "rcl": CF_OF, "rcr": CF_OF}
COUNTED = {"rol", "ror", "rcl", "rcr", "shl", "sal", "shr", "sar", "shld", "shrd"}
BREAKS = WRITERS | set(PARTIAL_KILLS) | {"cmc"}
STOPS = {"call", "ret", "retf", "iret", "iretd", "jecxz", "jcxz"} | set(D.LOOP_JUMPS)
PROBE = {"CF": "jb", "PF": "jp", "ZF": "je", "SF": "js", "OF": "jo"}
FALLBACK = re.compile(r"_flags /\* j|_cf /\* j|\(ecx != 0\) && \(_flags\)")
NOT_FLAG_JUMPS = {"jmp", "jecxz", "jcxz", "loop"}

def base(insn):
    return insn.mnemonic.split()[-1]


def count_is_zero_or_unknown(insn):
    count = insn.op_str.rsplit(",", 1)[-1].strip()
    if count == "cl":
        return True
    try:
        return int(count, 0) & 0x1F == 0
    except ValueError:
        return False


def kills(insn):
    m = base(insn)
    if m in COUNTED and count_is_zero_or_unknown(insn):
        return frozenset()
    if m in PARTIAL_KILLS:
        return PARTIAL_KILLS[m]
    if m in STRING_COMPARES and not insn.op_str.startswith("xmm"):
        return frozenset() if insn.mnemonic.startswith("rep") else ALL_FLAGS
    return ALL_FLAGS if m in WRITERS or m in CARRY_WRITERS else frozenset()


def reads(m):
    if m in OTHER_READS:
        return set(OTHER_READS[m].split())
    for prefix in ("j", "set", "cmov"):
        if m.startswith(prefix) and m not in NOT_FLAG_JUMPS and m[len(prefix):] in CC_FLAGS:
            return set(CC_FLAGS[m[len(prefix):]].split())
    if m in FCMOV_CC:
        return set(CC_FLAGS[FCMOV_CC[m]].split())
    return set()


def is_jcc(m):
    return m.startswith("j") and m not in NOT_FLAG_JUMPS or m in ("loope", "loopne")


def target(insn):
    op = insn.operands[0] if insn.operands else None
    return op.imm & 0xFFFFFFFF if op is not None and op.type == CS_OP_IMM else None


class Scan:
    def __init__(self, im, funcs):
        self.im, self.funcs = im, funcs
        self.cache = {}
        self.consumers, self.flag_ret = {}, {}
        self.direct_flag_ret = set()
        self.edges = {}
        for f in funcs:
            need, exits, first, _ = self.walk(f, funcs[f])
            self.edges[f] = exits
            if first:
                self.consumers[f] = need
        self.straight_line = set(self.consumers)
        self.close()
        for f, info in funcs.items():
            for va in info["insns"]:
                insn = self.decode(va)
                if insn.mnemonic != "call":
                    continue
                x = target(insn)
                if x not in funcs:
                    continue
                need, exits, first, direct = self.walk(va + insn.size, info, owner=f, calls=self.consumers)
                for t, live in exits:
                    more = self.consumers.get(t, set()) & live
                    if more:
                        need, first = need | more, True
                if direct:
                    self.direct_flag_ret.add(x)
                if first:
                    self.flag_ret.setdefault(x, set()).update(need)
        self.close()

    def decode(self, va):
        if va not in self.cache:
            self.cache[va] = self.im.decode(va)
        return self.cache[va]

    def walk(self, va, info, owner=None, calls=()):
        start = owner if owner is not None else va
        live, need, exits = set(ALL_FLAGS), set(), []
        first = direct = False
        while va in info["insns"]:
            insn = self.decode(va)
            m = base(insn)
            if m == "call" and target(insn) in calls:
                r = calls[target(insn)] & live
            else:
                r = reads(m) & live
                direct = direct or bool(r)
            if r:
                need |= r
                first = True
            if is_jcc(m) or m == "jmp":
                t = target(insn)
                if t is not None and not (start <= t < info["end"]):
                    exits.append((t, frozenset(live)))
                if m == "jmp":
                    return need, exits, first, direct
                va += insn.size
                continue
            live -= kills(insn)
            if not live or m in STOPS:
                return need, exits, first, direct
            va += insn.size
        if va == info["end"]:
            exits.append((va, frozenset(live)))
        return need, exits, first, direct

    def close(self):
        changed = True
        while changed:
            changed = False
            for f, exits in self.edges.items():
                for t, live in exits:
                    more = self.consumers.get(t, set()) & live
                    if more and not more <= self.consumers.get(f, set()):
                        self.consumers.setdefault(f, set()).update(more)
                        changed = True
            for x in list(self.flag_ret):
                for t in self.tail_targets(x):
                    if t in self.funcs and not self.flag_ret[x] <= self.flag_ret.get(t, set()):
                        self.flag_ret.setdefault(t, set()).update(self.flag_ret[x])
                        changed = True

    def tail_targets(self, x):
        info = self.funcs[x]
        out = []
        for va in info["insns"]:
            insn = self.decode(va)
            if insn.mnemonic == "jmp" or is_jcc(insn.mnemonic):
                t = target(insn)
                if t is not None and not (x <= t < info["end"]):
                    out.append(t)
        last = max(info["insns"], default=None)
        if last is not None:
            insn = self.decode(last)
            if last + insn.size == info["end"] and insn.mnemonic not in ("jmp", "ret", "retf", "int3", "ud2", "hlt"):
                out.append(info["end"])
        return out


class State:
    def __init__(self):
        self.lifter = None
        self.scan = None
        self.funcs = {}
        self.publish, self.cf_loads, self.missing, self.fallbacks = {}, {}, {}, {}


S = State()
_orig = {}


def pin(check, what):
    try:
        ok = check()
    except Exception as e:
        ok, what = False, f"{what} ({type(e).__name__}: {e})"
    if not ok:
        raise SystemExit(f"xflags: vendor interface changed: {what}")


def params(fn):
    return list(inspect.signature(fn).parameters)


def install(lifter, scan, funcs):
    if not _orig:
        pin(lambda: lifts._orig.get("block") is not None, "lifts.install must run before xflags.install")
        vendor = lifts._orig["block"]
        pin(lambda: vendor.__module__ == "recomp.lifter" and vendor.__name__ == "lift_basic_block",
            "lifts._orig['block'] is not recomp.lifter.lift_basic_block")
        pin(lambda: params(vendor) == ["lifter", "bb", "flag_state"], "lifter.lift_basic_block signature")
        pin(lambda: params(T.lift_basic_block) == ["lifter", "bb", "flag_state"], "lifts wrapper signature")
        pin(lambda: params(T._incoming_flag_state) == ["sources", "known", "is_entry"],
            "translator._incoming_flag_state signature")
        pin(lambda: params(L.publish_flags) == ["state", "note"], "lifter.publish_flags signature")
        pin(lambda: params(L._make_condition) == ["jcc", "flag_setter", "flag_ops"], "lifter._make_condition signature")
        pin(lambda: callable(L.Lifter._is_external_target), "Lifter._is_external_target is gone")
        pin(lambda: re.search(r"self\.needs_cf\s*=", inspect.getsource(L.Lifter.__init__)),
            "Lifter.__init__ no longer assigns needs_cf")
        pin(lambda: re.search(r"self\.lifter\.needs_cf\s*=",
                              inspect.getsource(T.FunctionTranslator.translate_function)),
            "FunctionTranslator.translate_function no longer assigns lifter.needs_cf")
        pin(lambda: [f.name for f in dataclasses.fields(D.BasicBlock)][:3] == ["start", "instructions", "successors"],
            "disasm.BasicBlock fields are no longer (start, instructions, successors)")
        pin(lambda: all(reads(m) for m in T._FLAG_READERS),
            "translator._FLAG_READERS has a mnemonic xflags.reads does not know")
        _orig.update(incoming=T._incoming_flag_state, block=T.lift_basic_block)
    S.lifter, S.scan, S.funcs = lifter, scan, funcs
    S.publish.clear(), S.cf_loads.clear(), S.missing.clear(), S.fallbacks.clear()
    T._incoming_flag_state = incoming_flag_state
    T.lift_basic_block = lift_basic_block


def incoming_flag_state(sources, known, is_entry):
    start = S.lifter.func_start
    if is_entry and start in S.scan.consumers:
        if sources:
            raise SystemExit(f"cross-fragment consumer sub_{start:08X} has an in-function predecessor "
                             + ", ".join(f"0x{p:08X}" for p in sorted(sources)))
        return ("eflags", [])
    return _orig["incoming"](sources, known, is_entry)


def check(site, need, state, setter_va, what):
    if state and state[0] == "eflags":
        S.missing.pop(site, None)
        return
    gone = [f for f in sorted(need) if not (state and L._make_condition(PROBE[f], *state))]
    if gone:
        S.missing[site] = (f"cross-fragment flag {','.join(gone)} has no rule after "
                           f"{state[0] if state else 'nothing'} at 0x{setter_va:08X} ({what})")
    else:
        S.missing.pop(site, None)


def publish(lifter, state, site, need, setter_va, what):
    check(site, need, state, setter_va, what)
    out = L.publish_flags(state, note=f"flags for {what}")
    if out and "_cf" in out[0] and not lifter.needs_cf:
        S.missing[site] = f"cross-fragment CF at 0x{setter_va:08X} ({what}) is _cf, not computed here"
    if out:
        S.publish[site] = what
    else:
        S.publish.pop(site, None)
    return out


def cf_load(lifter, site):
    if lifter.needs_cf:
        S.cf_loads[site] = True
        return ["_cf = (int)(g_eflags & 1u); /* cross-fragment CF */"]
    S.cf_loads.pop(site, None)
    return []


def lift_basic_block(lifter, bb, flag_state=None):
    sc, insns = S.scan, bb.instructions
    stmts, state = [], flag_state
    if bb.start == lifter.func_start and state and state[0] == "eflags" and bb.start in sc.consumers:
        stmts += cf_load(lifter, bb.start)
    seg = []
    for insn in insns:
        callee = insn.call_target if insn.is_call else None
        if callee in sc.consumers:
            out, state = run(lifter, bb, seg, state)
            stmts += out
            seg = []
            stmts += publish(lifter, state, ("call", insn.address), sc.consumers[callee], insn.address,
                             f"call sub_{callee:08X}")
        seg.append(insn)
        if callee in sc.flag_ret:
            out, _ = run(lifter, bb, seg, state)
            stmts += out + cf_load(lifter, insn.address)
            seg, state = [], ("eflags", [])
    if seg:
        out, state = run(lifter, bb, seg, state)
        stmts += out
    last = insns[-1] if insns else None
    if last is None:
        return stmts, state
    pub = None
    t = last.jump_target
    if (last.is_branch and t is not None and lifter._is_external_target(t) and t in sc.consumers):
        pub = (len(stmts) - 1, sc.consumers[t], f"sub_{t:08X}")
    if not pub and not last.is_terminator and last.end_address == lifter.func_end \
            and lifter.func_end in sc.consumers:
        at = len(stmts) - 1 if last.is_cond_jump else len(stmts)
        pub = (at, sc.consumers[lifter.func_end], f"sub_{lifter.func_end:08X}")
    if not pub and last.is_ret and lifter.func_start in sc.flag_ret:
        pub = (len(stmts) - 1, sc.flag_ret[lifter.func_start], "ret")
    if pub:
        at, need, what = pub
        stmts[at:at] = publish(lifter, state, ("end", last.address), need, last.address, what)
    else:
        S.publish.pop(("end", last.address), None)
        S.missing.pop(("end", last.address), None)
    if any(FALLBACK.search(s) for s in stmts):
        S.fallbacks[last.address] = (lifter.func_start, last.mnemonic, bb.start)
    else:
        S.fallbacks.pop(last.address, None)
    return stmts, state


def run(lifter, bb, seg, state):
    if not seg:
        return [], state
    if len(seg) == len(bb.instructions):
        return _orig["block"](lifter, bb, state)
    return _orig["block"](lifter, D.BasicBlock(seg[0].address, list(seg), []), state)


def category(site, owner):
    info = S.funcs.get(owner)
    if info is None or site not in info["insns"]:
        return "after-table"
    sc = S.scan
    order = sorted(info["insns"])
    i = order.index(site) - 1
    while i >= 0:
        insn = sc.decode(order[i])
        if order[i] + insn.size != order[i + 1]:
            break
        if lifts.is_simd(insn):
            return "after-simd"
        m = insn.mnemonic
        if m in BREAKS or m == "call" or m == "jmp" or m.startswith("ret") or m.startswith("j"):
            break
        i -= 1
    return "other"


def report(texts):
    if S.scan is None:
        raise SystemExit("xflags.report called without xflags.install: "
                         "call xflags.install(lifter, Scan, funcs) before translating")
    text_counts, joined = text_fallbacks(texts)
    fb = []
    for site, (owner, mnem, block) in sorted(S.fallbacks.items()):
        if block in joined:
            continue
        fb.append({"va": f"0x{site:08X}", "function": f"sub_{owner:08X}", "mnemonic": mnem,
                   "category": category(site, owner)})
    per_func = {}
    for row in fb:
        per_func[row["function"]] = per_func.get(row["function"], 0) + 1
    sc = S.scan
    return {
        "consumers": {f"0x{f:08X}": sorted(n) for f, n in sorted(sc.consumers.items())},
        "straight_line_consumers": len(sc.straight_line),
        "flag_ret": {f"0x{f:08X}": sorted(n) for f, n in sorted(sc.flag_ret.items())},
        "direct_flag_ret": len(sc.direct_flag_ret),
        "publish": {f"{k[0]} 0x{k[1]:08X}": v for k, v in sorted(S.publish.items())},
        "cf_loads": [f"0x{k:08X}" for k in sorted(S.cf_loads)],
        "missing": [v for _, v in sorted(S.missing.items())],
        "fallbacks": fb,
        "fallback_mismatch": sorted(f for f in set(per_func) | set(text_counts)
                                    if per_func.get(f, 0) != text_counts.get(f, 0)),
    }


FUNC = re.compile(r"^void (sub_[0-9A-F]{8})\(void\)", re.M)
JOIN = re.compile(r"\b_jf_([0-9A-F]{8}) /\* ")


def text_fallbacks(texts):
    counts, joined = {}, set()
    for text in texts:
        cur = None
        for line in text.splitlines():
            mt = FUNC.match(line)
            if mt:
                cur = mt.group(1)
            elif FALLBACK.search(line):
                counts[cur] = counts.get(cur, 0) + 1
            joined.update(int(v, 16) for v in JOIN.findall(line))
    return counts, joined
