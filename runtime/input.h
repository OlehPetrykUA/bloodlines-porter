#ifndef RT_INPUT_H
#define RT_INPUT_H

#include "pad.h"
#include "rt.h"

#include <SDL3/SDL.h>
#include <stdint.h>

#define WM_ACTIVATE 0x0006
#define WM_ACTIVATEAPP 0x001C
#define WM_KEYDOWN 0x0100
#define WM_KEYUP 0x0101
#define WM_CHAR 0x0102
#define WM_SYSKEYDOWN 0x0104
#define WM_SYSKEYUP 0x0105
#define WM_MOUSEMOVE 0x0200
#define WM_LBUTTONDOWN 0x0201
#define WM_LBUTTONUP 0x0202
#define WM_RBUTTONDOWN 0x0204
#define WM_RBUTTONUP 0x0205
#define WM_MBUTTONDOWN 0x0207
#define WM_MBUTTONUP 0x0208
#define WM_MOUSEWHEEL 0x020A
#define WM_XBUTTONDOWN 0x020B
#define WM_XBUTTONUP 0x020C

#define MK_LBUTTON 0x0001u
#define MK_RBUTTON 0x0002u
#define MK_SHIFT 0x0004u
#define MK_CONTROL 0x0008u
#define MK_MBUTTON 0x0010u
#define MK_XBUTTON1 0x0020u
#define MK_XBUTTON2 0x0040u
#define WHEEL_DELTA 120

#define RT_KEYIDX 512

typedef struct {
    uint16_t sdl;
    uint8_t scan, ext, vk;
    const char *name;
} rt_key;

typedef struct {
    uint32_t msg, wp, lp;
} rt_wmsg;

typedef struct {
    int32_t x, y;
    int32_t read_x, read_y;
    uint32_t buttons;
    uint8_t held[RT_KEYIDX];
    uint8_t taken[RT_KEYIDX];
    uint8_t unmapped[RT_KEYIDX];
    int gui, focus, host_focus, relative;
    double fx, fy;
    uint32_t rel_switches, recentres, looks;
    uint32_t capture, last_lp;
    uint32_t cursor;
    int show_count;
    int applied;
    int move_pending, warp_pending;
    int32_t warp_x, warp_y;
    int client_w, client_h, origin_x, origin_y;
    uint32_t moves, nbuttons, keys, chars, dropped, chords, warps;
} rt_input_state;

enum { RT_CUR_UNSET = -2, RT_CUR_HIDDEN = -1 };

enum { OP_WAIT, OP_SLEEP, OP_MOVE, OP_DOWN, OP_UP, OP_CLICK, OP_WHEEL, OP_KEY, OP_KEYDOWN, OP_KEYUP, OP_TEXT, OP_CHORD,
       OP_FOCUS, OP_CAPTURE, OP_LOOK, OP_MINIMISE, OP_WAITMAP, OP_WAITBINK };

typedef struct {
    uint32_t line;
    uint8_t op;
    int32_t a, b;
    char text[64];
    char src[96];
} rt_script_cmd;

typedef struct {
    uint32_t window_id;
    int32_t x, y;
    uint16_t mod;
    rt_present_rects rects;
    float density;
} rt_script_ctx;

extern const rt_key rt_keys[];
extern const uint32_t rt_nkeys;

void rt_input_init(rt_input_state *s);
const rt_key *rt_input_key(uint32_t scancode);
const rt_key *rt_input_key_named(const char *name);
void rt_input_map(const rt_present_rects *r, float density, float x, float y, int32_t *cx, int32_t *cy);
void rt_input_unmap(const rt_present_rects *r, float density, int32_t cx, int32_t cy, float *x, float *y);
int rt_input_translate(rt_input_state *s, const SDL_Event *e, const rt_present_rects *r, float density, rt_wmsg *out,
                       int cap);
int rt_input_pad_apply(rt_input_state *s, const SDL_Event *ev, int k, int dx, int dy, const rt_present_rects *r, float density,
                       rt_wmsg *out, int cap);
int rt_input_pad_pump(rt_input_state *s, rt_pad_dev *d, int active, uint64_t now_ns, const rt_present_rects *r, float density,
                      rt_wmsg *out, int cap);
int rt_input_pad_lost(rt_input_state *s, rt_pad_dev *d, const rt_present_rects *r, float density, rt_wmsg *out, int cap);
int rt_input_release_all(rt_input_state *s, rt_wmsg *out, int cap);
int rt_input_flush_move(rt_input_state *s, rt_wmsg *out, int cap);
void rt_input_take(rt_input_state *s, uint32_t msg, uint32_t lp);
int rt_input_state_key_down(const rt_input_state *s, uint32_t vk);
int rt_input_state_set_cursor(rt_input_state *s, int32_t x, int32_t y);
void rt_input_state_read_cursor(rt_input_state *s);
void rt_input_lat_inject(uint64_t ts);
void rt_input_client_point(const rt_input_state *s, int32_t *x, int32_t *y);
int rt_input_relative_wanted(const rt_input_state *s);
int rt_input_relative_update(rt_input_state *s);
int rt_input_shape(const rt_input_state *s);
const char *rt_input_shape_name(int shape);
const char *rt_input_msg_name(uint32_t msg, char *buf, size_t n);

void rt_input_minimise(int on);

int rt_script_wait_done(uint32_t ready, uint32_t consumed, const char *last, const char *want);
int rt_script_parse_line(const char *line, uint32_t n, rt_script_cmd *c);
int rt_script_parse(const char *text, rt_script_cmd **out, uint32_t *n);
int rt_script_events(const rt_script_cmd *c, rt_script_ctx *x, SDL_Event *out, int cap);

#endif
