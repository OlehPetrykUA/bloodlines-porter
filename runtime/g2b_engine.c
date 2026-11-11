#include "recomp_types.h"
#include "rt.h"
#include "aspect.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { G_BYTES, G_JMP, G_CELL, G_DATA, G_CALL };

#define CALL_LEN 6
#define STOCK_SCALE "d80d7cb92210"

typedef struct {
    const char *id, *module;
    uint32_t rva;
    int kind;
    const char *old, *new_;
    uint32_t cell;
    int absoff;
    const rt_shim *shim;
    uint32_t guard, glen;
} g2b_row;

const int rt_engine_override __attribute__((weak)) = 0;

static struct {
    int w, h, on, have, checked, keep, hud;
    double r;
} s_a = {1024, 768, 0, 0, 1, 0, 0, 4.0 / 3.0};

static uint32_t fbits(float f)
{
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

static float gfloat(uint32_t va)
{
    float f;
    memcpy(&f, rt_host(va), 4);
    return f;
}

static void pfloat(uint32_t va, float f) { memcpy(rt_host(va), &f, 4); }

static uint32_t unhex(const char *s, uint8_t *out)
{
    uint32_t n = 0;
    for (; s[0] && s[1] && n < 64; s += 2) {
        unsigned v;
        sscanf(s, "%2x", &v);
        out[n++] = (uint8_t)v;
    }
    return n;
}

static void configure(int w, int h, int on);

static void mode_check(void)
{
    if (s_a.checked) return;
    s_a.checked = 1;
    uint32_t sw = rt_hook_va("engine", 0xb42330);
    if (!sw) return;
    int w = (int)MEM32(sw), h = (int)MEM32(sw + 4);
    if (w <= 0 || h <= 0 || (w == s_a.w && h == s_a.h)) return;
    rt_log("aspect engine mode %dx%d differs from the request", w, h);
    configure(w, h, s_a.on);
}

static uint32_t aspect(const uint32_t *a)
{
    (void)a;
    mode_check();
    if (s_a.on) return fbits((float)s_a.r);
    uint32_t cv = MEM32(rt_hook_va("engine", 0xa6ca8c));
    if (!cv) return 0x3faaaaabu;
    g_ecx = cv;
    if (rt_guest_call(MEM32(MEM32(cv) + 4), 0, NULL) & 0xffu) return 0x3faaaaabu;
    return MEM32(cv + 0x2c) ? 0x3fe38e39u : 0x3faaaaabu;
}

static float hor(float f) { return s_a.on ? (float)rt_aspect_up_hfov(f, s_a.r) : f; }

static struct {
    uint32_t va, in, out;
} s_fov[64];

static void hor_once(uint32_t va)
{
    __typeof__(s_fov[0]) *e = &s_fov[(va >> 2) % 64];
    uint32_t cur = MEM32(va);
    if (e->va != va || e->out != cur) e->in = cur;
    e->va = va;
    float in;
    memcpy(&in, &e->in, 4);
    e->out = fbits(hor(in));
    MEM32(va) = e->out;
}

static uint32_t view_fov(const uint32_t *a)
{
    hor_once(a[0] + 0x38);
    hor_once(a[0] + 0x3c);
    return 0;
}

static uint32_t camera_fov(const uint32_t *a)
{
    hor_once(a[0] + 0x340);
    return 0;
}

static const rt_shim k_aspect = {"m9", "AspectRatio", 0, aspect, RT_SHIM_REAL};
static const rt_shim k_view_fov = {"m9", "ViewFov", 1, view_fov, RT_SHIM_REAL};
static int pillar(int *wp, int *off)
{
    if (!s_a.on || !s_a.hud || s_a.w * 3 <= s_a.h * 4) return 0;
    *wp = (s_a.h * 4 + 1) / 3;
    *off = (s_a.w - *wp) / 2;
    return 1;
}

static uint32_t (*s_gcall)(uint32_t, int, const uint32_t *) = rt_guest_call;

static uint32_t load_size(const uint32_t *a)
{
    uint32_t self = g_ecx, args[2] = {a[0], a[1]};
    int wp, off;
    if (pillar(&wp, &off) && (int)a[0] == s_a.w && (int)a[1] == s_a.h) args[0] = (uint32_t)wp;
    g_ecx = self;
    return s_gcall(rt_hook_va("client", 0x1a98f0), 2, args);
}

static uint32_t load_pos(const uint32_t *a)
{
    uint32_t self = g_ecx, args[2] = {a[0], a[1]};
    int wp, off;
    if (pillar(&wp, &off) && !a[0] && !a[1]) args[0] = (uint32_t)off;
    g_ecx = self;
    return s_gcall(rt_hook_va("client", 0x1a9890), 2, args);
}

static uint32_t ui_width(const uint32_t *a)
{
    (void)a;
    int wp, off;
    if (pillar(&wp, &off)) return (uint32_t)wp;
    return s_gcall(rt_hook_va("client", 0xcd100), 0, NULL);
}

static uint32_t ui_frame(const uint32_t *a)
{
    uint32_t self = g_ecx, args[4] = {a[0], a[1], a[2], a[3]};
    int wp, off;
    if (pillar(&wp, &off) && (!a[0] || abs((int)a[0] - (s_a.w - (int)a[2])) <= 1) && abs((int)a[2] - wp) <= 1) args[0] = (uint32_t)((s_a.w - (int)a[2]) / 2);
    g_ecx = self;
    return s_gcall(rt_hook_va("client", 0x1a9950), 4, args);
}

static uint32_t ui_image(const uint32_t *a)
{
    uint32_t self = g_ecx, args[4] = {a[0], a[1], a[2], a[3]};
    int wp, off;
    if (pillar(&wp, &off)) args[0] = a[0] + (uint32_t)off;
    g_ecx = self;
    return s_gcall(rt_hook_va("client", 0x59620), 4, args);
}

static uint32_t ui_image5(const uint32_t *a)
{
    uint32_t self = g_ecx, args[5] = {a[0], a[1], a[2], a[3], a[4]};
    int wp, off;
    if (pillar(&wp, &off)) args[0] = a[0] + (uint32_t)off;
    g_ecx = self;
    return s_gcall(rt_hook_va("client", 0x5c3b0), 5, args);
}

static uint32_t ui_text_x(const uint32_t *a)
{
    uint32_t self = g_ecx, args[3] = {a[0], a[1], a[2]};
    int wp, off;
    g_ecx = self;
    uint32_t r = s_gcall(rt_hook_va("client", 0x5f590), 3, args);
    uint32_t *x = pillar(&wp, &off) ? rt_host(a[2]) : NULL;
    if (x) *x += (uint32_t)off;
    return r;
}

static const rt_shim k_ui_image = {"m9", "UiImage", 4, ui_image, RT_SHIM_REAL};
static const rt_shim k_ui_image5 = {"m9", "UiImage5", 5, ui_image5, RT_SHIM_REAL};
static const rt_shim k_ui_text_x = {"m9", "UiTextX", 3, ui_text_x, RT_SHIM_REAL};
static const rt_shim k_camera_fov = {"m9", "CameraFov", 1, camera_fov, RT_SHIM_REAL};
static const rt_shim k_ui_width = {"m9", "UiWidth", 0, ui_width, RT_SHIM_REAL};
static const rt_shim k_ui_frame = {"m9", "UiFrame", 4, ui_frame, RT_SHIM_REAL};
static const rt_shim k_load_size = {"m9", "LoadSize", 2, load_size, RT_SHIM_REAL};
static const rt_shim k_load_pos = {"m9", "LoadPos", 2, load_pos, RT_SHIM_REAL};

static const g2b_row k_rows[] = {
    {"g2b_modes", "engine", 0xfc8ac, G_BYTES, "8d642400", "eb199090", 0, -1, NULL, 0xfc7a0, 0x110},
    {"g2b_aspect", "engine", 0x7a424, G_CELL, "8b0d8ccaa6208b01ff500484c0750d8b0d8ccaa6208b412c85c0750ac7442410abaaaa3feb08c7442410398ee33f",
     "ff158008400089442410eb2290909090909090909090909090909090909090909090909090909090909090909090", 32, 2, &k_aspect, 0x7a424, 0x2e},
    {"g2b_anamorphic", "client", 0x191fd5, G_BYTES, "74", "eb", 0, -1, NULL, 0x191fd5, 1},
    {"g2b_gamma", "client", 0x109bae, G_BYTES, "dc0dc87e2210", "d80d7cb92210", 0, 2, NULL, 0x109bae, 6},
    {"g2b_load_size", "client", 0x16728d, G_CALL, "e85e260400", "", 36, -1, &k_load_size, 0x16728d, 5},
    {"g2b_load_pos", "client", 0x167298, G_CALL, "e8f3250400", "", 37, -1, &k_load_pos, 0x167298, 5},
    {"g2b_ui_charedit_frame", "client", 0x1716b1, G_CALL, "e89a820300", "", 38, -1, &k_ui_frame, 0x1716b1, 5},
    {"g2b_ui_wizard_ctor", "client", 0x1748fa, G_CALL, "e851500300", "", 40, -1, &k_ui_frame, 0x1748fa, 5},
    {"g2b_ui_wizard_show", "client", 0x174bd1, G_CALL, "e87a4d0300", "", 41, -1, &k_ui_frame, 0x174bd1, 5},
    {"g2b_ui_charedit_w", "client", 0x171697, G_CALL, "e864baf5ff", "", 42, -1, &k_ui_width, 0x171697, 5},
    {"g2b_ui_charedit_cx_a", "client", 0x1720b7, G_CALL, "e844b0f5ff", "", 43, -1, &k_ui_width, 0x1720b7, 5},
    {"g2b_ui_charedit_cx_b", "client", 0x17216b, G_CALL, "e890aff5ff", "", 44, -1, &k_ui_width, 0x17216b, 5},
    {"g2b_ui_char_w", "client", 0x17659e, G_CALL, "e85d6bf5ff", "", 45, -1, &k_ui_width, 0x17659e, 5},
    {"g2b_ui_equip_w", "client", 0x178964, G_CALL, "e89747f5ff", "", 46, -1, &k_ui_width, 0x178964, 5},
    {"g2b_ui_info_w", "client", 0x17b5ce, G_CALL, "e82d1bf5ff", "", 47, -1, &k_ui_width, 0x17b5ce, 5},
    {"g2b_ui_barter_frame", "client", 0x16ee75, G_CALL, "e8d6aa0300", "", 50, -1, &k_ui_frame, 0x16ee75, 5},
    {"g2b_ui_box_paint", "client", 0x170f49, G_CALL, "e8d286eeff", "", 51, -1, &k_ui_image, 0x170f49, 5},
    {"g2b_ui_slot_paint", "client", 0x16b108, G_CALL, "e813e5eeff", "", 52, -1, &k_ui_image, 0x16b108, 5},
    {"g2b_ui_slot_paint_b", "client", 0x16b219, G_CALL, "e802e4eeff", "", 53, -1, &k_ui_image, 0x16b219, 5},
    {"g2b_ui_model_k", "client", 0x18b8cd, G_CALL, "e82e18f4ff", "", 54, -1, &k_ui_width, 0x18b8cd, 5},
    {"g2b_ui_barter_sel_paint", "client", 0x16d0f9, G_CALL, "e8b2f2eeff", "", 55, -1, &k_ui_image5, 0x16d0f9, 5},
    {"g2b_ui_money_text", "client", 0x5f4db, G_CALL, "e8b0000000", "", 56, -1, &k_ui_text_x, 0x5f4db, 5},
    {"g2b_view_fov", "client", 0x192069, G_JMP, "d94538d80df8582210", "", 34, 5, &k_view_fov, 0x192069, 9},
    {"g2b_camera_fov", "client", 0x6710d, G_JMP, "d99e40030000", "", 35, -1, &k_camera_fov, 0x67108, 11},
    {"g2b_hud", "client", 0x227ec8, G_DATA, "000000000000503f", "", 0, -1, NULL, 0x4bc53, 6},
};

#define NROWS (sizeof k_rows / sizeof *k_rows)

static const g2b_row *hud_row(void) { return &k_rows[NROWS - 1]; }

static int hud_write(void)
{
    const g2b_row *r = hud_row();
    uint32_t va = rt_hook_va(r->module, r->rva);
    rt_hook_revert(r->id);
    if (!s_a.on || !s_a.have || !s_a.keep || !va || s_a.w * 3 < s_a.h * 4) return 0;
    if (rt_hook_claimed(rt_hook_va(r->module, r->guard), r->glen)) {
        rt_log("aspect row %s owned by up", r->id);
        return 0;
    }
    uint8_t old[8], nw[8];
    unhex(r->old, old);
    double d = 1.0 / (768.0 * s_a.r);
    memcpy(nw, &d, 8);
    return rt_hook_patch(r->id, va, old, nw, 8) == RT_HOOK_WRITTEN;
}

static void configure(int w, int h, int on)
{
    if (w > 0 && h > 0) {
        s_a.w = w;
        s_a.h = h;
        s_a.r = (double)w / h;
    }
    s_a.on = on && s_a.have;
    s_a.hud = hud_write();
}

void rt_aspect_engine_set(int w, int h, int on)
{
    s_a.checked = 1;
    configure(w, h, on);
}

void rt_aspect_engine_init(int w, int h)
{
    s_a.have = s_a.keep = 0;
    int keep = 0, keeps = 0;
    for (size_t i = 0; i + 1 < NROWS; i++) {
        const g2b_row *r = &k_rows[i];
        int k = !strcmp(r->new_, STOCK_SCALE);
        keeps += k;
        uint32_t va = rt_hook_va(r->module, r->rva);
        if (!va) continue;
        if (rt_hook_claimed(rt_hook_va(r->module, r->guard), r->glen)) {
            rt_log("aspect row %s owned by up", r->id);
            continue;
        }
        uint8_t old[64], nw[64];
        uint32_t n = unhex(r->old, old);
        const uint8_t *p = rt_host(va);
        int st;
        if (r->kind == G_BYTES && r->absoff >= 0) {
            unhex(r->new_, nw);
            uint8_t cur[64];
            memcpy(cur, p, n);
            st = rt_hook_same(p, old, n, r->absoff)   ? RT_HOOK_ABSENT
                 : rt_hook_same(p, nw, n, r->absoff) ? rt_hook_patch(r->id, va, old, cur, n)
                                                     : -1;
        } else if (r->kind == G_BYTES) {
            unhex(r->new_, nw);
            st = !memcmp(p, old, n) ? RT_HOOK_ABSENT : rt_hook_patch(r->id, va, old, nw, n);
        } else if (r->kind == G_CELL) {
            unhex(r->new_, nw);
            st = rt_hook_same(p, old, CALL_LEN, r->absoff) ? RT_HOOK_ABSENT
                 : memcmp(p + CALL_LEN, nw + CALL_LEN, n - CALL_LEN) ? -1
                                                                      : rt_hook_site(r->id, va, old, CALL_LEN, r->absoff, 0xFF, r->cell, r->shim);
        } else
            st = rt_hook_site(r->id, va, old, n, r->absoff, r->kind == G_CALL ? 0xE8 : 0xE9, r->cell, r->shim);
        if (st < 0) rt_stop(5, va, "aspect row %s: bytes at 0x%08X match neither the stock nor the G2b build", r->id, va);
        rt_log("aspect row %s %s", r->id, st == RT_HOOK_BAKED ? "on (build)" : "not in this build");
        s_a.have |= st == RT_HOOK_BAKED;
        keep += k && st == RT_HOOK_BAKED;
    }
    s_a.keep = keeps && keep == keeps;
    configure(w, h, 1);
    s_a.checked = 0;
    rt_log("aspect engine %dx%d r %.4f fov %s hud %s", s_a.w, s_a.h, s_a.r, s_a.on ? "hor+" : "stock",
           rt_hook_claimed(rt_hook_va("client", hud_row()->rva), 8) ? "uniform" : "stock");}

static int s_bad;

static void erow(const char *c, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

static void erow(const char *c, const char *fmt, ...)
{
    char b[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    printf("m9 %s %s\n", c, b);
    fflush(stdout);
}

static const int k_sizes[][2] = {{1024, 768}, {1280, 1024}, {1920, 1080}, {3440, 1440}, {5120, 1440}};

#define NSIZES (sizeof k_sizes / sizeof *k_sizes)

static uint32_t s_persp[8], s_anam_vt;

static uint32_t rec_noop1(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t rec_noop0(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t rec_persp(const uint32_t *a)
{
    memcpy(s_persp, a, sizeof s_persp);
    return 0;
}

static uint32_t rec_anam(const uint32_t *a)
{
    (void)a;
    return s_anam_vt;
}

static const rt_shim k_rec_118 = {"m9", "TestMat118", 1, rec_noop1, RT_SHIM_REAL};
static const rt_shim k_rec_144 = {"m9", "TestMat144", 0, rec_noop0, RT_SHIM_REAL};
static const rt_shim k_rec_14c = {"m9", "TestMat14c", 8, rec_persp, RT_SHIM_REAL};
static const rt_shim k_rec_anam = {"m9", "TestAnam4", 0, rec_anam, RT_SHIM_REAL};

static uint32_t obj(uint32_t size) { return rt_heap_alloc(size, 1); }

static void guest_env(void)
{
    static int once;
    if (once) return;
    once = 1;
    rt_arena_use(rt_arena_new());
    rt_load();
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    rt_reset_cpu();
    rt_aspect_engine_init(1024, 768);
}

static double dbl(const uint32_t *p)
{
    double d;
    memcpy(&d, p, 8);
    return d;
}

static int case_aspect(void)
{
    uint32_t vt = obj(0x200), ms = obj(0x10), avt = obj(0x10), an = obj(0x40), self = obj(0x100);
    MEM32(vt + 0x118) = rt_dynamic_shim("m9", k_rec_118.name, &k_rec_118);
    MEM32(vt + 0x144) = rt_dynamic_shim("m9", k_rec_144.name, &k_rec_144);
    MEM32(vt + 0x14c) = rt_dynamic_shim("m9", k_rec_14c.name, &k_rec_14c);
    MEM32(avt + 4) = rt_dynamic_shim("m9", k_rec_anam.name, &k_rec_anam);
    MEM32(ms) = vt;
    MEM32(an) = avt;
    MEM32(rt_hook_va("engine", 0xa6c014)) = ms;
    MEM32(rt_hook_va("engine", 0xa6ca8c)) = an;
    uint32_t fn = rt_hook_va("engine", 0x7a3e0);
    for (size_t k = 0; k < NSIZES; k++)
        for (int anam = 0; anam < 2; anam++)
            for (int flag = 0; flag < 2; flag++) {
                rt_aspect_engine_set(k_sizes[k][0], k_sizes[k][1], 1);
                MEM32(an + 0x2c) = (uint32_t)anam;
                memset(s_persp, 0, sizeof s_persp);
                uint32_t args[4] = {fbits(75.0f), fbits(4.0f), fbits(4096.0f), (uint32_t)flag};
                g_ecx = self;
                rt_guest_call(fn, 4, args);
                double got = dbl(s_persp + 2), want = flag ? 1.0 : (double)k_sizes[k][0] / k_sizes[k][1];
                int ok = fabs(got - want) <= 1e-6 * want;
                s_bad |= !ok;
                erow("engine-aspect", "size=%dx%d anamorphic=%d flag=%d aspect=%.7f want=%.7f ok=%d", k_sizes[k][0], k_sizes[k][1], anam,
                     flag, got, want, ok);
            }
    return 0;
}

static int case_fov(void)
{
    uint32_t c34 = MEM32(RT_EXE_BASE + 0x800u + 4u * 34), c35 = MEM32(RT_EXE_BASE + 0x800u + 4u * 35);
    uint32_t view = obj(0x80), cam = obj(0x400);
    static const float k_in[] = {75.0f, 45.0f};
    uint8_t site = *(uint8_t *)rt_host(rt_hook_va("client", 0x191fd5));
    int anam_skip = site == 0xEB;
    s_bad |= !anam_skip;
    erow("engine-fov", "anamorphic-multiply site=0x%02X skipped=%d", site, anam_skip);
    for (size_t k = 0; k < NSIZES; k++)
        for (size_t f = 0; f < 2; f++) {
            rt_aspect_engine_set(k_sizes[k][0], k_sizes[k][1], 1);
            double r = (double)k_sizes[k][0] / k_sizes[k][1], in = k_in[f];
            pfloat(view + 0x38, k_in[f]);
            pfloat(view + 0x3c, k_in[f]);
            pfloat(cam + 0x340, k_in[f]);
            if (c34) rt_guest_call(c34, 1, &view);
            if (c35) rt_guest_call(c35, 1, &cam);
            double vo = gfloat(view + 0x38), co = gfloat(cam + 0x340), want = rt_aspect_up_hfov(in, r);
            if (c34) rt_guest_call(c34, 1, &view);
            if (c35) rt_guest_call(c35, 1, &cam);
            int same = gfloat(view + 0x38) == (float)vo && gfloat(view + 0x3c) == (float)vo && gfloat(cam + 0x340) == (float)co;
            s_bad |= !same;
            erow("engine-fov", "size=%dx%d in=%.2f twice view=%.4f camera=%.4f same=%d", k_sizes[k][0], k_sizes[k][1], in, gfloat(view + 0x38),
                 gfloat(cam + 0x340), same);
            double vv = rt_aspect_fovy(vo, r), v43 = rt_aspect_fovy(in, 4.0 / 3.0);
            int ok = fabs(vo - want) <= 0.01 && fabs(co - want) <= 0.01 && fabs(gfloat(view + 0x3c) - want) <= 0.01 && fabs(vv - v43) <= 0.01 &&
                     (k_sizes[k][0] * 3 != k_sizes[k][1] * 4 || fabs(vo - in) <= 1e-4);
            s_bad |= !ok;
            erow("engine-fov", "size=%dx%d in=%.2f view=%.4f camera=%.4f want=%.4f vfov=%.4f vfov43=%.4f tan=%.6f ok=%d", k_sizes[k][0],
                 k_sizes[k][1], in, vo, co, want, vv, v43, tan(vo * 3.14159265358979323846 / 360.0), ok);
        }
    return 0;
}

static int case_hud(void)
{
    uint32_t self = obj(0x200), fn = rt_hook_va("client", 0x4bc40), sw = rt_hook_va("client", 0x4a5830);
    for (size_t k = 0; k < NSIZES; k++) {
        rt_aspect_engine_set(k_sizes[k][0], k_sizes[k][1], 1);
        MEM32(sw) = (uint32_t)k_sizes[k][0];
        MEM32(sw + 4) = (uint32_t)k_sizes[k][1];
        g_ecx = self;
        rt_guest_call(fn, 0, NULL);
        int wp = (int)MEM32(self + 0x180), hp = (int)MEM32(self + 0x184);
        double ratio = hp ? (double)wp / hp : 0;
        int narrow = k_sizes[k][0] * 3 < k_sizes[k][1] * 4;
        double wantr = narrow ? 160.0 / 125.0 : 128.0 / 94.0;
        int ok = fabs(ratio / wantr - 1.0) <= 0.005 && (k || (wp == 128 && hp == 94));
        s_bad |= !ok;
        erow("engine-hud", "size=%dx%d width=%d height=%d ratio=%.4f want=%.4f ok=%d", k_sizes[k][0], k_sizes[k][1], wp, hp, ratio, wantr, ok);
        double d;
        memcpy(&d, rt_host(rt_hook_va("client", 0x227ec8)), 8);
        double edge = 1024.0 * d * k_sizes[k][0], r = (double)k_sizes[k][0] / k_sizes[k][1];
        int dok = narrow ? d == 1.0 / 1024.0 && edge <= k_sizes[k][0] + 1e-6 : fabs(d * 768.0 * r - 1.0) <= 1e-12 && edge <= k_sizes[k][0] + 1e-6;
        s_bad |= !dok;
        erow("engine-hud", "size=%dx%d hud=%.10f edge=%.2f narrow=%d ok=%d", k_sizes[k][0], k_sizes[k][1], d, edge, narrow, dok);
    }
    double hud;
    memcpy(&hud, rt_host(rt_hook_va("client", 0x227ec8)), 8);
    int n = 0;
    for (size_t i = 0; i < NROWS; i++) {
        const g2b_row *r = &k_rows[i];
        if (strcmp(r->new_, STOCK_SCALE)) continue;
        uint32_t g = rt_hook_va(r->module, r->rva), gd = MEM32(g + 2);
        int gok = MEM8(g) == 0xD8 && gfloat(gd) == 1.0f / 1024.0f && gd != rt_hook_va("client", 0x227ec8);
        s_bad |= !gok;
        n++;
        erow("engine-hud", "stock-scale %s site=0x%08X operand=0x%08X value=%.10f hud=%.10f ok=%d", r->id, g, gd, gfloat(gd), hud, gok);
    }
    s_bad |= n != 1;
    erow("engine-hud", "stock-scale rows=%d want=1", n);
    return 0;
}

static struct {
    uint32_t va, a[4], ret;
    int n, na;
} s_rec;

static uint32_t rec_call(uint32_t va, int n, const uint32_t *args)
{
    s_rec.va = va;
    s_rec.na = n;
    for (int i = 0; i < 4; i++) s_rec.a[i] = i < n ? args[i] : 0;
    s_rec.n++;
    return s_rec.ret;
}

static int case_ui(void)
{
    static const int k_all[][2] = {{640, 480}, {800, 600}, {1024, 768}, {1600, 1200}, {1280, 1024}, {1920, 1080}, {2560, 1080}, {3440, 1440}, {5120, 1440}};
    uint32_t va_w = rt_hook_va("client", 0xcd100), va_b = rt_hook_va("client", 0x1a9950), va_i = rt_hook_va("client", 0x59620);
    s_gcall = rec_call;
    for (size_t k = 0; k < sizeof k_all / sizeof *k_all; k++) {
        int w = k_all[k][0], h = k_all[k][1];
        rt_aspect_engine_set(w, h, 1);
        int wide = w * 3 > h * 4;
        s_rec.ret = (uint32_t)w;
        s_rec.n = 0;
        int uw = (int)ui_width(NULL);
        int w_stock = s_rec.n == 1 && s_rec.va == va_w && s_rec.na == 0 && uw == w;
        int w_pill = s_rec.n == 0 && abs(uw * 3 - h * 4) <= 2;
        int w_ok = wide ? w_pill && s_a.hud : w_stock;
        int fw = wide ? uw : w, fh = h;
        uint32_t fr[4] = {0, 0, (uint32_t)fw, (uint32_t)fh}, qy[4] = {0, 90, (uint32_t)fw, (uint32_t)fh - 180}, kx[4] = {40, 0, (uint32_t)fw, (uint32_t)fh},
                 full[4] = {0, 0, (uint32_t)w, (uint32_t)h};
        s_rec.n = 0;
        ui_frame(fr);
        int x0 = (int)s_rec.a[0], f_ok = s_rec.va == va_b && s_rec.na == 4 && (int)s_rec.a[2] == fw && (int)s_rec.a[3] == fh;
        ui_frame(qy);
        int qx = (int)s_rec.a[0], q_ok = (int)s_rec.a[1] == 90;
        ui_frame(kx);
        int k_ok = (int)s_rec.a[0] == 40;
        ui_frame(full);
        int full_ok = (int)s_rec.a[0] == 0;
        uint32_t rt[4] = {(uint32_t)(w - fw), 0, (uint32_t)fw, (uint32_t)fh};
        ui_frame(rt);
        int rt_ok = wide ? abs((int)s_rec.a[0] - x0) <= 1 : (int)s_rec.a[0] == 0;
        uint32_t bx[4] = {768, 101, 256, 512};
        ui_image(bx);
        int b_ok = (int)s_rec.a[0] == 768 + (wide ? (w - uw) / 2 : 0) && (int)s_rec.a[1] == 101 && (int)s_rec.a[2] == 256 && (int)s_rec.a[3] == 512 && s_rec.va == va_i;
        int cw = 300, cx = (uw - cw) / 2 + (wide ? (w - uw) / 2 : 0);
        int ok = w_ok && f_ok && q_ok && k_ok && full_ok && rt_ok && b_ok && s_rec.n == 6;
        uint32_t sx[5] = {200, 300, 128, 64, 0x1000};
        ui_image5(sx);
        ok = ok && (int)s_rec.a[0] == 200 + (wide ? (w - uw) / 2 : 0) && s_rec.a[1] == 300 && s_rec.a[2] == 128 && s_rec.a[3] == 64 && s_rec.na == 5 &&
             s_rec.va == rt_hook_va("client", 0x5c3b0);
        uint32_t slot = rt_hook_va("client", 0x3eede4), *sp = rt_host(slot), keep = *sp, tx[3] = {7, 0x2000, slot};
        *sp = 464;
        ui_text_x(tx);
        ok = ok && (int)*sp == 464 + (wide ? (w - uw) / 2 : 0) && s_rec.na == 3 && s_rec.a[2] == slot && s_rec.va == rt_hook_va("client", 0x5f590);
        *sp = keep;
        if (wide) ok = ok && abs(w - fw - 2 * x0) <= 1 && qx == x0 && abs(cx - (w - cw) / 2) <= 1;
        else ok = ok && x0 == 0 && qx == 0;
        s_bad |= !ok;
        erow("engine-ui", "size=%dx%d wide=%d width=%d frame_x=%d child_x=%d hud=%d ok=%d", w, h, wide, uw, x0, cx, s_a.hud, ok);
    }
    s_gcall = rt_guest_call;
    s_rec.ret = 0;
    rt_aspect_engine_set(1024, 768, 1);
    return 0;
}

static int case_load(void)
{
    static const int k_all[][2] = {{640, 480}, {800, 600}, {1024, 768}, {1600, 1200}, {1280, 1024}, {1920, 1080}, {2560, 1080}, {3440, 1440}, {5120, 1440}};
    uint32_t va_size = rt_hook_va("client", 0x1a98f0), va_pos = rt_hook_va("client", 0x1a9890);
    s_gcall = rec_call;
    for (size_t k = 0; k < sizeof k_all / sizeof *k_all; k++) {
        int w = k_all[k][0], h = k_all[k][1];
        rt_aspect_engine_set(w, h, 1);
        uint32_t full[2] = {(uint32_t)w, (uint32_t)h}, zero[2] = {0, 0}, odd[2] = {(uint32_t)w - 1, (uint32_t)h}, mv[2] = {5, 0};
        s_rec.n = 0;
        load_size(full);
        int gw = (int)s_rec.a[0], gh = (int)s_rec.a[1], sz_ok = s_rec.va == va_size;
        load_pos(zero);
        int gx = (int)s_rec.a[0], gy = (int)s_rec.a[1], pos_ok = s_rec.va == va_pos;
        load_size(odd);
        int odd_ok = (int)s_rec.a[0] == w - 1;
        load_pos(mv);
        int mv_ok = s_rec.a[0] == 5;
        int wide = w * 3 > h * 4;
        double d;
        memcpy(&d, rt_host(rt_hook_va("client", 0x227ec8)), 8);
        int ok = s_rec.n == 4 && sz_ok && pos_ok && odd_ok && mv_ok && gh == h && gy == 0;
        if (wide) ok = ok && s_a.hud && abs(gw * 3 - h * 4) <= 2 && abs(w - gw - 2 * gx) <= 1 && gx > 0 && fabs(gw / 1024.0 - w * d) < 1.0 / 1024.0;
        else ok = ok && gw == w && gx == 0;
        s_bad |= !ok;
        erow("engine-load", "size=%dx%d wide=%d panel=%dx%d at=%d,%d hud=%d ok=%d", w, h, wide, gw, gh, gx, gy, s_a.hud, ok);
    }
    s_gcall = rt_guest_call;
    rt_aspect_engine_set(1024, 768, 1);
    return 0;
}

static int case_rows(void)
{
    const char *id;
    uint32_t va;
    int st, seen = 0;
    for (uint32_t i = 0; rt_hook_info(i, &id, &va, &st); i++)
        if (!strncmp(id, "g2b_", 4)) {
            seen++;
            erow("engine-rows", "row=%s va=0x%08X state=%d", id, va, st);
        }
    int want = s_a.have ? (int)NROWS : 0;
    s_bad |= seen != want;
    erow("engine-rows", "rows=%d want=%d built=%d", seen, want, s_a.have);
    if (!s_a.have) return 0;
    rt_hook_revert_all();
    const g2b_row *r = &k_rows[1];
    uint32_t sv = rt_hook_va(r->module, 0x7a44e);
    uint8_t cur[4];
    memcpy(cur, rt_host(sv), 4);
    rt_hook_patch("up_fake_auto_ar", sv, cur, cur, 4);
    rt_aspect_engine_init(1920, 1080);
    int bound = 0, skipped = 1;
    for (uint32_t i = 0; rt_hook_info(i, &id, &va, &st); i++) {
        bound += !strncmp(id, "g2b_", 4);
        skipped &= strcmp(id, r->id) != 0;
    }
    s_bad |= bound != (int)NROWS - 1 || !skipped;
    erow("engine-rows", "claimed=%s bound=%d want=%d skipped=%d", r->id, bound, (int)NROWS - 1, skipped);
    rt_hook_revert_all();
    rt_aspect_engine_init(1024, 768);
    return 0;
}

int rt_g2b_selftest(const char *name)
{
    guest_env();
    s_bad = 0;
    if (!strcmp(name, "engine-aspect")) case_aspect();
    else if (!strcmp(name, "engine-fov")) case_fov();
    else if (!strcmp(name, "engine-hud")) case_hud();
    else if (!strcmp(name, "engine-load")) case_load();
    else if (!strcmp(name, "engine-ui")) case_ui();
    else if (!strcmp(name, "engine-rows")) case_rows();
    else return 2;
    erow(name, "result bad=%d built=%d", s_bad, s_a.have);
    return s_bad ? 1 : 0;
}
