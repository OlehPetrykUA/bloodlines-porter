#include "recomp_types.h"
#include "rt.h"

#include <string.h>

static struct {
    int door, hist;
    uint32_t door_obj, hist_busy;
} s_pl;

uint32_t (*rt_up_gc)(uint32_t, int, const uint32_t *) = rt_guest_call;

static uint32_t vp(uint32_t rva) { return rt_hook_va("vampire", rva); }

static int pon(const char *plugin, const char *key) { return rt_up_plugin_on(plugin, "Enabled") && rt_up_plugin_on(plugin, key); }

uint32_t rt_up_call_this(uint32_t fn, uint32_t self, int n, const uint32_t *args)
{
    uint32_t keep = g_ecx;
    g_ecx = self;
    uint32_t r = rt_up_gc(fn, n, args);
    g_ecx = keep;
    return r;
}

#define call_this rt_up_call_this

void rt_up_plugins_load(void)
{
    s_pl.door = pon("vtmb-tweaks", "bFixDoorsAtHighFPS");
    s_pl.hist = pon("history-stat-reset-fix", "Enabled");
    s_pl.door_obj = 0;
    rt_up_model_load();
    rt_up_console_load();
    rt_up_tweaks_load();
    rt_up_container_load();
}

static float as_f(uint32_t v)
{
    float f;
    memcpy(&f, &v, 4);
    return f;
}

static uint32_t as_u(float f)
{
    uint32_t v;
    memcpy(&v, &f, 4);
    return v;
}

static uint32_t hook_door(const uint32_t *a)
{
    uint32_t self = g_ecx, args[2] = {a[0], a[1]};
    if (s_pl.door && MEM32(self) == vp(0x454b94)) {
        if (!s_pl.door_obj) s_pl.door_obj = MEM32(vp(0x70b228));
        if (s_pl.door_obj && as_f(MEM32(s_pl.door_obj + 0x10)) > 0.0f) {
            volatile float step = 1.0f / as_f(MEM32(s_pl.door_obj + 0x10));
            volatile float per = step / 60.0f;
            args[1] = as_u(per * as_f(a[1]));
        }
    }
    return call_this(vp(0x1c17e0), self, 2, args);
}

static uint32_t hist_find(uint32_t obj)
{
    uint32_t n = MEM32(obj + 0x13bc), t = MEM32(obj + 0x13c0);
    if (!t || n - 1 >= 0x20) return 0;
    for (; n; n--, t += 4) {
        uint32_t e = MEM32(t);
        if (e && !MEM32(e + 0x10) && MEM32(e + 4) == 0x23) return e;
    }
    return 0;
}

static void hist_reset(uint32_t obj)
{
    uint32_t first = hist_find(obj);
    if (!first || s_pl.hist_busy) return;
    uint32_t stat = 0x22, two[2], one;
    uint32_t keep = call_this(vp(0x48e5), first, 1, &stat);
    uint32_t cur = call_this(vp(0x40d9), obj, 0, NULL);
    s_pl.hist_busy = obj;
    two[0] = cur;
    two[1] = obj;
    call_this(vp(0x11004), vp(0x74f028), 2, two);
    s_pl.hist_busy = 0;
    uint32_t second = hist_find(obj);
    if (second) {
        two[0] = stat;
        two[1] = keep;
        call_this(vp(0x11e46), second, 2, two);
    }
    uint32_t client = rt_hook_va("client", 0);
    if (!client) return;
    uint32_t p = MEM32(client + 0x5fb14c);
    if (p && MEM32(p + 0x274) && MEM32(p + 0xedc)) {
        one = cur;
        call_this(client + 0x17d930, MEM32(p + 0xedc), 1, &one);
    }
}

static uint32_t hist_epilogue(uint32_t arg)
{
    uint32_t eax = g_eax, self = g_esi, extra = arg == 0 ? g_edi : arg;
    if (!s_pl.hist) return eax;
    hist_reset(self);
    return call_this(vp(0x5024), self, 1, &extra);
}

static uint32_t hook_hist_a(const uint32_t *a)
{
    (void)a;
    return hist_epilogue(0);
}

static uint32_t hook_hist_c(const uint32_t *a)
{
    (void)a;
    return hist_epilogue(0xFFFFFFFFu);
}

static uint32_t hook_hist_b(const uint32_t *a)
{
    (void)a;
    if (!s_pl.hist || g_ebx != g_ebp || s_pl.hist_busy != g_esi) return 0;
    s_pl.hist_busy = 0;
    return 1;
}

const rt_shim rt_upk_door = {"up", "DoorFps", 2, hook_door, RT_SHIM_REAL};
const rt_shim rt_upk_hist_a = {"up", "HistStatA", 0, hook_hist_a, RT_SHIM_REAL};
const rt_shim rt_upk_hist_b = {"up", "HistStatB", 0, hook_hist_b, RT_SHIM_REAL};
const rt_shim rt_upk_hist_c = {"up", "HistStatC", 0, hook_hist_c, RT_SHIM_REAL};

typedef struct {
    uint32_t va, ecx, n, a0, a1;
} rec_call;

static rec_call s_rec[16];
static uint32_t s_nrec;

static uint32_t s_swap_tab, s_swap_ent;

static uint32_t record(uint32_t va, int n, const uint32_t *a)
{
    if (s_nrec < 16) s_rec[s_nrec++] = (rec_call){va, g_ecx, (uint32_t)n, n > 0 ? a[0] : 0, n > 1 ? a[1] : 0};
    if (s_swap_tab && va == vp(0x11004)) MEM32(s_swap_tab) = s_swap_ent;
    return va == vp(0x48e5) ? 0xAA : va == vp(0x40d9) ? 0xBB : 0x77;
}

static void set_gc(uint32_t (*f)(uint32_t, int, const uint32_t *))
{
    rt_up_gc = f;
    s_nrec = 0;
}

static int rec_is(uint32_t i, uint32_t va, uint32_t ecx, uint32_t n, uint32_t a0, uint32_t a1)
{
    return i < s_nrec && s_rec[i].va == va && s_rec[i].ecx == ecx && s_rec[i].n == n && s_rec[i].a0 == a0 && s_rec[i].a1 == a1;
}

static void door_cases(void)
{
    uint32_t vt = vp(0x454b94), og = vp(0x70b228), save_og = MEM32(og), obj = rt_heap_alloc(0x40, 1), door = rt_heap_alloc(0x40, 1);
    float dt = 1.0f / 30.0f;
    MEM32(obj + 0x10) = as_u(dt);
    MEM32(og) = obj;
    MEM32(door) = vt;
    s_pl.door_obj = 0;
    s_pl.door = 1;
    uint32_t a[2] = {5, as_u(0.25f)};
    set_gc(record);
    g_ecx = door;
    hook_door(a);
    float want = (1.0f / dt / 60.0f) * 0.25f;
    int scaled = s_nrec == 1 && rec_is(0, vp(0x1c17e0), door, 2, 5, as_u(want)) && want > 0.12f && want < 0.13f;
    MEM32(door) = vt + 4;
    set_gc(record);
    g_ecx = door;
    hook_door(a);
    int other = rec_is(0, vp(0x1c17e0), door, 2, 5, as_u(0.25f));
    MEM32(door) = vt;
    uint32_t bad[3] = {0, 0x80000000u, 0x7fc00000u};
    int zero = 1;
    for (int i = 0; i < 3; i++) {
        MEM32(obj + 0x10) = bad[i];
        s_pl.door_obj = 0;
        set_gc(record);
        g_ecx = door;
        hook_door(a);
        zero = zero && rec_is(0, vp(0x1c17e0), door, 2, 5, as_u(0.25f));
    }
    MEM32(obj + 0x10) = as_u(dt);
    s_pl.door = 0;
    set_gc(record);
    g_ecx = door;
    hook_door(a);
    int off = rec_is(0, vp(0x1c17e0), door, 2, 5, as_u(0.25f));
    rt_up_check(scaled, "door hook scales the step by 1 / frame time / 60 for the door class", "calls %u", s_nrec);
    rt_up_check(other && off, "door hook passes the step through for another class and with the switch off", "other %d off %d", other, off);
    rt_up_check(zero, "door hook passes the step through when the frame time is zero, negative zero or NaN", "zero %d", zero);
    set_gc(rt_guest_call);
    MEM32(og) = save_og;
    s_pl.door_obj = 0;
    rt_heap_free(obj);
    rt_heap_free(door);
}

static void hist_cases(void)
{
    uint32_t obj = rt_heap_alloc(0x1400, 1), tab = rt_heap_alloc(16, 1), ent = rt_heap_alloc(0x40, 1);
    uint32_t cl = rt_hook_va("client", 0), cp = MEM32(cl + 0x5fb14c), cpv = rt_heap_alloc(0x1000, 1);
    uint32_t ent2 = rt_heap_alloc(0x40, 1), keep_ecx = g_ecx;
    MEM32(obj + 0x13bc) = 1;
    MEM32(obj + 0x13c0) = tab;
    MEM32(tab) = ent;
    MEM32(ent + 0x10) = 0;
    MEM32(ent + 4) = 0x23;
    MEM32(ent2 + 0x10) = 0;
    MEM32(ent2 + 4) = 0x23;
    s_swap_tab = tab;
    s_swap_ent = ent2;
    MEM32(cl + 0x5fb14c) = cpv;
    MEM32(cpv + 0x274) = 1;
    MEM32(cpv + 0xedc) = 0x5151;
    s_pl.hist = 1;
    s_pl.hist_busy = 0;
    set_gc(record);
    g_ecx = 0xECEC;
    g_esi = obj;
    g_edi = 0x99;
    g_eax = 0x1234;
    uint32_t ret = hook_hist_a(NULL);
    int order = s_nrec == 6 && rec_is(0, vp(0x48e5), ent, 1, 0x22, 0) && rec_is(1, vp(0x40d9), obj, 0, 0, 0) &&
                rec_is(2, vp(0x11004), vp(0x74f028), 2, 0xBB, obj) && rec_is(3, vp(0x11e46), ent2, 2, 0x22, 0xAA) &&
                rec_is(4, cl + 0x17d930, 0x5151, 1, 0xBB, 0) && rec_is(5, vp(0x5024), obj, 1, 0x99, 0) && ret == 0x77 && !s_pl.hist_busy;
    rt_up_check(order, "history stat hook A runs the reset calls in order, the second scan using the entry the reset left, then the epilogue call with edi",
                "calls %u ret 0x%X", s_nrec, ret);
    s_swap_tab = 0;
    MEM32(tab) = ent;
    set_gc(record);
    g_esi = obj;
    hook_hist_c(NULL);
    int minus = s_nrec == 6 && rec_is(5, vp(0x5024), obj, 1, 0xFFFFFFFFu, 0);
    MEM32(ent + 4) = 0x24;
    set_gc(record);
    g_esi = obj;
    hook_hist_c(NULL);
    int nomatch = s_nrec == 1 && rec_is(0, vp(0x5024), obj, 1, 0xFFFFFFFFu, 0);
    MEM32(ent + 4) = 0x23;
    s_pl.hist = 0;
    set_gc(record);
    g_esi = obj;
    hook_hist_a(NULL);
    int off = s_nrec == 0;
    rt_up_check(minus && nomatch && off, "history stat hook C passes -1, skips the reset without a type-0x23 entry and does nothing when off",
                "minus %d nomatch %d off %d", minus, nomatch, off);
    s_pl.hist = 1;
    s_pl.hist_busy = 0;
    g_ebx = 7;
    g_ebp = 7;
    g_esi = obj;
    int idle = hook_hist_b(NULL) == 0;
    s_pl.hist_busy = obj;
    uint32_t hit = hook_hist_b(NULL);
    int cleared = !s_pl.hist_busy;
    s_pl.hist_busy = obj;
    g_ebp = 8;
    int differ = hook_hist_b(NULL) == 0 && s_pl.hist_busy == obj;
    g_ebp = 7;
    g_esi = obj + 4;
    int other = hook_hist_b(NULL) == 0 && s_pl.hist_busy == obj;
    g_esi = obj;
    s_pl.hist = 0;
    int off_b = hook_hist_b(NULL) == 0 && s_pl.hist_busy == obj;
    rt_up_check(idle && hit == 1 && cleared && differ && other && off_b, "history stat hook B overrides the compare once, only mid-reset on the same object",
                "idle %d hit %u cleared %d differ %d other %d off %d", idle, hit, cleared, differ, other, off_b);
    set_gc(rt_guest_call);
    s_pl.hist_busy = 0;
    MEM32(cl + 0x5fb14c) = cp;
    rt_heap_free(obj);
    rt_heap_free(tab);
    rt_heap_free(ent);
    rt_heap_free(ent2);
    g_ecx = keep_ecx;
    rt_heap_free(cpv);
}

void rt_up_plugins_selftest(void)
{
    rt_up_plugins_reset();
    rt_up_plugins_load();
    door_cases();
    hist_cases();
    rt_up_model_selftest();
    rt_intrinsic_selftest();
    rt_kernel32_text_selftest();
    rt_up_console_selftest();
    rt_up_tweaks_selftest();
    rt_up_container_selftest();
    rt_up_plugins_reset();
    rt_up_plugins_load();
}
