import re

from capstone import x86_const as X
from recomp import disasm as D
from recomp import lifter as L
from recomp import translator as T

SIMD_GROUPS = {X.X86_GRP_MMX, X.X86_GRP_3DNOW, X.X86_GRP_SSE1, X.X86_GRP_SSE2, X.X86_GRP_SSE3, X.X86_GRP_SSSE3,
               X.X86_GRP_SSE41, X.X86_GRP_SSE42, X.X86_GRP_SSE4A, X.X86_GRP_AVX, X.X86_GRP_AVX2}
SIMD_REG = re.compile(r"\b(x?mm[0-7])\b")
SIMD_ALWAYS = {"emms", "femms", "ldmxcsr", "stmxcsr"}
MMX_OPS = frozenset((
    "movd movq packsswb packssdw packuswb paddb paddw paddd paddsb paddsw paddusb paddusw pand pandn "
    "pcmpeqb pcmpeqw pcmpeqd pcmpgtb pcmpgtw pcmpgtd pmaddwd pmulhw pmullw por psllw pslld psllq psraw psrad "
    "psrlw psrld psrlq psubb psubw psubd psubsb psubsw psubusb psubusw punpckhbw punpckhwd punpckhdq "
    "punpcklbw punpcklwd punpckldq pxor emms").split())
MMX_REG = re.compile(r"\bmm[0-7]\b")
UNPACK_LOW = {"punpcklbw": "MMX_PUNPCKLBW", "punpcklwd": "MMX_PUNPCKLWD", "punpckldq": "MMX_PUNPCKLDQ"}
COMMENT = re.compile(r"\s*/\*.*\*/\s*")
NOOP = re.compile(r"/\* (nop|wait - FPU sync|(stc|clc|cmc): no adc/sbb|bt .*: no CF consumer)")
ATOMIC_OPS = {"add": "ADD", "sub": "SUB", "inc": "ADD", "dec": "SUB", "and": "AND", "or": "OR", "xor": "XOR"}
SYMBOL_OP = {"+": "ADD", "-": "SUB", "&": "AND", "|": "OR", "^": "XOR"}
STORE = re.compile(r"(MEM(8|16|32))\((.*)\) = \1\(\3\) ([-+&|^]) (.*);")
FDIV = {"fdiv", "fdivp", "fdivr", "fdivrp", "fidiv", "fidivr"}
FCC = {"fcom", "fcomp", "fcompp", "fucom", "fucomp", "fucompp", "ficom", "ficomp", "ftst", "fxam"}
FCC_SET = re.compile(r"g_fp_cc = (RECOMP_FCMP_CC\(g_fp_cmp\)|recomp_fxam\(fp_top\(\)\));")
AH_BITS = tuple(f"? 0x{b:02X}u : 0u" for b in (0x80, 0x40, 0x04, 0x01))

COUNTS = {}
_lahf, _lock, _orig = {}, {}, {}


def _recount():
    COUNTS["lock"] = {k: sum(v == k for v in _lock.values()) for k in ("atomic", "plain")}
    COUNTS["lahf"] = {k: sum(v == k for v in _lahf.values()) for k in ("idiom", "lifted", "unimplemented")}


def is_hint(m):
    return m.startswith("prefetch") or m in ("sfence", "lfence", "mfence", "pause")


def is_simd(insn):
    return (insn.mnemonic in SIMD_ALWAYS or bool(SIMD_GROUPS & set(insn.groups))
            or bool(SIMD_REG.search(insn.op_str)))


def is_mmx(insn):
    return (insn.mnemonic in MMX_OPS and "xmm" not in insn.op_str
            and (insn.mnemonic == "emms" or bool(MMX_REG.search(insn.op_str))))


def lift_mmx(self, insn, orig):
    m, ops = insn.mnemonic, insn.operands
    if m == "emms":
        return ["g_fp_top = 0; /* emms */"]
    if m in UNPACK_LOW and ops[1].type == "mem":
        out = [f"{ops[0].reg} = {UNPACK_LOW[m]}({ops[0].reg}, MMX_FROM32(MEM32({L._fmt_mem(ops[1])})));"
               f" /* {text_of(insn)} */"]
    else:
        out = orig(self, insn)
    if not out or any("RECOMP_UNIMPL" in s for s in out):
        if insn.address in self.unimplemented.get(m, []):
            self.unimplemented[m].remove(insn.address)
        return None
    return out + ["g_fp_top = 0;"]


def text_of(insn):
    return f"{insn.mnemonic} {insn.op_str}".strip()


def unimpl(text, va):
    return [f'RECOMP_UNIMPL("{text}", 0x{va:08X}u);']


def atomic_rmw(lines, op):
    for k, s in enumerate(lines):
        mt = STORE.fullmatch(s.strip())
        if mt:
            break
    else:
        return None
    acc, bits, addr, sym, src = mt.groups()
    if SYMBOL_OP[sym] != op:
        return None
    t, mem = f"uint{bits}_t", f"{acc}({addr})"
    pre = [s.replace(mem, "_lo") for s in lines[:k]]
    post = [s.replace(mem, "_ln") for s in lines[k + 1:]]
    if any(mem in s for s in pre + post) or mem in src:
        return None
    head = (f"{{ {t} _lo = RT_ATOMIC_{op}({bits}, {addr}, {src});"
            f" {t} _ln = ({t})(_lo {sym} ({t})({src}));")
    return [head] + pre + post + ["}"]


def atomic_xchg(insn):
    ops = insn.operands
    if len(ops) != 2 or sorted(o.type for o in ops) != ["mem", "reg"]:
        return None
    mem, reg = (ops[0], ops[1]) if ops[0].type == "mem" else (ops[1], ops[0])
    bits = 8 * (mem.mem_size or 4)
    if bits not in (8, 16, 32):
        return None
    return [f"{{ uint32_t _tmp = RT_ATOMIC_XCHG({bits}, {L._fmt_mem(mem)}, {L._fmt_operand_read(reg)});",
            L._fmt_operand_write(reg, "_tmp") + f" }} /* {text_of(insn)} */"]


def fdiv_ze(s):
    if "RECOMP_FP_PC(" not in s or s.count(" / ") != 1:
        return s
    i = s.index("RECOMP_FP_PC(") + len("RECOMP_FP_PC(")
    j = s.index(");", i)
    return s[:i] + "rt_fdiv(" + s[i:j].replace(" / ", ", ") + ")" + s[j:]


def keep_exceptions(s):
    return FCC_SET.sub(lambda mt: f"g_fp_cc = (uint16_t)((g_fp_cc & 0xB8FFu) | ({mt.group(1)}));", s)


def install():
    COUNTS.clear()
    _lahf.clear()
    _lock.clear()
    _recount()
    sites = {}
    if not _orig:
        _orig.update(lift=L.Lifter.lift_instruction, decode=D.Disassembler._decode_instruction,
                     block=T.lift_basic_block)
    orig, decode, block = _orig["lift"], _orig["decode"], _orig["block"]

    def decode_unlocked(self, cs_insn):
        insn = decode(self, cs_insn)
        if insn.mnemonic.startswith("lock ") and insn.mnemonic[5:] not in ("xadd", "cmpxchg"):
            insn.mnemonic = insn.mnemonic[5:]
            insn.locked = True
        return insn

    D.Disassembler._decode_instruction = decode_unlocked

    def lift_basic_block(lifter, bb, flag_state=None):
        lahfs = [i.address for i in bb.instructions if i.mnemonic == "lahf"]
        for va in lahfs:
            _lahf.pop(va, None)
            sites.pop(va, None)
        stmts, state = block(lifter, bb, flag_state)
        tracked = iter([va for va in lahfs if va not in _lahf])
        out = []
        for st in stmts:
            if "/* lahf (" in st:
                va = next(tracked, None)
                if va is not None and not all(b in st for b in AH_BITS):
                    st = unimpl("lahf", va)[0]
                    sites[va] = ("lahf", "unimplemented")
                    _lahf[va] = "unimplemented"
            out.append(st)
        for va in lahfs:
            _lahf.setdefault(va, "idiom")
        _recount()
        return out, state

    T.lift_basic_block = lift_basic_block
    L.lift_basic_block = lift_basic_block

    def locked(self, insn):
        m, ops = insn.mnemonic, insn.operands
        out = None
        if m in ATOMIC_OPS and ops and ops[0].type == "mem":
            out = atomic_rmw(orig(self, insn), ATOMIC_OPS[m])
        elif m == "xchg":
            out = atomic_xchg(insn)
        _lock[insn.address] = "atomic" if out else "plain"
        _recount()
        if out:
            return out
        text = text_of(insn) if m == "xchg" and not getattr(insn, "locked", False) else f"lock {text_of(insn)}"
        sites[insn.address] = (text, "unimplemented")
        return unimpl(text, insn.address)

    def lift_instruction(self, insn):
        m = insn.mnemonic
        if m == "pause":
            sites[insn.address] = (text_of(insn), "hint")
            return [f"RECOMP_BACK_EDGE(0x{insn.address:08X}u); /* pause */"]
        if is_hint(m):
            sites[insn.address] = (text_of(insn), "hint")
            return orig(self, insn)
        if is_mmx(insn):
            out = lift_mmx(self, insn, orig)
            if out:
                sites.pop(insn.address, None)
                return out
        if is_simd(insn):
            sites[insn.address] = (text_of(insn), "simd-trap")
            return [f'RT_SIMD_TRAP("{text_of(insn)}", 0x{insn.address:08X}u);']
        if getattr(insn, "locked", False) or (m == "xchg" and any(o.type == "mem" for o in insn.operands)):
            return locked(self, insn)
        if m in ("lock xadd", "lock cmpxchg"):
            out = orig(self, insn)
            _lock[insn.address] = "atomic" if any("RECOMP_ATOMIC_" in s for s in out) else "plain"
            _recount()
            return out
        own = LIFTS.get(m)
        if own:
            out = own(self, insn)
            if out is not None:
                return out
        out = orig(self, insn)
        if m in FDIV:
            out = [fdiv_ze(s) for s in out]
        if m in FCC:
            out = [keep_exceptions(s) for s in out]
        if m == "int3":
            sites[insn.address] = ("int3", "fault-as-original")
            return unimpl("int3", insn.address)
        if out and all(COMMENT.fullmatch(s) for s in out) and not any(NOOP.search(s) for s in out):
            sites[insn.address] = (text_of(insn), "unimplemented")
            if m == "lahf":
                _lahf[insn.address] = "unimplemented"
                _recount()
            return unimpl(text_of(insn), insn.address)
        return out

    L.Lifter.lift_instruction = lift_instruction
    return sites


def mem_arg(insn):
    ops = insn.operands
    return L._fmt_mem(ops[0]) if len(ops) == 1 and ops[0].type == "mem" else None


def call_with_mem(fn):
    def lift(self, insn):
        a = mem_arg(insn)
        return None if a is None else [f"{fn}({a}); /* {text_of(insn)} */"]
    return lift


LIFTS = {
    "fnclex": lambda self, insn: ["g_fp_cc &= 0x7F00u; /* fnclex */"],
    "fninit": lambda self, insn: ["rt_fninit(); /* fninit */"],
    "ffree": lambda self, insn: [f"(void)0; /* {text_of(insn)}: tags are derived from the stack depth */"],
    "fnstenv": call_with_mem("rt_fnstenv"),
    "fldenv": call_with_mem("rt_fldenv"),
    "fnsave": call_with_mem("rt_fnsave"),
    "frstor": call_with_mem("rt_frstor"),
}
