#include "recomp_types.h"
#include "rt.h"
#include "fx.h"
#include "map.h"
#include "toggles.h"

#include <SDL3/SDL.h>
#include <dirent.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define D3D_SDK_VERSION 32u
#define D3DDEVTYPE_HAL 1u
#define D3DCREATE_FLAGS 0x52u
#define D3DFMT_A8R8G8B8 21u
#define D3DFMT_X8R8G8B8 22u
#define D3DFMT_D24S8 75u
#define D3DFMT_INDEX16 101u
#define D3DFMT_DXT1 0x31545844u
#define D3DPOOL_DEFAULT 0u
#define D3DPOOL_MANAGED 1u
#define D3DUSAGE_DYNAMIC 0x200u
#define D3DLOCK_READONLY 0x10u
#define D3DLOCK_NOOVERWRITE 0x1000u
#define D3DLOCK_DISCARD 0x2000u
#define D3DSWAPEFFECT_DISCARD 1u
#define D3DPRESENT_INTERVAL_IMMEDIATE 0x80000000u
#define D3DCLEAR_TARGET 1u
#define D3DCLEAR_ZBUFFER 2u
#define D3DRS_ZENABLE 7u
#define D3DRS_CULLMODE 22u
#define D3DRS_ZFUNC 23u
#define D3DRS_LIGHTING 137u
#define D3DCMP_LESS 2u
#define D3DCULL_NONE 1u
#define D3DFVF_XYZRHW 4u
#define D3DFVF_DIFFUSE 0x40u
#define D3DPT_TRIANGLESTRIP 5u
#define D3DCUBEMAP_FACE_NEGATIVE_X 1u
#define D3DDEVCAPS_HWTRANSFORMANDLIGHT 0x10000u
#define D3DDEVCAPS_PUREDEVICE 0x100000u
#define W 640u
#define H 480u

static int s_pass, s_fail;

static void result(int ok, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("%s ", ok ? "PASS" : "FAIL");
    vprintf(fmt, ap);
    printf("\n");
    fflush(stdout);
    va_end(ap);
    if (ok) s_pass++;
    else s_fail++;
}

static uint32_t m(const char *name, int n, ...)
{
    uint32_t a[12] = {0};
    va_list ap;
    va_start(ap, n);
    for (int i = 0; i < n && i < 12; i++) a[i] = va_arg(ap, uint32_t);
    va_end(ap);
    rt_shim_fn fn = rt_gfx_method(name);
    if (!fn) {
        result(0, "%s has a bridge body", name);
        return 0x8876086Cu;
    }
    return fn(a);
}

static uint32_t galloc(uint32_t n) { return rt_heap_alloc(n, 1); }

static void *host_fn(void *obj, uint32_t kind, const char *name)
{
    const rt_d3d9_table *t = &rt_d3d9_tables[kind];
    for (uint32_t i = 0; i < t->n; i++)
        if (!strcmp(t->rows[i].name, name)) return (*(void ***)obj)[i];
    return NULL;
}

static uint8_t pat(uint32_t seed, uint32_t r, uint32_t c) { return (uint8_t)(seed * 31u + r * 7u + c * 13u); }

static void *thread_call(void *arg)
{
    (void)arg;
    m("IDirect3DDevice9::BeginScene", 1, 0x12345670u);
    return NULL;
}

static void thread_case(void)
{
    fflush(stdout);
    fflush(stderr);
    pid_t pid = fork();
    if (pid == 0) {
        pthread_t t;
        pthread_create(&t, NULL, thread_call, NULL);
        pthread_join(t, NULL);
        _exit(0);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    result(WIFEXITED(st) && WEXITSTATUS(st) == 5, "a d3d9 call from a second pthread exits 5 (child exit %d)",
           WIFEXITED(st) ? WEXITSTATUS(st) : -1);
}

static void args_case(void)
{
    int n = 0, q = 0;
    const char *const *full = rt_default_args(0, &n);
    const char *const *quiet = rt_default_args(1, &q);
    int ok = q == n - 3, j = 0, dropped = 0;
    char gone[64] = "";
    for (int i = 0; ok && i < n; i++) {
        if (j < q && !strcmp(full[i], quiet[j])) {
            j++;
            continue;
        }
        dropped++;
        snprintf(gone + strlen(gone), sizeof gone - strlen(gone), "%s%s", gone[0] ? " " : "", full[i]);
    }
    ok &= j == q && dropped == 3 && !strcmp(gone, "-condebug +mm_verbose 1");
    result(ok, "--guest-quiet drops exactly \"-condebug +mm_verbose 1\" from the default guest arguments (dropped \"%s\")",
           gone);
}

static uint32_t shim(const char *dll, const char *name, const uint32_t *a)
{
    const rt_shim *s = rt_find_shim(dll, name);
    return s ? s->fn(a) : 0xDEAD0000u;
}

static uint32_t ws(const char *ord, uint32_t x) { return shim("WSOCK32.dll", ord, (const uint32_t[]){x}); }

static void gdi_font_cases(void)
{
    uint32_t bits = galloc(4);
    MEM32(bits) = 0x12345678u;
    uint32_t dib = shim("GDI32.dll", "CreateDIBSection", (const uint32_t[]){0x71008u, galloc(40), 0, bits, 0, 0});
    result(dib == 0 && MEM32(bits) == 0, "CreateDIBSection fails with NULL and *ppvBits NULL (0x%08X, bits 0x%08X)", dib,
           MEM32(bits));
    uint32_t abc = shim("GDI32.dll", "GetCharABCWidthsW", (const uint32_t[]){0x71008u, 0, 0xFF, galloc(12 * 256)});
    uint32_t ggo = shim("GDI32.dll", "GetGlyphOutlineA", (const uint32_t[]){0x71008u, 'A', 6, galloc(20), 0, 0, galloc(16)});
    uint32_t eto = shim("GDI32.dll", "ExtTextOutW", (const uint32_t[]){0x71008u, 0, 0, 0, 0, galloc(4), 1, 0});
    result(abc == 0 && ggo == 0xFFFFFFFFu && eto == 0,
           "Win32 font raster fails: GetCharABCWidthsW %u, GetGlyphOutlineA 0x%08X (GDI_ERROR), ExtTextOutW %u", abc, ggo,
           eto);
    uint32_t clip = shim("USER32.dll", "OpenClipboard", (const uint32_t[]){0});
    uint32_t sh = shim("SHELL32.dll", "ShellExecuteA", (const uint32_t[]){0, 0, rt_put_string("http://x"), 0, 0, 1});
    result(clip == 0 && sh == 2, "OpenClipboard fails (%u) and ShellExecuteA returns ERROR_FILE_NOT_FOUND (%u)", clip, sh);
}

static void wsock_cases(void)
{
    uint32_t a = ws("#15", 0x8769u), b = ws("#9", 27015u);
    result(a == 27015u && b == 0x8769u, "wsock32 #15 ntohs(0x8769) = %u and #9 htons(27015) = 0x%04X", a, b);
    uint32_t ip = ws("#10", rt_put_string("127.0.0.1")), bad = ws("#10", rt_put_string("1.2.3.300"));
    result(ip == 0x0100007Fu && bad == 0xFFFFFFFFu, "wsock32 #10 inet_addr 127.0.0.1 = 0x%08X, 1.2.3.300 = 0x%08X", ip,
           bad);
    uint32_t h = ws("#52", rt_put_string("localhost")), e = ws("#111", 0);
    result(h == 0 && e == 10093u, "wsock32 #52 gethostbyname fails (0x%08X), #111 WSAGetLastError = %u", h, e);
}

static void layout_cases(void)
{
    uint32_t g = galloc(56), back = galloc(56);
    for (uint32_t i = 0; i < 14; i++) MEM32(g + 4 * i) = 0x11000000u + 0x010101u * (i + 1);
    MEM32(g + 28) = 0x00010004u;
    uint64_t hb[8];
    memset(hb, 0xCC, sizeof hb);
    void *hwnd = (void *)(uintptr_t)0x1122334455667788ull;
    rt_gfx_pp_in(g, hb, hwnd);
    const uint8_t *h = (const uint8_t *)hb;
    int ok = 1;
    for (uint32_t i = 0; i < 7; i++) ok &= !memcmp(h + 4 * i, rt_host(g + 4 * i), 4);
    void *hp;
    memcpy(&hp, h + 32, 8);
    ok &= hp == hwnd;
    for (uint32_t i = 8; i < 14; i++) ok &= !memcmp(h + 40 + 4 * (i - 8), rt_host(g + 4 * i), 4);
    rt_gfx_pp_out(hb, back, MEM32(g + 28));
    for (uint32_t i = 0; i < 14; i++) ok &= MEM32(back + 4 * i) == MEM32(g + 4 * i);
    result(ok, "D3DPRESENT_PARAMETERS round-trips 56 -> 64 -> 56 bytes field by field, hDeviceWindow 28 <-> 32");
    struct {
        int32_t pitch;
        void *bits;
    } lr = {0x1234, (void *)(uintptr_t)0xDEADBEEFCAFEull};
    uint32_t gl = galloc(16);
    MEM32(gl + 8) = 0xA5A5A5A5u;
    rt_gfx_lr_out(&lr, gl, 0x31000040u);
    result(MEM32(gl) == 0x1234u && MEM32(gl + 4) == 0x31000040u && MEM32(gl + 8) == 0xA5A5A5A5u,
           "D3DLOCKED_RECT 16 -> 8 bytes: Pitch 0x%X pBits 0x%08X, nothing past 8 bytes written", MEM32(gl), MEM32(gl + 4));
}

static void size_cases(void)
{
    static const struct {
        int windowed, gw, gh;
        uint32_t bw, bh, ow, oh;
    } k[] = {
        {1, 1594, 1196, 0, 0, 1594, 1196},   {1, 1594, 1196, 0, 768, 1594, 768},  {1, 1594, 1196, 1024, 0, 1024, 1196},
        {1, 1594, 1196, 800, 600, 800, 600}, {0, 1594, 1196, 0, 0, 0, 0},         {0, 1594, 1196, 1280, 1024, 1280, 1024},
        {1, 0, 0, 0, 0, 0, 0},
    };
    int bad = 0;
    for (size_t i = 0; i < sizeof k / sizeof k[0]; i++) {
        uint32_t w = k[i].bw, h = k[i].bh;
        rt_gfx_pp_size(k[i].windowed, k[i].gw, k[i].gh, &w, &h);
        bad += w != k[i].ow || h != k[i].oh;
    }
    result(!bad, "a windowed 0x0 back buffer takes the guest window's 1594x1196 (an SDL window of 2056x1195 does not matter), "
                 "nonzero stays, full screen stays (%d wrong)", bad);
    int hang = rt_hang_due(4999, 0) == 0 && rt_hang_due(5000, 0) == 1 && rt_hang_due(5000, 1) == 0 && rt_hang_due(60000, 1) == 0 &&
               rt_hang_due(119999, 1) == 0 && rt_hang_due(120000, 1) == 1 && rt_hang_due(200000, 0) == 1;
    result(hang, "watchdog: 5 s stops in dev; 5 s and 60 s do not stop in player; 120 s stops in player; over the limit stops in both");
}

static void surface_full(const char *label, uint32_t s, uint32_t rows, uint32_t rowbytes, uint32_t seed)
{
    uint32_t out = galloc(8);
    uint32_t hr = m("IDirect3DSurface9::LockRect", 4, s, out, 0u, 0u);
    uint32_t pitch = MEM32(out), b = MEM32(out + 4);
    uint64_t n = (uint64_t)pitch * (rows - 1) + rowbytes;
    int ok = !hr && b && rt_span_ok(b, n) && pitch >= rowbytes;
    if (ok)
        for (uint32_t r = 0; r < rows; r++)
            for (uint32_t c = 0; c < rowbytes; c++) MEM8(b + r * pitch + c) = pat(seed, r, c);
    m("IDirect3DSurface9::UnlockRect", 1, s);
    hr = m("IDirect3DSurface9::LockRect", 4, s, out, 0u, D3DLOCK_READONLY);
    pitch = MEM32(out);
    b = MEM32(out + 4);
    int same = !hr && b;
    for (uint32_t r = 0; same && r < rows; r++)
        for (uint32_t c = 0; c < rowbytes; c++) same &= MEM8(b + r * pitch + c) == pat(seed, r, c);
    m("IDirect3DSurface9::UnlockRect", 1, s);
    result(ok && same, "%s: pBits 0x%08X pitch %u passes rt_span_ok(%llu); pattern written through it reads back after "
                       "Unlock with a READONLY lock", label, b, pitch, (unsigned long long)n);
}

static void buffer_cases(uint32_t dev, int vb)
{
    const char *iface = vb ? "IDirect3DVertexBuffer9" : "IDirect3DIndexBuffer9";
    char lock[64], unlock[64], release[64];
    snprintf(lock, sizeof lock, "%s::Lock", iface);
    snprintf(unlock, sizeof unlock, "%s::Unlock", iface);
    snprintf(release, sizeof release, "%s::Release", iface);
    const char *create = vb ? "IDirect3DDevice9::CreateVertexBuffer" : "IDirect3DDevice9::CreateIndexBuffer";
    uint32_t fmt = vb ? 0u : D3DFMT_INDEX16, outp = galloc(4), outb = galloc(4);
    uint32_t hr = m(create, 7, dev, 4096u, 0u, fmt, D3DPOOL_MANAGED, outp, 0u);
    uint32_t buf = MEM32(outp);
    int ok = !hr && buf;
    hr = m(lock, 5, buf, 0u, 0u, outb, 0u);
    uint32_t b = MEM32(outb);
    ok &= !hr && b && rt_span_ok(b, 4096);
    for (uint32_t i = 0; ok && i < 4096; i++) MEM8(b + i) = pat(1, 0, i);
    m(unlock, 1, buf);
    hr = m(lock, 5, buf, 0u, 0u, outb, D3DLOCK_READONLY);
    b = MEM32(outb);
    int same = !hr && b;
    for (uint32_t i = 0; same && i < 4096; i++) same &= MEM8(b + i) == pat(1, 0, i);
    m(unlock, 1, buf);
    result(ok && same, "%s plain lock: pBits 0x%08X passes rt_span_ok; pattern reads back after Unlock", iface, b);

    uint32_t outq = galloc(4);
    hr = m(create, 7, dev, 4096u, 0u, fmt, D3DPOOL_MANAGED, outq, 0u);
    uint32_t other = MEM32(outq);
    ok = !hr && other;
    hr = m(lock, 5, other, 0u, 0u, outb, 0u);
    b = MEM32(outb);
    ok &= !hr && b;
    for (uint32_t i = 0; ok && i < 4096; i++) MEM8(b + i) = pat(7, 0, i);
    m(unlock, 1, other);
    hr = m(lock, 5, buf, 0u, 0u, outb, D3DLOCK_DISCARD);
    b = MEM32(outb);
    ok &= !hr && b;
    for (uint32_t i = 0; ok && i < 16; i++) MEM8(b + i) = pat(8, 0, i);
    m(unlock, 1, buf);
    hr = m(lock, 5, buf, 0u, 0u, outb, D3DLOCK_READONLY);
    b = MEM32(outb);
    same = !hr && b;
    for (uint32_t i = 0; same && i < 4096; i++) same &= MEM8(b + i) == (i < 16 ? pat(8, 0, i) : pat(1, 0, i));
    m(unlock, 1, buf);
    result(ok && same, "%s MANAGED lock with DISCARD (ignored by dxvk) keeps the untouched bytes after Unlock", iface);
    m(release, 1, other);
    m(release, 1, buf);

    hr = m(create, 7, dev, 4096u, D3DUSAGE_DYNAMIC, fmt, D3DPOOL_DEFAULT, outp, 0u);
    buf = MEM32(outp);
    ok = !hr && buf;
    hr = m(lock, 5, buf, 0u, 1024u, outb, D3DLOCK_DISCARD);
    b = MEM32(outb);
    ok &= !hr && b && rt_span_ok(b, 1024);
    for (uint32_t i = 0; ok && i < 1024; i++) MEM8(b + i) = pat(2, 0, i);
    m(unlock, 1, buf);
    hr = m(lock, 5, buf, 1024u, 1024u, outb, D3DLOCK_NOOVERWRITE);
    b = MEM32(outb);
    ok &= !hr && b && rt_span_ok(b, 1024);
    for (uint32_t i = 0; ok && i < 1024; i++) MEM8(b + i) = pat(3, 0, i);
    m(unlock, 1, buf);
    hr = m(lock, 5, buf, 0u, 2048u, outb, 0u);
    b = MEM32(outb);
    same = !hr && b;
    for (uint32_t i = 0; same && i < 1024; i++) same &= MEM8(b + i) == pat(2, 0, i) && MEM8(b + 1024 + i) == pat(3, 0, i);
    m(unlock, 1, buf);
    result(ok && same, "%s DISCARD then NOOVERWRITE locks: both writes read back through a plain lock", iface);
    m(release, 1, buf);
}

static uint32_t level_surface(uint32_t dev, uint32_t usage, uint32_t pool)
{
    uint32_t outp = galloc(4), outs = galloc(4);
    uint32_t hr = m("IDirect3DDevice9::CreateTexture", 9, dev, 64u, 64u, 1u, usage, D3DFMT_A8R8G8B8, pool, outp, 0u);
    if (!hr) hr = m("IDirect3DTexture9::GetSurfaceLevel", 3, MEM32(outp), 0u, outs);
    return hr ? 0 : MEM32(outs);
}

static uint32_t write_lock(uint32_t s, uint32_t rect, uint32_t flags, uint32_t rows, uint32_t seed)
{
    uint32_t lr = galloc(8);
    uint32_t hr = m("IDirect3DSurface9::LockRect", 4, s, lr, rect, flags);
    if (hr) return hr;
    uint32_t pitch = MEM32(lr), b = MEM32(lr + 4);
    uint32_t bytes = rect ? 4u * (MEM32(rect + 8) - MEM32(rect)) : 256u;
    for (uint32_t r = 0; r < rows; r++)
        for (uint32_t c = 0; c < bytes; c++) MEM8(b + r * pitch + c) = pat(seed, r, c);
    return m("IDirect3DSurface9::UnlockRect", 1, s);
}

static int check_level(uint32_t s, uint32_t seed_out, uint32_t seed_in, uint32_t x0, uint32_t y0, uint32_t x1, uint32_t y1)
{
    uint32_t lr = galloc(8);
    if (m("IDirect3DSurface9::LockRect", 4, s, lr, 0u, D3DLOCK_READONLY)) return 0;
    uint32_t pitch = MEM32(lr), b = MEM32(lr + 4);
    int ok = 1;
    for (uint32_t y = 0; ok && y < 64; y++)
        for (uint32_t c = 0; c < 256; c++) {
            int in = y >= y0 && y < y1 && c >= 4 * x0 && c < 4 * x1;
            ok &= MEM8(b + y * pitch + c) == (in ? pat(seed_in, y - y0, c - 4 * x0) : pat(seed_out, y, c));
        }
    m("IDirect3DSurface9::UnlockRect", 1, s);
    return ok;
}

static void image_discard_cases(uint32_t dev)
{
    uint32_t scratch = level_surface(dev, 0u, D3DPOOL_MANAGED), rect = galloc(16);
    uint32_t s = level_surface(dev, 0u, D3DPOOL_MANAGED);
    int ok = scratch && s && !write_lock(s, 0u, 0u, 64, 12) && !write_lock(scratch, 0u, 0u, 64, 11) &&
             !write_lock(s, 0u, D3DLOCK_DISCARD, 1, 13);
    result(ok && check_level(s, 12, 13, 0, 0, 64, 1),
           "MANAGED texture level locked whole with DISCARD (ignored by dxvk) keeps its untouched pixels after Unlock");
    s = level_surface(dev, D3DUSAGE_DYNAMIC, D3DPOOL_DEFAULT);
    static const uint32_t r[4] = {8, 8, 24, 24};
    memcpy(rt_host(rect), r, 16);
    ok = s && !write_lock(s, 0u, 0u, 64, 14) && !write_lock(scratch, rect, 0u, 16, 11) &&
         !write_lock(s, rect, D3DLOCK_DISCARD, 1, 15);
    result(ok && check_level(s, 14, 15, 8, 8, 24, 9),
           "DEFAULT texture level, RECT (8,8)-(24,24) locked with DISCARD (ignored by dxvk for partial locks) keeps "
           "every pixel it did not write");
}

static void texture_cases(uint32_t dev)
{
    uint32_t outp = galloc(4), outs = galloc(4), out2 = galloc(4), rect = galloc(16), lr = galloc(8);
    uint32_t base = rt_gfx_live();
    uint32_t hr = m("IDirect3DDevice9::CreateTexture", 9, dev, 64u, 64u, 1u, 0u, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, outp, 0u);
    uint32_t tex = MEM32(outp);
    m("IDirect3DTexture9::GetSurfaceLevel", 3, tex, 0u, outs);
    m("IDirect3DTexture9::GetSurfaceLevel", 3, tex, 0u, out2);
    uint32_t s = MEM32(outs);
    result(!hr && s && s == MEM32(out2) && rt_gfx_live() == base + 2,
           "GetSurfaceLevel twice gives one proxy 0x%08X (live objects %u, baseline %u)", s, rt_gfx_live(), base);

    surface_full("2D texture level, full lock", s, 64, 256, 4);
    MEM32(rect) = 8;
    MEM32(rect + 4) = 8;
    MEM32(rect + 8) = 24;
    MEM32(rect + 12) = 24;
    hr = m("IDirect3DSurface9::LockRect", 4, s, lr, rect, 0u);
    uint32_t pitch = MEM32(lr), b = MEM32(lr + 4);
    int ok = !hr && b && rt_span_ok(b, (uint64_t)pitch * 15 + 64);
    for (uint32_t r = 0; ok && r < 16; r++)
        for (uint32_t c = 0; c < 64; c++) MEM8(b + r * pitch + c) = pat(9, r, c);
    m("IDirect3DSurface9::UnlockRect", 1, s);
    hr = m("IDirect3DSurface9::LockRect", 4, s, lr, 0u, D3DLOCK_READONLY);
    pitch = MEM32(lr);
    b = MEM32(lr + 4);
    int same = !hr && b;
    for (uint32_t y = 0; same && y < 64; y++)
        for (uint32_t c = 0; c < 256; c++) {
            int in = y >= 8 && y < 24 && c >= 32 && c < 96;
            same &= MEM8(b + y * pitch + c) == (in ? pat(9, y - 8, c - 32) : pat(4, y, c));
        }
    m("IDirect3DSurface9::UnlockRect", 1, s);
    result(ok && same, "2D texture level, RECT lock (8,8)-(24,24): rect writes land inside, the rest is kept");

    static const struct {
        int32_t r[4];
        const char *what;
    } k_bad[6] = {{{-1, 0, 8, 8}, "left < 0"},       {{0, -1, 8, 8}, "top < 0"},
                  {{8, 0, 8, 8}, "right <= left"},   {{0, 8, 8, 8}, "bottom <= top"},
                  {{0, 0, 65, 8}, "right > width"},  {{0, 0, 8, 65}, "bottom > height"}};
    for (int i = 0; i < 6; i++) {
        memcpy(rt_host(rect), k_bad[i].r, 16);
        hr = m("IDirect3DSurface9::LockRect", 4, s, lr, rect, 0u);
        if (!hr) m("IDirect3DSurface9::UnlockRect", 1, s);
        result(hr == 0x8876086Cu, "LockRect with RECT (%d,%d)-(%d,%d), %s, returns D3DERR_INVALIDCALL (0x%08X)", k_bad[i].r[0],
               k_bad[i].r[1], k_bad[i].r[2], k_bad[i].r[3], k_bad[i].what, hr);
    }

    m("IDirect3DSurface9::Release", 1, s);
    m("IDirect3DSurface9::Release", 1, s);
    uint32_t r = m("IDirect3DTexture9::Release", 1, tex);
    result(r == 0 && rt_gfx_live() == base, "Release to 0 frees the texture and its surface proxies (live %u, baseline %u)",
           rt_gfx_live(), base);

    image_discard_cases(dev);

    hr = m("IDirect3DDevice9::CreateCubeTexture", 8, dev, 32u, 1u, 0u, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, outp, 0u);
    uint32_t cube = MEM32(outp);
    hr |= m("IDirect3DCubeTexture9::GetCubeMapSurface", 4, cube, D3DCUBEMAP_FACE_NEGATIVE_X, 0u, outs);
    if (!hr) surface_full("cube face NEGATIVE_X, full lock", MEM32(outs), 32, 128, 5);
    else result(0, "cube face created (hr 0x%08X)", hr);

    hr = m("IDirect3DDevice9::CreateTexture", 9, dev, 64u, 64u, 1u, 0u, D3DFMT_DXT1, D3DPOOL_MANAGED, outp, 0u);
    hr |= m("IDirect3DTexture9::GetSurfaceLevel", 3, MEM32(outp), 0u, outs);
    if (!hr) surface_full("DXT1 texture, full lock (16 block rows of 128 bytes)", MEM32(outs), 16, 128, 6);
    else result(0, "DXT1 texture created (hr 0x%08X)", hr);
}

typedef struct {
    float x, y, z, rhw;
    uint32_t color;
} tl_vertex;

static void quad(void *dev, float x0, float x1, float z, uint32_t color)
{
    tl_vertex v[4] = {{x0, 0, z, 1, color}, {x1, 0, z, 1, color}, {x0, (float)H, z, 1, color}, {x1, (float)H, z, 1, color}};
    ((int32_t (*)(void *, uint32_t, uint32_t, const void *, uint32_t))host_fn(dev, K_DEVICE, "IDirect3DDevice9::DrawPrimitiveUP"))(
        dev, D3DPT_TRIANGLESTRIP, 2, v, sizeof v[0]);
}

static uint32_t pixel(void *dev, uint32_t x, uint32_t y)
{
    uint32_t *px = NULL, w = 0, h = 0;
    if (rt_gfx_readback(dev, &px, &w, &h)) return 0xFFFFFFFFu;
    uint32_t v = x < w && y < h ? px[y * w + x] & 0xFFFFFFu : 0xFFFFFFFFu;
    free(px);
    return v;
}

static uint32_t fbits(float f)
{
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

static void draw_cases(uint32_t dev)
{
    void *hd = rt_gfx_host(dev);
    uint32_t hr = m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF3366CCu, fbits(1.0f), 0u);
    uint32_t c = pixel(hd, W / 2, H / 2);
    result(!hr && c == 0x3366CCu, "Clear to 0x3366CC, the capture path reads 0x%06X", c);

    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_ZENABLE, 1u);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_ZFUNC, D3DCMP_LESS);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_CULLMODE, D3DCULL_NONE);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_LIGHTING, 0u);
    hr = m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFF000000u, fbits(0.25f), 0u);
    m("IDirect3DDevice9::BeginScene", 1, dev);
    ((int32_t (*)(void *, uint32_t))host_fn(hd, K_DEVICE, "IDirect3DDevice9::SetFVF"))(hd, D3DFVF_XYZRHW | D3DFVF_DIFFUSE);
    quad(hd, 0, W / 2, 0.5f, 0xFFFF0000u);
    quad(hd, W / 2, W, 0.1f, 0xFF00FF00u);
    m("IDirect3DDevice9::EndScene", 1, dev);
    uint32_t l = pixel(hd, W / 4, H / 2), r = pixel(hd, 3 * W / 4, H / 2);
    result(!hr && l == 0 && r == 0x00FF00u,
           "Clear Z 0.25 as float: a quad at z 0.5 is hidden (0x%06X), a quad at z 0.1 shows (0x%06X)", l, r);
    hr = m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
    result(!hr, "Present returns D3D_OK (0x%08X)", hr);

    static const uint32_t vs11[] = {0xFFFE0101u, 0x0000001Fu, 0x80000000u, 0x900F0000u,
                                    0x00000001u, 0xC00F0000u, 0x90E40000u, 0x0000FFFFu};
    uint32_t tok = galloc(sizeof vs11), out = galloc(4);
    memcpy(rt_host(tok), vs11, sizeof vs11);
    hr = m("IDirect3DDevice9::CreateVertexShader", 3, dev, tok, out);
    result(!hr && MEM32(out), "a vs_1_1 token stream creates a vertex shader (hr 0x%08X)", hr);
}

#define D3DERR_NOTFOUND 0x88760866u
#define D3DERR_INVALIDCALL 0x8876086Cu
#define D3DFMT_VERTEXDATA 100u
#define D3DPOOL_SYSTEMMEM 2u
#define D3DRTYPE_SURFACE 1u
#define D3DRTYPE_TEXTURE 3u
#define D3DRTYPE_CUBETEXTURE 5u
#define D3DRTYPE_VERTEXBUFFER 6u
#define D3DRTYPE_INDEXBUFFER 7u
#define D3DQUERYTYPE_EVENT 8u
#define D3DISSUE_END 1u
#define D3DGETDATA_FLUSH 1u
#define D3DTEXF_POINT 1u
#define D3DPT_TRIANGLELIST 4u
#define D3DMULTISAMPLE_NONE 0u
#define D3DSPD_IUNKNOWN 1u

static char s_n[128];

static const char *nm(const char *iface, const char *method)
{
    snprintf(s_n, sizeof s_n, "%s::%s", iface, method);
    return s_n;
}

static uint32_t mi(const char *iface, const char *method, int n, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                   uint32_t a4, uint32_t a5)
{
    return m(nm(iface, method), n, a0, a1, a2, a3, a4, a5);
}

static uint32_t created_obj(const char *create, int n, uint32_t *a, int out_index)
{
    uint32_t out = galloc(4);
    a[out_index] = out;
    uint32_t hr = m(create, n, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8]);
    return hr ? 0 : MEM32(out);
}

static uint32_t new_texture(uint32_t dev, uint32_t size, uint32_t levels, uint32_t usage, uint32_t pool)
{
    uint32_t a[9] = {dev, size, size, levels, usage, D3DFMT_A8R8G8B8, pool, 0, 0};
    return created_obj("IDirect3DDevice9::CreateTexture", 9, a, 7);
}

static uint32_t new_cube(uint32_t dev, uint32_t size, uint32_t levels)
{
    uint32_t a[9] = {dev, size, levels, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, 0, 0, 0};
    return created_obj("IDirect3DDevice9::CreateCubeTexture", 8, a, 6);
}

static uint32_t new_buffer(uint32_t dev, int vb, uint32_t size, uint32_t fvf)
{
    uint32_t a[9] = {dev, size, 0, vb ? fvf : D3DFMT_INDEX16, D3DPOOL_MANAGED, 0, 0, 0, 0};
    return created_obj(vb ? "IDirect3DDevice9::CreateVertexBuffer" : "IDirect3DDevice9::CreateIndexBuffer", 7, a, 5);
}

static void refcount_case(const char *iface, uint32_t obj)
{
    uint32_t base = rt_gfx_live();
    uint32_t r1 = mi(iface, "AddRef", 1, obj, 0, 0, 0, 0, 0);
    uint32_t r2 = mi(iface, "Release", 1, obj, 0, 0, 0, 0, 0);
    int alive = rt_gfx_host(obj) != NULL;
    result(obj && r1 == 2 && r2 == 1 && alive, "%s::AddRef forwards: AddRef gives %u, then Release gives %u and the proxy "
                                               "stays", iface, r1, r2);
    uint32_t r3 = mi(iface, "Release", 1, obj, 0, 0, 0, 0, 0);
    result(obj && r3 == 0 && rt_gfx_live() == base - 1 && !rt_gfx_host(obj),
           "%s::Release after AddRef frees the object only at the second Release (%u, live %u, baseline %u)", iface, r3,
           rt_gfx_live(), base);
}

static void device_case(const char *iface, uint32_t obj, uint32_t dev)
{
    uint32_t out = galloc(4);
    uint32_t hr = mi(iface, "GetDevice", 2, obj, out, 0, 0, 0, 0);
    uint32_t got = MEM32(out);
    uint32_t r = got ? m("IDirect3DDevice9::Release", 1, got) : 0;
    result(!hr && got == dev && r > 0, "%s::GetDevice returns the device proxy 0x%08X (want 0x%08X), its reference "
                                       "released (count %u)", iface, got, dev, r);
}

static void private_cases(const char *iface, uint32_t obj)
{
    static const uint8_t k_guid[16] = {0x10, 0x32, 0x54, 0x76, 0x98, 0xBA, 0xDC, 0xFE,
                                       0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF};
    uint32_t guid = galloc(16), data = galloc(8), buf = galloc(12), size = galloc(4);
    memcpy(rt_host(guid), k_guid, 16);
    for (uint32_t i = 0; i < 8; i++) MEM8(data + i) = (uint8_t)(0xA0 + i);
    memset(rt_host(buf), 0, 12);
    uint32_t hr = mi(iface, "SetPrivateData", 5, obj, guid, data, 8, 0, 0);
    result(!hr, "%s::SetPrivateData stores 8 bytes (hr 0x%08X)", iface, hr);
    MEM32(size) = 8;
    hr = mi(iface, "GetPrivateData", 4, obj, guid, buf, size, 0, 0);
    result(!hr && MEM32(size) == 8 && !memcmp(rt_host(buf), rt_host(data), 8) && MEM32(buf + 8) == 0,
           "%s::GetPrivateData reads the 8 bytes back (hr 0x%08X, size %u)", iface, hr, MEM32(size));
    hr = mi(iface, "FreePrivateData", 2, obj, guid, 0, 0, 0, 0);
    MEM32(size) = 8;
    uint32_t again = mi(iface, "GetPrivateData", 4, obj, guid, buf, size, 0, 0);
    result(!hr && again == D3DERR_NOTFOUND, "%s::FreePrivateData removes them: a later GetPrivateData gives 0x%08X", iface,
           again);
}

static void priority_cases(const char *iface, uint32_t obj)
{
    uint32_t p0 = mi(iface, "SetPriority", 2, obj, 5, 0, 0, 0, 0);
    uint32_t p1 = mi(iface, "SetPriority", 2, obj, 7, 0, 0, 0, 0);
    result(p0 == 0 && p1 == 5, "%s::SetPriority on MANAGED returns the old value (%u, then %u)", iface, p0, p1);
    uint32_t g = mi(iface, "GetPriority", 1, obj, 0, 0, 0, 0, 0);
    result(g == 7, "%s::GetPriority reads back 7 (%u)", iface, g);
    uint32_t hr = mi(iface, "PreLoad", 1, obj, 0, 0, 0, 0, 0);
    result(hr == 0 && rt_gfx_host(obj), "%s::PreLoad returns and the object stays", iface);
}

static void resource_cases(const char *iface, uint32_t obj, uint32_t dev)
{
    device_case(iface, obj, dev);
    private_cases(iface, obj);
    priority_cases(iface, obj);
}

static void type_case(const char *iface, uint32_t obj, uint32_t want)
{
    uint32_t t = mi(iface, "GetType", 1, obj, 0, 0, 0, 0, 0);
    result(t == want, "%s::GetType gives %u (want %u)", iface, t, want);
}

static int read_back(uint32_t s, uint32_t rows, uint32_t rowbytes, uint32_t seed)
{
    uint32_t lr = galloc(8);
    if (!s || m("IDirect3DSurface9::LockRect", 4, s, lr, 0u, D3DLOCK_READONLY)) return 0;
    uint32_t pitch = MEM32(lr), b = MEM32(lr + 4);
    int ok = 1;
    for (uint32_t r = 0; ok && r < rows; r++)
        for (uint32_t c = 0; c < rowbytes; c++) ok &= MEM8(b + r * pitch + c) == pat(seed, r, c);
    m("IDirect3DSurface9::UnlockRect", 1, s);
    return ok;
}

static void fill(uint32_t lr, uint32_t rows, uint32_t rowbytes, uint32_t seed)
{
    uint32_t pitch = MEM32(lr), b = MEM32(lr + 4);
    for (uint32_t r = 0; r < rows; r++)
        for (uint32_t c = 0; c < rowbytes; c++) MEM8(b + r * pitch + c) = pat(seed, r, c);
}

static uint32_t sub_surface(const char *iface, uint32_t tex, uint32_t face, uint32_t level)
{
    uint32_t out = galloc(4), hr;
    if (!strcmp(iface, "IDirect3DTexture9")) hr = m("IDirect3DTexture9::GetSurfaceLevel", 3, tex, level, out);
    else hr = m("IDirect3DCubeTexture9::GetCubeMapSurface", 4, tex, face, level, out);
    return hr ? 0 : MEM32(out);
}

static void texture_set_cases(uint32_t dev, int cube)
{
    const char *iface = cube ? "IDirect3DCubeTexture9" : "IDirect3DTexture9";
    uint32_t size = cube ? 32u : 64u;
    refcount_case(iface, cube ? new_cube(dev, size, 1) : new_texture(dev, size, 1, 0, D3DPOOL_MANAGED));
    uint32_t t = cube ? new_cube(dev, size, 3) : new_texture(dev, size, 3, 0, D3DPOOL_MANAGED);
    resource_cases(iface, t, dev);
    uint32_t old = mi(iface, "SetLOD", 2, t, 1, 0, 0, 0, 0);
    result(old == 0, "%s::SetLOD 1 on MANAGED returns the old LOD %u", iface, old);
    uint32_t lod = mi(iface, "GetLOD", 1, t, 0, 0, 0, 0, 0);
    result(lod == 1, "%s::GetLOD reads back %u", iface, lod);
    uint32_t hr = mi(iface, "SetAutoGenFilterType", 2, t, D3DTEXF_POINT, 0, 0, 0, 0);
    result(!hr, "%s::SetAutoGenFilterType POINT returns 0x%08X", iface, hr);
    uint32_t f = mi(iface, "GetAutoGenFilterType", 1, t, 0, 0, 0, 0, 0);
    result(f == D3DTEXF_POINT, "%s::GetAutoGenFilterType reads back %u", iface, f);
    hr = mi(iface, "GenerateMipSubLevels", 1, t, 0, 0, 0, 0, 0);
    result(!hr && rt_gfx_host(t), "%s::GenerateMipSubLevels returns", iface);

    uint32_t lr0 = galloc(8), lr1 = galloc(8), face = cube ? 2u : 0u;
    uint32_t w1 = size / 2, w0 = size;
    if (cube) hr = m("IDirect3DCubeTexture9::LockRect", 6, t, face, 1u, lr1, 0u, 0u);
    else hr = m("IDirect3DTexture9::LockRect", 5, t, 1u, lr1, 0u, 0u);
    if (!hr) fill(lr1, w1, 4 * w1, 21);
    uint32_t hu = cube ? m("IDirect3DCubeTexture9::UnlockRect", 3, t, face, 1u) : m("IDirect3DTexture9::UnlockRect", 2, t, 1u);
    result(!hr && !hu && rt_span_ok(MEM32(lr1 + 4), 4ull * w1 * w1) && read_back(sub_surface(iface, t, face, 1), w1, 4 * w1, 21),
           "%s::LockRect on level 1%s: a pattern written through the lock reads back through the level surface", iface,
           cube ? " of face 2" : "");

    uint32_t h0, h1, u0, u1;
    if (cube) {
        h0 = m("IDirect3DCubeTexture9::LockRect", 6, t, face, 0u, lr0, 0u, 0u);
        h1 = m("IDirect3DCubeTexture9::LockRect", 6, t, face, 1u, lr1, 0u, 0u);
    } else {
        h0 = m("IDirect3DTexture9::LockRect", 5, t, 0u, lr0, 0u, 0u);
        h1 = m("IDirect3DTexture9::LockRect", 5, t, 1u, lr1, 0u, 0u);
    }
    if (!h0) fill(lr0, w0, 4 * w0, 22);
    u0 = cube ? m("IDirect3DCubeTexture9::UnlockRect", 3, t, face, 0u) : m("IDirect3DTexture9::UnlockRect", 2, t, 0u);
    if (!h1) fill(lr1, w1, 4 * w1, 23);
    u1 = cube ? m("IDirect3DCubeTexture9::UnlockRect", 3, t, face, 1u) : m("IDirect3DTexture9::UnlockRect", 2, t, 1u);
    int ok0 = read_back(sub_surface(iface, t, face, 0), w0, 4 * w0, 22), ok1 = read_back(sub_surface(iface, t, face, 1), w1, 4 * w1, 23);
    result(!h0 && !h1 && !u0 && !u1 && ok0 && ok1,
           "%s::UnlockRect finds its level: lock levels 0 and 1, unlock 0 first, write level 1, both patterns read back "
           "(level 0 %d, level 1 %d)", iface, ok0, ok1);
    if (cube) {
        h0 = m("IDirect3DCubeTexture9::LockRect", 6, t, 0u, 0u, lr0, 0u, 0u);
        h1 = m("IDirect3DCubeTexture9::LockRect", 6, t, 1u, 0u, lr1, 0u, 0u);
        if (!h0) fill(lr0, w0, 4 * w0, 24);
        u0 = m("IDirect3DCubeTexture9::UnlockRect", 3, t, 0u, 0u);
        if (!h1) fill(lr1, w0, 4 * w0, 25);
        u1 = m("IDirect3DCubeTexture9::UnlockRect", 3, t, 1u, 0u);
        ok0 = read_back(sub_surface(iface, t, 0, 0), w0, 4 * w0, 24);
        ok1 = read_back(sub_surface(iface, t, 1, 0), w0, 4 * w0, 25);
        result(!h0 && !h1 && !u0 && !u1 && ok0 && ok1,
               "%s::UnlockRect finds its face: lock faces 0 and 1 at level 0, unlock face 0 first, write face 1, both "
               "patterns read back (face 0 %d, face 1 %d)", iface, ok0, ok1);
    }
    uint32_t rect = galloc(16);
    static const int32_t k_r[4] = {0, 0, 8, 8};
    memcpy(rt_host(rect), k_r, 16);
    hr = cube ? m("IDirect3DCubeTexture9::AddDirtyRect", 3, t, face, rect) : m("IDirect3DTexture9::AddDirtyRect", 2, t, rect);
    uint32_t hn = cube ? m("IDirect3DCubeTexture9::AddDirtyRect", 3, t, face, 0u) : m("IDirect3DTexture9::AddDirtyRect", 2, t, 0u);
    result(!hr && !hn, "%s::AddDirtyRect with a RECT and with NULL returns D3D_OK (0x%08X, 0x%08X)", iface, hr, hn);
}

static void iunknown_case(uint32_t tex)
{
    uint32_t guid = galloc(16), data = galloc(16);
    memset(rt_host(guid), 0x42, 16);
    memset(rt_host(data), 0, 16);
    fflush(stdout);
    fflush(stderr);
    pid_t pid = fork();
    if (pid == 0) {
        uint32_t hr = m("IDirect3DTexture9::SetPrivateData", 5, tex, guid, data, 8u, D3DSPD_IUNKNOWN);
        _exit(hr == D3DERR_INVALIDCALL ? 0 : 1);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    result(WIFEXITED(st) && WEXITSTATUS(st) == 0,
           "SetPrivateData with D3DSPD_IUNKNOWN returns D3DERR_INVALIDCALL without reaching dxvk (child exit %d, signal %d)",
           WIFEXITED(st) ? WEXITSTATUS(st) : -1, WIFSIGNALED(st) ? WTERMSIG(st) : 0);
}

static void surface_set_cases(uint32_t dev)
{
    const char *iface = "IDirect3DSurface9";
    uint32_t t = new_texture(dev, 64, 1, 0, D3DPOOL_MANAGED);
    uint32_t s = sub_surface("IDirect3DTexture9", t, 0, 0);
    resource_cases(iface, s, dev);
    type_case(iface, s, D3DRTYPE_SURFACE);
    uint32_t d = galloc(36);
    memset(rt_host(d), 0xEE, 36);
    uint32_t hr = m("IDirect3DSurface9::GetDesc", 2, s, d);
    result(!hr && MEM32(d) == D3DFMT_A8R8G8B8 && MEM32(d + 4) == D3DRTYPE_SURFACE && MEM32(d + 8) == 0 &&
               MEM32(d + 12) == D3DPOOL_MANAGED && MEM32(d + 24) == 64 && MEM32(d + 28) == 64 && MEM32(d + 32) == 0xEEEEEEEEu,
           "IDirect3DSurface9::GetDesc equals the CreateTexture arguments: format %u type %u usage %u pool %u %ux%u, "
           "32 bytes", MEM32(d), MEM32(d + 4), MEM32(d + 8), MEM32(d + 12), MEM32(d + 24), MEM32(d + 28));
    iunknown_case(t);
}

static void buffer_set_cases(uint32_t dev, int vb)
{
    const char *iface = vb ? "IDirect3DVertexBuffer9" : "IDirect3DIndexBuffer9";
    uint32_t fvf = D3DFVF_XYZRHW | D3DFVF_DIFFUSE;
    refcount_case(iface, new_buffer(dev, vb, 256, fvf));
    uint32_t b = new_buffer(dev, vb, 4096, fvf);
    resource_cases(iface, b, dev);
    type_case(iface, b, vb ? D3DRTYPE_VERTEXBUFFER : D3DRTYPE_INDEXBUFFER);
    if (!vb) return;
    uint32_t d = galloc(28);
    memset(rt_host(d), 0xEE, 28);
    uint32_t hr = m("IDirect3DVertexBuffer9::GetDesc", 2, b, d);
    result(!hr && MEM32(d) == D3DFMT_VERTEXDATA && MEM32(d + 4) == D3DRTYPE_VERTEXBUFFER && MEM32(d + 12) == D3DPOOL_MANAGED &&
               MEM32(d + 16) == 4096 && MEM32(d + 20) == fvf && MEM32(d + 24) == 0xEEEEEEEEu,
           "IDirect3DVertexBuffer9::GetDesc writes 24 bytes: Size %u FVF 0x%X", MEM32(d + 16), MEM32(d + 20));
}

void rt_gfx_movie_stats(uint32_t *shown, uint32_t *dropped);

static void movie_case(void)
{
    enum { MW = 640, MH = 480, N = 60 };
    uint32_t *px = malloc((size_t)MW * MH * 4);
    if (!px || !rt_gfx_movie_ready()) {
        result(0, "movie blit: device ready and frame buffer allocated");
        free(px);
        return;
    }
    int bad = 0;
    uint64_t worst = 0;
    for (int i = 0; i < N; i++) {
        for (size_t k = 0; k < (size_t)MW * MH; k++) px[k] = 0xFF000000u | (uint32_t)(i * 4) << 8;
        uint64_t t = rt_now_ns();
        int rc = rt_gfx_movie_blit(px, MW, MH, MW * 4, MW, MH);
        uint64_t ns = rt_now_ns() - t;
        if (ns > worst) worst = ns;
        bad += rc != 0 || ns >= 1000000000ull;
        SDL_Delay(33);
    }
    uint32_t shown = 0, dropped = 0;
    rt_gfx_movie_stats(&shown, &dropped);
    rt_gfx_movie_end();
    free(px);
    result(!bad && shown >= 1 && dropped <= 2,
           "movie blit: %d calls return 0 within 1 s (bad %d, worst %.1f ms), %u shown, %u dropped (at most 2 on an idle gpu)", N,
           bad, (double)worst / 1e6, shown, dropped);
}

static void query_cases(uint32_t dev)
{
    const char *iface = "IDirect3DQuery9";
    uint32_t a[9] = {dev, D3DQUERYTYPE_EVENT, 0};
    refcount_case(iface, created_obj("IDirect3DDevice9::CreateQuery", 3, a, 2));
    uint32_t q = created_obj("IDirect3DDevice9::CreateQuery", 3, a, 2);
    device_case(iface, q, dev);
    type_case(iface, q, D3DQUERYTYPE_EVENT);
    uint32_t n = m("IDirect3DQuery9::GetDataSize", 1, q);
    result(n == 4, "IDirect3DQuery9::GetDataSize of an event query is %u", n);
    m("IDirect3DQuery9::Issue", 2, q, D3DISSUE_END);
    uint32_t buf = galloc(4), hr = 1;
    MEM32(buf) = 0;
    for (int i = 0; i < 2000 && hr == 1; i++) {
        hr = m("IDirect3DQuery9::GetData", 4, q, buf, 4u, D3DGETDATA_FLUSH);
        if (hr == 1) SDL_Delay(1);
    }
    result(hr == 0 && MEM32(buf) == 1, "IDirect3DQuery9::GetData with FLUSH after Issue(END) reaches S_OK and writes TRUE "
                                       "(hr 0x%08X, data %u)", hr, MEM32(buf));
    uint32_t hn = m("IDirect3DQuery9::GetData", 4, q, 0u, 4u, 0u), hz = m("IDirect3DQuery9::GetData", 4, q, 0u, 0u, 0u);
    result(hn == D3DERR_INVALIDCALL && hz == 0, "IDirect3DQuery9::GetData NULL with size 4 is D3DERR_INVALIDCALL (0x%08X), "
                                                "NULL with size 0 polls (0x%08X)", hn, hz);
}

static void shader_decl_cases(uint32_t dev)
{
    static const uint8_t k_el[24] = {0, 0, 0, 0, 3, 0, 9, 0, 0, 0, 16, 0, 4, 0, 10, 0, 0xFF, 0, 0, 0, 17, 0, 0, 0};
    uint32_t elems = galloc(24);
    memcpy(rt_host(elems), k_el, 24);
    uint32_t a[9] = {dev, elems, 0};
    const char *iface = "IDirect3DVertexDeclaration9";
    refcount_case(iface, created_obj("IDirect3DDevice9::CreateVertexDeclaration", 3, a, 2));
    uint32_t decl = created_obj("IDirect3DDevice9::CreateVertexDeclaration", 3, a, 2);
    device_case(iface, decl, dev);
    uint32_t out = galloc(32), cnt = galloc(4);
    memset(rt_host(out), 0xEE, 32);
    MEM32(cnt) = 0;
    uint32_t hr = m("IDirect3DVertexDeclaration9::GetDeclaration", 3, decl, 0u, cnt);
    uint32_t n = MEM32(cnt);
    hr |= m("IDirect3DVertexDeclaration9::GetDeclaration", 3, decl, out, cnt);
    result(!hr && n == 3 && !memcmp(rt_host(out), k_el, 24) && MEM32(out + 24) == 0xEEEEEEEEu,
           "IDirect3DVertexDeclaration9::GetDeclaration returns the %u elements it was created from, END included", n);

    static const uint32_t vs11[] = {0xFFFE0101u, 0x0000001Fu, 0x80000000u, 0x900F0000u,
                                    0x00000001u, 0xC00F0000u, 0x90E40000u, 0x0000FFFFu};
    static const uint32_t ps11[] = {0xFFFF0101u, 0x00000001u, 0x800F0000u, 0x90E40000u, 0x0000FFFFu};
    for (int ps = 0; ps < 2; ps++) {
        const uint32_t *code = ps ? ps11 : vs11;
        uint32_t bytes = ps ? sizeof ps11 : sizeof vs11, tok = galloc(bytes);
        memcpy(rt_host(tok), code, bytes);
        uint32_t c[9] = {dev, tok, 0};
        const char *create = ps ? "IDirect3DDevice9::CreatePixelShader" : "IDirect3DDevice9::CreateVertexShader";
        iface = ps ? "IDirect3DPixelShader9" : "IDirect3DVertexShader9";
        refcount_case(iface, created_obj(create, 3, c, 2));
        uint32_t sh = created_obj(create, 3, c, 2);
        device_case(iface, sh, dev);
        uint32_t fn = galloc(bytes + 4), sz = galloc(4);
        memset(rt_host(fn), 0xEE, bytes + 4);
        MEM32(sz) = 0;
        hr = mi(iface, "GetFunction", 3, sh, 0, sz, 0, 0, 0);
        n = MEM32(sz);
        hr |= mi(iface, "GetFunction", 3, sh, fn, sz, 0, 0, 0);
        result(!hr && n == bytes && !memcmp(rt_host(fn), code, bytes) && MEM32(fn + bytes) == 0xEEEEEEEEu,
               "%s::GetFunction returns the %u bytes it was created from", iface, n);
    }
}

static void stateblock_cases(uint32_t dev)
{
    const char *iface = "IDirect3DStateBlock9";
    uint32_t out = galloc(4);
    uint32_t hb = m("IDirect3DDevice9::BeginStateBlock", 1, dev);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_LIGHTING, 0u);
    uint32_t he = m("IDirect3DDevice9::EndStateBlock", 2, dev, out);
    uint32_t sb = MEM32(out);
    result(!hb, "IDirect3DDevice9::BeginStateBlock returns D3D_OK (0x%08X)", hb);
    result(!he && sb, "IDirect3DDevice9::EndStateBlock gives a state block proxy 0x%08X (hr 0x%08X)", sb, he);
    uint32_t ha = m("IDirect3DStateBlock9::Apply", 1, sb);
    result(!ha, "IDirect3DStateBlock9::Apply returns D3D_OK (0x%08X)", ha);
    uint32_t hc = m("IDirect3DStateBlock9::Capture", 1, sb);
    result(!hc, "IDirect3DStateBlock9::Capture returns D3D_OK (0x%08X)", hc);
    device_case(iface, sb, dev);
    refcount_case(iface, sb);
}

static uint32_t surface_pixel(uint32_t s, uint32_t x, uint32_t y)
{
    uint32_t lr = galloc(8);
    if (m("IDirect3DSurface9::LockRect", 4, s, lr, 0u, D3DLOCK_READONLY)) return 0xFFFFFFFFu;
    uint32_t v = MEM32(MEM32(lr + 4) + y * MEM32(lr) + 4 * x) & 0xFFFFFFu;
    m("IDirect3DSurface9::UnlockRect", 1, s);
    return v;
}

static void device_set_cases(uint32_t d3d, uint32_t dev)
{
    void *hd = rt_gfx_host(dev);
    uint32_t c1 = galloc(304), c2 = galloc(304);
    uint32_t h1 = m("IDirect3D9::GetDeviceCaps", 4, d3d, 0u, D3DDEVTYPE_HAL, c1);
    uint32_t h2 = m("IDirect3DDevice9::GetDeviceCaps", 2, dev, c2);
    uint32_t blend = MEM32(c2 + 172);
    MEM32(c1 + 172) = MEM32(c2 + 172) = 0;
    result(!h1 && !h2 && blend == 8 && !memcmp(rt_host(c1), rt_host(c2), 304),
           "IDirect3DDevice9::GetDeviceCaps equals IDirect3D9::GetDeviceCaps (304 bytes) except "
           "MaxVertexBlendMatrixIndex, which the hardware-VP device sets to %u", blend);
    uint32_t q = galloc(4);
    MEM32(q) = 0xEEEEEEEEu;
    uint32_t hr = m("IDirect3D9::CheckDeviceMultiSampleType", 7, d3d, 0u, D3DDEVTYPE_HAL, D3DFMT_X8R8G8B8, 1u,
                    D3DMULTISAMPLE_NONE, q);
    result(!hr && MEM32(q) != 0xEEEEEEEEu, "IDirect3D9::CheckDeviceMultiSampleType NONE succeeds (hr 0x%08X, quality "
                                           "levels %u)", hr, MEM32(q));

    uint32_t light = galloc(104);
    static const float k_light[26] = {0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10, 0, 0, 1, 100, 1, 1, 0, 0, 0, 0};
    memcpy(rt_host(light), k_light, 104);
    MEM32(light) = 1;
    hr = m("IDirect3DDevice9::SetLight", 3, dev, 0u, light);
    result(!hr, "IDirect3DDevice9::SetLight with a 104-byte point light returns D3D_OK (0x%08X)", hr);
    hr = m("IDirect3DDevice9::LightEnable", 3, dev, 0u, 1u);
    result(!hr, "IDirect3DDevice9::LightEnable returns D3D_OK (0x%08X)", hr);
    uint32_t plane = galloc(16);
    static const float k_plane[4] = {0, 0, 1, 0};
    memcpy(rt_host(plane), k_plane, 16);
    hr = m("IDirect3DDevice9::SetClipPlane", 3, dev, 0u, plane);
    result(!hr, "IDirect3DDevice9::SetClipPlane with 16 bytes returns D3D_OK (0x%08X)", hr);
    m("IDirect3DDevice9::LightEnable", 3, dev, 0u, 0u);

    uint32_t vb = new_buffer(dev, 1, 4 * sizeof(tl_vertex), D3DFVF_XYZRHW | D3DFVF_DIFFUSE), lp = galloc(4);
    tl_vertex v[3] = {{0, 0, 0.5f, 1, 0xFF0000FFu}, {(float)W, 0, 0.5f, 1, 0xFF0000FFu}, {0, (float)H, 0.5f, 1, 0xFF0000FFu}};
    if (!m("IDirect3DVertexBuffer9::Lock", 5, vb, 0u, 0u, lp, 0u)) {
        memcpy(rt_host(MEM32(lp)), v, sizeof v);
        m("IDirect3DVertexBuffer9::Unlock", 1, vb);
    }
    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFF000000u, fbits(1.0f), 0u);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_ZENABLE, 0u);
    m("IDirect3DDevice9::SetVertexShader", 2, dev, 0u);
    m("IDirect3DDevice9::SetPixelShader", 2, dev, 0u);
    m("IDirect3DDevice9::BeginScene", 1, dev);
    ((int32_t (*)(void *, uint32_t))host_fn(hd, K_DEVICE, "IDirect3DDevice9::SetFVF"))(hd, D3DFVF_XYZRHW | D3DFVF_DIFFUSE);
    m("IDirect3DDevice9::SetStreamSource", 5, dev, 0u, vb, 0u, (uint32_t)sizeof(tl_vertex));
    char where[32], detail[256];
    rt_gfx_gap(1, 0, where, sizeof where, detail, sizeof detail);
    hr = m("IDirect3DDevice9::DrawPrimitive", 4, dev, D3DPT_TRIANGLELIST, 0u, 1u);
    rt_gfx_gap(1, 1, where, sizeof where, detail, sizeof detail);
    result(!strcmp(where, "d3d9 draw"), "a gap spent in DrawPrimitive is labelled \"%s\" (want \"d3d9 draw\")", where);
    m("IDirect3DDevice9::EndScene", 1, dev);
    m("IDirect3DDevice9::SetStreamSource", 5, dev, 0u, 0u, 0u, 0u);
    uint32_t in = pixel(hd, W / 8, H / 8), outp = pixel(hd, 7 * W / 8, 7 * H / 8);
    result(!hr && in == 0x0000FFu && outp == 0, "IDirect3DDevice9::DrawPrimitive draws a triangle seen in the readback "
                                                "(inside 0x%06X, outside 0x%06X)", in, outp);

    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF2288EEu, fbits(1.0f), 0u);
    uint32_t bbo = galloc(4);
    hr = m("IDirect3DDevice9::GetBackBuffer", 5, dev, 0u, 0u, 0u, bbo);
    uint32_t bb = MEM32(bbo);
    result(!hr && bb && rt_gfx_host(bb), "IDirect3DDevice9::GetBackBuffer gives a surface proxy 0x%08X", bb);
    uint32_t ra[9] = {dev, W, H, D3DFMT_X8R8G8B8, 0, 0, 0, 0, 0};
    uint32_t rt = created_obj("IDirect3DDevice9::CreateRenderTarget", 9, ra, 7);
    result(rt && rt_gfx_host(rt), "IDirect3DDevice9::CreateRenderTarget gives a surface proxy 0x%08X", rt);
    hr = m("IDirect3DDevice9::StretchRect", 6, dev, bb, 0u, rt, 0u, 0u);
    uint32_t oa[9] = {dev, W, H, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, 0, 0, 0, 0};
    uint32_t off = created_obj("IDirect3DDevice9::CreateOffscreenPlainSurface", 7, oa, 5);
    uint32_t hg = m("IDirect3DDevice9::GetRenderTargetData", 3, dev, rt, off);
    uint32_t c = off ? surface_pixel(off, W / 2, H / 2) : 0xFFFFFFFFu;
    result(!hr && c == 0x2288EEu, "IDirect3DDevice9::StretchRect copies the cleared colour 0x2288EE into a render target "
                                  "(read 0x%06X)", c);
    result(off && rt_gfx_host(off), "IDirect3DDevice9::CreateOffscreenPlainSurface gives a SYSTEMMEM surface 0x%08X", off);
    result(!hg && c == 0x2288EEu, "IDirect3DDevice9::GetRenderTargetData and an offscreen lock read that colour (0x%06X)", c);
    m("IDirect3DSurface9::Release", 1, off);
    m("IDirect3DSurface9::Release", 1, rt);
    m("IDirect3DSurface9::Release", 1, bb);
    m("IDirect3DVertexBuffer9::Release", 1, vb);
}

static void present_n(uint32_t dev, int n)
{
    for (int i = 0; i < n; i++) m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
}

static void guest(const char *s) { rt_guest_text(0, s, (uint32_t)strlen(s)); }

static void map_cases(uint32_t dev)
{
    char name[64];
    rt_stage(11, "%s", "selftest");
    present_n(dev, 5);
    uint32_t before = rt_gfx_frame_count();
    uint64_t t = rt_now_ns() + 1000000000000ull;
    guest("Map: twosec\nR_NewMap\n");
    for (int i = 0; i < 40; i++) rt_map_present(t += 2000000000ull);
    uint32_t n = rt_map_ready(name, sizeof name);
    result(n == 0, "map tracker: 40 Presents 2 s apart after R_NewMap never reach ready (ready count %u)", n);
    guest("Map: x\nR_NewMap\n");
    for (int i = 0; i < 63; i++) rt_map_present(t += 16000000ull);
    n = rt_map_ready(name, sizeof name);
    result(n == 0, "map tracker: 63 Presents 16 ms apart span under 1 s and are not ready yet (count %u)", n);
    rt_map_present(t += 16000000ull);
    n = rt_map_ready(name, sizeof name);
    result(n == 1 && !strcmp(name, "x"), "map tracker: Map: x, R_NewMap, then 64 Presents 16 ms apart give map ready x "
                                         "(count %u, name %s)", n, name);
    present_n(dev, 3);
    uint32_t after = rt_gfx_frame_count();
    result(before == 4 && after == 2, "frames before stage 12 are not counted: %u frames at stage 11, %u after 3 Presents "
                                      "from stage 12", before, after);
    guest("Map: y\nR_NewMap\n");
    for (int i = 0; i < 64; i++) rt_map_present(t += 16000000ull);
    n = rt_map_ready(name, sizeof name);
    result(n == 2 && !strcmp(name, "y"), "map tracker: a second map makes the ready count 2 (count %u, name %s)", n, name);
    guest("R_NewMap\n");
    for (int i = 0; i < 64; i++) rt_map_present(t += 16000000ull);
    n = rt_map_ready(name, sizeof name);
    result(n == 3 && !strcmp(name, "?"), "map tracker: no Map: line since the last ready gives ? (count %u, name %s)", n, name);
    guest("Map: burst\nR_NewMap\n");
    for (int i = 0; i < 48; i++) rt_map_present(t += i & 1 ? 2000000ull : 7000000ull);
    rt_map_present(t += 163000000ull);
    rt_map_present(t += 477000000ull);
    rt_map_present(t += 257000000ull);
    for (int i = 0; i < 40; i++) rt_map_present(t += 7000000ull);
    n = rt_map_ready(name, sizeof name);
    result(n == 3, "map tracker: a loading burst of 48 fast Presents, then 477 and 257 ms stalls, is not ready (count %u)", n);
    for (int i = 0; i < 110; i++) rt_map_present(t += 7000000ull);
    n = rt_map_ready(name, sizeof name);
    result(n == 4 && !strcmp(name, "burst"), "map tracker: 1 s of steady 7 ms Presents after the last stall gives map ready "
                                             "burst (count %u, name %s)", n, name);
}

static void tex_overflow_case(uint32_t dev)
{
    uint64_t before = rt_gfx_tex_overflow();
    uint32_t out = galloc(4), made = 0;
    for (uint32_t i = 0; i < 80 && rt_gfx_tex_overflow() == before; i++, made++) {
        MEM32(out) = 0;
        if (!m("IDirect3DDevice9::CreateTexture", 9, dev, 1u, 1u, 1u, 0x01000000u + i, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM,
               out, 0u) &&
            MEM32(out))
            m("IDirect3DTexture9::Release", 1, MEM32(out));
    }
    uint64_t after = rt_gfx_tex_overflow();
    result(before == 0 && after == 1, "texture combos past 64 are counted: overflow %llu -> %llu after %u new combos",
           (unsigned long long)before, (unsigned long long)after, made);
}

static void reset_case(uint32_t d3d)
{
    rt_win *w = rt_win_new();
    w->w = 320;
    w->h = 240;
    w->toplevel = 1;
    snprintf(w->title, sizeof w->title, "bloodlines-native gfx selftest reset");
    rt_win_sdl(w);
    if (!w->sdl) {
        result(0, "second SDL window for the Reset case");
        return;
    }
    uint32_t pp = galloc(56), out = galloc(4);
    uint32_t v[14] = {320, 240, D3DFMT_X8R8G8B8, 1, 0, 0, D3DSWAPEFFECT_DISCARD, w->hwnd, 1, 1, D3DFMT_D24S8, 0, 0,
                      D3DPRESENT_INTERVAL_IMMEDIATE};
    memcpy(rt_host(pp), v, sizeof v);
    uint32_t hr = m("IDirect3D9::CreateDevice", 7, d3d, 0u, D3DDEVTYPE_HAL, w->hwnd, D3DCREATE_FLAGS, pp, out);
    uint32_t dev = MEM32(out);
    if (hr || !dev) {
        result(0, "CreateDevice for the Reset case (hr 0x%08X)", hr);
        return;
    }
    rt_present_rects r0, r1, r2;
    m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
    int ok0 = rt_gfx_present_rects(&r0);
    uint32_t ca[9] = {dev, 4096, 0, 0, D3DPOOL_DEFAULT, 0, 0, 0, 0};
    uint32_t vb = created_obj("IDirect3DDevice9::CreateVertexBuffer", 7, ca, 5);
    MEM32(pp) = 640;
    MEM32(pp + 4) = 480;
    uint32_t hb = m("IDirect3DDevice9::Reset", 2, dev, pp);
    int ok1 = rt_gfx_present_rects(&r1);
    result(vb && hb == D3DERR_INVALIDCALL && ok0 && ok1 && r1.sw == 320 && r1.sh == 240,
           "IDirect3DDevice9::Reset with a live DEFAULT-pool buffer is D3DERR_INVALIDCALL (0x%08X) and the present size "
           "stays %dx%d", hb, r1.sw, r1.sh);
    if (vb) m("IDirect3DVertexBuffer9::Release", 1, vb);
    MEM32(pp) = 640;
    MEM32(pp + 4) = 480;
    MEM32(pp + 28) = w->hwnd;
    hr = m("IDirect3DDevice9::Reset", 2, dev, pp);
    m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
    int ok2 = rt_gfx_present_rects(&r2);
    result(!hr && ok2 && r2.sw == 640 && r2.sh == 480, "after the buffer is released, Reset (0x%08X) and Present give a "
                                                       "present size of %dx%d", hr, r2.sw, r2.sh);
    uint32_t bbo = galloc(4), d = galloc(32), bb = 0, dh = 1;
    if (!hr && !m("IDirect3DDevice9::GetBackBuffer", 5, dev, 0u, 0u, 0u, bbo)) {
        bb = MEM32(bbo);
        dh = m("IDirect3DSurface9::GetDesc", 2, bb, d);
        m("IDirect3DSurface9::Release", 1, bb);
    }
    result(!hr && !dh && MEM32(d + 24) == 640 && MEM32(d + 28) == 480 && MEM32(pp + 28) == w->hwnd,
           "IDirect3DDevice9::Reset to 640x480 (hr 0x%08X): back buffer %ux%u, guest hDeviceWindow kept", hr, MEM32(d + 24),
           MEM32(d + 28));
    m("IDirect3DDevice9::Release", 1, dev);
    SDL_HideWindow(w->sdl);
}

static uint32_t aspect_device(uint32_t d3d, int ww, int wh, uint32_t bw, uint32_t bh, rt_win **win)
{
    rt_win *w = rt_win_new();
    w->w = ww;
    w->h = wh;
    w->toplevel = 1;
    snprintf(w->title, sizeof w->title, "bloodlines-native gfx selftest aspect");
    rt_win_sdl(w);
    *win = w;
    if (!w->sdl) return 0;
    uint32_t pp = galloc(56), out = galloc(4);
    uint32_t v[14] = {bw, bh, D3DFMT_X8R8G8B8, 1, 0, 0, D3DSWAPEFFECT_DISCARD, w->hwnd, 1, 1, D3DFMT_D24S8, 0, 0,
                      D3DPRESENT_INTERVAL_IMMEDIATE};
    memcpy(rt_host(pp), v, sizeof v);
    uint32_t hr = m("IDirect3D9::CreateDevice", 7, d3d, 0u, D3DDEVTYPE_HAL, w->hwnd, D3DCREATE_FLAGS, pp, out);
    return hr ? 0 : MEM32(out);
}

static char s_out[PATH_MAX];

static void save_png(const char *name, uint32_t *px, uint32_t w, uint32_t h)
{
    char p[PATH_MAX + 64];
    snprintf(p, sizeof p, "%s/m9", s_out);
    mkdir(p, 0755);
    snprintf(p, sizeof p, "%s/m9/%s", s_out, name);
    SDL_Surface *s = SDL_CreateSurfaceFrom((int)w, (int)h, SDL_PIXELFORMAT_XRGB8888, px, (int)w * 4);
    result(s && SDL_SavePNG(s, p), "capture %s %ux%u written", name, w, h);
    SDL_DestroySurface(s);
}

static void scene_png(uint32_t dev, const char *name)
{
    uint32_t *px = NULL, pw = 0, ph = 0;
    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF3366CCu, fbits(1.0f), 0u);
    if (rt_gfx_readback(rt_gfx_host(dev), &px, &pw, &ph)) {
        result(0, "back buffer readback for %s", name);
        return;
    }
    save_png(name, px, pw, ph);
    free(px);
}

static void reset_pair(uint32_t d3d)
{
    static const int sz[3][2] = {{1280, 1024}, {3440, 1440}, {1280, 1024}};
    rt_win *w = NULL;
    uint32_t dev = aspect_device(d3d, 1280, 1024, 1280, 1024, &w);
    uint32_t pp = galloc(56);
    for (int k = 0; k < 2; k++) {
        int ok = 0, released = 0;
        if (dev) {
            uint32_t v[14] = {(uint32_t)sz[k + 1][0], (uint32_t)sz[k + 1][1], D3DFMT_X8R8G8B8, 1, 0, 0, D3DSWAPEFFECT_DISCARD, w->hwnd, 1,
                              1, D3DFMT_D24S8, 0, 0, D3DPRESENT_INTERVAL_IMMEDIATE};
            memcpy(rt_host(pp), v, sizeof v);
            uint32_t ca[9] = {dev, 4096, 0, 0, D3DPOOL_DEFAULT, 0, 0, 0, 0};
            uint32_t vb = created_obj("IDirect3DDevice9::CreateVertexBuffer", 7, ca, 5);
            uint32_t hb = m("IDirect3DDevice9::Reset", 2, dev, pp);
            released = vb && hb == D3DERR_INVALIDCALL;
            if (vb) m("IDirect3DVertexBuffer9::Release", 1, vb);
            uint32_t hr = m("IDirect3DDevice9::Reset", 2, dev, pp);
            rt_present_rects r;
            m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
            ok = !hr && rt_gfx_present_rects(&r) && r.sw == sz[k + 1][0] && r.sh == sz[k + 1][1];
        }
        printf("m9 gfx reset from=%dx%d to=%dx%d ok=%d released=%d\n", sz[k][0], sz[k][1], sz[k + 1][0], sz[k + 1][1], ok, released);
        result(ok && released, "Reset %dx%d to %dx%d with the DEFAULT-pool buffer released first", sz[k][0], sz[k][1], sz[k + 1][0],
               sz[k + 1][1]);
    }
    if (dev) m("IDirect3DDevice9::Release", 1, dev);
    if (w && w->sdl) SDL_HideWindow(w->sdl);
}

static void aspect_cases(uint32_t d3d)
{
    rt_aspect_launch_set(3440, 1440);
    uint32_t n = m("IDirect3D9::GetAdapterModeCount", 3, d3d, 0u, D3DFMT_X8R8G8B8), o = galloc(16), found = 0;
    for (uint32_t i = 0; i < n; i++)
        if (!m("IDirect3D9::EnumAdapterModes", 5, d3d, 0u, D3DFMT_X8R8G8B8, i, o) && MEM32(o) == 3440 && MEM32(o + 4) == 1440)
            found++;
    result(found >= 1, "EnumAdapterModes for format 22 lists 3440x1440 among %u modes (%u entries)", n, found);
    rt_win *w = NULL;
    uint32_t dev = aspect_device(d3d, 3440, 1440, 3440, 1440, &w);
    printf("m9 gfx mode-3440x1440 enum=%d created=%d\n", found >= 1, dev != 0);
    fflush(stdout);
    if (!dev) {
        result(0, "windowed device at 3440x1440 (window %s)", w && w->sdl ? "ok" : "missing");
        return;
    }
    scene_png(dev, "mode-3440x1440.png");
    uint32_t *px = NULL, pw = 0, ph = 0;
    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF3366CCu, fbits(1.0f), 0u);
    int rb = rt_gfx_readback(rt_gfx_host(dev), &px, &pw, &ph);
    result(!rb && pw == 3440 && ph == 1440 && px[1440 * 3440 / 2 + 1720] % 0x1000000u == 0x3366CCu,
           "a windowed device at 3440x1440 is created and read back as %ux%u", pw, ph);
    free(px);
    m("IDirect3DDevice9::Release", 1, dev);
    SDL_HideWindow(w->sdl);
    dev = aspect_device(d3d, 640, 480, 1280, 720, &w);
    if (!dev) {
        result(0, "windowed device with a 1280x720 back buffer in a 640x480 window");
        return;
    }
    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF3366CCu, fbits(1.0f), 0u);
    m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
    int sw = 0, sh = 0, r[4];
    SDL_GetWindowSizeInPixels(w->sdl, &sw, &sh);
    rt_aspect_letterbox(1280, 720, sw, sh, r);
    rt_present_rects pr;
    int ok = rt_gfx_present_rects(&pr);
    result(ok && pr.sw == 1280 && pr.sh == 720 && pr.dx == r[0] && pr.dy == r[1] && pr.dw == r[2] && pr.dh == r[3] &&
               labs((long)pr.dw * 720 - (long)pr.dh * 1280) <= 1280,
           "Present into a %dx%d window letterboxes the 1280x720 back buffer to %d,%d %dx%d with its own shape", sw, sh,
           pr.dx, pr.dy, pr.dw, pr.dh);
    m("IDirect3DDevice9::Release", 1, dev);
    SDL_HideWindow(w->sdl);
    reset_pair(d3d);
    rt_aspect_launch_set(0, 0);
}

static int swapchain_lines(void)
{
    const char *dir = getenv("DXVK_LOG_PATH");
    DIR *d = dir ? opendir(dir) : NULL;
    int n = 0;
    struct dirent *e;
    while (d && (e = readdir(d))) {
        size_t l = strlen(e->d_name);
        if (l <= 9 || strcmp(e->d_name + l - 9, "_d3d9.log")) continue;
        char path[PATH_MAX + 300];
        snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
        FILE *f = fopen(path, "r");
        char line[1024];
        while (f && fgets(line, sizeof line, f))
            if (strstr(line, "Actual swapchain properties")) n++;
        if (f) fclose(f);
    }
    if (d) closedir(d);
    return n;
}

static void resize_cases(uint32_t d3d)
{
    static const int k_sz[5][2] = {{800, 600}, {1280, 720}, {500, 900}, {1600, 900}, {1024, 768}};
    int before = swapchain_lines();
    rt_win *w = NULL;
    uint32_t dev = aspect_device(d3d, 640, 480, 640, 480, &w);
    if (!dev) {
        result(0, "windowed device for the resize cases");
        return;
    }
    SDL_ShowWindow(w->sdl);
    uint64_t worst = 0;
    int frames = 0, failed = 0;
    for (int i = 0; i < 5; i++) {
        SDL_SetWindowSize(w->sdl, k_sz[i][0], k_sz[i][1]);
        SDL_SyncWindow(w->sdl);
        for (int f = 0; f < 30; f++) {
            m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF000000u + (uint32_t)(i * 40 + f), fbits(1.0f), 0u);
            uint64_t t0 = rt_now_ns();
            failed += m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u) != 0;
            uint64_t dt = rt_now_ns() - t0;
            if (dt > worst) worst = dt;
            frames++;
            SDL_PumpEvents();
        }
    }
    SDL_Delay(200);
    int made = swapchain_lines() - before;
    result(frames == 150 && !failed && worst < 500000000ull,
           "resize through 5 window sizes, 30 frames each: %d frames, %d failed, slowest Present %.1f ms (limit 500 ms)", frames, failed,
           (double)worst / 1e6);
    result(made == 1, "the dxvk log gains exactly one \"Actual swapchain properties\" line over the 5 resizes (%d)", made);
    uint32_t pp = galloc(56);
    uint32_t v[14] = {640, 480, D3DFMT_X8R8G8B8, 1, 0, 0, D3DSWAPEFFECT_DISCARD, w->hwnd, 1, 1, D3DFMT_D24S8, 0, 0,
                      D3DPRESENT_INTERVAL_IMMEDIATE};
    memcpy(rt_host(pp), v, sizeof v);
    uint32_t hr = m("IDirect3DDevice9::Reset", 2, dev, pp);
    for (int f = 0; f < 60; f++) {
        m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
        SDL_PumpEvents();
        SDL_Delay(10);
    }
    int layers, unfit;
    rt_gfx_layer_counts(w->sdl, &layers, &unfit);
    result(!hr && layers >= 1 && !unfit, "after Reset (0x%08X) every game layer keeps aspect fit: %d layers, %d not fitted", hr, layers, unfit);
    m("IDirect3DDevice9::Release", 1, dev);
    SDL_HideWindow(w->sdl);
}

static void toggle_pass(uint32_t d3d, int vsync)
{
    int keep = rt_gfx_vsync(NULL);
    rt_gfx_vsync_set(vsync);
    int before = swapchain_lines();
    uint64_t c0, l0, c1, l1;
    rt_gfx_cfg_counts(&c0, &l0);
    uint64_t sv0 = rt_gfx_link_starved();
    rt_win *w = NULL;
    uint32_t dev = aspect_device(d3d, 640, 480, 640, 480, &w);
    if (!dev) {
        result(0, "windowed device for the full-screen toggle cases (vsync %d)", vsync);
        rt_gfx_vsync_set(keep);
        return;
    }
    SDL_ShowWindow(w->sdl);
    int failed = 0, entered = 0, left = 0;
    for (int i = 0; i < 20; i++)
        for (int s = 0; s < 2; s++) {
            int full = s == 0;
            SDL_SetWindowFullscreen(w->sdl, full);
            SDL_SyncWindow(w->sdl);
            int now = (SDL_GetWindowFlags(w->sdl) & SDL_WINDOW_FULLSCREEN) != 0;
            if (full) entered += now;
            else left += !now;
            for (int f = 0; f < 30; f++) {
                m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF000000u + (uint32_t)(i * 8 + f), fbits(1.0f), 0u);
                failed += m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u) != 0;
                SDL_PumpEvents();
            }
        }
    SDL_SetWindowFullscreen(w->sdl, false);
    SDL_SyncWindow(w->sdl);
    SDL_Delay(200);
    rt_gfx_cfg_counts(&c1, &l1);
    int made = swapchain_lines() - before;
    uint64_t sv = rt_gfx_link_starved() - sv0;
    result(!failed && entered == 20 && left == 20,
           "vsync %s: 20 window and full-screen round trips, 30 frames each way: %d entered, %d left, %d presents failed", vsync ? "on" : "off",
           entered, left, failed);
    result(made == 1 && l1 == l0 && sv == 0,
           "vsync %s: one swapchain for the whole stress (%d made), %llu game-layer reconfigurations with a link attached, %llu link stalls",
           vsync ? "on" : "off", made, (unsigned long long)(l1 - l0), (unsigned long long)sv);
    m("IDirect3DDevice9::Release", 1, dev);
    SDL_HideWindow(w->sdl);
    rt_gfx_vsync_set(keep);
}

static void toggle_cases(uint32_t d3d)
{
    if (!rt_gfx_link_paced()) return;
    toggle_pass(d3d, 0);
    toggle_pass(d3d, 1);
}

static int link_settle(uint32_t dev, int want, int frames, int *failed)
{
    int ok = 0;
    uint64_t end = rt_now_ns() + 5000000000ull;
    for (int k = 0; rt_now_ns() < end && (!ok || frames > 0); k++) {
        SDL_PumpEvents();
        m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF102030u + (uint32_t)k, fbits(1.0f), 0u);
        *failed += m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u) != 0;
        if (ok) frames--;
        else ok = rt_gfx_link_layer() == want;
    }
    return ok && rt_gfx_link_layer() == want;
}

static void relayer_case(uint32_t d3d)
{
    if (!rt_gfx_link_paced()) return;
    int was = rt_toggle_get(RT_TOG_PICTURE);
    uint64_t sv0 = rt_gfx_link_starved();
    rt_win *w = NULL;
    uint32_t dev = aspect_device(d3d, 640, 480, 640, 480, &w);
    if (!dev) {
        result(0, "windowed device for the picture-mode link cases");
        return;
    }
    SDL_ShowWindow(w->sdl);
    SDL_SetWindowSize(w->sdl, 1280, 960);
    SDL_SyncWindow(w->sdl);
    int failed = 0, on_fx = 0, on_game = 0, live = 0;
    rt_toggle_parse(RT_TOG_PICTURE, "off");
    int start = link_settle(dev, 1, 30, &failed);
    for (int i = 0; i < 5; i++) {
        rt_toggle_parse(RT_TOG_PICTURE, "metalfx");
        on_fx += link_settle(dev, 2, 60, &failed);
        live += rt_fx_wanted() != 0;
        rt_toggle_parse(RT_TOG_PICTURE, "off");
        on_game += link_settle(dev, 1, 60, &failed);
    }
    uint64_t sv = rt_gfx_link_starved() - sv0;
    result(start && on_fx == 5 && on_game == 5 && live == 5 && !failed && sv == 0,
           "picture mode MetalFX and back 5 times, 60 frames each: link on the MetalFX layer %d, on the game layer %d, MetalFX live %d, %d presents "
           "failed, %llu link stalls",
           on_fx, on_game, live, failed, (unsigned long long)sv);
    rt_toggle_parse(RT_TOG_PICTURE, was == RT_PIC_METALFX ? "metalfx" : "off");
    m("IDirect3DDevice9::Release", 1, dev);
    SDL_HideWindow(w->sdl);
}

#define D3DFVF_TEX1 0x100u
#define D3DSAMP_MAGFILTER 5u
#define D3DSAMP_MINFILTER 6u
#define D3DSAMP_MIPFILTER 7u
#define D3DTEXF_LINEAR 2u
#define ANISO_TEX 256u
#define ANISO_LEVELS 9u

typedef struct {
    float x, y, z, rhw;
    uint32_t color;
    float u, v;
} tex_vertex;

static uint32_t aniso_texture(uint32_t dev)
{
    uint32_t tex = new_texture(dev, ANISO_TEX, ANISO_LEVELS, 0, D3DPOOL_MANAGED), lr = galloc(8);
    for (uint32_t l = 0; tex && l < ANISO_LEVELS; l++) {
        uint32_t s = ANISO_TEX >> l, cell = 16u >> l;
        if (m("IDirect3DTexture9::LockRect", 5, tex, l, lr, 0u, 0u)) return 0;
        uint32_t pitch = MEM32(lr), b = MEM32(lr + 4);
        for (uint32_t y = 0; y < s; y++)
            for (uint32_t x = 0; x < s; x++)
                MEM32(b + y * pitch + 4 * x) = !cell ? 0xFF808080u : ((x / cell + y / cell) & 1u) ? 0xFFFFFFFFu : 0xFF000000u;
        m("IDirect3DTexture9::UnlockRect", 2, tex, l);
    }
    return tex;
}

static void aniso_case(uint32_t d3d)
{
    rt_win *w = NULL;
    uint32_t dev = aspect_device(d3d, (int)W, (int)H, W, H, &w);
    uint32_t tex = dev ? aniso_texture(dev) : 0;
    result(dev && tex, "aniso: device and a 256x256 checkerboard with %u mip levels", ANISO_LEVELS);
    if (!dev || !tex) return;
    void *hd = rt_gfx_host(dev);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_ZENABLE, 0u);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_CULLMODE, D3DCULL_NONE);
    m("IDirect3DDevice9::SetRenderState", 3, dev, D3DRS_LIGHTING, 0u);
    m("IDirect3DDevice9::SetTexture", 3, dev, 0u, tex);
    m("IDirect3DDevice9::SetSamplerState", 4, dev, 0u, D3DSAMP_MINFILTER, D3DTEXF_LINEAR);
    m("IDirect3DDevice9::SetSamplerState", 4, dev, 0u, D3DSAMP_MAGFILTER, D3DTEXF_LINEAR);
    m("IDirect3DDevice9::SetSamplerState", 4, dev, 0u, D3DSAMP_MIPFILTER, D3DTEXF_LINEAR);
    const float cx = (float)W / 2, near_half = 3.0f * (float)W, far_rhw = 0.0625f, ur = 2.0f, vn = 2.0f, vf = 8.0f;
    tex_vertex v[4] = {{cx - near_half * far_rhw, 0, 0.5f, far_rhw, 0xFFFFFFFFu, -ur, vf},
                       {cx + near_half * far_rhw, 0, 0.5f, far_rhw, 0xFFFFFFFFu, ur, vf},
                       {cx - near_half, (float)H, 0.5f, 1.0f, 0xFFFFFFFFu, -ur, vn},
                       {cx + near_half, (float)H, 0.5f, 1.0f, 0xFFFFFFFFu, ur, vn}};
    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFF000000u, fbits(1.0f), 0u);
    m("IDirect3DDevice9::BeginScene", 1, dev);
    ((int32_t (*)(void *, uint32_t))host_fn(hd, K_DEVICE, "IDirect3DDevice9::SetFVF"))(hd, D3DFVF_XYZRHW | D3DFVF_DIFFUSE | D3DFVF_TEX1);
    ((int32_t (*)(void *, uint32_t, uint32_t, const void *, uint32_t))host_fn(hd, K_DEVICE, "IDirect3DDevice9::DrawPrimitiveUP"))(
        hd, D3DPT_TRIANGLESTRIP, 2, v, sizeof v[0]);
    m("IDirect3DDevice9::EndScene", 1, dev);
    uint32_t *px = NULL, pw = 0, ph = 0;
    if (rt_gfx_readback(hd, &px, &pw, &ph)) {
        result(0, "aniso: back buffer readback");
    } else {
        save_png("aniso.png", px, pw, ph);
        free(px);
    }
    m("IDirect3DDevice9::SetTexture", 3, dev, 0u, 0u);
    m("IDirect3DTexture9::Release", 1, tex);
    m("IDirect3DDevice9::Release", 1, dev);
    if (w && w->sdl) SDL_HideWindow(w->sdl);
}

static void m6_cases(uint32_t d3d, uint32_t dev)
{
    device_set_cases(d3d, dev);
    texture_set_cases(dev, 0);
    texture_set_cases(dev, 1);
    surface_set_cases(dev);
    buffer_set_cases(dev, 1);
    buffer_set_cases(dev, 0);
    query_cases(dev);
    shader_decl_cases(dev);
    stateblock_cases(dev);
    tex_overflow_case(dev);
    map_cases(dev);
    reset_case(d3d);
    aspect_cases(d3d);
    resize_cases(d3d);
    toggle_cases(d3d);
    relayer_case(d3d);
    aniso_case(d3d);
}

static void path_cases(void)
{
    rt_gfx_paths p;
    const char *app = "/Applications/My Game/Bloodlines Native.app/Contents/MacOS/bloodlines-native";
    rt_gfx_resolve_paths(app, "/h", NULL, NULL, NULL, NULL, &p);
    result(p.bundled && !strcmp(p.lib, "/Applications/My Game/Bloodlines Native.app/Contents/Frameworks/libdxvk_d3d9.0.dylib") &&
               !strcmp(p.loader, "/Applications/My Game/Bloodlines Native.app/Contents/Frameworks/libvulkan.1.dylib") &&
               !strcmp(p.icd, "/Applications/My Game/Bloodlines Native.app/Contents/Resources/kosmickrisp_mesa_icd.aarch64.json"),
           "gfx paths inside a .app resolve to Frameworks and Resources");
    rt_gfx_resolve_paths("/h/build/bloodlines-native", "/h", NULL, NULL, NULL, NULL, &p);
    result(!p.bundled && !strcmp(p.lib, "/h/Library/Caches/BloodlinesPorter/dev/dxvk/inst/lib/libdxvk_d3d9.0.dylib") &&
               !strncmp(p.loader, "/opt/homebrew/", 14) && !strncmp(p.icd, "/opt/homebrew/", 14),
           "gfx paths outside a .app keep the dev paths");
    rt_gfx_resolve_paths("/h/build/bloodlines-native", "/h", "/d", NULL, NULL, NULL, &p);
    result(!p.bundled && !strcmp(p.lib, "/d/dxvk/inst/lib/libdxvk_d3d9.0.dylib"), "gfx paths outside a .app follow BN_DEV_HOME");
    rt_gfx_resolve_paths("/Users/x/Game/Contents/MacOS/bloodlines-native", "/h", NULL, NULL, NULL, NULL, &p);
    result(!p.bundled, "gfx paths: Contents/MacOS outside a .app is not a bundle");
    rt_gfx_resolve_paths(app, "/h", "/d", "/e/lib", "/e/loader", "/e/icd", &p);
    result(p.bundled && !strcmp(p.lib, "/e/lib") && !strcmp(p.loader, "/e/loader") && !strcmp(p.icd, "/e/icd"),
           "gfx paths: env values win inside a .app");
    rt_gfx_resolve_paths(app, "/h", NULL, NULL, "/e/loader", NULL, &p);
    result(!strcmp(p.loader, "/e/loader") && strstr(p.lib, "/Contents/Frameworks/") && strstr(p.icd, "/Contents/Resources/"),
           "gfx paths: one env value overrides only itself");
}

#define CARD_W 1280
#define CARD_H 1024
#define CARD_RECTS 512

static uint32_t s_rects;

static void card_fill(uint32_t dev, const int32_t *r, uint32_t n, uint32_t argb)
{
    if (!s_rects) s_rects = galloc(16 * CARD_RECTS);
    for (uint32_t at = 0; at < n; at += CARD_RECTS) {
        uint32_t k = n - at < CARD_RECTS ? n - at : CARD_RECTS;
        memcpy(rt_host(s_rects), r + 4 * at, 16 * k);
        m("IDirect3DDevice9::Clear", 7, dev, k, s_rects, D3DCLEAR_TARGET, argb, fbits(1.0f), 0u);
    }
}

static void card_draw(uint32_t dev, const int patch[4], uint32_t rgb, const int lines[4])
{
    static int32_t r[4 * 4096];
    uint32_t n = 0;
    m("IDirect3DDevice9::Clear", 7, dev, 0u, 0u, D3DCLEAR_TARGET, 0xFFC8C8C8u, fbits(1.0f), 0u);
    for (uint32_t i = 0; i < 256; i++) {
        int32_t g[4] = {(int32_t)i * 5, 32, (int32_t)i * 5 + 5, 224};
        card_fill(dev, g, 1, 0xFF000000u | (16u + i * 224u / 255u) << 16 | (240u - i * 200u / 255u) << 8 | 96u);
    }
    int32_t box[4] = {lines[0], lines[1], lines[0] + lines[2], lines[1] + lines[3]};
    card_fill(dev, box, 1, 0xFFF0F0F0u);
    for (int y = lines[1] + 3; y < lines[1] + lines[3]; y += 6) {
        int32_t *q = r + 4 * n++;
        q[0] = lines[0], q[1] = y, q[2] = lines[0] + lines[2], q[3] = y + 1;
    }
    for (int x = lines[0] + 3; x < lines[0] + lines[2]; x += 6) {
        int32_t *q = r + 4 * n++;
        q[0] = x, q[1] = lines[1], q[2] = x + 1, q[3] = lines[1] + lines[3];
    }
    card_fill(dev, r, n, 0xFF282828u);
    n = 0;
    for (int y = 600; y + 16 <= 880; y += 20)
        for (int x = 64; x + 12 <= 900; x += 14) {
            uint32_t h = (uint32_t)(x * 73856093) ^ (uint32_t)(y * 19349663);
            static const int8_t k_stroke[6][4] = {{1, 1, 3, 15}, {9, 1, 11, 15}, {1, 1, 11, 3}, {1, 7, 11, 9}, {1, 13, 11, 15}, {5, 1, 7, 15}};
            for (int s = 0; s < 6 && n < 4096; s++)
                if (h >> (s * 3) & 1u) {
                    int32_t *q = r + 4 * n++;
                    q[0] = x + k_stroke[s][0], q[1] = y + k_stroke[s][1], q[2] = x + k_stroke[s][2], q[3] = y + k_stroke[s][3];
                }
        }
    card_fill(dev, r, n, 0xFF202020u);
    int32_t p[4] = {patch[0], patch[1], patch[0] + patch[2], patch[1] + patch[3]};
    card_fill(dev, p, 1, 0xFF000000u | rgb);
}

static void fx_perf(uint32_t dev, rt_win *w, int frames)
{
    rt_toggle_parse(RT_TOG_PICTURE, "metalfx");
    SDL_ShowWindow(w->sdl);
    float dens = SDL_GetWindowPixelDensity(w->sdl);
    if (dens <= 0) dens = 1;
    SDL_SetWindowSize(w->sdl, (int)(3440 / dens), (int)(1440 / dens));
    SDL_SyncWindow(w->sdl);
    int live = 0;
    for (int k = 0; k < 400 && !live; k++) {
        SDL_PumpEvents();
        m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
        live = rt_fx_wanted();
        if (!live) SDL_Delay(10);
    }
    int pw = 0, ph = 0;
    SDL_GetWindowSizeInPixels(w->sdl, &pw, &ph);
    printf("m9 fx-spatial perf window=%dx%d frames=%d live=%d\n", pw, ph, frames, live);
    fflush(stdout);
    result(live, "picture mode MetalFX turns the fx view on within 4 s");
    uint64_t t0 = rt_now_ns();
    for (int k = 0; live && k < frames; k++) {
        SDL_PumpEvents();
        m("IDirect3DDevice9::Present", 5, dev, 0u, 0u, 0u, 0u);
        rt_fx_drain();
    }
    printf("m9 fx-spatial perf wall_s=%.2f\n", (double)(rt_now_ns() - t0) / 1e9);
    rt_fx_report();
}

int rt_gfx_fx_selftest(const char *overlay, int frames)
{
    char ov[PATH_MAX];
    if (!realpath(overlay, ov)) snprintf(ov, sizeof ov, "%s", overlay);
    snprintf(s_out, sizeof s_out, "%s", ov);
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    if (rt_window_init() || rt_gfx_init(RT_GFX_DXVK, ov)) {
        result(0, "SDL and dxvk for fx-spatial");
        goto done;
    }
    uint32_t d3d = rt_find_shim("d3d9.dll", "Direct3DCreate9")->fn((const uint32_t[]){D3D_SDK_VERSION});
    rt_win *w = NULL;
    uint32_t dev = d3d ? aspect_device(d3d, CARD_W, CARD_H, CARD_W, CARD_H, &w) : 0;
    result(dev != 0, "fx-spatial: a %ux%u dxvk device", CARD_W, CARD_H);
    if (!dev) goto done;
    uint64_t seed = rt_now_ns() ^ (uint64_t)getpid() * 2654435761u;
    uint32_t rgb = 0;
    for (int c = 0; c < 3; c++) rgb = rgb << 8 | (uint32_t)(40 + (seed >> (c * 11)) % 176);
    const int patch[4] = {960, 640, 192, 128}, lines[4] = {64, 288, 1152, 256};
    card_draw(dev, patch, rgb, lines);
    uint32_t *px = NULL, cw = 0, ch = 0;
    if (rt_gfx_readback(rt_gfx_host(dev), &px, &cw, &ch)) {
        result(0, "fx-spatial: card read back from dxvk");
        goto done;
    }
    save_png("card.png", px, cw, ch);
    printf("m9 fx-spatial card w=%u h=%u patch=%d,%d,%d,%d rgb=%u,%u,%u lines=%d,%d,%d,%d\n", cw, ch, patch[0], patch[1], patch[2],
           patch[3], rgb >> 16 & 255u, rgb >> 8 & 255u, rgb & 255u, lines[0], lines[1], lines[2], lines[3]);
    static const uint32_t k_out[2][2] = {{2560, 2048}, {1800, 1440}};
    for (int k = 0; k < 2; k++) {
        uint32_t ow = k_out[k][0], oh = k_out[k][1];
        uint32_t *o = malloc((size_t)ow * oh * 4);
        double ms = 0;
        int rc = o ? rt_fx_upscale_sync(px, cw, ch, cw * 4, ow, oh, o, &ms) : -5;
        char name[64];
        snprintf(name, sizeof name, "spatial-%ux%u.png", ow, oh);
        printf("m9 fx-spatial out w=%u h=%u rc=%d gpu_ms=%.3f\n", ow, oh, rc, ms);
        result(rc == 0, "fx-spatial: MTLFXSpatialScaler %ux%u -> %ux%u (rc %d)", cw, ch, ow, oh, rc);
        if (!rc) save_png(name, o, ow, oh);
        free(o);
    }
    free(px);
    if (frames > 0) {
        fx_perf(dev, w, frames);
        uint32_t r = m("IDirect3DDevice9::Release", 1, dev);
        result(r == 0 && !rt_gfx_host(dev), "fx-spatial: device Release frees the device with the readback surface cached (count %u)", r);
    }
done:
    printf("selftest: %d PASS, %d FAIL\n", s_pass, s_fail);
    fflush(stdout);
    fflush(stderr);
    _exit(s_fail ? 1 : 0);
}

int rt_gfx_selftest(const char *overlay)
{
    path_cases();
    char ov[PATH_MAX];
    if (!realpath(overlay, ov)) snprintf(ov, sizeof ov, "%s", overlay);
    snprintf(s_out, sizeof s_out, "%s", ov);
    rt_settings_load(ov, 0);
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    SDL_SetHint(SDL_HINT_MAC_BACKGROUND_APP, "1");
    if (rt_window_init()) {
        result(0, "SDL_Init");
        return 1;
    }
    if (rt_gfx_init(RT_GFX_DXVK, ov)) {
        result(0, "dxvk files present and loaded");
        return 1;
    }
    args_case();
    thread_case();
    layout_cases();
    size_cases();
    wsock_cases();
    gdi_font_cases();

    uint32_t d3d = rt_find_shim("d3d9.dll", "Direct3DCreate9")->fn((const uint32_t[]){D3D_SDK_VERSION});
    result(d3d != 0, "Direct3DCreate9 gives a bridge proxy 0x%08X", d3d);
    if (!d3d) goto done;
    uint32_t id = galloc(1104);
    memset(rt_host(id), 0xEE, 1104);
    uint32_t hr = m("IDirect3D9::GetAdapterIdentifier", 4, d3d, 0u, 0u, id);
    result(!hr && MEM32(id + 1100) == 0xEEEEEEEEu && MEM8(id + 512) && MEM8(id + 512) != 0xEE,
           "GetAdapterIdentifier writes 1100 bytes: \"%.64s\" vendor 0x%04X device 0x%04X", (const char *)rt_host(id + 512),
           MEM32(id + 1064), MEM32(id + 1068));
    uint32_t caps = galloc(304);
    hr = m("IDirect3D9::GetDeviceCaps", 4, d3d, 0u, D3DDEVTYPE_HAL, caps);
    uint32_t dc = MEM32(caps + 28);
    result(!hr && (dc & D3DDEVCAPS_HWTRANSFORMANDLIGHT) && (dc & D3DDEVCAPS_PUREDEVICE),
           "GetDeviceCaps DevCaps 0x%08X has HWTRANSFORMANDLIGHT and PUREDEVICE, so the game picks 0x52", dc);

    rt_win *w = rt_win_new();
    w->w = (int)W;
    w->h = (int)H;
    w->toplevel = 1;
    snprintf(w->title, sizeof w->title, "bloodlines-native gfx selftest");
    rt_win_sdl(w);
    if (!w->sdl) {
        result(0, "SDL window with SDL_WINDOW_VULKAN");
        goto done;
    }
    SDL_ShowWindow(w->sdl);
    uint32_t pp = galloc(56), out = galloc(4);
    uint32_t v[14] = {W, H, D3DFMT_X8R8G8B8, 1, 0, 0, D3DSWAPEFFECT_DISCARD, w->hwnd, 1, 1, D3DFMT_D24S8, 0, 0,
                      D3DPRESENT_INTERVAL_IMMEDIATE};
    memcpy(rt_host(pp), v, sizeof v);
    hr = m("IDirect3D9::CreateDevice", 7, d3d, 0u, D3DDEVTYPE_HAL, w->hwnd, D3DCREATE_FLAGS, pp, out);
    uint32_t dev = MEM32(out);
    result(!hr && dev && MEM32(pp + 28) == w->hwnd,
           "CreateDevice with flags 0x52 on the SDL Vulkan window (hr 0x%08X), guest hDeviceWindow kept", hr);
    if (!dev) goto done;
    scene_png(dev, "backbuffer.png");
    buffer_cases(dev, 1);
    buffer_cases(dev, 0);
    texture_cases(dev);
    draw_cases(dev);
    movie_case();
    m6_cases(d3d, dev);
done:
    printf("selftest: %d PASS, %d FAIL\n", s_pass, s_fail);
    rt_gfx_report();
    fflush(stdout);
    fflush(stderr);
    _exit(s_fail ? 1 : 0);
}
