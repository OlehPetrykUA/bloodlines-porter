#include "recomp_types.h"
#include "rt.h"

#include <pthread.h>
#include <string.h>

#define MAGIC 0x50414548u
#define FREED 0x45455246u
#define SEG 0x100000u
#define BIG 0x40000u
#define NCLASS 96

typedef struct {
    uint32_t va, size;
} range;

static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static uint32_t s_lo = RT_HEAP_LO, s_hi = RT_VIRT_HI, s_next = RT_HEAP_LO, s_high, s_failures;
static range s_res[65536], s_free_pages[16384];
static uint32_t s_nres, s_nfree_pages;
static uint32_t s_bins[NCLASS], s_seg, s_seg_end;

void rt_heap_init(uint32_t lo, uint32_t hi)
{
    pthread_mutex_lock(&s_mu);
    s_lo = s_next = lo;
    s_hi = hi;
    s_high = 0;
    s_failures = 0;
    s_nres = s_nfree_pages = 0;
    memset(s_bins, 0, sizeof s_bins);
    s_seg = s_seg_end = 0;
    pthread_mutex_unlock(&s_mu);
}

static uint32_t round64k(uint32_t n) { return (n + 0xFFFFu) & ~0xFFFFu; }

static uint32_t reserve_locked(uint32_t size)
{
    uint32_t n = round64k(size);
    if (!n || n < size) return 0;
    for (uint32_t i = 0; i < s_nfree_pages; i++)
        if (s_free_pages[i].size >= n) {
            uint32_t va = s_free_pages[i].va;
            s_free_pages[i].va += n;
            s_free_pages[i].size -= n;
            if (!s_free_pages[i].size) s_free_pages[i] = s_free_pages[--s_nfree_pages];
            memset(rt_host(va), 0, n);
            if (s_nres < sizeof s_res / sizeof s_res[0]) s_res[s_nres++] = (range){va, n};
            return va;
        }
    if (n > s_hi - s_next) {
        s_failures++;
        return 0;
    }
    uint32_t va = s_next;
    s_next += n;
    if (s_next - s_lo > s_high) s_high = s_next - s_lo;
    if (s_nres < sizeof s_res / sizeof s_res[0]) s_res[s_nres++] = (range){va, n};
    return va;
}

uint32_t rt_pages_reserve(uint32_t size)
{
    pthread_mutex_lock(&s_mu);
    uint32_t va = reserve_locked(size);
    pthread_mutex_unlock(&s_mu);
    return va;
}

static void add_free_locked(range r)
{
    for (uint32_t i = 0; i < s_nfree_pages;) {
        range f = s_free_pages[i];
        if (f.va + f.size != r.va && r.va + r.size != f.va) {
            i++;
            continue;
        }
        r = (range){f.va < r.va ? f.va : r.va, f.size + r.size};
        s_free_pages[i] = s_free_pages[--s_nfree_pages];
    }
    if (s_nfree_pages < sizeof s_free_pages / sizeof s_free_pages[0]) s_free_pages[s_nfree_pages++] = r;
}

static int release_locked(uint32_t va)
{
    for (uint32_t i = 0; i < s_nres; i++)
        if (s_res[i].va == va) {
            add_free_locked(s_res[i]);
            s_res[i] = s_res[--s_nres];
            return 1;
        }
    return 0;
}

int rt_pages_release(uint32_t va)
{
    pthread_mutex_lock(&s_mu);
    int ok = release_locked(va);
    pthread_mutex_unlock(&s_mu);
    return ok;
}

int rt_pages_query(uint32_t va, uint32_t *base, uint32_t *size)
{
    pthread_mutex_lock(&s_mu);
    int ok = 0;
    for (uint32_t i = 0; i < s_nres && !ok; i++)
        if (va - s_res[i].va < s_res[i].size) {
            *base = s_res[i].va;
            *size = s_res[i].size;
            ok = 1;
        }
    pthread_mutex_unlock(&s_mu);
    return ok;
}

static int class_of(uint32_t n, uint32_t *cap)
{
    if (n <= 1024) {
        uint32_t c = n <= 16 ? 16 : (n + 15u) & ~15u;
        *cap = c;
        return (int)(c / 16u) - 1;
    }
    uint32_t c = 2048, k = 64;
    while (c < n) {
        c *= 2;
        k++;
    }
    *cap = c;
    return (int)k;
}

uint32_t rt_heap_alloc(uint32_t size, int zero)
{
    if (size > 0x7FFF0000u) {
        s_failures++;
        return 0;
    }
    uint32_t cap;
    pthread_mutex_lock(&s_mu);
    uint32_t p = 0;
    if (size > BIG) {
        uint32_t va = reserve_locked(size + 16u);
        if (va) {
            p = va + 16u;
            cap = round64k(size + 16u) - 16u;
        }
    } else {
        int k = class_of(size, &cap);
        if (s_bins[k]) {
            p = s_bins[k];
            s_bins[k] = MEM32(p);
            if (zero) memset(rt_host(p), 0, cap);
        } else {
            if (s_seg_end - s_seg < cap + 16u) {
                uint32_t va = reserve_locked(SEG);
                if (va) {
                    s_seg = va;
                    s_seg_end = va + SEG;
                }
            }
            if (s_seg_end - s_seg >= cap + 16u) {
                p = s_seg + 16u;
                s_seg += cap + 16u;
            }
        }
    }
    if (p) {
        MEM32(p - 16) = MAGIC;
        MEM32(p - 12) = cap;
        MEM32(p - 8) = 0;
        MEM32(p - 4) = size;
    } else {
        s_failures++;
    }
    pthread_mutex_unlock(&s_mu);
    return p;
}

int rt_heap_owns(uint32_t p) { return p >= 16u && p < RT_GUEST_TOP && MEM32(p - 16) == MAGIC; }

int rt_heap_free(uint32_t p)
{
    if (!p) return 1;
    if (!rt_heap_owns(p)) return 0;
    pthread_mutex_lock(&s_mu);
    uint32_t cap = MEM32(p - 12);
    MEM32(p - 16) = FREED;
    if (cap > BIG) {
        release_locked(p - 16u);
    } else {
        uint32_t c;
        int k = class_of(cap, &c);
        MEM32(p) = s_bins[k];
        s_bins[k] = p;
    }
    pthread_mutex_unlock(&s_mu);
    return 1;
}

uint32_t rt_heap_size(uint32_t p) { return rt_heap_owns(p) ? MEM32(p - 4) : 0xFFFFFFFFu; }

uint32_t rt_heap_realloc(uint32_t p, uint32_t size, uint32_t flags)
{
    uint32_t n = MEM32(p - 4), cap = MEM32(p - 12);
    if (size <= cap) {
        if ((flags & 0x8u) && size > n) memset(rt_host(p + n), 0, size - n);
        MEM32(p - 4) = size;
        return p;
    }
    if (flags & 0x10u) return 0;
    uint32_t q = rt_heap_alloc(size, 0);
    if (!q) return 0;
    memmove(rt_host(q), rt_host(p), n);
    if (flags & 0x8u) memset(rt_host(q + n), 0, size - n);
    rt_heap_free(p);
    return q;
}

uint32_t rt_heap_high_water(void) { return s_high; }

uint32_t rt_heap_failures(void) { return s_failures; }

uint32_t rt_alloc(uint32_t size) { return rt_heap_alloc(size, 0); }

void rt_heap_selftest(void)
{
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    uint32_t a = rt_pages_reserve(0x100000), b = rt_pages_reserve(0x100000), c = rt_pages_reserve(0x100000), d = rt_pages_reserve(0x10000);
    rt_pages_release(a);
    rt_pages_release(c);
    rt_pages_release(b);
    uint32_t abc = rt_pages_reserve(0x300000);
    rt_up_check(abc == a, "a block freed between two free neighbours merges with both", "3 MB request at 0x%08X, first block 0x%08X", abc, a);
    rt_pages_release(abc);
    uint32_t part = rt_pages_reserve(0x100000);
    rt_pages_release(part);
    uint32_t whole = rt_pages_reserve(0x300000);
    rt_up_check(part == a && whole == a, "the front of a split range merges back when freed", "1 MB at 0x%08X, then 3 MB at 0x%08X", part, whole);
    rt_pages_release(whole);
    rt_pages_release(d);
    uint32_t seed = 12345u, p[48], most = 0, before = rt_heap_high_water();
    for (int round = 0; round < 40; round++) {
        uint32_t total = 0;
        for (int i = 0; i < 48; i++) {
            seed = seed * 1103515245u + 12345u;
            uint32_t n = 300000u + (seed >> 8) % 1800000u;
            p[i] = rt_heap_alloc(n, 0);
            total += round64k(n + 16u);
        }
        if (total > most) most = total;
        for (int i = 47; i > 0; i--) {
            seed = seed * 1103515245u + 12345u;
            int j = (int)((seed >> 8) % (uint32_t)(i + 1));
            uint32_t t = p[i];
            p[i] = p[j];
            p[j] = t;
        }
        for (int i = 0; i < 48; i++) rt_heap_free(p[i]);
    }
    uint32_t high = rt_heap_high_water(), fails = rt_heap_failures();
    rt_up_check(!fails && high <= before + most + 0x200000u, "40 rounds of big blocks freed in random order keep the heap's high-water flat",
                "high-water %u KB, largest round %u KB, %u failed allocations", high >> 10, most >> 10, fails);
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
}
