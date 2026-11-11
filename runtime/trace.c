#include "recomp_types.h"
#include "rt.h"
#include "map.h"
#include "toggles.h"

#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_ROWS 8192
#define LINE 4096

static int s_log = -1, s_tsv = -1, s_on, s_stage;
static const char *s_stage_name = "none";
static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static uint32_t s_rows[MAX_ROWS], s_row_caller[MAX_ROWS], s_nrows;
static uint64_t s_count[MAX_ROWS];
static uint8_t s_seen[MAX_ROWS];
static char s_marker[256] = "M3_CONSOLE_READY";
static char s_buf[4][LINE];
static uint32_t s_buf_n[4];
static uint64_t s_max_gap_ns;
static pthread_mutex_t s_gap_mu = PTHREAD_MUTEX_INITIALIZER;
static RECOMP_TLS int t_gap_held;
static char s_last_text[512];

static void out2(const char *s, size_t n)
{
    write(2, s, n);
    if (s_log >= 0) write(s_log, s, n);
}

static void vline(const char *prefix, const char *fmt, va_list ap)
{
    char b[LINE + 64];
    int n = snprintf(b, sizeof b, "%s", prefix);
    int m = vsnprintf(b + n, sizeof b - (size_t)n - 1, fmt, ap);
    if (m < 0) m = 0;
    n += m < (int)(sizeof b - (size_t)n - 2) ? m : (int)(sizeof b - (size_t)n - 2);
    b[n++] = '\n';
    out2(b, (size_t)n);
}

void rt_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    pthread_mutex_lock(&s_mu);
    vline("rt: ", fmt, ap);
    pthread_mutex_unlock(&s_mu);
    va_end(ap);
}

void rt_trace_on(int on) { s_on = on; }

int rt_tracing(void) { return s_on; }

int rt_stage_reached(void) { return s_stage; }

void rt_set_marker(const char *m) { snprintf(s_marker, sizeof s_marker, "%s", m); }

static uint32_t s_max_gap_where;
static int s_max_gap_stage;
static _Atomic uint64_t s_t10;
static uint64_t s_steady_gap_ns;
static char s_steady_where[64] = "guest", s_steady_detail[256] = "";
static double s_steady_at;

static _Atomic uint64_t s_t12;

void rt_note_pump_gap(uint64_t ns)
{
    uint64_t start = rt_now_ns() - ns, base = s_t12 ? s_t12 : s_t10;
    pthread_mutex_lock(&s_gap_mu);
    t_gap_held = 1;
    int steady = base && start >= base + 5000000000ull && ns > s_steady_gap_ns;
    rt_gfx_gap(ns, steady, s_steady_where, sizeof s_steady_where, s_steady_detail, sizeof s_steady_detail);
    if (steady) {
        s_steady_gap_ns = ns;
        s_steady_at = (double)(start - base) / 1e9;
    }
    if (ns > s_max_gap_ns) {
        s_max_gap_ns = ns;
        s_max_gap_where = rt_last_back_edge();
        s_max_gap_stage = s_stage;
    }
    t_gap_held = 0;
    pthread_mutex_unlock(&s_gap_mu);
}

uint64_t rt_max_pump_gap_ms(void)
{
    pthread_mutex_lock(&s_gap_mu);
    uint64_t ms = s_max_gap_ns / 1000000u;
    pthread_mutex_unlock(&s_gap_mu);
    return ms;
}

static const char *const k_stage_names[] = {"none", "host", "mapped", "process", "dllmain", "launcher-main",
                                            "window", "engine-init", "console", "device", "first-present", "menu",
                                            "map"};

static _Atomic uint64_t s_t6, s_t8, s_t11;
static int s_quiet, s_player;

void rt_set_player(int on) { s_player = on; }

int rt_player(void) { return s_player; }

uint64_t rt_stage6_ns(void) { return s_t6; }

uint64_t rt_stage11_ns(void) { return s_t11; }

void rt_set_guest_quiet(int quiet) { s_quiet = quiet; }

uint64_t rt_frames_start_ns(void) { return s_t12 ? s_t12 : s_t11 ? s_t11 : s_quiet ? s_t8 : s_player ? s_t10 : 0; }

static const char *const k_default_args[] = {"-window", "-w",     "1280",   "-h",    "1024",        "-dxlevel",
                                             "90",      "-nojoy",   "-noip",  "-nolan", "-dev",
                                             "-condebug", "+mm_verbose", "1", "+echo", "M3_CONSOLE_READY"};

const char *const *rt_default_args(int quiet, int *n)
{
    static const char *built[2][sizeof k_default_args / sizeof k_default_args[0] + 1];
    const char **built_args = built[quiet ? 1 : 0];
    int nd = (int)(sizeof k_default_args / sizeof k_default_args[0]);
    int m = 0, lw, lh;
    static char dim[2][16];
    rt_aspect_launch_size(&lw, &lh);
    snprintf(dim[0], sizeof dim[0], "%d", lw);
    snprintf(dim[1], sizeof dim[1], "%d", lh);
    for (int i = 0; i < nd; i++) {
        if (s_player && !strcmp(k_default_args[i], "-dev")) continue;
        if ((quiet || s_player) && !strcmp(k_default_args[i], "-condebug")) continue;
        if ((quiet || s_player) && !strcmp(k_default_args[i], "+mm_verbose")) {
            i++;
            continue;
        }
        int after_w = i > 0 && !strcmp(k_default_args[i - 1], "-w"), after_h = i > 0 && !strcmp(k_default_args[i - 1], "-h");
        built_args[m++] = after_w ? dim[0] : after_h ? dim[1] : k_default_args[i];
        if (rt_audio_nosound() && !strcmp(k_default_args[i], "90")) built_args[m++] = "-nosound";
    }
    *n = m;
    return built_args;
}

void rt_stage(int n, const char *fmt, ...)
{
    char b[LINE];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    if (n > s_stage && n != 9 && n != 10) {
        s_stage = n;
        s_stage_name = k_stage_names[n];
    }
    rt_log("stage %d %s%s%s", n, k_stage_names[n], b[0] ? " " : "", b);
    if (n == 10) s_t10 = rt_now_ns();
    if (n == 6 && !s_t6) s_t6 = rt_now_ns();
    if (n == 8) {
        s_t8 = rt_now_ns();
        rt_stage8_reached();
    }
    if (n == 11) {
        s_t11 = rt_now_ns();
        rt_gfx_menu();
    }
    if (n == 12) {
        pthread_mutex_lock(&s_gap_mu);
        s_steady_gap_ns = 0;
        s_steady_at = 0;
        snprintf(s_steady_where, sizeof s_steady_where, "guest");
        s_steady_detail[0] = 0;
        pthread_mutex_unlock(&s_gap_mu);
        s_t12 = rt_now_ns();
    }
}

static char s_map_name[64], s_ready_name[64];
static int s_map_armed;
static uint32_t s_map_count, s_ready_n;
static uint64_t s_map_last, s_map_start;

static void map_check(const char *s, uint32_t len)
{
    if (len == 8 && !memcmp(s, "R_NewMap", 8)) {
        s_map_armed = 1;
        s_map_count = 0;
        return;
    }
    if (len < 6 || len > 5 + 63 || memcmp(s, "Map: ", 5)) return;
    for (uint32_t i = 5; i < len; i++)
        if (!(s[i] == '_' || (s[i] >= '0' && s[i] <= '9') || (s[i] >= 'A' && s[i] <= 'Z') || (s[i] >= 'a' && s[i] <= 'z')))
            return;
    snprintf(s_map_name, sizeof s_map_name, "%.*s", (int)(len - 5), s + 5);
    s_map_armed = 0;
    s_map_count = 0;
}

void rt_map_present(uint64_t now_ns)
{
    uint64_t prev = s_map_last;
    s_map_last = now_ns;
    if (!s_map_armed) return;
    s_map_count = s_map_count && now_ns - prev > 250000000ull ? 1 : s_map_count + 1;
    if (s_map_count == 1) s_map_start = now_ns;
    if (s_map_count < 30 || now_ns - s_map_start < 1000000000ull) return;
    char name[64];
    snprintf(name, sizeof name, "%s", s_map_name[0] ? s_map_name : "?");
    s_map_name[0] = 0;
    s_map_armed = 0;
    s_map_count = 0;
    rt_log("map ready %s at +%.1f s", name, s_t8 ? ((double)now_ns - (double)s_t8) / 1e9 : 0.0);
    pthread_mutex_lock(&s_mu);
    snprintf(s_ready_name, sizeof s_ready_name, "%s", name);
    uint32_t n = __atomic_add_fetch(&s_ready_n, 1, __ATOMIC_ACQ_REL);
    pthread_mutex_unlock(&s_mu);
    if (n == 1) rt_stage(12, "%s", name);
}

uint32_t rt_map_ready(char *name, size_t n)
{
    pthread_mutex_lock(&s_mu);
    uint32_t r = __atomic_load_n(&s_ready_n, __ATOMIC_ACQUIRE);
    if (name && n) snprintf(name, n, "%s", s_ready_name);
    pthread_mutex_unlock(&s_mu);
    return r;
}

static void menu_check(const char *s, uint32_t len)
{
    static const char k[] = "main menu rendering particles on frame ";
    if (s_stage < 8 || s_stage >= 11 || len <= sizeof k - 1 || memcmp(s, k, sizeof k - 1)) return;
    uint32_t n = 0, i = sizeof k - 1;
    for (; i < len && s[i] >= '0' && s[i] <= '9'; i++) n = n * 10 + (uint32_t)(s[i] - '0');
    if (i > sizeof k - 1 && n >= 30) rt_stage(11, "frame %u", n);
}

int rt_trace_open(const char *logdir)
{
    char path[1024], stamp[32];
    time_t t = time(NULL);
    struct tm tm;
    gmtime_r(&t, &tm);
    strftime(stamp, sizeof stamp, "%Y%m%dT%H%M%SZ", &tm);
    snprintf(path, sizeof path, "%s/run-%s.log", logdir, stamp);
    s_log = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    snprintf(path, sizeof path, "%s/first-calls-%s.tsv", logdir, stamp);
    s_tsv = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    return s_log >= 0 && s_tsv >= 0 ? 0 : -1;
}

const rt_module *rt_owner(uint32_t va)
{
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (va - rt_modules[m]->base < rt_modules[m]->size) return rt_modules[m];
    return NULL;
}

const char *rt_where(uint32_t va, char *buf, size_t n)
{
    const rt_module *m = rt_owner(va);
    if (!m) {
        if (va >= RT_SHIM_BASE) snprintf(buf, n, "shim+0x%X (?)", va - RT_SHIM_BASE);
        else snprintf(buf, n, "?+0x%08X (?)", va);
        return buf;
    }
    const rt_fname *f = NULL;
    uint32_t lo = 0, hi = m->nfnames;
    while (lo < hi) {
        uint32_t mid = (lo + hi) / 2;
        if (m->fnames[mid].va <= va) lo = mid + 1;
        else hi = mid;
    }
    if (lo) f = &m->fnames[lo - 1];
    if (f && f->va) {
        if (f->name) snprintf(buf, n, "%s+0x%X (%s)", m->name, va - m->base, f->name);
        else snprintf(buf, n, "%s+0x%X (sub_%08X)", m->name, va - m->base, f->va);
    } else {
        snprintf(buf, n, "%s+0x%X (?)", m->name, va - m->base);
    }
    return buf;
}

static void row_name(uint32_t i, const char **dll, const char **name, uint32_t *kind)
{
    const rt_shim *s = rt_shim_at(i, dll, name);
    *kind = !s || !s->fn ? 2 : s->kind;
}

static const char *const k_kind[] = {"shim", "fail", "stub", "bridge"};

void rt_trace_first(uint32_t i, uint32_t caller)
{
    if (i >= MAX_ROWS) return;
    __atomic_add_fetch(&s_count[i], 1, __ATOMIC_RELAXED);
    rt_thread *t = rt_self();
    if (t) t->last_caller = caller;
    if (!s_on || __atomic_exchange_n(&s_seen[i], 1, __ATOMIC_ACQ_REL)) return;
    pthread_mutex_lock(&s_mu);
    uint32_t r = s_nrows++;
    s_rows[r] = i;
    s_row_caller[r] = caller;
    pthread_mutex_unlock(&s_mu);
    const char *dll, *name;
    uint32_t kind;
    char w[512];
    row_name(i, &dll, &name, &kind);
    rt_log("first %s!%s %s from %s", dll, name, k_kind[kind], rt_where(caller, w, sizeof w));
}

static void flush_line(int src, const char *line, uint32_t n)
{
    char b[LINE + 16];
    int len = snprintf(b, sizeof b, "guest: %.*s\n", (int)n, line);
    if (len > (int)sizeof b - 1) len = (int)sizeof b - 1;
    snprintf(s_last_text, sizeof s_last_text, "%.*s", (int)n, line);
    (void)src;
    pthread_mutex_lock(&s_mu);
    out2(b, (size_t)len);
    pthread_mutex_unlock(&s_mu);
}

static uint32_t engine_writer(void)
{
    const rt_module *e = NULL;
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (!strcmp(rt_modules[m]->name, "engine")) e = rt_modules[m];
    if (!e) return 0;
    for (uint32_t a = g_esp; a < g_esp + 0x10000u && a < RT_GUEST_TOP - 4; a += 4) {
        uint32_t v = MEM32(a);
        if (v < e->code_lo || v >= e->code_hi || v < 6) continue;
        if (MEM8(v - 5) == 0xE8 || (MEM8(v - 6) == 0xFF && MEM8(v - 5) == 0x15) ||
            (MEM8(v - 2) == 0xFF && (MEM8(v - 1) & 0x38) == 0x10 && (MEM8(v - 1) & 0xC0) == 0xC0) ||
            (MEM8(v - 3) == 0xFF && (MEM8(v - 2) & 0x38) == 0x10 && (MEM8(v - 2) & 0xC0) == 0x40) ||
            (MEM8(v - 6) == 0xFF && (MEM8(v - 5) & 0x38) == 0x10 && (MEM8(v - 5) & 0xC0) == 0x80) ||
            (MEM8(v - 2) == 0xFF && (MEM8(v - 1) & 0x38) == 0x10 && (MEM8(v - 1) & 0xC0) == 0x00))
            return v;
    }
    return 0;
}

void rt_guest_text(int src, const char *text, uint32_t n)
{
    if (src < 0 || src > 3) return;
    uint32_t writer = s_stage < 7 && s_stage >= 4 ? engine_writer() : 0;
    for (uint32_t k = 0; k < n; k++) {
        char c = text[k];
        if (c == '\n' || s_buf_n[src] == LINE - 1) {
            uint32_t len = s_buf_n[src];
            if (len && s_buf[src][len - 1] == '\r') len--;
            if (writer && s_stage < 7) {
                char w[512];
                rt_stage(7, "writer %s", rt_where(writer, w, sizeof w));
            }
            flush_line(src, s_buf[src], len);
            uint32_t t = len;
            while (t && (s_buf[src][t - 1] == ' ' || s_buf[src][t - 1] == '\t')) t--;
            if (s_stage == 7 && t == strlen(s_marker) && !memcmp(s_buf[src], s_marker, t)) rt_stage(8, "%s", "");
            menu_check(s_buf[src], t);
            map_check(s_buf[src], t);
            s_buf_n[src] = 0;
            if (c == '\n') continue;
        }
        s_buf[src][s_buf_n[src]++] = c;
    }
}

static void write_tsv(void)
{
    if (s_tsv < 0) return;
    char b[1024];
    int n = snprintf(b, sizeof b, "order\tdll!name\tkind\tcaller_module\tcaller_function\tcalls\n");
    write(s_tsv, b, (size_t)n);
    for (uint32_t r = 0; r < s_nrows; r++) {
        const char *dll, *name;
        uint32_t kind;
        row_name(s_rows[r], &dll, &name, &kind);
        const rt_module *m = rt_owner(s_row_caller[r]);
        char w[512], fn[512] = "?";
        rt_where(s_row_caller[r], w, sizeof w);
        char *o = strchr(w, '('), *c = strrchr(w, ')');
        if (o && c > o) snprintf(fn, sizeof fn, "%.*s", (int)(c - o - 1), o + 1);
        n = snprintf(b, sizeof b, "%u\t%s!%s\t%s\t%s\t%s\t%llu\n", r + 1, dll, name, k_kind[kind], m ? m->name : "?", fn,
                     (unsigned long long)s_count[s_rows[r]]);
        write(s_tsv, b, (size_t)(n < (int)sizeof b ? n : (int)sizeof b - 1));
    }
    close(s_tsv);
    s_tsv = -1;
}

void rt_exit(int code)
{
    static int once;
    if (__atomic_exchange_n(&once, 1, __ATOMIC_ACQ_REL)) {
        for (;;) pause();
    }
    write_tsv();
    rt_audio_report();
    rt_mp3_report();
    if (s_on) {
        char w[512];
        int locked = !t_gap_held && pthread_mutex_lock(&s_gap_mu) == 0;
        rt_log("max pump gap %llu ms", (unsigned long long)(s_max_gap_ns / 1000000u));
        rt_log("max pump gap at stage %d, last back edge %s", s_max_gap_stage, rt_where(s_max_gap_where, w, sizeof w));
        if (rt_gfx_mode() == RT_GFX_DXVK) {
            rt_log("max pump gap steady %llu ms in %s at +%.1f s", (unsigned long long)(s_steady_gap_ns / 1000000u),
                   s_steady_where, s_steady_at);
            rt_log("gfx steady gap detail %s", s_steady_detail);
        }
        if (locked) pthread_mutex_unlock(&s_gap_mu);
        rt_log("allocator high-water 0x%08X bytes, %u failed allocations", rt_heap_high_water(), rt_heap_failures());
        rt_gfx_report();
        rt_input_report();
        rt_log("exit %d", code);
    }
    if (s_log >= 0) close(s_log);
    _exit(code);
}

static const char *const k_reason[] = {
    [3] = "dispatch miss", [4] = "guest memory fault", [5] = "runtime", [6] = "host fault",
    [7] = "unimplemented shim", [8] = "untranslated", [9] = "SIMD trap", [10] = "stub",
    [11] = "guest exit", [12] = "unhandled guest exception", [13] = "DllMain failed", [14] = "deadlock",
    [15] = "graphics"};

void rt_stop(int code, uint32_t gva, const char *fmt, ...)
{
    char msg[LINE], w[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    if (!s_on) {
        char b[LINE + 8];
        int n = snprintf(b, sizeof b, "rt: %s\n", msg);
        write(2, b, (size_t)(n < (int)sizeof b ? n : (int)sizeof b - 1));
        _exit(code);
    }
    const char *why = code >= 3 && code <= 15 && k_reason[code] ? k_reason[code] : "stop";
    rt_log("FAULT %s (exit %d): %s", why, code, msg);
    rt_log("  guest address 0x%08X = %s", gva, rt_where(gva, w, sizeof w));
    rt_thread *t = rt_self();
    rt_log("  thread 0x%X (%s), last stage %d %s", t ? t->tid : 0, t && t->primary ? "primary" : t ? "worker" : "host",
           s_stage, s_stage_name);
    uint32_t lc = t ? t->last_caller : 0;
    rt_log("  last shim caller 0x%08X = %s; last back edge 0x%08X", lc, rt_where(lc, w, sizeof w), rt_last_back_edge());
    char rows[LINE] = "";
    size_t o = 0;
    for (uint32_t r = s_nrows > 8 ? s_nrows - 8 : 0; r < s_nrows && o < sizeof rows - 128; r++) {
        const char *dll, *name;
        uint32_t kind;
        row_name(s_rows[r], &dll, &name, &kind);
        o += (size_t)snprintf(rows + o, sizeof rows - o, "%s%s!%s", o ? ", " : "", dll, name);
    }
    rt_log("  last 8 first-calls: %s", rows);
    if (s_last_text[0]) rt_log("  last guest text: %s", s_last_text);
    if (code == 12) rt_seh_dump();
    if (code == 14) rt_dump_waits();
    rt_exit(code);
}

void rt_fatal(int code, const char *fmt, ...)
{
    char msg[LINE];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    rt_stop(code, 0, "%s", msg);
}
