import math
import plistlib
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

RT_ICON = 3
RT_GROUP_ICON = 14
SIZES = (16, 32, 128, 256, 512)
ICON = 1024
TILE = 824
RADIUS = 185
SMALL_CROP = 942


class IconError(Exception):
    pass


def sections(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        raise IconError("not a PE file")
    nsec, osz = struct.unpack_from("<H", d, pe + 6)[0], struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    dd = opt + (96 if struct.unpack_from("<H", d, opt)[0] == 0x10B else 112)
    rva = struct.unpack_from("<I", d, dd + 16)[0]
    secs = [struct.unpack_from("<IIII", d, opt + osz + 40 * i + 8) for i in range(nsec)]
    return rva, secs


def offset(secs, rva):
    for vs, va, rs, rp in secs:
        if va <= rva < va + max(vs, rs):
            return rva - va + rp
    raise IconError("RVA %#x is in no section" % rva)


def resources(d):
    root_rva, secs = sections(d)
    if not root_rva:
        raise IconError("no resource directory")
    root = offset(secs, root_rva)

    def entries(o):
        named, ids = struct.unpack_from("<HH", d, o + 12)
        for i in range(named + ids):
            yield struct.unpack_from("<II", d, o + 16 + 8 * i)

    def leaf(target):
        while target & 0x80000000:
            target = next(entries(root + (target & 0x7FFFFFFF)))[1]
        rva, size = struct.unpack_from("<II", d, root + target)
        o = offset(secs, rva)
        return d[o:o + size]

    out = {}
    for typ, sub in entries(root):
        if typ in (RT_ICON, RT_GROUP_ICON) and sub & 0x80000000:
            for name, target in entries(root + (sub & 0x7FFFFFFF)):
                if not name & 0x80000000:
                    out.setdefault(typ, {})[name] = leaf(target)
    return out


def frames(exe):
    try:
        res = resources(Path(exe).read_bytes())
    except (OSError, struct.error, StopIteration) as e:
        raise IconError("cannot read %s: %s" % (exe, e))
    groups, icons = res.get(RT_GROUP_ICON), res.get(RT_ICON, {})
    if not groups:
        raise IconError("%s has no icon group" % Path(exe).name)
    g = groups[min(groups)]
    rows = []
    for i in range(struct.unpack_from("<H", g, 4)[0]):
        w, _, _, _, _, bits, _, nid = struct.unpack_from("<BBBBHHIH", g, 6 + 14 * i)
        if nid in icons and bits in (4, 8):
            rows.append((w or 256, bits, icons[nid]))
    if not rows:
        raise IconError("%s has no 16 or 256 colour icon" % Path(exe).name)
    return rows


def dib_lit(blob):
    size, w, h = struct.unpack_from("<Iii", blob)
    bits = struct.unpack_from("<H", blob, 14)[0]
    h //= 2
    colors = struct.unpack_from("<I", blob, 32)[0] or 1 << bits
    pal = [blob[size + 4 * i:size + 4 * i + 3] for i in range(colors)]
    data = size + 4 * colors
    stride, mask_stride = ((w * bits + 31) // 32) * 4, ((w + 31) // 32) * 4
    mask = data + stride * h
    lit = [[0.0] * w for _ in range(h)]
    for y in range(h):
        row, mrow = data + stride * (h - 1 - y), mask + mask_stride * (h - 1 - y)
        for x in range(w):
            if blob[mrow + x // 8] >> (7 - x % 8) & 1:
                lit[y][x] = 1.0
                continue
            b, g, r = pal[blob[row + x // 2] >> (0 if x % 2 else 4) & 15 if bits == 4 else blob[row + x]]
            lit[y][x] = 0.0 if not (r or g or b) else 0.6 if not (g or b) else 1.0
    return lit


def largest_shape(lit):
    h, w = len(lit), len(lit[0])
    seen, best = set(), []
    for sy in range(h):
        for sx in range(w):
            if not lit[sy][sx] or (sx, sy) in seen:
                continue
            todo, part = [(sx, sy)], []
            seen.add((sx, sy))
            while todo:
                x, y = todo.pop()
                part.append((x, y))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        q = (x + dx, y + dy)
                        if 0 <= q[0] < w and 0 <= q[1] < h and lit[q[1]][q[0]] and q not in seen:
                            seen.add(q)
                            todo.append(q)
            if len(part) > len(best):
                best = part
    return best


def blur(grid, n):
    h, w = len(grid), len(grid[0])
    for _ in range(3):
        grid = [[sum(r[max(0, x - n):x + n + 1]) / (2 * n + 1) for x in range(w)] for r in grid]
        cols = list(zip(*grid))
        grid = [[sum(cols[x][max(0, y - n):y + n + 1]) / (2 * n + 1) for x in range(w)] for y in range(h)]
    return grid


def field_of(shape, lit, fine=6):
    xs, ys = [p[0] for p in shape], [p[1] for p in shape]
    x0, y0 = min(xs) - 3, min(ys) - 3
    cols, rows = max(xs) - x0 + 4, max(ys) - y0 + 4
    grid = [[0.0] * (cols * fine) for _ in range(rows * fine)]
    for x, y in shape:
        for j in range((y - y0) * fine, (y - y0 + 1) * fine):
            for i in range((x - x0) * fine, (x - x0 + 1) * fine):
                grid[j][i] = lit[y][x]
    grid = [[(a + b) / 2 for a, b in zip(r, reversed(r))] for r in grid]
    return blur(grid, 3), cols, rows


def tile_alpha(x, y):
    inner = TILE / 2 - RADIUS
    dx, dy = abs(x + 0.5 - ICON / 2) - inner, abs(y + 0.5 - ICON / 2) - inner
    d = (max(dx, 0) ** 2 + max(dy, 0) ** 2) ** 0.5 + min(max(dx, dy), 0) - RADIUS
    return 0.0 if d >= 1 else 1.0 if d <= -1 else (1 - d) / 2


def mix(a, b, t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(p + (q - p) * t for p, q in zip(a, b))


def game_pixel(field, cols, rows):
    grid_w, grid_h = len(field[0]), len(field)
    scale = min(700 / rows, 560 / cols)
    ox, oy = (ICON - cols * scale) / 2, (ICON - rows * scale) / 2

    def pixel(x, y):
        a = tile_alpha(x, y)
        if not a:
            return (0, 0, 0, 0)
        r = ((x - ICON / 2) ** 2 + (y - ICON / 2) ** 2) ** 0.5 / (TILE * 0.72)
        c = mix((66, 9, 15), (12, 3, 5), r)
        u, v = (x + 0.5 - ox) / scale * grid_w / cols - 0.5, (y + 0.5 - oy) / scale * grid_h / rows - 0.5
        if 0 <= u < grid_w - 1 and 0 <= v < grid_h - 1:
            i, j = int(u), int(v)
            fx, fy = u - i, v - j
            f = (field[j][i] * (1 - fx) + field[j][i + 1] * fx) * (1 - fy) + (field[j + 1][i] * (1 - fx) + field[j + 1][i + 1] * fx) * fy
            c = mix(c, (168, 18, 28), f * 5)
            c = mix(c, mix((244, 240, 234), (182, 174, 174), (y - 170) / 680), (f - 0.27) / 0.02)
        return (int(c[0]), int(c[1]), int(c[2]), int(a * 255 + 0.5))

    return pixel


def porter_pixel(x, y):
    a = tile_alpha(x, y)
    if not a:
        return (0, 0, 0, 0)
    c = mix((44, 44, 50), (17, 17, 21), (y - 100) / 824)
    px, py = x + 0.5 - 512, y + 0.5
    if 180 < py < 600:
        half = 250 * math.sin((py - 180) / 420 * math.pi / 2) ** 1.7
    elif 600 <= py < 850:
        half = (62500 - (py - 600) ** 2) ** 0.5
    else:
        half = -9
    c = mix(c, mix((206, 34, 44), (116, 10, 20), (y - 180) / 670), ((half - abs(px)) / 1.5 + 1) / 2)
    stem = min(44 - abs(px), py - 440, 660 - py)
    head = min(py - 610, 790 - py - abs(px) * 1.25)
    c = mix(c, (248, 246, 244), (max(stem, head) / 1.5 + 1) / 2)
    return (int(c[0]), int(c[1]), int(c[2]), int(a * 255 + 0.5))


def png(pixel):
    rows = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(ICON)) for y in range(ICON))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", ICON, ICON, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def run(argv):
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode:
        raise IconError("%s failed: %s" % (Path(argv[0]).name, (r.stderr or r.stdout).strip()))


def from_pixel(pixel, out):
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "icon.png"
        src.write_bytes(png(pixel))
        iconset = Path(t) / "AppIcon.iconset"
        iconset.mkdir()
        small = Path(t) / "small.png"
        run(["/usr/bin/sips", "-c", str(SMALL_CROP), str(SMALL_CROP), str(src), "--out", str(small)])
        for s in SIZES:
            for name, px in (("icon_%dx%d.png" % (s, s), s), ("icon_%dx%d@2x.png" % (s, s), s * 2)):
                run(["/usr/bin/sips", "-z", str(px), str(px), str(small if s <= 32 else src), "--out", str(iconset / name)])
        run(["/usr/bin/iconutil", "-c", "icns", "-o", str(out), str(iconset)])
    return out


def icns(exe, out):
    _, _, blob = max(frames(exe))
    lit = dib_lit(blob)
    field, cols, rows = field_of(largest_shape(lit), lit)
    return from_pixel(game_pixel(field, cols, rows), out)


def porter_icon(out):
    return from_pixel(porter_pixel, out)


def add(app, exe, name, say=print):
    res = Path(app) / "Contents/Resources"
    try:
        icns(exe, res / name)
    except (IconError, OSError) as e:
        say("no app icon: %s" % e)
        return False
    plist = Path(app) / "Contents/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info["CFBundleIconFile"] = name
    plist.write_bytes(plistlib.dumps(info))
    return True
