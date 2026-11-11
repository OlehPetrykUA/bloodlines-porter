#include "fx.h"
#include "input.h"
#include "toggles.h"
#include "winstate.h"
#include "winstate_selftest.h"

#include <SDL3/SDL.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    const char *name;
    int (*run)(const char *dir, int player);
} m9_case;

static const char *const k_nondefault[RT_TOG_COUNT] = {"fps", "native", "21:9"};

static void row(const char *c, const char *label, const char *fmt, ...) __attribute__((format(printf, 3, 4)));

static void row(const char *c, const char *label, const char *fmt, ...)
{
    char b[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    printf("m9 %s %s%s%s\n", c, label, b[0] ? " " : "", b);
    fflush(stdout);
}

static void values(char *b, size_t n)
{
    char v[32];
    size_t used = 0;
    b[0] = 0;
    for (int i = 0; i < RT_TOG_COUNT && used < n; i++)
        used += (size_t)snprintf(b + used, n - used, "%s%s=%s", i ? " " : "", rt_toggle_name(i), rt_toggle_value(i, v, sizeof v));
}

static int path_in(char *out, size_t n, const char *dir, const char *name)
{
    return snprintf(out, n, "%s/%s", dir, name) >= (int)n;
}

static int case_menu(const char *dir, int player)
{
    char p[PATH_MAX];
    if (path_in(p, sizeof p, dir, "menu.tsv")) return 1;
    rt_set_player(player);
    rt_toggles_defaults(0);
    rt_fx_probe();
    return rt_hud_dump_menu(p) ? 1 : 0;
}

static const char *s_overlay;
static int s_frames;

static int case_fx(const char *dir, int player)
{
    (void)dir;
    (void)player;
    int sp, tp, in, m4;
    rt_fx_probe();
    rt_fx_support(&sp, &tp, &in, &m4);
    row("fx", "support", "spatial=%d temporal=%d interpolator=%d metal4fx=%d", sp, tp, in, m4);
    const char *reason = "";
    uint32_t mask = rt_toggle_available(RT_TOG_PICTURE, &reason);
    char start[32], v[32], seq[128] = "";
    rt_toggle_value(RT_TOG_PICTURE, start, sizeof start);
    int presses = mask ? __builtin_popcount(mask & ~(1u << RT_PIC_NATIVE)) : 2;
    for (int k = 0; k < presses; k++) {
        rt_toggle_request(RT_TOG_PICTURE, RT_SRC_CHORD);
        rt_toggles_apply();
        snprintf(seq + strlen(seq), sizeof seq - strlen(seq), "%s%s", k ? " " : "", rt_toggle_value(RT_TOG_PICTURE, v, sizeof v));
    }
    int back = !strcmp(v, start);
    int off = !strcmp(start, "off");
    row("fx", "cycle", "mask=%u start=%s seq=\"%s\" back=%d native=%d reason=\"%s\"", mask, start, seq, back, RT_ENGINE_OVERRIDE, reason);
    int bad = !off || (sp ? mask != (RT_ENGINE_OVERRIDE ? 7u : 5u) || !back || strcmp(seq, "metalfx off") != 0
                          : mask != 0 || !reason[0] || !back);
    row("fx", "result", "bad=%d", bad);
    return bad;
}

static int case_fx_spatial(const char *dir, int player)
{
    (void)dir;
    (void)player;
    if (!rt_fx_probe()) {
        row("fx-spatial", "fallback", "picture=off");
        return 0;
    }
    rt_arena_use(rt_arena_new());
    rt_load();
    return rt_gfx_fx_selftest(s_overlay, s_frames);
}

typedef struct {
    int n;
    uint64_t t[1024];
    uint8_t m[1024];
} series;

static void add(series *s, uint64_t t, int made)
{
    s->t[s->n] = t;
    s->m[s->n++] = (uint8_t)made;
}

static void make_series(series *s, const char *name)
{
    s->n = 0;
    if (!strcmp(name, "steady")) {
        for (int i = 0; i <= 600; i++) add(s, (uint64_t)i * 5000000u, 0);
    } else if (!strcmp(name, "spike")) {
        uint64_t t = 0;
        add(s, 0, 0);
        for (int i = 0; i < 600; i++) {
            int back = 599 - i;
            t += back == 60 || back == 150 || back == 260 || back == 387 ? 20000000u : 5000000u;
            add(s, t, 0);
        }
    } else {
        for (int i = 0; i <= 300; i++) {
            if (i) add(s, (uint64_t)i * 10000000u - 5000000u, 1);
            add(s, (uint64_t)i * 10000000u, 0);
        }
    }
}

static int case_fps(const char *dir, int player)
{
    (void)dir;
    (void)player;
    static const char *const names[] = {"steady", "spike", "interp"};
    static series s;
    for (int k = 0; k < 3; k++) {
        rt_fps_reset();
        make_series(&s, names[k]);
        for (int i = 0; i < s.n; i++) rt_fps_present(s.t[i], s.m[i]);
        char title[48];
        double fps, p50, p99;
        rt_fps_text(title, sizeof title);
        rt_fps_stats(&fps, &p50, &p99);
        row("fps", names[k], "title=\"%s\" p50=%.2f p99=%.2f", title, p50, p99);
    }
    rt_set_player(1);
    rt_stage(10, "%s", "");
    rt_fps_reset();
    make_series(&s, "steady");
    for (int i = 0; i < s.n; i++) rt_fps_present(s.t[i], s.m[i]);
    double fps, p50, p99;
    rt_fps_stats(&fps, &p50, &p99);
    row("fps", "player", "fps=%.0f p50=%.2f frames-start=%d", fps, p50, rt_frames_start_ns() != 0);
    rt_set_player(player);
    return 0;
}

static int render(const char *dir, const char *state, int s, const rt_hud_info *i)
{
    char png[PATH_MAX], tsv[PATH_MAX], a[64], b[64];
    snprintf(a, sizeof a, "overlay-%s.png", state);
    snprintf(b, sizeof b, "overlay-%s.tsv", state);
    if (path_in(png, sizeof png, dir, a) || path_in(tsv, sizeof tsv, dir, b)) return 1;
    return rt_hud_render(s, i, png, tsv) ? 1 : 0;
}

static int case_overlay(const char *dir, int player)
{
    rt_set_player(player);
    rt_hud_info a = {200, 5.0, 7.5, 120, 2, 1.5, "all", "unavailable"};
    rt_hud_info b = a;
    b.fps = 144;
    b.p50 = 6.9;
    b.p99 = 9.1;
    b.load = 2.25;
    b.picture = "native";
    return render(dir, "all", RT_HUD_ALL, &a) | render(dir, "all2", RT_HUD_ALL, &b) | render(dir, "fps", RT_HUD_FPS, &a) |
           render(dir, "off", RT_HUD_OFF, &a);
}

static void write_file(const char *dir, const char *text)
{
    char p[PATH_MAX];
    if (path_in(p, sizeof p, dir, "settings.txt")) return;
    FILE *f = fopen(p, "w");
    if (!f) return;
    fputs(text, f);
    fclose(f);
}

static int file_has(const char *dir, const char *text)
{
    char p[PATH_MAX], b[1024];
    if (path_in(p, sizeof p, dir, "settings.txt")) return 0;
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    size_t n = fread(b, 1, sizeof b - 1, f);
    fclose(f);
    b[n] = 0;
    return strstr(b, text) != NULL;
}

static void fresh(const char *base, const char *name, char *out, size_t n)
{
    snprintf(out, n, "%s/%s", base, name);
    mkdir(out, 0755);
    char p[PATH_MAX];
    snprintf(p, sizeof p, "%s/settings.txt", out);
    unlink(p);
}

static void state_row(const char *label, const char *extra, int player, const char *d)
{
    char v[512];
    rt_settings_load(d, player);
    values(v, sizeof v);
    row("settings", label, "%s%s%s", extra, extra[0] ? " " : "", v);
}

static int case_settings(const char *dir, int player)
{
    (void)player;
    char d[PATH_MAX], v[32], w[32];
    for (int i = 0; i < RT_TOG_COUNT; i++) {
        char name[64];
        snprintf(name, sizeof name, "rt-%s", rt_toggle_name(i));
        fresh(dir, name, d, sizeof d);
        rt_settings_load(d, 0);
        rt_toggle_parse(i, k_nondefault[i]);
        rt_toggle_value(i, v, sizeof v);
        rt_settings_save();
        rt_settings_load(d, 0);
        rt_toggle_value(i, w, sizeof w);
        snprintf(name, sizeof name, "roundtrip-%s", rt_toggle_name(i));
        row("settings", name, "wrote=%s read=%s", v, w);
    }
    fresh(dir, "defaults-run", d, sizeof d);
    state_row("defaults-run", "", 0, d);
    fresh(dir, "defaults-player", d, sizeof d);
    state_row("defaults-player", "", 1, d);
    fresh(dir, "no-file", d, sizeof d);
    state_row("no-file", "", 0, d);
    fresh(dir, "unknown-name", d, sizeof d);
    write_file(d, "bogus=1\n");
    unsigned w0 = rt_settings_warnings();
    rt_settings_load(d, 0);
    char b[512], x[64];
    values(b, sizeof b);
    snprintf(x, sizeof x, "unknown_lines=%u", rt_settings_warnings() - w0);
    row("settings", "unknown-name", "%s %s", x, b);
    fresh(dir, "bad-value", d, sizeof d);
    write_file(d, "aspect=sideways\n");
    w0 = rt_settings_warnings();
    rt_settings_load(d, 0);
    values(b, sizeof b);
    snprintf(x, sizeof x, "bad_name=aspect bad_lines=%u", rt_settings_warnings() - w0);
    row("settings", "bad-value", "%s %s", x, b);
    fresh(dir, "readonly", d, sizeof d);
    rt_settings_load(d, 0);
    chmod(d, 0555);
    w0 = rt_settings_warnings();
    rt_toggle_request(RT_TOG_OVERLAYS, RT_SRC_MENU);
    rt_toggles_apply();
    rt_toggle_request(RT_TOG_OVERLAYS, RT_SRC_MENU);
    rt_toggles_apply();
    unsigned lines = rt_settings_warnings() - w0;
    chmod(d, 0755);
    row("settings", "readonly", "readonly_lines=%u survived=1", lines);
    static const char user[] = "d3d9.maxFrameLatency = 1";
    char cfg[256];
    fresh(dir, "dxvk", d, sizeof d);
    write_file(d, "aniso=16\n");
    rt_settings_load(d, 0);
    row("settings", "dxvk-set", "value='%s'", rt_dxvk_config("d3d9.a = 1", NULL, cfg, sizeof cfg));
    row("settings", "dxvk-user", "value='%s' user='%s' kept=%d", rt_dxvk_config("d3d9.a = 1", user, cfg, sizeof cfg), user,
        !strcmp(cfg, user));
    fresh(dir, "dropped-keys", d, sizeof d);
    write_file(d, "interp=on\nsmoothing=on\nhdr=on\ncontroller=xbox\naniso=16\nmouse=on\n");
    w0 = rt_settings_warnings();
    rt_settings_load(d, 0);
    row("settings", "dropped-keys", "warn_lines=%u", rt_settings_warnings() - w0);
    fresh(dir, "player-native", d, sizeof d);
    write_file(d, "picture=native\n");
    w0 = rt_settings_warnings();
    rt_settings_load(d, 1);
    rt_toggle_value(RT_TOG_PICTURE, v, sizeof v);
    unsigned pw = rt_settings_warnings() - w0;
    rt_toggle_request(RT_TOG_OVERLAYS, RT_SRC_MENU);
    rt_toggles_apply();
    int pfile = file_has(d, "picture=off\n");
    write_file(d, "picture=native\n");
    rt_settings_load(d, 0);
    rt_toggle_value(RT_TOG_PICTURE, w, sizeof w);
    row("settings", "player-native", "player=%s warn_lines=%u saved_off=%d dev=%s", v, pw, pfile, w);
    fresh(dir, "held-step", d, sizeof d);
    write_file(d, "picture=native\n");
    rt_settings_load(d, 0);
    rt_toggle_set_available(RT_TOG_PICTURE, 5u, "");
    rt_toggle_value(RT_TOG_PICTURE, v, sizeof v);
    int kept = file_has(d, "picture=native\n");
    rt_toggle_request(RT_TOG_OVERLAYS, RT_SRC_MENU);
    rt_toggles_apply();
    int saved = file_has(d, "picture=native\n") && file_has(d, "overlays=");
    row("settings", "held-step", "saved=native mask=5 value=%s file_kept=%d save_kept=%d", v, kept, saved);
    int held_bad = strcmp(v, "off") || !kept || !saved;
    fresh(dir, "options-video", d, sizeof d);
    rt_settings_load(d, 0);
    int lw, lh;
    rt_aspect_launch_size(&lw, &lh);
    rt_aspect_options_value("ScreenWidth", 1728);
    rt_aspect_options_value("ScreenHeight", 1080);
    int before6 = file_has(d, "aspect=1728x1080\n");
    rt_stage(6, "%s", "");
    rt_aspect_options_value("ScreenWidth", 1728);
    rt_aspect_options_value("ScreenHeight", 1080);
    int after6 = file_has(d, "aspect=1728x1080\n");
    rt_settings_load(d, 0);
    rt_toggle_value(RT_TOG_ASPECT, v, sizeof v);
    row("settings", "options-video", "before_stage6=%d after_stage6=%d reload=%s", before6, after6, v);
    rt_aspect_options_value("ScreenWidth", (uint32_t)lw);
    rt_aspect_options_value("ScreenHeight", (uint32_t)lh);
    char back[48];
    snprintf(back, sizeof back, "aspect=%dx%d\n", lw, lh);
    int picked_back = file_has(d, back);
    row("settings", "options-video-back", "launch=%dx%d stored=%d", lw, lh, picked_back);
    fresh(dir, "options-window", d, sizeof d);
    char p[1100];
    snprintf(p, sizeof p, "%s/settings.txt", d);
    FILE *f = fopen(p, "w");
    if (f) {
        fputs("window=10,20,800,600\n", f);
        fclose(f);
    }
    rt_settings_load(d, 0);
    rt_settings_save();
    int rect_kept = file_has(d, "window=10,20,800,600\n");
    rt_aspect_options_value("ScreenWidth", 1000);
    rt_aspect_options_value("ScreenHeight", 700);
    int rect_gone = !file_has(d, "window=");
    row("settings", "options-window", "kept=%d forgotten_after_pick=%d", rect_kept, rect_gone);
    return held_bad || before6 || !after6 || strcmp(v, "1728x1080") || !picked_back || !rect_kept || !rect_gone;
}

static void keyev(rt_input_state *s, rt_wmsg *m, int *n, uint32_t sc, int down, uint16_t mod)
{
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
    e.key.scancode = (SDL_Scancode)sc;
    e.key.mod = mod;
    e.key.down = down;
    static const rt_present_rects r = {0, 0, 1280, 1024, 0, 0, 1280, 1024};
    *n += rt_input_translate(s, &e, &r, 1.0f, m + *n, 64 - *n);
}

static void count(const rt_wmsg *m, int n, int *keys, int *chars)
{
    *keys = *chars = 0;
    for (int i = 0; i < n; i++) {
        if (m[i].msg == WM_KEYDOWN || m[i].msg == WM_SYSKEYDOWN) (*keys)++;
        if (m[i].msg == WM_CHAR) (*chars)++;
    }
}

static void press(rt_input_state *s, rt_wmsg *m, int *n, uint32_t sc, int cmd)
{
    uint16_t mod = cmd ? SDL_KMOD_LGUI : 0;
    if (cmd) keyev(s, m, n, SDL_SCANCODE_LGUI, 1, mod);
    keyev(s, m, n, sc, 1, mod);
    keyev(s, m, n, sc, 0, mod);
    if (cmd) keyev(s, m, n, SDL_SCANCODE_LGUI, 0, 0);
}

static int case_input(const char *dir, int player)
{
    (void)dir;
    rt_set_player(player);
    rt_toggles_defaults(0);
    for (int cmd = 1; cmd >= 0; cmd--)
        for (int k = 0; k < 8; k++) {
            rt_input_state s;
            rt_wmsg m[64];
            int n = 0, keys, chars;
            rt_input_init(&s);
            s.host_focus = 1;
            unsigned before = rt_toggle_log_count();
            press(&s, m, &n, SDL_SCANCODE_1 + (uint32_t)k, cmd);
            rt_toggles_apply();
            count(m, n, &keys, &chars);
            unsigned t = rt_toggle_log_count() - before;
            char label[16];
            snprintf(label, sizeof label, "%s%d", cmd ? "chord" : "bare", k + 1);
            if (cmd) row("input", label, "name=%s toggles=%u guest_keys=%d guest_chars=%d dev=%d", t ? rt_toggle_last() : "-", t, keys, chars, rt_dev_take() != 0);
            else row("input", label, "toggles=%u guest_keys=%d guest_chars=%d", t, keys, chars);
        }
    rt_input_state s;
    rt_wmsg m[64];
    int n = 0;
    rt_input_init(&s);
    s.host_focus = 1;
    unsigned before = rt_toggle_log_count();
    rt_toggle_request(RT_TOG_OVERLAYS, RT_SRC_MENU);
    press(&s, m, &n, SDL_SCANCODE_1, 1);
    rt_toggles_apply();
    row("input", "both", "name=%s toggles=%u", rt_toggle_last(), rt_toggle_log_count() - before);
    rt_toggles_defaults(0);
    rt_input_init(&s);
    s.host_focus = 1;
    s.cursor = 0;
    s.focus = 1;
    rt_input_relative_update(&s);
    row("input", "capture-default", "relative=%d cursor=%d", s.relative, rt_input_shape(&s) != RT_CUR_HIDDEN);
    rt_toggles_defaults(0);
    rt_input_init(&s);
    s.host_focus = 1;
    s.cursor = 0;
    rt_input_relative_update(&s);
    row("input", "focus-captured", "relative=%d cursor=%d", s.relative, rt_input_shape(&s) != RT_CUR_HIDDEN);
    s.focus = 0;
    rt_input_relative_update(&s);
    row("input", "focus-lost-captured", "relative=%d cursor=%d", s.relative, rt_input_shape(&s) != RT_CUR_HIDDEN);
    s.focus = 1;
    rt_input_relative_update(&s);
    row("input", "focus-regained", "relative=%d cursor=%d", s.relative, rt_input_shape(&s) != RT_CUR_HIDDEN);
    s.host_focus = 0;
    rt_input_relative_update(&s);
    row("input", "host-unfocused", "relative=%d cursor=%d", s.relative, rt_input_shape(&s) != RT_CUR_HIDDEN);
    return 0;
}

static void chord(int digit)
{
    rt_input_state s;
    rt_wmsg m[64];
    int n = 0;
    rt_input_init(&s);
    s.host_focus = 1;
    press(&s, m, &n, SDL_SCANCODE_1 + (uint32_t)digit - 1, 1);
    rt_toggles_apply();
}

static int case_live(const char *dir, int player)
{
    (void)dir;
    rt_set_player(player);
    rt_toggles_defaults(player);
    rt_arena_use(rt_arena_new());
    rt_load();
    int sp, tp, in, m4;
    rt_fx_probe();
    rt_fx_support(&sp, &tp, &in, &m4);
    int bad = 0;
    int l0 = rt_hud_live_lines();
    chord(1);
    int l1 = rt_hud_live_lines();
    chord(1);
    int l2 = rt_hud_live_lines();
    chord(1);
    int l3 = rt_hud_live_lines();
    int ok = l0 != l1 && l1 != l2 && l0 != l2 && l3 == l0;
    row("live", "overlays", "lines=%d,%d,%d back=%d changed=%d", l0, l1, l2, l3 == l0, ok);
    bad |= !ok;
    if (sp) {
        int p0 = rt_fx_mode_on();
        chord(2);
        int p1 = rt_fx_mode_on();
        chord(2);
        int p2 = rt_fx_mode_on();
        ok = p0 == 0 && p1 == 1 && p2 == 0;
        row("live", "picture", "mode_on=%d,%d,%d changed=%d", p0, p1, p2, ok);
    } else {
        ok = !rt_toggle_available(RT_TOG_PICTURE, NULL);
        row("live", "picture", "unsupported=1 changed=%d", ok);
    }
    bad |= !ok;
    unsigned t0 = rt_toggle_log_count();
    for (int d = 3; d <= 8; d++) chord(d);
    ok = rt_toggle_log_count() == t0 && rt_dev_take() == 0;
    row("live", "dead-keys", "toggles=%u ok=%d", rt_toggle_log_count() - t0, ok);
    bad |= !ok;
    row("live", "result", "bad=%d", bad);
    return bad;
}

static int near_hfov(double a, double b) { return fabs(a - b) <= 0.01; }

static int case_aspect(const char *dir, int player)
{
    (void)dir;
    (void)player;
    int bad = 0;
    static const char *const in[] = {"16:9", "21:9", "fit", "engine", "3440x1440", "0x0", "text", "", "16:0", "16:", "x9"};
    static const int want[] = {1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0};
    static const char *const kind[] = {"ratio", "fit", "engine", "size"};
    for (size_t k = 0; k < sizeof in / sizeof *in; k++) {
        rt_aspect a;
        int ok = rt_aspect_parse(in[k], &a);
        bad |= ok != want[k];
        row("aspect", "parse", "input=\"%s\" kind=%s", in[k], ok ? kind[a.kind] : "bad");
    }
    static const int win[3][2] = {{1280, 1024}, {3440, 1440}, {2056, 1329}};
    static const int exp[3][5][2] = {
        {{1280, 720}, {1280, 1024}, {1280, 535}, {1280, 1024}, {3440, 1440}},
        {{2560, 1440}, {3440, 1440}, {3440, 1440}, {1280, 1024}, {3440, 1440}},
        {{2056, 1156}, {2056, 1329}, {2056, 860}, {1280, 1024}, {3440, 1440}},
    };
    for (int k = 0; k < 3; k++) {
        for (int s = 0; s < 4; s++) {
            rt_aspect a;
            int bw, bh;
            rt_aspect_from_step(s, &a);
            rt_aspect_size(&a, win[k][0], win[k][1], &bw, &bh);
            bad |= bw != exp[k][s][0] || bh != exp[k][s][1];
            row("aspect", "size", "state=%s win=%dx%d out=%dx%d", rt_aspect_step_name(s), win[k][0], win[k][1], bw, bh);
        }
        rt_aspect a;
        int bw, bh;
        rt_aspect_parse("3440x1440", &a);
        rt_aspect_size(&a, win[k][0], win[k][1], &bw, &bh);
        bad |= bw != exp[k][4][0] || bh != exp[k][4][1];
        row("aspect", "size", "state=3440x1440 win=%dx%d out=%dx%d", win[k][0], win[k][1], bw, bh);
    }
    {
        static const int k_keep[][6] = {{1920, 1440, 3440, 1316, 1920, 1440}, {1920, 1440, 1280, 800, 1920, 1440}, {800, 600, 3440, 1316, 800, 600},
                                        {320, 200, 3440, 1316, RT_ASPECT_FLOOR_W, RT_ASPECT_FLOOR_H}, {640, 480, 3440, 1316, 800, 600},
                                        {720, 480, 3440, 1316, 720, 480}, {640, 600, 3440, 1316, 640, 600}};
        for (size_t k = 0; k < sizeof k_keep / sizeof *k_keep; k++) {
            rt_aspect a;
            char spec[32];
            int bw, bh;
            snprintf(spec, sizeof spec, "%dx%d", k_keep[k][0], k_keep[k][1]);
            rt_aspect_parse(spec, &a);
            rt_aspect_size(&a, k_keep[k][2], k_keep[k][3], &bw, &bh);
            int ok = bw == k_keep[k][4] && bh == k_keep[k][5];
            bad |= !ok;
            row("aspect", "size-keep", "state=%s win=%dx%d out=%dx%d ok=%d", spec, k_keep[k][2], k_keep[k][3], bw, bh, ok);
        }
        static const int k_win[][7] = {{3440, 1316, 1, 1920, 1440, 1920, 1440}, {2056, 1228, 1, 1920, 1440, 1920, 1440},
                                       {3440, 1316, 1, 800, 600, 800, 600}, {2056, 1228, 2, 1920, 1440, 3840, 2880}};
        int d0w, d0h;
        rt_desktop_size(&d0w, &d0h);
        for (size_t k = 0; k < sizeof k_win / sizeof *k_win; k++) {
            rt_aspect a;
            int bw, bh, ww, wh;
            char spec[32];
            double dens = k_win[k][2];
            snprintf(spec, sizeof spec, "%dx%d", k_win[k][3], k_win[k][4]);
            rt_desktop_usable_set(k_win[k][0], k_win[k][1], k_win[k][0], k_win[k][1], 0);
            rt_desktop_density_set(dens);
            rt_toggles_defaults(0);
            rt_toggle_parse(RT_TOG_ASPECT, spec);
            rt_toggle_parse(RT_TOG_PICTURE, dens > 1.0 ? "native" : "off");
            rt_aspect_launch_size(&bw, &bh);
            rt_aspect_native_window(bw, bh, &ww, &wh);
            rt_aspect_parse(spec, &a);
            int shape = labs((long)ww * a.h - (long)wh * a.w) <= a.w;
            int fits = ww <= k_win[k][0] && wh <= k_win[k][1];
            int exact = bw == k_win[k][5] && bh == k_win[k][6];
            int ok = !RT_ENGINE_OVERRIDE || (shape && fits && exact);
            bad |= !ok;
            row("aspect", "size-window", "usable=%dx%d density=%.0f state=%s backbuffer=%dx%d window=%dx%d shape=%d fits=%d exact=%d override=%d ok=%d",
                k_win[k][0], k_win[k][1], dens, spec, bw, bh, ww, wh, shape, fits, exact, RT_ENGINE_OVERRIDE, ok);
        }
        rt_desktop_usable_set(d0w, d0h, 0, 0, -1);
        rt_desktop_density_set(0);
        rt_toggles_defaults(0);
    }
    {
        uint32_t pw = 0, ph = 0, fw = 0, fh = 0, kw = 800, kh = 600;
        rt_gfx_pp_size(1, 1594, 1196, &pw, &ph);
        rt_gfx_pp_size(1, 1594, 1196, &kw, &kh);
        rt_gfx_pp_size(0, 1594, 1196, &fw, &fh);
        int ok = pw == 1594 && ph == 1196 && kw == 800 && kh == 600 && !fw && !fh;
        bad |= !ok;
        row("aspect", "pp-size", "zero=%ux%u kept=%ux%u full=%ux%u ok=%d", pw, ph, kw, kh, fw, fh, ok);
    }
    static const struct {
        int from;
        double ratio;
        int to;
    } nx[] = {{RT_ASPECT_STEP_169, 1.25, RT_ASPECT_STEP_FIT},      {RT_ASPECT_STEP_169, 16.0 / 9.0, RT_ASPECT_STEP_219},
              {RT_ASPECT_STEP_FIT, 1.25, RT_ASPECT_STEP_219},      {RT_ASPECT_STEP_219, 1.25, RT_ASPECT_STEP_169},
              {RT_ASPECT_STEP_ENGINE, 1.25, RT_ASPECT_STEP_169},   {RT_ASPECT_STEP_ENGINE, 1.7778, RT_ASPECT_STEP_169},
              {RT_ASPECT_STEP_169, 43.0 / 18.0, RT_ASPECT_STEP_219}, {RT_ASPECT_STEP_169, 1.547, RT_ASPECT_STEP_FIT}};
    for (size_t k = 0; k < sizeof nx / sizeof *nx; k++) {
        int to = rt_aspect_next(nx[k].from, nx[k].ratio);
        bad |= to != nx[k].to;
        row("aspect", "next", "from=%s ratio=%.4f to=%s", rt_aspect_step_name(nx[k].from), nx[k].ratio, rt_aspect_step_name(to));
    }
    static const double ratio[] = {16.0 / 9.0 * 1.005, 43.0 / 18.0 * 0.995, 1.5, 16.0 / 10.0, 16.0 / 9.0 * 1.02};
    static const int skip[] = {1, 1, 0, 0, 0};
    for (size_t k = 0; k < sizeof ratio / sizeof *ratio; k++) {
        int s = rt_aspect_fit_skipped(ratio[k]);
        bad |= s != skip[k];
        row("aspect", "fit", "ratio=%.4f skipped=%d next_after_169=%d", ratio[k], s, rt_aspect_next(RT_ASPECT_STEP_169, ratio[k]));
    }
    static const double dims[][3] = {{60, 1280, 1024}, {60, 1280, 720}, {60, 3440, 1440}, {75, 2056, 1329}};
    for (size_t k = 0; k < sizeof dims / sizeof *dims; k++) {
        double h = rt_aspect_hfov(dims[k][0], dims[k][1], dims[k][2]), v = rt_aspect_fovy(h, dims[k][1] / dims[k][2]);
        bad |= !near_hfov(v, dims[k][0]);
        row("aspect", "hfov", "vfov=%.1f w=%.0f h=%.0f out=%.3f", dims[k][0], dims[k][1], dims[k][2], h);
    }
    static const int box[][4] = {{1920, 1080, 3440, 1440}, {3440, 1440, 1920, 1080}, {1280, 1024, 1280, 1024}, {1280, 1024, 1728, 1117}, {1920, 1080, 1728, 1117}};
    for (size_t k = 0; k < sizeof box / sizeof *box; k++) {
        int r[4];
        rt_aspect_letterbox(box[k][0], box[k][1], box[k][2], box[k][3], r);
        long skew = labs((long)r[2] * box[k][1] - (long)r[3] * box[k][0]);
        int inside = r[0] >= 0 && r[1] >= 0 && r[0] + r[2] <= box[k][2] && r[1] + r[3] <= box[k][3];
        int centred = labs(r[0] - (box[k][2] - r[2] - r[0])) <= 1 && labs(r[1] - (box[k][3] - r[3] - r[1])) <= 1;
        bad |= skew > box[k][0] || skew > box[k][1] || !inside || !centred;
        row("aspect", "rect", "bw=%d bh=%d dx=%d dy=%d dw=%d dh=%d within=%dx%d", box[k][0], box[k][1], r[0], r[1], r[2], r[3], box[k][2],
            box[k][3]);
    }
    rt_toggles_defaults(0);
    int dw, dh;
    rt_desktop_size(&dw, &dh);
    int fit_in = !rt_aspect_fit_skipped((double)dw / dh);
    char seq[128] = "", v[32];
    int cur = rt_toggle_get(RT_TOG_ASPECT);
    bad |= strcmp(rt_toggle_value(RT_TOG_ASPECT, v, sizeof v), "4:3") != 0;
    for (int k = 0; k < 4; k++) {
        int next = cur >= RT_ASPECT_STEP_219 ? RT_ASPECT_STEP_169 : cur + 1;
        if (next == RT_ASPECT_STEP_FIT && !fit_in) next = RT_ASPECT_STEP_219;
        rt_toggle_request(RT_TOG_ASPECT, RT_SRC_CHORD);
        rt_toggles_apply();
        bad |= rt_toggle_get(RT_TOG_ASPECT) != next;
        cur = next;
        snprintf(seq + strlen(seq), sizeof seq - strlen(seq), "%s%s", k ? " " : "", rt_toggle_value(RT_TOG_ASPECT, v, sizeof v));
    }
    row("aspect", "cycle", "desktop=%dx%d fit_in_cycle=%d from_start=\"%s\"", dw, dh, fit_in, seq);
    rt_toggle_parse(RT_TOG_ASPECT, "engine");
    rt_toggle_request(RT_TOG_ASPECT, RT_SRC_CHORD);
    rt_toggles_apply();
    row("aspect", "cycle", "from_engine=%s", rt_toggle_value(RT_TOG_ASPECT, v, sizeof v));
    bad |= !strcmp(v, "engine");
    rt_toggles_defaults(0);
    rt_toggle_parse(RT_TOG_ASPECT, "3440x1440");
    rt_toggle_request(RT_TOG_ASPECT, RT_SRC_CHORD);
    rt_toggles_apply();
    row("aspect", "cycle", "from_custom=%s", rt_toggle_value(RT_TOG_ASPECT, v, sizeof v));
    bad |= !strcmp(v, "3440x1440") || !strcmp(v, "engine");
    rt_toggles_defaults(0);
    rt_toggle_parse(RT_TOG_ASPECT, "21:9");
    rt_aspect_launch_set(0, 0);
    int lw, lh;
    rt_aspect_launch_size(&lw, &lh);
    row("aspect", "launch", "aspect=21:9 desktop=%dx%d size=%dx%d", dw, dh, lw, lh);
    rt_toggles_defaults(0);
    rt_aspect_launch_size(&lw, &lh);
    char dv[32];
    rt_toggle_value(RT_TOG_ASPECT, dv, sizeof dv);
    bad |= strcmp(dv, "4:3") != 0;
    bad |= lw > dw || lh > dh;
    int ew, eh, fw, fh;
    rt_aspect a;
    rt_aspect_parse(dv, &a);
    rt_aspect_size(&a, dw, dh, &fw, &fh);
    rt_aspect_engine_mode(lw, lh, &ew, &eh);
    if (RT_ENGINE_OVERRIDE) bad |= lw != fw || lh != fh;
    else bad |= (dw >= 1280 && dh >= 1024 && (lw < 1280 || lh < 1024)) || ew != lw || eh != lh;
    row("aspect", "launch", "aspect=default start=%s size=%dx%d override=%d", dv, lw, lh, RT_ENGINE_OVERRIDE);
    static const char *const k_tin[] = {"16:10", "5:4", "32:9", "4:3", "2560x1600", "16:0", "16:", "x9", "0x0", "0:9", "16:10x"};
    static const int k_tok[] = {1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0};
    for (size_t k = 0; k < sizeof k_tin / sizeof *k_tin; k++) {
        char tv[32];
        rt_toggles_defaults(0);
        int took = rt_toggle_parse(RT_TOG_ASPECT, k_tin[k]) == 0;
        rt_toggle_value(RT_TOG_ASPECT, tv, sizeof tv);
        int ok = took == k_tok[k] && !strcmp(tv, took ? k_tin[k] : "4:3");
        bad |= !ok;
        row("aspect", "toggle-parse", "input=\"%s\" taken=%d value=%s ok=%d", k_tin[k], took, tv, ok);
    }
    {
        int d0w, d0h, tw, th;
        rt_desktop_size(&d0w, &d0h);
        rt_desktop_usable_set(3440, 1440, 3440, 1316, 0);
        rt_toggles_defaults(0);
        rt_toggle_parse(RT_TOG_ASPECT, "16:10");
        rt_aspect_launch_set(0, 0);
        rt_aspect_launch_size(&tw, &th);
        int ok = RT_ENGINE_OVERRIDE ? tw == 2105 && th == 1316 : tw == 1600 && th == 1200;
        bad |= !ok;
        row("aspect", "launch", "aspect=16:10 desktop=3440x1440 usable=3440x1316 size=%dx%d override=%d ok=%d", tw, th, RT_ENGINE_OVERRIDE, ok);
        rt_desktop_usable_set(d0w, d0h, 0, 0, -1);
        rt_toggles_defaults(0);
        rt_aspect_launch_set(0, 0);
    }
    static const double k_dens[] = {1.0, 2.0};
    for (size_t k = 0; k < sizeof k_dens / sizeof *k_dens; k++) {
        int ww, wh, nw, nh, nww, nwh;
        rt_desktop_density_set(k_dens[k]);
        rt_aspect_native_window(lw, lh, &ww, &wh);
        rt_toggles_defaults(0);
        rt_toggle_parse(RT_TOG_PICTURE, "native");
        rt_aspect_launch_size(&nw, &nh);
        rt_aspect_native_window(nw, nh, &nww, &nwh);
        double dens = RT_ENGINE_OVERRIDE ? k_dens[k] : 1.0;
        bad |= ww != lw || wh != lh || nww != ww || nwh != wh || nw != (int)lround(lw * dens) || nh != (int)lround(lh * dens);
        row("aspect", "native", "density=%.2f window=%dx%d backbuffer=%dx%d native_window=%dx%d native_backbuffer=%dx%d", dens, ww, wh, lw, lh,
            nww, nwh, nw, nh);
    }
    static const int k_usable[][5] = {{2056, 1329, 2056, 1228, 32}, {3440, 1440, 3440, 1415, 32}, {1280, 800, 1280, 720, 28}};
    static const char *const k_fit_as[] = {"fit", "16:9", "21:9"};
    static const char *const k_pics[] = {"off", "native", "metalfx"};
    int sdw, sdh;
    rt_desktop_size(&sdw, &sdh);
    for (size_t u = 0; u < sizeof k_usable / sizeof *k_usable; u++)
        for (size_t k = 0; k < sizeof k_dens / sizeof *k_dens; k++)
            for (size_t s = 0; s < sizeof k_fit_as / sizeof *k_fit_as; s++)
                for (size_t p = 0; p < sizeof k_pics / sizeof *k_pics; p++) {
                    int bw, bh, ww, wh;
                    const int *d = k_usable[u];
                    char got[32];
                    rt_desktop_usable_set(d[0], d[1], d[2], d[3], d[4]);
                    rt_desktop_density_set(k_dens[k]);
                    rt_toggles_defaults(0);
                    rt_toggle_parse(RT_TOG_ASPECT, k_fit_as[s]);
                    rt_toggle_parse(RT_TOG_PICTURE, k_pics[p]);
                    rt_aspect_launch_size(&bw, &bh);
                    rt_aspect_native_window(bw, bh, &ww, &wh);
                    rt_toggle_value(RT_TOG_ASPECT, got, sizeof got);
                    int fits = ww <= d[2] && wh + d[4] <= d[3];
                    int scale_ok = p == 1 ? bw == (int)lround(ww * (RT_ENGINE_OVERRIDE ? k_dens[k] : 1.0)) : bw == ww;
                    int ok = (!RT_ENGINE_OVERRIDE || fits) && scale_ok;
                    bad |= !ok;
                    row("aspect", "usable",
                        "desktop=%dx%d usable=%dx%d title=%d density=%.2f aspect=%s launched=%s picture=%s backbuffer=%dx%d window=%dx%d framed=%dx%d fits=%d ok=%d",
                        d[0], d[1], d[2], d[3], d[4], k_dens[k], k_fit_as[s], got, k_pics[p], bw, bh, ww, wh, ww, wh + d[4], fits, ok);
                }
    static const char *const k_flag_as[] = {"default", "fit", "16:9", "21:9", "3440x1440", "engine"};
    for (size_t u = 0; u < 2; u++)
        for (size_t k = 0; k < sizeof k_dens / sizeof *k_dens; k++)
            for (size_t s = 0; s < sizeof k_flag_as / sizeof *k_flag_as; s++)
                for (size_t p = 0; p < sizeof k_pics / sizeof *k_pics; p++) {
                    int bw, bh, ww, wh;
                    const int *d = k_usable[u];
                    rt_desktop_usable_set(d[0], d[1], d[2], d[3], d[4]);
                    rt_desktop_density_set(k_dens[k]);
                    rt_toggles_defaults(0);
                    if (s) rt_toggle_parse(RT_TOG_ASPECT, k_flag_as[s]);
                    rt_toggle_parse(RT_TOG_PICTURE, k_pics[p]);
                    rt_aspect_launch_size(&bw, &bh);
                    rt_aspect_native_window(bw, bh, &ww, &wh);
                    int native = rt_aspect_launch_native();
                    uint64_t f = rt_gfx_flags_for(RT_GFX_DXVK, native), fn = rt_gfx_flags_for(RT_GFX_NULL, native);
                    int hd = (f & SDL_WINDOW_HIGH_PIXEL_DENSITY) != 0;
                    int want = RT_ENGINE_OVERRIDE && p == 1 && strcmp(k_flag_as[s], "engine") && k_dens[k] > 1.0;
                    int custom = !strcmp(k_flag_as[s], "3440x1440") && RT_ENGINE_OVERRIDE;
                    int shape = labs((long)ww * bh - (long)wh * bw) <= bw;
                    int scaled = custom ? shape : bw == (int)lround(ww * k_dens[k]) && bh == (int)lround(wh * k_dens[k]);
                    int ok = hd == want && (hd ? scaled : custom ? shape : bw == ww && bh == wh) && (f & SDL_WINDOW_VULKAN) && fn == 0;
                    bad |= !ok;
                    row("aspect", "flags",
                        "desktop=%dx%d density=%.2f aspect=%s picture=%s backbuffer=%dx%d window=%dx%d high_pixel_density=%d vulkan=%d null_flags=%llu ok=%d",
                        d[0], d[1], k_dens[k], k_flag_as[s], k_pics[p], bw, bh, ww, wh, hd, (f & SDL_WINDOW_VULKAN) != 0, (unsigned long long)fn, ok);
                }
    static const int k_def43[][5] = {{2056, 1329, 2056, 1228, 32}, {3440, 1440, 3440, 1415, 32}, {1470, 956, 1470, 923, 32}};
    for (size_t u = 0; u < sizeof k_def43 / sizeof *k_def43; u++) {
        const int *d = k_def43[u];
        int bw, bh, ww, wh, cw = d[2], ch = d[3] - d[4];
        char got[32];
        rt_desktop_usable_set(d[0], d[1], d[2], d[3], d[4]);
        rt_desktop_density_set(1.0);
        rt_settings_load(NULL, (int)u & 1);
        rt_aspect_launch_size(&bw, &bh);
        rt_aspect_native_window(bw, bh, &ww, &wh);
        rt_toggle_value(RT_TOG_ASPECT, got, sizeof got);
        double hf = rt_aspect_up_hfov(75.0, (double)bw / bh);
        int ratio = labs((long)ww * 3 - (long)wh * 4) <= 4 && labs((long)bw * 3 - (long)bh * 4) <= 4;
        int fits = ww <= cw && wh <= ch;
        int largest = !RT_ENGINE_OVERRIDE || wh == ch || ww >= cw - 1;
        int ok = !strcmp(got, "4:3") && ratio && fits && largest && fabs(hf - 75.0) <= 0.05;
        bad |= !ok;
        row("aspect", "default43", "desktop=%dx%d client=%dx%d player=%d start=%s window=%dx%d engine=%dx%d hfov75=%.3f ratio=%d fits=%d largest=%d ok=%d",
            d[0], d[1], cw, ch, (int)u & 1, got, ww, wh, bw, bh, hf, ratio, fits, largest, ok);
    }
    rt_desktop_usable_set(sdw, sdh, 0, 0, -1);
    rt_desktop_density_set(0);
    {
        uint32_t target = 0x7A7A7A7Au;
        rt_display_density_set(target, 3.0);
        rt_window_display_set(target);
        double on_target = rt_desktop_density();
        rt_window_display_set(0);
        double on_other = rt_desktop_density();
        rt_display_density_set(0, 0);
        int ok = on_target == 3.0 && on_other != 3.0;
        bad |= !ok;
        row("aspect", "density-display", "target=0x%08X target_density=%.2f other_density=%.2f ok=%d", target, on_target, on_other, ok);
    }
    rt_toggles_defaults(0);
    rt_aspect_launch_size(&lw, &lh);
    static const int em[][4] = {{2056, 1329, 1600, 1200}, {2056, 1156, 1280, 1024}, {3440, 1440, 1600, 1200},
                                {1280, 1024, 1280, 1024}, {1279, 1023, 1024, 768},  {700, 500, 640, 480}};
    for (size_t k = 0; k < sizeof em / sizeof *em; k++) {
        int mw, mh;
        rt_aspect_engine_mode(em[k][0], em[k][1], &mw, &mh);
        bad |= mw != em[k][2] || mh != em[k][3];
        row("aspect", "engine-mode", "window=%dx%d mode=%dx%d", em[k][0], em[k][1], mw, mh);
    }
    rt_toggles_defaults(0);
    rt_toggle_parse(RT_TOG_ASPECT, "engine");
    rt_aspect_launch_size(&lw, &lh);
    bad |= lw != 1280 || lh != 1024;
    row("aspect", "launch", "aspect=engine size=%dx%d", lw, lh);
    row("aspect", "result", "bad=%d", bad);
    return bad ? 1 : 0;
}

static void put_file(const char *dir, const char *name, const char *text)
{
    char p[PATH_MAX];
    if (path_in(p, sizeof p, dir, name)) return;
    FILE *f = fopen(p, "w");
    if (!f) return;
    fputs(text, f);
    fclose(f);
}

static const char *key_of(const rt_pad_tgt *t)
{
    const rt_key *k = t->kind == RT_PADT_KEY ? rt_input_key((uint32_t)t->arg) : NULL;
    return k ? k->name : "none";
}

static int look_px(int speed)
{
    rt_pad p;
    int dx = 0, dy = 0, sum = 0;
    rt_pad_defaults(&p);
    p.look_speed = speed;
    p.rx = 32767;
    for (int i = 0; i < 10; i++) {
        rt_pad_look(&p, 10, &dx, &dy);
        sum += dx;
    }
    return sum;
}

static int case_controller(const char *dir, int player)
{
    (void)player;
    if (rt_pad_test_start()) {
        row("controller", "result", "bad=1");
        return 1;
    }
    int bad = 0;
    static const char *const names[] = {"Xbox Wireless Controller", "DualSense Wireless Controller", "Generic Pad"};
    static const int want[] = {RT_PAD_XBOX, RT_PAD_PLAYSTATION, RT_PAD_XBOX};
    for (int i = 0; i < 3; i++) {
        rt_toggles_defaults(0);
        rt_pad_dev d;
        rt_pad_dev_init(&d, NULL);
        SDL_JoystickID id = rt_pad_test_attach(names[i]);
        SDL_Event ev[RT_PAD_EVENTS];
        int dx, dy;
        rt_pad_poll(&d, 1, (rt_pad_pt){0, 0}, 1000000000u, ev, RT_PAD_EVENTS, &dx, &dy);
        int kind = rt_pad_kind();
        const char *got = d.gp ? SDL_GetGamepadName(d.gp) : NULL;
        row("controller", "type", "name=\"%s\" type=%s gamepad=\"%s\"", names[i], rt_pad_type_name(kind), got ? got : "");
        bad |= !id || kind != want[i];
        rt_pad_test_detach(id);
        rt_pad_poll(&d, 1, (rt_pad_pt){0, 0}, 2000000000u, ev, RT_PAD_EVENTS, &dx, &dy);
        rt_toggles_defaults(0);
        bad |= d.gp != NULL || rt_pad_kind() != RT_PAD_XBOX;
        row("controller", "removed", "pad=\"%s\" type=%s", names[i], rt_pad_type_name(rt_pad_kind()));
    }

    char d1[PATH_MAX], d2[PATH_MAX], d3[PATH_MAX];
    snprintf(d1, sizeof d1, "%s/pad-custom", dir);
    snprintf(d2, sizeof d2, "%s/pad-none", dir);
    snprintf(d3, sizeof d3, "%s/pad-db", dir);
    mkdir(d1, 0755);
    mkdir(d2, 0755);
    mkdir(d3, 0755);
    put_file(d1, "controls.txt", "south=e\nbogus=x\nnorth=notakey\nback=lgui\nlook_speed=600\n");
    char p2[PATH_MAX];
    if (!path_in(p2, sizeof p2, d2, "controls.txt")) unlink(p2);
    put_file(d3, "gamecontrollerdb.txt", "03000000deadbeef0000000000000000,Test Pad,a:b0,b:b1,platform:Mac OS X,\n");
    rt_pad p;
    int nbad = rt_pad_load(&p, d1);
    row("controller", "controls", "south=%s north=%s back=%s bad=%d look_speed=%d px=%d", key_of(&p.map[0]), key_of(&p.map[3]), key_of(&p.map[4]), nbad,
        p.look_speed, look_px(p.look_speed));
    bad |= strcmp(key_of(&p.map[0]), "e") || strcmp(key_of(&p.map[3]), "f") || strcmp(key_of(&p.map[4]), "c") || nbad != 3 || look_px(p.look_speed) != 60;
    nbad = rt_pad_load(&p, d2);
    row("controller", "defaults", "south=%s bad=%d look_speed=%d px=%d", key_of(&p.map[0]), nbad, p.look_speed, look_px(p.look_speed));
    bad |= strcmp(key_of(&p.map[0]), "space") || nbad != 0 || look_px(p.look_speed) != 120;
    int n = rt_pad_mappings(d3);
    row("controller", "mappings", "n=%d", n);
    bad |= n != 1;
    row("controller", "result", "bad=%d", bad);
    return bad ? 1 : 0;
}

int rt_g2b_selftest(const char *name);

static int case_engine_aspect(const char *dir, int player) { return (void)dir, (void)player, rt_g2b_selftest("engine-aspect"); }

static int case_engine_fov(const char *dir, int player) { return (void)dir, (void)player, rt_g2b_selftest("engine-fov"); }

static int case_engine_hud(const char *dir, int player) { return (void)dir, (void)player, rt_g2b_selftest("engine-hud"); }

static int case_engine_load(const char *dir, int player) { return (void)dir, (void)player, rt_g2b_selftest("engine-load"); }
static int case_engine_ui(const char *dir, int player) { return (void)dir, (void)player, rt_g2b_selftest("engine-ui"); }
static int case_engine_rows(const char *dir, int player) { return (void)dir, (void)player, rt_g2b_selftest("engine-rows"); }

static int file_count(const char *dir, const char *text)
{
    char p[PATH_MAX], b[2048];
    if (path_in(p, sizeof p, dir, "settings.txt")) return -1;
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    size_t n = fread(b, 1, sizeof b - 1, f);
    fclose(f);
    b[n] = 0;
    int c = 0;
    for (const char *q = b; (q = strstr(q, text)); q += strlen(text)) c++;
    return c;
}

static int window_settings(const char *dir)
{
    int bad = 0;
    char d[PATH_MAX];
    fresh(dir, "window-first", d, sizeof d);
    rt_settings_load(d, 0);
    rt_settings_save();
    int wl = file_count(d, "window="), fl = file_count(d, "fullscreen=");
    row("window", "settings-first-launch", "window_lines=%d fullscreen_lines=%d toggle_lines=%d", wl, fl, file_count(d, "overlays="));
    bad |= wl || fl;

    fresh(dir, "window-round", d, sizeof d);
    write_file(d, "window=10,20,900,500\nfullscreen=on\n");
    unsigned w0 = rt_settings_warnings();
    rt_settings_load(d, 0);
    rt_settings_save();
    int a = file_has(d, "window=10,20,900,500\n"), b = file_has(d, "fullscreen=on\n");
    rt_settings_load(d, 0);
    rt_settings_save();
    row("window", "settings-roundtrip", "window=%d fullscreen=%d window_lines=%d fullscreen_lines=%d toggle_lines=%d warnings=%u", a, b,
        file_count(d, "window="), file_count(d, "fullscreen="), file_count(d, "overlays="), rt_settings_warnings() - w0);
    bad |= !a || !b;

    fresh(dir, "window-off", d, sizeof d);
    write_file(d, "window=-5,-6,700,600\nfullscreen=off\n");
    rt_settings_load(d, 0);
    rt_settings_save();
    row("window", "settings-negative-off", "window=%d fullscreen=%d", file_has(d, "window=-5,-6,700,600\n"), file_has(d, "fullscreen=off\n"));

    fresh(dir, "window-only-fs", d, sizeof d);
    write_file(d, "fullscreen=on\n");
    rt_settings_load(d, 0);
    rt_settings_save();
    row("window", "settings-fullscreen-only", "window_lines=%d fullscreen=%d", file_count(d, "window="), file_has(d, "fullscreen=on\n"));

    static const char *const k_bad[] = {"window=0,0,0,0\n", "window=1,2,3\n", "window=1,2,3,4,5\n", "window=a,b,c,d\n", "window=1,2,3,4x\n",
                                        "fullscreen=maybe\n", "window=1,2,100000,4\n", "window=400000,0,900,500\n", "window=0,-400000,900,500\n"};
    for (size_t k = 0; k < sizeof k_bad / sizeof *k_bad; k++) {
        char name[32];
        snprintf(name, sizeof name, "window-bad-%zu", k);
        fresh(dir, name, d, sizeof d);
        write_file(d, k_bad[k]);
        w0 = rt_settings_warnings();
        rt_settings_load(d, 0);
        rt_settings_save();
        snprintf(name, sizeof name, "settings-bad-%zu", k);
        row("window", name, "warnings=%u window_lines=%d fullscreen_lines=%d", rt_settings_warnings() - w0, file_count(d, "window="),
            file_count(d, "fullscreen="));
    }
    fresh(dir, "window-reset", d, sizeof d);
    rt_settings_load(d, 0);
    return bad;
}

static int window_live(void)
{
    int bad = 0;
    if (rt_window_init()) {
        row("window", "flags", "available=0");
        return 1;
    }
    static const int k_start[][2] = {{1280, 1024}, {320, 240}};
    for (size_t k = 0; k < sizeof k_start / sizeof *k_start; k++) {
        SDL_Window *sw = SDL_CreateWindow("m9", k_start[k][0], k_start[k][1], SDL_WINDOW_HIDDEN);
        if (!sw) {
            row("window", "flags", "available=0");
            return 1;
        }
        rt_window_configure(sw, k_start[k][0], k_start[k][1]);
        int mw = 0, mh = 0;
        SDL_GetWindowMinimumSize(sw, &mw, &mh);
        unsigned long cb = rt_window_collection_behavior(sw);
        const char *hint = SDL_GetHint(SDL_HINT_VIDEO_MAC_FULLSCREEN_SPACES);
        row("window", "flags", "available=1 start=%dx%d resizable=%d min=%dx%d fullscreen_primary=%d fullscreen_none=%d spaces=%s", k_start[k][0],
            k_start[k][1], (SDL_GetWindowFlags(sw) & SDL_WINDOW_RESIZABLE) != 0, mw, mh, (int)(cb >> 7 & 1ul), (int)(cb >> 9 & 1ul),
            hint ? hint : "unset");
        bad |= !(SDL_GetWindowFlags(sw) & SDL_WINDOW_RESIZABLE) || !(cb >> 7 & 1ul);
        SDL_DestroyWindow(sw);
    }
    unsigned miss = rt_devmenu_window_items();
    for (int i = 0; i < RT_MENU_ITEMS; i++) {
        int has = rt_devmenu_has_action(rt_menu_items[i].action);
        row("window", "menu", "action=%s present=%d", rt_menu_items[i].action, has);
        bad |= !has;
    }
    row("window", "menu-again", "added_first=%u missing=%u", miss, rt_devmenu_window_items());
    return bad;
}

static int case_window(const char *dir, int player)
{
    (void)player;
    int bad = rt_ws_rows();
    bad |= window_settings(dir);
    bad |= window_live();
    row("window", "result", "bad=%d", bad);
    return bad ? 1 : 0;
}

static const m9_case k_cases[] = {
    {"engine-aspect", case_engine_aspect}, {"engine-fov", case_engine_fov}, {"engine-hud", case_engine_hud}, {"engine-load", case_engine_load}, {"engine-ui", case_engine_ui}, {"engine-rows", case_engine_rows},
    {"menu", case_menu}, {"input", case_input}, {"fps", case_fps}, {"overlay", case_overlay}, {"settings", case_settings},
    {"aspect", case_aspect}, {"controller", case_controller}, {"fx", case_fx}, {"live", case_live}, {"fx-spatial", case_fx_spatial},
    {"window", case_window},
};

int rt_m9_selftest(int argc, char **argv)
{
    const char *c = NULL, *overlay = NULL;
    int player = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--m9-selftest") && i + 1 < argc) c = argv[++i];
        else if (!strcmp(argv[i], "--player")) player = 1;
        else if (!strcmp(argv[i], "--overlay") && i + 1 < argc) overlay = argv[++i];
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc) s_frames = atoi(argv[++i]);
    }
    s_overlay = overlay;
    if (!c || !overlay) {
        fprintf(stderr, "usage: --m9-selftest <case> [--player] --overlay DIR\n");
        return 2;
    }
    char dir[PATH_MAX];
    snprintf(dir, sizeof dir, "%s/m9", overlay);
    mkdir(overlay, 0755);
    if (mkdir(dir, 0755) && errno != EEXIST) {
        fprintf(stderr, "m9 selftest: cannot create %s\n", dir);
        return 2;
    }
    for (size_t i = 0; i < sizeof k_cases / sizeof *k_cases; i++)
        if (!strcmp(c, k_cases[i].name)) return k_cases[i].run(dir, player);
    fprintf(stderr, "m9 selftest: unknown case %s\n", c);
    return 2;
}
