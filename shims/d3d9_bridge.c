#include "recomp_types.h"
#include "rt.h"
#include "fx.h"
#include "map.h"
#include "toggles.h"

#include <SDL3/SDL.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <math.h>
#include <pthread.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define D3DERR_INVALIDCALL 0x8876086Cu
#define D3DERR_DRIVERINTERNALERROR 0x88760827u
#define E_OUTOFMEMORY 0x8007000Eu
#define D3DLOCK_READONLY 0x10u
#define D3DLOCK_DISCARD 0x2000u
#define D3DLOCK_NOOVERWRITE 0x1000u
#define D3DPOOL_DEFAULT 0u
#define D3DCREATE_SOFTWARE_VERTEXPROCESSING 0x20u
#define D3DPOOL_SYSTEMMEM 2u
#define D3DBACKBUFFER_TYPE_MONO 0u
#define D3DFMT_A8R8G8B8 21u
#define D3DFMT_X8R8G8B8 22u
#define D3DQUERYTYPE_EVENT 8u
#define D3DISSUE_END 1u
#define D3DGETDATA_FLUSH 1u
#define D3DPRESENT_INTERVAL_ONE 1u
#define S_FALSE 1
void rt_input_lat_present(void);
#define DXVK_CONFIG_VALUE "d3d9.memoryTrackTest = True; d3d9.maxAvailableMemory = 1024; d3d9.deferSurfaceCreation = True; d3d9.maxFrameLatency = 1"
#define ICD_PATH "/opt/homebrew/opt/mesa/share/vulkan/icd.d/kosmickrisp_mesa_icd.aarch64.json"
#define ICD_FILE "kosmickrisp_mesa_icd.aarch64.json"
#define LOADER_PATH "/opt/homebrew/opt/vulkan-loader/lib/libvulkan.1.dylib"

typedef int32_t HR;

typedef struct {
    uint32_t BackBufferWidth, BackBufferHeight, BackBufferFormat, BackBufferCount, MultiSampleType, MultiSampleQuality,
        SwapEffect;
    uint32_t hDeviceWindow;
    int32_t Windowed, EnableAutoDepthStencil;
    uint32_t AutoDepthStencilFormat, Flags, FullScreen_RefreshRateInHz, PresentationInterval;
} g_present_params;

typedef struct {
    uint32_t BackBufferWidth, BackBufferHeight, BackBufferFormat, BackBufferCount, MultiSampleType, MultiSampleQuality,
        SwapEffect;
    void *hDeviceWindow;
    int32_t Windowed, EnableAutoDepthStencil;
    uint32_t AutoDepthStencilFormat, Flags, FullScreen_RefreshRateInHz, PresentationInterval;
} h_present_params;

_Static_assert(sizeof(g_present_params) == 56, "guest D3DPRESENT_PARAMETERS is 56 bytes");
_Static_assert(offsetof(g_present_params, SwapEffect) == 24, "guest SwapEffect at 24");
_Static_assert(offsetof(g_present_params, hDeviceWindow) == 28, "guest hDeviceWindow at 28");
_Static_assert(offsetof(g_present_params, Windowed) == 32, "guest Windowed at 32");
_Static_assert(offsetof(g_present_params, PresentationInterval) == 52, "guest PresentationInterval at 52");
_Static_assert(sizeof(h_present_params) == 64, "host D3DPRESENT_PARAMETERS is 64 bytes");
_Static_assert(offsetof(h_present_params, hDeviceWindow) == 32, "host hDeviceWindow at 32");
_Static_assert(offsetof(h_present_params, Windowed) == 40, "host Windowed at 40");
_Static_assert(offsetof(h_present_params, PresentationInterval) == 60, "host PresentationInterval at 60");

typedef struct {
    int32_t Pitch;
    uint32_t pBits;
} g_locked_rect;

typedef struct {
    int32_t Pitch;
    void *pBits;
} h_locked_rect;

_Static_assert(sizeof(g_locked_rect) == 8 && offsetof(g_locked_rect, pBits) == 4, "guest D3DLOCKED_RECT is 8 bytes");
_Static_assert(sizeof(h_locked_rect) == 16 && offsetof(h_locked_rect, pBits) == 8, "host D3DLOCKED_RECT is 16 bytes");

typedef struct __attribute__((packed, aligned(4))) {
    char Driver[512], Description[512], DeviceName[32];
    int64_t DriverVersion;
    uint32_t VendorId, DeviceId, SubSysId, Revision;
    uint8_t DeviceIdentifier[16];
    uint32_t WHQLLevel;
} g_adapter_id;

typedef struct {
    char Driver[512], Description[512], DeviceName[32];
    int64_t DriverVersion;
    uint32_t VendorId, DeviceId, SubSysId, Revision;
    uint8_t DeviceIdentifier[16];
    uint32_t WHQLLevel;
} h_adapter_id;

_Static_assert(sizeof(g_adapter_id) == 1100, "guest D3DADAPTER_IDENTIFIER9 is 1100 bytes");
_Static_assert(sizeof(h_adapter_id) == 1104, "host D3DADAPTER_IDENTIFIER9 is 1104 bytes");
_Static_assert(offsetof(g_adapter_id, DriverVersion) == 1056 && offsetof(h_adapter_id, DriverVersion) == 1056,
               "DriverVersion at 1056");
_Static_assert(offsetof(g_adapter_id, VendorId) == 1064 && offsetof(h_adapter_id, VendorId) == 1064, "VendorId at 1064");
_Static_assert(offsetof(g_adapter_id, DeviceIdentifier) == 1080 && offsetof(h_adapter_id, DeviceIdentifier) == 1080,
               "DeviceIdentifier at 1080");
_Static_assert(offsetof(g_adapter_id, WHQLLevel) == 1096 && offsetof(h_adapter_id, WHQLLevel) == 1096,
               "WHQLLevel at 1096");

typedef struct {
    uint32_t DeviceType, AdapterOrdinal, Caps, Caps2, Caps3, PresentationIntervals, CursorCaps, DevCaps;
    uint32_t rest[68];
} d3d_caps9;

_Static_assert(sizeof(d3d_caps9) == 304 && offsetof(d3d_caps9, DevCaps) == 28, "D3DCAPS9 is 304 bytes, DevCaps at 28");

typedef struct {
    uint32_t Format, Type, Usage, Pool, MultiSampleType, MultiSampleQuality, Width, Height;
} d3d_surface_desc;

_Static_assert(sizeof(d3d_surface_desc) == 32, "D3DSURFACE_DESC is 32 bytes");

void rt_gfx_pp_in(uint32_t g, void *h, void *hwnd)
{
    const g_present_params *s = rt_host(g);
    h_present_params *d = h;
    d->BackBufferWidth = s->BackBufferWidth;
    d->BackBufferHeight = s->BackBufferHeight;
    d->BackBufferFormat = s->BackBufferFormat;
    d->BackBufferCount = s->BackBufferCount;
    d->MultiSampleType = s->MultiSampleType;
    d->MultiSampleQuality = s->MultiSampleQuality;
    d->SwapEffect = s->SwapEffect;
    d->hDeviceWindow = hwnd;
    d->Windowed = s->Windowed;
    d->EnableAutoDepthStencil = s->EnableAutoDepthStencil;
    d->AutoDepthStencilFormat = s->AutoDepthStencilFormat;
    d->Flags = s->Flags;
    d->FullScreen_RefreshRateInHz = s->FullScreen_RefreshRateInHz;
    d->PresentationInterval = s->PresentationInterval;
}

void rt_gfx_pp_size(int windowed, int gw, int gh, uint32_t *bw, uint32_t *bh)
{
    if (!windowed) return;
    if (!*bw && gw > 0) *bw = (uint32_t)gw;
    if (!*bh && gh > 0) *bh = (uint32_t)gh;
}

void rt_gfx_pp_out(const void *h, uint32_t g, uint32_t hwnd)
{
    const h_present_params *s = h;
    g_present_params *d = rt_host(g);
    d->BackBufferWidth = s->BackBufferWidth;
    d->BackBufferHeight = s->BackBufferHeight;
    d->BackBufferFormat = s->BackBufferFormat;
    d->BackBufferCount = s->BackBufferCount;
    d->MultiSampleType = s->MultiSampleType;
    d->MultiSampleQuality = s->MultiSampleQuality;
    d->SwapEffect = s->SwapEffect;
    d->hDeviceWindow = hwnd;
    d->Windowed = s->Windowed;
    d->EnableAutoDepthStencil = s->EnableAutoDepthStencil;
    d->AutoDepthStencilFormat = s->AutoDepthStencilFormat;
    d->Flags = s->Flags;
    d->FullScreen_RefreshRateInHz = s->FullScreen_RefreshRateInHz;
    d->PresentationInterval = s->PresentationInterval;
}

void rt_gfx_lr_out(const void *h, uint32_t g, uint32_t bits)
{
    const h_locked_rect *s = h;
    g_locked_rect *d = rt_host(g);
    d->Pitch = s->Pitch;
    d->pBits = bits;
}

enum {
    D_RELEASE, D_ADAPTERCOUNT, D_ADAPTERID, D_MODECOUNT, D_ENUMMODES, D_DISPMODE, D_CHECKFMT, D_CHECKDS, D_CAPS,
    D_CREATEDEVICE,
    V_RELEASE, V_TESTCOOP, V_EVICT, V_SHOWCURSOR, V_PRESENT, V_GAMMA, V_CREATETEX, V_CREATECUBE, V_CREATEVB, V_CREATEIB,
    V_CREATEDS, V_SETRT, V_GETRT, V_SETDS, V_GETDS, V_BEGIN, V_END, V_CLEAR, V_TRANSFORM, V_VIEWPORT, V_MATERIAL,
    V_RS, V_SETTEX, V_TSS, V_SS, V_DIP, V_CREATEDECL, V_SETDECL, V_CREATEVS, V_SETVS, V_VSCONST, V_STREAM, V_INDICES,
    V_CREATEPS, V_SETPS, V_PSCONST, V_CREATEQUERY,
    T_RELEASE, T_TYPE, T_LEVELS, T_DESC, T_SURFACE,
    C_RELEASE, C_TYPE, C_LEVELS, C_DESC, C_SURFACE,
    S_ADDREF, S_RELEASE, S_LOCK, S_UNLOCK,
    B_RELEASE, B_LOCK, B_UNLOCK,
    I_RELEASE, I_LOCK, I_UNLOCK, I_DESC,
    DECL_RELEASE, PS_RELEASE, Q_RELEASE, Q_ISSUE,
    D_MSTYPE,
    V_CAPS, V_GETBB, V_CREATERT, V_OFFSCREEN, V_RTDATA, V_STRETCH, V_LIGHT, V_LIGHTENABLE, V_CLIP, V_BEGINSB, V_ENDSB,
    V_DP,
    T_ADDREF, T_DEVICE, T_SETPD, T_GETPD, T_FREEPD, T_SETPRI, T_GETPRI, T_PRELOAD, T_SETLOD, T_GETLOD, T_SETFILTER,
    T_GETFILTER, T_GENMIPS, T_LOCKRECT, T_UNLOCKRECT, T_DIRTY,
    C_ADDREF, C_DEVICE, C_SETPD, C_GETPD, C_FREEPD, C_SETPRI, C_GETPRI, C_PRELOAD, C_SETLOD, C_GETLOD, C_SETFILTER,
    C_GETFILTER, C_GENMIPS, C_LOCKRECT, C_UNLOCKRECT, C_DIRTY,
    S_DEVICE, S_SETPD, S_GETPD, S_FREEPD, S_SETPRI, S_GETPRI, S_PRELOAD, S_TYPE, S_DESC,
    B_ADDREF, B_DEVICE, B_SETPD, B_GETPD, B_FREEPD, B_SETPRI, B_GETPRI, B_PRELOAD, B_TYPE, B_DESC,
    I_ADDREF, I_DEVICE, I_SETPD, I_GETPD, I_FREEPD, I_SETPRI, I_GETPRI, I_PRELOAD, I_TYPE,
    Q_ADDREF, Q_DEVICE, Q_TYPE, Q_SIZE, Q_GETDATA,
    DECL_ADDREF, DECL_DEVICE, DECL_GET,
    VS_RELEASE, VS_ADDREF, VS_DEVICE, VS_FUNC,
    PS_ADDREF, PS_DEVICE, PS_FUNC,
    SB_RELEASE, SB_ADDREF, SB_DEVICE, SB_CAPTURE, SB_APPLY,
    V_RESET,
    M_REACHED,
    M_N = M_REACHED
};

typedef struct {
    uint32_t bounce, size, cap, caller, rows, rowbytes, flags, own, sub;
    int32_t pitch;
    uint8_t *host;
} lockrec;

typedef struct {
    void *host;
    uint32_t proxy, owner, kind, length, pool, nlock;
    lockrec lk[8];
} ent;

static int s_mode;
static void *s_lib;
static void *(*s_create9)(uint32_t);
static char s_overlay[PATH_MAX];
static uint32_t s_vtbl[K_N];
static int s_slot[M_N];
static int s_built;
static void *s_dev_window;
static void *s_dev_host;
static ent *s_e;
static uint32_t s_ne, s_cape, s_live, *s_freeidx, s_nfreeidx;
static uint32_t *s_h, s_hcap, s_hused;
static uint64_t s_locks, s_oob, s_late;

enum { T_COPY, T_LOCK, T_DRAW, T_PRESENT, T_N };

static uint64_t s_t[T_N], s_copy_bytes;

enum { B_N = 7 };

static uint64_t s_bprev[B_N], s_bsum[2][B_N], s_btotal[2], s_bn[2];

static uint64_t tic(void) { return rt_now_ns(); }

static void toc(int k, uint64_t t0)
{
    if (t0) s_t[k] += rt_now_ns() - t0;
}

static uint64_t s_gap_prev[6];

void rt_gfx_gap(uint64_t gap_ns, int describe, char *where, size_t wn, char *detail, size_t dn)
{
    uint64_t cur[6] = {s_t[T_PRESENT], s_t[T_DRAW], s_t[T_LOCK] + s_t[T_COPY], rt_sleep_ns(), rt_lock_wait_ns(),
                       clock_gettime_nsec_np(CLOCK_THREAD_CPUTIME_ID)};
    uint64_t d[6];
    for (int k = 0; k < 6; k++) {
        d[k] = cur[k] - s_gap_prev[k];
        s_gap_prev[k] = cur[k];
    }
    if (!describe) return;
    static const char *const k_where[4] = {"present", "d3d9 draw", "d3d9 Lock/Unlock", "sleep"};
    int best = 0;
    for (int k = 1; k < 4; k++)
        if (d[k] > d[best]) best = k;
    snprintf(where, wn, "%s", 2 * d[best] > gap_ns ? k_where[best] : "guest");
    snprintf(detail, dn, "present %.1f draws %.1f locks %.1f sleep %.1f lock-wait %.1f thread-cpu %.1f wall %.1f ms",
             (double)d[0] / 1e6, (double)d[1] / 1e6, (double)d[2] / 1e6, (double)d[3] / 1e6, (double)d[4] / 1e6,
             (double)d[5] / 1e6, (double)gap_ns / 1e6);
}
typedef struct {
    uint64_t end, total, part[B_N], cpu;
} ftrec;

#define FTR_CAP 65536
static ftrec *s_ftr;
static uint32_t s_ftr_n;
static int s_ftr_on = -1;
static uint64_t s_ftr_prev[B_N], s_ftr_cpu;

static void ftr_note(uint64_t now, uint64_t total, const uint64_t *cur)
{
    if (s_ftr_on < 0) {
        s_ftr_on = getenv("RT_FRAME_TRACE") != NULL;
        if (s_ftr_on) s_ftr = calloc(FTR_CAP, sizeof *s_ftr);
        if (!s_ftr) s_ftr_on = 0;
        memcpy(s_ftr_prev, cur, sizeof s_ftr_prev);
        s_ftr_cpu = clock_gettime_nsec_np(CLOCK_THREAD_CPUTIME_ID);
        return;
    }
    if (!s_ftr_on || s_ftr_n >= FTR_CAP) return;
    uint64_t cpu = clock_gettime_nsec_np(CLOCK_THREAD_CPUTIME_ID);
    ftrec *r = &s_ftr[s_ftr_n++];
    r->end = now;
    r->total = total;
    for (int k = 0; k < B_N; k++) {
        r->part[k] = cur[k] - s_ftr_prev[k];
        s_ftr_prev[k] = cur[k];
    }
    r->cpu = cpu - s_ftr_cpu;
    s_ftr_cpu = cpu;
}

static void ftr_dump(void)
{
    const char *p = getenv("RT_FRAME_TRACE");
    if (!p || !s_ftr) return;
    FILE *f = fopen(p, "w");
    if (!f) return;
    fprintf(f, "end_s total copies device-lock draws present sleep lock-wait pump thread-cpu (ms)\n");
    for (uint32_t i = 0; i < s_ftr_n; i++) {
        const ftrec *r = &s_ftr[i];
        fprintf(f, "%.6f %.3f", (double)r->end / 1e9, (double)r->total / 1e6);
        for (int k = 0; k < B_N; k++) fprintf(f, " %.3f", (double)r->part[k] / 1e6);
        fprintf(f, " %.3f\n", (double)r->cpu / 1e6);
    }
    fclose(f);
    rt_log("gfx frame trace: %u frames written to %s", s_ftr_n, p);
}

static uint64_t s_lost_since, s_lost_ns;
static uint32_t s_shaders[6];
static uint64_t *s_frames;
static uint32_t s_nframes, s_capframes;
static uint64_t s_last_present, s_fs_seen;
static int s_presented, s_menu_pending, s_check = -1, s_counting;
static double s_capture_s;
static int s_capture_done;
static pthread_mutex_t s_pr_mu = PTHREAD_MUTEX_INITIALIZER;
static struct {
    int have, src, dst, windowed;
    uint32_t bw, bh;
    int32_t s[4], d[4];
    void *win;
} s_pr;
static char s_cap_name[64];

#define VT(h, id, T) ((T)((*(void ***)(h))[s_slot[id]]))

static void main_only(const char *what)
{
    if (!SDL_IsMainThread()) {
        rt_thread *t = rt_self();
        rt_stop(5, 0, "d3d9 call %s from a thread other than the main thread (guest thread 0x%X)", what, t ? t->tid : 0);
    }
}

static uint32_t hash_of(void *p)
{
    uint64_t x = (uint64_t)(uintptr_t)p;
    x ^= x >> 33;
    x *= 0xFF51AFD7ED558CCDull;
    x ^= x >> 33;
    return (uint32_t)x;
}

static void hash_put_raw(void *p, uint32_t idx)
{
    for (uint32_t i = hash_of(p) & (s_hcap - 1);; i = (i + 1) & (s_hcap - 1))
        if (!s_h[i] || s_h[i] == UINT32_MAX) {
            if (!s_h[i]) s_hused++;
            s_h[i] = idx + 1;
            return;
        }
}

static void hash_rebuild(void)
{
    uint32_t cap = 1024;
    while (cap < 4 * s_live + 4) cap *= 2;
    free(s_h);
    s_h = calloc(cap, sizeof *s_h);
    if (!s_h) rt_fatal(5, "out of host memory");
    s_hcap = cap;
    s_hused = 0;
    for (uint32_t i = 0; i < s_ne; i++)
        if (s_e[i].host) hash_put_raw(s_e[i].host, i);
}

static uint32_t *hash_slot(void *p)
{
    if (!s_hcap) return NULL;
    for (uint32_t i = hash_of(p) & (s_hcap - 1);; i = (i + 1) & (s_hcap - 1)) {
        if (!s_h[i]) return NULL;
        if (s_h[i] != UINT32_MAX && s_e[s_h[i] - 1].host == p) return &s_h[i];
    }
}

static void hash_put(void *p, uint32_t idx)
{
    if (2 * (s_hused + 1) > s_hcap) {
        hash_rebuild();
        if (hash_slot(p)) return;
    }
    hash_put_raw(p, idx);
}

uint32_t rt_gfx_live(void) { return s_live; }

static uint32_t wrap(void *host, int kind, uint32_t owner)
{
    if (!host) return 0;
    uint32_t *f = hash_slot(host);
    if (f) return s_e[*f - 1].proxy;
    uint32_t p = rt_heap_alloc(16, 1);
    if (!p) rt_stop(5, 0, "d3d9 bridge: no guest memory for a proxy");
    uint32_t idx;
    if (s_nfreeidx) idx = s_freeidx[--s_nfreeidx];
    else {
        if (s_ne == s_cape) {
            s_cape = s_cape ? 2 * s_cape : 1024;
            s_e = realloc(s_e, s_cape * sizeof *s_e);
            s_freeidx = realloc(s_freeidx, s_cape * sizeof *s_freeidx);
            if (!s_e || !s_freeidx) rt_fatal(5, "out of host memory");
        }
        idx = s_ne++;
    }
    ent *e = &s_e[idx];
    memset(e, 0, sizeof *e);
    e->host = host;
    e->proxy = p;
    e->owner = owner ? owner : p;
    e->kind = (uint32_t)kind;
    MEM32(p) = s_vtbl[kind];
    MEM32(p + 4) = (uint32_t)kind;
    MEM32(p + 8) = e->owner;
    MEM32(p + 12) = idx;
    s_live++;
    hash_put(host, idx);
    return p;
}

void *rt_gfx_host(uint32_t proxy)
{
    if (!proxy || !rt_span_ok(proxy, 16)) return NULL;
    uint32_t i = MEM32(proxy + 12);
    return i < s_ne && s_e[i].proxy == proxy ? s_e[i].host : NULL;
}

static ent *ref(uint32_t va, uint32_t k1, uint32_t k2)
{
    if (!va || !rt_span_ok(va, 16)) return NULL;
    uint32_t i = MEM32(va + 12);
    if (i >= s_ne) return NULL;
    ent *e = &s_e[i];
    if (!e->host || e->proxy != va || (e->kind != k1 && e->kind != k2)) return NULL;
    if (MEM32(va) != s_vtbl[e->kind] || MEM32(va + 4) != e->kind) return NULL;
    return e;
}

typedef struct {
    uint32_t kind;
    const char *name;
    rt_shim_fn fn;
} method;

static const method k_m[M_N];

static uint32_t bad(int id, const char *what, uint32_t v)
{
    static uint32_t n;
    if (n++ < 32) rt_log("gfx %s: bad %s 0x%08X -> D3DERR_INVALIDCALL", k_m[id].name, what, v);
    return D3DERR_INVALIDCALL;
}

static ent *self(const uint32_t *a, int id)
{
    main_only(k_m[id].name);
    ent *e = ref(a[0], k_m[id].kind, k_m[id].kind);
    if (!e) bad(id, "this", a[0]);
    return e;
}

static int obj_arg(int id, uint32_t va, uint32_t k1, uint32_t k2, void **out)
{
    *out = NULL;
    if (!va) return 1;
    ent *e = ref(va, k1, k2);
    if (!e) {
        bad(id, "object", va);
        return 0;
    }
    *out = e->host;
    return 1;
}

static int span(int id, uint32_t va, uint64_t n, int null_ok)
{
    if (!va) return null_ok;
    if (rt_span_ok(va, n)) return 1;
    bad(id, "pointer", va);
    return 0;
}

static void drop(uint32_t i);

static void free_group(uint32_t owner)
{
    for (uint32_t i = 0; i < s_ne; i++)
        if (s_e[i].host && s_e[i].owner == owner) drop(i);
}

#define REGION (64u << 20)
#define NCLASS 17

static uint32_t s_region, s_bump, *s_fl[NCLASS], s_nfl[NCLASS], s_capfl[NCLASS];

static int class_of(uint32_t n)
{
    int c = 0;
    while ((256u << c) < n) c++;
    return c;
}

#define GUARD_PAGE 16384u
#define LOCK_RING 4096u
#define OVER_ROOM 65536u
#define LOCK_SLACK 2048u

static uint32_t s_over_n, s_over_max, s_over_lines, s_nest_n, s_nest_lines;

typedef struct {
    uint32_t va, size, flags, kind, length;
} lock_note;

static lock_note s_ring[LOCK_RING];
static uint32_t s_ring_next;

static void guard_block(uint32_t va, uint32_t sz, int prot)
{
    uintptr_t p = (uintptr_t)rt_host(va), lo = p & ~(uintptr_t)(GUARD_PAGE - 1);
    size_t len = (p + (sz < GUARD_PAGE ? GUARD_PAGE : sz) - lo + GUARD_PAGE - 1) & ~(size_t)(GUARD_PAGE - 1);
    if (mprotect((void *)lo, len, prot)) rt_log("gfx guard failed for 0x%08X", va);
}

static void late_line(uint32_t va, uint32_t at, uint32_t sz)
{
    const uint8_t *p = rt_host(va);
    lock_note n = {0};
    for (uint32_t i = 0; i < LOCK_RING && i < s_ring_next; i++) {
        lock_note *c = &s_ring[(s_ring_next - 1 - i) % LOCK_RING];
        if (c->va == va) {
            n = *c;
            break;
        }
    }
    uint32_t changed = 0;
    for (uint32_t k = 0; k < sz; k++) changed += p[k] != 0xA5;
    char hex[3 * 48 + 1];
    for (uint32_t k = 0; k < 48 && at + k < sz; k++) snprintf(hex + 3 * k, 4, "%02X ", p[at + k]);
    rt_log("gfx late write in bounce block 0x%08X at +0x%X, %u bytes, last lock kind %u length %u size %u flags 0x%X: %s", va, at,
           changed, n.kind, n.length, n.size, n.flags, hex);
}

static uint32_t bounce_get(uint32_t n, uint32_t *own)
{
    *own = 0;
    int c = class_of(n);
    if (c >= NCLASS) {
        *own = 1;
        return rt_pages_reserve(n);
    }
    uint32_t sz = 256u << c;
    if (s_nfl[c]) {
        uint32_t va = s_fl[c][--s_nfl[c]];
        if (s_check > 1) guard_block(va, sz, PROT_READ | PROT_WRITE);
        if (s_check > 0) {
            const uint8_t *p = rt_host(va);
            for (uint32_t k = 0; k < sz; k++)
                if (p[k] != 0xA5) {
                    if (s_late++ < 16) late_line(va, k, sz);
                    break;
                }
        }
        return va;
    }
    if (!s_region) s_region = rt_pages_reserve(REGION);
    if (s_check > 1) s_bump = (s_bump + GUARD_PAGE - 1) & ~(GUARD_PAGE - 1);
    uint32_t take = s_check > 1 && sz < GUARD_PAGE ? GUARD_PAGE : sz;
    if (s_region && s_bump + take <= REGION) {
        uint32_t va = s_region + s_bump;
        s_bump += take;
        return va;
    }
    *own = 1;
    return rt_pages_reserve(n);
}

static void bounce_put(uint32_t va, uint32_t n, uint32_t own)
{
    if (own) {
        rt_pages_release(va);
        return;
    }
    int c = class_of(n);
    if (s_check > 0) memset(rt_host(va), 0xA5, 256u << c);
    if (s_check > 1) guard_block(va, 256u << c, PROT_READ);
    if (s_nfl[c] == s_capfl[c]) {
        s_capfl[c] = s_capfl[c] ? 2 * s_capfl[c] : 64;
        s_fl[c] = realloc(s_fl[c], s_capfl[c] * sizeof *s_fl[c]);
        if (!s_fl[c]) rt_fatal(5, "out of host memory");
    }
    s_fl[c][s_nfl[c]++] = va;
}

static void drop(uint32_t i)
{
    ent *e = &s_e[i];
    for (uint32_t k = 0; k < e->nlock; k++) bounce_put(e->lk[k].bounce, e->lk[k].cap, e->lk[k].own);
    uint32_t *h = hash_slot(e->host);
    if (h) *h = UINT32_MAX;
    MEM32(e->proxy + 4) = K_N;
    rt_heap_free(e->proxy);
    e->host = NULL;
    s_freeidx[s_nfreeidx++] = i;
    s_live--;
}

static uint32_t lock_begin(ent *e, int id, uint8_t *host, uint32_t rows, uint32_t rowbytes, int32_t pitch, uint32_t flags,
                           int discard, uint32_t sub)
{
    lockrec *r = &e->lk[e->nlock];
    r->sub = sub;
    uint64_t size = rows ? (uint64_t)pitch * (rows - 1) + rowbytes : 0;
    if (size > 0x40000000ull) rt_stop(5, 0, "%s: lock of %llu bytes", k_m[id].name, (unsigned long long)size);
    r->size = (uint32_t)size;
    r->cap = s_check > 0 ? r->size + OVER_ROOM : r->size;
    r->bounce = bounce_get(r->cap, &r->own);
    if (!r->bounce) return 0;
    if (r->cap > r->size) {
        memset((uint8_t *)rt_host(r->bounce) + r->size, 0xA5, r->cap - r->size);
        r->caller = MEM32(g_esp);
    }
    r->rows = rows;
    r->rowbytes = rowbytes;
    r->pitch = pitch;
    r->flags = flags;
    r->host = host;
    s_locks++;
    if (!rt_span_ok(r->bounce, r->size)) s_oob++;
    uint64_t t0 = tic();
    if (!discard) {
        for (uint32_t k = 0; k < rows; k++)
            memcpy((uint8_t *)rt_host(r->bounce) + (size_t)k * (size_t)pitch, host + (size_t)k * (size_t)pitch, rowbytes);
        if (s_counting) s_copy_bytes += (uint64_t)rows * rowbytes;
    }
    toc(T_COPY, t0);
    e->nlock++;
    return r->bounce;
}

static void lock_end(ent *e, uint32_t i)
{
    lockrec *r = &e->lk[i];
    uint64_t t0 = tic();
    if (!(r->flags & D3DLOCK_READONLY)) {
        for (uint32_t k = 0; k < r->rows; k++)
            memcpy(r->host + (size_t)k * (size_t)r->pitch, (uint8_t *)rt_host(r->bounce) + (size_t)k * (size_t)r->pitch,
                   r->rowbytes);
        if (s_counting) s_copy_bytes += (uint64_t)r->rows * r->rowbytes;
    }
    toc(T_COPY, t0);
    if (s_check > 0) {
        s_ring[s_ring_next++ % LOCK_RING] = (lock_note){r->bounce, r->size, r->flags, e->kind, e->length};
        const uint8_t *b = rt_host(r->bounce);
        uint32_t over = 0;
        for (uint32_t k = r->cap; k > r->size; k--)
            if (b[k - 1] != 0xA5) {
                over = k - r->size;
                break;
            }
        if (over) {
            s_over_n++;
            if (over > s_over_max || s_over_lines < 24) {
                char w[96];
                s_over_lines++;
                if (over > s_over_max) s_over_max = over;
                rt_log("gfx lock overrun kind %u length %u off %u size %u over %u flags 0x%X from %s", e->kind, e->length, r->sub, r->size, over,
                       r->flags, rt_where(r->caller, w, sizeof w));
            }
        }
    }
    bounce_put(r->bounce, r->cap, r->own);
    e->lk[i] = e->lk[--e->nlock];
}

static int lock_of(const ent *e, uint32_t sub)
{
    for (uint32_t k = 0; k < e->nlock; k++)
        if (e->lk[k].sub == sub) return (int)k;
    return -1;
}

static void *s_lp_sys[3], *s_lp_dev;
static int s_lp_have[3];

static uint32_t release_of(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e) return 0;
    uint32_t owner = e->owner;
    uint32_t r = VT(e->host, id, uint32_t (*)(void *))(e->host);
    uint32_t held = 0;
    for (int k = 0; k < 3; k++) held += s_lp_sys[k] && s_lp_dev == e->host;
    if (id == V_RELEASE && held && r == held) {
        if (s_lp_dev == e->host) {
            for (int k = 0; k < 3; k++) {
                if (s_lp_sys[k]) VT(s_lp_sys[k], S_RELEASE, uint32_t (*)(void *))(s_lp_sys[k]);
                s_lp_sys[k] = NULL;
                s_lp_have[k] = 0;
            }
            s_lp_dev = NULL;
        }
        r = 0;
    }
    if (!r) {
        if (id == V_RELEASE) {
            s_dev_window = NULL;
            s_dev_host = NULL;
        }
        free_group(owner);
    }
    return r;
}

#define RELEASE(fn, id) \
    static uint32_t fn(const uint32_t *a) { return release_of(a, id); }

RELEASE(d_release, D_RELEASE)
RELEASE(v_release, V_RELEASE)
RELEASE(t_release, T_RELEASE)
RELEASE(c_release, C_RELEASE)
RELEASE(s_release, S_RELEASE)
RELEASE(b_release, B_RELEASE)
RELEASE(i_release, I_RELEASE)
RELEASE(decl_release, DECL_RELEASE)
RELEASE(ps_release, PS_RELEASE)
RELEASE(q_release, Q_RELEASE)

#define FWD0(fn, id) \
    static uint32_t fn(const uint32_t *a) \
    { \
        ent *e = self(a, id); \
        if (!e) return D3DERR_INVALIDCALL; \
        return (uint32_t)VT(e->host, id, uint32_t (*)(void *))(e->host); \
    }

#define FWD1(fn, id) \
    static uint32_t fn(const uint32_t *a) \
    { \
        ent *e = self(a, id); \
        if (!e) return D3DERR_INVALIDCALL; \
        return (uint32_t)VT(e->host, id, uint32_t (*)(void *, uint32_t))(e->host, a[1]); \
    }

#define FWD2(fn, id) \
    static uint32_t fn(const uint32_t *a) \
    { \
        ent *e = self(a, id); \
        if (!e) return D3DERR_INVALIDCALL; \
        return (uint32_t)VT(e->host, id, uint32_t (*)(void *, uint32_t, uint32_t))(e->host, a[1], a[2]); \
    }

#define FWD3(fn, id) \
    static uint32_t fn(const uint32_t *a) \
    { \
        ent *e = self(a, id); \
        if (!e) return D3DERR_INVALIDCALL; \
        return (uint32_t)VT(e->host, id, uint32_t (*)(void *, uint32_t, uint32_t, uint32_t))(e->host, a[1], a[2], a[3]); \
    }

FWD0(d_adaptercount, D_ADAPTERCOUNT)
FWD0(v_evict, V_EVICT)
FWD1(v_showcursor, V_SHOWCURSOR)
FWD0(v_begin, V_BEGIN)
FWD0(v_end, V_END)
FWD2(v_rs, V_RS)
FWD3(v_tss, V_TSS)
FWD3(v_ss, V_SS)
FWD0(t_type, T_TYPE)
FWD0(t_levels, T_LEVELS)
FWD0(c_type, C_TYPE)
FWD0(c_levels, C_LEVELS)
FWD1(q_issue, Q_ISSUE)

static uint32_t s_addref(const uint32_t *a)
{
    ent *e = self(a, S_ADDREF);
    if (!e) return 0;
    return VT(e->host, S_ADDREF, uint32_t (*)(void *))(e->host);
}

static uint32_t d_adapterid(const uint32_t *a)
{
    ent *e = self(a, D_ADAPTERID);
    if (!e) return D3DERR_INVALIDCALL;
    if (!span(D_ADAPTERID, a[3], sizeof(g_adapter_id), 0)) return D3DERR_INVALIDCALL;
    h_adapter_id id;
    memset(&id, 0, sizeof id);
    HR hr = VT(e->host, D_ADAPTERID, HR (*)(void *, uint32_t, uint32_t, h_adapter_id *))(e->host, a[1], a[2], &id);
    if (!hr) memcpy(rt_host(a[3]), &id, sizeof(g_adapter_id));
    return (uint32_t)hr;
}

static uint32_t s_modes[512][4], s_nmodes;

static int mode_cmp(const void *x, const void *y)
{
    const uint32_t *a = x, *b = y;
    if (a[0] != b[0]) return a[0] < b[0] ? -1 : 1;
    if (a[1] != b[1]) return a[1] < b[1] ? -1 : 1;
    return a[2] > b[2] ? -1 : a[2] < b[2];
}

static void modes_of(ent *e, uint32_t adapter, uint32_t fmt, int log)
{
    static const uint32_t k_engine[5][2] = {{640, 480}, {800, 600}, {1024, 768}, {1280, 1024}, {1600, 1200}};
    uint32_t n = VT(e->host, D_MODECOUNT, uint32_t (*)(void *, uint32_t, uint32_t))(e->host, adapter, fmt);
    s_nmodes = 0;
    for (uint32_t i = 0; i < n && s_nmodes < 507; i++)
        if (!VT(e->host, D_ENUMMODES, HR (*)(void *, uint32_t, uint32_t, uint32_t, void *))(e->host, adapter, fmt, i,
                                                                                           s_modes[s_nmodes]))
            s_nmodes++;
    if (!s_nmodes) return;
    uint32_t added = 0, want[6][2];
    int lw, lh;
    memcpy(want, k_engine, sizeof k_engine);
    rt_aspect_launch_size(&lw, &lh);
    want[5][0] = (uint32_t)lw;
    want[5][1] = (uint32_t)lh;
    for (int k = 0; k < 6; k++) {
        uint32_t i = 0;
        while (i < s_nmodes && !(s_modes[i][0] == want[k][0] && s_modes[i][1] == want[k][1])) i++;
        if (i < s_nmodes || s_nmodes >= 512) continue;
        s_modes[s_nmodes][0] = want[k][0];
        s_modes[s_nmodes][1] = want[k][1];
        s_modes[s_nmodes][2] = 60;
        s_modes[s_nmodes++][3] = fmt;
        added++;
    }
    qsort(s_modes, s_nmodes, sizeof s_modes[0], mode_cmp);
    if (log) rt_log("gfx display modes for format %u: %u from dxvk, %u windowed engine modes added", fmt, n, added);
}

static uint32_t d_modecount(const uint32_t *a)
{
    ent *e = self(a, D_MODECOUNT);
    if (!e) return 0;
    modes_of(e, a[1], a[2], 1);
    return s_nmodes;
}

static uint32_t d_enummodes(const uint32_t *a)
{
    ent *e = self(a, D_ENUMMODES);
    if (!e || !span(D_ENUMMODES, a[4], 16, 0)) return D3DERR_INVALIDCALL;
    modes_of(e, a[1], a[2], 0);
    if (a[3] >= s_nmodes) return D3DERR_INVALIDCALL;
    memcpy(rt_host(a[4]), s_modes[a[3]], 16);
    return 0;
}

static uint32_t d_dispmode(const uint32_t *a)
{
    ent *e = self(a, D_DISPMODE);
    if (!e || !span(D_DISPMODE, a[2], 16, 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, D_DISPMODE, HR (*)(void *, uint32_t, void *))(e->host, a[1], rt_host(a[2]));
}

static uint32_t d_checkfmt(const uint32_t *a)
{
    static uint32_t seen[512][3], nseen;
    ent *e = self(a, D_CHECKFMT);
    if (!e) return D3DERR_INVALIDCALL;
    HR hr = VT(e->host, D_CHECKFMT, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t))(
        e->host, a[1], a[2], a[3], a[4], a[5], a[6]);
    if (hr) {
        uint32_t k = 0;
        while (k < nseen && !(seen[k][0] == a[4] && seen[k][1] == a[5] && seen[k][2] == a[6])) k++;
        if (k == nseen && nseen < 512) {
            seen[nseen][0] = a[4];
            seen[nseen][1] = a[5];
            seen[nseen++][2] = a[6];
            rt_log("gfx CheckDeviceFormat refused usage 0x%X resource type %u format %u (adapter format %u) hr 0x%08X", a[4],
                   a[5], a[6], a[3], (uint32_t)hr);
        }
    }
    return (uint32_t)hr;
}

static uint32_t d_checkds(const uint32_t *a)
{
    ent *e = self(a, D_CHECKDS);
    if (!e) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, D_CHECKDS, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t))(
        e->host, a[1], a[2], a[3], a[4], a[5]);
}

static uint32_t d_caps(const uint32_t *a)
{
    ent *e = self(a, D_CAPS);
    if (!e || !span(D_CAPS, a[3], sizeof(d3d_caps9), 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, D_CAPS, HR (*)(void *, uint32_t, uint32_t, d3d_caps9 *))(e->host, a[1], a[2],
                                                                                         rt_host(a[3]));
}

static void image_check(void)
{
    int sdl = 0, loader = 0, kk = 0, lvp = 0;
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *n = _dyld_get_image_name(i), *b = strrchr(n, '/');
        b = b ? b + 1 : n;
        if (!strstr(b, "libdxvk_d3d9") && !strstr(b, "libvulkan") && !strstr(b, "libSDL3")) continue;
        rt_log("gfx image %s", n);
        sdl += !strncmp(b, "libSDL3", 7);
        loader += !strncmp(b, "libvulkan.", 10);
        kk += !strcmp(b, "libvulkan_kosmickrisp.dylib");
        lvp += !strcmp(b, "libvulkan_lvp.dylib");
    }
    if (sdl != 1 || loader != 1 || kk != 1 || lvp)
        rt_stop(15, 0, "gfx image check failed: libSDL3 images %d, Vulkan loaders %d, libvulkan_kosmickrisp %d, "
                       "libvulkan_lvp %d", sdl, loader, kk, lvp);
}

static void native_window(rt_win *w)
{
    int ww, wh;
    if (!w || !w->sdl) return;
    rt_aspect_native_window(w->w, w->h, &ww, &wh);
    if (ww == w->w && wh == w->h) return;
    SDL_SetWindowSize(w->sdl, ww, wh);
    rt_window_place(w->sdl, ww, wh);
    SDL_SyncWindow(w->sdl);
    rt_log("gfx native back buffer %dx%d in a %dx%d point window", w->w, w->h, ww, wh);
}

static uint32_t host_interval(h_present_params *pp, void *sdl_window)
{
    uint32_t gi = pp->PresentationInterval;
    if (rt_gfx_vsync(sdl_window)) pp->PresentationInterval = D3DPRESENT_INTERVAL_ONE;
    static int s_logged;
    if (!s_logged++) rt_log("gfx present interval: game 0x%X, host 0x%X", gi, pp->PresentationInterval);
    return gi;
}

static uint32_t d_createdevice(const uint32_t *a)
{
    ent *e = self(a, D_CREATEDEVICE);
    if (!e || !span(D_CREATEDEVICE, a[5], sizeof(g_present_params), 0) || !span(D_CREATEDEVICE, a[6], 4, 0))
        return D3DERR_INVALIDCALL;
    uint32_t ghwnd = ((g_present_params *)rt_host(a[5]))->hDeviceWindow;
    rt_win *fw = rt_win_get(a[3]), *dw = rt_win_get(ghwnd);
    if ((a[3] && !(fw && fw->sdl)) || (ghwnd && !(dw && dw->sdl))) return bad(D_CREATEDEVICE, "window", a[3]);
    native_window(dw ? dw : fw);
    h_present_params pp;
    rt_gfx_pp_in(a[5], &pp, dw ? dw->sdl : NULL);
    if (dw || fw) rt_gfx_pp_size(pp.Windowed, (dw ? dw : fw)->w, (dw ? dw : fw)->h, &pp.BackBufferWidth, &pp.BackBufferHeight);
    void *dev = NULL;
    uint32_t gi = host_interval(&pp, dw ? dw->sdl : fw ? fw->sdl : NULL);
    HR hr = VT(e->host, D_CREATEDEVICE, HR (*)(void *, uint32_t, uint32_t, void *, uint32_t, h_present_params *, void **))(
        e->host, a[1], a[2], fw ? fw->sdl : NULL, a[4], &pp, &dev);
    pp.PresentationInterval = gi;
    rt_gfx_pp_out(&pp, a[5], ghwnd);
    if (hr || !dev) {
        MEM32(a[6]) = 0;
        rt_log("gfx CreateDevice flags 0x%X failed hr 0x%08X", a[4], (uint32_t)hr);
        return hr ? (uint32_t)hr : D3DERR_INVALIDCALL;
    }
    h_adapter_id id;
    memset(&id, 0, sizeof id);
    VT(e->host, D_ADAPTERID, HR (*)(void *, uint32_t, uint32_t, h_adapter_id *))(e->host, a[1], 0, &id);
    rt_stage(9, "dxvk \"%.*s\" vendor 0x%04X %ux%u windowed %d format %u flags 0x%X backbuffers %u interval 0x%X", 512,
             id.Description, id.VendorId, pp.BackBufferWidth, pp.BackBufferHeight, pp.Windowed, pp.BackBufferFormat,
             a[4], pp.BackBufferCount, pp.PresentationInterval);
    image_check();
    s_dev_window = dw ? dw->sdl : fw ? fw->sdl : NULL;
    s_dev_host = dev;
    pthread_mutex_lock(&s_pr_mu);
    s_pr.bw = pp.BackBufferWidth;
    s_pr.bh = pp.BackBufferHeight;
    s_pr.windowed = pp.Windowed != 0;
    pthread_mutex_unlock(&s_pr_mu);
    rt_fx_attach(s_dev_window);
    MEM32(a[6]) = wrap(dev, K_DEVICE, 0);
    return 0;
}

static void lost(HR hr);

static uint32_t v_reset(const uint32_t *a)
{
    ent *e = self(a, V_RESET);
    if (!e || !span(V_RESET, a[1], sizeof(g_present_params), 0)) return D3DERR_INVALIDCALL;
    uint32_t ghwnd = ((g_present_params *)rt_host(a[1]))->hDeviceWindow;
    rt_win *dw = rt_win_get(ghwnd);
    if (ghwnd && !(dw && dw->sdl)) return bad(V_RESET, "window", ghwnd);
    native_window(dw);
    h_present_params pp;
    rt_gfx_pp_in(a[1], &pp, dw ? dw->sdl : NULL);
    if (dw) rt_gfx_pp_size(pp.Windowed, dw->w, dw->h, &pp.BackBufferWidth, &pp.BackBufferHeight);
    uint32_t gi = host_interval(&pp, dw ? dw->sdl : NULL);
    HR hr = VT(e->host, V_RESET, HR (*)(void *, h_present_params *))(e->host, &pp);
    pp.PresentationInterval = gi;
    rt_gfx_pp_out(&pp, a[1], ghwnd);
    rt_log("gfx Reset %ux%u windowed %d hr 0x%08X", pp.BackBufferWidth, pp.BackBufferHeight, pp.Windowed, (uint32_t)hr);
    lost(hr);
    if (hr) return (uint32_t)hr;
    rt_aspect_engine_set((int)pp.BackBufferWidth, (int)pp.BackBufferHeight, 1);
    if (dw) s_dev_window = dw->sdl;
    pthread_mutex_lock(&s_pr_mu);
    s_pr.bw = pp.BackBufferWidth;
    s_pr.bh = pp.BackBufferHeight;
    s_pr.windowed = pp.Windowed != 0;
    pthread_mutex_unlock(&s_pr_mu);
    return 0;
}

static void lost(HR hr)
{
    uint64_t now = rt_now_ns();
    if ((uint32_t)hr == D3DERR_DRIVERINTERNALERROR) rt_stop(15, 0, "d3d9 returned D3DERR_DRIVERINTERNALERROR");
    if (hr) {
        if (!s_lost_since) s_lost_since = now;
        else if (now - s_lost_since > 5000000000ull) rt_stop(15, 0, "device lost for more than 5 s (hr 0x%08X)", (uint32_t)hr);
    } else if (s_lost_since) {
        s_lost_ns += now - s_lost_since;
        s_lost_since = 0;
    }
}

static uint32_t v_testcoop(const uint32_t *a)
{
    ent *e = self(a, V_TESTCOOP);
    if (!e) return D3DERR_INVALIDCALL;
    HR hr = VT(e->host, V_TESTCOOP, HR (*)(void *))(e->host);
    lost(hr);
    return (uint32_t)hr;
}

static void surface_release(void *s) { VT(s, S_RELEASE, uint32_t (*)(void *))(s); }

static void frame_line(void *dev)
{
    void *rt = NULL, *ds = NULL;
    d3d_surface_desc r, d;
    memset(&r, 0, sizeof r);
    memset(&d, 0, sizeof d);
    if (!VT(dev, V_GETRT, HR (*)(void *, uint32_t, void **))(dev, 0, &rt) && rt) {
        VT(rt, S_DESC, HR (*)(void *, d3d_surface_desc *))(rt, &r);
        surface_release(rt);
    }
    if (!VT(dev, V_GETDS, HR (*)(void *, void **))(dev, &ds) && ds) {
        VT(ds, S_DESC, HR (*)(void *, d3d_surface_desc *))(ds, &d);
        surface_release(ds);
    }
    rt_log("gfx frame backbuffer %ux%u %u depth %ux%u %u", r.Width, r.Height, r.Format, d.Width, d.Height, d.Format);
}

void *rt_gfx_device_window(void) { return s_dev_window; }

void rt_gfx_shader_counts(uint32_t out[6])
{
    for (int i = 0; i < 6; i++) out[i] = __atomic_load_n(&s_shaders[i], __ATOMIC_RELAXED);
}

int rt_gfx_readback(void *dev, uint32_t **pixels, uint32_t *w, uint32_t *h)
{
    void *bb = NULL, *sys = NULL;
    d3d_surface_desc d;
    h_locked_rect lr;
    HR hr = dev ? VT(dev, V_GETBB, HR (*)(void *, uint32_t, uint32_t, uint32_t, void **))(
                      dev, 0, 0, D3DBACKBUFFER_TYPE_MONO, &bb)
                : -1;
    if (!hr) hr = VT(bb, S_DESC, HR (*)(void *, d3d_surface_desc *))(bb, &d);
    if (!hr && d.Format != D3DFMT_X8R8G8B8 && d.Format != D3DFMT_A8R8G8B8) {
        rt_log("gfx readback: back buffer format %u is not X8R8G8B8 or A8R8G8B8", d.Format);
        hr = -1;
    }
    if (!hr)
        hr = VT(dev, V_OFFSCREEN, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
            dev, d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &sys, NULL);
    if (!hr) hr = VT(dev, V_RTDATA, HR (*)(void *, void *, void *))(dev, bb, sys);
    if (!hr) hr = VT(sys, S_LOCK, HR (*)(void *, h_locked_rect *, const void *, uint32_t))(sys, &lr, NULL, D3DLOCK_READONLY);
    if (!hr) {
        uint32_t *px = malloc((size_t)d.Width * d.Height * 4);
        if (px)
            for (uint32_t y = 0; y < d.Height; y++)
                for (uint32_t x = 0; x < d.Width; x++)
                    px[(size_t)y * d.Width + x] = ((uint32_t *)((uint8_t *)lr.pBits + (size_t)y * (size_t)lr.Pitch))[x] | 0xFF000000u;
        VT(sys, S_UNLOCK, HR (*)(void *))(sys);
        *pixels = px;
        *w = d.Width;
        *h = d.Height;
        hr = px ? 0 : -1;
    }
    if (sys) surface_release(sys);
    if (bb) surface_release(bb);
    return hr ? -1 : 0;
}

static void capture(void *dev, const char *name)
{
    uint32_t *px = NULL, w = 0, h = 0;
    if (rt_gfx_readback(dev, &px, &w, &h)) {
        rt_log("gfx capture failed");
        return;
    }
    char path[PATH_MAX + 64], stamp[32];
    time_t t = time(NULL);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(stamp, sizeof stamp, "%Y%m%dT%H%M%SZ", &tm);
    snprintf(path, sizeof path, "%s/shots", s_overlay);
    mkdir(path, 0755);
    snprintf(path, sizeof path, "%s/shots/%s-%s.png", s_overlay, name, stamp);
    SDL_Surface *s = SDL_CreateSurfaceFrom((int)w, (int)h, SDL_PIXELFORMAT_XRGB8888, px, (int)w * 4);
    int ok = s && SDL_SavePNG(s, path);
    rt_log("gfx capture %s %ux%u %s", path, w, h, ok ? "written" : SDL_GetError());
    SDL_DestroySurface(s);
    free(px);
}

static int s_lp_on = -1, s_lp_cur, s_dump_a = -1, s_dump_n;
static uint32_t s_lp_w, s_lp_h, s_lp_fmt, s_lp_frame[3], s_lp_n, s_lp_black, s_lp_probed, s_dumped;
static uint64_t s_lp_t[3];
static float s_lp_prev = -1.0f;

static void dump_frame(const h_locked_rect *lr, uint32_t w, uint32_t h, uint32_t n, uint64_t t)
{
    char path[PATH_MAX + 64];
    snprintf(path, sizeof path, "%s/dump", s_overlay);
    mkdir(path, 0755);
    snprintf(path, sizeof path, "%s/dump/f%06u.bin", s_overlay, n);
    FILE *f = fopen(path, "wb");
    uint32_t hw = w / 2, hh = h / 2, hdr[6] = {0x31444242u, hw, hh, n, (uint32_t)t, (uint32_t)(t >> 32)};
    uint32_t *row = f ? malloc((size_t)hw * 4) : NULL;
    if (row && fwrite(hdr, sizeof hdr, 1, f) == 1) {
        for (uint32_t y = 0; y < hh; y++) {
            const uint32_t *src = (const uint32_t *)((const uint8_t *)lr->pBits + (size_t)(2 * y) * (size_t)lr->Pitch);
            for (uint32_t x = 0; x < hw; x++) row[x] = src[2 * x];
            fwrite(row, 4, hw, f);
        }
        s_dumped++;
    }
    free(row);
    if (f) fclose(f);
}

static void luma_probe(void *dev)
{
    if (s_lp_on < 0) s_lp_on = getenv("RT_GFX_LUMA") && atoi(getenv("RT_GFX_LUMA")) > 0;
    if (!s_lp_on) return;
    void *bb = NULL;
    d3d_surface_desc d;
    h_locked_rect lr;
    HR hr = VT(dev, V_GETBB, HR (*)(void *, uint32_t, uint32_t, uint32_t, void **))(dev, 0, 0, D3DBACKBUFFER_TYPE_MONO, &bb);
    if (!hr) hr = VT(bb, S_DESC, HR (*)(void *, d3d_surface_desc *))(bb, &d);
    if (!hr && d.Format != D3DFMT_X8R8G8B8 && d.Format != D3DFMT_A8R8G8B8) hr = -1;
    if (!hr && (!s_lp_sys[0] || s_lp_dev != dev || s_lp_w != d.Width || s_lp_h != d.Height || s_lp_fmt != d.Format)) {
        for (int k = 0; k < 3; k++) {
            if (s_lp_sys[k]) surface_release(s_lp_sys[k]);
            s_lp_sys[k] = NULL;
            s_lp_have[k] = 0;
        }
        for (int k = 0; k < 3 && !hr; k++)
            hr = VT(dev, V_OFFSCREEN, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
                dev, d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &s_lp_sys[k], NULL);
        s_lp_dev = dev;
        s_lp_w = d.Width;
        s_lp_h = d.Height;
        s_lp_fmt = d.Format;
        if (hr)
            for (int k = 0; k < 3; k++) {
                if (s_lp_sys[k]) surface_release(s_lp_sys[k]);
                s_lp_sys[k] = NULL;
            }
    }
    s_lp_n++;
    if (!hr) hr = VT(dev, V_RTDATA, HR (*)(void *, void *, void *))(dev, bb, s_lp_sys[s_lp_cur]);
    if (!hr) {
        s_lp_have[s_lp_cur] = 1;
        s_lp_frame[s_lp_cur] = s_lp_n;
        s_lp_t[s_lp_cur] = clock_gettime_nsec_np(CLOCK_UPTIME_RAW);
        int old = (s_lp_cur + 1) % 3;
        s_lp_cur = old;
        if (s_lp_have[old] && !VT(s_lp_sys[old], S_LOCK, HR (*)(void *, h_locked_rect *, const void *, uint32_t))(
                                  s_lp_sys[old], &lr, NULL, D3DLOCK_READONLY)) {
            double sum = 0;
            for (uint32_t gy = 0; gy < 24; gy++)
                for (uint32_t gx = 0; gx < 32; gx++) {
                    uint32_t x = (2 * gx + 1) * d.Width / 64, y = (2 * gy + 1) * d.Height / 48;
                    uint32_t p = *(const uint32_t *)((const uint8_t *)lr.pBits + (size_t)y * (size_t)lr.Pitch + (size_t)x * 4);
                    sum += 0.299 * ((p >> 16) & 255) + 0.587 * ((p >> 8) & 255) + 0.114 * (p & 255);
                }
            if (s_dump_a < 0) {
                const char *v = getenv("RT_GFX_DUMP");
                if (!v || sscanf(v, "%d:%d", &s_dump_a, &s_dump_n) != 2 || s_dump_a < 0 || s_dump_n <= 0) s_dump_a = s_dump_n = 0;
            }
            if (s_dump_n && s_lp_frame[old] >= (uint32_t)s_dump_a && s_lp_frame[old] - (uint32_t)s_dump_a < (uint32_t)s_dump_n)
                dump_frame(&lr, d.Width, d.Height, s_lp_frame[old], s_lp_t[old]);
            VT(s_lp_sys[old], S_UNLOCK, HR (*)(void *))(s_lp_sys[old]);
            float l = (float)(sum / 768.0);
            s_lp_probed++;
            if (l < 2.0f && s_lp_prev >= 8.0f) {
                s_lp_black++;
                rt_log("gfx black frame n=%u luma=%.1f prev=%.1f src=game", s_lp_frame[old], l, s_lp_prev);
            }
            s_lp_prev = l;
            s_lp_have[old] = 0;
        }
    }
    if (bb) surface_release(bb);
}

void rt_gfx_luma_report(void)
{
    if (s_lp_on > 0) rt_log("gfx black frames %u of %u probed (game back buffer)", s_lp_black, s_lp_probed);
    if (s_dump_n) rt_log("gfx back buffers dumped %u (frames %d to %d)", s_dumped, s_dump_a, s_dump_a + s_dump_n - 1);
}

static void fx_present(void *win, uint32_t bw, uint32_t bh)
{
    int pw = 0, ph = 0, r[4];
    SDL_GetWindowSizeInPixels(win, &pw, &ph);
    rt_aspect_letterbox((int)bw, (int)bh, pw, ph, r);
    rt_fx_frame(pw, ph, r);
}

static uint32_t v_present(const uint32_t *a)
{
    ent *e = self(a, V_PRESENT);
    if (!e || !span(V_PRESENT, a[1], 16, 1) || !span(V_PRESENT, a[2], 16, 1)) return D3DERR_INVALIDCALL;
    if (a[4]) rt_stop(10, 0, "IDirect3DDevice9::Present with a dirty region 0x%08X is not bridged", a[4]);
    if (rt_close_take()) rt_engine_exit();
    void *hwnd = NULL;
    if (a[3]) {
        rt_win *w = rt_win_get(a[3]);
        if (!w || !w->sdl) return bad(V_PRESENT, "window", a[3]);
        hwnd = w->sdl;
    }
    if (s_menu_pending) {
        s_menu_pending = 0;
        frame_line(e->host);
    }
    uint64_t t11 = rt_stage11_ns();
    if (s_capture_s > 0 && !s_capture_done && t11 && rt_now_ns() - t11 >= (uint64_t)(s_capture_s * 1e9)) {
        s_capture_done = 1;
        capture(e->host, "menu");
    }
    char cap[64];
    pthread_mutex_lock(&s_pr_mu);
    snprintf(cap, sizeof cap, "%s", s_cap_name);
    s_cap_name[0] = 0;
    s_pr.src = a[1] != 0;
    s_pr.dst = a[2] != 0;
    if (a[1]) memcpy(s_pr.s, rt_host(a[1]), 16);
    if (a[2]) memcpy(s_pr.d, rt_host(a[2]), 16);
    s_pr.win = hwnd ? hwnd : s_dev_window;
    s_pr.have = 1;
    int32_t lb[4];
    if (!a[2] && s_pr.windowed && s_pr.win) {
        int pw = 0, ph = 0, r[4];
        SDL_GetWindowSizeInPixels(s_pr.win, &pw, &ph);
        rt_aspect_letterbox((int)s_pr.bw, (int)s_pr.bh, pw, ph, r);
        if (pw > 0 && ph > 0 && (r[2] != pw || r[3] != ph)) {
            lb[0] = r[0];
            lb[1] = r[1];
            lb[2] = r[0] + r[2];
            lb[3] = r[1] + r[3];
            memcpy(s_pr.d, lb, sizeof lb);
            s_pr.dst = 1;
        }
    }
    void *fx_win = s_pr.win;
    uint32_t fx_bw = s_pr.bw, fx_bh = s_pr.bh;
    pthread_mutex_unlock(&s_pr_mu);
    if (cap[0]) capture(e->host, cap);
    if (fx_win) rt_gfx_layer_fit(fx_win);
    if (fx_win) fx_present(fx_win, fx_bw, fx_bh);
    luma_probe(e->host);
    uint64_t t0 = tic();
    HR hr = VT(e->host, V_PRESENT, HR (*)(void *, const void *, const void *, void *, const void *))(
        e->host, a[1] ? rt_host(a[1]) : NULL, a[2] ? rt_host(a[2]) : NULL, hwnd, NULL);
    toc(T_PRESENT, t0);
    rt_input_lat_present();
    lost(hr);
    uint64_t now = rt_now_ns(), fs = rt_frames_start_ns();
    if (fs != s_fs_seen) {
        s_fs_seen = fs;
        rt_gfx_pace_reset();
        s_nframes = 0;
        s_counting = 0;
        s_copy_bytes = 0;
        memset(s_bsum, 0, sizeof s_bsum);
        memset(s_btotal, 0, sizeof s_btotal);
        memset(s_bn, 0, sizeof s_bn);
    }
    if (fs && s_counting) {
        if (s_nframes == s_capframes) {
            s_capframes = s_capframes ? 2 * s_capframes : 4096;
            s_frames = realloc(s_frames, s_capframes * sizeof *s_frames);
            if (!s_frames) rt_fatal(5, "out of host memory");
        }
        s_frames[s_nframes++] = now - s_last_present;
        uint64_t cur[B_N] = {s_t[T_COPY], s_t[T_LOCK], s_t[T_DRAW], s_t[T_PRESENT], rt_sleep_ns(), rt_lock_wait_ns(),
                             rt_pump_ns()};
        ftr_note(now, now - s_last_present, cur);
        int slow = now - s_last_present > 33333333u;
        s_bn[slow]++;
        s_btotal[slow] += now - s_last_present;
        for (int k = 0; k < B_N; k++) {
            s_bsum[slow][k] += cur[k] - s_bprev[k];
            s_bprev[k] = cur[k];
        }
    } else if (fs) {
        s_counting = 1;
        uint64_t cur[B_N] = {s_t[T_COPY], s_t[T_LOCK], s_t[T_DRAW], s_t[T_PRESENT], rt_sleep_ns(), rt_lock_wait_ns(),
                             rt_pump_ns()};
        memcpy(s_bprev, cur, sizeof cur);
    }
    s_last_present = now;
    if (!hr && !s_presented) {
        s_presented = 1;
        rt_stage(10, "%s", "");
    }
    if (!hr) rt_map_present(now);
    if (!hr) rt_fps_present(now, 0);
    return (uint32_t)hr;
}

uint32_t rt_gfx_frame_count(void) { return s_nframes; }

static void *s_mv_surf, *s_mv_sys, *s_mv_q;
static uint32_t s_mv_w, s_mv_h, s_mv_shown, s_mv_dropped;

static int slot_by_name(int kind, const char *name)
{
    const rt_d3d9_table *t = &rt_d3d9_tables[kind];
    for (uint32_t i = 0; t->rows && i < t->n; i++)
        if (!strcmp(t->rows[i].name, name)) return (int)i;
    return -1;
}

int rt_gfx_movie_ready(void) { return s_mode == RT_GFX_DXVK && s_dev_host != NULL; }

void rt_gfx_movie_end(void)
{
    if (s_mv_dropped) rt_log("movie frames presented %u dropped %u while the gpu was behind", s_mv_shown, s_mv_dropped);
    if (s_mv_surf) surface_release(s_mv_surf);
    if (s_mv_sys) surface_release(s_mv_sys);
    if (s_mv_q) VT(s_mv_q, Q_RELEASE, uint32_t (*)(void *))(s_mv_q);
    s_mv_surf = s_mv_sys = s_mv_q = NULL;
    s_mv_w = s_mv_h = s_mv_shown = s_mv_dropped = 0;
}

void rt_gfx_movie_stats(uint32_t *shown, uint32_t *dropped)
{
    *shown = s_mv_shown;
    *dropped = s_mv_dropped;
}

int rt_gfx_movie_blit(const void *xrgb, uint32_t w, uint32_t h, uint32_t pitch, uint32_t dw, uint32_t dh)
{
    main_only("movie blit");
    void *dev = s_dev_host;
    int fill = slot_by_name(K_DEVICE, "IDirect3DDevice9::ColorFill");
    int upd = slot_by_name(K_DEVICE, "IDirect3DDevice9::UpdateSurface");
    if (!dev || fill < 0 || upd < 0 || !w || !h || pitch < w * 4) return -1;
    uint64_t t0 = rt_now_ns();
    __atomic_store_n(&rt_bridge_name, "movie blit", __ATOMIC_RELEASE);
    __atomic_store_n(&rt_bridge_t0, t0, __ATOMIC_RELEASE);
    int rc = -1;
    void *bb = NULL;
    d3d_surface_desc bd;
    h_locked_rect lr;
    memset(&bd, 0, sizeof bd);
    if (s_mv_surf && (s_mv_w != w || s_mv_h != h)) rt_gfx_movie_end();
    if (s_mv_q && VT(s_mv_q, Q_GETDATA, HR (*)(void *, void *, uint32_t, uint32_t))(s_mv_q, NULL, 0, D3DGETDATA_FLUSH) == S_FALSE) {
        s_mv_dropped++;
        rc = 0;
        goto out;
    }
    if (!s_mv_surf) {
        HR (*mk)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *) =
            VT(dev, V_OFFSCREEN, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *));
        if (mk(dev, w, h, D3DFMT_X8R8G8B8, D3DPOOL_DEFAULT, &s_mv_surf, NULL) || !s_mv_surf) {
            s_mv_surf = NULL;
            goto out;
        }
        if (mk(dev, w, h, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, &s_mv_sys, NULL) || !s_mv_sys) {
            s_mv_sys = NULL;
            rt_gfx_movie_end();
            goto out;
        }
        if (VT(dev, V_CREATEQUERY, HR (*)(void *, uint32_t, void **))(dev, D3DQUERYTYPE_EVENT, &s_mv_q) || !s_mv_q) {
            s_mv_q = NULL;
            rt_gfx_movie_end();
            goto out;
        }
    }
    s_mv_w = w;
    s_mv_h = h;
    if (VT(dev, V_GETBB, HR (*)(void *, uint32_t, uint32_t, uint32_t, void **))(dev, 0, 0, D3DBACKBUFFER_TYPE_MONO, &bb) || !bb)
        goto out;
    VT(bb, S_DESC, HR (*)(void *, d3d_surface_desc *))(bb, &bd);
    if (VT(s_mv_sys, S_LOCK, HR (*)(void *, h_locked_rect *, const void *, uint32_t))(s_mv_sys, &lr, NULL, 0)) goto out;
    for (uint32_t y = 0; y < h; y++)
        memcpy((uint8_t *)lr.pBits + (size_t)y * (size_t)lr.Pitch, (const uint8_t *)xrgb + (size_t)y * pitch, (size_t)w * 4);
    VT(s_mv_sys, S_UNLOCK, HR (*)(void *))(s_mv_sys);
    if (((HR (*)(void *, void *, const void *, void *, const void *))(*(void ***)dev)[upd])(dev, s_mv_sys, NULL, s_mv_surf, NULL))
        goto out;
    int32_t dst[4] = {0, 0, (int32_t)(dw < bd.Width ? dw : bd.Width), (int32_t)(dh < bd.Height ? dh : bd.Height)};
    ((HR (*)(void *, void *, const void *, uint32_t))(*(void ***)dev)[fill])(dev, bb, NULL, 0xFF000000u);
    HR hr = VT(dev, V_STRETCH, HR (*)(void *, void *, const void *, void *, const void *, uint32_t))(
        dev, s_mv_surf, NULL, bb, dst, 2);
    char cap[64];
    pthread_mutex_lock(&s_pr_mu);
    snprintf(cap, sizeof cap, "%s", s_cap_name);
    s_cap_name[0] = 0;
    s_pr.src = s_pr.dst = 0;
    s_pr.win = s_dev_window;
    s_pr.have = 1;
    int32_t lb[4];
    if (s_pr.windowed && s_dev_window) {
        int pw = 0, ph = 0, r[4];
        SDL_GetWindowSizeInPixels(s_dev_window, &pw, &ph);
        if (pw > 0 && ph > 0) {
            rt_aspect_letterbox((int)bd.Width, (int)bd.Height, pw, ph, r);
            lb[0] = r[0];
            lb[1] = r[1];
            lb[2] = r[0] + r[2];
            lb[3] = r[1] + r[3];
            memcpy(s_pr.d, lb, sizeof lb);
            s_pr.dst = 1;
        }
    }
    pthread_mutex_unlock(&s_pr_mu);
    if (s_dev_window) rt_gfx_layer_fit(s_dev_window);
    if (cap[0]) capture(dev, cap);
    VT(s_mv_q, Q_ISSUE, HR (*)(void *, uint32_t))(s_mv_q, D3DISSUE_END);
    HR ph = VT(dev, V_PRESENT, HR (*)(void *, const void *, const void *, void *, const void *))(dev, NULL, NULL, NULL, NULL);
    lost(ph);
    s_mv_shown++;
    if (!ph) rt_fps_present(rt_now_ns(), 0);
    rc = hr || ph ? -1 : 0;
out:
    if (bb) surface_release(bb);
    __atomic_store_n(&rt_bridge_t0, 0, __ATOMIC_RELEASE);
    uint64_t ms = (rt_now_ns() - t0) / 1000000ull;
    if (ms >= 1000) rt_log("gfx slow bridge call movie blit %llu ms", (unsigned long long)ms);
    return rc;
}

static uint32_t v_gamma(const uint32_t *a)
{
    ent *e = self(a, V_GAMMA);
    if (!e || !span(V_GAMMA, a[3], 1536, 0)) return D3DERR_INVALIDCALL;
    VT(e->host, V_GAMMA, void (*)(void *, uint32_t, uint32_t, const void *))(e->host, a[1], a[2], rt_host(a[3]));
    return 0;
}

static uint32_t created(HR hr, void *obj, uint32_t out, int kind, uint32_t length, uint32_t pool)
{
    if (hr || !obj) {
        MEM32(out) = 0;
        return hr ? (uint32_t)hr : D3DERR_INVALIDCALL;
    }
    uint32_t p = wrap(obj, kind, 0);
    s_e[MEM32(p + 12)].length = length;
    s_e[MEM32(p + 12)].pool = pool;
    MEM32(out) = p;
    return 0;
}

static int create_args(int id, uint32_t out, uint32_t shared)
{
    if (!span(id, out, 4, 0)) return 0;
    if (shared) {
        bad(id, "pSharedHandle", shared);
        return 0;
    }
    return 1;
}

static struct {
    uint32_t cube, format, pool, usage;
    uint64_t n, failed;
} s_texlog[64];
static uint32_t s_ntexlog;
static uint64_t s_texlog_over;

uint64_t rt_gfx_tex_overflow(void) { return s_texlog_over; }

static const char *fmt_name(uint32_t f, char *b)
{
    if (f > 0xFFFFFF) snprintf(b, 16, "%c%c%c%c", f & 0xFF, (f >> 8) & 0xFF, (f >> 16) & 0xFF, f >> 24);
    else snprintf(b, 16, "%u", f);
    return b;
}

static void tex_log(int cube, uint32_t format, uint32_t pool, uint32_t usage, uint32_t w, uint32_t h, uint32_t levels,
                    uint32_t hr)
{
    uint32_t k = 0;
    while (k < s_ntexlog && !(s_texlog[k].cube == (uint32_t)cube && s_texlog[k].format == format &&
                              s_texlog[k].pool == pool && s_texlog[k].usage == usage))
        k++;
    if (k == s_ntexlog) {
        if (k == 64) {
            s_texlog_over++;
            return;
        }
        s_texlog[k].cube = (uint32_t)cube;
        s_texlog[k].format = format;
        s_texlog[k].pool = pool;
        s_texlog[k].usage = usage;
        s_ntexlog++;
        char f[16];
        rt_log("gfx texture %s format %s pool %u usage 0x%X first %ux%u levels %u hr 0x%08X", cube ? "cube" : "2d",
               fmt_name(format, f), pool, usage, w, h, levels, hr);
    }
    s_texlog[k].n++;
    s_texlog[k].failed += hr != 0;
}

static uint32_t v_createtex(const uint32_t *a)
{
    ent *e = self(a, V_CREATETEX);
    if (!e || !create_args(V_CREATETEX, a[7], a[8])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATETEX,
               HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], a[5], a[6], &o, NULL);
    uint32_t r = created(hr, o, a[7], K_TEXTURE, 0, a[6]);
    tex_log(0, a[5], a[6], a[4], a[1], a[2], a[3], r);
    return r;
}

static uint32_t v_createcube(const uint32_t *a)
{
    ent *e = self(a, V_CREATECUBE);
    if (!e || !create_args(V_CREATECUBE, a[6], a[7])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATECUBE, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], a[5], &o, NULL);
    uint32_t r = created(hr, o, a[6], K_CUBE, 0, a[5]);
    tex_log(1, a[4], a[5], a[3], a[1], a[1], a[2], r);
    return r;
}

static uint32_t v_createvb(const uint32_t *a)
{
    ent *e = self(a, V_CREATEVB);
    if (!e || !create_args(V_CREATEVB, a[5], a[6])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATEVB, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], &o, NULL);
    return created(hr, o, a[5], K_VB, a[1], a[4]);
}

static uint32_t v_createib(const uint32_t *a)
{
    ent *e = self(a, V_CREATEIB);
    if (!e || !create_args(V_CREATEIB, a[5], a[6])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATEIB, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], &o, NULL);
    return created(hr, o, a[5], K_IB, a[1], a[4]);
}

static uint32_t v_createds(const uint32_t *a)
{
    ent *e = self(a, V_CREATEDS);
    if (!e || !create_args(V_CREATEDS, a[7], a[8])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATEDS,
               HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], a[5], a[6], &o, NULL);
    return created(hr, o, a[7], K_SURFACE, 0, 0);
}

static uint32_t v_createquery(const uint32_t *a)
{
    ent *e = self(a, V_CREATEQUERY);
    if (!e || !span(V_CREATEQUERY, a[2], 4, 1)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATEQUERY, HR (*)(void *, uint32_t, void **))(e->host, a[1], a[2] ? &o : NULL);
    if (!a[2]) return (uint32_t)hr;
    return created(hr, o, a[2], K_QUERY, 0, 0);
}

static uint32_t v_createdecl(const uint32_t *a)
{
    ent *e = self(a, V_CREATEDECL);
    if (!e || !span(V_CREATEDECL, a[1], 8, 0) || !span(V_CREATEDECL, a[2], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATEDECL, HR (*)(void *, const void *, void **))(e->host, rt_host(a[1]), &o);
    return created(hr, o, a[2], K_DECL, 0, 0);
}

static void shader_count(uint32_t version, HR hr)
{
    static const uint32_t k[5] = {0xFFFE0101u, 0xFFFE0200u, 0xFFFF0101u, 0xFFFF0104u, 0xFFFF0200u};
    if (hr) {
        s_shaders[5]++;
        rt_log("gfx shader create failed: version token 0x%08X hr 0x%08X", version, (uint32_t)hr);
        return;
    }
    for (int i = 0; i < 5; i++)
        if (version == k[i]) {
            s_shaders[i]++;
            return;
        }
    rt_log("gfx shader version token 0x%08X created", version);
}

static uint32_t create_shader(const uint32_t *a, int id, int kind)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[1], 4, 0) || !span(id, a[2], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, id, HR (*)(void *, const void *, void **))(e->host, rt_host(a[1]), &o);
    shader_count(MEM32(a[1]), hr);
    return created(hr, o, a[2], kind, 0, 0);
}

static uint32_t v_createvs(const uint32_t *a) { return create_shader(a, V_CREATEVS, K_VS); }
static uint32_t v_createps(const uint32_t *a) { return create_shader(a, V_CREATEPS, K_PS); }

static uint32_t set_obj(const uint32_t *a, int id, uint32_t k1, uint32_t k2)
{
    ent *e = self(a, id);
    void *o;
    if (!e || !obj_arg(id, a[1], k1, k2, &o)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, void *))(e->host, o);
}

static uint32_t v_setds(const uint32_t *a) { return set_obj(a, V_SETDS, K_SURFACE, K_SURFACE); }
static uint32_t v_setdecl(const uint32_t *a) { return set_obj(a, V_SETDECL, K_DECL, K_DECL); }
static uint32_t v_setvs(const uint32_t *a) { return set_obj(a, V_SETVS, K_VS, K_VS); }
static uint32_t v_setps(const uint32_t *a) { return set_obj(a, V_SETPS, K_PS, K_PS); }
static uint32_t v_indices(const uint32_t *a) { return set_obj(a, V_INDICES, K_IB, K_IB); }

static uint32_t v_setrt(const uint32_t *a)
{
    ent *e = self(a, V_SETRT);
    void *o;
    if (!e || !obj_arg(V_SETRT, a[2], K_SURFACE, K_SURFACE, &o)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, V_SETRT, HR (*)(void *, uint32_t, void *))(e->host, a[1], o);
}

static uint32_t v_settex(const uint32_t *a)
{
    ent *e = self(a, V_SETTEX);
    void *o;
    if (!e || !obj_arg(V_SETTEX, a[2], K_TEXTURE, K_CUBE, &o)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, V_SETTEX, HR (*)(void *, uint32_t, void *))(e->host, a[1], o);
}

static uint32_t v_stream(const uint32_t *a)
{
    ent *e = self(a, V_STREAM);
    void *o;
    if (!e || !obj_arg(V_STREAM, a[2], K_VB, K_VB, &o)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, V_STREAM, HR (*)(void *, uint32_t, void *, uint32_t, uint32_t))(e->host, a[1], o, a[3], a[4]);
}

static uint32_t v_getrt(const uint32_t *a)
{
    ent *e = self(a, V_GETRT);
    if (!e || !span(V_GETRT, a[2], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_GETRT, HR (*)(void *, uint32_t, void **))(e->host, a[1], &o);
    MEM32(a[2]) = hr || !o ? 0 : wrap(o, K_SURFACE, 0);
    return (uint32_t)hr;
}

static uint32_t v_getds(const uint32_t *a)
{
    ent *e = self(a, V_GETDS);
    if (!e || !span(V_GETDS, a[1], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_GETDS, HR (*)(void *, void **))(e->host, &o);
    MEM32(a[1]) = hr || !o ? 0 : wrap(o, K_SURFACE, 0);
    return (uint32_t)hr;
}

static uint32_t v_clear(const uint32_t *a)
{
    ent *e = self(a, V_CLEAR);
    if (!e || !span(V_CLEAR, a[2], 16ull * a[1], 1)) return D3DERR_INVALIDCALL;
    float z;
    memcpy(&z, &a[5], 4);
    return (uint32_t)VT(e->host, V_CLEAR, HR (*)(void *, uint32_t, const void *, uint32_t, uint32_t, float, uint32_t))(
        e->host, a[1], a[2] ? rt_host(a[2]) : NULL, a[3], a[4], z, a[6]);
}

static uint32_t in_place2(const uint32_t *a, int id, uint64_t n)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[2], n, 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, uint32_t, const void *))(e->host, a[1], rt_host(a[2]));
}

static uint32_t in_place1(const uint32_t *a, int id, uint64_t n)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[1], n, 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, const void *))(e->host, rt_host(a[1]));
}

static uint32_t v_transform(const uint32_t *a) { return in_place2(a, V_TRANSFORM, 64); }
static uint32_t v_viewport(const uint32_t *a) { return in_place1(a, V_VIEWPORT, 24); }
static uint32_t v_material(const uint32_t *a) { return in_place1(a, V_MATERIAL, 68); }

static uint32_t consts(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[2], 16ull * a[3], 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, uint32_t, const void *, uint32_t))(e->host, a[1], rt_host(a[2]), a[3]);
}

static uint32_t v_vsconst(const uint32_t *a) { return consts(a, V_VSCONST); }
static uint32_t v_psconst(const uint32_t *a) { return consts(a, V_PSCONST); }

static uint32_t v_dip(const uint32_t *a)
{
    ent *e = self(a, V_DIP);
    if (!e) return D3DERR_INVALIDCALL;
    uint64_t t0 = tic();
    HR hr = VT(e->host, V_DIP, HR (*)(void *, uint32_t, int32_t, uint32_t, uint32_t, uint32_t, uint32_t))(
        e->host, a[1], (int32_t)a[2], a[3], a[4], a[5], a[6]);
    toc(T_DRAW, t0);
    return (uint32_t)hr;
}

static uint32_t level_desc(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[2], sizeof(d3d_surface_desc), 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, uint32_t, void *))(e->host, a[1], rt_host(a[2]));
}

static uint32_t t_desc(const uint32_t *a) { return level_desc(a, T_DESC); }
static uint32_t c_desc(const uint32_t *a) { return level_desc(a, C_DESC); }

static uint32_t t_surface(const uint32_t *a)
{
    ent *e = self(a, T_SURFACE);
    if (!e || !span(T_SURFACE, a[2], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, T_SURFACE, HR (*)(void *, uint32_t, void **))(e->host, a[1], &o);
    MEM32(a[2]) = hr || !o ? 0 : wrap(o, K_SURFACE, e->proxy);
    return (uint32_t)hr;
}

static uint32_t c_surface(const uint32_t *a)
{
    ent *e = self(a, C_SURFACE);
    if (!e || !span(C_SURFACE, a[3], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, C_SURFACE, HR (*)(void *, uint32_t, uint32_t, void **))(e->host, a[1], a[2], &o);
    MEM32(a[3]) = hr || !o ? 0 : wrap(o, K_SURFACE, e->proxy);
    return (uint32_t)hr;
}

static HR image_unlock_host(ent *e, int id, uint32_t face, uint32_t level)
{
    if (id == S_LOCK || id == S_UNLOCK) return VT(e->host, S_UNLOCK, HR (*)(void *))(e->host);
    if (id == T_LOCKRECT || id == T_UNLOCKRECT) return VT(e->host, T_UNLOCKRECT, HR (*)(void *, uint32_t))(e->host, level);
    return VT(e->host, C_UNLOCKRECT, HR (*)(void *, uint32_t, uint32_t))(e->host, face, level);
}

static uint32_t image_lock(const uint32_t *a, int id, uint32_t face, uint32_t level, uint32_t lrva, uint32_t rectva,
                           uint32_t flags)
{
    ent *e = self(a, id);
    if (!e || !span(id, lrva, sizeof(g_locked_rect), 0) || !span(id, rectva, 16, 1)) return D3DERR_INVALIDCALL;
    d3d_surface_desc d;
    HR hr;
    uint32_t sub = 0;
    if (id == S_LOCK) hr = VT(e->host, S_DESC, HR (*)(void *, d3d_surface_desc *))(e->host, &d);
    else if (id == T_LOCKRECT) {
        sub = level;
        hr = VT(e->host, T_DESC, HR (*)(void *, uint32_t, d3d_surface_desc *))(e->host, level, &d);
    } else {
        sub = face * VT(e->host, C_LEVELS, uint32_t (*)(void *))(e->host) + level;
        hr = VT(e->host, C_DESC, HR (*)(void *, uint32_t, d3d_surface_desc *))(e->host, level, &d);
    }
    if (hr) return (uint32_t)hr;
    if (lock_of(e, sub) >= 0) return bad(id, "second lock of the same level", a[0]);
    if (e->nlock == 8) rt_stop(5, 0, "%s: more than 8 open locks on one texture", k_m[id].name);
    uint32_t w = d.Width, h = d.Height;
    if (rectva) {
        const int32_t *r = rt_host(rectva);
        if (r[0] < 0 || r[1] < 0 || r[2] <= r[0] || r[3] <= r[1] || (uint32_t)r[2] > d.Width || (uint32_t)r[3] > d.Height)
            return bad(id, "rectangle", rectva);
        w = (uint32_t)(r[2] - r[0]);
        h = (uint32_t)(r[3] - r[1]);
    }
    h_locked_rect lr;
    const void *rect = rectva ? rt_host(rectva) : NULL;
    if (id == S_LOCK)
        hr = VT(e->host, S_LOCK, HR (*)(void *, h_locked_rect *, const void *, uint32_t))(e->host, &lr, rect, flags);
    else if (id == T_LOCKRECT)
        hr = VT(e->host, T_LOCKRECT, HR (*)(void *, uint32_t, h_locked_rect *, const void *, uint32_t))(e->host, level, &lr,
                                                                                                       rect, flags);
    else
        hr = VT(e->host, C_LOCKRECT, HR (*)(void *, uint32_t, uint32_t, h_locked_rect *, const void *, uint32_t))(
            e->host, face, level, &lr, rect, flags);
    if (hr) return (uint32_t)hr;
    int discard = (flags & (D3DLOCK_DISCARD | D3DLOCK_NOOVERWRITE)) == D3DLOCK_DISCARD && d.Pool == D3DPOOL_DEFAULT &&
                  w == d.Width && h == d.Height;
    uint32_t b = lock_begin(e, id, lr.pBits, (uint32_t)rt_rows_of(d.Format, h), (uint32_t)rt_pitch_of(d.Format, w),
                            lr.Pitch, flags, discard, sub);
    if (!b) {
        image_unlock_host(e, id, face, level);
        return E_OUTOFMEMORY;
    }
    rt_gfx_lr_out(&lr, lrva, b);
    return 0;
}

static uint32_t image_unlock(const uint32_t *a, int id, uint32_t face, uint32_t level)
{
    ent *e = self(a, id);
    if (!e) return D3DERR_INVALIDCALL;
    uint32_t sub = id == T_UNLOCKRECT ? level : 0;
    if (id == C_UNLOCKRECT) sub = face * VT(e->host, C_LEVELS, uint32_t (*)(void *))(e->host) + level;
    int k = lock_of(e, sub);
    if (k >= 0) lock_end(e, (uint32_t)k);
    return (uint32_t)image_unlock_host(e, id, face, level);
}

static uint32_t s_lockrect(const uint32_t *a) { return image_lock(a, S_LOCK, 0, 0, a[1], a[2], a[3]); }
static uint32_t s_unlockrect(const uint32_t *a) { return image_unlock(a, S_UNLOCK, 0, 0); }
static uint32_t t_lockrect(const uint32_t *a) { return image_lock(a, T_LOCKRECT, 0, a[1], a[2], a[3], a[4]); }
static uint32_t t_unlockrect(const uint32_t *a) { return image_unlock(a, T_UNLOCKRECT, 0, a[1]); }
static uint32_t c_lockrect(const uint32_t *a) { return image_lock(a, C_LOCKRECT, a[1], a[2], a[3], a[4], a[5]); }
static uint32_t c_unlockrect(const uint32_t *a) { return image_unlock(a, C_UNLOCKRECT, a[1], a[2]); }

static uint32_t buf_lock(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[3], 4, 0)) return D3DERR_INVALIDCALL;
    uint32_t off = a[1], n = a[2] ? a[2] : (e->length > off ? e->length - off : 0);
    if (off > e->length || n > e->length - off) return bad(id, "range", off);
    if (e->nlock == 8) rt_stop(5, 0, "%s: more than 8 open locks on one buffer", k_m[id].name);
    uint32_t slack = e->length - off - n;
    if (slack > LOCK_SLACK) slack = LOCK_SLACK;
    for (uint32_t k = 0; s_check > 0 && k < e->nlock; k++)
        if (off < e->lk[k].sub + e->lk[k].size && e->lk[k].sub < off + n + slack) {
            s_nest_n++;
            if (s_nest_lines++ < 24)
                rt_log("gfx nested lock overlap %s length %u open off %u size %u, new off %u size %u slack %u flags 0x%X", k_m[id].name,
                       e->length, e->lk[k].sub, e->lk[k].size, off, n, slack, a[4]);
        }
    void *hp = NULL;
    uint64_t t0 = tic();
    HR hr = VT(e->host, id, HR (*)(void *, uint32_t, uint32_t, void **, uint32_t))(e->host, off, a[2] ? n + slack : 0, &hp,
                                                                                    a[4]);
    toc(T_LOCK, t0);
    if (hr || !hp) {
        MEM32(a[3]) = 0;
        return hr ? (uint32_t)hr : D3DERR_INVALIDCALL;
    }
    uint32_t b = lock_begin(e, id, hp, 1, n + slack, (int32_t)(n + slack), a[4], 0, off);
    if (!b) {
        VT(e->host, id + 1, HR (*)(void *))(e->host);
        return E_OUTOFMEMORY;
    }
    MEM32(a[3]) = b;
    return 0;
}

static uint32_t buf_unlock(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e) return D3DERR_INVALIDCALL;
    if (e->nlock) lock_end(e, e->nlock - 1);
    uint64_t t0 = tic();
    HR hr = VT(e->host, id, HR (*)(void *))(e->host);
    toc(T_LOCK, t0);
    return (uint32_t)hr;
}

static uint32_t b_lock(const uint32_t *a) { return buf_lock(a, B_LOCK); }
static uint32_t b_unlock(const uint32_t *a) { return buf_unlock(a, B_UNLOCK); }
static uint32_t i_lock(const uint32_t *a) { return buf_lock(a, I_LOCK); }
static uint32_t i_unlock(const uint32_t *a) { return buf_unlock(a, I_UNLOCK); }

static uint32_t i_desc(const uint32_t *a)
{
    ent *e = self(a, I_DESC);
    if (!e || !span(I_DESC, a[1], 20, 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, I_DESC, HR (*)(void *, void *))(e->host, rt_host(a[1]));
}

#define FWDV0(fn, id) \
    static uint32_t fn(const uint32_t *a) \
    { \
        ent *e = self(a, id); \
        if (e) VT(e->host, id, void (*)(void *))(e->host); \
        return 0; \
    }

#define ADDREF(fn, id) \
    static uint32_t fn(const uint32_t *a) \
    { \
        ent *e = self(a, id); \
        return e ? VT(e->host, id, uint32_t (*)(void *))(e->host) : 0; \
    }

static uint32_t get_device(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[1], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, id, HR (*)(void *, void **))(e->host, &o);
    MEM32(a[1]) = hr || !o ? 0 : wrap(o, K_DEVICE, 0);
    return (uint32_t)hr;
}

#define DEVICE(fn, id) \
    static uint32_t fn(const uint32_t *a) { return get_device(a, id); }

#define D3DSPD_IUNKNOWN 1u

static uint32_t set_pd(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[1], 16, 0)) return D3DERR_INVALIDCALL;
    if (a[4] & D3DSPD_IUNKNOWN) {
        static int once;
        if (!once++) rt_log("gfx %s with D3DSPD_IUNKNOWN refused: a guest interface has no host object", k_m[id].name);
        return D3DERR_INVALIDCALL;
    }
    if (!span(id, a[2], a[3], a[3] == 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, const void *, const void *, uint32_t, uint32_t))(
        e->host, rt_host(a[1]), a[2] ? rt_host(a[2]) : NULL, a[3], a[4]);
}

static uint32_t get_pd(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[1], 16, 0) || !span(id, a[3], 4, a[2] == 0)) return D3DERR_INVALIDCALL;
    if (a[2] && !span(id, a[2], MEM32(a[3]), 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, const void *, void *, void *))(
        e->host, rt_host(a[1]), a[2] ? rt_host(a[2]) : NULL, a[3] ? rt_host(a[3]) : NULL);
}

static uint32_t free_pd(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[1], 16, 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, const void *))(e->host, rt_host(a[1]));
}

#define PRIVATE(set, get, fr, sid, gid, fid) \
    static uint32_t set(const uint32_t *a) { return set_pd(a, sid); } \
    static uint32_t get(const uint32_t *a) { return get_pd(a, gid); } \
    static uint32_t fr(const uint32_t *a) { return free_pd(a, fid); }

#define RESOURCE(p, P) \
    DEVICE(p##_device, P##_DEVICE) \
    PRIVATE(p##_setpd, p##_getpd, p##_freepd, P##_SETPD, P##_GETPD, P##_FREEPD) \
    FWD1(p##_setpri, P##_SETPRI) \
    FWD0(p##_getpri, P##_GETPRI) \
    FWDV0(p##_preload, P##_PRELOAD)

RESOURCE(t, T)
RESOURCE(c, C)
RESOURCE(s, S)
RESOURCE(b, B)
RESOURCE(i, I)

#define TEXTURE(p, P) \
    ADDREF(p##_addref, P##_ADDREF) \
    FWD1(p##_setlod, P##_SETLOD) \
    FWD0(p##_getlod, P##_GETLOD) \
    FWD1(p##_setfilter, P##_SETFILTER) \
    FWD0(p##_getfilter, P##_GETFILTER) \
    FWDV0(p##_genmips, P##_GENMIPS)

TEXTURE(t, T)
TEXTURE(c, C)
ADDREF(b_addref, B_ADDREF)
ADDREF(i_addref, I_ADDREF)
ADDREF(q_addref, Q_ADDREF)
ADDREF(decl_addref, DECL_ADDREF)
ADDREF(vs_addref, VS_ADDREF)
ADDREF(ps_addref, PS_ADDREF)
ADDREF(sb_addref, SB_ADDREF)
DEVICE(q_device, Q_DEVICE)
DEVICE(decl_device, DECL_DEVICE)
DEVICE(vs_device, VS_DEVICE)
DEVICE(ps_device, PS_DEVICE)
DEVICE(sb_device, SB_DEVICE)
FWD0(s_type, S_TYPE)
FWD0(b_type, B_TYPE)
FWD0(i_type, I_TYPE)
FWD0(q_type, Q_TYPE)
FWD0(q_size, Q_SIZE)
FWD0(sb_capture, SB_CAPTURE)
FWD0(sb_apply, SB_APPLY)
FWD0(v_beginsb, V_BEGINSB)
FWD2(v_lightenable, V_LIGHTENABLE)
RELEASE(vs_release, VS_RELEASE)
RELEASE(sb_release, SB_RELEASE)

static uint32_t t_dirty(const uint32_t *a)
{
    ent *e = self(a, T_DIRTY);
    if (!e || !span(T_DIRTY, a[1], 16, 1)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, T_DIRTY, HR (*)(void *, const void *))(e->host, a[1] ? rt_host(a[1]) : NULL);
}

static uint32_t c_dirty(const uint32_t *a)
{
    ent *e = self(a, C_DIRTY);
    if (!e || !span(C_DIRTY, a[2], 16, 1)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, C_DIRTY, HR (*)(void *, uint32_t, const void *))(e->host, a[1],
                                                                                 a[2] ? rt_host(a[2]) : NULL);
}

static uint32_t s_desc(const uint32_t *a)
{
    ent *e = self(a, S_DESC);
    if (!e || !span(S_DESC, a[1], sizeof(d3d_surface_desc), 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, S_DESC, HR (*)(void *, void *))(e->host, rt_host(a[1]));
}

static uint32_t b_desc(const uint32_t *a)
{
    ent *e = self(a, B_DESC);
    if (!e || !span(B_DESC, a[1], 24, 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, B_DESC, HR (*)(void *, void *))(e->host, rt_host(a[1]));
}

static uint32_t q_getdata(const uint32_t *a)
{
    ent *e = self(a, Q_GETDATA);
    if (!e || !span(Q_GETDATA, a[1], a[2], !a[2])) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, Q_GETDATA, HR (*)(void *, void *, uint32_t, uint32_t))(
        e->host, a[1] ? rt_host(a[1]) : NULL, a[2], a[3]);
}

static uint32_t decl_get(const uint32_t *a)
{
    ent *e = self(a, DECL_GET);
    if (!e || !span(DECL_GET, a[2], 4, 0)) return D3DERR_INVALIDCALL;
    HR hr = VT(e->host, DECL_GET, HR (*)(void *, void *, void *))(e->host, NULL, rt_host(a[2]));
    if (hr || !a[1]) return (uint32_t)hr;
    if (!span(DECL_GET, a[1], 8ull * MEM32(a[2]), 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, DECL_GET, HR (*)(void *, void *, void *))(e->host, rt_host(a[1]), rt_host(a[2]));
}

static uint32_t get_function(const uint32_t *a, int id)
{
    ent *e = self(a, id);
    if (!e || !span(id, a[2], 4, 0) || (a[1] && !span(id, a[1], MEM32(a[2]), 0))) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, id, HR (*)(void *, void *, void *))(e->host, a[1] ? rt_host(a[1]) : NULL,
                                                                   rt_host(a[2]));
}

static uint32_t vs_func(const uint32_t *a) { return get_function(a, VS_FUNC); }
static uint32_t ps_func(const uint32_t *a) { return get_function(a, PS_FUNC); }

static uint32_t d_mstype(const uint32_t *a)
{
    ent *e = self(a, D_MSTYPE);
    if (!e || !span(D_MSTYPE, a[6], 4, 1)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, D_MSTYPE, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void *))(
        e->host, a[1], a[2], a[3], a[4], a[5], a[6] ? rt_host(a[6]) : NULL);
}

static uint32_t v_caps(const uint32_t *a)
{
    ent *e = self(a, V_CAPS);
    if (!e || !span(V_CAPS, a[1], sizeof(d3d_caps9), 0)) return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, V_CAPS, HR (*)(void *, void *))(e->host, rt_host(a[1]));
}

static uint32_t v_getbb(const uint32_t *a)
{
    ent *e = self(a, V_GETBB);
    if (!e || !span(V_GETBB, a[4], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_GETBB, HR (*)(void *, uint32_t, uint32_t, uint32_t, void **))(e->host, a[1], a[2], a[3], &o);
    MEM32(a[4]) = hr || !o ? 0 : wrap(o, K_SURFACE, 0);
    return (uint32_t)hr;
}

static uint32_t v_creatert(const uint32_t *a)
{
    ent *e = self(a, V_CREATERT);
    if (!e || !create_args(V_CREATERT, a[7], a[8])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_CREATERT,
               HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], a[5], a[6], &o, NULL);
    return created(hr, o, a[7], K_SURFACE, 0, D3DPOOL_DEFAULT);
}

static uint32_t v_offscreen(const uint32_t *a)
{
    ent *e = self(a, V_OFFSCREEN);
    if (!e || !create_args(V_OFFSCREEN, a[5], a[6])) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_OFFSCREEN, HR (*)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *))(
        e->host, a[1], a[2], a[3], a[4], &o, NULL);
    return created(hr, o, a[5], K_SURFACE, 0, a[4]);
}

static uint32_t v_rtdata(const uint32_t *a)
{
    ent *e = self(a, V_RTDATA);
    void *src, *dst;
    if (!e || !obj_arg(V_RTDATA, a[1], K_SURFACE, K_SURFACE, &src) || !obj_arg(V_RTDATA, a[2], K_SURFACE, K_SURFACE, &dst))
        return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, V_RTDATA, HR (*)(void *, void *, void *))(e->host, src, dst);
}

static uint32_t v_stretch(const uint32_t *a)
{
    ent *e = self(a, V_STRETCH);
    void *src, *dst;
    if (!e || !obj_arg(V_STRETCH, a[1], K_SURFACE, K_SURFACE, &src) || !span(V_STRETCH, a[2], 16, 1) ||
        !obj_arg(V_STRETCH, a[3], K_SURFACE, K_SURFACE, &dst) || !span(V_STRETCH, a[4], 16, 1))
        return D3DERR_INVALIDCALL;
    return (uint32_t)VT(e->host, V_STRETCH, HR (*)(void *, void *, const void *, void *, const void *, uint32_t))(
        e->host, src, a[2] ? rt_host(a[2]) : NULL, dst, a[4] ? rt_host(a[4]) : NULL, a[5]);
}

static uint32_t v_light(const uint32_t *a) { return in_place2(a, V_LIGHT, 104); }
static uint32_t v_clip(const uint32_t *a) { return in_place2(a, V_CLIP, 16); }

static uint32_t v_endsb(const uint32_t *a)
{
    ent *e = self(a, V_ENDSB);
    if (!e || !span(V_ENDSB, a[1], 4, 0)) return D3DERR_INVALIDCALL;
    void *o = NULL;
    HR hr = VT(e->host, V_ENDSB, HR (*)(void *, void **))(e->host, &o);
    return created(hr, o, a[1], K_SB, 0, 0);
}

static uint32_t v_dp(const uint32_t *a)
{
    ent *e = self(a, V_DP);
    if (!e) return D3DERR_INVALIDCALL;
    uint64_t t0 = tic();
    HR hr = VT(e->host, V_DP, HR (*)(void *, uint32_t, uint32_t, uint32_t))(e->host, a[1], a[2], a[3]);
    toc(T_DRAW, t0);
    return (uint32_t)hr;
}

static const method k_m[M_N] = {
    [D_RELEASE] = {K_D3D, "IDirect3D9::Release", d_release},
    [D_ADAPTERCOUNT] = {K_D3D, "IDirect3D9::GetAdapterCount", d_adaptercount},
    [D_ADAPTERID] = {K_D3D, "IDirect3D9::GetAdapterIdentifier", d_adapterid},
    [D_MODECOUNT] = {K_D3D, "IDirect3D9::GetAdapterModeCount", d_modecount},
    [D_ENUMMODES] = {K_D3D, "IDirect3D9::EnumAdapterModes", d_enummodes},
    [D_DISPMODE] = {K_D3D, "IDirect3D9::GetAdapterDisplayMode", d_dispmode},
    [D_CHECKFMT] = {K_D3D, "IDirect3D9::CheckDeviceFormat", d_checkfmt},
    [D_CHECKDS] = {K_D3D, "IDirect3D9::CheckDepthStencilMatch", d_checkds},
    [D_CAPS] = {K_D3D, "IDirect3D9::GetDeviceCaps", d_caps},
    [D_CREATEDEVICE] = {K_D3D, "IDirect3D9::CreateDevice", d_createdevice},
    [V_RELEASE] = {K_DEVICE, "IDirect3DDevice9::Release", v_release},
    [V_TESTCOOP] = {K_DEVICE, "IDirect3DDevice9::TestCooperativeLevel", v_testcoop},
    [V_EVICT] = {K_DEVICE, "IDirect3DDevice9::EvictManagedResources", v_evict},
    [V_SHOWCURSOR] = {K_DEVICE, "IDirect3DDevice9::ShowCursor", v_showcursor},
    [V_PRESENT] = {K_DEVICE, "IDirect3DDevice9::Present", v_present},
    [V_GAMMA] = {K_DEVICE, "IDirect3DDevice9::SetGammaRamp", v_gamma},
    [V_CREATETEX] = {K_DEVICE, "IDirect3DDevice9::CreateTexture", v_createtex},
    [V_CREATECUBE] = {K_DEVICE, "IDirect3DDevice9::CreateCubeTexture", v_createcube},
    [V_CREATEVB] = {K_DEVICE, "IDirect3DDevice9::CreateVertexBuffer", v_createvb},
    [V_CREATEIB] = {K_DEVICE, "IDirect3DDevice9::CreateIndexBuffer", v_createib},
    [V_CREATEDS] = {K_DEVICE, "IDirect3DDevice9::CreateDepthStencilSurface", v_createds},
    [V_SETRT] = {K_DEVICE, "IDirect3DDevice9::SetRenderTarget", v_setrt},
    [V_GETRT] = {K_DEVICE, "IDirect3DDevice9::GetRenderTarget", v_getrt},
    [V_SETDS] = {K_DEVICE, "IDirect3DDevice9::SetDepthStencilSurface", v_setds},
    [V_GETDS] = {K_DEVICE, "IDirect3DDevice9::GetDepthStencilSurface", v_getds},
    [V_BEGIN] = {K_DEVICE, "IDirect3DDevice9::BeginScene", v_begin},
    [V_END] = {K_DEVICE, "IDirect3DDevice9::EndScene", v_end},
    [V_CLEAR] = {K_DEVICE, "IDirect3DDevice9::Clear", v_clear},
    [V_TRANSFORM] = {K_DEVICE, "IDirect3DDevice9::SetTransform", v_transform},
    [V_VIEWPORT] = {K_DEVICE, "IDirect3DDevice9::SetViewport", v_viewport},
    [V_MATERIAL] = {K_DEVICE, "IDirect3DDevice9::SetMaterial", v_material},
    [V_RS] = {K_DEVICE, "IDirect3DDevice9::SetRenderState", v_rs},
    [V_SETTEX] = {K_DEVICE, "IDirect3DDevice9::SetTexture", v_settex},
    [V_TSS] = {K_DEVICE, "IDirect3DDevice9::SetTextureStageState", v_tss},
    [V_SS] = {K_DEVICE, "IDirect3DDevice9::SetSamplerState", v_ss},
    [V_DIP] = {K_DEVICE, "IDirect3DDevice9::DrawIndexedPrimitive", v_dip},
    [V_CREATEDECL] = {K_DEVICE, "IDirect3DDevice9::CreateVertexDeclaration", v_createdecl},
    [V_SETDECL] = {K_DEVICE, "IDirect3DDevice9::SetVertexDeclaration", v_setdecl},
    [V_CREATEVS] = {K_DEVICE, "IDirect3DDevice9::CreateVertexShader", v_createvs},
    [V_SETVS] = {K_DEVICE, "IDirect3DDevice9::SetVertexShader", v_setvs},
    [V_VSCONST] = {K_DEVICE, "IDirect3DDevice9::SetVertexShaderConstantF", v_vsconst},
    [V_STREAM] = {K_DEVICE, "IDirect3DDevice9::SetStreamSource", v_stream},
    [V_INDICES] = {K_DEVICE, "IDirect3DDevice9::SetIndices", v_indices},
    [V_CREATEPS] = {K_DEVICE, "IDirect3DDevice9::CreatePixelShader", v_createps},
    [V_SETPS] = {K_DEVICE, "IDirect3DDevice9::SetPixelShader", v_setps},
    [V_PSCONST] = {K_DEVICE, "IDirect3DDevice9::SetPixelShaderConstantF", v_psconst},
    [V_CREATEQUERY] = {K_DEVICE, "IDirect3DDevice9::CreateQuery", v_createquery},
    [T_RELEASE] = {K_TEXTURE, "IDirect3DTexture9::Release", t_release},
    [T_TYPE] = {K_TEXTURE, "IDirect3DTexture9::GetType", t_type},
    [T_LEVELS] = {K_TEXTURE, "IDirect3DTexture9::GetLevelCount", t_levels},
    [T_DESC] = {K_TEXTURE, "IDirect3DTexture9::GetLevelDesc", t_desc},
    [T_SURFACE] = {K_TEXTURE, "IDirect3DTexture9::GetSurfaceLevel", t_surface},
    [C_RELEASE] = {K_CUBE, "IDirect3DCubeTexture9::Release", c_release},
    [C_TYPE] = {K_CUBE, "IDirect3DCubeTexture9::GetType", c_type},
    [C_LEVELS] = {K_CUBE, "IDirect3DCubeTexture9::GetLevelCount", c_levels},
    [C_DESC] = {K_CUBE, "IDirect3DCubeTexture9::GetLevelDesc", c_desc},
    [C_SURFACE] = {K_CUBE, "IDirect3DCubeTexture9::GetCubeMapSurface", c_surface},
    [S_ADDREF] = {K_SURFACE, "IDirect3DSurface9::AddRef", s_addref},
    [S_RELEASE] = {K_SURFACE, "IDirect3DSurface9::Release", s_release},
    [S_LOCK] = {K_SURFACE, "IDirect3DSurface9::LockRect", s_lockrect},
    [S_UNLOCK] = {K_SURFACE, "IDirect3DSurface9::UnlockRect", s_unlockrect},
    [B_RELEASE] = {K_VB, "IDirect3DVertexBuffer9::Release", b_release},
    [B_LOCK] = {K_VB, "IDirect3DVertexBuffer9::Lock", b_lock},
    [B_UNLOCK] = {K_VB, "IDirect3DVertexBuffer9::Unlock", b_unlock},
    [I_RELEASE] = {K_IB, "IDirect3DIndexBuffer9::Release", i_release},
    [I_LOCK] = {K_IB, "IDirect3DIndexBuffer9::Lock", i_lock},
    [I_UNLOCK] = {K_IB, "IDirect3DIndexBuffer9::Unlock", i_unlock},
    [I_DESC] = {K_IB, "IDirect3DIndexBuffer9::GetDesc", i_desc},
    [DECL_RELEASE] = {K_DECL, "IDirect3DVertexDeclaration9::Release", decl_release},
    [PS_RELEASE] = {K_PS, "IDirect3DPixelShader9::Release", ps_release},
    [Q_RELEASE] = {K_QUERY, "IDirect3DQuery9::Release", q_release},
    [Q_ISSUE] = {K_QUERY, "IDirect3DQuery9::Issue", q_issue},
    [D_MSTYPE] = {K_D3D, "IDirect3D9::CheckDeviceMultiSampleType", d_mstype},
    [V_CAPS] = {K_DEVICE, "IDirect3DDevice9::GetDeviceCaps", v_caps},
    [V_GETBB] = {K_DEVICE, "IDirect3DDevice9::GetBackBuffer", v_getbb},
    [V_CREATERT] = {K_DEVICE, "IDirect3DDevice9::CreateRenderTarget", v_creatert},
    [V_OFFSCREEN] = {K_DEVICE, "IDirect3DDevice9::CreateOffscreenPlainSurface", v_offscreen},
    [V_RTDATA] = {K_DEVICE, "IDirect3DDevice9::GetRenderTargetData", v_rtdata},
    [V_STRETCH] = {K_DEVICE, "IDirect3DDevice9::StretchRect", v_stretch},
    [V_LIGHT] = {K_DEVICE, "IDirect3DDevice9::SetLight", v_light},
    [V_LIGHTENABLE] = {K_DEVICE, "IDirect3DDevice9::LightEnable", v_lightenable},
    [V_CLIP] = {K_DEVICE, "IDirect3DDevice9::SetClipPlane", v_clip},
    [V_BEGINSB] = {K_DEVICE, "IDirect3DDevice9::BeginStateBlock", v_beginsb},
    [V_ENDSB] = {K_DEVICE, "IDirect3DDevice9::EndStateBlock", v_endsb},
    [V_DP] = {K_DEVICE, "IDirect3DDevice9::DrawPrimitive", v_dp},
#define TEX_ROWS(p, P, K, N) \
    [P##_ADDREF] = {K, N "::AddRef", p##_addref}, \
    [P##_DEVICE] = {K, N "::GetDevice", p##_device}, \
    [P##_SETPD] = {K, N "::SetPrivateData", p##_setpd}, \
    [P##_GETPD] = {K, N "::GetPrivateData", p##_getpd}, \
    [P##_FREEPD] = {K, N "::FreePrivateData", p##_freepd}, \
    [P##_SETPRI] = {K, N "::SetPriority", p##_setpri}, \
    [P##_GETPRI] = {K, N "::GetPriority", p##_getpri}, \
    [P##_PRELOAD] = {K, N "::PreLoad", p##_preload}, \
    [P##_SETLOD] = {K, N "::SetLOD", p##_setlod}, \
    [P##_GETLOD] = {K, N "::GetLOD", p##_getlod}, \
    [P##_SETFILTER] = {K, N "::SetAutoGenFilterType", p##_setfilter}, \
    [P##_GETFILTER] = {K, N "::GetAutoGenFilterType", p##_getfilter}, \
    [P##_GENMIPS] = {K, N "::GenerateMipSubLevels", p##_genmips}, \
    [P##_LOCKRECT] = {K, N "::LockRect", p##_lockrect}, \
    [P##_UNLOCKRECT] = {K, N "::UnlockRect", p##_unlockrect}, \
    [P##_DIRTY] = {K, N "::AddDirtyRect", p##_dirty},
    TEX_ROWS(t, T, K_TEXTURE, "IDirect3DTexture9")
    TEX_ROWS(c, C, K_CUBE, "IDirect3DCubeTexture9")
#define RES_ROWS(p, P, K, N) \
    [P##_DEVICE] = {K, N "::GetDevice", p##_device}, \
    [P##_SETPD] = {K, N "::SetPrivateData", p##_setpd}, \
    [P##_GETPD] = {K, N "::GetPrivateData", p##_getpd}, \
    [P##_FREEPD] = {K, N "::FreePrivateData", p##_freepd}, \
    [P##_SETPRI] = {K, N "::SetPriority", p##_setpri}, \
    [P##_GETPRI] = {K, N "::GetPriority", p##_getpri}, \
    [P##_PRELOAD] = {K, N "::PreLoad", p##_preload}, \
    [P##_TYPE] = {K, N "::GetType", p##_type},
    RES_ROWS(s, S, K_SURFACE, "IDirect3DSurface9")
    RES_ROWS(b, B, K_VB, "IDirect3DVertexBuffer9")
    RES_ROWS(i, I, K_IB, "IDirect3DIndexBuffer9")
    [S_DESC] = {K_SURFACE, "IDirect3DSurface9::GetDesc", s_desc},
    [B_ADDREF] = {K_VB, "IDirect3DVertexBuffer9::AddRef", b_addref},
    [B_DESC] = {K_VB, "IDirect3DVertexBuffer9::GetDesc", b_desc},
    [I_ADDREF] = {K_IB, "IDirect3DIndexBuffer9::AddRef", i_addref},
    [Q_ADDREF] = {K_QUERY, "IDirect3DQuery9::AddRef", q_addref},
    [Q_DEVICE] = {K_QUERY, "IDirect3DQuery9::GetDevice", q_device},
    [Q_TYPE] = {K_QUERY, "IDirect3DQuery9::GetType", q_type},
    [Q_SIZE] = {K_QUERY, "IDirect3DQuery9::GetDataSize", q_size},
    [Q_GETDATA] = {K_QUERY, "IDirect3DQuery9::GetData", q_getdata},
    [DECL_ADDREF] = {K_DECL, "IDirect3DVertexDeclaration9::AddRef", decl_addref},
    [DECL_DEVICE] = {K_DECL, "IDirect3DVertexDeclaration9::GetDevice", decl_device},
    [DECL_GET] = {K_DECL, "IDirect3DVertexDeclaration9::GetDeclaration", decl_get},
    [VS_RELEASE] = {K_VS, "IDirect3DVertexShader9::Release", vs_release},
    [VS_ADDREF] = {K_VS, "IDirect3DVertexShader9::AddRef", vs_addref},
    [VS_DEVICE] = {K_VS, "IDirect3DVertexShader9::GetDevice", vs_device},
    [VS_FUNC] = {K_VS, "IDirect3DVertexShader9::GetFunction", vs_func},
    [PS_ADDREF] = {K_PS, "IDirect3DPixelShader9::AddRef", ps_addref},
    [PS_DEVICE] = {K_PS, "IDirect3DPixelShader9::GetDevice", ps_device},
    [PS_FUNC] = {K_PS, "IDirect3DPixelShader9::GetFunction", ps_func},
    [SB_RELEASE] = {K_SB, "IDirect3DStateBlock9::Release", sb_release},
    [SB_ADDREF] = {K_SB, "IDirect3DStateBlock9::AddRef", sb_addref},
    [SB_DEVICE] = {K_SB, "IDirect3DStateBlock9::GetDevice", sb_device},
    [SB_CAPTURE] = {K_SB, "IDirect3DStateBlock9::Capture", sb_capture},
    [SB_APPLY] = {K_SB, "IDirect3DStateBlock9::Apply", sb_apply},
    [V_RESET] = {K_DEVICE, "IDirect3DDevice9::Reset", v_reset},
};

_Static_assert(M_REACHED == 166, "166 reached methods");

rt_shim_fn rt_gfx_method(const char *name)
{
    for (int i = 0; i < M_REACHED; i++)
        if (!strcmp(k_m[i].name, name)) return k_m[i].fn;
    return NULL;
}

static void build(void)
{
    if (s_built) return;
    s_built = 1;
    s_check = getenv("RT_GFX_CHECK") ? atoi(getenv("RT_GFX_CHECK")) : 0;
    for (int id = 0; id < M_N; id++) {
        const rt_d3d9_table *t = &rt_d3d9_tables[k_m[id].kind];
        s_slot[id] = -1;
        for (uint32_t i = 0; t->rows && i < t->n; i++)
            if (!strcmp(t->rows[i].name, k_m[id].name)) s_slot[id] = (int)i;
        if (s_slot[id] < 0) rt_stop(5, 0, "d3d9 bridge method %s is not in the d3d9 tables", k_m[id].name);
    }
    for (int k = 0; k < K_N; k++) {
        const rt_d3d9_table *t = &rt_d3d9_tables[k];
        if (!t->rows) continue;
        rt_shim *rows = calloc(t->n, sizeof *rows);
        uint32_t v = rt_heap_alloc(4 * t->n, 1);
        if (!rows || !v) rt_fatal(5, "out of memory for the d3d9 vtables");
        for (uint32_t i = 0; i < t->n; i++) {
            rows[i] = t->rows[i];
            rows[i].fn = rt_gfx_method(rows[i].name);
            rows[i].kind = RT_SHIM_BRIDGE;
            MEM32(v + 4 * i) = rt_dynamic_shim("d3d9.dll", rows[i].name, &rows[i]);
        }
        s_vtbl[k] = v;
    }
}

static uint32_t b_create9(const uint32_t *a)
{
    if (s_mode != RT_GFX_DXVK) return rt_d3d9_null_shims[0].fn(a);
    main_only("Direct3DCreate9");
    build();
    void *d = s_create9(a[0]);
    uint32_t p = wrap(d, K_D3D, 0);
    rt_log("Direct3DCreate9(%u) -> dxvk IDirect3D9 0x%08X", a[0], p);
    return p;
}

rt_shim rt_d3d9_bridge_shims[] = {
    {"d3d9.dll", "Direct3DCreate9", 1, b_create9, RT_SHIM_REAL},
};

const uint32_t rt_nd3d9_bridge_shims = sizeof rt_d3d9_bridge_shims / sizeof rt_d3d9_bridge_shims[0];

int rt_gfx_mode(void) { return s_mode; }

uint64_t rt_gfx_flags_for(int mode, int native)
{
    if (mode != RT_GFX_DXVK) return 0;
    return (uint64_t)SDL_WINDOW_VULKAN | (native ? (uint64_t)SDL_WINDOW_HIGH_PIXEL_DENSITY : 0);
}

uint64_t rt_gfx_window_flags(void) { return rt_gfx_flags_for(s_mode, rt_aspect_launch_native()); }

void rt_gfx_set_capture(double s) { s_capture_s = s; }

void rt_gfx_capture_named(const char *name)
{
    pthread_mutex_lock(&s_pr_mu);
    snprintf(s_cap_name, sizeof s_cap_name, "%s", name);
    pthread_mutex_unlock(&s_pr_mu);
}

int rt_gfx_capture_pending(void)
{
    pthread_mutex_lock(&s_pr_mu);
    int p = s_cap_name[0] != 0;
    pthread_mutex_unlock(&s_pr_mu);
    return p;
}

int rt_gfx_present_rects(rt_present_rects *r)
{
    pthread_mutex_lock(&s_pr_mu);
    __typeof__(s_pr) p = s_pr;
    pthread_mutex_unlock(&s_pr_mu);
    if (!p.have) return 0;
    int pw = 0, ph = 0;
    if (p.win) SDL_GetWindowSizeInPixels(p.win, &pw, &ph);
    if (p.windowed && p.src) *r = (rt_present_rects){p.s[0], p.s[1], p.s[2] - p.s[0], p.s[3] - p.s[1], 0, 0, 0, 0};
    else *r = (rt_present_rects){0, 0, (int)p.bw, (int)p.bh, 0, 0, 0, 0};
    if (p.windowed && p.dst) {
        r->dx = p.d[0];
        r->dy = p.d[1];
        r->dw = p.d[2] - p.d[0];
        r->dh = p.d[3] - p.d[1];
    } else {
        r->dw = pw;
        r->dh = ph;
    }
    return r->sw > 0 && r->sh > 0 && r->dw > 0 && r->dh > 0;
}

void rt_gfx_menu(void) { s_menu_pending = s_mode == RT_GFX_DXVK; }

static void env(const char *name, const char *value)
{
    const char *old = getenv(name);
    setenv(name, value, 0);
    rt_log("gfx env %s=%s (%s)", name, getenv(name), old ? "kept" : "set");
}

void rt_gfx_resolve_paths(const char *exe, const char *home, const char *e_dev, const char *e_lib, const char *e_loader,
                          const char *e_icd, rt_gfx_paths *o)
{
    static const char tail[] = "/Contents/MacOS";
    char dir[PATH_MAX] = "";
    if (exe) {
        snprintf(dir, sizeof dir, "%s", exe);
        char *slash = strrchr(dir, '/');
        if (slash) *slash = 0;
    }
    size_t n = strlen(dir), t = sizeof tail - 1;
    o->bundled = 0;
    if (n > t + 4 && !strcmp(dir + n - t, tail)) {
        dir[n - t] = 0;
        n -= t;
        o->bundled = !strcmp(dir + n - 4, ".app");
    }
    if (o->bundled) {
        snprintf(o->lib, sizeof o->lib, "%s/Contents/Frameworks/libdxvk_d3d9.0.dylib", dir);
        snprintf(o->loader, sizeof o->loader, "%s/Contents/Frameworks/libvulkan.1.dylib", dir);
        snprintf(o->icd, sizeof o->icd, "%s/Contents/Resources/" ICD_FILE, dir);
    } else {
        if (e_dev && *e_dev) snprintf(o->lib, sizeof o->lib, "%s/dxvk/inst/lib/libdxvk_d3d9.0.dylib", e_dev);
        else snprintf(o->lib, sizeof o->lib, "%s/Library/Caches/BloodlinesPorter/dev/dxvk/inst/lib/libdxvk_d3d9.0.dylib", home ? home : ".");
        snprintf(o->loader, sizeof o->loader, "%s", LOADER_PATH);
        snprintf(o->icd, sizeof o->icd, "%s", ICD_PATH);
    }
    if (e_lib) snprintf(o->lib, sizeof o->lib, "%s", e_lib);
    if (e_loader) snprintf(o->loader, sizeof o->loader, "%s", e_loader);
    if (e_icd) snprintf(o->icd, sizeof o->icd, "%s", e_icd);
}

static void own_exe(char out[PATH_MAX])
{
    char raw[PATH_MAX];
    uint32_t n = sizeof raw;
    out[0] = 0;
    if (_NSGetExecutablePath(raw, &n) || !realpath(raw, out)) out[0] = 0;
}

static void archive_dxvk_log(const char *dir)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d))) {
        size_t n = strlen(e->d_name);
        char from[PATH_MAX + 300], to[PATH_MAX + 64], stamp[32];
        struct stat st;
        if (n <= 9 || strcmp(e->d_name + n - 9, "_d3d9.log")) continue;
        snprintf(from, sizeof from, "%s/%s", dir, e->d_name);
        if (stat(from, &st) || !st.st_size) continue;
        struct tm tm;
        gmtime_r(&st.st_mtime, &tm);
        strftime(stamp, sizeof stamp, "%Y%m%dT%H%M%SZ", &tm);
        snprintf(to, sizeof to, "%s/dxvk-%s.log", dir, stamp);
        if (rename(from, to)) rt_log("gfx dxvk log %s not kept: %s", from, strerror(errno));
    }
    closedir(d);
}

int rt_gfx_init(int mode, const char *overlay)
{
    s_mode = mode;
    snprintf(s_overlay, sizeof s_overlay, "%s", overlay);
    rt_d3d9_bridge_shims[0].kind = mode == RT_GFX_DXVK ? RT_SHIM_BRIDGE : RT_SHIM_REAL;
    if (mode != RT_GFX_DXVK) {
        rt_log("gfx null");
        return 0;
    }
    char exe[PATH_MAX], path[PATH_MAX + 64];
    own_exe(exe);
    rt_gfx_paths gp;
    rt_gfx_resolve_paths(exe, getenv("HOME"), getenv("BN_DEV_HOME"), getenv("RT_DXVK_LIB"), getenv("SDL_VULKAN_LIBRARY"),
                         getenv("VK_DRIVER_FILES"), &gp);
    rt_log("gfx paths %s: lib %s loader %s icd %s", gp.bundled ? "bundle" : "dev", gp.lib, gp.loader, gp.icd);
    const char *lib = gp.lib, *icd = gp.icd;
    const char *need[3] = {lib, gp.loader, icd};
    for (int i = 0; i < 3; i++)
        if (access(need[i], R_OK)) {
            rt_log("gfx missing %s; --gfx null runs without dxvk", need[i]);
            return -1;
        }
    env("DXVK_WSI_DRIVER", "SDL3");
    env("VK_DRIVER_FILES", icd);
    char dxvk_cfg[512];
    setenv("DXVK_CONFIG", rt_dxvk_config(DXVK_CONFIG_VALUE, getenv("DXVK_CONFIG"), dxvk_cfg, sizeof dxvk_cfg), 1);
    env("MESA_KK_DEBUG", "force_robustness");
    snprintf(path, sizeof path, "%s/logs", overlay);
    archive_dxvk_log(path);
    env("DXVK_LOG_PATH", path);
    snprintf(path, sizeof path, "%s/cache", overlay);
    mkdir(path, 0755);
    snprintf(path, sizeof path, "%s/cache/dxvk", overlay);
    mkdir(path, 0755);
    env("DXVK_SHADER_CACHE_PATH", path);
    snprintf(path, sizeof path, "%s/cache/mesa", overlay);
    mkdir(path, 0755);
    env("MESA_SHADER_CACHE_DIR", path);
    SDL_SetHint(SDL_HINT_VULKAN_LIBRARY, gp.loader);
    rt_log("gfx hint %s=%s", SDL_HINT_VULKAN_LIBRARY, SDL_GetHint(SDL_HINT_VULKAN_LIBRARY));
    snprintf(path, sizeof path, "%s/files/Vampire/vidcfg.bin", overlay);
    FILE *f = fopen(path, "rb");
    if (f) {
        unsigned char id[16];
        int zero = fread(id, 1, 16, f) == 16;
        fclose(f);
        for (int i = 0; i < 16 && zero; i++) zero = !id[i];
        if (zero) rt_log("gfx deleted %s (null device ID): %s", path, unlink(path) ? "failed" : "ok");
    }
    s_lib = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
    if (!s_lib) {
        rt_log("gfx missing %s: %s; --gfx null runs without dxvk", lib, dlerror());
        return -1;
    }
    s_create9 = (void *(*)(uint32_t))dlsym(s_lib, "Direct3DCreate9");
    if (!s_create9) {
        rt_log("gfx missing Direct3DCreate9 in %s; --gfx null runs without dxvk", lib);
        return -1;
    }
    rt_log("gfx dxvk %s", lib);
    return 0;
}

static int cmp64(const void *x, const void *y)
{
    uint64_t a = *(const uint64_t *)x, b = *(const uint64_t *)y;
    return a < b ? -1 : a > b;
}

void rt_gfx_report(void)
{
    uint32_t n = s_nframes;
    double q[4] = {0, 0, 0, 0};
    uint64_t *v = n ? malloc(n * sizeof *v) : NULL;
    if (v) {
        memcpy(v, s_frames, n * sizeof *v);
        qsort(v, n, sizeof *v, cmp64);
        static const double p[3] = {0.50, 0.95, 0.99};
        for (int i = 0; i < 3; i++) q[i] = (double)v[(uint32_t)ceil(p[i] * n) - 1] / 1e6;
        q[3] = (double)v[n - 1] / 1e6;
        free(v);
    }
    rt_log("frames n %u p50 %.2f p95 %.2f p99 %.2f max %.2f", n, q[0], q[1], q[2], q[3]);
    rt_gfx_pace_report();
    rt_gfx_luma_report();
    rt_log("guest cpu swaps %llu", (unsigned long long)rt_cpu_swaps());
    rt_log("gfx lock overruns %u max %u", s_over_n, s_over_max);
    if (s_check > 0) rt_log("gfx nested lock overlaps %u", s_nest_n);
    static const char *const k_part[B_N] = {"copies", "device-lock", "draws", "present", "sleep", "lock-wait", "pump"};
    for (int s = 0; s < 2; s++) {
        char b[512];
        size_t o = 0;
        double d = s_bn[s] ? (double)s_bn[s] * 1e6 : 1;
        uint64_t rest = s_btotal[s];
        for (int k = 0; k < B_N; k++) {
            o += (size_t)snprintf(b + o, sizeof b - o, " %s %.2f", k_part[k], (double)s_bsum[s][k] / d);
            rest -= s_bsum[s][k] < rest ? s_bsum[s][k] : rest;
        }
        rt_log("frames %s n %llu ms/frame: total %.2f%s other %.2f", s ? "over 33.3 ms" : "up to 33.3 ms",
               (unsigned long long)s_bn[s], (double)s_btotal[s] / d, b, (double)rest / d);
    }
    rt_log("frames copied %.1f KiB per frame", n ? (double)s_copy_bytes / 1024.0 / n : 0.0);
    ftr_dump();
    rt_log("shaders vs_1_1 %u vs_2_0 %u ps_1_1 %u ps_1_4 %u ps_2_0 %u failed %u", s_shaders[0], s_shaders[1], s_shaders[2],
           s_shaders[3], s_shaders[4], s_shaders[5]);
    for (uint32_t k = 0; k < s_ntexlog; k++) {
        char f[16];
        rt_log("gfx textures %s format %s pool %u usage 0x%X n %llu failed %llu", s_texlog[k].cube ? "cube" : "2d",
               fmt_name(s_texlog[k].format, f), s_texlog[k].pool, s_texlog[k].usage, (unsigned long long)s_texlog[k].n,
               (unsigned long long)s_texlog[k].failed);
    }
    if (s_texlog_over) rt_log("gfx textures past 64 combos n %llu", (unsigned long long)s_texlog_over);
    rt_log("gfx locks %llu out-of-arena %llu late-writes %llu", (unsigned long long)s_locks, (unsigned long long)s_oob,
           (unsigned long long)s_late);
    uint64_t lost_ns = s_lost_ns + (s_lost_since ? rt_now_ns() - s_lost_since : 0);
    rt_log("gfx lost %llu", (unsigned long long)(lost_ns / 1000000u));
    rt_fx_report();
}
