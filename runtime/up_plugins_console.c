#include "recomp_types.h"
#include "rt.h"

#include <stdio.h>
#include <string.h>

enum {
    E_MGR = 0x13064a8, E_CMD = 0x13064a4, E_TIME = 0x314890, E_CNT = 0xb4378c, E_ON = 0xb43788, E_ALT = 0x12b0758, E_CLOSE = 0x10d650,
    G_ENG = 0x6c330, G_CON = 0x6c338, G_MIN = 0x220e0,
    V_VISIBLE = 0x58, V_NAME = 0x1a8, V_CLOSE = 0xc, V_FIND = 0x98, V_HIDE = 0xb8, F_LIST = 0xcc
};

static struct {
    int on;
    uint8_t held, have, closing, keep, want, menu;
    uint32_t was_open, con_vis, obj, menu_arg, menu_mgr, menu_con, menu_keep, str_min, str_unpause;
    double t;
    char name[0x80];
} s_c;

void rt_up_console_load(void) { s_c.on = rt_up_plugin_on("console-pause-fix", "Enabled"); }

uint32_t rt_up_gnew(const char *s)
{
    uint32_t n = (uint32_t)strlen(s) + 1, va = rt_heap_alloc(n, 1);
    memcpy(rt_host(va), s, n);
    return va;
}

uint32_t rt_up_tramp(const char *module, uint32_t rva, uint32_t skip)
{
    uint32_t va = rt_hook_va(module, rva);
    const uint8_t *p = rt_host(va);
    int32_t rel;
    if (p[0] != 0xE9) return va;
    memcpy(&rel, p + 1, 4);
    return va + 5u + (uint32_t)rel + skip;
}

static uint32_t eng(void) { return rt_hook_va("engine", 0); }

static uint32_t gui(void) { return rt_hook_va("gameui", 0); }

static uint32_t strs(uint32_t *slot, const char *s)
{
    if (!*slot) *slot = rt_up_gnew(s);
    return *slot;
}

static uint32_t vc(uint32_t obj, uint32_t off, int n, const uint32_t *a) { return rt_up_call_this(MEM32(MEM32(obj) + off), obj, n, a); }

static uint32_t vis(uint32_t obj) { return obj ? (uint8_t)vc(obj, V_VISIBLE, 0, NULL) != 0 : 0; }

static uint32_t mgr_obj(void)
{
    uint32_t m = MEM32(eng() + E_MGR);
    return m && MEM32(m + 4) ? MEM32(m + 8) : 0;
}

static double now(void)
{
    double d;
    memcpy(&d, rt_host(eng() + E_TIME), 8);
    return d;
}

static uint32_t hook_con_a(const uint32_t *a)
{
    (void)a;
    uint32_t run = rt_up_tramp("engine", 0x10dc10, 7);
    if (!s_c.on) return rt_up_gc(run, 0, NULL);
    uint8_t held = s_c.held;
    uint32_t was = s_c.was_open, open = vis(mgr_obj());
    s_c.was_open = open;
    if (!open) {
        uint32_t eg = MEM32(gui() + G_ENG), p = eg ? vc(eg, V_NAME, 0, NULL) : 0;
        s_c.have = 1;
        s_c.con_vis = vis(MEM32(gui() + G_CON));
        s_c.obj = mgr_obj();
        s_c.t = now();
        snprintf(s_c.name, sizeof s_c.name, "%s", p ? rt_gstr(p) : "");
    }
    s_c.held = 1;
    uint32_t r = rt_up_gc(run, 0, NULL);
    uint32_t con = mgr_obj();
    if (con) {
        uint32_t zero = 0;
        rt_up_call_this(gui() + G_MIN, con, 1, &zero);
        uint32_t list = MEM32(con + F_LIST);
        if (list) {
            uint32_t find[2] = {strs(&s_c.str_min, "Minimize"), 0}, btn = vc(list, V_FIND, 2, find);
            if (btn) vc(btn, V_HIDE, 1, &zero);
        }
    }
    s_c.held = held & 1;
    s_c.was_open = was;
    return r;
}

static int con_eat(const char *cmd)
{
    if (!s_c.on || !cmd) return 0;
    int setp = !strcmp(cmd, "setpause");
    if (s_c.closing && setp) return 1;
    if (s_c.held && setp) {
        if (s_c.was_open) return 1;
        uint32_t e = eng();
        if (MEM32(e + E_CNT) == 0 && (MEM32(e + E_ALT) != 0 || MEM32(e + E_ON) != 0)) {
            s_c.keep = 1;
            s_c.want = 0;
            return 1;
        }
        s_c.want = 1;
        s_c.keep = 0;
    }
    return s_c.menu && s_c.menu_arg && s_c.menu_mgr && !s_c.menu_con && s_c.menu_keep && !strcmp(cmd, "unpause");
}

static uint32_t hook_con_b(const uint32_t *a)
{
    uint32_t cmd = a[1];
    if (con_eat(cmd ? rt_gstr(cmd) : NULL)) return 1;
    return rt_up_call_this(rt_up_tramp("engine", 0x1a570, 9), g_ecx, 1, &cmd);
}

static uint32_t hook_con_c(const uint32_t *a)
{
    uint32_t self = g_ecx, arg = a[1], before = mgr_obj();
    uint32_t r = rt_up_call_this(rt_up_tramp("gameui", 0x22070, 9), self, 1, &arg);
    if (!s_c.on || arg != 0 || self != before || s_c.have != 1) return r;
    uint32_t e = eng(), eg = MEM32(gui() + G_ENG), p = eg ? vc(eg, V_NAME, 0, NULL) : 0, bl = 0;
    const char *nm = eg ? (p ? rt_gstr(p) : NULL) : "";
    if (s_c.obj == mgr_obj() && nm && !strcmp(nm, s_c.name)) bl = now() >= s_c.t;
    uint8_t menu = s_c.menu;
    uint32_t con_vis = s_c.con_vis, fire = s_c.want & bl;
    s_c.want = 0;
    s_c.have = 0;
    s_c.keep = 0;
    s_c.obj = 0;
    if (fire && eg && (!menu || s_c.menu_arg == 0)) {
        uint32_t cnt = MEM32(e + E_CNT);
        if (MEM32(e + E_ON) != 0 && cnt != 0)
            MEM32(e + E_CNT) = cnt - 1;
        else {
            uint32_t cmd = strs(&s_c.str_unpause, "unpause");
            rt_up_call_this(rt_up_tramp("engine", 0x1a570, 9), eg, 1, &cmd);
        }
    }
    if (!bl || menu) return r;
    if (!con_vis) {
        uint32_t zero = 0;
        rt_up_gc(e + E_CLOSE, 1, &zero);
    } else if (MEM32(e + E_CMD)) {
        s_c.closing = 1;
        vc(MEM32(e + E_CMD), V_CLOSE, 0, NULL);
        s_c.closing = 0;
    }
    return r;
}

static uint32_t hook_con_d(const uint32_t *a)
{
    uint32_t self = g_ecx, arg = a[1], run = rt_up_tramp("gameui", 0x59d0, 9);
    if (!s_c.on) return rt_up_call_this(run, self, 1, &arg);
    uint8_t menu = s_c.menu;
    uint32_t o_arg = s_c.menu_arg, o_mgr = s_c.menu_mgr, o_con = s_c.menu_con, o_keep = s_c.menu_keep;
    s_c.menu = 1;
    s_c.menu_arg = arg;
    s_c.menu_mgr = vis(mgr_obj());
    s_c.menu_con = vis(MEM32(gui() + G_CON));
    s_c.menu_keep = s_c.keep;
    uint32_t r = rt_up_call_this(run, self, 1, &arg);
    s_c.menu = menu & 1;
    s_c.menu_arg = o_arg;
    s_c.menu_mgr = o_mgr;
    s_c.menu_con = o_con;
    s_c.menu_keep = o_keep;
    return r;
}

const rt_shim rt_upk_con_a = {"up", "ConsoleOpen", 0, hook_con_a, RT_SHIM_REAL};
const rt_shim rt_upk_con_b = {"up", "ConsoleCmd", 0, hook_con_b, RT_SHIM_REAL};
const rt_shim rt_upk_con_c = {"up", "ConsoleClose", 0, hook_con_c, RT_SHIM_REAL};
const rt_shim rt_upk_con_d = {"up", "ConsoleMenu", 0, hook_con_d, RT_SHIM_REAL};

typedef struct {
    uint32_t fn, ecx, n, a0, snap;
} con_rec;

static con_rec s_cr[16];
static uint32_t s_ncr;
static uint32_t s_wmgr_vis, s_wgui_vis, s_wname, s_wmgr, s_wgui, s_wbtn;

static uint32_t con_fake(uint32_t fn, int n, const uint32_t *a)
{
    uint32_t snap = (s_c.menu_mgr & 1) | (s_c.menu_con & 1) << 1 | (s_c.menu_keep & 1) << 2 | (uint32_t)(s_c.menu & 1) << 3 | s_c.menu_arg << 8;
    if (s_ncr < 16) s_cr[s_ncr++] = (con_rec){fn, g_ecx, (uint32_t)n, n > 0 ? a[0] : 0, snap};
    if (fn >> 12 != 0xA) return 0x55;
    uint32_t off = fn & 0xFFF;
    if (off == V_VISIBLE) return g_ecx == s_wmgr ? s_wmgr_vis : g_ecx == s_wgui ? s_wgui_vis : 0;
    if (off == V_NAME) return s_wname;
    if (off == V_FIND) return s_wbtn;
    return 0x55;
}

static void con_reset(void)
{
    int on = s_c.on;
    memset(&s_c, 0, sizeof s_c);
    s_c.on = on;
    s_ncr = 0;
    rt_up_gc = con_fake;
}

static int is_call(uint32_t i, uint32_t fn, uint32_t ecx, uint32_t n)
{
    return i < s_ncr && s_cr[i].fn == fn && s_cr[i].ecx == ecx && s_cr[i].n == n;
}

#define VT(off) (0xA000u + (off))

void rt_up_console_selftest(void)
{
    uint32_t e = eng(), g = gui(), save[8], stack = g_esp, sp = rt_heap_alloc(0x40, 1), keep_ecx = g_ecx;
    uint32_t at[8] = {e + E_MGR, e + E_CMD, e + E_CNT, e + E_ON, e + E_ALT, g + G_ENG, g + G_CON, e + E_TIME};
    for (int i = 0; i < 8; i++) save[i] = MEM32(at[i]);
    uint32_t t_hi = MEM32(e + E_TIME + 4);
    uint32_t mgr = rt_heap_alloc(0x20, 1), mobj = rt_heap_alloc(0x100, 1), gobj = rt_heap_alloc(0x100, 1), eobj = rt_heap_alloc(0x40, 1);
    uint32_t list = rt_heap_alloc(0x40, 1), btn = rt_heap_alloc(0x40, 1);
    uint32_t vt = rt_heap_alloc(0x200, 1), cmd = rt_heap_alloc(0x20, 1);
    for (uint32_t off = 0; off < 0x200; off += 4) MEM32(vt + off) = VT(off);
    MEM32(mobj) = MEM32(gobj) = MEM32(eobj) = MEM32(list) = MEM32(btn) = vt;
    MEM32(mobj + F_LIST) = list;
    MEM32(mgr + 4) = 1;
    MEM32(mgr + 8) = mobj;
    MEM32(e + E_MGR) = mgr;
    MEM32(e + E_CMD) = mobj;
    MEM32(g + G_ENG) = eobj;
    MEM32(g + G_CON) = gobj;
    s_wmgr_vis = s_wgui_vis = 0;
    s_wname = rt_up_gnew("Main Menu");
    s_wmgr = mobj;
    s_wgui = gobj;
    s_wbtn = btn;
    double t0 = 5.0;
    memcpy(rt_host(e + E_TIME), &t0, 8);
    g_esp = sp;
    s_c.on = 1;
    uint32_t cargs[2] = {0, cmd};
#define EAT() con_eat(rt_gstr(cmd))

    con_reset();
    strcpy(rt_host(cmd), "setpause");
    g_ecx = eobj;
    uint32_t pass = hook_con_b(cargs);
    int ran_orig = s_ncr == 1 && is_call(0, rt_up_tramp("engine", 0x1a570, 9), eobj, 1) && s_cr[0].a0 == cmd && pass == 0x55;
    s_c.closing = 1;
    s_ncr = 0;
    uint32_t swallowed = hook_con_b(cargs);
    int closing = ran_orig && swallowed == 1 && s_ncr == 0;
    strcpy(rt_host(cmd), "echo");
    int other = EAT() == 0;
    strcpy(rt_host(cmd), "setpause");
    s_c.on = 0;
    int off = EAT() == 0;
    s_c.on = 1;
    rt_up_check(closing && other && off, "console command hook eats setpause only while the console is closing, else runs the original, and does nothing when off",
                "pass %u swallowed %u other %d off %d", pass, swallowed, other, off);

    con_reset();
    s_c.held = 1;
    s_c.was_open = 1;
    int open_eat = EAT() == 1 && !s_c.want && !s_c.keep;
    s_c.was_open = 0;
    MEM32(e + E_CNT) = 2;
    MEM32(e + E_ON) = 1;
    int count = EAT() == 0 && s_c.want == 1 && s_c.keep == 0;
    MEM32(e + E_CNT) = 0;
    MEM32(e + E_ON) = 0;
    MEM32(e + E_ALT) = 0;
    int plain = EAT() == 0 && s_c.want == 1 && s_c.keep == 0;
    MEM32(e + E_ON) = 1;
    int keep_a = EAT() == 1 && s_c.keep == 1 && s_c.want == 0;
    MEM32(e + E_ON) = 0;
    MEM32(e + E_ALT) = 1;
    s_c.keep = 0;
    s_c.want = 0;
    int keep_b = EAT() == 1 && s_c.keep == 1 && s_c.want == 0;
    MEM32(e + E_ALT) = 0;
    rt_up_check(open_eat && count && plain && keep_a && keep_b,
                "console command hook eats setpause when the console was already open or the engine pause is held, else marks an unpause",
                "open %d count %d plain %d keepa %d keepb %d", open_eat, count, plain, keep_a, keep_b);

    con_reset();
    strcpy(rt_host(cmd), "unpause");
    s_c.menu = 1;
    s_c.menu_arg = 7;
    s_c.menu_mgr = 1;
    s_c.menu_con = 0;
    s_c.menu_keep = 1;
    int eat = EAT() == 1;
    s_c.menu_con = 1;
    int con_up = EAT() == 0;
    s_c.menu_con = 0;
    s_c.menu_arg = 0;
    int no_arg = EAT() == 0;
    s_c.menu_arg = 7;
    s_c.menu = 0;
    int out = EAT() == 0;
    rt_up_check(eat && con_up && no_arg && out, "console command hook eats unpause only inside the menu call with the recorded state",
                "eat %d conup %d noarg %d out %d", eat, con_up, no_arg, out);

    con_reset();
    s_wmgr_vis = 1;
    s_wgui_vis = 0;
    s_c.keep = 1;
    s_c.menu = 1;
    s_c.menu_arg = 3;
    g_ecx = gobj;
    uint32_t da[2] = {0, 0x77};
    hook_con_d(da);
    int ran = s_ncr == 3 && is_call(2, rt_up_tramp("gameui", 0x59d0, 9), gobj, 1) && s_cr[2].a0 == 0x77;
    int during = s_ncr == 3 && s_cr[2].snap == (1u | 0u | 4u | 8u | 0x77u << 8);
    int back = s_c.menu == 1 && s_c.menu_arg == 3 && s_c.menu_mgr == 0 && s_c.menu_con == 0 && s_c.menu_keep == 0;
    rt_up_check(ran && during && back, "console menu hook records the console state around the original call and restores it",
                "ran %d during %d back %d calls %u", ran, during, back, s_ncr);

    con_reset();
    s_wmgr_vis = 0;
    s_wgui_vis = 1;
    g_ecx = 0;
    hook_con_a(NULL);
    int cap = s_c.have == 1 && s_c.con_vis == 1 && s_c.obj == mobj && !strcmp(s_c.name, "Main Menu") && s_c.t == 5.0 && s_c.held == 0 && s_c.was_open == 0;
    int tail = s_ncr == 7 && is_call(4, g + G_MIN, mobj, 1) && is_call(5, VT(V_FIND), list, 2) && is_call(6, VT(V_HIDE), btn, 1);
    con_reset();
    s_wmgr_vis = 1;
    hook_con_a(NULL);
    int shown = s_c.have == 0 && s_c.held == 0;
    con_reset();
    s_c.on = 0;
    hook_con_a(NULL);
    int pass_a = s_ncr == 1 && s_cr[0].fn == rt_up_tramp("engine", 0x10dc10, 7) && s_c.have == 0;
    s_c.on = 1;
    rt_up_check(cap && tail && shown && pass_a, "console open hook captures the previous menu once, hides Minimize, skips capture when open and only runs the original when off",
                "cap %d tail %d shown %d passa %d calls %u", cap, tail, shown, pass_a, s_ncr);

    con_reset();
    s_wmgr_vis = 1;
    s_c.have = 1;
    s_c.obj = mobj;
    strcpy(s_c.name, "Main Menu");
    s_c.t = 1.0;
    s_c.want = 1;
    s_c.con_vis = 1;
    g_ecx = mobj;
    uint32_t ca[2] = {0, 0};
    MEM32(e + E_ON) = 0;
    hook_con_c(ca);
    int unp = s_ncr == 4 && is_call(2, rt_up_tramp("engine", 0x1a570, 9), eobj, 1) && !s_c.have && !s_c.want && !s_c.obj;
    int closed = is_call(3, VT(V_CLOSE), mobj, 0) && !s_c.closing;
    con_reset();
    s_c.have = 1;
    s_c.obj = mobj;
    strcpy(s_c.name, "Other");
    s_c.t = 1.0;
    s_c.want = 1;
    s_c.con_vis = 1;
    g_ecx = mobj;
    hook_con_c(ca);
    int renamed = s_ncr == 2 && !s_c.have;
    con_reset();
    s_c.have = 1;
    s_c.obj = mobj;
    strcpy(s_c.name, "Main Menu");
    s_c.t = 1.0;
    s_c.want = 1;
    s_c.con_vis = 0;
    g_ecx = mobj;
    MEM32(e + E_ON) = 1;
    MEM32(e + E_CNT) = 3;
    hook_con_c(ca);
    int counted = MEM32(e + E_CNT) == 2 && is_call(2, e + E_CLOSE, mobj, 1);
    con_reset();
    s_c.have = 1;
    s_c.obj = mobj;
    s_c.name[0] = 0;
    s_c.t = 1.0;
    s_c.want = 1;
    s_c.con_vis = 0;
    g_ecx = mobj;
    MEM32(e + E_ON) = 1;
    MEM32(e + E_CNT) = 3;
    MEM32(g + G_ENG) = 0;
    hook_con_c(ca);
    MEM32(g + G_ENG) = eobj;
    int nulled = MEM32(e + E_CNT) == 3 && s_ncr == 2 && s_cr[1].fn == e + E_CLOSE && s_cr[1].n == 1;
    con_reset();
    s_c.have = 1;
    s_c.obj = mobj;
    s_c.name[0] = 0;
    s_c.t = 1.0;
    s_c.want = 1;
    s_c.con_vis = 0;
    g_ecx = mobj;
    MEM32(e + E_ON) = 1;
    MEM32(e + E_CNT) = 3;
    uint32_t keep_name = s_wname;
    s_wname = 0;
    hook_con_c(ca);
    s_wname = keep_name;
    int unnamed = MEM32(e + E_CNT) == 3 && !s_c.have;
    rt_up_check(unp && closed && renamed && counted && nulled && unnamed,
                "console close hook replays unpause or lowers the pause count, then closes the console, and does neither when the menu changed or has no name",
                "unp %d closed %d renamed %d counted %d nulled %d unnamed %d calls %u", unp, closed, renamed, counted, nulled, unnamed, s_ncr);

    for (int i = 0; i < 8; i++) MEM32(at[i]) = save[i];
    MEM32(e + E_TIME + 4) = t_hi;
    g_esp = stack;
    g_ecx = keep_ecx;
    rt_up_gc = rt_guest_call;
    rt_heap_free(sp);
    rt_heap_free(mgr);
    rt_heap_free(mobj);
    rt_heap_free(gobj);
    rt_heap_free(eobj);
    rt_heap_free(list);
    rt_heap_free(btn);
    rt_heap_free(vt);
    rt_heap_free(cmd);
    rt_heap_free(s_wname);
    rt_up_console_load();
}
