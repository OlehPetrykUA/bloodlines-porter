#ifndef RT_TOGGLES_H
#define RT_TOGGLES_H

#include "aspect.h"

#include <stddef.h>
#include <stdint.h>

enum {
    RT_TOG_OVERLAYS,
    RT_TOG_PICTURE,
    RT_TOG_ASPECT,
    RT_TOG_COUNT
};

enum { RT_SRC_CHORD = 1, RT_SRC_MENU, RT_SRC_SETTINGS };

enum { RT_OVL_ALL, RT_OVL_FPS, RT_OVL_OFF };

enum { RT_HUD_ALL, RT_HUD_FPS, RT_HUD_OFF };

typedef struct {
    int fps;
    double p50, p99;
    uint32_t created, failed;
    double load;
    const char *overlays, *picture;
} rt_hud_info;

int rt_toggle_get(int id);
const char *rt_toggle_name(int id);
const char *rt_toggle_title(int id);
void rt_toggle_request(int id, int source);
void rt_toggles_apply(void);
void rt_toggle_set_available(int id, uint32_t steps, const char *reason);
uint32_t rt_toggle_available(int id, const char **reason);
int rt_toggle_chord(uint32_t scancode);
int rt_toggle_key(int id);
int rt_toggle_parse(int id, const char *value);
void rt_aspect_launch_set(int w, int h);
void rt_aspect_launch_size(int *w, int *h);
const char *rt_toggle_value(int id, char *buf, size_t n);
const char *rt_toggle_label(int id, char *buf, size_t n);
const char *rt_dxvk_config(const char *base, const char *user, char *out, size_t n);
void rt_toggles_defaults(int player);
unsigned rt_toggle_log_count(void);
const char *rt_toggle_last(void);

int rt_settings_load(const char *dir, int player);
int rt_settings_save(void);
unsigned rt_settings_warnings(void);
const char *rt_settings_dir(void);

void rt_fps_present(uint64_t now_ns, int made);
void rt_fps_reset(void);
int rt_fps_text(char *b, size_t n);
void rt_fps_stats(double *fps, double *p50, double *p99);

void rt_gfx_shader_counts(uint32_t out[6]);

void rt_hud_init(void);
void rt_hud_menu_add(void *menu);
int rt_hud_dump_menu(const char *tsv);
int rt_hud_live_lines(void);
int rt_hud_render(int state, const rt_hud_info *info, const char *png, const char *tsv);

int rt_m9_selftest(int argc, char **argv);

#endif
