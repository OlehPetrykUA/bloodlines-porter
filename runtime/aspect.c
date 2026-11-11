#include "aspect.h"

#include <math.h>
#include <string.h>
#include <strings.h>

#define FIT_SKIP 0.01
#define DIM_MAX 99999
#define DEG (3.14159265358979323846 / 180.0)

static const char *const k_steps[RT_ASPECT_STEPS] = {"16:9", "fit", "21:9", "engine"};

static int number(const char **p, int *out)
{
    long v = 0;
    const char *s = *p;
    if (*s < '0' || *s > '9') return 0;
    while (*s >= '0' && *s <= '9') {
        v = v * 10 + (*s++ - '0');
        if (v > DIM_MAX) return 0;
    }
    *p = s;
    *out = (int)v;
    return 1;
}

int rt_aspect_parse(const char *s, rt_aspect *a)
{
    int x, y;
    const char *p = s;
    memset(a, 0, sizeof *a);
    a->kind = -1;
    if (!s || !*s) return 0;
    if (!strcasecmp(s, "fit")) {
        a->kind = RT_ASPECT_FIT;
        return 1;
    }
    if (!strcasecmp(s, "engine")) {
        a->kind = RT_ASPECT_ENGINE;
        return 1;
    }
    if (!number(&p, &x) || x <= 0) return 0;
    if (*p == ':') {
        p++;
        if (!number(&p, &y) || y <= 0 || *p) return 0;
        if (x == 21 && y == 9) {
            x = 43;
            y = 18;
        }
        a->kind = RT_ASPECT_RATIO;
        a->num = x;
        a->den = y;
        return 1;
    }
    if (*p == 'x' || *p == 'X') {
        p++;
        if (!number(&p, &y) || y <= 0 || *p) return 0;
        a->kind = RT_ASPECT_SIZE;
        a->w = x;
        a->h = y;
        return 1;
    }
    return 0;
}

void rt_aspect_from_step(int step, rt_aspect *a)
{
    memset(a, 0, sizeof *a);
    switch (step) {
    case RT_ASPECT_STEP_169:
        a->kind = RT_ASPECT_RATIO;
        a->num = 16;
        a->den = 9;
        break;
    case RT_ASPECT_STEP_219:
        a->kind = RT_ASPECT_RATIO;
        a->num = 43;
        a->den = 18;
        break;
    case RT_ASPECT_STEP_FIT:
        a->kind = RT_ASPECT_FIT;
        break;
    default:
        a->kind = RT_ASPECT_ENGINE;
        break;
    }
}

const char *rt_aspect_step_name(int step)
{
    return step >= 0 && step < RT_ASPECT_STEPS ? k_steps[step] : "engine";
}

int rt_aspect_fit_skipped(double ratio)
{
    return fabs(ratio / (16.0 / 9.0) - 1.0) <= FIT_SKIP || fabs(ratio / (43.0 / 18.0) - 1.0) <= FIT_SKIP;
}

int rt_aspect_next(int step, double window_ratio)
{
    int n = step < RT_ASPECT_STEP_169 || step >= RT_ASPECT_STEP_219 ? RT_ASPECT_STEP_169 : step + 1;
    if (n == RT_ASPECT_STEP_FIT && rt_aspect_fit_skipped(window_ratio)) n = RT_ASPECT_STEP_219;
    return n;
}

void rt_aspect_size(const rt_aspect *a, int win_w, int win_h, int *bw, int *bh)
{
    int w = win_w, h = win_h;
    if (a->kind == RT_ASPECT_ENGINE) {
        *bw = RT_ASPECT_ENGINE_W;
        *bh = RT_ASPECT_ENGINE_H;
        return;
    }
    if (a->kind == RT_ASPECT_SIZE) {
        w = a->w;
        h = a->h;
    } else if (a->kind == RT_ASPECT_RATIO && w > 0 && h > 0) {
        if ((long)w * a->den >= (long)h * a->num)
            w = (int)((long)h * a->num / a->den);
        else
            h = (int)((long)w * a->den / a->num);
    }
    if (w < RT_ASPECT_MIN_W || h < RT_ASPECT_MIN_H || (w == RT_ASPECT_MIN_W && h == RT_ASPECT_MIN_H)) {
        w = RT_ASPECT_FLOOR_W;
        h = RT_ASPECT_FLOOR_H;
    }
    *bw = w;
    *bh = h;
}

static const int k_engine_modes[][2] = {{640, 480}, {800, 600}, {1024, 768}, {1280, 1024}, {1600, 1200}};

void rt_aspect_engine_mode(int win_w, int win_h, int *w, int *h)
{
    int best = 0;
    for (size_t i = 1; i < sizeof k_engine_modes / sizeof *k_engine_modes; i++)
        if (k_engine_modes[i][0] <= win_w && k_engine_modes[i][1] <= win_h) best = (int)i;
    *w = k_engine_modes[best][0];
    *h = k_engine_modes[best][1];
}

void rt_aspect_letterbox(int bw, int bh, int dw, int dh, int r[4])
{
    int w = dw, h = dh;
    if (bw > 0 && bh > 0 && dw > 0 && dh > 0) {
        if ((long)dw * bh <= (long)dh * bw)
            h = (int)(((long)bh * dw + bw / 2) / bw);
        else
            w = (int)(((long)bw * dh + bh / 2) / bh);
    }
    if (w > dw) w = dw;
    if (h > dh) h = dh;
    r[0] = (dw - w) / 2;
    r[1] = (dh - h) / 2;
    r[2] = w;
    r[3] = h;
}

double rt_aspect_hfov(double vfov_deg, double w, double h)
{
    return 2.0 * atan(tan(vfov_deg * DEG / 2.0) * w / h) / DEG;
}

double rt_aspect_fovy(double fovx_deg, double aspect)
{
    return 2.0 * atan(tan(fovx_deg * DEG / 2.0) / aspect) / DEG;
}

double rt_aspect_up_hfov(double fov_deg, double ratio)
{
    return 2.0 * atan(tan(fov_deg * DEG / 2.0) * 0.75 * ratio) / DEG;
}
