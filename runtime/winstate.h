#ifndef RT_WINSTATE_H
#define RT_WINSTATE_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define RT_WIN_MIN_W 640
#define RT_WIN_MIN_H 480
#define RT_WIN_HIST 256
#define RT_WIN_SETTLE_MS 1500u
#define RT_WIN_SAVE_MS 400u
#define RT_WIN_DIM_MAX 99999
#define RT_WIN_POS_MAX (RT_WIN_DIM_MAX * 4)
#define RT_WIN_FULLSCREEN_PRIMARY (1ul << 7)
#define RT_WIN_FULLSCREEN_OTHER ((1ul << 8) | (1ul << 9))

typedef struct {
    int x, y, w, h;
} rt_wrect;

typedef struct {
    int have, fullscreen, in_fs, live, dirty, n;
    rt_wrect rect;
    uint32_t dirty_ms;
    uint32_t t[RT_WIN_HIST];
    rt_wrect r[RT_WIN_HIST];
} rt_winstate;

typedef struct {
    const char *action, *title, *key;
    unsigned long mask;
    int app;
} rt_menu_item;

#define RT_MENU_CTRL (1ul << 18)
#define RT_MENU_CMD (1ul << 20)
#define RT_MENU_ITEMS 5

static const rt_menu_item rt_menu_items[RT_MENU_ITEMS] = {
    {"hide:", "Hide", "h", RT_MENU_CMD, 1},
    {"terminate:", "Quit", "q", RT_MENU_CMD, 1},
    {"performMiniaturize:", "Minimize", "m", RT_MENU_CMD, 0},
    {"performZoom:", "Zoom", "", 0, 0},
    {"toggleFullScreen:", "Enter Full Screen", "f", RT_MENU_CTRL | RT_MENU_CMD, 0},
};

static inline unsigned rt_menu_missing(const char *const *have, int n)
{
    unsigned miss = 0;
    for (int i = 0; i < RT_MENU_ITEMS; i++) {
        int found = 0;
        for (int k = 0; k < n; k++)
            if (have[k] && !strcmp(have[k], rt_menu_items[i].action)) found = 1;
        if (!found) miss |= 1u << i;
    }
    return miss;
}

static inline unsigned long rt_win_collection(unsigned long cur)
{
    return (cur & ~RT_WIN_FULLSCREEN_OTHER) | RT_WIN_FULLSCREEN_PRIMARY;
}

static inline void rt_win_minimum(int cw, int ch, int *mw, int *mh)
{
    *mw = cw > 0 && cw < RT_WIN_MIN_W ? cw : RT_WIN_MIN_W;
    *mh = ch > 0 && ch < RT_WIN_MIN_H ? ch : RT_WIN_MIN_H;
}

static inline void rt_ws_clear(rt_winstate *s) { memset(s, 0, sizeof *s); }

static inline int rt_ws_parse(rt_winstate *s, const char *name, const char *v)
{
    if (!strcmp(name, "window")) {
        int x, y, w, h, used = 0;
        if (sscanf(v, "%d,%d,%d,%d%n", &x, &y, &w, &h, &used) != 4 || v[used] || w <= 0 || h <= 0 || w > RT_WIN_DIM_MAX || h > RT_WIN_DIM_MAX ||
            x < -RT_WIN_POS_MAX || x > RT_WIN_POS_MAX || y < -RT_WIN_POS_MAX || y > RT_WIN_POS_MAX)
            return -1;
        s->have = 1;
        s->rect = (rt_wrect){x, y, w, h};
        return 0;
    }
    if (!strcmp(name, "fullscreen")) {
        if (!strcmp(v, "on")) s->fullscreen = 1;
        else if (!strcmp(v, "off")) s->fullscreen = 0;
        else return -1;
        return 0;
    }
    return 1;
}

static inline void rt_ws_write(const rt_winstate *s, FILE *f)
{
    if (s->have) fprintf(f, "window=%d,%d,%d,%d\n", s->rect.x, s->rect.y, s->rect.w, s->rect.h);
    if (s->have || s->fullscreen) fprintf(f, "fullscreen=%s\n", s->fullscreen ? "on" : "off");
}

static inline void rt_ws_live(rt_winstate *s, uint32_t t, rt_wrect cur)
{
    s->live = 1;
    s->n = 0;
    if (s->in_fs) return;
    s->t[0] = t;
    s->r[0] = cur;
    s->n = 1;
}

static inline void rt_ws_mark(rt_winstate *s, uint32_t t)
{
    s->dirty = 1;
    s->dirty_ms = t;
}

static inline void rt_ws_note(rt_winstate *s, uint32_t t, rt_wrect r)
{
    if (!s->live || s->in_fs) return;
    if (s->n && !memcmp(&s->r[s->n - 1], &r, sizeof r)) return;
    if (s->n == RT_WIN_HIST) {
        memmove(s->t, s->t + 1, (RT_WIN_HIST - 1) * sizeof *s->t);
        memmove(s->r, s->r + 1, (RT_WIN_HIST - 1) * sizeof *s->r);
        s->n--;
    }
    s->t[s->n] = t;
    s->r[s->n++] = r;
    s->rect = r;
    s->have = 1;
    rt_ws_mark(s, t);
}

static inline void rt_ws_enter_fs(rt_winstate *s, uint32_t t)
{
    if (s->live && !s->in_fs && s->n) {
        int k = 0;
        for (int i = 0; i < s->n; i++)
            if (t - s->t[i] >= RT_WIN_SETTLE_MS) k = i;
        s->rect = s->r[k];
        s->have = 1;
    }
    s->in_fs = 1;
    s->fullscreen = 1;
    rt_ws_mark(s, t);
}

static inline void rt_ws_leave_fs(rt_winstate *s, uint32_t t, rt_wrect cur)
{
    s->in_fs = 0;
    s->fullscreen = 0;
    rt_ws_mark(s, t);
    if (!s->live) return;
    s->t[0] = t;
    s->r[0] = cur;
    s->n = 1;
    s->rect = cur;
    s->have = 1;
}

static inline int rt_ws_due(const rt_winstate *s, uint32_t t) { return s->dirty && t - s->dirty_ms >= RT_WIN_SAVE_MS; }

static inline int rt_ws_inside(const rt_wrect *d, int x, int y) { return x >= d->x && y >= d->y && x < d->x + d->w && y < d->y + d->h; }

static inline int rt_ws_clamp(int v, int lo, int hi) { return hi < lo ? lo : v < lo ? lo : v > hi ? hi : v; }

static inline rt_wrect rt_ws_restore(const rt_wrect *saved, const rt_wrect *disp, int nd, int *placed)
{
    rt_wrect o = *saved;
    *placed = 0;
    if (o.w < RT_WIN_MIN_W) o.w = RT_WIN_MIN_W;
    if (o.h < RT_WIN_MIN_H) o.h = RT_WIN_MIN_H;
    if (nd <= 0) return o;
    const rt_wrect *d = &disp[0];
    for (int i = 0; i < nd; i++)
        if (rt_ws_inside(&disp[i], o.x + o.w / 2, o.y + o.h / 2)) {
            d = &disp[i];
            *placed = 1;
            break;
        }
    if (o.w > d->w) o.w = d->w;
    if (o.h > d->h) o.h = d->h;
    if (*placed) {
        o.x = rt_ws_clamp(o.x, d->x, d->x + d->w - o.w);
        o.y = rt_ws_clamp(o.y, d->y, d->y + d->h - o.h);
    } else {
        o.x = d->x + (d->w - o.w) / 2;
        o.y = d->y + (d->h - o.h) / 2;
    }
    return o;
}

void rt_window_settings_clear(void);
void rt_window_forget_saved(void);
int rt_window_settings_parse(const char *name, const char *value);
void rt_window_settings_write(FILE *f);
void rt_window_configure(void *sdl_window, int cw, int ch);
unsigned long rt_window_collection_behavior(void *sdl_window);
unsigned rt_devmenu_window_items(void);
int rt_devmenu_has_action(const char *action);

#endif
