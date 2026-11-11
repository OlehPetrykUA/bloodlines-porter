#include "recomp_types.h"
#include "rt.h"

static uint32_t once(const char *what, uint32_t r)
{
    rt_log("deliberate failure %s -> 0x%08X", what, r);
    return r;
}

static uint32_t CoCreateInstance(const uint32_t *a)
{
    if (a[4]) MEM32(a[4]) = 0;
    return once("CoCreateInstance", 0x80040154u);
}

static uint32_t WSAStartup(const uint32_t *a)
{
    (void)a;
    return once("WSAStartup", 10091);
}

static uint32_t no_process(const uint32_t *a)
{
    (void)a;
    rt_set_last_error(50);
    return once("process creation (CreatePipe/CreateProcessA)", 0);
}

static uint32_t WSACleanup(const uint32_t *a)
{
    (void)a;
    return once("WSACleanup (WSANOTINITIALISED)", 0xFFFFFFFFu);
}

static uint32_t gethostbyname_(const uint32_t *a)
{
    (void)a;
    return once("gethostbyname (WSANOTINITIALISED)", 0);
}

static uint32_t WSAGetLastError(const uint32_t *a)
{
    (void)a;
    return 10093;
}

static uint32_t CreateDIBSection(const uint32_t *a)
{
    if (a[3] && rt_span_ok(a[3], 4)) MEM32(a[3]) = 0;
    return once("CreateDIBSection (no Win32 font raster)", 0);
}

static uint32_t gdi_false(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t GetGlyphOutlineA(const uint32_t *a)
{
    (void)a;
    return 0xFFFFFFFFu;
}

static uint32_t OpenClipboard(const uint32_t *a)
{
    (void)a;
    return once("OpenClipboard (no host clipboard)", 0);
}

static uint32_t ShellExecuteA(const uint32_t *a)
{
    (void)a;
    return once("ShellExecuteA (ERROR_FILE_NOT_FOUND)", 2);
}

const rt_shim rt_fail_shims[] = {
    {"user32.dll", "OpenClipboard", 1, OpenClipboard, RT_SHIM_FAIL},
    {"shell32.dll", "ShellExecuteA", 6, ShellExecuteA, RT_SHIM_FAIL},
    {"gdi32.dll", "CreateDIBSection", 6, CreateDIBSection, RT_SHIM_FAIL},
    {"gdi32.dll", "GetCharABCWidthsA", 4, gdi_false, RT_SHIM_FAIL},
    {"gdi32.dll", "GetCharABCWidthsW", 4, gdi_false, RT_SHIM_FAIL},
    {"gdi32.dll", "GetGlyphOutlineA", 7, GetGlyphOutlineA, RT_SHIM_FAIL},
    {"gdi32.dll", "ExtTextOutW", 8, gdi_false, RT_SHIM_FAIL},
    {"gdi32.dll", "MoveToEx", 4, gdi_false, RT_SHIM_FAIL},
    {"wsock32.dll", "#52", 1, gethostbyname_, RT_SHIM_FAIL},
    {"wsock32.dll", "#111", 0, WSAGetLastError, RT_SHIM_FAIL},
    {"wsock32.dll", "#116", 0, WSACleanup, RT_SHIM_FAIL},
    {"kernel32.dll", "CreatePipe", 4, no_process, RT_SHIM_FAIL},
    {"kernel32.dll", "CreateProcessA", 10, no_process, RT_SHIM_FAIL},
    {"ole32.dll", "CoCreateInstance", 5, CoCreateInstance, RT_SHIM_FAIL},
    {"wsock32.dll", "#115", 2, WSAStartup, RT_SHIM_FAIL},
    {"ws2_32.dll", "#115", 2, WSAStartup, RT_SHIM_FAIL},
};

const uint32_t rt_nfail_shims = sizeof rt_fail_shims / sizeof rt_fail_shims[0];
