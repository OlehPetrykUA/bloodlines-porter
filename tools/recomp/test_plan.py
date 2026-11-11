import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "third_party", "xboxrecomp"))

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

import build_all
import lifts
import pe as pefile
import plan
import test_mmx
from recomp import disasm as D
from recomp import lifter as L
from recomp import translator as T

BASE = 0x10000000

CONFORM = {
    "fnclex": ("8b4c2404 d9e8 d9ee def9 dd39 dbe2 dfe0 ddd8 c3",
               ["mov ecx, dword ptr [esp + 4]", "fld1", "fldz", "fdivp st(1)", "fnstsw word ptr [ecx]", "fnclex",
                "fnstsw ax", "fstp st(0)", "ret"]),
    "pause": ("b964000000 b807000000 f390 49 75fb c3",
              ["mov ecx, 0x64", "mov eax, 7", "pause", "dec ecx", "jne TARGET", "ret"], 10),
    "fcom_ze": ("d9e8 d9ee def9 d9e8 ded9 dfe0 c3", ["fld1", "fldz", "fdivp st(1)", "fld1", "fcompp", "fnstsw ax", "ret"]),
    "fxam_ze": ("d9e8 d9ee def9 d9e5 dfe0 ddd8 c3", ["fld1", "fldz", "fdivp st(1)", "fxam", "fnstsw ax", "fstp st(0)", "ret"]),
    "fninit": ("8b4c2404 d9eb d929 dbe3 d97904 dfe0 c3",
               ["mov ecx, dword ptr [esp + 4]", "fldpi", "fldcw word ptr [ecx]", "fninit", "fnstcw word ptr [ecx + 4]",
                "fnstsw ax", "ret"]),
    "ffree": ("d9ee d9e8 ddc1 dfe0 c3", ["fldz", "fld1", "ffree st(1)", "fnstsw ax", "ret"]),
    "fnstenv": ("8b4c2404 d9e8 d9ee d931 d9791c ddd8 ddd8 c3",
                ["mov ecx, dword ptr [esp + 4]", "fld1", "fldz", "fnstenv [ecx]", "fnstcw word ptr [ecx + 0x1c]",
                 "fstp st(0)", "fstp st(0)", "ret"]),
    "fldenv": ("8b4c2404 d921 d97920 dfe0 c3",
               ["mov ecx, dword ptr [esp + 4]", "fldenv [ecx]", "fnstcw word ptr [ecx + 0x20]", "fnstsw ax", "ret"]),
    "fnsave": ("8b4c2404 dd4178 d9ee d9e8 dd31 dfe0 dd21 dd99800000 00 dd9988000000 dd9990000000 c3",
               ["mov ecx, dword ptr [esp + 4]", "fld qword ptr [ecx + 0x78]", "fldz", "fld1", "fnsave dword ptr [ecx]",
                "fnstsw ax", "frstor dword ptr [ecx]", "fstp qword ptr [ecx + 0x80]", "fstp qword ptr [ecx + 0x88]",
                "fstp qword ptr [ecx + 0x90]", "ret"]),
    "lock": ("8b4c2404 33c0 f0ff01 f0ff4904 0f94c0 f0ff4908 7501 40 c3",
             ["mov ecx, dword ptr [esp + 4]", "xor eax, eax", "lock inc dword ptr [ecx]",
              "lock dec dword ptr [ecx + 4]", "sete al", "lock dec dword ptr [ecx + 8]", "jne TARGET", "inc eax",
              "ret"]),
}
WIDTHS = {8: ("", "byte", "dl"), 16: ("66", "word", "dx"), 32: ("", "dword", "edx")}
FLAGS = (("0f944120", "sete byte ptr [ecx + 0x20]"), ("0f984121", "sets byte ptr [ecx + 0x21]"),
         ("0f924122", "setb byte ptr [ecx + 0x22]"), ("0f904123", "seto byte ptr [ecx + 0x23]"),
         ("0f9a4124", "setp byte ptr [ecx + 0x24]"))
LOAD = ("8b4c2404 8b5140", ["mov ecx, dword ptr [esp + 4]", "mov edx, dword ptr [ecx + 0x40]"])
for op, code in (("add", 0x00), ("sub", 0x28), ("and", 0x20), ("or", 0x08), ("xor", 0x30), ("inc", 0), ("dec", 1)):
    for bits, (pre, size, reg) in WIDTHS.items():
        if op in ("inc", "dec"):
            enc = f"f0{pre}{'fe' if bits == 8 else 'ff'}{'01' if op == 'inc' else '09'}"
            text = f"lock {op} {size} ptr [ecx]"
        else:
            enc = f"f0{pre}{code + (bits > 8):02x}11"
            text = f"lock {op} {size} ptr [ecx], {reg}"
        CONFORM[f"lock_{op}{bits}"] = (" ".join([LOAD[0], enc] + [h for h, _ in FLAGS] + ["c3"]),
                                       LOAD[1] + [text] + [t for _, t in FLAGS] + ["ret"])
for bits, (pre, size, reg) in WIDTHS.items():
    CONFORM[f"xchg{bits}"] = (
        f"{LOAD[0]} {pre}{'86' if bits == 8 else '87'}11 {pre}{'88' if bits == 8 else '89'}5120 c3",
        LOAD[1] + [f"xchg {size} ptr [ecx], {reg}", f"mov {size} ptr [ecx + 0x20], {reg}", "ret"])
CONFORM["lock_split"] = (" ".join([LOAD[0], "f001510e"] + [h for h, _ in FLAGS] + ["c3"]),
                         LOAD[1] + ["lock add dword ptr [ecx + 0xe], edx"] + [t for _, t in FLAGS] + ["ret"])
SIMD = {"simd": ("f30f10442404 c3", ["movss xmm0, dword ptr [esp + 4]", "ret"])}
SIMD.update({name: case[:2] for name, case in test_mmx.CASES.items()})
DLLMAIN = bytes.fromhex("b801000000c20c00")
XF_CC = ("o", "no", "b", "ae", "e", "ne", "be", "a", "s", "ns", "p", "np", "l", "ge", "le", "g")
XF_REL = {"call": ("e8", 5), "jmp": ("e9", 5), "jne": ("0f85", 6)}
XF_PRE = [("8b4c2404", "mov ecx, dword ptr [esp + 4]"), ("8b11", "mov edx, dword ptr [ecx]"),
          ("3b5104", "cmp edx, dword ptr [ecx + 4]")]
XF_TAIL = [("33c0", "xor eax, eax"), ("c3", "ret"), ("b801000000", "mov eax, 1"), ("c3", "ret")]
XF_OPS = {
    "sbb": ([], [("19c0", "sbb eax, eax"), ("c3", "ret")]),
    "adc": ([("b805000000", "mov eax, 5")], [("83d000", "adc eax, 0"), ("c3", "ret")]),
    "setl": ([("b800000000", "mov eax, 0")], [("0f9cc0", "setl al"), ("c3", "ret")]),
    "cmovb": ([("b807000000", "mov eax, 7")], [("0f42c2", "cmovb eax, edx"), ("c3", "ret")]),
}
XF_HELPER = "xf_helper"


def xf_cases():
    funcs, aliases, names = {}, {}, []

    def add(name, caller, consumer, edge):
        names.append(name)
        tail = name + "_t"
        if edge in ("fall", "same"):
            funcs[name] = caller + consumer
            if edge == "fall":
                aliases[tail] = (name, len(caller))
            return
        funcs[tail] = consumer
        funcs[name] = caller + [("@" + edge, tail)]
        if edge == "jne":
            funcs[name] += [("b802000000", "mov eax, 2"), ("c3", "ret")]
        if edge == "call":
            funcs[name] += [("c3", "ret")]

    funcs[XF_HELPER] = XF_PRE + [("c20400", "ret 4")]
    for i, cc in enumerate(XF_CC):
        consumer = [(f"{0x70 + i:02x}03", f"j{cc} *")] + XF_TAIL
        for edge in ("jmp", "jne", "fall", "call"):
            add(f"xf_j{cc}_{'jcc' if edge == 'jne' else edge}", XF_PRE, consumer, edge)
        pushed = [("8b4c2404", "mov ecx, dword ptr [esp + 4]"), ("51", "push ecx"), ("@call", XF_HELPER)]
        add(f"xf_j{cc}_helper", pushed[:1] + [("39c9", "cmp ecx, ecx")] + pushed[1:], consumer, "same")
        add(f"xf_j{cc}_helperfall", pushed, consumer, "fall")
    for op, (setup, consumer) in XF_OPS.items():
        for edge in ("jmp", "jne", "fall"):
            add(f"xf_{op}_{'jcc' if edge == 'jne' else edge}", XF_PRE + setup, consumer, edge)
    return funcs, aliases, names


def xf_encode(funcs, aliases):
    def size(item):
        return XF_REL[item[0][1:]][1] if item[0][0] == "@" else len(item[0]) // 2

    rvas, pos = {}, len(DLLMAIN)
    for name, items in funcs.items():
        pos += -pos % 16
        rvas[name] = 0x1000 + pos
        pos += sum(size(i) for i in items)
    out, want = {}, {}
    for name, items in funcs.items():
        code, at, lines = b"", rvas[name], []
        for item in items:
            if item[0][0] == "@":
                op, n = XF_REL[item[0][1:]]
                code += bytes.fromhex(op) + struct.pack("<i", rvas[item[1]] - (at + n))
                lines.append(item[0][1:] + " *")
            else:
                code += bytes.fromhex(item[0])
                lines.append(item[1])
            at = rvas[name] + len(code)
        out[name], want[name] = code, lines
    return out, want, {n: (f, sum(size(i) for i in funcs[f][:k])) for n, (f, k) in aliases.items()}


def xf_pe():
    funcs, aliases, names = xf_cases()
    bodies, want, offsets = xf_encode(funcs, aliases)
    cs = Cs(CS_ARCH_X86, CS_MODE_32)
    data = make_pe(bodies, aliases=offsets)
    p = pefile.parse(data)
    for name, body in bodies.items():
        got = [f"{i.mnemonic} {i.op_str}".strip() for i in cs.disasm(body, BASE + p.exports[name])]
        assert len(got) == len(want[name]) and all(
            g.split()[0] == w[:-2] if w.endswith(" *") else g == w for g, w in zip(got, want[name])), (name, got)
    for name, (func, offset) in offsets.items():
        assert p.exports[name] == p.exports[func] + offset, name
        assert offset in [i.address for i in cs.disasm(bodies[func], 0)], name
    return data, names


def import_dir(imports, rva):
    dlls = list(imports)
    desc = 20 * (len(dlls) + 1)
    tables, strings, slots = bytearray(), bytearray(), {}
    tab_rva = rva + desc
    n_slots = sum(len(v) + 1 for v in imports.values())
    str_rva = tab_rva + 8 * n_slots
    name_at, entries = {}, []
    for dll in dlls:
        name_at[dll] = str_rva + len(strings)
        strings += dll.encode() + b"\0"
        strings += bytes(len(strings) % 2)
        hints = []
        for n in imports[dll]:
            hints.append(str_rva + len(strings))
            strings += b"\0\0" + n.encode() + b"\0"
            strings += bytes(len(strings) % 2)
        entries.append(hints)
    ilt, iat, desc_bytes = bytearray(), bytearray(), bytearray()
    ilt_rva, iat_rva = tab_rva, tab_rva + 4 * n_slots
    for dll, hints in zip(dlls, entries):
        desc_bytes += struct.pack("<IIIII", ilt_rva + len(ilt), 0, 0, name_at[dll], iat_rva + len(iat))
        for name, h in zip(imports[dll], hints):
            slots[(dll, name)] = iat_rva + len(iat)
            ilt += struct.pack("<I", h)
            iat += struct.pack("<I", h)
        ilt += bytes(4)
        iat += bytes(4)
    data = desc_bytes + bytes(20) + ilt + iat + strings
    return bytes(data), slots, iat_rva, len(iat)


def tls_dir(tls, rva, base):
    data = bytes(tls["data"])
    start = rva + 24 + 4
    blob = struct.pack("<IIIIII", base + start, base + start + len(data), base + rva + 24, 0,
                       tls.get("zero_fill", 0), 0)
    return blob + bytes(4) + data


def make_pe(funcs, forwarders=None, base=BASE, imports=None, tls=None, aliases=None):
    forwarders = forwarders or {}
    aliases = aliases or {}
    code, rvas = bytearray(DLLMAIN), {}
    for name, body in funcs.items():
        code += b"\xcc" * (-len(code) % 16)
        rvas[name] = 0x1000 + len(code)
        code += body
    for name, (func, offset) in aliases.items():
        assert offset < len(funcs[func]), (name, func, offset)
        rvas[name] = rvas[func] + offset
    edata = 0x1000 + 0x1000 * max(1, -(-len(code) // 0x1000))
    names = sorted(list(funcs) + list(forwarders) + list(aliases))
    strings = bytearray(b"synthetic.dll\0")
    fwd_rva = {}
    for n in forwarders:
        fwd_rva[n] = len(strings)
        strings += forwarders[n].encode() + b"\0"
    name_rva = {}
    for n in names:
        name_rva[n] = len(strings)
        strings += n.encode() + b"\0"
    head = 40 + 4 * len(names) * 2 + 2 * len(names)
    eat = [edata + head + fwd_rva[n] if n in forwarders else rvas[n] for n in names]
    ed = struct.pack("<IIHHIIIIIII", 0, 0, 0, 0, edata + head, 1, len(names), len(names), edata + 40,
                     edata + 40 + 4 * len(names), edata + 40 + 8 * len(names))
    ed += b"".join(struct.pack("<I", r) for r in eat)
    ed += b"".join(struct.pack("<I", edata + head + name_rva[n]) for n in names)
    ed += b"".join(struct.pack("<H", i) for i in range(len(names)))
    ed += strings
    dirs = [(edata, len(ed))] + [(0, 0)] * 15
    sec = bytearray(ed)
    if imports:
        sec += bytes(-len(sec) % 16)
        blob, _, iat_rva, iat_size = import_dir(imports, edata + len(sec))
        dirs[1] = (edata + len(sec), 20 * (len(imports) + 1))
        dirs[12] = (iat_rva, iat_size)
        sec += blob
    if tls:
        sec += bytes(-len(sec) % 16)
        dirs[9] = (edata + len(sec), 24)
        sec += tls_dir(tls, edata + len(sec), base)
    flags = 0xC0000040 if imports or tls else 0x40000040
    text = bytes(code) + bytes(-len(code) % 0x200)
    edat = bytes(sec) + bytes(-len(sec) % 0x200)
    image = edata + ((len(sec) + 0xFFF) & ~0xFFF)
    opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", 0x10B, 6, 0, len(text), len(edat), 0, 0x1000, 0x1000, edata,
                      base, 0x1000, 0x200, 4, 0, 0, 0, 4, 0, 0, image, 0x400, 0, 2, 0x100, 0x100000, 0x1000,
                      0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", *d) for d in dirs)
    hdr = bytearray(b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40))
    hdr += b"PE\0\0" + struct.pack("<HHIIIHH", 0x14C, 2, 0, 0, 0, len(opt), 0x2102) + opt
    hdr += struct.pack("<8sIIIIIIHHI", b".text", len(code), 0x1000, len(text), 0x400, 0, 0, 0, 0, 0x60000020)
    hdr += struct.pack("<8sIIIIIIHHI", b".edata", len(sec), edata, len(edat), 0x400 + len(text), 0, 0, 0, 0,
                       flags)
    return bytes(hdr) + bytes(0x400 - len(hdr)) + text + edat


def assembled(cases):
    cs = Cs(CS_ARCH_X86, CS_MODE_32)
    out = {}
    for name, case in cases.items():
        out[name] = bytes.fromhex(case[0].replace(" ", ""))
    p = pefile.parse(make_pe(out))
    for name, case in cases.items():
        rva = p.exports[name]
        got = [f"{i.mnemonic} {i.op_str}".strip() for i in cs.disasm(out[name], BASE + rva)]
        target = case[2] if len(case) > 2 else 0x17
        want = [w.replace("TARGET", hex(BASE + rva + target)) for w in case[1]]
        assert got == want, (name, got, want)
    return out


def test_layout():
    mods = {"a": {"base": 0x10000000, "preferred": 0x10000000, "size": 0x30000, "relocs": True},
            "b": {"base": 0x10020000, "preferred": 0x10000000, "size": 0x10000, "relocs": True}}
    errs = plan.check(mods)
    assert len(errs) == 1 and "b [0x10020000" in errs[0] and "overlaps a [0x10000000" in errs[0], errs
    assert plan.suggest(mods, ["a", "b"]) == {"a": 0x10000000, "b": 0x10030000}
    low = {"c": {"base": 0x0BFF0000, "preferred": 0x0BFF0000, "size": 0x20000, "relocs": False}}
    assert "runtime regions" in plan.check(low)[0]


def test_resolve():
    p = pefile.parse(make_pe({"Code": b"\xc3"}, {"Fwd": "other.Target", "Loop": "synthetic.Loop"}))
    assert p.ordinals == {"#1": "Code", "#2": "Fwd", "#3": "Loop"}, p.ordinals
    mods = {"synthetic": {"pe": p, "base": 0x20000000}}
    by = {"synthetic.dll": "synthetic"}
    code = plan.resolve(mods, by, "SYNTHETIC.dll", "Code")
    assert code == ("code", "synthetic", 0x20000000 + p.exports["Code"]), code
    assert plan.resolve(mods, by, "synthetic.dll", "#1") == code
    assert plan.resolve(mods, by, "synthetic.dll", "Fwd") == ("forward", "other.dll", "Target")
    assert plan.resolve(mods, by, "kernel32.dll", "Sleep") is None
    try:
        plan.resolve(mods, by, "synthetic.dll", "Loop")
    except SystemExit as e:
        assert "forwarder chain" in str(e)
    else:
        raise AssertionError("forwarder loop not refused")


def test_simd_detection():
    cs = Cs(CS_ARCH_X86, CS_MODE_32)
    cs.detail = True
    d = D.Disassembler()
    kinds = {}
    for hexcode in ("f30f10442404", "0f77", "0f0d00", "0f1800", "0faee8", "d9e8", "f0ff01", "660fefc0", "0f6fc1"):
        insn = d._decode_instruction(next(cs.disasm(bytes.fromhex(hexcode), 0x1000)))
        kinds[insn.mnemonic] = "hint" if lifts.is_hint(insn.mnemonic) else "simd" if lifts.is_simd(insn) else "x86"
    assert kinds == {"movss": "simd", "emms": "simd", "prefetch": "hint", "prefetchnta": "hint", "lfence": "hint",
                     "fld1": "x86", "lock inc": "x86", "pxor": "simd", "movq": "simd"}, kinds


def test_summary_refuses_unimplemented():
    good = {"module": "m", "functions": 2, "translated": 2, "skipped": 0, "rows": [{}, {}],
            "untranslated": [{"module": "m", "va": "0x1", "mnemonic": "movss", "category": "simd-trap"}]}
    bad = dict(good, untranslated=good["untranslated"] + [
        {"module": "m", "va": "0x2", "mnemonic": "fnop", "category": "unimplemented"}])
    assert build_all.disallowed([good]) == []
    assert build_all.disallowed([bad]) == ["m 0x2 fnop (unimplemented)"]


def test_make_pe_imports_tls():
    imports = {"KERNEL32.dll": ["Sleep", "TlsAlloc"], "USER32.dll": ["MessageBoxA"]}
    data = make_pe({"Code": b"\xc3"}, imports=imports, tls={"data": b"\x01\x02\x03", "zero_fill": 8})
    p = pefile.parse(data)
    got = [(d, n) for d, n, _ in p.imports]
    assert got == [("KERNEL32.dll", "Sleep"), ("KERNEL32.dll", "TlsAlloc"), ("USER32.dll", "MessageBoxA")], got
    slots = [s for _, _, s in p.imports]
    assert len(set(slots)) == 3 and all(s % 4 == 0 for s in slots), slots
    t = p.tls
    assert t["end"] - t["start"] == 3 and t["zero_fill"] == 8 and t["callbacks"] == [], t
    img = p.mapped()
    assert img[t["start"] - BASE:t["end"] - BASE] == b"\x01\x02\x03"
    assert BASE + 0x2000 <= t["index"] < BASE + p.size_of_image and p.u32(t["index"] - BASE) == 0
    assert p.exports == pefile.parse(make_pe({"Code": b"\xc3"})).exports
    assert hashlib.sha256(make_pe({"Code": b"\xc3"})).hexdigest() == (
        "d167758e4156e364c3da5b7ccfde3df127883e7f3b1a45563b69aaf1c2e63bc8")


def lift(hexcode, va=0x10001000):
    cs = Cs(CS_ARCH_X86, CS_MODE_32)
    cs.detail = True
    d = D.Disassembler()
    insns = [d._decode_instruction(i) for i in cs.disasm(bytes.fromhex(hexcode.replace(" ", "")), va)]
    lifter = L.Lifter()
    lifter.needs_cf = True
    stmts, _ = T.lift_basic_block(lifter, D.BasicBlock(start=va, instructions=insns))
    return insns, "\n".join(stmts)


def test_lifts():
    sites = lifts.install()
    _, c = lift("9f")
    assert 'RECOMP_UNIMPL("lahf", 0x10001000u)' in c, c
    assert sites[0x10001000] == ("lahf", "unimplemented"), sites
    row = {"module": "m", "va": "0x10001000", "mnemonic": "lahf", "category": sites[0x10001000][1]}
    assert build_all.disallowed([{"module": "m", "untranslated": [row]}]) == ["m 0x10001000 lahf (unimplemented)"]
    _, c = lift("39d8 9f", 0x10002000)
    assert "/* lahf (cmp) */" in c and "RECOMP_UNIMPL" not in c, c
    assert lifts.COUNTS["lahf"] == {"idiom": 1, "lifted": 0, "unimplemented": 1}, lifts.COUNTS
    lifter = L.Lifter()
    lifter.needs_cf = True
    d = D.Disassembler()
    cs = Cs(CS_ARCH_X86, CS_MODE_32)
    cs.detail = True
    head = [d._decode_instruction(i) for i in cs.disasm(bytes.fromhex("39d8"), 0x10004000)]
    loop = [d._decode_instruction(i) for i in cs.disasm(bytes.fromhex("9f4939d875fa"), 0x10004002)]
    _, state = T.lift_basic_block(lifter, D.BasicBlock(start=0x10004000, instructions=head))
    T.lift_basic_block(lifter, D.BasicBlock(start=0x10004002, instructions=loop))
    assert sites[0x10004002][1] == "unimplemented", sites
    stmts, _ = T.lift_basic_block(lifter, D.BasicBlock(start=0x10004002, instructions=loop), flag_state=state)
    assert "0x10004002" not in str(sites.get(0x10004002)) and 0x10004002 not in sites, sites
    assert any("/* lahf (cmp) */" in x for x in stmts), stmts
    assert lifts.COUNTS["lahf"] == {"idiom": 2, "lifted": 0, "unimplemented": 1}, lifts.COUNTS
    _, c = lift("0fafc1 9f", 0x10005000)
    assert 'RECOMP_UNIMPL("lahf", 0x10005003u);' in c and "/* lahf (" not in c, c
    assert sites[0x10005003] == ("lahf", "unimplemented"), sites
    assert lifts.COUNTS["lahf"] == {"idiom": 2, "lifted": 0, "unimplemented": 2}, lifts.COUNTS
    for code in ("d8d1", "d9e4", "d9e5", "ded9", "de10", "dde1"):
        _, c = lift(code, 0x10006000)
        assert "g_fp_cc = (uint16_t)((g_fp_cc & 0xB8FFu) | (" in c and c.count("g_fp_cc =") == 1, c
    _, c = lift("f390", 0x10003000)
    assert c == "RECOMP_BACK_EDGE(0x10003000u); /* pause */" and sites[0x10003000] == ("pause", "hint"), (c, sites)
    for name, case in CONFORM.items():
        if name.startswith(("lock_", "xchg")):
            _, c = lift(case[0], 0x10010000)
            assert "RT_ATOMIC_" in c and "RECOMP_UNIMPL" not in c, (name, c)
            assert ("MEM" not in c.split("RT_ATOMIC_")[1].split(";")[1]), (name, c)
    _, c = lift(CONFORM["lock_add8"][0], 0x10010000)
    assert "_cf = (int)((((uint64_t)(_lo) + (uint64_t)(LO8(edx))) >> 8) & 1);" in c, c
    _, c = lift(CONFORM["lock_sub16"][0], 0x10010000)
    assert "_cf = (int)((uint32_t)(_lo) < (uint32_t)(LO16(edx)));" in c, c
    _, c = lift("f0ff01 f0fe09 f0ff4908", 0x10011000)
    assert c.count("RT_ATOMIC_ADD(32, ecx, 1)") == 1 and "RT_ATOMIC_SUB(8, ecx, 1)" in c, c
    _, c = lift("8701 8611", 0x10012000)
    assert "RT_ATOMIC_XCHG(32, ecx, eax)" in c and "RT_ATOMIC_XCHG(8, ecx, LO8(edx))" in c, c
    _, c = lift("f00fc111 f00fb111", 0x10013000)
    assert c.count("RECOMP_ATOMIC_") == 2, c
    plain = lifts.COUNTS["lock"]["plain"]
    assert plain == 0, lifts.COUNTS
    _, c = lift("f00fab01", 0x10014000)
    assert 'RECOMP_UNIMPL("lock bts dword ptr [ecx], eax", 0x10014000u)' in c, c
    assert sites[0x10014000][1] == "unimplemented" and lifts.COUNTS["lock"]["plain"] == 1, lifts.COUNTS
    _, c = lift("d9e8 d9ee def9 d8749004", 0x10015000)
    assert c.count("rt_fdiv(") == 2, c
    _, c = lift("0f6f00 0f6bc0 0f6112 0ffe10 0f72e208 0f7f10 0f77 0f0fc8b4 0f590c24", 0x10016000)
    assert c.count("RT_SIMD_TRAP") == 2 and c.index("RT_SIMD_TRAP") > c.index("/* emms */"), c
    assert "mm2 = MMX_PUNPCKLWD(mm2, MMX_FROM32(MEM32(edx)));" in c, c
    _, u = lift("0f6012 0f6112 0f6212", 0x10017000)
    assert all(f"mm2 = MMX_PUNPCKL{s}(mm2, MMX_FROM32(MEM32(edx)));" in u for s in ("BW", "WD", "DQ")), u
    assert "MMX_MEM" not in u, u
    assert c.count("g_fp_top = 0;") == 7 and all(0x10016000 + k not in sites for k in (0, 3, 6, 9, 12, 16, 19)), c
    assert sites[0x10016015][1] == "simd-trap" and sites[0x10016019][1] == "simd-trap", sites


def write_selftest(out):
    os.makedirs(out, exist_ok=True)
    for name, cases in (("conform", CONFORM), ("simd", SIMD)):
        data = make_pe(assembled(cases))
        path = os.path.join(out, f"{name}.dll")
        if not os.path.exists(path) or open(path, "rb").read() != data:
            open(path, "wb").write(data)
    layout = os.path.join(out, "layout.toml")
    text = "".join(f"[modules.{n}]\nbase = 0x{BASE:08X}\n\n" for n in ("conform", "simd"))
    text += "[runtime]\ndata_cells = 0x00031000\n"
    if not os.path.exists(layout) or open(layout).read() != text:
        open(layout, "w").write(text)
    data = xf_pe()[0]
    path = os.path.join(out, "xflags.dll")
    if not os.path.exists(path) or open(path, "rb").read() != data:
        open(path, "wb").write(data)
    layout = os.path.join(out, "xflags-layout.toml")
    text = f"[modules.xflags]\nbase = 0x{BASE:08X}\n\n[runtime]\ndata_cells = 0x00031000\n"
    if not os.path.exists(layout) or open(layout).read() != text:
        open(layout, "w").write(text)


if __name__ == "__main__":
    test_layout()
    test_resolve()
    test_simd_detection()
    test_summary_refuses_unimplemented()
    test_make_pe_imports_tls()
    assembled(CONFORM)
    assembled(SIMD)
    assert len(xf_pe()[1]) == 16 * 6 + 12, len(xf_pe()[1])
    test_lifts()
    if len(sys.argv) > 1:
        write_selftest(sys.argv[1])
    print("ok")
