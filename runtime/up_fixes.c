#include "recomp_types.h"
#include "rt.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

enum { K_BYTES, K_JMP, K_CALL, K_PTR };

typedef struct {
    const char *id, *sw0, *sw1, *module;
    uint32_t rva;
    int kind;
    const char *old, *new_;
    uint32_t cell;
    int absoff;
    const rt_shim *shim;
    uint32_t target;
} fix_row;

static struct {
    int coronas, lights, claws, histories, fov;
    uint32_t shadows_n, shadows_ctr, fov_v;
    int fov_set;
} s_fx;

static uint32_t cl(uint32_t rva) { return rt_hook_va("client", rva); }

static uint32_t (*s_guest_call)(uint32_t, int, const uint32_t *) = rt_guest_call;

static void load_switches(void)
{
    s_fx.coronas = rt_up_switch_on("Bugs", "No Coronas Fix");
    s_fx.lights = rt_up_switch_on("Bugs", "Lights Through Walls Fix");
    s_fx.claws = rt_up_switch_on("Bugs", "Fix Claws Stats");
    s_fx.histories = rt_up_switch_on("Bugs", "Fix Histories");
    s_fx.shadows_n = (uint32_t)rt_up_switch_num("Bugs", "Force Shadows Rebuild");
    s_fx.fov = rt_up_switch_on("Tweaks", "FOV Reset Fix") || rt_up_plugin_on("vtmb-tweaks", "Enabled");
    rt_up_plugins_load();
}

static uint32_t hook_histories(const uint32_t *a)
{
    (void)a;
    if (!s_fx.histories) return 0;
    uint32_t p = cl(0x4a0d50);
    if (p && MEM32(p)) {
        MEM32(cl(0x4a0d5c)) = 0;
        MEM32(cl(0x4a0d60)) = 0;
    }
    return 0;
}

static uint32_t hook_coronas(const uint32_t *a)
{
    uint32_t e = a[0];
    if (!e || MEM32(e + 0x24) != 0x5f766e65u || MEM32(e + 0x28) != 0x69727073u) return 0;
    uint8_t *b = rt_host(e);
    if (!b[0x1a3]) return 0;
    if (s_fx.coronas) b[0x464] = b[0x1a3];
    if (s_fx.lights && b[0x16c] == 3) b[0x16c] = 4;
    return 0;
}

static uint32_t hook_claws(const uint32_t *a)
{
    if (s_fx.claws && !strcmp(rt_gstr(a[0]), "item_w_claws")) return 0;
    return s_guest_call(cl(0x1e28e0), 2, a);
}

static uint32_t hook_shadows(const uint32_t *a)
{
    uint32_t eax = a[0];
    if (!s_fx.shadows_n) return eax;
    s_fx.shadows_ctr++;
    if (MEM32(cl(0x4cf004)) == eax && s_fx.shadows_ctr == s_fx.shadows_n) {
        s_fx.shadows_ctr = 0;
        return eax + 1;
    }
    return eax;
}

static uint32_t hook_fov_parse(const uint32_t *a)
{
    uint32_t v = rt_guest_call(rt_hook_va("vampire", 0x431447), 1, a);
    s_fx.fov_v = v;
    s_fx.fov_set = 1;
    return v;
}

static uint32_t hook_fov_store(const uint32_t *a)
{
    return s_fx.fov && s_fx.fov_set ? s_fx.fov_v : a[0];
}

static const rt_shim k_histories = {"up", "FixHistories", 0, hook_histories, RT_SHIM_REAL};
static const rt_shim k_coronas = {"up", "FixCoronasLights", 1, hook_coronas, RT_SHIM_REAL};
static const rt_shim k_claws = {"up", "FixClawsStats", 0, hook_claws, RT_SHIM_REAL};
static const rt_shim k_shadows = {"up", "ForceShadowsRebuild", 1, hook_shadows, RT_SHIM_REAL};
static const rt_shim k_fov_parse = {"up", "FovResetParse", 0, hook_fov_parse, RT_SHIM_REAL};
static const rt_shim k_fov_store = {"up", "FovResetStore", 1, hook_fov_store, RT_SHIM_REAL};

static const fix_row k_rows[] = {
    {"fix_paths_a", "Bugs/Fix Paths", NULL, "FileSystem_Stdio", 0x3f33, K_BYTES, "5c", "2f", 0, -1, NULL},
    {"fix_paths_b", "Bugs/Fix Paths", NULL, "FileSystem_Stdio", 0x3f40, K_BYTES, "5c", "2f", 0, -1, NULL},
    {"floats_female", "Bugs/Floats For Female Fix", NULL, "vampire", 0x1f463c, K_BYTES, "75", "eb", 0, -1, NULL},
    {"model_lods", "Bugs/Fix Model LODs", NULL, "engine", 0xa6791, K_BYTES, "01", "00", 0, -1, NULL},
    {"save_speed_a", "Tweaks/Improve Save Speed", NULL, "engine", 0x985fa, K_BYTES, "09", "01", 0, -1, NULL},
    {"save_speed_b", "Tweaks/Improve Save Speed", NULL, "engine", 0x9896a, K_BYTES, "09", "01", 0, -1, NULL},
    {"save_speed_c", "Tweaks/Improve Save Speed", NULL, "engine", 0xc53af, K_BYTES, "09", "01", 0, -1, NULL},
    {"ragdolls", "Tweaks/Non-Solid Ragdolls", "vtmb-tweaks/bNonSolidRagdolls", "vampire", 0x154e90, K_BYTES, "c1e90283e101", "b90100000090", 0, -1, NULL},
    {"no_tracers", "Tweaks/No Bullet Tracers", "vtmb-tweaks/bNoTracers", "client", 0x4099a, K_BYTES, "0f84af000000", "e9b000000000", 0, -1, NULL},
    {"protean_warform_b", "protean-improved/Warform Frenzy", NULL, "vampire", 0x33ed75, K_BYTES, "74", "eb", 0, -1, NULL},
    {"door_fps_b", "vtmb-tweaks/bFixDoorsAtHighFPS", NULL, "vampire", 0xf3a66, K_BYTES, "8b8e0807000041", "b9020000009090", 0, -1, NULL},
    {"histories", "Bugs/Fix Histories", NULL, "client", 0x57dc0, K_JMP, "568b35500d4a10", "", 1, 3, &k_histories},
    {"coronas_lights", "Bugs/No Coronas Fix", "Bugs/Lights Through Walls Fix", "engine", 0x65dc2, K_JMP, "5150ff5740", "", 2, -1, &k_coronas},
    {"claws_stats", "Bugs/Fix Claws Stats", NULL, "client", 0x1853a8, K_CALL, "e833d50500", "", 3, -1, &k_claws},
    {"shadows_rebuild", "Bugs/Force Shadows Rebuild", NULL, "client", 0xd7f60, K_JMP, "390504f04c10", "", 4, 2, &k_shadows},
    {"fov_reset_parse", "Tweaks/FOV Reset Fix", "vtmb-tweaks/Enabled", "vampire", 0xd2dc1, K_CALL, "e881e63500", "", 5, -1, &k_fov_parse},
    {"fov_reset_store", "Tweaks/FOV Reset Fix", "vtmb-tweaks/Enabled", "vampire", 0x239e01, K_JMP, "899f781e0000", "", 6, -1, &k_fov_store},
    {"door_fps_a", "vtmb-tweaks/bFixDoorsAtHighFPS", NULL, "vampire", 0xdab2, K_JMP, "e9293d1b00", "", 7, -1, &rt_upk_door},
    {"hist_stat_a", "history-stat-reset-fix/Enabled", NULL, "vampire", 0xd6dd8, K_JMP, "5f5e5b81c4a0000000", "", 8, -1, &rt_upk_hist_a},
    {"hist_stat_c", "history-stat-reset-fix/Enabled", NULL, "vampire", 0xd6deb, K_JMP, "5f5e5b81c4a0000000", "", 9, -1, &rt_upk_hist_c},
    {"hist_stat_b", "history-stat-reset-fix/Enabled", NULL, "vampire", 0x206ad1, K_JMP, "3bdd0f84f6000000", "", 10, -1, &rt_upk_hist_b},
    {"model_loader", "model-loader-fix/Enable Model Loader Fix", NULL, "engine", 0xb87a0, K_JMP, "53558b6c240c56", "", 11, -1, &rt_upk_model},
    {"console_open", "console-pause-fix/Enabled", NULL, "engine", 0x10dc10, K_JMP, "8b0da4643021", "", 12, 2, &rt_upk_con_a},
    {"console_cmd", "console-pause-fix/Enabled", NULL, "engine", 0x1a570, K_JMP, "8b44240450", "", 13, -1, &rt_upk_con_b},
    {"console_close", "console-pause-fix/Enabled", NULL, "gameui", 0x22070, K_JMP, "8b44240456", "", 14, -1, &rt_upk_con_c},
    {"console_menu", "console-pause-fix/Enabled", NULL, "gameui", 0x59d0, K_JMP, "568bf18b0d30c30610", "", 15, 5, &rt_upk_con_d},
    {"tw_nodoor", "vtmb-tweaks/bNoDoorAutoClose", NULL, "vampire", 0xf09eb, K_JMP, "8b066a018b", "", 17, -1, &rt_upk_nodoor},
    {"tw_drop", "vtmb-tweaks/bNPCsDropAllWeapons", NULL, "vampire", 0x32ba84, K_CALL, "e883a3cdff", "", 18, -1, &rt_upk_drop},
    {"tw_stack_find", "vtmb-tweaks/bStackableWeapons", NULL, "vampire", 0x17724e, K_CALL, "e865dfe9ff", "", 19, -1, &rt_upk_stack_find},
    {"tw_stack_pick", "vtmb-tweaks/bStackableWeapons", NULL, "vampire", 0x17788c, K_CALL, "e845c0e9ff", "", 20, -1, &rt_upk_stack_pick},
    {"tw_stack_skip", "vtmb-tweaks/bStackableWeapons", NULL, "vampire", 0x32d142, K_JMP, "8b078bcfff9008060000", "", 21, -1, &rt_upk_stack_skip},
    {"tw_altf4", "vtmb-tweaks/bEnableAltF4", NULL, "engine", 0xa38ee, K_JMP, "a11035d620", "", 16, 1, &rt_upk_altf4},
    {"tw_corpse_think", "vtmb-tweaks/bNoCorpseDespawn", NULL, "vampire", 0x265d72, K_CALL, "e8f0b8daff", "", 22, -1, &rt_upk_corpse_think},
    {"tw_corpse_fade", "vtmb-tweaks/bNoCorpseDespawn", NULL, "vampire", 0x2696f0, K_JMP, "5356578bf9", "", 23, -1, &rt_upk_corpse_fade},
    {"tw_corpse_mark", "vtmb-tweaks/bNoCorpseDespawn", NULL, "vampire", 0x32c40f, K_CALL, "e8d1bbcdff", "", 24, -1, &rt_upk_corpse_mark},
    {"tw_corpse_clear", "vtmb-tweaks/bNoCorpseDespawn", NULL, "vampire", 0x46251c, K_PTR, "02b80010", "", 25, 0, &rt_upk_corpse_clear, 0xb802},
    {"cs_hide", "container-stack-fix/Enabled", NULL, "vampire", 0xda3cb, K_JMP, "0f8486000000", "", 26, -1, &rt_upk_cs_hide},
    {"cs_give", "container-stack-fix/Enabled", NULL, "client", 0x16dfa4, K_JMP, "8b4424348a888101000084c9", "", 27, -1, &rt_upk_cs_give},
    {"cs_owned", "container-stack-fix/Enabled", NULL, "client", 0x16fe4a, K_JMP, "8a44241384c00f85aa000000", "", 28, -1, &rt_upk_cs_owned},
    {"cs_keys", "container-stack-fix/Enabled", NULL, "client", 0x24aa90, K_PTR, "60431b10", "", 29, 0, &rt_upk_cs_keys, 0x1b4360},
    {"cs_barter", "container-stack-fix/Enabled", NULL, "client", 0x102150, K_JMP, "6a0068a42f4d10", "", 30, 3, &rt_upk_cs_barter},
};

#define NROWS (sizeof k_rows / sizeof k_rows[0])

static uint32_t unhex(const char *s, uint8_t *out)
{
    uint32_t n = 0;
    for (; s[0] && s[1]; s += 2) {
        char b[3] = {s[0], s[1], 0};
        out[n++] = (uint8_t)strtoul(b, NULL, 16);
    }
    return n;
}

static int sw_on(const char *s)
{
    char sec[32];
    const char *slash = s ? strchr(s, '/') : NULL;
    if (!slash) return 0;
    snprintf(sec, sizeof sec, "%.*s", (int)(slash - s), s);
    if (rt_up_plugin_known(sec)) return rt_up_plugin_on(sec, "Enabled") && rt_up_plugin_on(sec, slash + 1);
    return rt_up_switch_on(sec, slash + 1);
}

static int row_on(const fix_row *r) { return sw_on(r->sw0) || sw_on(r->sw1); }

static int ptr_state(const fix_row *r, uint32_t va)
{
    uint32_t base = rt_hook_va(r->module, 0), v = MEM32(va), size = 0, cell = RT_EXE_BASE + 0x800u + 4u * r->cell;
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (rt_modules[m]->base == base) size = rt_modules[m]->size;
    if (v == base + r->target) return RT_HOOK_ABSENT;
    if (v - base >= size || size - (v - base) < 6) return -1;
    const uint8_t *t = rt_host(v);
    return t[0] == 0xFF && t[1] == 0x25 && !memcmp(t + 2, &cell, 4) ? RT_HOOK_BAKED : -1;
}

static int row_state(const fix_row *r)
{
    uint8_t old[16], nw[16];
    uint32_t n = unhex(r->old, old), va = rt_hook_va(r->module, r->rva);
    if (!va) return -1;
    if (r->kind == K_PTR) return ptr_state(r, va);
    const uint8_t *p = rt_host(va);
    if (rt_hook_same(p, old, n, r->absoff)) return RT_HOOK_ABSENT;
    if (r->kind == K_BYTES) {
        unhex(r->new_, nw);
        return memcmp(p, nw, n) ? -1 : RT_HOOK_BAKED;
    }
    return p[0] == (r->kind == K_JMP ? 0xE9 : 0xE8) ? RT_HOOK_BAKED : -1;
}

void rt_up_fixes_guard(void)
{
    for (size_t i = 0; i < NROWS; i++)
        if (row_state(&k_rows[i]) == RT_HOOK_BAKED)
            rt_stop(5, 0, "UP build needs -game Unofficial_Patch (row %s is patched)", k_rows[i].id);
}

static void apply_row(const fix_row *r)
{
    uint8_t old[16], nw[16];
    uint32_t n = unhex(r->old, old), va = rt_hook_va(r->module, r->rva);
    int st;
    if (r->kind == K_BYTES) {
        unhex(r->new_, nw);
        st = row_state(r);
        if (st == RT_HOOK_BAKED) st = rt_hook_patch(r->id, va, old, nw, n);
    } else if (r->kind == K_PTR) {
        st = row_state(r);
        if (st == RT_HOOK_BAKED) st = rt_hook_ptr(r->id, va, rt_hook_va(r->module, r->target), r->cell, r->shim);
    } else {
        st = row_state(r) == RT_HOOK_BAKED ? rt_hook_site(r->id, va, old, n, r->absoff, r->kind == K_JMP ? 0xE9 : 0xE8, r->cell, r->shim)
                                           : row_state(r);
    }
    rt_log("up fixrow %s %s", r->id, st == RT_HOOK_BAKED ? row_on(r) ? "on (build)" : "built in, switch off" : st == RT_HOOK_ABSENT ? "not in this build" : "bytes differ");
}

int rt_up_default_clan(const char *in, size_t n, char *out, size_t outn)
{
    if (n + 1 > outn) return -1;
    int layer = 0;
    for (size_t i = 0; i < n; i += 1)
        if ((i == 0 || in[i - 1] == '\n') && n - i >= 6 && !memcmp(in + i, "layer_", 6)) layer = 1;
    size_t o = 0;
    for (size_t i = 0; i < n;) {
        size_t e = i;
        while (e < n && in[e] != '\n') e++;
        if (e < n) e++;
        if (layer || e - i < 13 || memcmp(in + i, "default_clan ", 13)) {
            memcpy(out + o, in + i, e - i);
            o += e - i;
        }
        i = e;
    }
    out[o] = 0;
    return (int)o;
}

static void default_clan(void)
{
    char guest[PATH_MAX], host[PATH_MAX], rel[PATH_MAX], dst[PATH_MAX];
    int exists, ov;
    snprintf(guest, sizeof guest, "C:\\VTMB\\%s\\cfg\\config.cfg", rt_up_mod());
    if (rt_vfs_resolve(guest, host, sizeof host, &exists, &ov, rel, sizeof rel) || !exists) return;
    FILE *f = fopen(host, "rb");
    if (!f) return;
    static char in[1 << 16], out[1 << 16];
    size_t n = fread(in, 1, sizeof in, f);
    fclose(f);
    int m = rt_up_default_clan(in, n, out, sizeof out);
    if (m < 0 || (size_t)m == n) return;
    snprintf(dst, sizeof dst, "%s", host);
    if (!ov && rt_vfs_cow(rel, dst, sizeof dst)) return;
    f = fopen(dst, "wb");
    if (!f) return;
    fwrite(out, 1, (size_t)m, f);
    fclose(f);
    rt_log("up default clan line dropped from %s", guest);
}

void rt_up_fixes_apply(void)
{
    load_switches();
    for (size_t i = 0; i < NROWS; i++) apply_row(&k_rows[i]);
    if (rt_up_switch_on("Tweaks", "Default Clan Fix")) default_clan();
}

int rt_up_list_fixes(const char *game)
{
    rt_up_switches_reset();
    rt_up_plugins_reset();
    if (game) {
        char p[PATH_MAX], q[PATH_MAX];
        snprintf(p, sizeof p, "%s/Bin/loader/loader.ini", game);
        int got = rt_up_switches_read(p);
        printf("loader.ini %s\n", got < 0 ? "not found" : "read");
        for (uint32_t i = 0; i < rt_up_plugin_count(); i++) {
            const char *plugin, *key;
            rt_up_plugin_row(i, &plugin, &key);
            if (strcmp(key, "Enabled")) continue;
            snprintf(p, sizeof p, "%s/Bin/loader/%s.vtm", game, plugin);
            snprintf(q, sizeof q, "%s/Bin/loader/%s.ini", game, rt_up_plugin_ini(plugin));
            int present = access(p, R_OK) == 0;
            rt_up_plugin_set(plugin, "Enabled", present);
            if (present) rt_up_plugin_read(q, plugin);
        }
    }
    const char *sha = rt_up_build_sha();
    printf("build sha256 %s\n", sha ? sha : "none");
    for (uint32_t i = 0; i < rt_up_plugin_count(); i++) {
        const char *plugin, *key;
        rt_up_plugin_row(i, &plugin, &key);
        if (!strcmp(key, "Enabled")) printf("plugin %s %s\n", plugin, rt_up_plugin_on(plugin, key) ? "enabled" : "disabled");
    }
    for (size_t i = 0; i < NROWS; i++) {
        const fix_row *r = &k_rows[i];
        int st = row_state(r);
        printf("row %s %s+0x%x %s switch %s site %s\n", r->id, r->module, r->rva, r->kind == K_BYTES ? "bytes" : r->kind == K_JMP ? "jmp" : r->kind == K_PTR ? "ptr" : "call",
               row_on(r) ? "on" : "off", st == RT_HOOK_BAKED ? "patched" : st == RT_HOOK_ABSENT ? "vanilla" : "bytes differ");
    }
    printf("rows %zu\n", NROWS);
    return 0;
}

static void set_ini(const char *dir, const char *body)
{
    rt_up_plugins_reset();
    char p[PATH_MAX];
    snprintf(p, sizeof p, "%s/fixes.ini", dir);
    FILE *f = fopen(p, "w");
    if (!f) {
        rt_up_check(0, "fixes.ini can be written", "%s", p);
        return;
    }
    fputs("[Tweaks]\nDefault Clan Fix = 0\n", f);
    fputs(body, f);
    fclose(f);
    rt_up_switches_reset();
    rt_up_switches_read(p);
    load_switches();
}

static void put_state(const fix_row *r, int baked, uint8_t *save)
{
    uint8_t old[16], nw[16];
    uint32_t n = unhex(r->old, old), va = rt_hook_va(r->module, r->rva);
    if (r->kind == K_PTR) {
        uint32_t cell = RT_EXE_BASE + 0x800u + 4u * r->cell;
        uint8_t *p = rt_host(va);
        memcpy(save, p, 12);
        MEM32(va) = baked ? va + 4 : rt_hook_va(r->module, r->target);
        if (baked) {
            p[4] = 0xFF;
            p[5] = 0x25;
            memcpy(p + 6, &cell, 4);
        }
        return;
    }
    memcpy(save, rt_host(va), n);
    for (uint32_t i = 0; i < n; i++)
        if (r->absoff < 0 || i < (uint32_t)r->absoff || i >= (uint32_t)r->absoff + 4) ((uint8_t *)rt_host(va))[i] = old[i];
    if (baked) {
        uint8_t *p = rt_host(va);
        if (r->kind == K_BYTES) {
            unhex(r->new_, nw);
            memcpy(p, nw, n);
        } else {
            p[0] = r->kind == K_JMP ? 0xE9 : 0xE8;
            p[1] ^= 0xFF;
        }
    }
}

static void restore_state(const fix_row *r, const uint8_t *save)
{
    uint8_t old[16];
    memcpy(rt_host(rt_hook_va(r->module, r->rva)), save, r->kind == K_PTR ? 12 : unhex(r->old, old));
}

static uint8_t s_keep[NROWS][16];

static void all_state(int baked)
{
    for (size_t i = 0; i < NROWS; i++) put_state(&k_rows[i], baked, s_keep[i]);
}

static void all_restore(void)
{
    for (size_t i = 0; i < NROWS; i++) restore_state(&k_rows[i], s_keep[i]);
}

static void with_bytes(const fix_row *r, int baked, void (*fn)(const fix_row *))
{
    uint8_t save[16];
    put_state(r, baked, save);
    fn(r);
    restore_state(r, save);
}

static int s_result;
static uint32_t s_rec_va;

static uint32_t record_call(uint32_t va, int n, const uint32_t *a)
{
    (void)n;
    (void)a;
    s_rec_va = va;
    return 0x77;
}

static void probe_row(const fix_row *r)
{
    s_result = row_state(r);
}

static uint32_t cell_of(const fix_row *r) { return RT_EXE_BASE + 0x800u + 4u * r->cell; }

static const fix_row *row_by_id(const char *id)
{
    for (size_t i = 0; i < NROWS; i++)
        if (!strcmp(k_rows[i].id, id)) return &k_rows[i];
    return NULL;
}

static int mutated_reads_differ(const fix_row *r)
{
    uint8_t save[16], old[16];
    uint32_t n = unhex(r->old, old), va = rt_hook_va(r->module, r->rva);
    uint8_t *p = rt_host(va);
    memcpy(save, p, n);
    int before = row_state(r);
    p[0] ^= 0x5A;
    int after = row_state(r);
    memcpy(p, save, n);
    return before >= 0 && after == -1;
}

static void plugin_cases(const char *dir)
{
    const char *sha = rt_up_build_sha();
    int hex = sha && strlen(sha) == 64;
    for (size_t i = 0; hex && i < 64; i++) hex = (sha[i] >= '0' && sha[i] <= '9') || (sha[i] >= 'a' && sha[i] <= 'f');
    int any_baked = 0;
    for (size_t i = 0; i < NROWS; i++) any_baked |= row_state(&k_rows[i]) == RT_HOOK_BAKED;
    rt_up_check(any_baked ? hex : !sha, "a patched build embeds a 64-digit up.sha256 and a plain build embeds none", "baked %d sha %s",
                any_baked, sha ? sha : "none");
    const fix_row *tr = row_by_id("no_tracers"), *rag = row_by_id("ragdolls"),
                  *pro = row_by_id("protean_warform_b"), *fov = row_by_id("fov_reset_parse");
    rt_up_check(tr && rag && pro && fov, "the plug-in byte rows exist", "");
    if (!(tr && rag && pro && fov)) return;
    set_ini(dir, "");
    rt_up_check(row_on(tr) && row_on(rag) && row_on(pro) && row_on(fov), "plug-in rows are on with the shipped defaults",
                "tr %d rag %d pro %d fov %d", row_on(tr), row_on(rag), row_on(pro), row_on(fov));
    rt_up_plugin_set("vtmb-tweaks", "bNoTracers", 0);
    int tr_key = !row_on(tr);
    rt_up_plugins_reset();
    rt_up_plugin_set("vtmb-tweaks", "Enabled", 0);
    int tr_plugin = !row_on(tr);
    rt_up_check(tr_key && tr_plugin, "a plug-in row is off when its key or the whole plug-in is off", "key %d plugin %d", tr_key, tr_plugin);
    rt_up_plugins_reset();
    set_ini(dir, "[Tweaks]\nNon-Solid Ragdolls = 0\nNo Bullet Tracers = 0\nFOV Reset Fix = 0\n");
    rt_up_plugin_set("vtmb-tweaks", "bNonSolidRagdolls", 0);
    rt_up_plugin_set("vtmb-tweaks", "bNoTracers", 0);
    rt_up_plugin_set("vtmb-tweaks", "Enabled", 0);
    load_switches();
    int none = !row_on(rag) && !row_on(tr) && !row_on(fov) && !s_fx.fov;
    rt_up_plugins_reset();
    set_ini(dir, "[Tweaks]\nNon-Solid Ragdolls = 1\nNo Bullet Tracers = 1\nFOV Reset Fix = 0\n");
    rt_up_plugin_set("vtmb-tweaks", "bNonSolidRagdolls", 0);
    rt_up_plugin_set("vtmb-tweaks", "bNoTracers", 0);
    int loader_only = row_on(rag) && row_on(tr);
    rt_up_plugins_reset();
    set_ini(dir, "[Tweaks]\nNon-Solid Ragdolls = 0\nNo Bullet Tracers = 0\nFOV Reset Fix = 0\n");
    int plugin_only = row_on(rag) && row_on(tr) && row_on(fov) && s_fx.fov;
    rt_up_check(none && loader_only && plugin_only, "a doubly keyed row is on when either switch is on and off only when both are off",
                "none %d loader %d plugin %d", none, loader_only, plugin_only);
    rt_up_plugins_reset();
    rt_up_plugin_set("protean-improved", "Enabled", 0);
    int pro_plugin = !row_on(pro);
    rt_up_plugins_reset();
    rt_up_plugin_set("protean-improved", "Warform Frenzy", 0);
    int pro_key = !row_on(pro);
    rt_up_plugins_reset();
    rt_up_plugin_set("protean-improved", "Filter Load Fix", 0);
    int pro_other = row_on(pro);
    rt_up_check(pro_plugin && pro_key && pro_other, "the protean row follows its plug-in and its Warform Frenzy key only",
                "plugin %d key %d other %d", pro_plugin, pro_key, pro_other);
    rt_up_plugins_reset();
    rt_up_check(mutated_reads_differ(tr) && mutated_reads_differ(pro) && mutated_reads_differ(rag),
                "a changed byte at a row site is reported as bytes differ", "tracers %d protean %d ragdolls %d",
                mutated_reads_differ(tr), mutated_reads_differ(pro), mutated_reads_differ(rag));
    rt_up_plugins_reset();
}

void rt_up_fixes_selftest(const char *dir)
{
    rt_hook_revert_all();
    set_ini(dir, "[Bugs]\nFix Paths = 1\nFix Histories = 1\nNo Coronas Fix = 1\nLights Through Walls Fix = 1\nFix Claws Stats = 1\n"
                 "Force Shadows Rebuild = 3\n[Tweaks]\nFOV Reset Fix = 1\n");
    for (size_t i = 0; i < NROWS; i++) {
        const fix_row *r = &k_rows[i];
        rt_up_check(row_state(r) >= 0, r->id, "bytes at %s+0x%x are neither %s nor the patched form", r->module, r->rva, r->old);
        with_bytes(r, 1, probe_row);
        rt_up_check(s_result == RT_HOOK_BAKED, r->id, "patched site reads %d, want baked", s_result);
        with_bytes(r, 0, probe_row);
        rt_up_check(s_result == RT_HOOK_ABSENT, r->id, "vanilla site reads %d, want absent", s_result);
    }
    {
        int baked = 0, on = 0;
        for (size_t i = 0; i < NROWS; i++) {
            if (!row_on(&k_rows[i])) continue;
            on++;
            baked += row_state(&k_rows[i]) == RT_HOOK_BAKED;
        }
        rt_up_check(baked == 0 || baked == on, "the build is all vanilla or all patched across switched-on rows", "%d patched of %d switched on", baked, on);
    }
    {
        uint32_t cells[NROWS], prev[NROWS];
        for (size_t i = 0; i < NROWS; i++) cells[i] = cell_of(&k_rows[i]), prev[i] = MEM32(cells[i]);
        mprotect(rt_host(RT_EXE_BASE), 0x4000, PROT_READ);
        all_state(0);
        rt_up_fixes_apply();
        rt_up_check(rt_hook_count() == 0, "a vanilla build claims no row", "count %u", rt_hook_count());
        all_restore();
        all_state(1);
        rt_up_fixes_apply();
        uint32_t claimed = rt_hook_count(), bound = 0, same = 1, hooks = 0;
        for (size_t i = 0; i < NROWS; i++) {
            const fix_row *r = &k_rows[i];
            hooks += r->kind != K_BYTES;
            if (r->kind != K_BYTES) bound += MEM32(cells[i]) != prev[i] && MEM32(cells[i]) != 0;
            same &= rt_hook_claimed(rt_hook_va(r->module, r->rva), 1);
        }
        rt_up_check(claimed == NROWS && same, "a patched build claims every row", "claimed %u of %zu", claimed, NROWS);
        rt_up_check(bound == hooks, "every hook row binds its cell", "%u of %u cells bound", bound, hooks);
        rt_hook_revert_all();
        int back = 1;
        for (size_t i = 0; i < NROWS; i++) {
            const fix_row *r = &k_rows[i];
            uint8_t old[16];
            unhex(r->old, old);
            int32_t rel;
            memcpy(&rel, old + 1, 4);
            uint32_t want = r->kind == K_CALL ? rt_hook_va(r->module, r->rva) + 5u + (uint32_t)rel
                            : r->kind == K_PTR ? rt_hook_va(r->module, r->target) : prev[i];
            back &= MEM32(cells[i]) == want;
        }
        rt_up_check(back && !rt_hook_count(), "revert binds the original call target or restores the cell and releases every row",
                    "count %u", rt_hook_count());
        all_restore();
    }
    {
        uint32_t va = rt_heap_alloc(16, 1);
        uint8_t old[4] = {1, 2, 3, 4}, nw[4] = {9, 8, 7, 6};
        memcpy(rt_host(va), old, 4);
        int a = rt_hook_patch("t_data", va, old, nw, 4);
        int w = !memcmp(rt_host(va), nw, 4);
        int dup = rt_hook_patch("t_data", va, old, nw, 4);
        int c = rt_hook_claimed(va + 3, 1);
        rt_hook_revert("t_data");
        int r = !memcmp(rt_host(va), old, 4) && !rt_hook_claimed(va, 4);
        uint8_t bad[4] = {5, 5, 5, 5};
        int mm = rt_hook_patch("t_bad", va, bad, nw, 4);
        rt_up_check(a == RT_HOOK_WRITTEN && w && dup == -1 && c && r && mm == -1, "rt_hook_patch writes, rejects, claims and reverts",
                    "write %d new %d dup %d claim %d revert %d mismatch %d", a, w, dup, c, r, mm);
        rt_heap_free(va);
    }
    {
        uint32_t e = rt_heap_alloc(0x500, 1);
        uint8_t *b = rt_host(e);
        MEM32(e + 0x24) = 0x5f766e65u;
        MEM32(e + 0x28) = 0x69727073u;
        b[0x1a3] = 7;
        b[0x16c] = 3;
        b[0x464] = 0;
        uint32_t a[1] = {e};
        hook_coronas(a);
        int both = b[0x464] == 7 && b[0x16c] == 4;
        set_ini(dir, "[Bugs]\nNo Coronas Fix = 0\nLights Through Walls Fix = 1\n");
        b[0x464] = 0;
        b[0x16c] = 3;
        hook_coronas(a);
        int lights = b[0x464] == 0 && b[0x16c] == 4;
        set_ini(dir, "[Bugs]\nNo Coronas Fix = 1\nLights Through Walls Fix = 0\n");
        b[0x16c] = 3;
        hook_coronas(a);
        int coronas = b[0x464] == 7 && b[0x16c] == 3;
        set_ini(dir, "[Bugs]\nNo Coronas Fix = 0\nLights Through Walls Fix = 0\n");
        b[0x464] = 0;
        hook_coronas(a);
        int off = b[0x464] == 0 && b[0x16c] == 3;
        set_ini(dir, "[Bugs]\nNo Coronas Fix = 1\nLights Through Walls Fix = 1\n");
        MEM32(e + 0x28) = 0;
        b[0x464] = 0;
        hook_coronas(a);
        int other = b[0x464] == 0;
        a[0] = 0;
        hook_coronas(a);
        rt_up_check(both && lights && coronas && off && other, "coronas and lights hook outputs, each switch alone and off",
                    "both %d lights %d coronas %d off %d other %d", both, lights, coronas, off, other);
        rt_heap_free(e);
    }
    {
        uint32_t p = cl(0x4a0d50), d1 = cl(0x4a0d5c), d2 = cl(0x4a0d60), s0 = MEM32(p), s1 = MEM32(d1), s2 = MEM32(d2);
        MEM32(p) = 0x1234;
        MEM32(d1) = 5;
        MEM32(d2) = 6;
        hook_histories(NULL);
        int zeroed = !MEM32(d1) && !MEM32(d2);
        MEM32(p) = 0;
        MEM32(d1) = 5;
        MEM32(d2) = 6;
        hook_histories(NULL);
        int kept = MEM32(d1) == 5 && MEM32(d2) == 6;
        MEM32(p) = 0x1234;
        set_ini(dir, "[Bugs]\nFix Histories = 0\n");
        hook_histories(NULL);
        int off = MEM32(d1) == 5 && MEM32(d2) == 6;
        rt_up_check(zeroed && kept && off, "histories hook zeroes two cells only when the pointer is set and the switch is on",
                    "zeroed %d kept %d off %d", zeroed, kept, off);
        MEM32(p) = s0;
        MEM32(d1) = s1;
        MEM32(d2) = s2;
    }
    {
        uint32_t s = rt_heap_alloc(32, 1), g = rt_heap_alloc(32, 1);
        strcpy(rt_host(s), "item_w_claws");
        strcpy(rt_host(g), "item_w_fists");
        set_ini(dir, "[Bugs]\nFix Claws Stats = 1\n");
        uint32_t a[2] = {s, g};
        int on = hook_claws(a) == 0;
        set_ini(dir, "[Bugs]\nFix Claws Stats = 0\n");
        s_guest_call = record_call;
        s_rec_va = 0;
        uint32_t r = hook_claws(a);
        s_guest_call = rt_guest_call;
        rt_up_check(on, "claws hook calls item_w_claws equal to item_w_fists when on", "result nonzero");
        rt_up_check(s_rec_va == cl(0x1e28e0) && r == 0x77, "claws hook takes the original call when the switch is off",
                    "called 0x%08X returned 0x%X", s_rec_va, r);
        rt_heap_free(s);
        rt_heap_free(g);
    }
    {
        uint32_t m = cl(0x4cf004), s0 = MEM32(m);
        set_ini(dir, "[Bugs]\nForce Shadows Rebuild = 3\n");
        s_fx.shadows_ctr = 0;
        MEM32(m) = 77;
        uint32_t a[1] = {77}, r1 = hook_shadows(a), r2 = hook_shadows(a), r3 = hook_shadows(a), r4 = hook_shadows(a);
        a[0] = 78;
        s_fx.shadows_ctr = 2;
        uint32_t other = hook_shadows(a);
        set_ini(dir, "[Bugs]\nForce Shadows Rebuild = 0\n");
        s_fx.shadows_ctr = 2;
        a[0] = 77;
        uint32_t off = hook_shadows(a);
        rt_up_check(r1 == 77 && r2 == 77 && r3 == 78 && r4 == 77 && other == 78 && off == 77 && s_fx.shadows_ctr == 2,
                    "shadows hook fires on the Nth equal pass, then restarts; off leaves the compare alone",
                    "%u %u %u %u other %u off %u ctr %u", r1, r2, r3, r4, other, off, s_fx.shadows_ctr);
        MEM32(m) = s0;
    }
    {
        set_ini(dir, "[Tweaks]\nFOV Reset Fix = 1\n");
        s_fx.fov_set = 0;
        uint32_t a[1] = {55};
        int unset = hook_fov_store(a) == 55;
        s_fx.fov_v = 90;
        s_fx.fov_set = 1;
        int on = hook_fov_store(a) == 90;
        set_ini(dir, "[Tweaks]\nFOV Reset Fix = 0\n");
        rt_up_plugin_set("vtmb-tweaks", "Enabled", 0);
        load_switches();
        int off = hook_fov_store(a) == 55;
        rt_up_plugins_reset();
        rt_up_check(unset && on && off, "fov store hook returns the parsed FOV, else the caller's value", "unset %d on %d off %d", unset, on, off);
    }
    {
        char out[256];
        const char *a = "name x\ndefault_clan 5\nz 1\n", *b = "default_clan 5\nlayer_1 2\n", *c = "a\r\ndefault_clan 2\r\nb\r\n";
        int la = rt_up_default_clan(a, strlen(a), out, sizeof out);
        int ok = la >= 0 && !strcmp(out, "name x\nz 1\n");
        int lb = rt_up_default_clan(b, strlen(b), out, sizeof out);
        ok &= lb >= 0 && !strcmp(out, b);
        int lc = rt_up_default_clan(c, strlen(c), out, sizeof out);
        ok &= lc >= 0 && !strcmp(out, "a\r\nb\r\n");
        rt_up_check(ok, "default clan line dropped unless a layer_ line is present", "got %s", out);
    }
    plugin_cases(dir);
    rt_up_plugins_selftest();
    rt_up_switches_reset();
    rt_up_plugins_reset();
    load_switches();
}
