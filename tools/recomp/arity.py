import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discover
import pe

SLOT = r"\[{} \+ (0x[0-9a-f]+|\d+)\]"


def args_read(im, va):
    depth, frame, seen = 0, None, set()
    o = va - im.base
    for insn in im.cs.disasm(im.img[o:o + 0x400], va):
        ops = insn.op_str
        for reg, ref in (("esp", depth), ("ebp", frame)):
            if ref is None:
                continue
            for m in re.finditer(SLOT.format(reg), ops):
                off = int(m.group(1), 0) - ref
                if off >= 4:
                    seen.add(off // 4)
        if insn.mnemonic == "push":
            depth += 4
        elif insn.mnemonic == "pop":
            depth -= 4
        elif insn.mnemonic == "mov" and ops == "ebp, esp":
            frame = depth
        elif insn.mnemonic in ("sub", "add") and re.fullmatch(r"esp, (0x[0-9a-f]+|\d+)", ops):
            depth += int(ops[5:], 0) * (1 if insn.mnemonic == "sub" else -1)
        if insn.mnemonic == "ret":
            break
    return sorted(seen)


def main():
    p = pe.parse(open(sys.argv[1], "rb").read())
    im = discover.Image(p, p.image_base)
    for name in sys.argv[2:]:
        print(name, "reads stack arguments", args_read(im, p.image_base + p.exports[name]))


if __name__ == "__main__":
    main()
