#include "recomp_types.h"
#include "rt.h"

#include <string.h>

enum {
    C_OWNER_FN = 0x7b160, C_COUNT = 0x49d984, C_LIST = 0x49d978, C_TABLE = 0x2b8e98, C_KEY_FN = 0x1b4360, C_PANEL_FN = 0xf0ed0,
    C_PANELS = 0x4cfe38, C_BARTER_NAME = 0x27b3d0, C_CMD_FN = 0x101e20, C_CMD_STR = 0x4d2fa4, KEY_A = 0x40, KEY_B = 0x41
};

static int s_cs;

static uint32_t cl(uint32_t rva) { return rt_hook_va("client", rva); }

static uint32_t vcall(uint32_t obj, uint32_t off) { return rt_up_call_this(MEM32(MEM32(obj) + off), obj, 0, NULL); }

void rt_up_container_load(void) { s_cs = rt_up_plugin_on("container-stack-fix", "Enabled"); }

static uint32_t hook_cs_hide(const uint32_t *a)
{
    (void)a;
    return (uint32_t)s_cs;
}

static uint32_t hook_cs_give(const uint32_t *a)
{
    (void)a;
    return s_cs && g_esi && (uint8_t)vcall(g_esi, 0x3e8) != 0;
}

static uint32_t hook_cs_owned(const uint32_t *a)
{
    (void)a;
    uint32_t self = g_esi, item = g_ebx;
    if (!s_cs || MEM8(self + 0x181) != 0) return 0;
    uint32_t mode = MEM32(self + 0x4d4);
    if ((mode != 2 && mode != 4) || !item || (uint8_t)vcall(item, 0x3e8) == 0) return 0;
    uint32_t owner = rt_up_call_this(cl(C_OWNER_FN), item, 0, NULL);
    if (!owner) return 0;
    int32_t limit = (int32_t)MEM32(owner + 0x4fea8);
    uint32_t type = MEM16(item + 0x95e);
    if (type == 0xffff) return 0;
    int32_t n = (int32_t)MEM32(cl(C_COUNT));
    if (n <= 0) return 0;
    uint32_t list = MEM32(cl(C_LIST)), table = MEM32(cl(C_TABLE));
    for (; n; n--, list += 4) {
        uint32_t h = MEM32(list);
        if (h == 0xffffffffu) continue;
        uint32_t e = table + (h & 0x1fff) * 12, ent;
        if (MEM32(e + 8) != h >> 13 || !(ent = MEM32(e + 4))) continue;
        if (MEM16(ent + 0x95e) == type && (int32_t)MEM32(ent + 0x954) >= limit) return 1;
    }
    return 0;
}

static uint32_t hook_cs_keys(const uint32_t *a)
{
    if (s_cs && (a[0] == KEY_A || a[0] == KEY_B)) return a[0];
    return rt_up_call_this(cl(C_KEY_FN), g_ecx, 1, a);
}

static uint32_t hook_cs_barter(const uint32_t *a)
{
    (void)a;
    if (s_cs) {
        uint32_t name = cl(C_BARTER_NAME), p = rt_up_call_this(cl(C_PANEL_FN), cl(C_PANELS), 1, &name);
        if (p && MEM8(p + 0x4d8) && !MEM8(p + 0x181)) return p;
    }
    uint32_t args[2] = {cl(C_CMD_STR), 0};
    return rt_up_gc(cl(C_CMD_FN), 2, args);
}

const rt_shim rt_upk_cs_hide = {"up", "CsHide", 0, hook_cs_hide, RT_SHIM_REAL};
const rt_shim rt_upk_cs_give = {"up", "CsGive", 0, hook_cs_give, RT_SHIM_REAL};
const rt_shim rt_upk_cs_owned = {"up", "CsOwned", 0, hook_cs_owned, RT_SHIM_REAL};
const rt_shim rt_upk_cs_keys = {"up", "CsKeys", 1, hook_cs_keys, RT_SHIM_REAL};
const rt_shim rt_upk_cs_barter = {"up", "CsBarter", 0, hook_cs_barter, RT_SHIM_REAL};

typedef struct {
    uint32_t fn, ecx, n, a0, a1;
} c_rec;

static c_rec s_cr[16];
static uint32_t s_ncr, s_ret_vc, s_ret_owner, s_ret_panel;

static uint32_t c_fake(uint32_t fn, int n, const uint32_t *a)
{
    if (s_ncr < 16) s_cr[s_ncr++] = (c_rec){fn, g_ecx, (uint32_t)n, n > 0 ? a[0] : 0, n > 1 ? a[1] : 0};
    if (fn == cl(C_OWNER_FN)) return s_ret_owner;
    if (fn == cl(C_PANEL_FN)) return s_ret_panel;
    if (fn >> 12 == 0xC) return s_ret_vc;
    return 0x55;
}

static void c_reset(void)
{
    s_ncr = 0;
    rt_up_gc = c_fake;
}

static int c_is(uint32_t i, uint32_t fn, uint32_t ecx, uint32_t n, uint32_t a0, uint32_t a1)
{
    return i < s_ncr && s_cr[i].fn == fn && s_cr[i].ecx == ecx && s_cr[i].n == n && s_cr[i].a0 == a0 && s_cr[i].a1 == a1;
}

static void owned_cases(uint32_t self, uint32_t item)
{
    uint32_t owner = rt_heap_alloc(0x50000, 1), list = rt_heap_alloc(16, 1), table = rt_heap_alloc(12 * 0x40, 1), ent = rt_heap_alloc(0xa00, 1);
    uint32_t sv_n = MEM32(cl(C_COUNT)), sv_l = MEM32(cl(C_LIST)), sv_t = MEM32(cl(C_TABLE));
    MEM32(cl(C_COUNT)) = 3;
    MEM32(cl(C_LIST)) = list;
    MEM32(cl(C_TABLE)) = table;
    MEM32(list) = 0xffffffffu;
    MEM32(list + 4) = (5u << 13) | 0x20;
    MEM32(list + 8) = (7u << 13) | 0x21;
    MEM32(table + 0x20 * 12 + 8) = 4;
    MEM32(table + 0x20 * 12 + 4) = ent;
    MEM32(table + 0x21 * 12 + 8) = 7;
    MEM32(table + 0x21 * 12 + 4) = ent;
    MEM32(owner + 0x4fea8) = 10;
    MEM16(item + 0x95e) = 0x33;
    MEM16(ent + 0x95e) = 0x33;
    MEM32(ent + 0x954) = 10;
    MEM8(self + 0x181) = 0;
    MEM32(self + 0x4d4) = 2;
    s_ret_vc = 1;
    s_ret_owner = owner;
    g_esi = self;
    g_ebx = item;
    c_reset();
    int full = hook_cs_owned(NULL) == 1 && s_ncr == 2 && c_is(0, 0xC3E8, item, 0, 0, 0) && c_is(1, cl(C_OWNER_FN), item, 0, 0, 0);
    MEM32(self + 0x4d4) = 4;
    int mode4 = hook_cs_owned(NULL) == 1;
    MEM32(ent + 0x954) = 9;
    int room = hook_cs_owned(NULL) == 0;
    MEM32(ent + 0x954) = 11;
    MEM32(table + 0x21 * 12 + 8) = 6;
    int stale = hook_cs_owned(NULL) == 0;
    MEM32(table + 0x21 * 12 + 8) = 7;
    MEM16(ent + 0x95e) = 0x34;
    int other = hook_cs_owned(NULL) == 0;
    MEM16(ent + 0x95e) = 0x33;
    rt_up_check(full && mode4 && room && stale && other, "container owned hook flags a held stack of the same type at or over the owner limit",
                "full %d mode4 %d room %d stale %d other %d", full, mode4, room, stale, other);
    int gates = 1;
    MEM32(self + 0x4d4) = 3;
    gates &= hook_cs_owned(NULL) == 0;
    MEM32(self + 0x4d4) = 2;
    MEM8(self + 0x181) = 1;
    gates &= hook_cs_owned(NULL) == 0;
    MEM8(self + 0x181) = 0;
    g_ebx = 0;
    gates &= hook_cs_owned(NULL) == 0;
    g_ebx = item;
    s_ret_vc = 0;
    gates &= hook_cs_owned(NULL) == 0;
    s_ret_vc = 1;
    s_ret_owner = 0;
    gates &= hook_cs_owned(NULL) == 0;
    s_ret_owner = owner;
    MEM16(item + 0x95e) = 0xffff;
    gates &= hook_cs_owned(NULL) == 0;
    MEM16(item + 0x95e) = 0x33;
    MEM32(cl(C_COUNT)) = 0;
    gates &= hook_cs_owned(NULL) == 0;
    MEM32(cl(C_COUNT)) = 3;
    s_cs = 0;
    c_reset();
    int off = hook_cs_owned(NULL) == 0 && s_ncr == 0;
    s_cs = 1;
    rt_up_check(gates && off, "container owned hook leaves the flag alone for every failed gate and when off", "gates %d off %d", gates, off);
    MEM32(cl(C_COUNT)) = sv_n;
    MEM32(cl(C_LIST)) = sv_l;
    MEM32(cl(C_TABLE)) = sv_t;
    rt_heap_free(owner);
    rt_heap_free(list);
    rt_heap_free(table);
    rt_heap_free(ent);
}

void rt_up_container_selftest(void)
{
    uint32_t keep_ecx = g_ecx, keep_esi = g_esi, keep_ebx = g_ebx;
    uint32_t vt = rt_heap_alloc(0x400, 1), self = rt_heap_alloc(0x500, 1), item = rt_heap_alloc(0xa00, 1), panel = rt_heap_alloc(0x500, 1);
    for (uint32_t off = 0; off < 0x400; off += 4) MEM32(vt + off) = 0xC000u + off;
    MEM32(self) = vt;
    MEM32(item) = vt;
    s_cs = 1;

    int hide = hook_cs_hide(NULL) == 1;
    s_cs = 0;
    hide = hide && hook_cs_hide(NULL) == 0;
    s_cs = 1;
    rt_up_check(hide, "container hide hook clears the found flag for a stackable item only when on", "hide %d", hide);

    g_esi = item;
    s_ret_vc = 1;
    c_reset();
    int give = hook_cs_give(NULL) == 1 && s_ncr == 1 && c_is(0, 0xC3E8, item, 0, 0, 0);
    s_ret_vc = 0;
    int nogive = hook_cs_give(NULL) == 0;
    g_esi = 0;
    c_reset();
    int noobj = hook_cs_give(NULL) == 0 && s_ncr == 0;
    g_esi = item;
    s_ret_vc = 1;
    s_cs = 0;
    c_reset();
    int give_off = hook_cs_give(NULL) == 0 && s_ncr == 0;
    s_cs = 1;
    rt_up_check(give && nogive && noobj && give_off, "container give hook keeps the action open only for a stackable item, and never when off",
                "give %d no %d noobj %d off %d", give, nogive, noobj, give_off);

    owned_cases(self, item);

    uint32_t keys[3] = {KEY_A, KEY_B, 0x42};
    g_ecx = self;
    c_reset();
    int eat = hook_cs_keys(&keys[0]) == KEY_A && hook_cs_keys(&keys[1]) == KEY_B && s_ncr == 0;
    int pass = hook_cs_keys(&keys[2]) == 0x55 && s_ncr == 1 && c_is(0, cl(C_KEY_FN), self, 1, 0x42, 0);
    s_cs = 0;
    c_reset();
    int keys_off = hook_cs_keys(&keys[0]) == 0x55 && s_ncr == 1 && c_is(0, cl(C_KEY_FN), self, 1, KEY_A, 0);
    s_cs = 1;
    rt_up_check(eat && pass && keys_off, "container key hook swallows keys 0x40 and 0x41 and passes every other key, and all keys when off",
                "eat %d pass %d off %d", eat, pass, keys_off);

    g_ecx = 0;
    MEM8(panel + 0x4d8) = 1;
    MEM8(panel + 0x181) = 0;
    s_ret_panel = panel;
    c_reset();
    int open = hook_cs_barter(NULL) == panel && s_ncr == 1 && c_is(0, cl(C_PANEL_FN), cl(C_PANELS), 1, cl(C_BARTER_NAME), 0) &&
               !strcmp(rt_gstr(cl(C_BARTER_NAME)), "VBarterUI");
    int cmd = 1;
    uint32_t states[3][3] = {{0, 1, 0}, {1, 0, 0}, {1, 1, 1}};
    for (int i = 0; i < 3; i++) {
        s_ret_panel = states[i][0] ? panel : 0;
        MEM8(panel + 0x4d8) = (uint8_t)states[i][1];
        MEM8(panel + 0x181) = (uint8_t)states[i][2];
        c_reset();
        cmd &= hook_cs_barter(NULL) == 0x55 && s_ncr == 2 && c_is(1, cl(C_CMD_FN), 0, 2, cl(C_CMD_STR), 0);
    }
    s_ret_panel = panel;
    MEM8(panel + 0x4d8) = 1;
    MEM8(panel + 0x181) = 0;
    s_cs = 0;
    c_reset();
    int barter_off = hook_cs_barter(NULL) == 0x55 && s_ncr == 1 && c_is(0, cl(C_CMD_FN), 0, 2, cl(C_CMD_STR), 0);
    s_cs = 1;
    rt_up_check(open && cmd && barter_off, "container barter hook skips the command while the barter panel is open and active, and runs it otherwise or when off",
                "open %d cmd %d off %d", open, cmd, barter_off);

    rt_up_gc = rt_guest_call;
    g_ecx = keep_ecx;
    g_esi = keep_esi;
    g_ebx = keep_ebx;
    rt_heap_free(vt);
    rt_heap_free(self);
    rt_heap_free(item);
    rt_heap_free(panel);
    rt_up_container_load();
}
