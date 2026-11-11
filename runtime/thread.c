#include "recomp_types.h"
#include "rt.h"

#include <dirent.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define INFINITE 0xFFFFFFFFu
#define MAX_HANDLES 16384
#define HOST_STACK (64u << 20)

int32_t g_xbox_guest_cpu_preempt;

static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t s_cv = PTHREAD_COND_INITIALIZER;
static int s_lock_on, s_free = 1, s_granted;
static unsigned s_next_ticket, s_head;
static _Atomic unsigned s_waiting;
static rt_thread *_Atomic s_owner;
static rt_thread *s_cpu_owner;
static uint64_t s_cpu_swaps;
static uint64_t s_quantum_ns = 2000000u;

static pthread_mutex_t s_omu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t s_ocv = PTHREAD_COND_INITIALIZER;
static rt_obj *s_tab[MAX_HANDLES];

static pthread_mutex_t s_tmu = PTHREAD_MUTEX_INITIALIZER;
static rt_thread *s_threads, *s_primary;
static uint32_t s_next_tid = 0x100;

static RECOMP_TLS int t_held, t_guest;
static RECOMP_TLS rt_thread *t_self;
static RECOMP_TLS uint64_t t_turn_ns;
static RECOMP_TLS uint32_t t_last_edge;

static _Atomic uint64_t s_t8, s_t_close, s_t_event;
static int s_until;
static double s_close_after, s_menu_exit = 5;

uint64_t rt_now_ns(void) { return clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW); }

rt_thread *rt_self(void) { return t_self; }

int rt_is_primary(void) { return t_self && t_self->primary; }

uint32_t rt_last_back_edge(void) { return t_last_edge; }

static uint32_t teb_of_current(void) { return g_fs_base ? g_fs_base : RT_TIB; }

uint32_t rt_get_last_error(void) { return MEM32(teb_of_current() + 0x34); }

void rt_set_last_error(uint32_t e) { MEM32(teb_of_current() + 0x34) = e; }

static void preempt_set(int32_t v) { __atomic_store_n(&g_xbox_guest_cpu_preempt, v, __ATOMIC_RELAXED); }

static void release(int handoff);

static RecompCpu *cpu_of(rt_thread *t) { return (RecompCpu *)(t + 1); }

static void cpu_take(void)
{
    if (s_cpu_owner == t_self) return;
    if (s_cpu_owner) *cpu_of(s_cpu_owner) = g_cpu;
    g_cpu = *cpu_of(t_self);
    s_cpu_owner = t_self;
    s_cpu_swaps++;
}

uint64_t rt_cpu_swaps(void) { return s_cpu_swaps; }

static uint64_t s_lock_wait_ns, s_sleep_ns;

uint64_t rt_lock_wait_ns(void) { return s_lock_wait_ns; }

uint64_t rt_sleep_ns(void) { return s_sleep_ns; }

static void acquire(int barge)
{
    pthread_mutex_lock(&s_mu);
    if (s_free && !s_granted && (barge || !s_waiting)) {
        s_free = 0;
    } else {
        unsigned my = s_next_ticket++;
        uint64_t t0 = rt_now_ns();
        s_waiting++;
        while (!(s_free && my == s_head)) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_nsec += 2000000;
            if (ts.tv_nsec >= 1000000000) {
                ts.tv_sec++;
                ts.tv_nsec -= 1000000000;
            }
            pthread_cond_timedwait(&s_cv, &s_mu, &ts);
            if (!(s_free && my == s_head) && (barge || rt_now_ns() - t0 >= s_quantum_ns)) preempt_set(1);
        }
        s_free = 0;
        s_granted = 0;
        s_head++;
        s_waiting--;
        if (t_self && t_self->primary) s_lock_wait_ns += rt_now_ns() - t0;
    }
    s_owner = t_self;
    pthread_mutex_unlock(&s_mu);
    t_held = 1;
    t_turn_ns = rt_now_ns();
    if (t_self && t_self->killed) {
        release(1);
        t_guest = 0;
        pthread_exit(NULL);
    }
    cpu_take();
}

static void release(int handoff)
{
    t_held = 0;
    pthread_mutex_lock(&s_mu);
    s_free = 1;
    s_owner = NULL;
    if (s_waiting) {
        if (handoff) {
            s_granted = 1;
            preempt_set(0);
        }
        pthread_cond_broadcast(&s_cv);
    }
    pthread_mutex_unlock(&s_mu);
}

void rt_lock_take(void)
{
    if (s_lock_on && t_guest && !t_held) acquire(1);
}

void rt_block_begin(const char *kind, uint32_t ms)
{
    if (t_self) {
        __atomic_store_n(&t_self->wait_kind, kind, __ATOMIC_RELAXED);
        __atomic_store_n(&t_self->wait_ms, ms, __ATOMIC_RELAXED);
        __atomic_store_n(&t_self->waiting, 1, __ATOMIC_RELEASE);
    }
    if (s_lock_on && t_held) release(0);
}

void rt_block_end(void)
{
    if (t_self) __atomic_store_n(&t_self->waiting, 0, __ATOMIC_RELEASE);
    if (s_lock_on && t_guest && !t_held) acquire(1);
}

void rt_yield(void)
{
    if (!s_lock_on || !t_held) return;
    if (t_self && t_self->primary && rt_pump_due()) rt_pump(0);
    if (s_waiting) {
        release(1);
        acquire(0);
    }
}

void xbox_GuestCpuPreempt(uint32_t va)
{
    if (!s_lock_on || !t_guest || !t_held) {
        if (!s_lock_on) preempt_set(0);
        return;
    }
    if (va) t_last_edge = va;
    if (t_self && t_self->primary && rt_pump_due()) rt_pump(0);
    if (s_waiting) {
        if (rt_now_ns() - t_turn_ns >= s_quantum_ns) {
            release(1);
            acquire(0);
        }
    } else {
        preempt_set(0);
    }
}

static rt_thread *thread_new(void)
{
    rt_thread *t = calloc(1, sizeof *t + sizeof(RecompCpu));
    if (!t) rt_fatal(5, "out of host memory");
    pthread_mutex_lock(&s_tmu);
    t->tid = s_next_tid;
    s_next_tid += 4;
    t->next = s_threads;
    __atomic_store_n(&s_threads, t, __ATOMIC_RELEASE);
    pthread_mutex_unlock(&s_tmu);
    return t;
}

uint32_t rt_teb_new(uint32_t teb, uint32_t tid, uint32_t lo, uint32_t hi)
{
    memset(rt_host(teb), 0, RT_TEB_SIZE);
    MEM32(teb + 0x00) = 0xFFFFFFFFu;
    MEM32(teb + 0x04) = hi;
    MEM32(teb + 0x08) = lo;
    MEM32(teb + 0x18) = teb;
    MEM32(teb + 0x20) = 0x10;
    MEM32(teb + 0x24) = tid;
    MEM32(teb + 0x2C) = teb + RT_TEB_TLS_ARRAY;
    for (uint32_t m = 0; m < rt_nmodules; m++) {
        const rt_module *mod = rt_modules[m];
        if (mod->tls_end <= mod->tls_start) continue;
        uint32_t n = mod->tls_end - mod->tls_start, idx = MEM32(mod->tls_index);
        uint32_t blk = rt_heap_alloc(n + mod->tls_zero_fill + 16u, 1);
        if (!blk) rt_fatal(5, "no memory for the static TLS block of %s", mod->name);
        memcpy(rt_host(blk), rt_host(mod->tls_start), n);
        if (idx < 64) MEM32(teb + RT_TEB_TLS_ARRAY + 4u * idx) = blk;
    }
    return teb;
}

void rt_tls_zero_slot(uint32_t slot)
{
    pthread_mutex_lock(&s_tmu);
    for (rt_thread *t = s_threads; t; t = t->next)
        if (!t->done) MEM32(t->teb + RT_TEB_TLS_SLOTS + 4u * slot) = 0;
    pthread_mutex_unlock(&s_tmu);
}

void rt_thread_primary(uint32_t teb)
{
    rt_thread *t = thread_new();
    t->primary = 1;
    t->teb = teb;
    t->stack_lo = RT_STACK_LO;
    t->stack_hi = RT_STACK_TOP;
    rt_teb_new(teb, t->tid, RT_STACK_LO, RT_STACK_TOP + 0x100u);
    rt_obj *o = rt_obj_new(RT_OBJ_THREAD);
    o->thread = t;
    t->handle = rt_handle_new(o);
    s_primary = t;
    t_self = t;
    t_guest = 1;
    const char *q = getenv("RT_GUEST_QUANTUM_MS");
    if (q && *q) s_quantum_ns = (uint64_t)strtoul(q, NULL, 0) * 1000000u;
    s_lock_on = 1;
    s_cpu_owner = t;
    acquire(0);
}

static void *thread_main(void *arg)
{
    rt_thread *t = arg;
    t_self = t;
    t_guest = 1;
    rt_altstack();
    pthread_mutex_lock(&s_omu);
    while (t->suspended) pthread_cond_wait(&s_ocv, &s_omu);
    pthread_mutex_unlock(&s_omu);
    acquire(0);
    rt_reset_cpu();
    g_esp = t->stack_hi - 16u;
    g_fs_base = t->teb;
    rt_modules_thread_attach();
    uint32_t p = t->param;
    uint32_t r = rt_guest_call(t->start, 1, &p);
    rt_thread_exit(r);
}

uint32_t rt_thread_create(uint32_t start, uint32_t param, uint32_t stack, uint32_t flags, uint32_t *tid)
{
    uint32_t size = stack ? (stack + 0xFFFFu) & ~0xFFFFu : 0x100000u;
    uint32_t lo = rt_pages_reserve(size), teb = rt_heap_alloc(RT_TEB_SIZE, 1);
    if (!lo || !teb) {
        rt_set_last_error(8);
        return 0;
    }
    rt_thread *t = thread_new();
    t->stack_lo = lo;
    t->stack_hi = lo + size;
    t->start = start;
    t->param = param;
    t->suspended = (flags & 4u) != 0;
    t->teb = rt_teb_new(teb, t->tid, lo, lo + size);
    rt_obj *o = rt_obj_new(RT_OBJ_THREAD);
    o->thread = t;
    t->handle = rt_handle_new(o);
    o->refs++;
    char w[512];
    rt_log("thread 0x%X created by 0x%X start %s param 0x%08X stack %u", t->tid, t_self ? t_self->tid : 0,
           rt_where(start, w, sizeof w), param, size);
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, HOST_STACK);
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
    pthread_t pt;
    if (pthread_create(&pt, &at, thread_main, t)) rt_fatal(5, "pthread_create failed");
    pthread_attr_destroy(&at);
    if (tid) *tid = t->tid;
    return t->handle;
}

uint32_t rt_thread_terminate(uint32_t h, uint32_t code)
{
    rt_obj *o = rt_handle_get(h, RT_OBJ_THREAD);
    if (!o) return 0;
    rt_thread *t = o->thread;
    if (t == t_self) rt_thread_exit(code);
    if (t->primary) rt_stop(5, 0, "TerminateThread on the primary thread");
    pthread_mutex_lock(&s_omu);
    if (!t->done) {
        t->exit_code = code;
        __atomic_store_n(&t->done, 1, __ATOMIC_RELEASE);
        t->killed = 1;
        t->suspended = 0;
        for (int i = 0; i < MAX_HANDLES; i++)
            if (s_tab[i] && s_tab[i]->type == RT_OBJ_MUTEX && s_tab[i]->owner == t->tid) {
                s_tab[i]->owner = 0;
                s_tab[i]->count = 0;
                s_tab[i]->abandoned = 1;
            }
    }
    pthread_cond_broadcast(&s_ocv);
    pthread_mutex_unlock(&s_omu);
    rt_log("thread 0x%X terminated with %u by 0x%X", t->tid, code, t_self ? t_self->tid : 0);
    return 1;
}

uint32_t rt_thread_resume(uint32_t h)
{
    rt_obj *o = rt_handle_get(h, RT_OBJ_THREAD);
    if (!o) return 0xFFFFFFFFu;
    pthread_mutex_lock(&s_omu);
    uint32_t prev = (uint32_t)o->thread->suspended;
    o->thread->suspended = 0;
    pthread_cond_broadcast(&s_ocv);
    pthread_mutex_unlock(&s_omu);
    return prev;
}

void rt_thread_exit(uint32_t code)
{
    rt_thread *t = t_self;
    if (!t || t->primary) {
        rt_log("guest primary thread exited with %u", code);
        rt_exit((int)code);
    }
    pthread_mutex_lock(&s_omu);
    t->exit_code = code;
    __atomic_store_n(&t->done, 1, __ATOMIC_RELEASE);
    for (int i = 0; i < MAX_HANDLES; i++)
        if (s_tab[i] && s_tab[i]->type == RT_OBJ_MUTEX && s_tab[i]->owner == t->tid) {
            s_tab[i]->owner = 0;
            s_tab[i]->count = 0;
            s_tab[i]->abandoned = 1;
        }
    pthread_cond_broadcast(&s_ocv);
    pthread_mutex_unlock(&s_omu);
    rt_log("thread 0x%X exited with %u", t->tid, code);
    if (t_held) release(1);
    t_guest = 0;
    pthread_exit(NULL);
}

uint32_t rt_guest_call(uint32_t va, int n, const uint32_t *args)
{
    if (s_lock_on && !t_held) rt_stop(5, va, "guest call to 0x%08X without the guest lock", va);
    uint32_t sv[7] = {g_ebx, g_ecx, g_edx, g_esi, g_edi, g_ebp, g_esp};
    int df = g_df;
    uint32_t esp = g_esp;
    for (int k = n - 1; k >= 0; k--) {
        g_esp -= 4;
        MEM32(g_esp) = args[k];
    }
    g_esp -= 4;
    MEM32(g_esp) = RT_CALLBACK_RET;
    rt_call(va);
    if (g_esp != esp && g_esp != esp - 4u * (uint32_t)n)
        rt_stop(5, va, "callback 0x%08X left esp off by %d", va, (int)(g_esp - esp));
    uint32_t r = g_eax;
    g_ebx = sv[0];
    g_ecx = sv[1];
    g_edx = sv[2];
    g_esi = sv[3];
    g_edi = sv[4];
    g_ebp = sv[5];
    g_esp = sv[6];
    g_df = df;
    return r;
}

void rt_obj_lock(void) { pthread_mutex_lock(&s_omu); }

void rt_obj_unlock(void) { pthread_mutex_unlock(&s_omu); }

void rt_obj_broadcast(void) { pthread_cond_broadcast(&s_ocv); }

void rt_obj_timedwait(uint32_t ms)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    uint64_t ns = (uint64_t)ts.tv_nsec + (uint64_t)ms * 1000000u;
    ts.tv_sec += (time_t)(ns / 1000000000u);
    ts.tv_nsec = (long)(ns % 1000000000u);
    pthread_cond_timedwait(&s_ocv, &s_omu, &ts);
}

rt_obj *rt_obj_new(int type)
{
    rt_obj *o = calloc(1, sizeof *o);
    if (!o) rt_fatal(5, "out of host memory");
    o->type = type;
    o->fd = -1;
    return o;
}

uint32_t rt_handle_new(rt_obj *o)
{
    pthread_mutex_lock(&s_omu);
    for (int i = 1; i < MAX_HANDLES; i++)
        if (!s_tab[i]) {
            s_tab[i] = o;
            o->refs++;
            pthread_mutex_unlock(&s_omu);
            return 0x100u + 4u * (uint32_t)i;
        }
    pthread_mutex_unlock(&s_omu);
    rt_fatal(5, "handle table full");
}

static int slot_of(uint32_t h)
{
    if (h < 0x104u || (h & 3u)) return -1;
    uint32_t i = (h - 0x100u) / 4u;
    return i < MAX_HANDLES ? (int)i : -1;
}

rt_obj *rt_handle_get(uint32_t h, int type)
{
    if (h == 0xFFFFFFFEu && t_self && type == RT_OBJ_THREAD) h = t_self->handle;
    int i = slot_of(h);
    rt_obj *o = i >= 0 ? s_tab[i] : NULL;
    if (!o || (type && o->type != type)) {
        rt_set_last_error(6);
        return NULL;
    }
    return o;
}

int rt_handle_close(uint32_t h)
{
    int i = slot_of(h);
    pthread_mutex_lock(&s_omu);
    rt_obj *o = i >= 0 ? s_tab[i] : NULL;
    if (!o) {
        pthread_mutex_unlock(&s_omu);
        rt_set_last_error(6);
        return 0;
    }
    s_tab[i] = NULL;
    int last = --o->refs == 0;
    pthread_mutex_unlock(&s_omu);
    if (last && o->fd >= 0) close(o->fd);
    if (last && o->type != RT_OBJ_THREAD) {
        free(o->path);
        if (o->names)
            for (uint32_t k = 0; k < o->n; k++) free(o->names[k]);
        free(o->names);
        free(o->dirs);
        free(o);
    }
    return 1;
}

uint32_t rt_handle_dup(uint32_t h)
{
    if (h == 0xFFFFFFFEu && t_self) h = t_self->handle;
    rt_obj *o = rt_handle_get(h, 0);
    return o ? rt_handle_new(o) : 0;
}

static int ready(rt_obj *o, rt_thread *me)
{
    switch (o->type) {
    case RT_OBJ_EVENT: return o->signalled;
    case RT_OBJ_MUTEX: return !o->owner || o->owner == me->tid;
    case RT_OBJ_SEM: return o->count > 0;
    case RT_OBJ_THREAD: return o->thread->done;
    default: return 1;
    }
}

static uint32_t consume(rt_obj *o, rt_thread *me)
{
    uint32_t r = 0;
    if (o->type == RT_OBJ_EVENT && !o->manual) o->signalled = 0;
    if (o->type == RT_OBJ_MUTEX) {
        o->owner = me->tid;
        o->count++;
        if (o->abandoned) {
            o->abandoned = 0;
            r = 0x80;
        }
    }
    if (o->type == RT_OBJ_SEM) o->count--;
    return r;
}

static int check(uint32_t n, rt_obj **os, int all, rt_thread *me, uint32_t *r)
{
    if (all) {
        for (uint32_t i = 0; i < n; i++)
            if (!ready(os[i], me)) return 0;
        uint32_t ab = 0;
        for (uint32_t i = 0; i < n; i++) ab |= consume(os[i], me);
        *r = ab ? 0x80 : 0;
        return 1;
    }
    for (uint32_t i = 0; i < n; i++)
        if (ready(os[i], me)) {
            *r = consume(os[i], me) + i;
            return 1;
        }
    return 0;
}

uint32_t rt_wait(uint32_t n, const uint32_t *handles, int all, uint32_t ms)
{
    rt_obj *os[64];
    rt_thread *me = t_self;
    static rt_thread host;
    if (!me) me = &host;
    if (n > 64) {
        rt_set_last_error(87);
        return 0xFFFFFFFFu;
    }
    for (uint32_t i = 0; i < n; i++) {
        os[i] = rt_handle_get(handles[i], 0);
        if (!os[i]) return 0xFFFFFFFFu;
    }
    uint32_t r;
    pthread_mutex_lock(&s_omu);
    int ok = n && check(n, os, all, me, &r);
    pthread_mutex_unlock(&s_omu);
    if (ok) return r;
    if (ms == 0) {
        if (!n) rt_yield();
        return n ? 0x102u : 0;
    }
    me->wait_n = n < 4 ? n : 4;
    for (uint32_t i = 0; i < me->wait_n; i++) me->wait_handles[i] = handles[i];
    rt_block_begin(n ? "wait" : "sleep", ms);
    uint64_t t_wait0 = rt_now_ns(), t_first = t_wait0;
    uint64_t end = ms == INFINITE ? UINT64_MAX : rt_now_ns() + (uint64_t)ms * 1000000u;
    r = 0x102u;
    for (;;) {
        pthread_mutex_lock(&s_omu);
        if (n && check(n, os, all, me, &r)) {
            pthread_mutex_unlock(&s_omu);
            break;
        }
        uint64_t now = rt_now_ns();
        if (now >= end) {
            pthread_mutex_unlock(&s_omu);
            r = n ? 0x102u : 0;
            break;
        }
        uint64_t slice = (me->primary ? 16000000u : 2000000u);
        if (end - now < slice) slice = end - now;
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        uint64_t ns = (uint64_t)ts.tv_nsec + slice;
        ts.tv_sec += (time_t)(ns / 1000000000u);
        ts.tv_nsec = (long)(ns % 1000000000u);
        pthread_cond_timedwait(&s_ocv, &s_omu, &ts);
        pthread_mutex_unlock(&s_omu);
        if (me->killed) {
            t_guest = 0;
            pthread_exit(NULL);
        }
        if (me->primary) {
            uint64_t t = rt_now_ns();
            s_sleep_ns += t - t_wait0;
            t_wait0 = t;
            rt_pump(0);
        }
    }
    if (me->primary) s_sleep_ns += rt_now_ns() - t_wait0;
    uint64_t waited = rt_now_ns() - t_first, asked = ms == 0xFFFFFFFFu ? 0 : (uint64_t)ms * 1000000ull;
    static unsigned s_slow;
    if (me->primary && waited > asked + 10000000ull && s_slow < 200 && ++s_slow)
        rt_log("slow %s: %u handles asked %u ms took %llu ms", n ? "wait" : "sleep", n, ms, (unsigned long long)(waited / 1000000ull));
    rt_block_end();
    me->wait_n = 0;
    return r;
}

void rt_dump_waits(void)
{
    char w[512];
    for (rt_thread *t = __atomic_load_n(&s_threads, __ATOMIC_ACQUIRE); t; t = t->next) {
        if (__atomic_load_n(&t->done, __ATOMIC_ACQUIRE)) continue;
        const char *kind = __atomic_load_n(&t->wait_kind, __ATOMIC_RELAXED);
        rt_log("  thread 0x%X%s: %s %s, %u handles [0x%X 0x%X 0x%X 0x%X], last shim caller %s", t->tid,
               t->primary ? " (primary)" : "", __atomic_load_n(&t->waiting, __ATOMIC_ACQUIRE) ? "waiting" : "running", kind ? kind : "-",
               t->wait_n, t->wait_handles[0], t->wait_handles[1], t->wait_handles[2], t->wait_handles[3],
               rt_where(t->last_caller, w, sizeof w));
    }
}

void rt_stage8_reached(void) { s_t8 = rt_now_ns(); }

void rt_note_event(void) { s_t_event = rt_now_ns(); }

void rt_close_requested(void)
{
    uint64_t none = 0;
    atomic_compare_exchange_strong(&s_t_close, &none, rt_now_ns());
}

int rt_close_take(void)
{
    static int taken;
    if (!s_t_close || taken) return 0;
    taken = 1;
    return 1;
}


static int all_blocked(void)
{
    int live = 0, blocked = 1;
    pthread_mutex_lock(&s_tmu);
    for (rt_thread *t = s_threads; t && blocked; t = t->next) {
        if (__atomic_load_n(&t->done, __ATOMIC_ACQUIRE)) continue;
        live++;
        if (!__atomic_load_n(&t->waiting, __ATOMIC_ACQUIRE) || __atomic_load_n(&t->wait_ms, __ATOMIC_RELAXED) != INFINITE) blocked = 0;
    }
    pthread_mutex_unlock(&s_tmu);
    return blocked && live > 0;
}

#define HANG_DEV_MS 5000ull
#define HANG_PLAYER_MS 120000ull

int rt_hang_due(uint64_t elapsed_ms, int player)
{
    return elapsed_ms >= (player ? HANG_PLAYER_MS : HANG_DEV_MS);
}

static double load1(void)
{
    double l[1] = {0};
    return getloadavg(l, 1) == 1 ? l[0] : -1;
}

static void dxvk_log_scan(time_t started)
{
    static char path[1024];
    static off_t off;
    if (!path[0]) {
        const char *dir = getenv("DXVK_LOG_PATH");
        DIR *d = dir ? opendir(dir) : NULL;
        if (!d) return;
        struct dirent *e;
        while ((e = readdir(d))) {
            size_t n = strlen(e->d_name);
            if (n > 9 && !strcmp(e->d_name + n - 9, "_d3d9.log")) {
                snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
                break;
            }
        }
        closedir(d);
        if (!path[0]) return;
    }
    struct stat st;
    if (stat(path, &st) || st.st_mtime < started) return;
    if (st.st_size < off) off = 0;
    int fd = open(path, O_RDONLY);
    if (fd < 0) return;
    char buf[65536];
    ssize_t n;
    do {
        n = pread(fd, buf, sizeof buf - 1, off);
        if (n <= 0) break;
        size_t end = (size_t)n;
        while (end && buf[end - 1] != '\n') end--;
        if (!end) {
            if ((size_t)n < sizeof buf - 1) break;
            end = (size_t)n;
        }
        buf[end] = 0;
        off += (off_t)end;
        for (char *l = buf, *next; l < buf + end; l = next) {
            next = strchr(l, '\n');
            if (next) *next++ = 0; else next = buf + end;
            if (strstr(l, "GPU likely hung") || strstr(l, "VK_ERROR_DEVICE_LOST")) {
                close(fd);
                if (strlen(l) > 200) l[200] = 0;
                rt_stop(15, 0, "gfx hang: dxvk: %s (load %.2f)", l, load1());
            }
        }
    } while ((size_t)n == sizeof buf - 1);
    close(fd);
}

static int since(uint64_t now, uint64_t t, uint64_t ns) { return t && now >= t && now - t >= ns; }

static void *watchdog(void *arg)
{
    (void)arg;
    uint64_t blocked_since = 0, last_scan = 0;
    time_t started = time(NULL);
    for (;;) {
        usleep(2000);
        uint64_t now = rt_now_ns();
        uint64_t b0 = __atomic_load_n(&rt_bridge_t0, __ATOMIC_ACQUIRE);
        const char *bn = __atomic_load_n(&rt_bridge_name, __ATOMIC_ACQUIRE);
        if (b0 && now > b0 && rt_hang_due((now - b0) / 1000000ull, rt_player()) && __atomic_load_n(&rt_bridge_t0, __ATOMIC_ACQUIRE) == b0)
            rt_stop(15, 0, "gfx hang: %s blocked %llu ms (load %.2f)", bn, (unsigned long long)((now - b0) / 1000000ull), load1());
        if (now - last_scan >= 500000000ull) {
            last_scan = now;
            dxvk_log_scan(started);
        }
        rt_thread *owner = s_owner;
        if (owner && owner == s_primary && rt_pump_due()) preempt_set(1);
        if (s_until == 1 && since(now, s_t8, 2000000000ull)) {
            rt_log("until console: exit 0");
            rt_exit(0);
        }
        uint64_t t11 = rt_stage11_ns();
        if (s_until == 2 && since(now, t11, (uint64_t)(s_menu_exit * 1e9))) {
            rt_log("until menu: exit 0");
            rt_exit(0);
        }
        uint64_t t_base = rt_player() ? rt_stage6_ns() : s_t8;
        uint64_t none = 0;
        if (s_close_after > 0 && !s_t_close && since(now, t_base, (uint64_t)(s_close_after * 1e9)) &&
            atomic_compare_exchange_strong(&s_t_close, &none, now)) {
            rt_log("close-after: requesting window close");
            rt_window_push_close();
        }
        if (since(now, s_t_close, 10000000000ull)) {
            rt_log("forced quit after window close");
            rt_exit(0);
        }
        if (all_blocked() && (!s_t_event || since(now, s_t_event, 10000000000ull))) {
            if (!blocked_since) blocked_since = now;
            if (now - blocked_since >= 10000000000ull) rt_stop(14, 0, "every guest thread is blocked with no timeout");
        } else {
            blocked_since = 0;
        }
    }
    return NULL;
}

void rt_watchdog_start(int until, double close_after_s, double capture_after_s)
{
    s_until = until;
    s_close_after = close_after_s;
    if (capture_after_s + 2 > s_menu_exit) s_menu_exit = capture_after_s + 2;
    s_t_event = rt_now_ns();
    pthread_t pt;
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
    pthread_create(&pt, &at, watchdog, NULL);
    pthread_attr_destroy(&at);
}
