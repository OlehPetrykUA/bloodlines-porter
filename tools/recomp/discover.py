import bisect
import struct

from capstone import CS_AC_WRITE, CS_ARCH_X86, CS_MODE_32, CS_OP_IMM, CS_OP_MEM, Cs
from capstone.x86 import X86_REG_INVALID

STOP = {"ret", "retf", "int3", "hlt", "ud2", "iret", "iretd"}


class Image:
    def __init__(self, pe, base):
        self.pe = pe
        self.base = base
        self.img = pe.mapped(base)
        self.ranges = [(base + a, base + b) for a, b in pe.exec_ranges()]
        self.slots = {}
        for r in pe.relocs:
            self.slots[base + r] = struct.unpack_from("<I", self.img, r)[0]
        self.cs = Cs(CS_ARCH_X86, CS_MODE_32)
        self.cs.detail = True

    def is_code(self, va):
        return any(a <= va < b for a, b in self.ranges)

    def decode(self, va):
        o = va - self.base
        return next(self.cs.disasm(self.img[o:o + 16], va), None)

    def code_slot(self, va):
        return va in self.slots and self.is_code(self.slots[va])

    def arm_slot(self, va):
        if not self.code_slot(va):
            return False
        prev = self.decode(va - 3)
        return not (prev is not None and prev.mnemonic == "jmp" and prev.disp_offset == 3 and _table_va(prev))

    def table(self, va, insn):
        own = range(insn.address, insn.address + insn.size)
        near = [s for s in [*range(va, va + 13), *range(va - 1, va - 13, -1)] if self.arm_slot(s) and s not in own]
        if not near:
            return []
        lo = hi = near[0]
        while self.arm_slot(lo - 4):
            lo -= 4
        while self.arm_slot(hi + 4):
            hi += 4
        return [self.slots[s] for s in range(lo, hi + 4, 4)]


def _imm_target(insn):
    if insn.operands and insn.operands[0].type == CS_OP_IMM:
        return insn.operands[0].imm & 0xFFFFFFFF
    return None


def _table_va(insn):
    if len(insn.operands) != 1 or insn.operands[0].type != CS_OP_MEM:
        return None
    m = insn.operands[0].mem
    if m.base == X86_REG_INVALID and m.index != X86_REG_INVALID and m.scale == 4:
        return m.disp & 0xFFFFFFFF
    return None


def descend(im, start, limit):
    seen, work, calls, tables, outside, mem_refs = {}, [start], set(), {}, set(), set()
    while work:
        va = work.pop()
        while va not in seen:
            if not (start <= va < limit):
                outside.add(va)
                break
            insn = im.decode(va)
            if insn is None:
                break
            seen[va] = insn.size
            if insn.disp_offset and insn.disp_size == 4:
                mem_refs.add(va + insn.disp_offset)
            m = insn.mnemonic
            nxt = va + insn.size
            if m == "call":
                t = _imm_target(insn)
                if t is not None and im.is_code(t):
                    calls.add(t)
                va = nxt
            elif m == "jmp":
                t = _imm_target(insn)
                if t is not None:
                    work.append(t)
                else:
                    tv = _table_va(insn)
                    if tv is not None:
                        arms = im.table(tv, insn)
                        tables[tv] = arms
                        work.extend(arms)
                break
            elif m.startswith("j") or m.startswith("loop"):
                t = _imm_target(insn)
                if t is not None:
                    work.append(t)
                va = nxt
            elif m in STOP:
                break
            else:
                va = nxt
    return seen, calls, tables, outside, mem_refs


def _close(im, base_seeds, extra, memo):
    kinds = {va: set(k) for va, k in base_seeds.items()}
    for va in extra:
        kinds.setdefault(va, set()).add("reloc")
    ends = sorted(b for _, b in im.ranges)
    while True:
        n = len(kinds)
        order = sorted(kinds)
        bodies, tables, mem_refs = {}, {}, set()
        for i, f in enumerate(order):
            limit = min([e for e in ends if e > f] + ([order[i + 1]] if i + 1 < len(order) else []))
            if (f, limit) not in memo:
                memo[(f, limit)] = descend(im, f, limit)
            seen, calls, tabs, outside, refs = memo[(f, limit)]
            bodies[f] = seen
            tables.update(tabs)
            mem_refs |= refs
            for c in calls:
                kinds.setdefault(c, set()).add("call")
            for t in outside:
                if im.is_code(t):
                    kinds.setdefault(t, set()).add("tail-jump")
            for arms in tabs.values():
                if any(not (f <= a < limit) for a in arms):
                    for a in arms:
                        kinds.setdefault(a, set()).add("table-arm")
        if len(kinds) == n:
            return kinds, bodies, tables, mem_refs


def discover(im, extra_seeds=()):
    pe, base = im.pe, im.base
    seeds = {base + pe.entry_rva: {"entry"}}
    for rva in pe.exports.values():
        if im.is_code(base + rva):
            seeds.setdefault(base + rva, set()).add("export")
    for va in extra_seeds:
        seeds.setdefault(va, set()).add("extra-entry")
    code_slots = {s: t for s, t in im.slots.items() if im.is_code(t)}
    reloc_targets = set(code_slots.values())
    extra, memo = set(), {}
    while True:
        kinds, bodies, tables, disp_slots = _close(im, seeds, extra, memo)
        arms = {a for t in tables.values() for a in t}
        insn_bytes = {a + i for seen in bodies.values() for a, n in seen.items() for i in range(n)}
        nxt, slot_kind = set(), {}
        for s, t in code_slots.items():
            if s in disp_slots or t in im.slots:
                slot_kind[t] = "data table in code"
            elif im.is_code(s) and s not in insn_bytes:
                slot_kind[t] = "jump-table arm"
            else:
                nxt.add(t)
        if nxt == extra:
            break
        extra = nxt
    for t in reloc_targets & set(kinds):
        kinds[t].add("reloc")

    for f in kinds:
        insn = im.decode(f)
        if insn is not None and insn.mnemonic == "jmp" and insn.size == 5:
            kinds[f].add("ilt-thunk")
    interior = {}
    for t in sorted(reloc_targets - set(kinds)):
        interior[t] = slot_kind.get(t, "interior label")
    funcs = {}
    for f, seen in bodies.items():
        end = max(a + n for a, n in seen.items()) if seen else f
        funcs[f] = {"end": end, "kinds": sorted(kinds[f]), "insns": set(seen), **scan(im, seen)}
    return funcs, interior, tables, reloc_targets


def scan(im, seen):
    unbounded, text_writes = [], []
    for va in sorted(seen):
        insn = im.decode(va)
        if insn.mnemonic == "jmp":
            tv = _table_va(insn)
            if tv is not None and not im.table(tv, insn):
                unbounded.append(va)
        for op in insn.operands:
            if (op.type == CS_OP_MEM and op.access & CS_AC_WRITE and op.mem.base == X86_REG_INVALID
                    and op.mem.index == X86_REG_INVALID and im.is_code(op.mem.disp & 0xFFFFFFFF)):
                text_writes.append(va)
    return {"unbounded_tables": unbounded, "text_writes": text_writes}


_starts = {}


def owner(funcs, va):
    key = (id(funcs), len(funcs))
    if key not in _starts:
        _starts.clear()
        _starts[key] = sorted(funcs)
    starts = _starts[key]
    i = j = bisect.bisect_right(starts, va) - 1
    hit = None
    while j >= 0 and (j == i or starts[j + 1] + 15 > va):
        if va < funcs[starts[j]]["end"]:
            hit = starts[j]
        j -= 1
    return hit
