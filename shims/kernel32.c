#include "recomp_types.h"
#include "rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

static int cp1252_upper(uint32_t c)
{
    return (c >= 'A' && c <= 'Z') || c == 0x8A || c == 0x8C || c == 0x8E || c == 0x9F ||
           (c >= 0xC0 && c <= 0xDE && c != 0xD7);
}

static int cp1252_lower(uint32_t c)
{
    return (c >= 'a' && c <= 'z') || c == 0x83 || c == 0x9A || c == 0x9C || c == 0x9E ||
           c == 0xB5 || (c >= 0xDF && c != 0xF7);
}

static uint32_t ctype1(uint32_t c)
{
    uint32_t t = 0;
    if (cp1252_upper(c)) t |= 0x101;
    else if (cp1252_lower(c)) t |= 0x102;
    else if (c == 0x88 || c == 0xAA || c == 0xBA) t |= 0x100;
    else if (c >= '0' && c <= '9') t |= 0x84;
    else if (c == ' ' || c == 0xA0) t |= 0x48;
    else if (c >= 9 && c <= 13) t |= c == 9 ? 0x68 : 0x28;
    else if (c < 0x20 || c == 0x7F) t |= 0x20;
    else if (c == 0x81 || c == 0x8D || c == 0x8F || c == 0x90 || c == 0x9D) t |= 0x20;
    else t |= 0x10;
    if ((c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f')) t |= 0x80;
    return t;
}

static uint32_t span(uint32_t src, int32_t n)
{
    if (n >= 0) return (uint32_t)n;
    return (uint32_t)strlen((const char *)rt_host(src)) + 1;
}

static uint32_t GetStringTypeA(const uint32_t *a)
{
    if (a[1] != 1) rt_fatal(7, "GetStringTypeA info type %u not modelled", a[1]);
    uint32_t n = span(a[2], (int32_t)a[3]);
    if (!rt_span_ok(a[2], n) || !rt_span_ok(a[4], 2ull * n)) {
        rt_set_last_error(998);
        return 0;
    }
    for (uint32_t i = 0; i < n; i++) MEM16(a[4] + 2 * i) = (uint16_t)ctype1(MEM8(a[2] + i));
    return 1;
}

static uint32_t LCMapStringA(const uint32_t *a)
{
    uint32_t flags = a[1], src = a[2], dst = a[4], cap = a[5];
    uint32_t n = span(src, (int32_t)a[3]);
    if (flags != 0x100 && flags != 0x200) rt_fatal(7, "LCMapStringA flags 0x%X not modelled", flags);
    if (!cap) return n;
    if (cap < n) {
        rt_set_last_error(122);
        return 0;
    }
    if (!rt_span_ok(src, n)) {
        rt_set_last_error(998);
        return 0;
    }
    if (!rt_span_ok(dst, n)) {
        rt_set_last_error(998);
        return 0;
    }
    for (uint32_t i = 0; i < n; i++) {
        uint32_t c = MEM8(src + i);
        MEM8(dst + i) = (uint8_t)(flags == 0x200 ? rt_cp1252_to_upper(c) : rt_cp1252_to_lower(c));
    }
    return n;
}

static uint32_t GetVersion(const uint32_t *a) { (void)a; return 0x0A280105u; }

static uint32_t GetVersionExA(const uint32_t *a)
{
    uint32_t p = a[0];
    MEM32(p + 4) = 5;
    MEM32(p + 8) = 1;
    MEM32(p + 12) = 2600;
    MEM32(p + 16) = 2;
    MEM8(p + 20) = 0;
    return 1;
}

static uint32_t HeapCreate(const uint32_t *a) { (void)a; return rt_st()->heap_handle; }

static uint32_t HeapDestroy(const uint32_t *a) { (void)a; return 1; }

static uint32_t HeapAlloc(const uint32_t *a) { return rt_heap_alloc(a[2], (a[1] & 0x8u) != 0); }

static uint32_t HeapFree(const uint32_t *a)
{
    if (rt_heap_free(a[2])) return 1;
    rt_set_last_error(87);
    return 0;
}

static uint32_t HeapSize(const uint32_t *a) { return rt_heap_size(a[2]); }

static uint32_t HeapReAlloc(const uint32_t *a)
{
    if (!rt_heap_owns(a[2])) {
        rt_set_last_error(87);
        return 0;
    }
    return rt_heap_realloc(a[2], a[3], a[1]);
}

static uint32_t HeapValidate(const uint32_t *a) { return !a[2] || rt_heap_owns(a[2]); }

static uint32_t HeapCompact(const uint32_t *a) { (void)a; return 0x100000u; }

static uint32_t HeapWalk(const uint32_t *a)
{
    (void)a;
    rt_set_last_error(259);
    return 0;
}

static uint32_t VirtualAlloc(const uint32_t *a)
{
    uint32_t addr = a[0], size = a[1], base, n;
    if (addr) {
        if (!rt_pages_query(addr, &base, &n) && rt_tracing()) rt_log("VirtualAlloc at 0x%08X outside any reservation", addr);
        return addr & ~0xFFFu;
    }
    if (!size) {
        rt_set_last_error(87);
        return 0;
    }
    uint32_t p = rt_pages_reserve(size);
    if (!p) {
        rt_set_last_error(8);
        if (rt_tracing()) rt_log("VirtualAlloc of %u bytes failed", size);
    }
    return p;
}

static uint32_t VirtualFree(const uint32_t *a)
{
    if (a[2] & 0x8000u) rt_pages_release(a[0]);
    return 1;
}

static uint32_t VirtualQuery(const uint32_t *a)
{
    uint32_t va = a[0], p = a[1], base, n;
    if (a[2] < 28) {
        rt_set_last_error(24);
        return 0;
    }
    uint32_t page = va & ~0xFFFu, v[7] = {page, 0, 0, 0, 0x10000u, 0, 0};
    const rt_module *m = rt_owner(va);
    if (rt_pages_query(va, &base, &n)) {
        v[1] = base;
        v[2] = 4;
        v[3] = base + n - page;
        v[4] = 0x1000u;
        v[5] = 4;
        v[6] = 0x20000u;
    } else if (m) {
        int code = va >= m->code_lo && va < m->code_hi;
        v[1] = m->base;
        v[2] = 0x80;
        v[3] = code ? ((m->code_hi + 0xFFFu) & ~0xFFFu) - page : m->base + m->size - page;
        v[4] = 0x1000u;
        v[5] = code ? 0x20 : 4;
        v[6] = 0x1000000u;
    } else if (va - RT_EXE_BASE < 0x1000u) {
        v[1] = RT_EXE_BASE;
        v[2] = 2;
        v[3] = 0x1000u;
        v[4] = 0x1000u;
        v[5] = 2;
        v[6] = 0x1000000u;
    } else {
        v[3] = 0x1000u;
    }
    memcpy(rt_host(p), v, sizeof v);
    return 28;
}

static uint32_t GlobalAlloc(const uint32_t *a)
{
    uint32_t p = rt_heap_alloc(a[1] ? a[1] : 1, (a[0] & 0x40u) != 0);
    if (!p) rt_set_last_error(8);
    return p;
}

static uint32_t GlobalFree(const uint32_t *a) { return rt_heap_free(a[0]) ? 0 : a[0]; }

static uint32_t GlobalLock(const uint32_t *a) { return a[0]; }

static uint32_t GlobalUnlock(const uint32_t *a)
{
    (void)a;
    rt_set_last_error(0);
    return 0;
}

static uint32_t GlobalSize(const uint32_t *a) { return rt_heap_owns(a[0]) ? rt_heap_size(a[0]) : 0; }

static uint32_t GlobalMemoryStatus(const uint32_t *a)
{
    uint32_t v[8] = {32, 10, 0x7FF00000u, 0x70000000u, 0x7FF00000u, 0x70000000u, 0x7FFE0000u, 0x70000000u};
    memcpy(rt_host(a[0]), v, sizeof v);
    return 0;
}

static uint32_t GetSystemInfo(const uint32_t *a)
{
    uint32_t v[9] = {0, 4096, 0x00010000u, 0x7FFEFFFFu, 1, 1, 586, 0x10000u, 15u | (0x0209u << 16)};
    memcpy(rt_host(a[0]), v, sizeof v);
    return 0;
}

static uint32_t OutputDebugStringA(const uint32_t *a)
{
    const char *s = rt_gstr(a[0]);
    size_t n = strlen(s);
    rt_guest_text(0, s, (uint32_t)n);
    if (!n || s[n - 1] != '\n') rt_guest_text(0, "\n", 1);
    return 0;
}

static char *s_env[256];
static int s_nenv = -1;

static void env_init(void)
{
    if (s_nenv >= 0) return;
    s_nenv = 0;
    for (const char *p = rt_gstr(rt_st()->env_a); *p; p += strlen(p) + 1) s_env[s_nenv++] = strdup(p);
}

static int env_find(const char *name)
{
    size_t n = strlen(name);
    for (int i = 0; i < s_nenv; i++)
        if (!strncasecmp(s_env[i], name, n) && s_env[i][n] == '=') return i;
    return -1;
}

static void env_publish(void)
{
    uint32_t n = 1;
    for (int i = 0; i < s_nenv; i++) n += (uint32_t)strlen(s_env[i]) + 1;
    uint32_t va = rt_heap_alloc(n, 1), o = va;
    for (int i = 0; i < s_nenv; i++) {
        size_t k = strlen(s_env[i]) + 1;
        memcpy(rt_host(o), s_env[i], k);
        o += (uint32_t)k;
    }
    MEM8(o) = 0;
    rt_st()->env_a = va;
}

static uint32_t GetEnvironmentVariableA(const uint32_t *a)
{
    env_init();
    int i = env_find(rt_gstr(a[0]));
    if (i < 0) {
        rt_set_last_error(203);
        return 0;
    }
    const char *v = strchr(s_env[i], '=') + 1;
    uint32_t n = (uint32_t)strlen(v);
    if (a[2] < n + 1) return n + 1;
    if (!rt_span_ok(a[1], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[1]), v, n + 1);
    return n;
}

static uint32_t SetEnvironmentVariableA(const uint32_t *a)
{
    env_init();
    const char *name = rt_gstr(a[0]);
    int i = env_find(name);
    if (i >= 0) {
        free(s_env[i]);
        s_env[i] = s_env[--s_nenv];
    }
    if (a[1] && s_nenv < 255) {
        char b[2048];
        snprintf(b, sizeof b, "%s=%s", name, rt_gstr(a[1]));
        s_env[s_nenv++] = strdup(b);
    }
    env_publish();
    return 1;
}

static uint32_t lstrcpyA(const uint32_t *a)
{
    size_t len = strlen(rt_gstr(a[1]));
    if (!rt_span_ok(a[0], (uint64_t)len + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memmove(rt_host(a[0]), rt_host(a[1]), len + 1);
    return a[0];
}

static uint32_t lstrcatA(const uint32_t *a)
{
    size_t n = strlen(rt_gstr(a[0])), len = strlen(rt_gstr(a[1]));
    if (!rt_span_ok(a[0], (uint64_t)n + len + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memmove((char *)rt_host(a[0]) + n, rt_host(a[1]), len + 1);
    return a[0];
}

static uint32_t lstrlenA(const uint32_t *a) { return a[0] ? (uint32_t)strlen(rt_gstr(a[0])) : 0; }

static uint32_t lstrlenW(const uint32_t *a)
{
    uint32_t n = 0;
    if (a[0])
        while (MEM16(a[0] + 2 * n)) n++;
    return n;
}

static const uint16_t k_cp1252_hi[32] = {
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
    0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178};

static uint16_t to_wide(uint8_t c) { return c >= 0x80 && c < 0xA0 ? k_cp1252_hi[c - 0x80] : c; }

int rt_cp1252_from_wide(uint16_t w)
{
    if (w < 0x80 || (w >= 0xA0 && w < 0x100)) return w;
    for (int i = 0; i < 32; i++)
        if (k_cp1252_hi[i] == w) return 0x80 + i;
    return -1;
}

static uint32_t MultiByteToWideChar(const uint32_t *a)
{
    uint32_t src = a[2], dst = a[4], cap = a[5];
    uint32_t n = (int32_t)a[3] < 0 ? (uint32_t)strlen(rt_gstr(src)) + 1 : a[3];
    if (!cap) return n;
    uint32_t w = cap < n ? cap : n;
    if (!rt_span_ok(src, w) || !rt_span_ok(dst, 2ull * w)) {
        rt_set_last_error(998);
        return 0;
    }
    for (uint32_t i = 0; i < w; i++) MEM16(dst + 2 * i) = to_wide(MEM8(src + i));
    if (w < n) {
        rt_set_last_error(122);
        return 0;
    }
    return n;
}

static uint32_t WideCharToMultiByte(const uint32_t *a)
{
    uint32_t src = a[2], dst = a[4], cap = a[5], used = a[7];
    uint32_t n = a[3];
    if ((int32_t)n < 0) {
        n = 0;
        while (MEM16(src + 2 * n)) n++;
        n++;
    }
    if (used) MEM32(used) = 0;
    if (!cap) return n;
    uint32_t w = cap < n ? cap : n;
    if (w < n && a[0] == 42) {
        rt_set_last_error(122);
        return 0;
    }
    if (!rt_span_ok(src, 2ull * w) || !rt_span_ok(dst, w)) {
        rt_set_last_error(998);
        return 0;
    }
    uint8_t def = a[6] ? MEM8(a[6]) : '?';
    for (uint32_t i = 0; i < w; i++) {
        int c = rt_cp1252_from_wide(MEM16(src + 2 * i));
        if (c < 0 && used) MEM32(used) = 1;
        MEM8(dst + i) = (uint8_t)(c < 0 ? def : c);
    }
    if (w < n) {
        rt_set_last_error(122);
        return 0;
    }
    return n;
}

void rt_kernel32_text_selftest(void)
{
    uint32_t s8 = rt_heap_alloc(16, 1), s16 = rt_heap_alloc(32, 1), d16 = rt_heap_alloc(32, 0), d8 = rt_heap_alloc(16, 0);
    memcpy((void *)XBOX_PTR(s8), "abc", 4);
    for (int i = 0; i < 4; i++) MEM16(s16 + 2 * i) = (uint16_t)"abc"[i];
    for (int i = 0; i < 8; i++) MEM16(d16 + 2 * i) = 0xFFFF;
    uint32_t m1[6] = {0, 0, s8, 0xFFFFFFFFu, d16, 3};
    uint32_t r = MultiByteToWideChar(m1), e = rt_get_last_error();
    rt_up_check(!r && e == 122 && MEM16(d16) == 'a' && MEM16(d16 + 4) == 'c' && MEM16(d16 + 6) == 0xFFFF,
                "MultiByteToWideChar with a buffer one short fills it, then fails with ERROR_INSUFFICIENT_BUFFER", "ret %u error %u", r, e);
    m1[5] = 4;
    r = MultiByteToWideChar(m1);
    rt_up_check(r == 4 && MEM16(d16 + 6) == 0, "MultiByteToWideChar with room converts the string and its NUL", "ret %u", r);
    memset((void *)XBOX_PTR(d8), 0x7E, 8);
    uint32_t w1[8] = {1252, 0, s16, 0xFFFFFFFFu, d8, 3, 0, 0};
    r = WideCharToMultiByte(w1), e = rt_get_last_error();
    rt_up_check(!r && e == 122 && !memcmp((void *)XBOX_PTR(d8), "abc~", 4),
                "WideCharToMultiByte with a buffer one short fills it, then fails with ERROR_INSUFFICIENT_BUFFER", "ret %u error %u", r, e);
    memset((void *)XBOX_PTR(d8), 0x7E, 8);
    w1[0] = 42;
    r = WideCharToMultiByte(w1);
    rt_up_check(!r && MEM8(d8) == 0x7E, "WideCharToMultiByte with CP_SYMBOL and a short buffer writes nothing", "ret %u", r);
    rt_heap_free(d8);
    rt_heap_free(d16);
    rt_heap_free(s16);
    rt_heap_free(s8);
}

static uint32_t IsBadPtr(const uint32_t *a) { return !a[0] || a[0] >= RT_GUEST_TOP; }

static uint32_t GetCurrentProcessId(const uint32_t *a) { (void)a; return 0x10; }

static uint32_t SetErrorMode(const uint32_t *a)
{
    static uint32_t mode;
    uint32_t old = mode;
    mode = a[0];
    return old;
}

static uint32_t SetLastError(const uint32_t *a)
{
    rt_set_last_error(a[0]);
    return 0;
}

static uint32_t GetUserDefaultLCID(const uint32_t *a) { (void)a; return 0x409; }

static uint32_t IsValidCodePage(const uint32_t *a) { return a[0] == 1252 || a[0] == 437; }

static uint32_t IsValidLocale(const uint32_t *a) { return a[0] == 0x409 || a[0] == 0x400 || a[0] == 0x800; }

static uint32_t CompareStringA(const uint32_t *a)
{
    const char *x = rt_gstr(a[2]), *y = rt_gstr(a[4]);
    uint32_t nx = (int32_t)a[3] < 0 ? (uint32_t)strlen(x) : a[3], ny = (int32_t)a[5] < 0 ? (uint32_t)strlen(y) : a[5];
    int fold = (a[1] & 1u) != 0;
    for (uint32_t i = 0; i < nx && i < ny; i++) {
        uint32_t c = (unsigned char)x[i], d = (unsigned char)y[i];
        if (fold) {
            c = rt_cp1252_to_lower(c);
            d = rt_cp1252_to_lower(d);
        }
        if (c != d) return c < d ? 1 : 3;
    }
    return nx == ny ? 2 : nx < ny ? 1 : 3;
}

static uint32_t FatalAppExitA(const uint32_t *a)
{
    rt_log("FatalAppExitA \"%s\"", rt_gstr(a[1]));
    rt_stop(5, MEM32(g_esp), "guest FatalAppExitA: %s", rt_gstr(a[1]));
}

static uint32_t GetCommandLineA(const uint32_t *a) { (void)a; return rt_st()->cmdline; }

static uint32_t GetEnvironmentStrings(const uint32_t *a) { (void)a; return rt_st()->env_a; }

static uint32_t not_on_this_platform(const uint32_t *a)
{
    (void)a;
    rt_set_last_error(120);
    return 0;
}

static uint32_t FreeEnvironmentStrings(const uint32_t *a) { (void)a; return 1; }

static uint32_t GetStartupInfoA(const uint32_t *a)
{
    memset(rt_host(a[0]), 0, 68);
    MEM32(a[0]) = 68;
    return 0;
}

static uint32_t SetHandleCount(const uint32_t *a) { return a[0]; }

static uint32_t GetACP(const uint32_t *a) { (void)a; return 1252; }

static uint32_t GetOEMCP(const uint32_t *a) { (void)a; return 437; }

static uint32_t GetCPInfo(const uint32_t *a)
{
    uint32_t p = a[1];
    memset(rt_host(p), 0, 18);
    MEM32(p) = 1;
    MEM8(p + 4) = '?';
    return 1;
}

static uint32_t GetLastError(const uint32_t *a) { (void)a; return rt_get_last_error(); }

static uint32_t GetCurrentProcess(const uint32_t *a) { (void)a; return 0xFFFFFFFFu; }

static uint32_t ExitProcess(const uint32_t *a)
{
    if (!rt_tracing()) rt_fatal(11, "guest called ExitProcess(%u)", a[0]);
    rt_log("guest ExitProcess(%u)", a[0]);
    rt_exit((int)a[0]);
}

static uint32_t TerminateProcess(const uint32_t *a)
{
    if (a[0] != 0xFFFFFFFFu) {
        rt_set_last_error(6);
        return 0;
    }
    if (!rt_tracing()) rt_fatal(11, "guest called TerminateProcess(%u)", a[1]);
    rt_log("guest TerminateProcess(%u)", a[1]);
    rt_exit((int)a[1]);
}

static uint32_t IsProcessorFeaturePresent(const uint32_t *a) { (void)a; return 0; }

const rt_shim rt_kernel32_shims[] = {
    {"kernel32.dll", "GetLocaleInfoW", 4, not_on_this_platform},
    {"kernel32.dll", "GetFullPathNameW", 4, not_on_this_platform},
    {"kernel32.dll", "GetFileAttributesW", 1, not_on_this_platform},
    {"kernel32.dll", "GetDriveTypeW", 1, not_on_this_platform},
    {"kernel32.dll", "GetCurrentDirectoryW", 2, not_on_this_platform},
    {"kernel32.dll", "GetModuleFileNameW", 3, not_on_this_platform},
    {"kernel32.dll", "GetCommandLineW", 0, not_on_this_platform},
    {"kernel32.dll", "CompareStringA", 6, CompareStringA},
    {"kernel32.dll", "CompareStringW", 6, not_on_this_platform},
    {"kernel32.dll", "ExitProcess", 1, ExitProcess},
    {"kernel32.dll", "FatalAppExitA", 2, FatalAppExitA},
    {"kernel32.dll", "FreeEnvironmentStringsA", 1, FreeEnvironmentStrings},
    {"kernel32.dll", "FreeEnvironmentStringsW", 1, FreeEnvironmentStrings},
    {"kernel32.dll", "GetACP", 0, GetACP},
    {"kernel32.dll", "GetCommandLineA", 0, GetCommandLineA},
    {"kernel32.dll", "GetCPInfo", 2, GetCPInfo},
    {"kernel32.dll", "GetCurrentProcess", 0, GetCurrentProcess},
    {"kernel32.dll", "GetCurrentProcessId", 0, GetCurrentProcessId},
    {"kernel32.dll", "GetEnvironmentStrings", 0, GetEnvironmentStrings},
    {"kernel32.dll", "GetEnvironmentStringsW", 0, not_on_this_platform},
    {"kernel32.dll", "GetEnvironmentVariableA", 3, GetEnvironmentVariableA},
    {"kernel32.dll", "GetLastError", 0, GetLastError},
    {"kernel32.dll", "GetOEMCP", 0, GetOEMCP},
    {"kernel32.dll", "GetStartupInfoA", 1, GetStartupInfoA},
    {"kernel32.dll", "GetStringTypeA", 5, GetStringTypeA},
    {"kernel32.dll", "GetStringTypeW", 4, not_on_this_platform},
    {"kernel32.dll", "GetSystemInfo", 1, GetSystemInfo},
    {"kernel32.dll", "GetUserDefaultLCID", 0, GetUserDefaultLCID},
    {"kernel32.dll", "GetVersion", 0, GetVersion},
    {"kernel32.dll", "GetVersionExA", 1, GetVersionExA},
    {"kernel32.dll", "GlobalAlloc", 2, GlobalAlloc},
    {"kernel32.dll", "GlobalFree", 1, GlobalFree},
    {"kernel32.dll", "GlobalLock", 1, GlobalLock},
    {"kernel32.dll", "GlobalMemoryStatus", 1, GlobalMemoryStatus},
    {"kernel32.dll", "GlobalSize", 1, GlobalSize},
    {"kernel32.dll", "GlobalUnlock", 1, GlobalUnlock},
    {"kernel32.dll", "HeapAlloc", 3, HeapAlloc},
    {"kernel32.dll", "HeapCompact", 2, HeapCompact},
    {"kernel32.dll", "HeapCreate", 3, HeapCreate},
    {"kernel32.dll", "HeapDestroy", 1, HeapDestroy},
    {"kernel32.dll", "HeapFree", 3, HeapFree},
    {"kernel32.dll", "HeapReAlloc", 4, HeapReAlloc},
    {"kernel32.dll", "HeapSize", 3, HeapSize},
    {"kernel32.dll", "HeapValidate", 3, HeapValidate},
    {"kernel32.dll", "HeapWalk", 2, HeapWalk},
    {"kernel32.dll", "IsBadCodePtr", 1, IsBadPtr},
    {"kernel32.dll", "IsBadReadPtr", 2, IsBadPtr},
    {"kernel32.dll", "IsBadWritePtr", 2, IsBadPtr},
    {"kernel32.dll", "IsProcessorFeaturePresent", 1, IsProcessorFeaturePresent},
    {"kernel32.dll", "IsValidCodePage", 1, IsValidCodePage},
    {"kernel32.dll", "IsValidLocale", 2, IsValidLocale},
    {"kernel32.dll", "LCMapStringA", 6, LCMapStringA},
    {"kernel32.dll", "LCMapStringW", 6, not_on_this_platform},
    {"kernel32.dll", "LocalFree", 1, GlobalFree},
    {"kernel32.dll", "lstrcatA", 2, lstrcatA},
    {"kernel32.dll", "lstrcpyA", 2, lstrcpyA},
    {"kernel32.dll", "lstrlenA", 1, lstrlenA},
    {"kernel32.dll", "lstrlenW", 1, lstrlenW},
    {"kernel32.dll", "MultiByteToWideChar", 6, MultiByteToWideChar},
    {"kernel32.dll", "OutputDebugStringA", 1, OutputDebugStringA},
    {"kernel32.dll", "SetEnvironmentVariableA", 2, SetEnvironmentVariableA},
    {"kernel32.dll", "SetErrorMode", 1, SetErrorMode},
    {"kernel32.dll", "SetHandleCount", 1, SetHandleCount},
    {"kernel32.dll", "SetLastError", 1, SetLastError},
    {"kernel32.dll", "TerminateProcess", 2, TerminateProcess},
    {"kernel32.dll", "VirtualAlloc", 4, VirtualAlloc},
    {"kernel32.dll", "VirtualFree", 3, VirtualFree},
    {"kernel32.dll", "VirtualQuery", 3, VirtualQuery},
    {"kernel32.dll", "WideCharToMultiByte", 8, WideCharToMultiByte},
};

const uint32_t rt_nkernel32_shims = sizeof rt_kernel32_shims / sizeof rt_kernel32_shims[0];
