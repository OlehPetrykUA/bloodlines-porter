#include "recomp_types.h"
#include "rt.h"

#include <string.h>

static uint32_t keep_eax(const uint32_t *a) { (void)a; return g_eax; }

static uint32_t Plat_Alloc(const uint32_t *a) { return rt_heap_alloc(a[0], 0); }

static uint32_t Plat_Free(const uint32_t *a)
{
    rt_heap_free(a[0]);
    return g_eax;
}

static uint32_t Plat_Realloc(const uint32_t *a)
{
    uint32_t old = a[0], size = a[1];
    if (!old) return rt_heap_alloc(size, 0);
    if (!rt_heap_owns(old)) return 0;
    return rt_heap_realloc(old, size, 0);
}

const rt_shim rt_tier0_shims[] = {
    {"tier0.dll", "Plat_Alloc", 0, Plat_Alloc},
    {"tier0.dll", "Plat_Free", 0, Plat_Free},
    {"tier0.dll", "Plat_Realloc", 0, Plat_Realloc},
    {"tier0.dll", "DevMsg", 0, keep_eax},
    {"tier0.dll", "AssertValidStringPtr", 0, keep_eax},
    {"tier0.dll", "_AssertValidReadPtr", 0, keep_eax},
    {"tier0.dll", "_AssertValidWritePtr", 0, keep_eax},
};

const uint32_t rt_ntier0_shims = sizeof rt_tier0_shims / sizeof rt_tier0_shims[0];
