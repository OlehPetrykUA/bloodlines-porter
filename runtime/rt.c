#include "recomp_types.h"
#include "rt.h"

#include <math.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <unistd.h>

ptrdiff_t g_xbox_mem_offset;
int g_force_return;
uint32_t g_xbox_code_lo, g_xbox_code_hi;
RecompCpu g_cpu = {.r_fp_control_word = 0x027F};
volatile uint32_t g_icall_trace[ICALL_TRACE_SIZE];
volatile uint32_t g_icall_trace_idx;
volatile uint64_t g_icall_count;

static uint64_t s_shim_calls, s_misses;
static char s_extra_path[1024];
static unsigned char *s_arenas[4];
static int s_narenas;
static RECOMP_TLS uint32_t s_pending_shim;
static const rt_shim **s_dyn;
static const char **s_dyn_dll, **s_dyn_name;
static uint32_t s_ndyn;
static const rt_shim **s_bound;
static uint8_t s_page_mod[RT_GUEST_TOP >> 16];

void *rt_host(uint32_t va) { return (void *)XBOX_PTR(va); }

rt_state *rt_st(void) { return (rt_state *)rt_host(RT_STATE); }

static void on_fault(int sig, siginfo_t *si, void *uap)
{
    (void)uap;
    unsigned char *a = (unsigned char *)si->si_addr;
    for (int i = 0; i < s_narenas; i++)
        if (a >= s_arenas[i] && a < s_arenas[i] + RT_ARENA_SIZE + RT_ARENA_GUARD) {
            unsigned long long off = (unsigned long long)(a - s_arenas[i]);
            if (off < RT_ARENA_SIZE)
                rt_stop(4, (uint32_t)off, "guest memory fault at guest address 0x%08X (arena offset 0x%09llX)",
                        (unsigned)off, off);
            rt_stop(4, 0, "guest memory fault past arena end (arena offset 0x%09llX)", off);
        }
    rt_stop(6, 0, "host fault, signal %d at host address %p", sig, (void *)a);
}

static pthread_key_t s_alt_key;
static pthread_once_t s_alt_once = PTHREAD_ONCE_INIT;

static void alt_free(void *p)
{
    stack_t ss = {.ss_flags = SS_DISABLE};
    sigaltstack(&ss, NULL);
    free(p);
}

static void alt_key(void)
{
    pthread_key_create(&s_alt_key, alt_free);
}

void rt_altstack(void)
{
    stack_t ss;
    ss.ss_size = 1u << 20;
    ss.ss_sp = malloc(ss.ss_size);
    ss.ss_flags = 0;
    if (!ss.ss_sp) return;
    if (sigaltstack(&ss, NULL)) {
        free(ss.ss_sp);
        return;
    }
    pthread_once(&s_alt_once, alt_key);
    pthread_setspecific(s_alt_key, ss.ss_sp);
}

static void install_fault_handler(void)
{
    static int done;
    if (done) return;
    done = 1;
    rt_altstack();
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_fault;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);
    sigaction(SIGABRT, &sa, NULL);
}

void *rt_arena_new(void)
{
    if (s_narenas == 4) rt_fatal(5, "too many arenas");
    unsigned char *p = mmap(NULL, RT_ARENA_SIZE + RT_ARENA_GUARD, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    if (p == MAP_FAILED) rt_fatal(5, "cannot reserve the 4 GiB guest arena");
    if (mprotect(p, (size_t)getpagesize(), PROT_NONE) != 0) rt_fatal(5, "cannot protect guest page 0");
    if (mprotect(p + RT_ARENA_SIZE, RT_ARENA_GUARD, PROT_NONE) != 0) rt_fatal(5, "cannot protect the arena tail");
    s_arenas[s_narenas++] = p;
    install_fault_handler();
    return p;
}

void rt_arena_use(void *arena) { g_xbox_mem_offset = (ptrdiff_t)arena; }

const rt_shim *rt_find_shim(const char *dll, const char *name)
{
    for (uint32_t t = 0; t < rt_nshim_tables; t++)
        for (uint32_t i = 0; i < *rt_shim_tables[t].n; i++) {
            const rt_shim *s = &rt_shim_tables[t].rows[i];
            if (!strcasecmp(s->dll, dll) && !strcmp(s->name, name)) return s;
        }
    return NULL;
}

uint32_t rt_put_string(const char *str)
{
    uint32_t n = (uint32_t)strlen(str) + 1u, va = rt_alloc(n);
    if (!va) rt_fatal(5, "guest heap exhausted");
    memcpy(rt_host(va), str, n);
    return va;
}

const char *rt_gstr(uint32_t va) { return va ? (const char *)rt_host(va) : ""; }

int rt_span_ok(uint32_t va, uint64_t n) { return n <= RT_GUEST_TOP && va <= RT_GUEST_TOP - n; }

static uint32_t put_bytes(const void *src, uint32_t n)
{
    uint32_t va = rt_alloc(n);
    if (!va) rt_fatal(5, "guest heap exhausted");
    memcpy(rt_host(va), src, n);
    return va;
}

void rt_load(void)
{
    g_xbox_code_lo = 0xFFFFFFFFu;
    g_xbox_code_hi = 0;
    for (uint32_t m = 0; m < rt_nmodules; m++) {
        const rt_module *mod = rt_modules[m];
        if (mod->base + (uint64_t)mod->size > RT_GUEST_TOP) rt_fatal(5, "module %s ends above the guest top", mod->name);
        memcpy(rt_host(mod->base), mod->image, mod->size);
        if (mod->code_lo < g_xbox_code_lo) g_xbox_code_lo = mod->code_lo;
        if (mod->code_hi > g_xbox_code_hi) g_xbox_code_hi = mod->code_hi;
        for (uint32_t p = mod->base >> 16; p < (mod->base + mod->size + 0xFFFFu) >> 16; p++) s_page_mod[p] = (uint8_t)(m + 1);
        mod->dispatch_init();
    }
    rt_state *st = rt_st();
    memset(st, 0, sizeof *st);
    st->heap_next = RT_HEAP_LO;
    st->virt_next = RT_VIRT_LO;
    st->heap_handle = 0x00500000u;
    static const char cmd[] = "vampire.exe";
    static const char env[] = "VTMB=1\0";
    static const char path[] = "C:\\VTMB\\Bin\\vstdlib.dll";
    st->cmdline = put_bytes(cmd, sizeof cmd);
    st->env_a = put_bytes(env, sizeof env);
    st->module_path = put_bytes(path, sizeof path);
    uint32_t *tib = rt_host(RT_TIB);
    memset(tib, 0, 0x1000);
    tib[0] = 0xFFFFFFFFu;
    tib[1] = RT_STACK_TOP + 0x100u;
    tib[2] = RT_STACK_LO;
    tib[6] = RT_TIB;
    free(s_bound);
    s_bound = calloc(rt_nimports + 1, sizeof *s_bound);
    if (!s_bound) rt_fatal(5, "out of host memory");
    for (uint32_t i = 0; i < rt_nimports; i++)
        s_bound[i] = rt_find_shim(rt_imports[i]->dll, rt_imports[i]->name);
}

const rt_shim *rt_bound(uint32_t i) { return i < rt_nimports && s_bound[i] && s_bound[i]->fn ? s_bound[i] : NULL; }

static int s_icall_trace = -1;

recomp_func_t recomp_lookup(uint32_t va)
{
    if (va >= RT_GUEST_TOP || !s_page_mod[va >> 16]) return NULL;
    if (s_icall_trace < 0) s_icall_trace = getenv("RT_TRACE_ICALLS") != NULL;
    if (s_icall_trace && rt_tracing() && rt_self()) {
        char w[512], c[512];
        rt_log("icall %s from %s", rt_where(va, w, sizeof w), rt_where(MEM32(g_esp), c, sizeof c));
    }
    return rt_modules[s_page_mod[va >> 16] - 1]->lookup(va);
}

void rt_simd_trap(const char *text, uint32_t va)
{
    rt_fatal(9, "SIMD instruction '%s' at guest address 0x%08X is not translated", text, va);
}

void rt_reset_cpu(void)
{
    g_eax = g_ecx = g_edx = g_ebx = g_esi = g_edi = g_ebp = g_seh_ebp = 0;
    g_esp = RT_STACK_TOP;
    g_fs_base = RT_TIB;
    g_df = 0;
    g_eflags = 0x202u;
    g_fp_top = 0;
    g_fp_control_word = 0x027F;
    g_fp_cc = 0;
    memset(g_fp_stack, 0, sizeof g_fp_stack);
}

const rt_shim *rt_shim_at(uint32_t i, const char **dll, const char **name)
{
    if (i < rt_nimports) {
        *dll = rt_imports[i]->dll;
        *name = rt_imports[i]->name;
        return s_bound[i];
    }
    i -= rt_nimports;
    if (i < s_ndyn) {
        *dll = s_dyn_dll[i];
        *name = s_dyn_name[i];
        return s_dyn[i];
    }
    *dll = "?";
    *name = "?";
    return NULL;
}

uint32_t rt_dynamic_shim(const char *dll, const char *name, const rt_shim *s)
{
    for (uint32_t j = 0; j < s_ndyn; j++)
        if (s_dyn[j] == s && !strcasecmp(s_dyn_dll[j], dll) && !strcmp(s_dyn_name[j], name))
            return RT_SHIM_BASE + RT_SHIM_STRIDE * (rt_nimports + j);
    if (rt_nimports + s_ndyn >= 8192) rt_fatal(5, "too many dynamic shims");
    s_dyn = realloc(s_dyn, (s_ndyn + 1) * sizeof *s_dyn);
    s_dyn_dll = realloc(s_dyn_dll, (s_ndyn + 1) * sizeof *s_dyn_dll);
    s_dyn_name = realloc(s_dyn_name, (s_ndyn + 1) * sizeof *s_dyn_name);
    if (!s_dyn || !s_dyn_dll || !s_dyn_name) rt_fatal(5, "out of host memory");
    s_dyn[s_ndyn] = s;
    s_dyn_dll[s_ndyn] = strdup(dll);
    s_dyn_name[s_ndyn] = strdup(name);
    return RT_SHIM_BASE + RT_SHIM_STRIDE * (rt_nimports + s_ndyn++);
}

volatile uint64_t rt_bridge_t0;
const char *volatile rt_bridge_name;

static void shim_dispatch(void)
{
    uint32_t i = s_pending_shim;
    const char *dll, *name;
    const rt_shim *s = rt_shim_at(i, &dll, &name);
    s_shim_calls++;
    if (__atomic_load_n(&g_xbox_guest_cpu_preempt, __ATOMIC_RELAXED)) xbox_GuestCpuPreempt(0);
    uint32_t caller = MEM32(g_esp);
    rt_trace_first(i, caller);
    if (!s || !s->fn) {
        int32_t pop = s ? (int32_t)(4 * s->pop) : i < rt_nimports ? rt_imports[i]->pop : -1;
        char conv[32] = "convention unknown, pop ?";
        if (pop >= 0) snprintf(conv, sizeof conv, "stdcall, pop %d", pop);
        uint32_t w[8];
        for (int k = 0; k < 8; k++) w[k] = MEM32(g_esp + 4u + 4u * k);
        rt_stop(10, caller, "stub %s!%s (%s) called from guest 0x%08X args 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X 0x%08X",
                dll, name, conv, caller, w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7]);
    }
    uint32_t esp = g_esp;
    static const char *filter;
    static int filter_read;
    if (!filter_read) {
        filter = getenv("RT_TRACE_CALLS");
        filter_read = 1;
    }
    static const char *stop_at;
    if (filter_read == 1) {
        stop_at = getenv("RT_STOP_AT");
        filter_read = 2;
    }
    if (stop_at && !strcmp(name, stop_at)) {
        rt_seh_dump();
        rt_stop(5, caller, "RT_STOP_AT %s!%s reached", dll, name);
    }
    if (s->kind == RT_SHIM_BRIDGE) {
        __atomic_store_n(&rt_bridge_name, name, __ATOMIC_RELAXED);
        __atomic_store_n(&rt_bridge_t0, rt_now_ns(), __ATOMIC_RELEASE);
    }
    g_eax = s->fn((const uint32_t *)rt_host(esp + 4u));
    if (s->kind == RT_SHIM_BRIDGE) {
        uint64_t ms = (rt_now_ns() - __atomic_load_n(&rt_bridge_t0, __ATOMIC_RELAXED)) / 1000000ull;
        __atomic_store_n(&rt_bridge_t0, 0, __ATOMIC_RELEASE);
        if (ms >= 1000) rt_log("gfx slow bridge call %s %llu ms", name, (unsigned long long)ms);
    }
    if (filter && strstr(name, filter))
        rt_log("call %s!%s(0x%X, 0x%X, 0x%X, 0x%X, 0x%X, 0x%X, 0x%X, 0x%X) = 0x%X from 0x%08X", dll, name, MEM32(esp + 4),
               MEM32(esp + 8), MEM32(esp + 12), MEM32(esp + 16), MEM32(esp + 20), MEM32(esp + 24), MEM32(esp + 28),
               MEM32(esp + 32), g_eax, MEM32(esp));
    g_esp = esp + 4u + 4u * s->pop;
}

recomp_func_t recomp_lookup_kernel(uint32_t va)
{
    if (va < RT_SHIM_BASE || (va - RT_SHIM_BASE) % RT_SHIM_STRIDE) return NULL;
    uint32_t i = (va - RT_SHIM_BASE) / RT_SHIM_STRIDE;
    if (i >= rt_nimports + s_ndyn) return NULL;
    s_pending_shim = i;
    return shim_dispatch;
}

void rt_intrinsic_ftol(void)
{
    double v = g_fp_stack[g_fp_top];
    g_fp_top = (g_fp_top + 1) & 7;
    int64_t r = recomp_fist(v, (uint16_t)(g_fp_control_word | 0x0C00u), 64);
    g_eax = (uint32_t)r;
    g_edx = (uint32_t)((uint64_t)r >> 32);
    g_esp += 4;
}

void rt_intrinsic_chkstk(void)
{
    uint32_t ret_esp = g_esp + 4u;
    uint32_t ret = MEM32(g_esp);
    g_esp = ret_esp - g_eax;
    g_eax = ret;
}

typedef struct {
    const rt_module *m;
    uint32_t loc, lower;
} stricmp_site;

static stricmp_site s_stricmp[4];
static int s_nstricmp = -1;

static const stricmp_site *stricmp_site_for(uint32_t ret)
{
    if (s_nstricmp < 0) {
        s_nstricmp = 0;
        for (uint32_t k = 0; k < rt_nmodules; k++)
            for (uint32_t i = 0; i < rt_modules[k]->nintrinsics && s_nstricmp < 4; i++) {
                const rt_module *m = rt_modules[k];
                if (!m->intrinsics[i].name || strcmp(m->intrinsics[i].name, "stricmp")) continue;
                uint32_t va = m->intrinsics[i].va, loc, rel;
                const unsigned char *p = m->image + (va - m->base);
                if (p[0xC] != 0x8D || p[0xD] != 0x05 || p[0x6C] != 0xE8 || p[0x76] != 0xE8) continue;
                memcpy(&loc, p + 0xE, 4);
                memcpy(&rel, p + 0x6D, 4);
                s_stricmp[s_nstricmp++] = (stricmp_site){m, loc, va + 0x71u + rel};
            }
    }
    if (s_nstricmp < 2) return s_nstricmp ? &s_stricmp[0] : NULL;
    const rt_module *m = rt_owner(ret);
    for (int i = 0; i < s_nstricmp; i++)
        if (s_stricmp[i].m == m) return &s_stricmp[i];
    return &s_stricmp[0];
}

void rt_intrinsic_stricmp(void)
{
    const stricmp_site *s = stricmp_site_for(MEM32(g_esp));
    uint32_t a = MEM32(g_esp + 4), b = MEM32(g_esp + 8);
    int32_t r = 0;
    for (;; a++, b++) {
        uint32_t x = MEM8(a), y = MEM8(b);
        if (x == y) {
            if (!x) break;
            continue;
        }
        if (s && MEM32(s->loc + 8)) {
            x = rt_guest_call(s->lower, 1, &x) & 0xFFu;
            y = rt_guest_call(s->lower, 1, &y) & 0xFFu;
        } else {
            x += x - 0x41u < 0x1Au ? 0x20u : 0u;
            y += y - 0x41u < 0x1Au ? 0x20u : 0u;
        }
        if (x != y) {
            r = x < y ? -1 : 1;
            break;
        }
    }
    g_eax = (uint32_t)r;
    g_esp += 4;
}

static int32_t stricmp_ref(const char *a, const char *b)
{
    for (;; a++, b++) {
        unsigned x = (unsigned char)*a, y = (unsigned char)*b;
        if (x >= 'A' && x <= 'Z') x += 32;
        if (y >= 'A' && y <= 'Z') y += 32;
        if (x != y) return x < y ? -1 : 1;
        if (!x) return 0;
    }
}

static int32_t stricmp_run(const char *a, const char *b, int *sp_ok)
{
    uint32_t sp = g_esp, ax = g_eax, st = rt_heap_alloc(64, 1), sa = rt_heap_alloc(64, 1), sb = rt_heap_alloc(64, 1);
    strcpy(rt_host(sa), a);
    strcpy(rt_host(sb), b);
    g_esp = st + 32;
    MEM32(g_esp) = s_nstricmp > 0 ? s_stricmp[0].m->base + 0x1000u : 0;
    MEM32(g_esp + 4) = sa;
    MEM32(g_esp + 8) = sb;
    rt_intrinsic_stricmp();
    *sp_ok &= g_esp == st + 36;
    int32_t r = (int32_t)g_eax;
    g_esp = sp;
    g_eax = ax;
    rt_heap_free(sb);
    rt_heap_free(sa);
    rt_heap_free(st);
    return r;
}

void rt_intrinsic_selftest(void)
{
    const stricmp_site *s = stricmp_site_for(0);
    rt_up_check(s && !strcmp(s->m->name, "engine") && s->loc == 0x2138EEA4u && s->lower == 0x201650B2u,
                "engine _stricmp intrinsic reads its locale cell and tolower call from the pinned bytes", "loc 0x%X tolower 0x%X",
                s ? s->loc : 0, s ? s->lower : 0);
    static const struct { const char *a, *b; int32_t r; } cases[] = {
        {"Models/Foo.mdl", "models/foo.MDL", 0}, {"abc", "abd", -1}, {"b", "A", 1}, {"[", "a", -1}, {"[", "A", -1},
        {"", "", 0}, {"a", "", 1}, {"", "a", -1}, {"\xC4", "\xE4", -1}, {"_", "A", -1}, {"Z", "[", 1}, {"Bip01 L Hip", "bip01 l hip", 0},
    };
    int sp_ok = 1, bad = -1;
    for (int i = 0; i < (int)(sizeof cases / sizeof cases[0]) && bad < 0; i++)
        if (stricmp_run(cases[i].a, cases[i].b, &sp_ok) != cases[i].r) bad = i;
    rt_up_check(bad < 0 && sp_ok, "engine _stricmp intrinsic returns -1, 0 or 1 like the CRT and pops only its return address", "case %d", bad);
    uint32_t seed = 12345;
    int diff = 0;
    for (int n = 0; n < 3000 && !diff; n++) {
        char a[12], b[12];
        int la = 0, lb = 0;
        seed = seed * 1103515245u + 12345u;
        la = (int)(seed >> 16) % 11;
        seed = seed * 1103515245u + 12345u;
        lb = (int)(seed >> 16) % 11;
        for (int k = 0; k < 11; k++) {
            static const char set[] = "aAbB[_zZ\xC4\xE4/";
            seed = seed * 1103515245u + 12345u;
            a[k] = set[(seed >> 16) % 11];
            seed = seed * 1103515245u + 12345u;
            b[k] = set[(seed >> 16) % 11];
        }
        a[la] = b[lb] = 0;
        diff = stricmp_run(a, b, &sp_ok) != stricmp_ref(a, b);
    }
    rt_up_check(!diff && sp_ok, "engine _stricmp intrinsic matches an ASCII-fold reference on 3000 random pairs", "");
    uint32_t sva = 0;
    for (uint32_t k = 0; k < rt_nmodules && !sva; k++)
        for (uint32_t i = 0; i < rt_modules[k]->nintrinsics; i++)
            if (!strcmp(rt_modules[k]->intrinsics[i].name, "stricmp")) sva = rt_modules[k]->intrinsics[i].va;
    const rt_module *em = s ? s->m : NULL;
    uint32_t cva = em ? em->entry : 0;
    recomp_func_t c1 = cva ? recomp_lookup_manual(cva) : NULL, c2 = cva ? recomp_lookup_manual(cva) : NULL;
    rt_up_check(sva && recomp_lookup_manual(sva) == rt_intrinsic_stricmp && recomp_lookup_manual(sva) == rt_intrinsic_stricmp && c1 &&
                    c1 == c2 && c1 == recomp_lookup(cva),
                "indirect-call cache returns the intrinsic and the module's own function, the same on a second lookup", "entry 0x%X", cva);
}

static recomp_func_t intrinsic_of(uint32_t va)
{
    if (va >= RT_GUEST_TOP || !s_page_mod[va >> 16]) return NULL;
    const rt_module *m = rt_modules[s_page_mod[va >> 16] - 1];
    for (uint32_t i = 0; i < m->nintrinsics; i++)
        if (m->intrinsics[i].va == va) switch (m->intrinsics[i].name[0]) {
            case 'f': return rt_intrinsic_ftol;
            case 'c': return rt_intrinsic_chkstk;
            case 's': return rt_intrinsic_stricmp;
        }
    return NULL;
}

#define LK_N 16384u
static struct {
    uint32_t va;
    recomp_func_t fn;
} s_lk[LK_N];

recomp_func_t recomp_lookup_manual(uint32_t va)
{
    if (s_icall_trace < 0) s_icall_trace = getenv("RT_TRACE_ICALLS") != NULL;
    if (s_icall_trace) return intrinsic_of(va);
    uint32_t h = ((va >> 2) ^ (va >> 16)) & (LK_N - 1);
    if (s_lk[h].va == va && s_lk[h].fn) return s_lk[h].fn;
    recomp_func_t fn = intrinsic_of(va);
    if (!fn) fn = recomp_lookup(va);
    if (fn) {
        s_lk[h].va = va;
        s_lk[h].fn = fn;
    }
    return fn;
}

static RECOMP_TLS struct { uint32_t va; jmp_buf buf; } s_jmp[64];

jmp_buf *recomp_setjmp_slot(uint32_t buf_va)
{
    for (int i = 0; i < 64; i++)
        if (s_jmp[i].va == buf_va || s_jmp[i].va == 0) {
            s_jmp[i].va = buf_va;
            MEM32(buf_va + 0) = g_ebp;
            MEM32(buf_va + 4) = g_ebx;
            MEM32(buf_va + 8) = g_edi;
            MEM32(buf_va + 12) = g_esi;
            MEM32(buf_va + 16) = g_esp;
            g_seh_ebp = g_ebp;
            return &s_jmp[i].buf;
        }
    rt_fatal(5, "too many setjmp buffers");
}

int recomp_guest_longjmp(uint32_t buf_va, uint32_t value)
{
    for (int i = 0; i < 64; i++)
        if (s_jmp[i].va == buf_va) {
            g_ebp = g_seh_ebp = MEM32(buf_va + 0);
            g_ebx = MEM32(buf_va + 4);
            g_edi = MEM32(buf_va + 8);
            g_esi = MEM32(buf_va + 12);
            g_esp = MEM32(buf_va + 16);
            longjmp(s_jmp[i].buf, value ? (int)value : 1);
        }
    return 0;
}

void rt_set_extra_path(const char *p)
{
    snprintf(s_extra_path, sizeof s_extra_path, "%s", p);
}

void recomp_icall_fail_log(uint32_t va)
{
    s_misses++;
    if (s_extra_path[0]) {
        FILE *f = fopen(s_extra_path, "a");
        if (f) {
            fprintf(f, "0x%08X\n", va);
            fclose(f);
        }
    }
    rt_fatal(3, "indirect call to unknown guest address 0x%08X", va);
}

void recomp_icall_not_code_log(uint32_t va) { recomp_icall_fail_log(va); }

void recomp_unimpl(const char *text, uint32_t va)
{
    rt_fatal(8, "untranslated instruction '%s' reached at guest address 0x%08X", text, va);
}

uint32_t xbox_PortIn(uint32_t port, int width)
{
    (void)width;
    rt_fatal(8, "port input from 0x%X reached", port);
}

void xbox_PortOut(uint32_t port, uint32_t value, int width)
{
    (void)value;
    (void)width;
    rt_fatal(8, "port output to 0x%X reached", port);
}

uint64_t xbox_ReadTimeStampCounter(void) { return rt_now_ns() * (RT_TSC_HZ / 1000000000ull); }

void xbox_Cpuid(uint32_t leaf, uint32_t subleaf, uint32_t out[4])
{
    (void)subleaf;
    if (leaf == 0) {
        out[0] = 1;
        memcpy(&out[1], "Genu", 4);
        memcpy(&out[3], "ineI", 4);
        memcpy(&out[2], "ntel", 4);
    } else if (leaf == 1) {
        out[0] = 0x00000F29u;
        out[1] = 0;
        out[2] = 0;
        out[3] = 0x00000001u | 0x00000010u | 0x00008000u;
    } else {
        out[0] = out[1] = out[2] = out[3] = 0;
    }
}

void rt_set_reg(int r, uint32_t v)
{
    uint32_t *regs[] = {&g_eax, &g_ecx, &g_edx, &g_ebx, &g_esp, &g_ebp, &g_esi, &g_edi};
    *regs[r] = v;
}

uint32_t rt_get_reg(int r)
{
    uint32_t regs[] = {g_eax, g_ecx, g_edx, g_ebx, g_esp, g_ebp, g_esi, g_edi};
    return regs[r];
}

int rt_fp_depth(void) { return (8 - g_fp_top) & 7; }

double rt_fp_st(int i) { return g_fp_stack[(g_fp_top + i) & 7]; }

uint16_t rt_fp_cw(void) { return g_fp_control_word; }

void rt_call(uint32_t va)
{
    recomp_func_t fn = recomp_lookup_manual(va);
    if (!fn) fn = recomp_lookup(va);
    if (!fn) fn = recomp_lookup_kernel(va);
    if (!fn) recomp_icall_fail_log(va);
    fn();
}

void rt_counters(uint64_t out[4])
{
    out[0] = g_icall_count;
    out[1] = s_shim_calls;
    out[2] = s_misses;
    out[3] = 0;
}

uint32_t rt_shim_call(const char *dll, const char *name, const uint32_t *args)
{
    const rt_shim *s = rt_find_shim(dll, name);
    if (!s || !s->fn) rt_fatal(7, "unimplemented shim %s!%s", dll, name);
    return s->fn(args);
}

uint32_t rt_last_error(void) { return rt_get_last_error(); }

void rt_cpuid(uint32_t leaf, uint32_t out[4]) { xbox_Cpuid(leaf, 0, out); }

uint32_t rt_import_count(void) { return rt_nimports; }

const rt_import *rt_import_at(uint32_t i) { return rt_imports[i]; }

uint32_t rt_module_info(int k)
{
    const rt_module *m = rt_modules[0];
    uint32_t v[] = {m->base, m->size, m->entry, m->code_lo, m->code_hi};
    return v[k];
}

void rt_fninit(void)
{
    g_fp_control_word = 0x037F;
    g_fp_cc = 0;
    g_fp_top = 0;
}

static uint16_t fp_tags(void)
{
    uint16_t tw = 0xFFFF;
    for (int i = g_fp_top ? g_fp_top : 8; i < 8; i++) {
        double v = g_fp_stack[i];
        uint16_t t = v == 0.0 ? 1 : isfinite(v) ? 0 : 2;
        tw = (uint16_t)((tw & ~(3u << (2 * i))) | (uint16_t)(t << (2 * i)));
    }
    return tw;
}

static void env_store(uint32_t a)
{
    MEM32(a) = g_fp_control_word;
    MEM32(a + 4) = (uint16_t)(((g_fp_top & 7u) << 11) | g_fp_cc);
    MEM32(a + 8) = fp_tags();
    for (uint32_t k = 12; k < 28; k += 4) MEM32(a + k) = 0;
}

static void env_load(uint32_t a)
{
    uint16_t sw = MEM16(a + 4);
    g_fp_control_word = MEM16(a);
    g_fp_top = (sw >> 11) & 7;
    g_fp_cc = sw & (uint16_t)~0x3800u;
}

void rt_fnstenv(uint32_t a)
{
    env_store(a);
    g_fp_control_word |= 0x3F;
}

void rt_fldenv(uint32_t a) { env_load(a); }

void rt_fnsave(uint32_t a)
{
    env_store(a);
    for (int i = 0; i < 8; i++) {
        uint64_t m;
        uint16_t se = recomp_f80_store(g_fp_stack[(g_fp_top + i) & 7], &m);
        MEM32(a + 28 + 10 * i) = (uint32_t)m;
        MEM32(a + 32 + 10 * i) = (uint32_t)(m >> 32);
        MEM16(a + 36 + 10 * i) = se;
    }
    rt_fninit();
}

void rt_frstor(uint32_t a)
{
    env_load(a);
    for (int i = 0; i < 8; i++) {
        uint64_t m = MEM32(a + 28 + 10 * i) | (uint64_t)MEM32(a + 32 + 10 * i) << 32;
        g_fp_stack[(g_fp_top + i) & 7] = recomp_f80_load(m, MEM16(a + 36 + 10 * i));
    }
}

int rt_harness_begin(const char *game, const char *overlay)
{
    char path[1024];
    rt_trace_on(1);
    if (game && overlay && rt_vfs_init(game, overlay)) return -1;
    if (overlay) {
        snprintf(path, sizeof path, "%s/registry.reg", overlay);
        if (rt_reg_load(path)) return -2;
    }
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (rt_modules[m]->tls_end > rt_modules[m]->tls_start) MEM32(rt_modules[m]->tls_index) = 0;
    rt_reset_cpu();
    rt_thread_primary(RT_TIB);
    return 0;
}

uint32_t rt_harness_call(uint32_t va, int n, const uint32_t *args)
{
    g_esp = RT_STACK_TOP;
    return rt_guest_call(va, n, args);
}
