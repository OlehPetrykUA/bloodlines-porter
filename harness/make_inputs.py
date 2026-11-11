import json
import random
import struct
import sys

INT_MIN, INT_MAX = -2**31, 2**31 - 1
SEEDS = [0, 1, -1, INT_MIN, INT_MAX, 2, 7, 42, 100, 255, 256, 1000, 4096, 65535, 65536, 123456,
         1234567, 99999999, -2, -42, -1000, -65536, -123456, -99999999, 0x12345678, 0x7FFF0000,
         -0x12345678, 31337, 8675309, 271828, 314159, 161803]
INT_BOUNDS = [(0, 0), (0, 1), (0, 9), (1, 6), (-5, 5), (-100, 100), (0, 255), (10, 20),
              (-1000000, 1000000), (0, 0x7FFFFFFE), (-0x40000000, 0x3FFFFFFF), (100, 100),
              (-1, 0), (3, 4), (0, 65535), (-7, -3)]
FLOAT_BOUNDS = [(0.0, 1.0), (-1.0, 1.0), (0.0, 100.0), (-1000.5, 1000.25), (0.001, 0.002),
                (5.0, 5.0), (-3.5, -1.25), (0.0, 360.0), (1e-6, 1e6), (-0.0, 0.0),
                (2.5, 2.75), (-1e30, 1e30)]
GAUSS = [(0.0, 1.0), (10.0, 2.0), (-5.0, 0.5), (100.0, 25.0), (0.0, 0.0), (1.5, 0.001)]
CHARS = (b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
         b"     ..//\\\\\t\n_-+:;,!?#%" + bytes(range(0x80, 0x100)))
WORDS = [b"", b"a", b"A", b"maps\\test.bsp", b"models/props/chair.mdl", b"C:\\VTMB\\Bin\\x.dll",
         b"noext", b".hidden", b"dir.with.dots/file", b"trailing/", b"\\\\server\\share\\f.txt",
         b"  padded  ", b"\t\n tabs \r\n", b"\xc0\xe9\xff\x80", b"MiXeD CaSe", b"x" * 63,
         b"12345", b"-42", b"+7", b"0x1F", b"3.14159", b"-0.5e3", b"1e10", b"  12abc", b"\xb5\xdf"]


def f32(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def rstr(r, n):
    return bytes(r.choice(CHARS) for _ in range(n))


def text(r):
    k = r.random()
    if k < 0.3:
        return r.choice(WORDS)
    if k < 0.5:
        return rstr(r, r.randint(0, 8))
    if k < 0.8:
        return rstr(r, r.randint(0, 40))
    return r.choice(WORDS) + rstr(r, r.randint(0, 12)) + r.choice(WORDS)


def bufsize(r, s):
    return r.choice([len(s) + 1, len(s), max(1, len(s) - 1), len(s) + 1 + r.randint(1, 16), 1, 2, 64])


def numtext(r):
    sign = r.choice(["", "-", "+", " ", "  -"])
    whole = str(r.randint(0, 10 ** r.randint(0, 12)))
    frac = r.choice(["", "." + str(r.randint(0, 99999)), "."])
    exp = r.choice(["", "", "e" + str(r.randint(-40, 40)), "E+5", "e-3"])
    tail = r.choice(["", "", "x", " 12", "abc"])
    return (sign + r.choice([whole, "0x" + format(r.randint(0, 0xFFFFFF), "x"), whole + frac + exp]) + tail).encode()


def snprintf_case(r):
    kinds = []
    fmt = b""
    for _ in range(r.randint(0, 4)):
        fmt += rstr(r, r.randint(0, 6)).replace(b"%", b"")
        k = r.choice(["d", "i", "u", "x", "X", "s", "c", "%", "5d", "-8s", "08x", "f", ".3f", "g", "e", ".1f", "10.4f"])
        fmt += b"%" + k.encode()
        if k == "%":
            continue
        if k[-1] in "diuxXc":
            v = r.choice([0, 1, -1, INT_MIN, INT_MAX, r.randint(INT_MIN, INT_MAX), ord("A"), 0xC9])
            kinds.append(["i", v & 0xFFFFFFFF])
        elif k[-1] == "s":
            kinds.append(["s", text(r).replace(b"\0", b"").hex()])
        else:
            v = r.choice([0.0, -0.0, 1.0, -1.5, 3.14159, 1e10, 1e-5, 123456.789, -98765.4321, 0.5, 2.5,
                          r.uniform(-1e6, 1e6), r.uniform(-1, 1)])
            kinds.append(["d", struct.unpack("<Q", struct.pack("<d", v))[0]])
    fmt += rstr(r, r.randint(0, 5)).replace(b"%", b"")
    size = r.choice([1, 2, 8, 16, 32, 64, 128, 256])
    return {"size": size, "fmt": fmt.hex(), "args": kinds}


def main(path):
    r = random.Random(20261026)
    pick = lambda table: "".join(format(r.randrange(len(table)), "x") for _ in range(1000))
    tables = {
        "int": [[lo & 0xFFFFFFFF, hi & 0xFFFFFFFF] for lo, hi in INT_BOUNDS],
        "float": [[f32(lo), f32(hi)] for lo, hi in FLOAT_BOUNDS],
        "gauss": [[f32(m), f32(s)] for m, s in GAUSS],
    }
    random_cases = [{"seed": seed & 0xFFFFFFFF, "int": pick(INT_BOUNDS), "float": pick(FLOAT_BOUNDS),
                     "gauss": pick(GAUSS), "gint": pick(INT_BOUNDS), "gfloat": pick(FLOAT_BOUNDS)}
                    for seed in SEEDS]
    strings = {}

    def gen(name, make):
        strings[name] = [make() for _ in range(500)]

    def two():
        a = text(r)
        b = r.choice([a, a.upper(), a.lower(), text(r), a[:r.randint(0, len(a))]])
        return {"a": a.hex(), "b": b.hex(), "n": r.choice([0, 1, 3, len(a), len(a) + 5, 100])}

    def stristr():
        a = text(r)
        i = r.randint(0, len(a))
        b = r.choice([a[i:i + r.randint(0, 6)], a[i:i + 3].swapcase(), text(r)[:4], b""])
        return {"a": a.hex(), "b": b.hex()}

    def copy():
        s = text(r)
        return {"s": s.hex(), "size": bufsize(r, s)}

    gen("Q_strncpy", copy)
    gen("Q_strcasecmp", two)
    gen("Q_strncasecmp", two)
    gen("Q_stristr", stristr)
    gen("Q_atoi", lambda: {"s": (numtext(r) if r.random() < 0.8 else text(r)).hex()})
    gen("Q_atof", lambda: {"s": (numtext(r) if r.random() < 0.8 else text(r)).hex()})
    gen("Q_snprintf", lambda: snprintf_case(r))
    gen("Q_FileBase", copy)
    gen("Q_StripExtension", copy)
    gen("Q_FixSlashes", lambda: {"s": text(r).hex(), "size": 80, "delim": r.choice([0x2F, 0x5C])})
    gen("Q_strnlwr", lambda: {"s": text(r).hex(), "size": 80, "n": r.choice([0, 1, 5, 20, 79])})
    def trim():
        pad = lambda: bytes(r.choice(b" \t\n\r ") for _ in range(r.choice([0, 0, 1, 2, 5])))
        s = pad() + text(r) + pad()
        return {"s": s.hex(), "size": r.choice([len(s) + 1, len(s), max(1, len(s) - 3), 1, 0, 2, 64])}

    gen("Q_trimspace", trim)
    json.dump({"tables": tables, "random": random_cases, "strings": strings}, open(path, "w"), separators=(",", ":"))


if __name__ == "__main__":
    main(sys.argv[1])
