import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arity
import discover
import translate


class FakePE:
    relocs = []
    entry_rva = 0x1000
    exports = {}

    def __init__(self, code, relocs=()):
        self.img = bytes(0x1000) + code + bytes(0x1000 - len(code))
        self.relocs = list(relocs)

    def mapped(self, base):
        return self.img

    def exec_ranges(self):
        return [(0x1000, 0x2000)]


def test_scan():
    code = bytes.fromhex("a300110010" "ff248500200010")
    im = discover.Image(FakePE(code), 0x10000000)
    seen, _, _, _, _ = discover.descend(im, 0x10001000, 0x10002000)
    found = discover.scan(im, seen)
    assert found == {"unbounded_tables": [0x10001005], "text_writes": [0x10001000]}, found


def test_global_ebp():
    text = "void f(void)\n{\n    uint32_t ebp = 0;\n    ebp = g_ebp;  /* c */\n    eax = (uint32_t)(ebp);\n}\n"
    out = translate.global_ebp(text, "x.c")
    assert "uint32_t ebp" not in out and "ebp = g_ebp" not in out and "(uint32_t)(ebp)" in out, out
    try:
        translate.global_ebp("    volatile uint32_t ebp;\n", "y.c")
    except SystemExit as e:
        assert "y.c" in str(e)
    else:
        raise AssertionError("survivor not caught")


def test_arity_register_adjust():
    im = discover.Image(FakePE(bytes.fromhex("2be0" "8b442404" "c3")), 0x10000000)
    assert arity.args_read(im, 0x10001000) == [1]


def test_tables_in_code():
    code = bytearray(0x40)
    code[0:7] = bytes.fromhex("ff248510100010")
    code[0x10:0x14] = (0x10001020).to_bytes(4, "little")
    pe = FakePE(bytes(code), [0x1003, 0x1010])
    pe.img = bytearray(pe.img)
    pe.img[0x800:0x804] = (0x10001010).to_bytes(4, "little")
    pe.relocs.append(0x800)
    im = discover.Image(pe, 0x10000000)
    assert not im.arm_slot(0x10001003) and im.arm_slot(0x10001010)
    funcs, interior, _, _ = discover.discover(im)
    assert interior.get(0x10001010) == "data table in code" and 0x10001010 not in funcs, (interior, sorted(funcs))


def test_split_table_arms():
    code = bytearray(0x40)
    code[0:7] = bytes.fromhex("ff248520100010")
    code[7] = 0xC3
    code[8] = 0xC3
    code[0x20:0x24] = (0x10001007).to_bytes(4, "little")
    code[0x24:0x28] = (0x10001030).to_bytes(4, "little")
    code[0x30] = 0xC3
    pe = FakePE(bytes(code), [0x1020, 0x1024])
    pe.exports = {"g": 0x1030}
    im = discover.Image(pe, 0x10000000)
    funcs, _, _, _ = discover.discover(im)
    assert 0x10001007 in funcs and "table-arm" in funcs[0x10001007]["kinds"], sorted(funcs)


if __name__ == "__main__":
    test_split_table_arms()
    test_scan()
    test_arity_register_adjust()
    test_tables_in_code()
    test_global_ebp()
    print("ok")
