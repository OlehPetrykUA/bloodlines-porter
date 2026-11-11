#include "recomp_types.h"
#include "rt.h"

#include <string.h>

enum {
    V_DOOR_VT = 0x454b94, V_GLOBAL = 0x566458, V_CLOSE_FN = 0x5e0c, V_FIND_FN = 0x151b8, V_PICK_FN = 0x138d6, V_CHECK_FN = 0x7e19,
    V_SLOT_FN = 0x100b4, V_DROP_FN = 0x32d0c0, V_LOOKUP_FN = 0x9c8c, V_CORPSE_VT = 0x4c558c, V_THINK_FN = 0x11667, V_MARK_FN = 0x7fe5,
    V_CLEAR_FN = 0xb802, V_GPGLOBALS = 0x70b228, V_FADE_DELAY = 0x44e664, CORPSE_RING = 0x800,
    E_KEY_ALT = 0xd60160, E_KEY_F4 = 0xd60178, E_CBUF_FN = 0x2dcc0, E_KEY_EPILOGUE = 0xa3bc7, E_KEY_CAVE = 0xa3c41
};

static struct {
    int nodoor, drop, stack, corpse, altf4;
    uint32_t ring[CORPSE_RING], next, str_exit;
} s_t;

static uint32_t vp(uint32_t rva) { return rt_hook_va("vampire", rva); }

static int pon(const char *key) { return rt_up_plugin_on("vtmb-tweaks", "Enabled") && rt_up_plugin_on("vtmb-tweaks", key); }

void rt_up_tweaks_load(void)
{
    s_t.nodoor = pon("bNoDoorAutoClose");
    s_t.drop = pon("bNPCsDropAllWeapons");
    s_t.stack = pon("bStackableWeapons");
    s_t.corpse = pon("bNoCorpseDespawn");
    s_t.altf4 = pon("bEnableAltF4");
}

static uint32_t hook_altf4(const uint32_t *a)
{
    (void)a;
    uint32_t e = rt_hook_va("engine", 0);
    if (!s_t.altf4 || !MEM32(e + E_KEY_ALT) || !MEM32(e + E_KEY_F4)) return e + E_KEY_CAVE;
    if (!s_t.str_exit) s_t.str_exit = rt_up_gnew("exit\n");
    rt_up_gc(e + E_CBUF_FN, 1, &s_t.str_exit);
    return e + E_KEY_EPILOGUE;
}

const rt_shim rt_upk_altf4 = {"up", "AltF4", 0, hook_altf4, RT_SHIM_REAL};

void rt_engine_exit(void)
{
    if (!s_t.str_exit) s_t.str_exit = rt_up_gnew("exit\n");
    rt_up_gc(rt_hook_va("engine", 0) + E_CBUF_FN, 1, &s_t.str_exit);
}

static uint32_t vcall(uint32_t obj, uint32_t off, int n, const uint32_t *a) { return rt_up_call_this(MEM32(MEM32(obj) + off), obj, n, a); }

static int door_free(uint32_t obj)
{
    if (MEM32(obj) != vp(V_DOOR_VT)) return 1;
    uint32_t arg = obj + 0x53c, held = rt_up_call_this(vp(V_LOOKUP_FN), MEM32(vp(V_GLOBAL)), 1, &arg);
    return !held || (uint8_t)vcall(held, 0x288, 0, NULL) == 0;
}

static uint32_t hook_nodoor(const uint32_t *a)
{
    (void)a;
    uint32_t self = g_esi, one = 1;
    if (!s_t.nodoor || door_free(self)) vcall(self, 0x3d0, 1, &one);
    return 0;
}

static uint32_t hook_drop(const uint32_t *a)
{
    (void)a;
    uint32_t self = g_ecx;
    if (!s_t.drop) return rt_up_call_this(vp(V_CLOSE_FN), self, 0, NULL);
    for (uint32_t i = 0; i < 0xe0; i++) {
        uint32_t w = rt_up_call_this(vp(V_SLOT_FN), self, 1, &i), d[3] = {w, 0, 0};
        if (w) rt_up_call_this(vp(V_DROP_FN), self, 3, d);
    }
    return 0;
}

static uint32_t hook_stack_find(const uint32_t *a)
{
    uint32_t self = g_ecx, both[2] = {a[0], a[1]}, o = rt_up_call_this(vp(V_FIND_FN), self, 2, both);
    if (s_t.stack && o && (uint8_t)vcall(o, 0x5cc, 0, NULL) != 0) return 0;
    return o;
}

static uint32_t hook_stack_pick(const uint32_t *a)
{
    uint32_t self = g_ecx, zero = 0;
    if (!s_t.stack) return rt_up_call_this(vp(V_PICK_FN), self, 1, a);
    if (rt_up_call_this(vp(V_CHECK_FN), self, 0, NULL) == 0) return rt_up_call_this(vp(V_PICK_FN), self, 1, &zero);
    return 1;
}

static uint32_t hook_stack_skip(const uint32_t *a)
{
    (void)a;
    return !s_t.stack || !((uint8_t)vcall(g_esi, 0x5cc, 0, NULL) && (int32_t)MEM32(g_esi + 0x8d0) > 1);
}

static uint32_t hook_corpse_think(const uint32_t *a)
{
    (void)a;
    if (s_t.corpse && MEM32(g_ecx) != vp(V_CORPSE_VT)) return vp(V_CORPSE_VT);
    return rt_up_call_this(vp(V_THINK_FN), g_ecx, 0, NULL);
}

static uint32_t hook_corpse_fade(const uint32_t *a)
{
    (void)a;
    uint32_t self = g_ecx;
    if (!s_t.corpse) return 0;
    for (uint32_t i = 0; i < CORPSE_RING; i++) {
        if (s_t.ring[i] != self) continue;
        float now, delay, at;
        uint32_t now_u = MEM32(MEM32(vp(V_GPGLOBALS)) + 0xc), delay_u = MEM32(vp(V_FADE_DELAY));
        memcpy(&now, &now_u, 4);
        memcpy(&delay, &delay_u, 4);
        at = (float)((double)now + (double)delay);
        memcpy(&now_u, &at, 4);
        MEM32(self + 0x17c) = now_u;
        return vp(0);
    }
    return 0;
}

static uint32_t hook_corpse_mark(const uint32_t *a)
{
    uint32_t eax = g_eax, self = g_ecx, args[3] = {a[0], a[1], a[2]};
    if (!s_t.corpse) return rt_up_call_this(vp(V_MARK_FN), self, 3, args);
    s_t.ring[s_t.next] = self;
    s_t.next = (s_t.next + 1) & (CORPSE_RING - 1);
    rt_up_call_this(vp(V_MARK_FN), self, 3, args);
    return eax;
}

static uint32_t hook_corpse_clear(const uint32_t *a)
{
    (void)a;
    if (s_t.corpse) {
        memset(s_t.ring, 0, sizeof s_t.ring);
        s_t.next = 0;
    }
    return rt_up_call_this(vp(V_CLEAR_FN), g_ecx, 0, NULL);
}

const rt_shim rt_upk_corpse_think = {"up", "CorpseThink", 0, hook_corpse_think, RT_SHIM_REAL};
const rt_shim rt_upk_corpse_fade = {"up", "CorpseFade", 0, hook_corpse_fade, RT_SHIM_REAL};
const rt_shim rt_upk_corpse_mark = {"up", "CorpseMark", 3, hook_corpse_mark, RT_SHIM_REAL};
const rt_shim rt_upk_corpse_clear = {"up", "CorpseClear", 0, hook_corpse_clear, RT_SHIM_REAL};

const rt_shim rt_upk_nodoor = {"up", "NoDoorAutoClose", 0, hook_nodoor, RT_SHIM_REAL};
const rt_shim rt_upk_drop = {"up", "NpcDropAll", 0, hook_drop, RT_SHIM_REAL};
const rt_shim rt_upk_stack_find = {"up", "StackFind", 2, hook_stack_find, RT_SHIM_REAL};
const rt_shim rt_upk_stack_pick = {"up", "StackPick", 1, hook_stack_pick, RT_SHIM_REAL};
const rt_shim rt_upk_stack_skip = {"up", "StackSkip", 0, hook_stack_skip, RT_SHIM_REAL};

typedef struct {
    uint32_t fn, ecx, n, a0, a1;
} t_rec;

static t_rec s_tr[0x120];
static uint32_t s_ntr, s_tret_lookup, s_tret_find, s_tret_check, s_tret_vc;

static uint32_t t_fake(uint32_t fn, int n, const uint32_t *a)
{
    if (s_ntr < 0x120) s_tr[s_ntr++] = (t_rec){fn, g_ecx, (uint32_t)n, n > 0 ? a[0] : 0, n > 1 ? a[1] : 0};
    if (fn == vp(V_SLOT_FN)) return a[0] == 3 ? 0x77 : 0;
    if (fn == vp(V_LOOKUP_FN)) return s_tret_lookup;
    if (fn == vp(V_FIND_FN)) return s_tret_find;
    if (fn == vp(V_CHECK_FN)) return s_tret_check;
    if (fn >> 12 == 0xB) return s_tret_vc;
    return 0x66;
}

static int t_is(uint32_t i, uint32_t fn, uint32_t ecx, uint32_t n, uint32_t a0)
{
    return i < s_ntr && s_tr[i].fn == fn && s_tr[i].ecx == ecx && s_tr[i].n == n && s_tr[i].a0 == a0;
}

static void t_reset(void)
{
    s_ntr = 0;
    s_tret_lookup = s_tret_find = s_tret_check = s_tret_vc = 0;
    rt_up_gc = t_fake;
}

static uint32_t f2u(float f)
{
    uint32_t v;
    memcpy(&v, &f, 4);
    return v;
}

static void altf4_cases(void)
{
    uint32_t e = rt_hook_va("engine", 0), alt = MEM32(e + E_KEY_ALT), f4 = MEM32(e + E_KEY_F4);
    uint32_t keys[4][2] = {{1, 1}, {1, 0}, {0, 1}, {0, 0}}, ret[4], calls[4];
    s_t.altf4 = 1;
    for (int i = 0; i < 4; i++) {
        MEM32(e + E_KEY_ALT) = keys[i][0];
        MEM32(e + E_KEY_F4) = keys[i][1];
        t_reset();
        ret[i] = hook_altf4(NULL);
        calls[i] = s_ntr;
    }
    t_reset();
    MEM32(e + E_KEY_ALT) = 1;
    MEM32(e + E_KEY_F4) = 1;
    hook_altf4(NULL);
    int quit = ret[0] == e + E_KEY_EPILOGUE && calls[0] == 1 && s_ntr == 1 && s_tr[0].fn == e + E_CBUF_FN && s_tr[0].n == 1 &&
               s_tr[0].a0 == s_t.str_exit && !strcmp(rt_gstr(s_t.str_exit), "exit\n");
    int pass = 1;
    for (int i = 1; i < 4; i++) pass = pass && ret[i] == e + E_KEY_CAVE && calls[i] == 0;
    s_t.altf4 = 0;
    t_reset();
    int off = hook_altf4(NULL) == e + E_KEY_CAVE && s_ntr == 0;
    s_t.altf4 = 1;
    MEM32(e + E_KEY_ALT) = alt;
    MEM32(e + E_KEY_F4) = f4;
    rt_up_check(quit && pass && off, "alt+f4 hook queues exit and leaves through the epilogue only with both keys down, and runs the original code otherwise or when off",
                "quit %d pass %d off %d", quit, pass, off);
}

static void corpse_cases(uint32_t obj, uint32_t vt)
{
    uint32_t gl = vp(V_GPGLOBALS), sv_gl = MEM32(gl), gv = rt_heap_alloc(0x20, 1), keep_eax = g_eax, keep_fade = MEM32(obj + 0x17c);
    MEM32(gv + 0xc) = f2u(100.0f);
    MEM32(gl) = gv;
    s_t.corpse = 1;
    memset(s_t.ring, 0, sizeof s_t.ring);
    s_t.next = 0;

    t_reset();
    g_ecx = obj;
    MEM32(obj) = vp(V_CORPSE_VT);
    uint32_t r_run = hook_corpse_think(NULL);
    int think_run = r_run == 0x66 && s_ntr == 1 && t_is(0, vp(V_THINK_FN), obj, 0, 0);
    MEM32(obj) = vt;
    t_reset();
    uint32_t r_skip = hook_corpse_think(NULL);
    int think_skip = r_skip == vp(V_CORPSE_VT) && s_ntr == 0;
    s_t.corpse = 0;
    t_reset();
    int think_off = hook_corpse_think(NULL) == 0x66 && s_ntr == 1 && t_is(0, vp(V_THINK_FN), obj, 0, 0);
    s_t.corpse = 1;
    rt_up_check(think_run && think_skip && think_off, "corpse think hook runs the original only for the corpse class, and always when off",
                "run %d skip %d off %d", think_run, think_skip, think_off);

    uint32_t three[3] = {0x10009c9b, 0, 0};
    t_reset();
    g_ecx = obj;
    g_eax = 0x4242;
    uint32_t r_mark = hook_corpse_mark(three);
    int marked = r_mark == 0x4242 && s_t.ring[0] == obj && s_t.next == 1 && s_ntr == 1 && t_is(0, vp(V_MARK_FN), obj, 3, 0x10009c9b);
    s_t.corpse = 0;
    t_reset();
    uint32_t r_off = hook_corpse_mark(three);
    int mark_off = r_off == 0x66 && s_t.next == 1 && s_ntr == 1;
    s_t.corpse = 1;
    s_t.next = CORPSE_RING - 1;
    t_reset();
    hook_corpse_mark(three);
    int wrapped = s_t.next == 0 && s_t.ring[CORPSE_RING - 1] == obj;
    rt_up_check(marked && mark_off && wrapped, "corpse mark hook records the object in the 0x800 ring before the original, keeps eax, and only calls through when off",
                "marked %d off %d wrapped %d", marked, mark_off, wrapped);

    float delay;
    uint32_t delay_u = MEM32(vp(V_FADE_DELAY));
    memcpy(&delay, &delay_u, 4);
    float want = (float)(100.0 + (double)delay);
    MEM32(obj + 0x17c) = 0;
    g_ecx = obj;
    int fade_set = hook_corpse_fade(NULL) == vp(0) && MEM32(obj + 0x17c) == f2u(want);
    MEM32(obj + 0x17c) = 0;
    g_ecx = obj + 8;
    int fade_miss = hook_corpse_fade(NULL) == 0 && MEM32(obj + 0x17c) == 0;
    s_t.corpse = 0;
    g_ecx = obj;
    int fade_off = hook_corpse_fade(NULL) == 0 && MEM32(obj + 0x17c) == 0;
    s_t.corpse = 1;
    rt_up_check(fade_set && fade_miss && fade_off, "corpse fade hook pushes a ringed object's fade time to now plus the delay and runs the original otherwise or when off",
                "set %d miss %d off %d", fade_set, fade_miss, fade_off);

    t_reset();
    g_ecx = obj;
    hook_corpse_clear(NULL);
    int cleared = s_t.next == 0 && s_t.ring[0] == 0 && s_t.ring[CORPSE_RING - 1] == 0 && s_ntr == 1 && t_is(0, vp(V_CLEAR_FN), obj, 0, 0);
    s_t.ring[0] = obj;
    s_t.next = 1;
    s_t.corpse = 0;
    t_reset();
    hook_corpse_clear(NULL);
    int clear_off = s_t.ring[0] == obj && s_ntr == 1 && t_is(0, vp(V_CLEAR_FN), obj, 0, 0);
    s_t.corpse = 1;
    g_ecx = obj;
    int stale = hook_corpse_fade(NULL) == vp(0);
    hook_corpse_clear(NULL);
    MEM32(obj + 0x17c) = 0;
    int fresh = hook_corpse_fade(NULL) == 0 && MEM32(obj + 0x17c) == 0;
    rt_up_check(cleared && clear_off && stale && fresh, "corpse clear hook empties the ring on level change then runs the original, and only calls through when off",
                "cleared %d off %d stale %d fresh %d", cleared, clear_off, stale, fresh);

    memset(s_t.ring, 0, sizeof s_t.ring);
    s_t.next = 0;
    MEM32(obj + 0x17c) = keep_fade;
    MEM32(gl) = sv_gl;
    g_eax = keep_eax;
    rt_heap_free(gv);
}

void rt_up_tweaks_selftest(void)
{
    uint32_t stack = g_esp, keep_ecx = g_ecx, keep_esi = g_esi;
    uint32_t vt = rt_heap_alloc(0x800, 1), obj = rt_heap_alloc(0x900, 1), held = rt_heap_alloc(0x40, 1), glob = vp(V_GLOBAL), sv_glob = MEM32(glob);
    uint32_t gobj = rt_heap_alloc(0x40, 1);
    for (uint32_t off = 0; off < 0x800; off += 4) MEM32(vt + off) = 0xB000u + off;
    MEM32(obj) = vt;
    MEM32(held) = vt;
    MEM32(glob) = gobj;
    s_t.nodoor = s_t.drop = s_t.stack = 1;

    t_reset();
    g_esi = obj;
    hook_nodoor(NULL);
    int plain = s_ntr == 1 && t_is(0, 0xB3D0, obj, 1, 1);
    MEM32(obj) = vp(V_DOOR_VT);
    t_reset();
    s_tret_lookup = held;
    s_tret_vc = 1;
    hook_nodoor(NULL);
    int kept = s_ntr == 2 && t_is(0, vp(V_LOOKUP_FN), gobj, 1, obj + 0x53c) && t_is(1, 0xB288, held, 0, 0);
    t_reset();
    s_tret_lookup = held;
    s_tret_vc = 0;
    hook_nodoor(NULL);
    int closes = s_ntr == 3 && s_tr[2].fn == MEM32(vp(V_DOOR_VT) + 0x3d0) && s_tr[2].ecx == obj;
    t_reset();
    s_tret_lookup = 0;
    hook_nodoor(NULL);
    int unheld = s_ntr == 2 && s_tr[1].ecx == obj && s_tr[1].a0 == 1;
    t_reset();
    s_t.nodoor = 0;
    s_tret_lookup = held;
    s_tret_vc = 1;
    hook_nodoor(NULL);
    int off_door = s_ntr == 1 && s_tr[0].ecx == obj;
    s_t.nodoor = 1;
    MEM32(obj) = vt;
    rt_up_check(plain && kept && closes && unheld && off_door,
                "no door auto close hook skips the close only for a door whose lookup is held, and always closes when off",
                "plain %d kept %d closes %d unheld %d off %d", plain, kept, closes, unheld, off_door);

    t_reset();
    g_ecx = obj;
    hook_drop(NULL);
    int walked = s_ntr == 0xe1 && t_is(0, vp(V_SLOT_FN), obj, 1, 0) && t_is(4, vp(V_DROP_FN), obj, 3, 0x77) && s_tr[4].a1 == 0 &&
                 t_is(0xe0, vp(V_SLOT_FN), obj, 1, 0xdf);
    s_t.drop = 0;
    t_reset();
    hook_drop(NULL);
    int orig = s_ntr == 1 && t_is(0, vp(V_CLOSE_FN), obj, 0, 0);
    s_t.drop = 1;
    rt_up_check(walked && orig, "npc drop hook asks each of 0xe0 slots, drops each weapon found and runs the original call when off",
                "walked %d orig %d calls %u", walked, orig, s_ntr);

    t_reset();
    g_ecx = obj;
    uint32_t pair[2] = {7, 9};
    s_tret_find = held;
    s_tret_vc = 1;
    uint32_t r_stack = hook_stack_find(pair);
    int stackable = r_stack == 0 && s_ntr == 2 && t_is(0, vp(V_FIND_FN), obj, 2, 7) && s_tr[0].a1 == 9;
    t_reset();
    s_tret_find = held;
    uint32_t r_plain = hook_stack_find(pair);
    int single = r_plain == held;
    t_reset();
    s_tret_find = 0;
    int missing = hook_stack_find(pair) == 0 && s_ntr == 1;
    s_t.stack = 0;
    t_reset();
    s_tret_find = held;
    s_tret_vc = 1;
    int off_find = hook_stack_find(pair) == held && s_ntr == 1;
    s_t.stack = 1;
    rt_up_check(stackable && single && missing && off_find, "stack lookup hook hides a stackable weapon from the lookup and passes everything else through",
                "stackable %d single %d missing %d off %d", stackable, single, missing, off_find);

    t_reset();
    g_ecx = obj;
    uint32_t arg9 = 9;
    s_tret_check = 0;
    uint32_t pick0 = hook_stack_pick(&arg9);
    int zeroed = s_ntr == 2 && t_is(1, vp(V_PICK_FN), obj, 1, 0) && pick0 == 0x66;
    t_reset();
    s_tret_check = 1;
    uint32_t pick1 = hook_stack_pick(&arg9);
    int shortcut = pick1 == 1 && s_ntr == 1;
    s_t.stack = 0;
    t_reset();
    hook_stack_pick(&arg9);
    int passed = s_ntr == 1 && t_is(0, vp(V_PICK_FN), obj, 1, 9);
    s_t.stack = 1;
    rt_up_check(zeroed && shortcut && passed, "stack pick hook retries with a zero argument unless the check passes and passes through when off",
                "zeroed %d shortcut %d passed %d", zeroed, shortcut, passed);

    t_reset();
    g_esi = obj;
    uint32_t keep_count = MEM32(obj + 0x8d0);
    s_tret_vc = 1;
    MEM32(obj + 0x8d0) = 2;
    int skip = hook_stack_skip(NULL) == 0 && t_is(0, 0xB5CC, obj, 0, 0);
    MEM32(obj + 0x8d0) = 1;
    int proceed_one = hook_stack_skip(NULL) == 1;
    s_tret_vc = 0;
    MEM32(obj + 0x8d0) = 2;
    int proceed_no = hook_stack_skip(NULL) == 1;
    s_t.stack = 0;
    s_tret_vc = 1;
    int off_skip = hook_stack_skip(NULL) == 1;
    s_tret_vc = 0;
    s_t.stack = 1;
    MEM32(obj + 0x8d0) = keep_count;
    rt_up_check(skip && proceed_one && proceed_no && off_skip, "stack skip hook skips only when the check passes and the count is above 1, and runs the original when the switch is off",
                "skip %d one %d none %d off %d", skip, proceed_one, proceed_no, off_skip);

    corpse_cases(obj, vt);
    altf4_cases();

    MEM32(glob) = sv_glob;
    g_esp = stack;
    g_ecx = keep_ecx;
    g_esi = keep_esi;
    rt_up_gc = rt_guest_call;
    rt_heap_free(vt);
    rt_heap_free(obj);
    rt_heap_free(held);
    rt_heap_free(gobj);
    rt_up_tweaks_load();
}
