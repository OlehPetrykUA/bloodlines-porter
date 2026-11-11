#include "recomp_types.h"
#include "rt.h"

#include <stdlib.h>
#include <string.h>

enum { M_TABLE = 4, M_ENTRY = 0x184, M_SLOTS = 0x400, M_NAME = 0x80, M_COUNT = 0xc2004, M_GEN = 0xc2050, M_GENS = 0xc2054, M_STAR = 0x61004 };

static int s_model_on;

void rt_up_model_load(void)
{
    s_model_on = rt_up_plugin_on("model-loader-fix", "Enabled") && rt_up_plugin_on("model-loader-fix", "Enable Model Loader Fix");
}

void rt_up_model_norm(const char *src, char *dst)
{
    uint32_t n = 0;
    char prev = 0;
    for (; *src && n + 1 < M_NAME; src++) {
        char c = *src == '\\' ? '/' : (*src >= 'A' && *src <= 'Z') ? (char)(*src + 0x20) : *src;
        if (c == '/' && prev == '/') continue;
        dst[n++] = prev = c;
    }
    dst[n] = 0;
}

static int model_eq(const char *s, const char *want)
{
    uint32_t n = 0;
    char prev = 0;
    for (; *s && n + 1 < M_NAME; s++) {
        char c = *s == '\\' ? '/' : (*s >= 'A' && *s <= 'Z') ? (char)(*s + 0x20) : *s;
        if (c == '/' && prev == '/') continue;
        if (c != want[n]) return 0;
        n++;
        prev = c;
    }
    return !want[n];
}

uint32_t rt_up_model_find(uint32_t self, const char *name)
{
    if (!name || !*name) return 0;
    if (*name == '*') return (uint32_t)atoi(name + 1) * M_ENTRY + M_STAR + self;
    char want[M_NAME];
    rt_up_model_norm(name, want);
    uint32_t slot = self + M_TABLE;
    for (uint32_t i = 0; i < M_SLOTS; i++, slot += M_ENTRY) {
        const char *s = rt_host(slot);
        if (*s && model_eq(s, want)) return slot;
    }
    slot = self + M_TABLE;
    uint32_t i = 0;
    while (i < M_SLOTS && *(const char *)rt_host(slot)) i++, slot += M_ENTRY;
    if (i == M_SLOTS) return self + M_TABLE + M_ENTRY * (M_SLOTS - 1);
    MEM32(self + M_COUNT)++;
    memset(rt_host(slot), 0, M_ENTRY);
    MEM32(self + M_GENS + 4 * i) = MEM32(self + M_GEN);
    memcpy(rt_host(slot), name, strnlen(name, M_NAME - 1));
    MEM32(slot + 0x80) = slot;
    MEM32(slot + 0x84) = 0;
    return slot;
}

static uint32_t hook_model(const uint32_t *a)
{
    uint32_t name = a[1];
    if (!s_model_on) return rt_up_call_this(rt_up_tramp("engine", 0xb87a0, 9), g_ecx, 1, &name);
    return rt_up_model_find(g_ecx, name ? rt_gstr(name) : NULL);
}

static uint32_t s_mfn, s_mecx, s_ma0;

static uint32_t m_fake(uint32_t fn, int n, const uint32_t *a)
{
    s_mfn = fn;
    s_mecx = g_ecx;
    s_ma0 = n ? a[0] : 0;
    return 0x4242;
}

const rt_shim rt_upk_model = {"up", "ModelLoader", 0, hook_model, RT_SHIM_REAL};

void rt_up_model_selftest(void)
{
    char out[M_NAME];
    rt_up_check(!strcmp(rt_up_plugin_ini("model-loader-fix"), "modelloader_fix") && !strcmp(rt_up_plugin_ini("vtmb-tweaks"), "vtmb-tweaks"),
                "the model loader reads modelloader_fix.ini, which the UP does not ship, and every other plug-in reads its own name", "");
    rt_up_model_norm("Models\\\\NPC//Gimble\\Head.MDL", out);
    rt_up_check(!strcmp(out, "models/npc/gimble/head.mdl"), "model name normaliser lowercases, maps backslashes and collapses slash runs", "got %s", out);
    char longname[300];
    memset(longname, 'A', sizeof longname - 1);
    longname[sizeof longname - 1] = 0;
    rt_up_model_norm(longname, out);
    rt_up_check(strlen(out) == 0x7f && out[0] == 'a', "model name normaliser stops at 0x7f characters", "len %zu", strlen(out));

    uint32_t t = rt_heap_alloc(0xc2100, 1);
    memset(rt_host(t), 0, 0xc2100);
    MEM32(t + M_GEN) = 0x31;
    uint32_t first = rt_up_model_find(t, "Models\\Foo.mdl");
    uint32_t again = rt_up_model_find(t, "models//foo.MDL");
    uint32_t other = rt_up_model_find(t, "models/bar.mdl");
    int ok = first == t + M_TABLE && again == first && other == first + M_ENTRY && MEM32(t + M_COUNT) == 2 &&
             !strcmp(rt_host(first), "Models\\Foo.mdl") && MEM32(first + 0x80) == first && MEM32(first + 0x84) == 0 &&
             MEM32(t + M_GENS) == 0x31 && MEM32(t + M_GENS + 4) == 0x31;
    rt_up_check(ok, "model lookup matches ignoring case and slash style, else claims the first free slot", "first 0x%X again 0x%X other 0x%X count %u",
                first, again, other, MEM32(t + M_COUNT));
    memset(rt_host(first), 0, M_ENTRY);
    uint32_t gap = rt_up_model_find(t, "models/bar.mdl");
    uint32_t reuse = rt_up_model_find(t, "models/baz.mdl");
    rt_up_check(gap == other && reuse == first, "model lookup scans past a freed slot and reuses the first free one", "gap 0x%X reuse 0x%X", gap, reuse);
    rt_up_check(rt_up_model_find(t, "*3") == t + 3 * M_ENTRY + M_STAR && rt_up_model_find(t, "") == 0 && rt_up_model_find(t, NULL) == 0,
                "model lookup resolves a star index and refuses an empty name", "");
    for (uint32_t i = 0; i < M_SLOTS; i++) ((char *)rt_host(t + M_TABLE + i * M_ENTRY))[0] = 'x';
    rt_up_check(rt_up_model_find(t, "models/none.mdl") == t + M_TABLE + M_ENTRY * (M_SLOTS - 1), "model lookup hands back the last slot when the table is full", "");
    char longp[300];
    memset(longp, 'q', sizeof longp - 1);
    longp[sizeof longp - 1] = 0;
    memset(rt_host(t + M_TABLE), 0, M_ENTRY);
    uint32_t lp = rt_up_model_find(t, longp);
    rt_up_check(lp == t + M_TABLE && strlen(rt_host(lp)) == 0x7f, "model lookup truncates a long stored name at 0x7f characters", "len %zu", strlen(rt_host(lp)));
    uint32_t count = MEM32(t + M_COUNT);
    rt_up_check(rt_up_model_find(t, longp) == lp && MEM32(t + M_COUNT) == count, "model lookup finds a truncated long name again instead of a new slot", "count %u then %u",
                count, MEM32(t + M_COUNT));
    rt_heap_free(t);

    uint32_t sv_ecx = g_ecx, tab = rt_heap_alloc(0xc2100, 1), nm = rt_heap_alloc(0x40, 1);
    memset(rt_host(tab), 0, 0xc2100);
    strcpy(rt_host(nm), "Models/A.mdl");
    g_ecx = tab;
    uint32_t args[2] = {0xCAFE, nm};
    s_model_on = 1;
    s_mfn = 0;
    rt_up_gc = m_fake;
    uint32_t handled = hook_model(args);
    int done = handled == tab + M_TABLE && s_mfn == 0;
    s_model_on = 0;
    uint32_t passed = hook_model(args);
    rt_up_gc = rt_guest_call;
    rt_up_check(done && passed == 0x4242 && s_mfn == rt_up_tramp("engine", 0xb87a0, 9) && s_mecx == tab && s_ma0 == nm && MEM32(tab + M_COUNT) == 1,
                "model hook returns the lookup result when on and runs the original with the same name when off", "handled 0x%X passed 0x%X", handled, passed);
    g_ecx = sv_ecx;
    rt_heap_free(tab);
    rt_heap_free(nm);
    rt_up_model_load();
}
