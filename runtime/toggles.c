#include "toggles.h"

#include "fx.h"
#include "pad.h"
#include "rt.h"
#include "winstate.h"

#include <SDL3/SDL.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define MAXSTEPS 5
#define CUSTOM 4
#define DEF_CUSTOM "4:3"
#define RING 4096
#define WINDOW_NS 2000000000ull

typedef struct {
    const char *name, *title;
    int n;
    const char *val[MAXSTEPS], *lab[MAXSTEPS];
    int def, def_player;
} tog;

static const tog k_tog[RT_TOG_COUNT] = {
    {"overlays", "Overlays", 3, {"all", "fps", "off"}, {"all", "FPS only", "off"}, RT_OVL_ALL, RT_OVL_OFF},
    {"picture", "Picture mode", 3, {"metalfx", "native", "off"}, {"MetalFX", "native", "off"}, 2, 2},
    {"aspect", "Aspect", 4, {"16:9", "fit", "21:9", "engine"}, {"16:9", "fit", "21:9", "engine"}, CUSTOM, CUSTOM},
};

static const struct {
    int id, digit;
} k_menu[] = {{RT_TOG_OVERLAYS, 1}, {RT_TOG_PICTURE, 2}};

static const char *const k_dropped[] = {"interp", "smoothing", "hdr", "controller", "aniso", "mouse"};

static _Atomic int s_val[RT_TOG_COUNT];
static _Atomic int s_pending[RT_TOG_COUNT];
static _Atomic uint32_t s_mask[RT_TOG_COUNT];
static const char *s_reason[RT_TOG_COUNT];
static int s_held[RT_TOG_COUNT];
static char s_custom[32];
static char s_dir[1024];
static int s_player;
static int s_save_warned;
static _Atomic unsigned s_warns, s_logged;
static _Atomic int s_last = -1;
static int s_ready;
static int s_launch_w, s_launch_h, s_opt_picked;
static double s_launch_scale = 1.0;

static void init_once(void)
{
    if (s_ready) return;
    s_ready = 1;
    rt_toggles_defaults(0);
}

void rt_toggles_defaults(int player)
{
    s_ready = 1;
    s_player = player;
    for (int i = 0; i < RT_TOG_COUNT; i++) {
        atomic_store(&s_val[i], player ? k_tog[i].def_player : k_tog[i].def);
        atomic_store(&s_pending[i], 0);
        atomic_store(&s_mask[i], 0);
        s_reason[i] = "Not built yet";
        s_held[i] = 0;
    }
    atomic_store(&s_mask[RT_TOG_OVERLAYS], 7u);
    s_reason[RT_TOG_OVERLAYS] = "";
    atomic_store(&s_mask[RT_TOG_ASPECT], 7u);
    s_reason[RT_TOG_ASPECT] = "";
    s_launch_w = s_launch_h = 0;
    s_opt_picked = 0;
    s_launch_scale = 1.0;
    snprintf(s_custom, sizeof s_custom, "%s", DEF_CUSTOM);
}

static const char *val_name(int id, int v) { return v == CUSTOM ? s_custom : k_tog[id].val[v]; }

int rt_toggle_get(int id)
{
    init_once();
    return id >= 0 && id < RT_TOG_COUNT ? atomic_load(&s_val[id]) : 0;
}

const char *rt_toggle_name(int id) { return id >= 0 && id < RT_TOG_COUNT ? k_tog[id].name : ""; }

const char *rt_toggle_title(int id) { return id >= 0 && id < RT_TOG_COUNT ? k_tog[id].title : ""; }

void rt_toggle_request(int id, int source)
{
    init_once();
    if (id < 0 || id >= RT_TOG_COUNT) return;
    int none = 0;
    atomic_compare_exchange_strong(&s_pending[id], &none, source ? source : RT_SRC_MENU);
}

void rt_toggle_set_available(int id, uint32_t steps, const char *reason)
{
    init_once();
    if (id < 0 || id >= RT_TOG_COUNT) return;
    s_reason[id] = reason ? reason : "";
    atomic_store(&s_mask[id], steps);
    int v = atomic_load(&s_val[id]);
    if (!steps || v >= k_tog[id].n || steps >> v & 1u) return;
    int d = s_player ? k_tog[id].def_player : k_tog[id].def;
    s_held[id] = v + 1;
    if (d == CUSTOM) snprintf(s_custom, sizeof s_custom, "%s", DEF_CUSTOM);
    atomic_store(&s_val[id], d);
    rt_log("toggle %s %s unavailable, %s this run, settings keep %s", k_tog[id].name, val_name(id, v), val_name(id, d), val_name(id, v));
}

uint32_t rt_toggle_available(int id, const char **reason)
{
    init_once();
    if (id < 0 || id >= RT_TOG_COUNT) return 0;
    if (reason) *reason = s_reason[id];
    return atomic_load(&s_mask[id]);
}

int rt_toggle_chord(uint32_t scancode)
{
    for (size_t i = 0; i < sizeof k_menu / sizeof *k_menu; i++)
        if (scancode == SDL_SCANCODE_1 + (uint32_t)k_menu[i].digit - 1) return k_menu[i].id;
    return -1;
}

int rt_toggle_key(int id)
{
    for (size_t i = 0; i < sizeof k_menu / sizeof *k_menu; i++)
        if (k_menu[i].id == id) return k_menu[i].digit;
    return 0;
}

const char *rt_toggle_value(int id, char *buf, size_t n)
{
    int v = rt_toggle_get(id);
    snprintf(buf, n, "%s", val_name(id, v));
    return buf;
}

const char *rt_toggle_label(int id, char *buf, size_t n)
{
    int v = rt_toggle_get(id);
    const char *s = !atomic_load(&s_mask[id]) ? "unavailable" : v == CUSTOM ? s_custom : k_tog[id].lab[v];
    snprintf(buf, n, "%s", s);
    return buf;
}

const char *rt_dxvk_config(const char *base, const char *user, char *out, size_t n)
{
    if (user) snprintf(out, n, "%s", user);
    else snprintf(out, n, "%s", base);
    rt_log("gfx env DXVK_CONFIG=%s (%s)", out, user ? "kept" : "set");
    return out;
}

static void aspect_now(rt_aspect *a)
{
    int v = rt_toggle_get(RT_TOG_ASPECT);
    if (v == CUSTOM) rt_aspect_parse(s_custom, a);
    else rt_aspect_from_step(v, a);
}

void rt_aspect_launch_set(int w, int h)
{
    s_launch_w = w;
    s_launch_h = h;
}

static void aspect_start_fix(int dw, int dh)
{
    if (rt_toggle_get(RT_TOG_ASPECT) != RT_ASPECT_STEP_FIT || dh <= 0) return;
    double r = (double)dw / dh;
    if (!rt_aspect_fit_skipped(r)) return;
    atomic_store(&s_val[RT_TOG_ASPECT], fabs(r / (16.0 / 9.0) - 1.0) <= fabs(r / (43.0 / 18.0) - 1.0) ? RT_ASPECT_STEP_169 : RT_ASPECT_STEP_219);
}

void rt_aspect_launch_size(int *w, int *h)
{
    if (s_launch_w <= 0 || s_launch_h <= 0) {
        rt_aspect a;
        int dw, dh;
        rt_desktop_size(&dw, &dh);
        aspect_start_fix(dw, dh);
        aspect_now(&a);
        int bw, bh, ux, uy, uw, uh;
        rt_aspect_size(&a, dw, dh, &bw, &bh);
        s_launch_scale = 1.0;
        if (RT_ENGINE_OVERRIDE && a.kind != RT_ASPECT_ENGINE) {
            if (a.kind != RT_ASPECT_SIZE && rt_desktop_usable(&ux, &uy, &uw, &uh)) rt_aspect_size(&a, uw, uh, &bw, &bh);
            if (rt_toggle_get(RT_TOG_PICTURE) == RT_PIC_NATIVE) s_launch_scale = rt_desktop_density();
            s_launch_w = (int)lround(bw * s_launch_scale);
            s_launch_h = (int)lround(bh * s_launch_scale);
        } else rt_aspect_engine_mode(bw, bh, &s_launch_w, &s_launch_h);
    }
    *w = s_launch_w;
    *h = s_launch_h;
}

static int s_opt_w;

void rt_aspect_options_value(const char *name, uint32_t v)
{
    char spec[32];
    if (!rt_stage6_ns()) return;
    if (!strcasecmp(name, "ScreenWidth")) {
        s_opt_w = v > 0 && v <= 99999 ? (int)v : 0;
        return;
    }
    if (strcasecmp(name, "ScreenHeight") || s_opt_w <= 0 || v == 0 || v > 99999) return;
    if (!s_opt_picked && s_opt_w == s_launch_w && (int)v == s_launch_h) return;
    s_opt_picked = 1;
    snprintf(spec, sizeof spec, "%dx%d", s_opt_w, (int)v);
    if (!rt_toggle_parse(RT_TOG_ASPECT, spec)) {
        rt_window_forget_saved();
        rt_settings_save();
    }
}

static double s_density;

static uint32_t s_density_id;
static double s_density_of_id;

void rt_desktop_density_set(double d) { s_density = d; }

void rt_display_density_set(uint32_t id, double d)
{
    s_density_id = id;
    s_density_of_id = d;
}

double rt_desktop_density(void)
{
    if (s_density > 0) return s_density;
    uint32_t id = rt_window_display();
    if (s_density_id && id == s_density_id) return s_density_of_id;
    const SDL_DisplayMode *m = SDL_GetDesktopDisplayMode((SDL_DisplayID)id);
    return m && m->pixel_density >= 1.0f ? m->pixel_density : 1.0;
}

int rt_aspect_launch_native(void)
{
    int w, h;
    rt_aspect_launch_size(&w, &h);
    return s_launch_scale != 1.0;
}

void rt_aspect_native_window(int bw, int bh, int *ww, int *wh)
{
    double w = bw / s_launch_scale, h = bh / s_launch_scale;
    rt_aspect a;
    int ux, uy, uw, uh;
    aspect_now(&a);
    if (RT_ENGINE_OVERRIDE && a.kind == RT_ASPECT_SIZE && rt_desktop_usable(&ux, &uy, &uw, &uh)) {
        double f = fmin(1.0, fmin((double)uw / w, (double)uh / h));
        w *= f;
        h *= f;
    }
    *ww = (int)lround(w);
    *wh = (int)lround(h);
}

static int custom_ok(const char *v)
{
    rt_aspect a;
    return strlen(v) < sizeof s_custom && rt_aspect_parse(v, &a) && (a.kind == RT_ASPECT_SIZE || a.kind == RT_ASPECT_RATIO);
}

int rt_toggle_parse(int id, const char *value)
{
    init_once();
    if (id < 0 || id >= RT_TOG_COUNT) return -1;
    for (int i = 0; i < k_tog[id].n; i++)
        if (!strcmp(k_tog[id].val[i], value)) {
            s_held[id] = 0;
            atomic_store(&s_val[id], i);
            return 0;
        }
    if (id == RT_TOG_ASPECT && custom_ok(value)) {
        snprintf(s_custom, sizeof s_custom, "%s", value);
        atomic_store(&s_val[id], CUSTOM);
        return 0;
    }
    return -1;
}

unsigned rt_toggle_log_count(void) { return atomic_load(&s_logged); }

const char *rt_toggle_last(void)
{
    int i = atomic_load(&s_last);
    return i < 0 ? "" : k_tog[i].name;
}

const char *rt_settings_dir(void) { return s_dir; }

unsigned rt_settings_warnings(void) { return atomic_load(&s_warns); }

void rt_toggles_apply(void)
{
    init_once();
    int saved = 0;
    for (int id = 0; id < RT_TOG_COUNT; id++) {
        int src = atomic_exchange(&s_pending[id], 0);
        if (!src) continue;
        const char *how = src == RT_SRC_CHORD ? "chord" : src == RT_SRC_SETTINGS ? "settings" : "menu";
        uint32_t mask = atomic_load(&s_mask[id]);
        atomic_store(&s_last, id);
        if (!mask) {
            rt_log("toggle %s unavailable (%s)", k_tog[id].name, s_reason[id]);
            atomic_fetch_add(&s_logged, 1);
            continue;
        }
        int cur = atomic_load(&s_val[id]), n = k_tog[id].n, next = cur;
        char old[32];
        rt_toggle_value(id, old, sizeof old);
        if (id == RT_TOG_PICTURE) mask &= ~(1u << RT_PIC_NATIVE);
        if (id == RT_TOG_ASPECT) {
            int dw, dh;
            rt_desktop_size(&dw, &dh);
            if (dh > 0 && rt_aspect_fit_skipped((double)dw / dh)) mask &= ~(1u << RT_ASPECT_STEP_FIT);
        }
        int base = cur >= n ? n - 1 : cur;
        for (int k = 1; k <= n; k++) {
            int s = (base + k) % n;
            if (mask >> s & 1u) {
                next = s;
                break;
            }
        }
        if (next == cur) continue;
        s_held[id] = 0;
        atomic_store(&s_val[id], next);
        rt_log("toggle %s %s -> %s (%s)", k_tog[id].name, old, k_tog[id].val[next], how);
        atomic_fetch_add(&s_logged, 1);
        saved = 1;
    }
    if (saved && s_dir[0]) rt_settings_save();
}

static int mkdir_p(const char *path)
{
    char b[1024];
    snprintf(b, sizeof b, "%s", path);
    for (char *p = b + 1; *p; p++) {
        if (*p != '/') continue;
        *p = 0;
        if (mkdir(b, 0755) && errno != EEXIST) return -1;
        *p = '/';
    }
    return mkdir(b, 0755) && errno != EEXIST ? -1 : 0;
}

static void warn(const char *fmt, const char *a, const char *b)
{
    rt_log(fmt, a, b);
    atomic_fetch_add(&s_warns, 1);
}

int rt_settings_load(const char *dir, int player)
{
    rt_toggles_defaults(player);
    rt_window_settings_clear();
    s_save_warned = 0;
    snprintf(s_dir, sizeof s_dir, "%s", dir ? dir : "");
    if (!s_dir[0]) return 0;
    mkdir_p(s_dir);
    char path[1100], line[128];
    snprintf(path, sizeof path, "%s/settings.txt", s_dir);
    FILE *f = fopen(path, "r");
    if (!f) {
        if (errno == ENOENT) return 0;
        warn("settings: cannot read %s (%s)", path, strerror(errno));
        return -1;
    }
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        char *eq = strchr(line, '=');
        if (!line[0] || line[0] == '#') continue;
        if (!eq) {
            warn("settings: ignored line \"%s\"%s", line, "");
            continue;
        }
        *eq = 0;
        int win = rt_window_settings_parse(line, eq + 1);
        if (win < 0) warn("settings: bad value \"%s\" for %s, default kept", eq + 1, line);
        if (win <= 0) continue;
        int id = -1, gone = 0;
        for (size_t i = 0; i < sizeof k_dropped / sizeof *k_dropped; i++)
            if (!strcmp(line, k_dropped[i])) gone = 1;
        if (gone) continue;
        for (int i = 0; i < RT_TOG_COUNT; i++)
            if (!strcmp(line, k_tog[i].name)) id = i;
        if (id < 0) {
            warn("settings: unknown name \"%s\"%s", line, "");
            continue;
        }
        char old[32];
        rt_toggle_value(id, old, sizeof old);
        if (rt_toggle_parse(id, player && id == RT_TOG_PICTURE && !strcmp(eq + 1, "native") ? "off" : eq + 1)) {
            warn("settings: bad value \"%s\" for %s, default kept", eq + 1, line);
            continue;
        }
        char now[32];
        rt_toggle_value(id, now, sizeof now);
        if (strcmp(old, now)) rt_log("toggle %s %s -> %s (settings)", line, old, now);
    }
    fclose(f);
    return 0;
}

int rt_settings_save(void)
{
    static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    if (!s_dir[0]) return -1;
    char path[1100], tmp[1110], v[32];
    snprintf(path, sizeof path, "%s/settings.txt", s_dir);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    pthread_mutex_lock(&mu);
    FILE *f = fopen(tmp, "w");
    int bad = !f;
    if (f) {
        for (int i = 0; i < RT_TOG_COUNT; i++)
            fprintf(f, "%s=%s\n", k_tog[i].name, s_held[i] ? k_tog[i].val[s_held[i] - 1] : rt_toggle_value(i, v, sizeof v));
        rt_window_settings_write(f);
        bad = fclose(f) != 0 || rename(tmp, path) != 0;
    }
    pthread_mutex_unlock(&mu);
    if (bad && !s_save_warned) {
        s_save_warned = 1;
        warn("settings: cannot write %s (%s)", path, strerror(errno));
    }
    return bad ? -1 : 0;
}

typedef struct {
    uint64_t ns;
    uint8_t made;
} present;

static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static present s_ring[RING];
static unsigned s_head, s_count;

void rt_fps_present(uint64_t now_ns, int made)
{
    pthread_mutex_lock(&s_mu);
    s_ring[s_head] = (present){now_ns, made != 0};
    s_head = (s_head + 1) % RING;
    if (s_count < RING) s_count++;
    pthread_mutex_unlock(&s_mu);
}

void rt_fps_reset(void)
{
    pthread_mutex_lock(&s_mu);
    s_head = s_count = 0;
    pthread_mutex_unlock(&s_mu);
}

static int cmp(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    return x < y ? -1 : x > y;
}

static double pct(double *v, unsigned n, int p)
{
    if (!n) return 0;
    qsort(v, n, sizeof *v, cmp);
    unsigned i = (n * (unsigned)p + 99) / 100;
    return v[i ? i - 1 : 0];
}

static void stats(long *real, long *made, double *p50, double *p99)
{
    static double gaps[RING];
    *real = *made = 0;
    *p50 = *p99 = 0;
    pthread_mutex_lock(&s_mu);
    unsigned n = s_count, first = n;
    uint64_t last = n ? s_ring[(s_head + RING - 1) % RING].ns : 0;
    uint64_t start = last > WINDOW_NS ? last - WINDOW_NS : 0;
    for (unsigned k = 0; k < n; k++) {
        if (s_ring[(s_head + RING - 1 - k) % RING].ns < start) break;
        first = n - 1 - k;
    }
    unsigned g = 0;
    for (unsigned i = first; i < n; i++) {
        const present *p = &s_ring[(s_head + RING - n + i) % RING];
        if (i > first) gaps[g++] = (double)(p->ns - s_ring[(s_head + RING - n + i - 1) % RING].ns) / 1e6;
        if (p->ns > start) {
            if (p->made) (*made)++;
            else (*real)++;
        }
    }
    pthread_mutex_unlock(&s_mu);
    unsigned nsorted = g;
    *p50 = pct(gaps, nsorted, 50);
    *p99 = pct(gaps, nsorted, 99);
}

void rt_fps_stats(double *fps, double *p50, double *p99)
{
    long real, made;
    double a, b;
    stats(&real, &made, &a, &b);
    if (fps) *fps = (double)real / 2.0;
    if (p50) *p50 = a;
    if (p99) *p99 = b;
}

int rt_fps_text(char *b, size_t n)
{
    long real, made;
    double a, c;
    stats(&real, &made, &a, &c);
    long r = lrint((double)real / 2.0), m = lrint((double)made / 2.0);
    return m ? snprintf(b, n, "%ld+%ld FPS", r, m) : snprintf(b, n, "%ld FPS", r);
}
