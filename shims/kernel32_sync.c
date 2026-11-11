#include "recomp_types.h"
#include "rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#define INFINITE 0xFFFFFFFFu

static uint64_t s_tls_used;

typedef struct {
    char name[260];
    rt_obj *obj;
} named;

static named s_named[256];
static int s_nnamed;

static uint32_t tid(void)
{
    rt_thread *t = rt_self();
    return t ? t->tid : 0x100;
}

static uint32_t CreateThread(const uint32_t *a)
{
    uint32_t id = 0, h = rt_thread_create(a[2], a[3], a[1], a[4], &id);
    if (h && a[5]) MEM32(a[5]) = id;
    return h;
}

static uint32_t ExitThread(const uint32_t *a) { rt_thread_exit(a[0]); }

static uint32_t GetCurrentThread(const uint32_t *a) { (void)a; return 0xFFFFFFFEu; }

static uint32_t GetCurrentThreadId(const uint32_t *a) { (void)a; return tid(); }

static uint32_t ResumeThread(const uint32_t *a) { return rt_thread_resume(a[0]); }

static uint32_t SetThreadPriority(const uint32_t *a)
{
    rt_log("SetThreadPriority 0x%08X %d", a[0], (int)a[1]);
    return 1;
}

static uint32_t TerminateThread(const uint32_t *a) { return rt_thread_terminate(a[0], a[1]); }

static uint32_t Sleep(const uint32_t *a)
{
    rt_wait(0, NULL, 0, a[0]);
    return 0;
}

static uint32_t TlsAlloc(const uint32_t *a)
{
    (void)a;
    for (uint32_t i = 0; i < RT_TLS_SLOTS; i++)
        if (!(s_tls_used >> i & 1)) {
            s_tls_used |= 1ull << i;
            rt_tls_zero_slot(i);
            return i;
        }
    rt_log("TlsAlloc: out of indexes");
    rt_set_last_error(18);
    return 0xFFFFFFFFu;
}

static uint32_t TlsFree(const uint32_t *a)
{
    if (a[0] >= RT_TLS_SLOTS || !(s_tls_used >> a[0] & 1)) {
        rt_set_last_error(87);
        return 0;
    }
    s_tls_used &= ~(1ull << a[0]);
    return 1;
}

static uint32_t TlsGetValue(const uint32_t *a)
{
    if (a[0] >= RT_TLS_SLOTS) {
        rt_set_last_error(87);
        return 0;
    }
    rt_set_last_error(0);
    return MEM32(g_fs_base + RT_TEB_TLS_SLOTS + 4u * a[0]);
}

static uint32_t TlsSetValue(const uint32_t *a)
{
    if (a[0] >= RT_TLS_SLOTS) {
        rt_set_last_error(87);
        return 0;
    }
    MEM32(g_fs_base + RT_TEB_TLS_SLOTS + 4u * a[0]) = a[1];
    return 1;
}

static uint32_t InitializeCriticalSection(const uint32_t *a)
{
    uint32_t cs = a[0];
    memset(rt_host(cs), 0, 24);
    MEM32(cs + 4) = 0xFFFFFFFFu;
    return 0;
}

static uint32_t DeleteCriticalSection(const uint32_t *a)
{
    memset(rt_host(a[0]), 0, 24);
    return 0;
}

static int s_cs_waiters;

static uint32_t EnterCriticalSection(const uint32_t *a)
{
    uint32_t cs = a[0], me = tid();
    uint32_t *own = rt_host(cs + 12);
    for (;;) {
        uint32_t owner = __atomic_load_n(own, __ATOMIC_SEQ_CST);
        if (owner == me) {
            MEM32(cs + 8)++;
            MEM32(cs + 4)++;
            return 0;
        }
        if (!owner) {
            __atomic_store_n(own, me, __ATOMIC_SEQ_CST);
            MEM32(cs + 8) = 1;
            MEM32(cs + 4)++;
            return 0;
        }
        rt_block_begin("EnterCriticalSection", INFINITE);
        rt_obj_lock();
        __atomic_fetch_add(&s_cs_waiters, 1, __ATOMIC_SEQ_CST);
        while (__atomic_load_n(own, __ATOMIC_SEQ_CST)) rt_obj_timedwait(2);
        __atomic_fetch_sub(&s_cs_waiters, 1, __ATOMIC_SEQ_CST);
        rt_obj_unlock();
        rt_block_end();
    }
}

static uint32_t LeaveCriticalSection(const uint32_t *a)
{
    uint32_t cs = a[0];
    uint32_t *own = rt_host(cs + 12);
    if (__atomic_load_n(own, __ATOMIC_SEQ_CST) != tid()) return 0;
    MEM32(cs + 4)--;
    if (--MEM32(cs + 8) == 0) {
        __atomic_store_n(own, 0, __ATOMIC_SEQ_CST);
        if (__atomic_load_n(&s_cs_waiters, __ATOMIC_SEQ_CST)) {
            rt_obj_lock();
            rt_obj_broadcast();
            rt_obj_unlock();
        }
    }
    return 0;
}

static uint32_t named_find(uint32_t name, int type)
{
    if (!name) return 0;
    for (int i = 0; i < s_nnamed; i++)
        if (!strcmp(s_named[i].name, rt_gstr(name)) && s_named[i].obj->type == type) {
            uint32_t h = rt_handle_new(s_named[i].obj);
            rt_set_last_error(183);
            return h;
        }
    return 0;
}

static uint32_t named_add(uint32_t name, uint32_t h)
{
    if (name && s_nnamed < 256) {
        snprintf(s_named[s_nnamed].name, sizeof s_named[0].name, "%s", rt_gstr(name));
        s_named[s_nnamed++].obj = rt_handle_get(h, 0);
    }
    rt_set_last_error(0);
    return h;
}

static uint32_t CreateEventA(const uint32_t *a)
{
    uint32_t h = named_find(a[3], RT_OBJ_EVENT);
    if (h) return h;
    rt_obj *o = rt_obj_new(RT_OBJ_EVENT);
    o->manual = a[1] != 0;
    o->signalled = a[2] != 0;
    return named_add(a[3], rt_handle_new(o));
}

static uint32_t set_event(uint32_t h, int v)
{
    rt_obj *o = rt_handle_get(h, RT_OBJ_EVENT);
    if (!o) return 0;
    rt_obj_lock();
    o->signalled = v;
    if (v) rt_obj_broadcast();
    rt_obj_unlock();
    return 1;
}

static uint32_t SetEvent(const uint32_t *a) { return set_event(a[0], 1); }

static uint32_t ResetEvent(const uint32_t *a) { return set_event(a[0], 0); }

static uint32_t CreateMutexA(const uint32_t *a)
{
    uint32_t h = named_find(a[2], RT_OBJ_MUTEX);
    if (h) {
        rt_log("CreateMutexA \"%s\" already exists", rt_gstr(a[2]));
        return h;
    }
    rt_obj *o = rt_obj_new(RT_OBJ_MUTEX);
    if (a[1]) {
        o->owner = tid();
        o->count = 1;
    }
    return named_add(a[2], rt_handle_new(o));
}

static uint32_t ReleaseMutex(const uint32_t *a)
{
    rt_obj *o = rt_handle_get(a[0], RT_OBJ_MUTEX);
    if (!o) return 0;
    rt_obj_lock();
    int ok = o->owner == tid();
    if (ok && --o->count == 0) {
        o->owner = 0;
        rt_obj_broadcast();
    }
    rt_obj_unlock();
    if (!ok) rt_set_last_error(288);
    return (uint32_t)ok;
}

static uint32_t CreateSemaphoreA(const uint32_t *a)
{
    uint32_t h = named_find(a[3], RT_OBJ_SEM);
    if (h) return h;
    if ((int32_t)a[2] <= 0 || (int32_t)a[1] < 0 || a[1] > a[2]) {
        rt_set_last_error(87);
        return 0;
    }
    rt_obj *o = rt_obj_new(RT_OBJ_SEM);
    o->count = a[1];
    o->max = a[2];
    return named_add(a[3], rt_handle_new(o));
}

static uint32_t ReleaseSemaphore(const uint32_t *a)
{
    rt_obj *o = rt_handle_get(a[0], RT_OBJ_SEM);
    if (!o) return 0;
    rt_obj_lock();
    uint32_t prev = o->count;
    int ok = (int32_t)a[1] > 0 && prev + a[1] <= o->max;
    if (ok) {
        o->count += a[1];
        rt_obj_broadcast();
    }
    rt_obj_unlock();
    if (!ok) {
        rt_set_last_error(298);
        return 0;
    }
    if (a[2]) MEM32(a[2]) = prev;
    return 1;
}

static uint32_t WaitForSingleObject(const uint32_t *a)
{
    uint32_t h = a[0];
    return rt_wait(1, &h, 0, a[1]);
}

static uint32_t WaitForMultipleObjects(const uint32_t *a)
{
    if (!a[0] || a[0] > 64) {
        rt_set_last_error(87);
        return 0xFFFFFFFFu;
    }
    if (!rt_span_ok(a[1], 4ull * a[0])) {
        rt_set_last_error(998);
        return 0xFFFFFFFFu;
    }
    return rt_wait(a[0], (const uint32_t *)rt_host(a[1]), a[2] != 0, a[3]);
}

static uint32_t CloseHandle(const uint32_t *a)
{
    if (a[0] >= 0x1Au && a[0] <= 0x1Cu) return 1;
    if (a[0] == 0xFFFFFFFEu || a[0] == 0xFFFFFFFFu) return 1;
    rt_obj *o = rt_handle_get(a[0], 0);
    if (o && o->refs == 1)
        for (int i = 0; i < s_nnamed; i++)
            if (s_named[i].obj == o) s_named[i--] = s_named[--s_nnamed];
    return (uint32_t)rt_handle_close(a[0]);
}

static uint32_t DuplicateHandle(const uint32_t *a)
{
    uint32_t h = rt_handle_dup(a[1]);
    if (!h) return 0;
    if (a[3]) MEM32(a[3]) = h;
    if (a[6] & 1u) rt_handle_close(a[1]);
    return 1;
}

static uint32_t InterlockedIncrement(const uint32_t *a)
{
    return __atomic_add_fetch((uint32_t *)rt_host(a[0]), 1u, __ATOMIC_SEQ_CST);
}

static uint32_t InterlockedDecrement(const uint32_t *a)
{
    return __atomic_sub_fetch((uint32_t *)rt_host(a[0]), 1u, __ATOMIC_SEQ_CST);
}

static uint32_t InterlockedExchange(const uint32_t *a)
{
    return __atomic_exchange_n((uint32_t *)rt_host(a[0]), a[1], __ATOMIC_SEQ_CST);
}

static uint32_t QueryPerformanceCounter(const uint32_t *a)
{
    uint64_t v = rt_now_ns() / 100u;
    MEM32(a[0]) = (uint32_t)v;
    MEM32(a[0] + 4) = (uint32_t)(v >> 32);
    return 1;
}

static uint32_t QueryPerformanceFrequency(const uint32_t *a)
{
    MEM32(a[0]) = 10000000u;
    MEM32(a[0] + 4) = 0;
    return 1;
}

static uint32_t GetTickCount(const uint32_t *a)
{
    (void)a;
    return (uint32_t)(rt_now_ns() / 1000000u);
}

static uint64_t ft_now(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return ((uint64_t)tv.tv_sec + 11644473600ull) * 10000000ull + (uint64_t)tv.tv_usec * 10u;
}

static void put64(uint32_t p, uint64_t v)
{
    MEM32(p) = (uint32_t)v;
    MEM32(p + 4) = (uint32_t)(v >> 32);
}

static uint64_t get64(uint32_t p) { return MEM32(p) | (uint64_t)MEM32(p + 4) << 32; }

static void put_systime(uint32_t p, const struct tm *tm, uint32_t ms)
{
    uint16_t v[8] = {(uint16_t)(tm->tm_year + 1900), (uint16_t)(tm->tm_mon + 1), (uint16_t)tm->tm_wday,
                     (uint16_t)tm->tm_mday, (uint16_t)tm->tm_hour, (uint16_t)tm->tm_min, (uint16_t)tm->tm_sec,
                     (uint16_t)ms};
    memcpy(rt_host(p), v, sizeof v);
}

static int64_t tz_bias_100ns(time_t t)
{
    struct tm lt;
    localtime_r(&t, &lt);
    return (int64_t)lt.tm_gmtoff * 10000000;
}

static uint32_t GetSystemTimeAsFileTime(const uint32_t *a)
{
    put64(a[0], ft_now());
    return 0;
}

static void ft_to_tm(uint64_t ft, struct tm *tm, uint32_t *ms)
{
    time_t t = (time_t)(ft / 10000000ull) - (time_t)11644473600ll;
    gmtime_r(&t, tm);
    *ms = (uint32_t)(ft / 10000u % 1000u);
}

static uint32_t GetSystemTime(const uint32_t *a)
{
    struct tm tm;
    uint32_t ms;
    ft_to_tm(ft_now(), &tm, &ms);
    put_systime(a[0], &tm, ms);
    return 0;
}

static uint32_t GetLocalTime(const uint32_t *a)
{
    struct tm tm;
    uint32_t ms;
    uint64_t ft = ft_now();
    ft_to_tm(ft + (uint64_t)tz_bias_100ns(time(NULL)), &tm, &ms);
    put_systime(a[0], &tm, ms);
    return 0;
}

static uint32_t FileTimeToLocalFileTime(const uint32_t *a)
{
    put64(a[1], get64(a[0]) + (uint64_t)tz_bias_100ns(time(NULL)));
    return 1;
}

static uint32_t LocalFileTimeToFileTime(const uint32_t *a)
{
    put64(a[1], get64(a[0]) - (uint64_t)tz_bias_100ns(time(NULL)));
    return 1;
}

static uint32_t FileTimeToSystemTime(const uint32_t *a)
{
    struct tm tm;
    uint32_t ms;
    ft_to_tm(get64(a[0]), &tm, &ms);
    put_systime(a[1], &tm, ms);
    return 1;
}

static uint32_t SystemTimeToFileTime(const uint32_t *a)
{
    const uint16_t *v = rt_host(a[0]);
    struct tm tm = {.tm_year = v[0] - 1900, .tm_mon = v[1] - 1, .tm_mday = v[3], .tm_hour = v[4], .tm_min = v[5],
                    .tm_sec = v[6]};
    time_t t = timegm(&tm);
    put64(a[1], ((uint64_t)t + 11644473600ull) * 10000000ull + (uint64_t)v[7] * 10000u);
    return 1;
}

static uint32_t FileTimeToDosDateTime(const uint32_t *a)
{
    struct tm tm;
    uint32_t ms;
    ft_to_tm(get64(a[0]), &tm, &ms);
    MEM16(a[1]) = (uint16_t)(((tm.tm_year - 80) << 9) | ((tm.tm_mon + 1) << 5) | tm.tm_mday);
    MEM16(a[2]) = (uint16_t)((tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec / 2));
    return 1;
}

static uint32_t GetTimeZoneInformation(const uint32_t *a)
{
    memset(rt_host(a[0]), 0, 172);
    MEM32(a[0]) = (uint32_t)(int32_t)(-tz_bias_100ns(time(NULL)) / 600000000);
    return 0;
}

const rt_shim rt_kernel32_sync_shims[] = {
    {"kernel32.dll", "CloseHandle", 1, CloseHandle},
    {"kernel32.dll", "CreateEventA", 4, CreateEventA},
    {"kernel32.dll", "CreateMutexA", 3, CreateMutexA},
    {"kernel32.dll", "CreateSemaphoreA", 4, CreateSemaphoreA},
    {"kernel32.dll", "CreateThread", 6, CreateThread},
    {"kernel32.dll", "DeleteCriticalSection", 1, DeleteCriticalSection},
    {"kernel32.dll", "DuplicateHandle", 7, DuplicateHandle},
    {"kernel32.dll", "EnterCriticalSection", 1, EnterCriticalSection},
    {"kernel32.dll", "ExitThread", 1, ExitThread},
    {"kernel32.dll", "FileTimeToDosDateTime", 3, FileTimeToDosDateTime},
    {"kernel32.dll", "FileTimeToLocalFileTime", 2, FileTimeToLocalFileTime},
    {"kernel32.dll", "FileTimeToSystemTime", 2, FileTimeToSystemTime},
    {"kernel32.dll", "GetCurrentThread", 0, GetCurrentThread},
    {"kernel32.dll", "GetCurrentThreadId", 0, GetCurrentThreadId},
    {"kernel32.dll", "GetLocalTime", 1, GetLocalTime},
    {"kernel32.dll", "GetSystemTime", 1, GetSystemTime},
    {"kernel32.dll", "GetSystemTimeAsFileTime", 1, GetSystemTimeAsFileTime},
    {"kernel32.dll", "GetTickCount", 0, GetTickCount},
    {"kernel32.dll", "GetTimeZoneInformation", 1, GetTimeZoneInformation},
    {"kernel32.dll", "InitializeCriticalSection", 1, InitializeCriticalSection},
    {"kernel32.dll", "InterlockedDecrement", 1, InterlockedDecrement},
    {"kernel32.dll", "InterlockedExchange", 2, InterlockedExchange},
    {"kernel32.dll", "InterlockedIncrement", 1, InterlockedIncrement},
    {"kernel32.dll", "LeaveCriticalSection", 1, LeaveCriticalSection},
    {"kernel32.dll", "LocalFileTimeToFileTime", 2, LocalFileTimeToFileTime},
    {"kernel32.dll", "QueryPerformanceCounter", 1, QueryPerformanceCounter},
    {"kernel32.dll", "QueryPerformanceFrequency", 1, QueryPerformanceFrequency},
    {"kernel32.dll", "ReleaseMutex", 1, ReleaseMutex},
    {"kernel32.dll", "ReleaseSemaphore", 3, ReleaseSemaphore},
    {"kernel32.dll", "ResetEvent", 1, ResetEvent},
    {"kernel32.dll", "ResumeThread", 1, ResumeThread},
    {"kernel32.dll", "SetEvent", 1, SetEvent},
    {"kernel32.dll", "SetThreadPriority", 2, SetThreadPriority},
    {"kernel32.dll", "Sleep", 1, Sleep},
    {"kernel32.dll", "SystemTimeToFileTime", 2, SystemTimeToFileTime},
    {"kernel32.dll", "TerminateThread", 2, TerminateThread},
    {"kernel32.dll", "TlsAlloc", 0, TlsAlloc},
    {"kernel32.dll", "TlsFree", 1, TlsFree},
    {"kernel32.dll", "TlsGetValue", 1, TlsGetValue},
    {"kernel32.dll", "TlsSetValue", 2, TlsSetValue},
    {"kernel32.dll", "WaitForMultipleObjects", 4, WaitForMultipleObjects},
    {"kernel32.dll", "WaitForSingleObject", 2, WaitForSingleObject},
};

const uint32_t rt_nkernel32_sync_shims = sizeof rt_kernel32_sync_shims / sizeof rt_kernel32_sync_shims[0];
