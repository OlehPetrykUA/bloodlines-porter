#include "recomp_types.h"
#include "rt.h"

#include <errno.h>
#include <string.h>
#include <sys/mman.h>

#define HOOK_MAX 128
#define CELL_SPAN 0x4000u
#define HOOK_BYTES 16
#define HOOK_CELLS 64

typedef struct {
    const char *id;
    uint32_t va;
    uint32_t n;
    int state;
    uint8_t old[HOOK_BYTES];
    uint32_t cell;
    uint32_t prev;
    uint32_t orig;
} hook_entry;

static hook_entry s_hooks[HOOK_MAX];
static uint32_t s_nhooks;

uint32_t rt_hook_va(const char *module, uint32_t rva)
{
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (!strcmp(rt_modules[m]->name, module)) return rt_modules[m]->base + rva;
    return 0;
}

static void cell_set(uint32_t cell, uint32_t v)
{
    void *page = rt_host(RT_EXE_BASE);
    if (mprotect(page, CELL_SPAN, PROT_READ | PROT_WRITE)) rt_fatal(5, "hook cell page cannot be made writable (errno %d)", errno);
    MEM32(cell) = v;
    if (mprotect(page, CELL_SPAN, PROT_READ)) rt_fatal(5, "hook cell page cannot be made read-only (errno %d)", errno);
}

static hook_entry *find(const char *id)
{
    for (uint32_t i = 0; i < s_nhooks; i++)
        if (!strcmp(s_hooks[i].id, id)) return &s_hooks[i];
    return NULL;
}

static hook_entry *add(const char *id, uint32_t va, uint32_t n, int state)
{
    if (!va || !n || n > HOOK_BYTES || s_nhooks >= HOOK_MAX || find(id)) return NULL;
    hook_entry *h = &s_hooks[s_nhooks++];
    memset(h, 0, sizeof *h);
    h->id = id;
    h->va = va;
    h->n = n;
    h->state = state;
    return h;
}

int rt_hook_patch(const char *id, uint32_t va, const uint8_t *old, const uint8_t *new_, uint32_t n)
{
    if (!va || !n || n > HOOK_BYTES) return -1;
    uint8_t *p = rt_host(va);
    if (!memcmp(p, old, n)) {
        hook_entry *h = add(id, va, n, RT_HOOK_WRITTEN);
        if (!h) return -1;
        memcpy(h->old, old, n);
        memcpy(p, new_, n);
        return RT_HOOK_WRITTEN;
    }
    if (!memcmp(p, new_, n)) {
        hook_entry *h = add(id, va, n, RT_HOOK_BAKED);
        return h ? RT_HOOK_BAKED : -1;
    }
    rt_log("hook %s: bytes at 0x%08X match neither old nor new", id, va);
    return -1;
}

int rt_hook_same(const uint8_t *p, const uint8_t *old, uint32_t n, int absoff)
{
    for (uint32_t i = 0; i < n; i++)
        if (p[i] != old[i] && !(absoff >= 0 && i >= (uint32_t)absoff && i < (uint32_t)absoff + 4)) return 0;
    return 1;
}

int rt_hook_site(const char *id, uint32_t va, const uint8_t *old, uint32_t n, int absoff, uint8_t opcode, uint32_t cell,
                 const rt_shim *shim)
{
    if (!va || n < 5 || n > HOOK_BYTES || cell >= HOOK_CELLS) return -1;
    const uint8_t *p = rt_host(va);
    if (rt_hook_same(p, old, n, absoff)) return RT_HOOK_ABSENT;
    if (p[0] != opcode) {
        rt_log("hook %s: site 0x%08X holds opcode 0x%02X, want old bytes or 0x%02X", id, va, p[0], opcode);
        return -1;
    }
    hook_entry *h = add(id, va, n, RT_HOOK_BAKED);
    if (!h) return -1;
    memcpy(h->old, old, n);
    h->cell = RT_EXE_BASE + 0x800u + 4u * cell;
    h->prev = MEM32(h->cell);
    if (opcode == 0xE8) {
        int32_t rel;
        memcpy(&rel, old + 1, 4);
        h->orig = va + 5u + (uint32_t)rel;
    }
    cell_set(h->cell, rt_dynamic_shim(shim->dll, shim->name, shim));
    return RT_HOOK_BAKED;
}

int rt_hook_ptr(const char *id, uint32_t va, uint32_t orig, uint32_t cell, const rt_shim *shim)
{
    if (!va || cell >= HOOK_CELLS) return -1;
    hook_entry *h = add(id, va, 4, RT_HOOK_BAKED);
    if (!h) return -1;
    memcpy(h->old, rt_host(va), 4);
    h->cell = RT_EXE_BASE + 0x800u + 4u * cell;
    h->prev = MEM32(h->cell);
    h->orig = orig;
    cell_set(h->cell, rt_dynamic_shim(shim->dll, shim->name, shim));
    return RT_HOOK_BAKED;
}

static void drop(hook_entry *h)
{
    *h = s_hooks[--s_nhooks];
}

int rt_hook_revert(const char *id)
{
    hook_entry *h = find(id);
    if (!h) return 0;
    if (h->state == RT_HOOK_WRITTEN) memcpy(rt_host(h->va), h->old, h->n);
    if (h->cell) cell_set(h->cell, h->orig ? h->orig : h->prev);
    drop(h);
    return 1;
}

void rt_hook_revert_all(void)
{
    while (s_nhooks) rt_hook_revert(s_hooks[s_nhooks - 1].id);
}

int rt_hook_claimed(uint32_t va, uint32_t n)
{
    for (uint32_t i = 0; i < s_nhooks; i++)
        if (va < s_hooks[i].va + s_hooks[i].n && s_hooks[i].va < va + n) return 1;
    return 0;
}

uint32_t rt_hook_count(void) { return s_nhooks; }

int rt_hook_info(uint32_t i, const char **id, uint32_t *va, int *state)
{
    if (i >= s_nhooks) return 0;
    *id = s_hooks[i].id;
    *va = s_hooks[i].va;
    *state = s_hooks[i].state;
    return 1;
}
