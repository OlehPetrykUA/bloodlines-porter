#ifndef RT_PAD_H
#define RT_PAD_H

#include <SDL3/SDL.h>
#include <stddef.h>
#include <stdint.h>

enum { RT_PAD_XBOX, RT_PAD_PLAYSTATION };

enum { RT_PADT_NONE, RT_PADT_KEY, RT_PADT_MOUSE, RT_PADT_WHEEL };

enum { RT_PAD_LT = 14, RT_PAD_RT = 15, RT_PAD_NBTN = 16 };

#define RT_PAD_DEAD 8000
#define RT_PAD_TRIGGER 16384
#define RT_PAD_LOOK_SPEED 1200
#define RT_PAD_EVENTS 64

typedef struct {
    int kind, arg;
} rt_pad_tgt;

typedef struct {
    float x, y;
} rt_pad_pt;

typedef struct {
    rt_pad_tgt map[RT_PAD_NBTN];
    int look_speed;
    uint8_t down[RT_PAD_NBTN];
    uint8_t dir[4];
    int rx, ry;
    double fx, fy;
} rt_pad;

typedef struct {
    rt_pad pad;
    SDL_Gamepad *gp;
    SDL_JoystickID id, ignored[8];
    int nignored;
    uint64_t last_ns;
} rt_pad_dev;

int rt_pad_detect(SDL_GamepadType type, const char *name);
int rt_pad_kind(void);
const char *rt_pad_type_name(int kind);
const char *rt_pad_button_name(int idx);

void rt_pad_defaults(rt_pad *p);
int rt_pad_load(rt_pad *p, const char *dir);
int rt_pad_mappings(const char *dir);

int rt_pad_button(rt_pad *p, int idx, int down, rt_pad_pt pt, SDL_Event *out, int cap);
int rt_pad_axis(rt_pad *p, int axis, int value, rt_pad_pt pt, SDL_Event *out, int cap);
int rt_pad_release(rt_pad *p, rt_pad_pt pt, SDL_Event *out, int cap);
void rt_pad_look(rt_pad *p, uint32_t ms, int *dx, int *dy);

void rt_pad_dev_init(rt_pad_dev *d, const char *dir);
int rt_pad_start(const char *dir);
int rt_pad_poll(rt_pad_dev *d, int active, rt_pad_pt pt, uint64_t now_ns, SDL_Event *out, int cap, int *dx, int *dy);

int rt_pad_test_start(void);
SDL_JoystickID rt_pad_test_attach(const char *name);
void rt_pad_test_button(SDL_JoystickID id, int button, int down);
void rt_pad_test_axis(SDL_JoystickID id, int axis, int value);
void rt_pad_test_detach(SDL_JoystickID id);

#endif
