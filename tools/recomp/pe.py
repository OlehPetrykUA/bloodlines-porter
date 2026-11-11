import struct
from dataclasses import dataclass, field

SCN_EXECUTE = 0x20000000
SCN_CODE = 0x00000020


@dataclass
class Section:
    name: str
    rva: int
    vsize: int
    raw_ptr: int
    raw_size: int
    flags: int

    @property
    def executable(self):
        return bool(self.flags & (SCN_EXECUTE | SCN_CODE))


@dataclass
class PE:
    data: bytes
    image_base: int
    entry_rva: int
    size_of_image: int
    size_of_headers: int
    sections: list
    exports: dict = field(default_factory=dict)
    forwarders: dict = field(default_factory=dict)
    imports: list = field(default_factory=list)
    relocs: list = field(default_factory=list)
    ordinals: dict = field(default_factory=dict)
    tls: dict = None
    export_dir: tuple = (0, 0)

    def rva_to_off(self, rva):
        if rva < self.size_of_headers:
            return rva
        for s in self.sections:
            if s.rva <= rva < s.rva + max(s.vsize, s.raw_size):
                d = rva - s.rva
                return s.raw_ptr + d if d < s.raw_size else None
        return None

    def u32(self, rva):
        return struct.unpack_from("<I", self.data, self.rva_to_off(rva))[0]

    def cstr(self, rva):
        o = self.rva_to_off(rva)
        return self.data[o:self.data.index(b"\0", o)].decode("latin-1")

    def exec_ranges(self):
        return [(s.rva, s.rva + max(s.vsize, s.raw_size)) for s in self.sections if s.executable]

    def is_exec_rva(self, rva):
        return any(a <= rva < b for a, b in self.exec_ranges())

    def mapped(self, base=None):
        base = self.image_base if base is None else base
        img = bytearray(self.size_of_image)
        img[:self.size_of_headers] = self.data[:self.size_of_headers]
        for s in self.sections:
            n = min(s.raw_size, max(s.vsize, s.raw_size))
            img[s.rva:s.rva + n] = self.data[s.raw_ptr:s.raw_ptr + n]
        delta = (base - self.image_base) & 0xFFFFFFFF
        for r in self.relocs:
            v = struct.unpack_from("<I", img, r)[0]
            struct.pack_into("<I", img, r, (v + delta) & 0xFFFFFFFF)
        return bytes(img)


def parse(data):
    if data[:2] != b"MZ":
        raise ValueError("not an MZ image")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("no PE signature")
    machine, nsec, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", data, pe + 4)
    if machine != 0x14C:
        raise ValueError(f"machine 0x{machine:x} is not i386")
    opt = pe + 24
    if struct.unpack_from("<H", data, opt)[0] != 0x10B:
        raise ValueError("not PE32")
    entry = struct.unpack_from("<I", data, opt + 16)[0]
    base = struct.unpack_from("<I", data, opt + 28)[0]
    size_img, size_hdr = struct.unpack_from("<II", data, opt + 56)
    ndirs = struct.unpack_from("<I", data, opt + 92)[0]
    dirs = [struct.unpack_from("<II", data, opt + 96 + 8 * i) for i in range(ndirs)]
    sections = []
    for i in range(nsec):
        o = opt + opt_size + 40 * i
        name = data[o:o + 8].rstrip(b"\0").decode("latin-1")
        vsize, rva, raw_size, raw_ptr = struct.unpack_from("<IIII", data, o + 8)
        flags = struct.unpack_from("<I", data, o + 36)[0]
        sections.append(Section(name, rva, vsize, raw_ptr, raw_size, flags))
    p = PE(data, base, entry, size_img, size_hdr, sections)
    if ndirs > 0 and dirs[0][0]:
        _exports(p, *dirs[0])
    if ndirs > 1 and dirs[1][0]:
        _imports(p, dirs[1][0])
    if ndirs > 5 and dirs[5][0]:
        _relocs(p, *dirs[5])
    if ndirs > 9 and dirs[9][0]:
        start, end, index, callbacks, zero_fill = struct.unpack_from("<IIIII", data, p.rva_to_off(dirs[9][0]))
        cbs = []
        while callbacks and p.u32(callbacks - base + 4 * len(cbs)):
            cbs.append(p.u32(callbacks - base + 4 * len(cbs)))
        p.tls = {"start": start, "end": end, "index": index, "callbacks": cbs, "zero_fill": zero_fill}
    return p


def _exports(p, rva, size):
    _, _, _, _, _, ordbase, nfun, nnames, afun, anames, aords = struct.unpack_from("<IIHHIIIIIII", p.data, p.rva_to_off(rva))
    p.export_dir = (rva, size)
    funcs = [p.u32(afun + 4 * i) for i in range(nfun)]
    named = {}
    for i in range(nnames):
        name = p.cstr(p.u32(anames + 4 * i))
        idx = struct.unpack_from("<H", p.data, p.rva_to_off(aords + 2 * i))[0]
        named[idx] = name
    for idx, frva in enumerate(funcs):
        if not frva:
            continue
        name = named.get(idx, f"#{ordbase + idx}")
        p.ordinals[f"#{ordbase + idx}"] = name
        if rva <= frva < rva + size:
            p.forwarders[name] = p.cstr(frva)
        else:
            p.exports[name] = frva


def _imports(p, rva):
    while True:
        oft, _, _, name_rva, ft = struct.unpack_from("<IIIII", p.data, p.rva_to_off(rva))
        if not (oft or name_rva or ft):
            break
        dll = p.cstr(name_rva)
        lookup = oft or ft
        i = 0
        while True:
            e = p.u32(lookup + 4 * i)
            if not e:
                break
            sym = f"#{e & 0xFFFF}" if e & 0x80000000 else p.cstr(e + 2)
            p.imports.append((dll, sym, ft + 4 * i))
            i += 1
        rva += 20


def _relocs(p, rva, size):
    end = rva + size
    while rva < end:
        page, block = struct.unpack_from("<II", p.data, p.rva_to_off(rva))
        if block < 8:
            break
        for j in range((block - 8) // 2):
            e = struct.unpack_from("<H", p.data, p.rva_to_off(rva + 8 + 2 * j))[0]
            kind = e >> 12
            if kind == 3:
                p.relocs.append(page + (e & 0xFFF))
            elif kind != 0:
                raise ValueError(f"unsupported relocation type {kind}")
        rva += block
