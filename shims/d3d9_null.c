#include "recomp_types.h"
#include "rt.h"

#include <string.h>

#define NOTAVAILABLE 0x8876086Au
#define INVALIDCALL 0x8876086Cu
#define O_VTBL 0
#define O_REFS 4
#define O_KIND 8
#define O_W 12
#define O_H 16
#define O_LEVELS 20
#define O_FMT 24
#define O_POOL 28
#define O_USAGE 32
#define O_SIZE 36
#define O_DATA 40
#define O_DEV 44
#define O_FVF 48
#define O_LEVEL 52
#define O_PARENT 56
#define O_VIDMEM 60
#define O_BUFS 64
#define OBJ_SIZE (O_BUFS + 4 * 16 * 6)

static uint32_t s_vtbl[K_N], s_d3d, s_dev, s_rs[256], s_vp[6], s_cursor, s_rt, s_ds, s_back, s_swap;
static uint32_t s_pp[14];
static uint64_t s_vidmem;
#define VIDMEM_CAP (256ull << 20)

static uint32_t d_ok(const uint32_t *a) { (void)a; return 0; }
static uint32_t d_one(const uint32_t *a) { (void)a; return 1; }

static uint32_t d_addref(const uint32_t *a) { return ++MEM32(a[0] + O_REFS); }

static uint32_t d_release(const uint32_t *a)
{
    uint32_t o = a[0], r = MEM32(o + O_REFS);
    if (!r) return 0;
    MEM32(o + O_REFS) = --r;
    uint32_t k = MEM32(o + O_KIND);
    if (!r && k >= K_SURFACE && k <= K_IB && o != s_back && o != s_rt && o != s_ds) {
        s_vidmem -= MEM32(o + O_VIDMEM);
        for (int i = 0; i < 16 * 6; i++) rt_heap_free(MEM32(o + O_BUFS + 4 * i));
        rt_heap_free(MEM32(o + O_DATA));
        MEM32(o + O_KIND) = K_N;
        rt_heap_free(o);
    }
    return r;
}

static uint32_t out_obj(uint32_t pp, uint32_t obj)
{
    if (!pp) return INVALIDCALL;
    if (obj) d_addref(&obj);
    MEM32(pp) = obj;
    return 0;
}

static uint32_t new_out(uint32_t pp, uint32_t obj)
{
    if (!pp) return INVALIDCALL;
    MEM32(pp) = obj;
    return obj ? 0 : 0x8007000Eu;
}

static uint32_t d_gettype(const uint32_t *a)
{
    static const uint32_t t[K_N] = {[K_SURFACE] = 1, [K_VOLUME] = 2, [K_TEXTURE] = 3, [K_VOLTEX] = 4, [K_CUBE] = 5, [K_VB] = 6, [K_IB] = 7};
    uint32_t k = MEM32(a[0] + O_KIND);
    return k < K_N ? t[k] : 0;
}

static uint32_t bpp_bits(uint32_t f)
{
    switch (f) {
    case 20: return 24;
    case 23: case 24: case 25: case 26: case 29: case 30: case 51: case 60: case 61: case 80: case 81: case 70: case 111: return 16;
    case 28: case 41: case 50: case 3: return 8;
    case 113: case 115: case 36: return 64;
    case 116: return 128;
    case 0x31545844u: return 4;
    case 0x32545844u: case 0x33545844u: case 0x34545844u: case 0x35545844u: return 8;
    default: return 32;
    }
}

static int is_dxt(uint32_t f) { return (f & 0x00FFFFFFu) == 0x00545844u; }

static uint32_t level_dim(uint32_t v, uint32_t l)
{
    v >>= l;
    return v ? v : 1;
}

#define MAX_BYTES 0x7FFF0000ull

uint64_t rt_pitch_of(uint32_t f, uint32_t w)
{
    if (is_dxt(f)) return (((uint64_t)w + 3) / 4) * (f == 0x31545844u ? 8u : 16u);
    return ((uint64_t)w * bpp_bits(f) + 7) / 8;
}

uint64_t rt_rows_of(uint32_t f, uint32_t h) { return is_dxt(f) ? ((uint64_t)h + 3) / 4 : h; }

static uint32_t new_obj(int kind)
{
    uint32_t o = rt_heap_alloc(OBJ_SIZE, 1);
    if (!o) return 0;
    MEM32(o + O_VTBL) = s_vtbl[kind];
    MEM32(o + O_REFS) = 1;
    MEM32(o + O_KIND) = (uint32_t)kind;
    MEM32(o + O_DEV) = s_dev;
    return o;
}

static uint32_t level_buf(uint32_t o, uint32_t slot, uint32_t w, uint32_t h, uint32_t d)
{
    uint32_t p = MEM32(o + O_BUFS + 4 * slot);
    if (!p) {
        uint32_t f = MEM32(o + O_FMT);
        uint64_t n = rt_pitch_of(f, w) * rt_rows_of(f, h) * d + 16;
        if (n > MAX_BYTES) return 0;
        p = rt_heap_alloc((uint32_t)n, 1);
        MEM32(o + O_BUFS + 4 * slot) = p;
    }
    return p;
}

static uint32_t new_surface(uint32_t w, uint32_t h, uint32_t fmt, uint32_t pool, uint32_t usage, uint32_t parent, uint32_t level)
{
    uint32_t s = new_obj(K_SURFACE);
    if (!s) return 0;
    MEM32(s + O_W) = w;
    MEM32(s + O_H) = h;
    MEM32(s + O_FMT) = fmt;
    MEM32(s + O_POOL) = pool;
    MEM32(s + O_USAGE) = usage;
    MEM32(s + O_PARENT) = parent;
    MEM32(s + O_LEVEL) = level;
    return s;
}

static void surface_desc(uint32_t p, uint32_t fmt, uint32_t type, uint32_t usage, uint32_t pool, uint32_t w, uint32_t h)
{
    uint32_t v[8] = {fmt, type, usage, pool, 0, 0, w, h};
    memcpy(rt_host(p), v, sizeof v);
}

static uint32_t t_levels(const uint32_t *a) { return MEM32(a[0] + O_LEVELS); }

static uint32_t t_desc(const uint32_t *a)
{
    uint32_t o = a[0], l = a[1];
    if (l >= MEM32(o + O_LEVELS)) return INVALIDCALL;
    surface_desc(a[2], MEM32(o + O_FMT), 1, MEM32(o + O_USAGE), MEM32(o + O_POOL), level_dim(MEM32(o + O_W), l),
                 level_dim(MEM32(o + O_H), l));
    return 0;
}

static uint32_t lock_rect(uint32_t o, uint32_t slot, uint32_t w, uint32_t h, uint32_t out, uint32_t rect)
{
    uint32_t f = MEM32(o + O_FMT), pitch = (uint32_t)rt_pitch_of(f, w), p = level_buf(o, slot, w, h, 1);
    if (!p) return 0x8007000Eu;
    if (rect) {
        uint32_t x = MEM32(rect), y = MEM32(rect + 4);
        if (x >= w || y >= h) return INVALIDCALL;
        p += is_dxt(f) ? (y / 4) * pitch + (x / 4) * (f == 0x31545844u ? 8 : 16) : y * pitch + x * (bpp_bits(f) / 8);
    }
    MEM32(out) = pitch;
    MEM32(out + 4) = p;
    return 0;
}

static uint32_t t_surface(const uint32_t *a)
{
    uint32_t o = a[0], l = a[1];
    if (l >= MEM32(o + O_LEVELS)) return INVALIDCALL;
    return out_obj(a[2], new_surface(level_dim(MEM32(o + O_W), l), level_dim(MEM32(o + O_H), l), MEM32(o + O_FMT),
                                     MEM32(o + O_POOL), MEM32(o + O_USAGE), o, l));
}

static uint32_t c_surface(const uint32_t *a)
{
    uint32_t o = a[0], l = a[2];
    if (l >= MEM32(o + O_LEVELS) || a[1] >= 6) return INVALIDCALL;
    uint32_t w = level_dim(MEM32(o + O_W), l);
    return out_obj(a[3], new_surface(w, w, MEM32(o + O_FMT), MEM32(o + O_POOL), MEM32(o + O_USAGE), o, a[1] * 16 + l));
}

static uint32_t s_lock(const uint32_t *a)
{
    uint32_t s = a[0], parent = MEM32(s + O_PARENT);
    if (parent) {
        uint32_t f = MEM32(parent + O_FMT), slot = MEM32(s + O_LEVEL), w = MEM32(s + O_W), h = MEM32(s + O_H);
        (void)f;
        return lock_rect(parent, slot, w, h, a[1], a[2]);
    }
    return lock_rect(s, 0, MEM32(s + O_W), MEM32(s + O_H), a[1], a[2]);
}

static uint32_t b_lock(const uint32_t *a)
{
    uint32_t o = a[0];
    if (!MEM32(o + O_DATA)) MEM32(o + O_DATA) = rt_heap_alloc(MEM32(o + O_SIZE) + 16, 1);
    if (!MEM32(o + O_DATA)) return 0x8007000Eu;
    if (a[1] > MEM32(o + O_SIZE)) return INVALIDCALL;
    MEM32(a[3]) = MEM32(o + O_DATA) + a[1];
    return 0;
}

static uint32_t b_desc(const uint32_t *a)
{
    uint32_t o = a[0], vb = MEM32(o + O_KIND) == K_VB;
    uint32_t v[6] = {vb ? 100u : MEM32(o + O_FMT), vb ? 6u : 7u, MEM32(o + O_USAGE), MEM32(o + O_POOL), MEM32(o + O_SIZE),
                     MEM32(o + O_FVF)};
    memcpy(rt_host(a[1]), v, vb ? 24 : 20);
    return 0;
}

static void caps(uint32_t p)
{
    uint32_t c[76];
    float f;
    memset(c, 0, sizeof c);
    for (int i = 2; i <= 21; i++) c[i] = 0xFFFFFFFFu;
    c[0] = 1;
    c[22] = c[23] = 2048;
    c[24] = 256;
    c[25] = 8192;
    c[26] = 2048;
    c[27] = 1;
    f = 1e10f;
    memcpy(&c[28], &f, 4);
    f = -1e8f;
    memcpy(&c[29], &f, 4);
    memcpy(&c[30], &f, 4);
    f = 1e8f;
    memcpy(&c[31], &f, 4);
    memcpy(&c[32], &f, 4);
    c[34] = c[36] = c[39] = 0xFFFFFFFFu;
    c[35] = 8;
    c[37] = 8;
    c[38] = 4;
    c[40] = 8;
    c[41] = 6;
    c[42] = 4;
    f = 64.0f;
    memcpy(&c[44], &f, 4);
    c[45] = 0xFFFFFu;
    c[46] = 0xFFFFFu;
    c[47] = 16;
    c[48] = 255;
    c[49] = 0xFFFE0101u;
    c[50] = 96;
    c[51] = 0xFFFF0101u;
    f = 1.0f;
    memcpy(&c[52], &f, 4);
    c[58] = 1;
    c[60] = 1;
    memcpy(rt_host(p), c, sizeof c);
}

static const uint32_t k_modes[][2] = {{640, 480}, {800, 600}, {1024, 768}, {1280, 720}, {1280, 1024}, {1920, 1080}};

static int fmt_ok(uint32_t f) { return f == 22 || f == 21 || f == 23; }

static uint32_t d9_ident(const uint32_t *a)
{
    uint32_t p = a[3];
    memset(rt_host(p), 0, 1100);
    strcpy((char *)rt_host(p), "null.dll");
    strcpy((char *)rt_host(p + 512), "Null Direct3D 9 device");
    strcpy((char *)rt_host(p + 1024), "\\\\.\\DISPLAY1");
    MEM32(p + 1056) = 0x00000001u;
    MEM32(p + 1060) = 0x00060000u;
    return 0;
}

static uint32_t d9_modecount(const uint32_t *a) { return fmt_ok(a[2]) ? (uint32_t)(sizeof k_modes / sizeof k_modes[0]) : 0; }

static uint32_t d9_enummodes(const uint32_t *a)
{
    if (!fmt_ok(a[2]) || a[3] >= sizeof k_modes / sizeof k_modes[0]) return INVALIDCALL;
    uint32_t v[4] = {k_modes[a[3]][0], k_modes[a[3]][1], 60, a[2]};
    memcpy(rt_host(a[4]), v, sizeof v);
    return 0;
}

static uint32_t disp_mode(uint32_t p)
{
    int w, h;
    rt_desktop_size(&w, &h);
    uint32_t v[4] = {(uint32_t)w, (uint32_t)h, 60, 22};
    memcpy(rt_host(p), v, sizeof v);
    return 0;
}

static uint32_t d9_dispmode(const uint32_t *a) { return disp_mode(a[2]); }

static uint32_t d9_checkfmt(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t d9_caps(const uint32_t *a)
{
    caps(a[3]);
    return 0;
}

static uint32_t d9_createdevice(const uint32_t *a)
{
    uint32_t pp = a[5];
    if (!pp || !a[6]) return INVALIDCALL;
    memcpy(s_pp, rt_host(pp), sizeof s_pp);
    if (!s_pp[0] || !s_pp[1]) {
        rt_win *w = rt_win_get(s_pp[7] ? s_pp[7] : a[3]);
        if (!s_pp[0]) s_pp[0] = w ? (uint32_t)w->w : 640;
        if (!s_pp[1]) s_pp[1] = w ? (uint32_t)w->h : 480;
        if (!s_pp[2]) s_pp[2] = 22;
        MEM32(pp) = s_pp[0];
        MEM32(pp + 4) = s_pp[1];
        MEM32(pp + 8) = s_pp[2];
    }
    if (!s_dev) {
        s_dev = new_obj(K_DEVICE);
        MEM32(s_dev + O_DEV) = s_dev;
        MEM32(s_dev + O_W) = a[3];
        MEM32(s_dev + O_H) = a[4];
        s_back = new_surface(s_pp[0], s_pp[1], s_pp[2], 0, 1, 0, 0);
        s_rt = s_back;
        s_swap = new_obj(K_SWAP);
        if (s_pp[9]) s_ds = new_surface(s_pp[0], s_pp[1], s_pp[10], 0, 2, 0, 0);
        s_vp[2] = s_pp[0];
        s_vp[3] = s_pp[1];
        float one = 1.0f;
        memcpy(&s_vp[5], &one, 4);
    }
    rt_log("IDirect3D9::CreateDevice %ux%u format %u windowed %u -> null device 0x%08X", s_pp[0], s_pp[1], s_pp[2], s_pp[8],
           s_dev);
    return out_obj(a[6], s_dev);
}

static uint32_t dev_showcursor(const uint32_t *a)
{
    uint32_t old = s_cursor;
    s_cursor = a[1];
    return old;
}

static uint32_t dev_present(const uint32_t *a)
{
    (void)a;
    static int once;
    if (!once) {
        once = 1;
        rt_log("IDirect3DDevice9::Present (null device, nothing drawn)");
    }
    return 0;
}

static uint32_t make_tex(int kind, uint32_t w, uint32_t h, uint32_t d, uint32_t levels, uint32_t usage, uint32_t fmt,
                         uint32_t pool, uint32_t pp)
{
    uint32_t o = new_obj(kind);
    if (!o) return 0x8007000Eu;
    uint32_t n = 1, m = w > h ? w : h;
    if (d > m) m = d;
    while (m > 1) {
        m >>= 1;
        n++;
    }
    if (!levels || levels > n) levels = n;
    MEM32(o + O_W) = w;
    MEM32(o + O_H) = h;
    MEM32(o + O_SIZE) = d;
    MEM32(o + O_LEVELS) = levels;
    MEM32(o + O_USAGE) = usage;
    MEM32(o + O_FMT) = fmt;
    MEM32(o + O_POOL) = pool;
    if (pool == 0) {
        uint64_t bytes = 0;
        for (uint32_t l = 0; l < levels; l++)
            bytes += rt_pitch_of(fmt, level_dim(w, l)) * rt_rows_of(fmt, level_dim(h, l)) * level_dim(d, l) *
                     (kind == K_CUBE ? 6u : 1u);
        if (bytes > MAX_BYTES) {
            rt_heap_free(o);
            MEM32(pp) = 0;
            return 0x8007000Eu;
        }
        if (s_vidmem + bytes > VIDMEM_CAP) {
            rt_heap_free(o);
            MEM32(pp) = 0;
            return 0x8876017Cu;
        }
        s_vidmem += bytes;
        MEM32(o + O_VIDMEM) = (uint32_t)bytes;
    }
    MEM32(pp) = o;
    return 0;
}

static uint32_t dev_texture(const uint32_t *a) { return make_tex(K_TEXTURE, a[1], a[2], 1, a[3], a[4], a[5], a[6], a[7]); }
static uint32_t dev_cube(const uint32_t *a) { return make_tex(K_CUBE, a[1], a[1], 1, a[2], a[3], a[4], a[5], a[6]); }

static uint32_t make_buf(int kind, uint32_t len, uint32_t usage, uint32_t fvf_or_fmt, uint32_t pool, uint32_t pp)
{
    if ((uint64_t)len + 16 > MAX_BYTES) {
        MEM32(pp) = 0;
        return 0x8007000Eu;
    }
    uint32_t o = new_obj(kind);
    if (!o) return 0x8007000Eu;
    MEM32(o + O_SIZE) = len;
    MEM32(o + O_USAGE) = usage;
    MEM32(o + (kind == K_VB ? O_FVF : O_FMT)) = fvf_or_fmt;
    MEM32(o + O_POOL) = pool;
    MEM32(pp) = o;
    return 0;
}

static uint32_t dev_vb(const uint32_t *a) { return make_buf(K_VB, a[1], a[2], a[3], a[4], a[5]); }
static uint32_t dev_ib(const uint32_t *a) { return make_buf(K_IB, a[1], a[2], a[3], a[4], a[5]); }

static uint32_t dev_ds(const uint32_t *a) { return new_out(a[7], new_surface(a[1], a[2], a[3], 0, 2, 0, 0)); }

static uint32_t dev_setrt(const uint32_t *a)
{
    if (!a[1]) s_rt = a[2];
    return 0;
}

static uint32_t dev_getrt(const uint32_t *a)
{
    if (!a[1]) return out_obj(a[2], s_rt);
    MEM32(a[2]) = 0;
    return 0x88760866u;
}

static uint32_t dev_setds(const uint32_t *a)
{
    s_ds = a[1];
    return 0;
}

static uint32_t dev_getds(const uint32_t *a)
{
    if (!s_ds) {
        MEM32(a[1]) = 0;
        return 0x88760866u;
    }
    return out_obj(a[1], s_ds);
}

static uint32_t dev_setvp(const uint32_t *a)
{
    memcpy(s_vp, rt_host(a[1]), sizeof s_vp);
    return 0;
}

static uint32_t dev_setrs(const uint32_t *a)
{
    if (a[1] < 256) s_rs[a[1]] = a[2];
    return 0;
}

static uint32_t dev_decl(const uint32_t *a) { return new_out(a[2], new_obj(K_DECL)); }
static uint32_t dev_vs(const uint32_t *a) { return new_out(a[2], new_obj(K_VS)); }
static uint32_t dev_ps(const uint32_t *a) { return new_out(a[2], new_obj(K_PS)); }

static uint32_t dev_query(const uint32_t *a)
{
    if (!a[2]) return 0;
    return new_out(a[2], new_obj(K_QUERY));
}

static const rt_shim k_d3d9[] = {
    {"d3d9.dll", "IDirect3D9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3D9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3D9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3D9::RegisterSoftwareDevice", 2, 0},
    {"d3d9.dll", "IDirect3D9::GetAdapterCount", 1, d_one},
    {"d3d9.dll", "IDirect3D9::GetAdapterIdentifier", 4, d9_ident},
    {"d3d9.dll", "IDirect3D9::GetAdapterModeCount", 3, d9_modecount},
    {"d3d9.dll", "IDirect3D9::EnumAdapterModes", 5, d9_enummodes},
    {"d3d9.dll", "IDirect3D9::GetAdapterDisplayMode", 3, d9_dispmode},
    {"d3d9.dll", "IDirect3D9::CheckDeviceType", 6, 0},
    {"d3d9.dll", "IDirect3D9::CheckDeviceFormat", 7, d9_checkfmt},
    {"d3d9.dll", "IDirect3D9::CheckDeviceMultiSampleType", 7, 0},
    {"d3d9.dll", "IDirect3D9::CheckDepthStencilMatch", 6, d_ok},
    {"d3d9.dll", "IDirect3D9::CheckDeviceFormatConversion", 5, 0},
    {"d3d9.dll", "IDirect3D9::GetDeviceCaps", 4, d9_caps},
    {"d3d9.dll", "IDirect3D9::GetAdapterMonitor", 2, 0},
    {"d3d9.dll", "IDirect3D9::CreateDevice", 7, d9_createdevice},
};
static const rt_shim k_device[] = {
    {"d3d9.dll", "IDirect3DDevice9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DDevice9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DDevice9::TestCooperativeLevel", 1, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetAvailableTextureMem", 1, 0},
    {"d3d9.dll", "IDirect3DDevice9::EvictManagedResources", 1, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetDirect3D", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetDeviceCaps", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetDisplayMode", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetCreationParameters", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetCursorProperties", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetCursorPosition", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::ShowCursor", 2, dev_showcursor},
    {"d3d9.dll", "IDirect3DDevice9::CreateAdditionalSwapChain", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetSwapChain", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetNumberOfSwapChains", 1, 0},
    {"d3d9.dll", "IDirect3DDevice9::Reset", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::Present", 5, dev_present},
    {"d3d9.dll", "IDirect3DDevice9::GetBackBuffer", 5, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetRasterStatus", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetDialogBoxMode", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetGammaRamp", 4, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetGammaRamp", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateTexture", 9, dev_texture},
    {"d3d9.dll", "IDirect3DDevice9::CreateVolumeTexture", 10, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateCubeTexture", 8, dev_cube},
    {"d3d9.dll", "IDirect3DDevice9::CreateVertexBuffer", 7, dev_vb},
    {"d3d9.dll", "IDirect3DDevice9::CreateIndexBuffer", 7, dev_ib},
    {"d3d9.dll", "IDirect3DDevice9::CreateRenderTarget", 9, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateDepthStencilSurface", 9, dev_ds},
    {"d3d9.dll", "IDirect3DDevice9::UpdateSurface", 5, 0},
    {"d3d9.dll", "IDirect3DDevice9::UpdateTexture", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetRenderTargetData", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetFrontBufferData", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::StretchRect", 6, 0},
    {"d3d9.dll", "IDirect3DDevice9::ColorFill", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateOffscreenPlainSurface", 7, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetRenderTarget", 3, dev_setrt},
    {"d3d9.dll", "IDirect3DDevice9::GetRenderTarget", 3, dev_getrt},
    {"d3d9.dll", "IDirect3DDevice9::SetDepthStencilSurface", 2, dev_setds},
    {"d3d9.dll", "IDirect3DDevice9::GetDepthStencilSurface", 2, dev_getds},
    {"d3d9.dll", "IDirect3DDevice9::BeginScene", 1, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::EndScene", 1, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::Clear", 7, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::SetTransform", 3, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetTransform", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::MultiplyTransform", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetViewport", 2, dev_setvp},
    {"d3d9.dll", "IDirect3DDevice9::GetViewport", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetMaterial", 2, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetMaterial", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetLight", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetLight", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::LightEnable", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetLightEnable", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetClipPlane", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetClipPlane", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetRenderState", 3, dev_setrs},
    {"d3d9.dll", "IDirect3DDevice9::GetRenderState", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateStateBlock", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::BeginStateBlock", 1, 0},
    {"d3d9.dll", "IDirect3DDevice9::EndStateBlock", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetClipStatus", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetClipStatus", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetTexture", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetTexture", 3, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetTextureStageState", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetTextureStageState", 4, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetSamplerState", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetSamplerState", 4, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::ValidateDevice", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetPaletteEntries", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetPaletteEntries", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetCurrentTexturePalette", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetCurrentTexturePalette", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetScissorRect", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetScissorRect", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetSoftwareVertexProcessing", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetSoftwareVertexProcessing", 1, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetNPatchMode", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetNPatchMode", 1, 0},
    {"d3d9.dll", "IDirect3DDevice9::DrawPrimitive", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::DrawIndexedPrimitive", 7, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::DrawPrimitiveUP", 5, 0},
    {"d3d9.dll", "IDirect3DDevice9::DrawIndexedPrimitiveUP", 9, 0},
    {"d3d9.dll", "IDirect3DDevice9::ProcessVertices", 7, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateVertexDeclaration", 3, dev_decl},
    {"d3d9.dll", "IDirect3DDevice9::SetVertexDeclaration", 2, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetVertexDeclaration", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetFVF", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetFVF", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateVertexShader", 3, dev_vs},
    {"d3d9.dll", "IDirect3DDevice9::SetVertexShader", 2, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetVertexShader", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetVertexShaderConstantF", 4, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetVertexShaderConstantF", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetVertexShaderConstantI", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetVertexShaderConstantI", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetVertexShaderConstantB", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetVertexShaderConstantB", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetStreamSource", 5, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetStreamSource", 5, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetStreamSourceFreq", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetStreamSourceFreq", 3, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetIndices", 2, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetIndices", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreatePixelShader", 3, dev_ps},
    {"d3d9.dll", "IDirect3DDevice9::SetPixelShader", 2, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetPixelShader", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetPixelShaderConstantF", 4, d_ok},
    {"d3d9.dll", "IDirect3DDevice9::GetPixelShaderConstantF", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetPixelShaderConstantI", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetPixelShaderConstantI", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::SetPixelShaderConstantB", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::GetPixelShaderConstantB", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::DrawRectPatch", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::DrawTriPatch", 4, 0},
    {"d3d9.dll", "IDirect3DDevice9::DeletePatch", 2, 0},
    {"d3d9.dll", "IDirect3DDevice9::CreateQuery", 3, dev_query},
};
static const rt_shim k_texture[] = {
    {"d3d9.dll", "IDirect3DTexture9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DTexture9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DTexture9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DTexture9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DTexture9::SetPrivateData", 5, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetPrivateData", 4, 0},
    {"d3d9.dll", "IDirect3DTexture9::FreePrivateData", 2, 0},
    {"d3d9.dll", "IDirect3DTexture9::SetPriority", 2, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetPriority", 1, 0},
    {"d3d9.dll", "IDirect3DTexture9::PreLoad", 1, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetType", 1, d_gettype},
    {"d3d9.dll", "IDirect3DTexture9::SetLOD", 2, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetLOD", 1, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetLevelCount", 1, t_levels},
    {"d3d9.dll", "IDirect3DTexture9::SetAutoGenFilterType", 2, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetAutoGenFilterType", 1, 0},
    {"d3d9.dll", "IDirect3DTexture9::GenerateMipSubLevels", 1, 0},
    {"d3d9.dll", "IDirect3DTexture9::GetLevelDesc", 3, t_desc},
    {"d3d9.dll", "IDirect3DTexture9::GetSurfaceLevel", 3, t_surface},
    {"d3d9.dll", "IDirect3DTexture9::LockRect", 5, 0},
    {"d3d9.dll", "IDirect3DTexture9::UnlockRect", 2, 0},
    {"d3d9.dll", "IDirect3DTexture9::AddDirtyRect", 2, 0},
};
static const rt_shim k_cube[] = {
    {"d3d9.dll", "IDirect3DCubeTexture9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::SetPrivateData", 5, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetPrivateData", 4, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::FreePrivateData", 2, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::SetPriority", 2, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetPriority", 1, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::PreLoad", 1, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetType", 1, d_gettype},
    {"d3d9.dll", "IDirect3DCubeTexture9::SetLOD", 2, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetLOD", 1, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetLevelCount", 1, t_levels},
    {"d3d9.dll", "IDirect3DCubeTexture9::SetAutoGenFilterType", 2, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetAutoGenFilterType", 1, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GenerateMipSubLevels", 1, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetLevelDesc", 3, t_desc},
    {"d3d9.dll", "IDirect3DCubeTexture9::GetCubeMapSurface", 4, c_surface},
    {"d3d9.dll", "IDirect3DCubeTexture9::LockRect", 6, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::UnlockRect", 3, 0},
    {"d3d9.dll", "IDirect3DCubeTexture9::AddDirtyRect", 3, 0},
};
static const rt_shim k_volume[] = {
    {"d3d9.dll", "IDirect3DVolumeTexture9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::Release", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::SetPrivateData", 5, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetPrivateData", 4, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::FreePrivateData", 2, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::SetPriority", 2, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetPriority", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::PreLoad", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetType", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::SetLOD", 2, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetLOD", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetLevelCount", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::SetAutoGenFilterType", 2, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetAutoGenFilterType", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GenerateMipSubLevels", 1, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetLevelDesc", 3, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::GetVolumeLevel", 3, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::LockBox", 5, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::UnlockBox", 2, 0},
    {"d3d9.dll", "IDirect3DVolumeTexture9::AddDirtyBox", 2, 0},
};
static const rt_shim k_surface[] = {
    {"d3d9.dll", "IDirect3DSurface9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DSurface9::AddRef", 1, d_addref},
    {"d3d9.dll", "IDirect3DSurface9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DSurface9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DSurface9::SetPrivateData", 5, 0},
    {"d3d9.dll", "IDirect3DSurface9::GetPrivateData", 4, 0},
    {"d3d9.dll", "IDirect3DSurface9::FreePrivateData", 2, 0},
    {"d3d9.dll", "IDirect3DSurface9::SetPriority", 2, 0},
    {"d3d9.dll", "IDirect3DSurface9::GetPriority", 1, 0},
    {"d3d9.dll", "IDirect3DSurface9::PreLoad", 1, 0},
    {"d3d9.dll", "IDirect3DSurface9::GetType", 1, 0},
    {"d3d9.dll", "IDirect3DSurface9::GetContainer", 3, 0},
    {"d3d9.dll", "IDirect3DSurface9::GetDesc", 2, 0},
    {"d3d9.dll", "IDirect3DSurface9::LockRect", 4, s_lock},
    {"d3d9.dll", "IDirect3DSurface9::UnlockRect", 1, d_ok},
    {"d3d9.dll", "IDirect3DSurface9::GetDC", 2, 0},
    {"d3d9.dll", "IDirect3DSurface9::ReleaseDC", 2, 0},
};
static const rt_shim k_vb[] = {
    {"d3d9.dll", "IDirect3DVertexBuffer9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DVertexBuffer9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::SetPrivateData", 5, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::GetPrivateData", 4, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::FreePrivateData", 2, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::SetPriority", 2, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::GetPriority", 1, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::PreLoad", 1, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::GetType", 1, 0},
    {"d3d9.dll", "IDirect3DVertexBuffer9::Lock", 5, b_lock},
    {"d3d9.dll", "IDirect3DVertexBuffer9::Unlock", 1, d_ok},
    {"d3d9.dll", "IDirect3DVertexBuffer9::GetDesc", 2, 0},
};
static const rt_shim k_ib[] = {
    {"d3d9.dll", "IDirect3DIndexBuffer9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DIndexBuffer9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::SetPrivateData", 5, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::GetPrivateData", 4, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::FreePrivateData", 2, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::SetPriority", 2, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::GetPriority", 1, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::PreLoad", 1, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::GetType", 1, 0},
    {"d3d9.dll", "IDirect3DIndexBuffer9::Lock", 5, b_lock},
    {"d3d9.dll", "IDirect3DIndexBuffer9::Unlock", 1, d_ok},
    {"d3d9.dll", "IDirect3DIndexBuffer9::GetDesc", 2, b_desc},
};
static const rt_shim k_swapchain[] = {
    {"d3d9.dll", "IDirect3DSwapChain9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::Release", 1, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::Present", 6, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::GetFrontBufferData", 2, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::GetBackBuffer", 4, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::GetRasterStatus", 2, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::GetDisplayMode", 2, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DSwapChain9::GetPresentParameters", 2, 0},
};
static const rt_shim k_stateblock[] = {
    {"d3d9.dll", "IDirect3DStateBlock9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DStateBlock9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DStateBlock9::Release", 1, 0},
    {"d3d9.dll", "IDirect3DStateBlock9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DStateBlock9::Capture", 1, 0},
    {"d3d9.dll", "IDirect3DStateBlock9::Apply", 1, 0},
};
static const rt_shim k_decl[] = {
    {"d3d9.dll", "IDirect3DVertexDeclaration9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DVertexDeclaration9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DVertexDeclaration9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DVertexDeclaration9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DVertexDeclaration9::GetDeclaration", 3, 0},
};
static const rt_shim k_vs[] = {
    {"d3d9.dll", "IDirect3DVertexShader9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DVertexShader9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DVertexShader9::Release", 1, 0},
    {"d3d9.dll", "IDirect3DVertexShader9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DVertexShader9::GetFunction", 3, 0},
};
static const rt_shim k_ps[] = {
    {"d3d9.dll", "IDirect3DPixelShader9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DPixelShader9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DPixelShader9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DPixelShader9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DPixelShader9::GetFunction", 3, 0},
};
static const rt_shim k_query[] = {
    {"d3d9.dll", "IDirect3DQuery9::QueryInterface", 3, 0},
    {"d3d9.dll", "IDirect3DQuery9::AddRef", 1, 0},
    {"d3d9.dll", "IDirect3DQuery9::Release", 1, d_release},
    {"d3d9.dll", "IDirect3DQuery9::GetDevice", 2, 0},
    {"d3d9.dll", "IDirect3DQuery9::GetType", 1, 0},
    {"d3d9.dll", "IDirect3DQuery9::GetDataSize", 1, 0},
    {"d3d9.dll", "IDirect3DQuery9::Issue", 2, d_ok},
    {"d3d9.dll", "IDirect3DQuery9::GetData", 4, 0},
};

const rt_d3d9_table rt_d3d9_tables[K_N] = {
    [K_D3D] = {k_d3d9, sizeof k_d3d9 / sizeof k_d3d9[0]},
    [K_DEVICE] = {k_device, sizeof k_device / sizeof k_device[0]},
    [K_SURFACE] = {k_surface, sizeof k_surface / sizeof k_surface[0]},
    [K_TEXTURE] = {k_texture, sizeof k_texture / sizeof k_texture[0]},
    [K_VOLTEX] = {k_volume, sizeof k_volume / sizeof k_volume[0]},
    [K_CUBE] = {k_cube, sizeof k_cube / sizeof k_cube[0]},
    [K_VB] = {k_vb, sizeof k_vb / sizeof k_vb[0]},
    [K_IB] = {k_ib, sizeof k_ib / sizeof k_ib[0]},
    [K_SWAP] = {k_swapchain, sizeof k_swapchain / sizeof k_swapchain[0]},
    [K_SB] = {k_stateblock, sizeof k_stateblock / sizeof k_stateblock[0]},
    [K_DECL] = {k_decl, sizeof k_decl / sizeof k_decl[0]},
    [K_VS] = {k_vs, sizeof k_vs / sizeof k_vs[0]},
    [K_PS] = {k_ps, sizeof k_ps / sizeof k_ps[0]},
    [K_QUERY] = {k_query, sizeof k_query / sizeof k_query[0]},
};

static uint32_t Direct3DCreate9(const uint32_t *a)
{
    if (!s_d3d) {
        for (int k = 0; k < K_N; k++) {
            if (!rt_d3d9_tables[k].rows) continue;
            uint32_t v = rt_heap_alloc(4 * rt_d3d9_tables[k].n, 1);
            for (uint32_t i = 0; i < rt_d3d9_tables[k].n; i++)
                MEM32(v + 4 * i) = rt_dynamic_shim("d3d9.dll", rt_d3d9_tables[k].rows[i].name, &rt_d3d9_tables[k].rows[i]);
            s_vtbl[k] = v;
        }
        s_d3d = new_obj(K_D3D);
    }
    rt_log("Direct3DCreate9(%u) -> null IDirect3D9 0x%08X", a[0], s_d3d);
    return s_d3d;
}

const rt_shim rt_d3d9_null_shims[] = {
    {"d3d9.dll", "Direct3DCreate9", 1, Direct3DCreate9},
};

const uint32_t rt_nd3d9_null_shims = sizeof rt_d3d9_null_shims / sizeof rt_d3d9_null_shims[0];
