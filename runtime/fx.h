#ifndef RT_FX_H
#define RT_FX_H

#include <stdint.h>

enum { RT_PIC_METALFX, RT_PIC_NATIVE, RT_PIC_OFF };

#ifndef RT_ENGINE_OVERRIDE
extern const int rt_engine_override;
#define RT_ENGINE_OVERRIDE rt_engine_override
#endif

double rt_desktop_density(void);
void rt_desktop_density_set(double d);
void rt_aspect_native_window(int bw, int bh, int *ww, int *wh);

int rt_fx_probe(void);
void rt_fx_support(int *spatial, int *temporal, int *interp, int *m4);
int rt_fx_attach(void *sdl_window);
int rt_gfx_layer_fit(void *sdl_window);
void rt_gfx_layer_counts(void *sdl_window, int *layers, int *unfit);
void rt_gfx_pace_reset(void);
void rt_gfx_pace_report(void);
void rt_gfx_luma_report(void);
void rt_gfx_present_trace_dump(void);
void rt_gfx_link_report(void);
int rt_gfx_link_paced(void);
int rt_gfx_vsync(void *sdl_window);
void rt_gfx_window_gone(void);
void rt_gfx_vsync_set(int on);
void rt_gfx_cfg_counts(uint64_t *n, uint64_t *linked);
uint64_t rt_gfx_link_starved(void);
int rt_gfx_link_layer(void);
int rt_fx_frame(int pw, int ph, const int r[4]);
int rt_fx_wanted(void);
int rt_fx_mode_on(void);
void rt_fx_drain(void);
void rt_fx_report(void);
int rt_fx_upscale_sync(const void *bgra, uint32_t w, uint32_t h, uint32_t pitch, uint32_t ow, uint32_t oh, void *out,
                       double *gpu_ms);

int rt_gfx_fx_selftest(const char *overlay, int frames);

#endif
