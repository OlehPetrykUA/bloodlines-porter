#ifndef RT_WINSTATE_SELFTEST_H
#define RT_WINSTATE_SELFTEST_H

#include "aspect.h"
#include "winstate.h"

#include <stdio.h>
#include <stdlib.h>

static inline void rt_wsr_rect(const char *name, const rt_wrect *r) { printf("%s=%d,%d,%d,%d", name, r->x, r->y, r->w, r->h); }

static inline int rt_ws_rows(void)
{
    int bad = 0;
    static const int k_launch[][2] = {{2056, 1329}, {3440, 1440}, {1470, 956}};
    static const int k_wins[][2] = {{2056, 1329}, {3440, 1440}, {1470, 956}, {900, 500}};
    static const char *const k_as[] = {"4:3", "fit", "16:9", "21:9"};
    for (size_t l = 0; l < sizeof k_launch / sizeof *k_launch; l++)
        for (size_t a = 0; a < sizeof k_as / sizeof *k_as; a++)
            for (size_t k = 0; k < sizeof k_wins / sizeof *k_wins; k++) {
                rt_aspect as;
                int bw, bh, r[4];
                rt_aspect_parse(k_as[a], &as);
                rt_aspect_size(&as, k_launch[l][0], k_launch[l][1], &bw, &bh);
                rt_aspect_letterbox(bw, bh, k_wins[k][0], k_wins[k][1], r);
                long skew = labs((long)r[2] * bh - (long)r[3] * bw);
                bad |= skew > (bw > bh ? bw : bh) || (r[2] != k_wins[k][0] && r[3] != k_wins[k][1]);
                printf("m9 window rect launch=%dx%d aspect=%s bb=%dx%d win=%dx%d dx=%d dy=%d dw=%d dh=%d\n", k_launch[l][0], k_launch[l][1],
                       k_as[a], bw, bh, k_wins[k][0], k_wins[k][1], r[0], r[1], r[2], r[3]);
            }

    rt_winstate s;
    rt_wrect a = {100, 100, 1280, 1024}, b = {120, 80, 900, 500}, c = {40, 60, 1000, 700};
    rt_ws_clear(&s);
    rt_ws_note(&s, 100, b);
    printf("m9 window state-first-launch have=%d dirty=%d\n", s.have, s.dirty);
    bad |= s.have || s.dirty;

    rt_ws_clear(&s);
    rt_ws_live(&s, 0, a);
    rt_ws_note(&s, 1000, b);
    printf("m9 window state-drag have=%d due_early=%d due_late=%d ", s.have, rt_ws_due(&s, 1100), rt_ws_due(&s, 1500));
    rt_wsr_rect("rect", &s.rect);
    printf("\n");

    rt_ws_clear(&s);
    rt_ws_live(&s, 0, a);
    rt_ws_note(&s, 10000, b);
    rt_ws_note(&s, 20100, (rt_wrect){0, 0, 1600, 900});
    rt_ws_note(&s, 20500, (rt_wrect){0, 0, 2056, 1329});
    rt_ws_enter_fs(&s, 21000);
    rt_ws_note(&s, 22000, (rt_wrect){0, 0, 2056, 1329});
    printf("m9 window state-enter fullscreen=%d ", s.fullscreen);
    rt_wsr_rect("rect", &s.rect);
    printf("\n");
    rt_ws_leave_fs(&s, 30000, c);
    printf("m9 window state-leave fullscreen=%d ", s.fullscreen);
    rt_wsr_rect("rect", &s.rect);
    printf("\n");

    rt_ws_clear(&s);
    rt_ws_live(&s, 0xFFFFF000u, a);
    rt_ws_note(&s, 0xFFFFF100u, b);
    rt_ws_enter_fs(&s, 0x400u);
    printf("m9 window state-wrap fullscreen=%d ", s.fullscreen);
    rt_wsr_rect("rect", &s.rect);
    printf("\n");

    rt_ws_clear(&s);
    rt_ws_parse(&s, "window", "200,150,1100,800");
    rt_ws_parse(&s, "fullscreen", "on");
    rt_ws_live(&s, 0, (rt_wrect){200, 150, 1100, 800});
    rt_ws_note(&s, 100, (rt_wrect){0, 0, 1500, 900});
    rt_ws_note(&s, 600, (rt_wrect){0, 0, 2056, 1329});
    rt_ws_enter_fs(&s, 1000);
    printf("m9 window state-startup-fs fullscreen=%d ", s.fullscreen);
    rt_wsr_rect("rect", &s.rect);
    printf("\n");

    rt_ws_clear(&s);
    rt_ws_enter_fs(&s, 500);
    {
        char buf[64] = "";
        FILE *f = tmpfile();
        if (f) {
            rt_ws_write(&s, f);
            rewind(f);
            size_t n = fread(buf, 1, sizeof buf - 1, f);
            buf[n] = 0;
            fclose(f);
        }
        int written = strstr(buf, "fullscreen=on\n") != NULL;
        printf("m9 window state-prelive-fs fullscreen=%d dirty=%d written=%d\n", s.fullscreen, s.dirty, written);
        bad |= !s.dirty || !written;
    }
    s.dirty = 0;
    rt_ws_leave_fs(&s, 900, c);
    printf("m9 window state-prelive-leave fullscreen=%d dirty=%d\n", s.fullscreen, s.dirty);
    bad |= !s.dirty || s.fullscreen;

    static const rt_wrect d1[] = {{0, 32, 2056, 1228}};
    static const rt_wrect d2[] = {{0, 32, 2056, 1228}, {2056, 0, 3440, 1415}};
    static const struct {
        const char *name;
        rt_wrect saved;
        const rt_wrect *d;
        int nd;
    } rc[] = {
        {"inside", {100, 100, 1280, 1024}, d1, 1},      {"offscreen", {5000, 100, 900, 500}, d1, 1},
        {"second-display", {2500, 100, 1000, 700}, d2, 2}, {"unplugged-second", {2500, 100, 1000, 700}, d1, 1},
        {"too-big", {100, 100, 3000, 2000}, d1, 1},     {"too-small", {100, 100, 300, 200}, d1, 1},
        {"overhang", {1400, 200, 900, 500}, d1, 1},     {"no-displays", {10, 20, 300, 200}, NULL, 0},
    };
    for (size_t k = 0; k < sizeof rc / sizeof *rc; k++) {
        int placed;
        rt_wrect o = rt_ws_restore(&rc[k].saved, rc[k].d, rc[k].nd, &placed);
        printf("m9 window restore case=%s ", rc[k].name);
        rt_wsr_rect("out", &o);
        printf(" placed=%d\n", placed);
    }

    static const char *const k_min[][2] = {{"1280", "1024"}, {"320", "240"}, {"0", "0"}, {"800", "300"}};
    for (size_t k = 0; k < sizeof k_min / sizeof *k_min; k++) {
        int mw, mh;
        rt_win_minimum(atoi(k_min[k][0]), atoi(k_min[k][1]), &mw, &mh);
        printf("m9 window minimum start=%sx%s min=%dx%d\n", k_min[k][0], k_min[k][1], mw, mh);
    }
    printf("m9 window collection from=0x%lX to=0x%lX\n", 0x301ul, rt_win_collection(0x301ul));
    return bad;
}

#endif
