#ifndef RT_ASPECT_H
#define RT_ASPECT_H

enum { RT_ASPECT_RATIO, RT_ASPECT_FIT, RT_ASPECT_ENGINE, RT_ASPECT_SIZE };

enum { RT_ASPECT_STEP_169, RT_ASPECT_STEP_FIT, RT_ASPECT_STEP_219, RT_ASPECT_STEP_ENGINE, RT_ASPECT_STEPS };

#define RT_ASPECT_ENGINE_W 1280
#define RT_ASPECT_ENGINE_H 1024
#define RT_ASPECT_MIN_W 640
#define RT_ASPECT_MIN_H 480
#define RT_ASPECT_FLOOR_W 800
#define RT_ASPECT_FLOOR_H 600

typedef struct {
    int kind;
    int num;
    int den;
    int w;
    int h;
} rt_aspect;

int rt_aspect_parse(const char *s, rt_aspect *a);
void rt_aspect_from_step(int step, rt_aspect *a);
const char *rt_aspect_step_name(int step);
int rt_aspect_fit_skipped(double ratio);
int rt_aspect_next(int step, double window_ratio);
void rt_aspect_size(const rt_aspect *a, int win_w, int win_h, int *bw, int *bh);
void rt_aspect_engine_mode(int win_w, int win_h, int *w, int *h);
void rt_aspect_letterbox(int bw, int bh, int dw, int dh, int r[4]);
double rt_aspect_hfov(double vfov_deg, double w, double h);
double rt_aspect_fovy(double fovx_deg, double aspect);
double rt_aspect_up_hfov(double fov_deg, double ratio);

#endif
