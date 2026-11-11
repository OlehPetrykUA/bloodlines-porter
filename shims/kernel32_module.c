#include "recomp_types.h"
#include "rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define MAXMOD 64

static uint8_t s_started[MAXMOD], s_starting[MAXMOD], s_no_thread_calls[MAXMOD];
static uint32_t s_loads[MAXMOD];
static const char *s_pseudo[64];
static int s_npseudo;
static uint32_t s_filter;

static const char *const k_system[] = {"kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "winmm.dll",
                                       "d3d9.dll", "dinput8.dll", "ole32.dll", "oleaut32.dll", "shell32.dll",
                                       "shlwapi.dll", "wsock32.dll", "ws2_32.dll", "dbghelp.dll", "binkw32.dll",
                                       "mss32.dll", "ntdll.dll", "version.dll", "comctl32.dll", "dsound.dll",
                                       "ddraw.dll", "imm32.dll", "d3dx9_24.dll", "dinput.dll"};

static const char *base_name(const char *p)
{
    const char *a = strrchr(p, '\\'), *b = strrchr(p, '/');
    const char *s = a > b ? a : b;
    return s ? s + 1 : p;
}

static int mod_index(uint32_t base)
{
    for (uint32_t m = 0; m < rt_nmodules && m < MAXMOD; m++)
        if (rt_modules[m]->base == base) return (int)m;
    return -1;
}

static int mod_by_file(const char *file)
{
    for (uint32_t m = 0; m < rt_nmodules && m < MAXMOD; m++)
        if (!rt_stricmp(base_name(rt_modules[m]->path), file)) return (int)m;
    return -1;
}

static int mod_by_name(const char *name)
{
    for (uint32_t m = 0; m < rt_nmodules && m < MAXMOD; m++)
        if (!strcmp(rt_modules[m]->name, name)) return (int)m;
    return -1;
}

static void pseudo_init(void)
{
    if (s_npseudo) return;
    for (size_t i = 0; i < sizeof k_system / sizeof k_system[0]; i++) s_pseudo[s_npseudo++] = k_system[i];
    for (uint32_t i = 0; i < rt_nimports && s_npseudo < 64; i++) {
        int seen = 0;
        for (int k = 0; k < s_npseudo && !seen; k++) seen = !strcasecmp(s_pseudo[k], rt_imports[i]->dll);
        if (!seen) s_pseudo[s_npseudo++] = rt_imports[i]->dll;
    }
}

static int pseudo_of(const char *file)
{
    pseudo_init();
    for (int k = 0; k < s_npseudo; k++)
        if (!rt_stricmp(s_pseudo[k], file)) return k;
    return -1;
}

static const char *pseudo_name(uint32_t h)
{
    pseudo_init();
    if (h < RT_PSEUDO_MOD || (h - RT_PSEUDO_MOD) % 0x10000u) return NULL;
    uint32_t k = (h - RT_PSEUDO_MOD) / 0x10000u;
    return k < (uint32_t)s_npseudo ? s_pseudo[k] : NULL;
}

static void start_module(int m)
{
    if (s_started[m] || s_starting[m]) return;
    s_starting[m] = 1;
    const rt_module *mod = rt_modules[m];
    for (uint32_t d = 0; d < mod->ndeps; d++) {
        int k = mod_by_name(mod->deps[d]);
        if (k >= 0) start_module(k);
    }
    uint32_t r = 1;
    if (mod->entry != mod->base) {
        uint32_t args[3] = {mod->base, 1, 0};
        r = rt_guest_call(mod->entry, 3, args);
    }
    if (rt_stage_reached() <= 4) rt_stage(4, "%s = %u", mod->name, r);
    else rt_log("dllmain %s = %u", mod->name, r);
    if (!r) rt_stop(13, mod->entry, "DllMain of %s returned FALSE", mod->name);
    s_started[m] = 1;
}

void rt_modules_thread_attach(void)
{
    for (uint32_t m = 0; m < rt_nmodules && m < MAXMOD; m++) {
        const rt_module *mod = rt_modules[m];
        if (!s_started[m] || s_no_thread_calls[m] || mod->entry == mod->base) continue;
        uint32_t args[3] = {mod->base, 2, 0};
        rt_guest_call(mod->entry, 3, args);
    }
}

void rt_module_disable_thread_calls(uint32_t base)
{
    int m = mod_index(base);
    if (m >= 0) s_no_thread_calls[m] = 1;
}

uint32_t rt_module_load(const char *path, int start, uint32_t *err)
{
    char file[260];
    snprintf(file, sizeof file, "%s", base_name(path));
    if (!strchr(file, '.')) strncat(file, ".dll", sizeof file - strlen(file) - 1);
    size_t n = strlen(file);
    while (n && (file[n - 1] == ' ' || file[n - 1] == '.')) file[--n] = 0;
    int m = mod_by_file(file);
    if (m >= 0) {
        s_loads[m]++;
        if (start) start_module(m);
        return rt_modules[m]->base;
    }
    int k = pseudo_of(file);
    if (k >= 0) return RT_PSEUDO_MOD + 0x10000u * (uint32_t)k;
    rt_log("LoadLibrary: no module %s", path);
    *err = 126;
    return 0;
}

static uint32_t lookup_pseudo(const char *dll, const char *name)
{
    for (uint32_t i = 0; i < rt_nimports; i++)
        if (!strcasecmp(rt_imports[i]->dll, dll) && !strcmp(rt_imports[i]->name, name)) return rt_imports[i]->va;
    const rt_shim *s = rt_find_shim(dll, name);
    return s && s->fn ? rt_dynamic_shim(dll, name, s) : 0;
}

static uint32_t export_of(uint32_t base, const char *name, uint32_t ord, int depth)
{
    const char *pn = pseudo_name(base);
    if (pn) {
        char b[16];
        if (!name) snprintf(b, sizeof b, "#%u", ord);
        return lookup_pseudo(pn, name ? name : b);
    }
    if (mod_index(base) < 0 && base != RT_EXE_BASE) return 0;
    uint32_t pe = base + MEM32(base + 0x3C);
    uint32_t dir = MEM32(pe + 0x78), size = MEM32(pe + 0x7C);
    if (!dir) return 0;
    uint32_t ed = base + dir, ob = MEM32(ed + 0x10), nf = MEM32(ed + 0x14), nn = MEM32(ed + 0x18);
    uint32_t funcs = base + MEM32(ed + 0x1C), names = base + MEM32(ed + 0x20), ords = base + MEM32(ed + 0x24);
    uint32_t idx = 0xFFFFFFFFu;
    if (name) {
        for (uint32_t i = 0; i < nn; i++)
            if (!strcmp(rt_gstr(base + MEM32(names + 4 * i)), name)) {
                idx = MEM16(ords + 2 * i);
                break;
            }
    } else if (ord >= ob) {
        idx = ord - ob;
    }
    if (idx >= nf) return 0;
    uint32_t rva = MEM32(funcs + 4 * idx);
    if (!rva) return 0;
    if (rva >= dir && rva < dir + size) {
        char fw[256];
        snprintf(fw, sizeof fw, "%s", rt_gstr(base + rva));
        char *dot = strrchr(fw, '.');
        if (!dot || depth >= 8) return 0;
        *dot = 0;
        uint32_t e = 0, t = rt_module_load(fw, 0, &e);
        if (!t) return 0;
        if (dot[1] == '#') return export_of(t, NULL, (uint32_t)strtoul(dot + 2, NULL, 10), depth + 1);
        return export_of(t, dot + 1, 0, depth + 1);
    }
    return base + rva;
}

uint32_t rt_module_export(uint32_t base, uint32_t name_or_ord)
{
    if (name_or_ord < 0x10000u) return export_of(base, NULL, name_or_ord, 0);
    return export_of(base, rt_gstr(name_or_ord), 0, 0);
}

static uint32_t LoadLibraryA(const uint32_t *a)
{
    uint32_t e = 0, h = rt_module_load(rt_gstr(a[0]), 1, &e);
    if (!h) rt_set_last_error(e);
    return h;
}

static uint32_t LoadLibraryExA(const uint32_t *a)
{
    uint32_t e = 0, h = rt_module_load(rt_gstr(a[0]), !(a[2] & 3u), &e);
    if (!h) rt_set_last_error(e);
    return h;
}

static uint32_t GetProcAddress(const uint32_t *a)
{
    uint32_t r = rt_module_export(a[0], a[1]);
    if (!r) {
        if (a[1] < 0x10000u) rt_log("GetProcAddress(0x%08X, #%u): not found", a[0], a[1]);
        else rt_log("GetProcAddress(0x%08X, \"%s\"): not found", a[0], rt_gstr(a[1]));
        rt_set_last_error(127);
    }
    return r;
}

static uint32_t FreeLibrary(const uint32_t *a)
{
    int m = mod_index(a[0]);
    if (m >= 0 && s_loads[m]) s_loads[m]--;
    return m >= 0 || pseudo_name(a[0]) ? 1 : 0;
}

static uint32_t GetModuleHandleA(const uint32_t *a)
{
    if (!a[0]) return RT_EXE_BASE;
    char file[260];
    snprintf(file, sizeof file, "%s", base_name(rt_gstr(a[0])));
    if (!strchr(file, '.')) strncat(file, ".dll", sizeof file - strlen(file) - 1);
    if (!rt_stricmp(file, "vampire.exe")) return RT_EXE_BASE;
    int m = mod_by_file(file);
    if (m >= 0 && (s_started[m] || s_loads[m])) return rt_modules[m]->base;
    if (m < 0) {
        int k = pseudo_of(file);
        if (k >= 0) return RT_PSEUDO_MOD + 0x10000u * (uint32_t)k;
    }
    rt_set_last_error(126);
    return 0;
}

static uint32_t GetModuleFileNameA(const uint32_t *a)
{
    char b[300];
    int m = mod_index(a[0]);
    const char *pn = pseudo_name(a[0]);
    if (!a[0] || a[0] == RT_EXE_BASE) snprintf(b, sizeof b, "C:\\VTMB\\vampire.exe");
    else if (m >= 0) {
        snprintf(b, sizeof b, "C:\\VTMB\\%s", rt_modules[m]->path);
        for (char *c = b; *c; c++)
            if (*c == '/') *c = '\\';
    } else if (pn) snprintf(b, sizeof b, "C:\\WINDOWS\\system32\\%s", pn);
    else {
        rt_set_last_error(126);
        return 0;
    }
    uint32_t n = (uint32_t)strlen(b), size = a[2];
    if (!size) return 0;
    uint32_t c = n < size ? n : size - 1;
    if (!rt_span_ok(a[1], (uint64_t)c + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[1]), b, c);
    MEM8(a[1] + c) = 0;
    return c;
}

static uint32_t DisableThreadLibraryCalls(const uint32_t *a)
{
    rt_module_disable_thread_calls(a[0]);
    return 1;
}

static int is_call_ret(uint32_t v)
{
    const rt_module *m = rt_owner(v);
    if (!m || v < m->code_lo || v >= m->code_hi || v < 6) return 0;
    return MEM8(v - 5) == 0xE8 || (MEM8(v - 6) == 0xFF && (MEM8(v - 5) & 0x38) == 0x10) ||
           (MEM8(v - 2) == 0xFF && (MEM8(v - 1) & 0x38) == 0x10) || (MEM8(v - 3) == 0xFF && (MEM8(v - 2) & 0x38) == 0x10);
}

void rt_seh_dump(void)
{
    char w[512];
    uint32_t rec = MEM32(g_fs_base ? g_fs_base : RT_TIB);
    rt_log("  SEH chain (fs:[0] = 0x%08X):", rec);
    for (int i = 0; i < 32 && rec && rec != 0xFFFFFFFFu && rec < RT_GUEST_TOP; i++) {
        uint32_t next = MEM32(rec), h = MEM32(rec + 4);
        rt_log("    frame 0x%08X next 0x%08X handler %s scope 0x%08X try level %d", rec, next, rt_where(h, w, sizeof w),
               MEM32(rec + 8), (int)MEM32(rec + 12));
        rec = next;
    }
    rt_log("  unhandled exception filter 0x%08X", s_filter);
    int n = 0;
    for (uint32_t p = g_esp; p < g_esp + 0x10000u && p < RT_GUEST_TOP && n < 32; p += 4)
        if (is_call_ret(MEM32(p))) {
            rt_log("    stack 0x%08X: %s", p, rt_where(MEM32(p), w, sizeof w));
            n++;
        }
}

static uint32_t RaiseException(const uint32_t *a)
{
    uint32_t code = a[0], caller = MEM32(g_esp);
    if (code == 0x406D1388u && a[2] >= 3 && a[3]) {
        uint32_t info = a[3];
        uint32_t t = MEM32(info + 8);
        rt_thread *self = rt_self();
        if (t == 0xFFFFFFFFu) t = self ? self->tid : 0;
        rt_log("thread 0x%X named \"%s\"", t, rt_gstr(MEM32(info + 4)));
        return 0;
    }
    char args[256] = "";
    size_t o = 0;
    for (uint32_t i = 0; i < a[2] && i < 8 && a[3]; i++)
        o += (size_t)snprintf(args + o, sizeof args - o, " 0x%08X", MEM32(a[3] + 4 * i));
    rt_stop(12, caller, "RaiseException code 0x%08X%s flags 0x%X, %u arguments%s", code,
            code == 0xE06D7363u ? " (MSVC C++ throw)" : "", a[1], a[2], args);
}

static uint32_t SetUnhandledExceptionFilter(const uint32_t *a)
{
    uint32_t old = s_filter;
    s_filter = a[0];
    return old;
}

static uint32_t UnhandledExceptionFilter(const uint32_t *a)
{
    (void)a;
    rt_stop(12, MEM32(g_esp), "UnhandledExceptionFilter reached (guest exception dispatch)");
}

static uint32_t RtlUnwind(const uint32_t *a)
{
    (void)a;
    rt_stop(12, MEM32(g_esp), "RtlUnwind reached (guest exception dispatch)");
}

const rt_shim rt_kernel32_module_shims[] = {
    {"kernel32.dll", "DisableThreadLibraryCalls", 1, DisableThreadLibraryCalls},
    {"kernel32.dll", "FreeLibrary", 1, FreeLibrary},
    {"kernel32.dll", "GetModuleFileNameA", 3, GetModuleFileNameA},
    {"kernel32.dll", "GetModuleHandleA", 1, GetModuleHandleA},
    {"kernel32.dll", "GetProcAddress", 2, GetProcAddress},
    {"kernel32.dll", "LoadLibraryA", 1, LoadLibraryA},
    {"kernel32.dll", "LoadLibraryExA", 3, LoadLibraryExA},
    {"kernel32.dll", "RaiseException", 4, RaiseException},
    {"kernel32.dll", "RtlUnwind", 4, RtlUnwind},
    {"kernel32.dll", "SetUnhandledExceptionFilter", 1, SetUnhandledExceptionFilter},
    {"kernel32.dll", "UnhandledExceptionFilter", 1, UnhandledExceptionFilter},
};

const uint32_t rt_nkernel32_module_shims = sizeof rt_kernel32_module_shims / sizeof rt_kernel32_module_shims[0];
