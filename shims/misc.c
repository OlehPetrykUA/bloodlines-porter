#include "recomp_types.h"
#include "rt.h"

#include <arpa/inet.h>
#include <string.h>

static uint32_t s_ok(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t GetStockObject(const uint32_t *a) { return 0x00070000u + (a[0] & 0xFFu); }

static uint32_t one(const uint32_t *a)
{
    (void)a;
    return 1;
}

static uint32_t SelectObject(const uint32_t *a) { return a[1] ? 0x00070000u : 0; }

static uint32_t s_gdi_next = 0x00071000u;

static uint32_t new_gdi(const uint32_t *a)
{
    (void)a;
    return s_gdi_next += 4;
}

static uint32_t zero(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t GetTextMetricsA(const uint32_t *a)
{
    memset(rt_host(a[1]), 0, 53);
    MEM32(a[1]) = 16;
    MEM32(a[1] + 4) = 13;
    MEM32(a[1] + 8) = 3;
    MEM32(a[1] + 20) = 8;
    MEM32(a[1] + 24) = 16;
    return 1;
}

static uint32_t GetDeviceCaps(const uint32_t *a)
{
    int w, h;
    rt_desktop_size(&w, &h);
    switch (a[1]) {
    case 8: return (uint32_t)w;
    case 10: return (uint32_t)h;
    case 12: return 32;
    case 14: return 1;
    case 88: case 90: return 96;
    case 116: return 60;
    default: return 0;
    }
}

static uint32_t DrawTextA(const uint32_t *a)
{
    (void)a;
    return 16;
}

static uint32_t s_empty;

static uint32_t AIL_set_redist_directory(const uint32_t *a)
{
    (void)a;
    if (!s_empty) s_empty = rt_heap_alloc(4, 1);
    return s_empty;
}

static uint32_t swap16(const uint32_t *a) { return ntohs((uint16_t)a[0]); }

static uint32_t inet_addr_(const uint32_t *a) { return a[0] ? inet_addr((const char *)rt_host(a[0])) : INADDR_NONE; }

const rt_shim rt_misc_shims[] = {
    {"wsock32.dll", "#9", 1, swap16},
    {"wsock32.dll", "#10", 1, inet_addr_},
    {"wsock32.dll", "#15", 1, swap16},
    {"mss32.dll", "_AIL_set_redist_directory@4", 1, AIL_set_redist_directory},
    {"mss32.dll", "_AIL_shutdown@0", 0, zero},
    {"mss32.dll", "_AIL_startup@0", 0, one},
    {"gdi32.dll", "BitBlt", 9, one},
    {"gdi32.dll", "CreateCompatibleDC", 1, new_gdi},
    {"gdi32.dll", "CreateFontA", 14, new_gdi},
    {"gdi32.dll", "CreatePen", 3, new_gdi},
    {"gdi32.dll", "DeleteDC", 1, one},
    {"gdi32.dll", "ExtTextOutA", 8, one},
    {"gdi32.dll", "GetDeviceCaps", 2, GetDeviceCaps},
    {"gdi32.dll", "GetTextMetricsA", 2, GetTextMetricsA},
    {"gdi32.dll", "SetBkColor", 2, zero},
    {"gdi32.dll", "SetBkMode", 2, one},
    {"gdi32.dll", "SetTextAlign", 2, zero},
    {"gdi32.dll", "SetTextColor", 2, zero},
    {"user32.dll", "DrawTextA", 5, DrawTextA},
    {"gdi32.dll", "DeleteObject", 1, one},
    {"gdi32.dll", "GetStockObject", 1, GetStockObject},
    {"gdi32.dll", "SelectObject", 2, SelectObject},
    {"user32.dll", "FillRect", 3, one},
    {"ole32.dll", "CoInitialize", 1, s_ok},
    {"ole32.dll", "OleInitialize", 1, s_ok},
    {"ole32.dll", "RegisterDragDrop", 2, s_ok},
    {"ole32.dll", "RevokeDragDrop", 1, s_ok},
};

const uint32_t rt_nmisc_shims = sizeof rt_misc_shims / sizeof rt_misc_shims[0];
