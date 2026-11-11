#include "pad.h"

#include "input.h"

#include <math.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

static const char *const k_names[RT_PAD_NBTN] = {"south", "east", "west", "north", "back", "start", "leftshoulder", "rightshoulder",
                                                 "leftstick", "rightstick", "dpup", "dpdown", "dpleft", "dpright", "lefttrigger",
                                                 "righttrigger"};

static const SDL_GamepadButton k_sdl[14] = {
    SDL_GAMEPAD_BUTTON_SOUTH,      SDL_GAMEPAD_BUTTON_EAST,       SDL_GAMEPAD_BUTTON_WEST,       SDL_GAMEPAD_BUTTON_NORTH,
    SDL_GAMEPAD_BUTTON_BACK,       SDL_GAMEPAD_BUTTON_START,      SDL_GAMEPAD_BUTTON_LEFT_SHOULDER, SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER,
    SDL_GAMEPAD_BUTTON_LEFT_STICK, SDL_GAMEPAD_BUTTON_RIGHT_STICK, SDL_GAMEPAD_BUTTON_DPAD_UP,   SDL_GAMEPAD_BUTTON_DPAD_DOWN,
    SDL_GAMEPAD_BUTTON_DPAD_LEFT,  SDL_GAMEPAD_BUTTON_DPAD_RIGHT,
};

static const char *const k_default[RT_PAD_NBTN] = {"space", "e",      "r",      "f",      "c",      "escape",     "wheeldown", "wheelup",
                                                   "lshift", "lctrl", "1",      "3",      "4",      "2",          "mouse2",    "mouse1"};

static const int k_dir_key[4] = {SDL_SCANCODE_W, SDL_SCANCODE_A, SDL_SCANCODE_S, SDL_SCANCODE_D};

static const char *const k_face[2][4] = {{"A", "B", "X", "Y"}, {"Cross", "Circle", "Square", "Triangle"}};

static _Atomic int s_detected;

int rt_pad_detect(SDL_GamepadType type, const char *name)
{
    switch (type) {
    case SDL_GAMEPAD_TYPE_XBOX360:
    case SDL_GAMEPAD_TYPE_XBOXONE: return RT_PAD_XBOX;
    case SDL_GAMEPAD_TYPE_PS3:
    case SDL_GAMEPAD_TYPE_PS4:
    case SDL_GAMEPAD_TYPE_PS5: return RT_PAD_PLAYSTATION;
    default: break;
    }
    if (!name) return RT_PAD_XBOX;
    if (strcasestr(name, "Xbox")) return RT_PAD_XBOX;
    if (strcasestr(name, "PlayStation") || strcasestr(name, "DualSense") || strcasestr(name, "DualShock")) return RT_PAD_PLAYSTATION;
    return RT_PAD_XBOX;
}

int rt_pad_kind(void) { return atomic_load(&s_detected); }

const char *rt_pad_type_name(int kind) { return kind == RT_PAD_PLAYSTATION ? "playstation" : "xbox"; }

const char *rt_pad_button_name(int idx) { return idx >= 0 && idx < RT_PAD_NBTN ? k_names[idx] : ""; }

static int parse_target(const char *v, rt_pad_tgt *t)
{
    if (!strcmp(v, "mouse1") || !strcmp(v, "mouse2") || !strcmp(v, "mouse3")) {
        *t = (rt_pad_tgt){RT_PADT_MOUSE, v[5] == '1' ? 1 : v[5] == '2' ? 3 : 2};
        return 0;
    }
    if (!strcmp(v, "wheelup") || !strcmp(v, "wheeldown")) {
        *t = (rt_pad_tgt){RT_PADT_WHEEL, v[5] == 'u' ? 1 : -1};
        return 0;
    }
    const rt_key *k = rt_input_key_named(v);
    if (!k || k->sdl == SDL_SCANCODE_LGUI || k->sdl == SDL_SCANCODE_RGUI) return -1;
    *t = (rt_pad_tgt){RT_PADT_KEY, k->sdl};
    return 0;
}

static const char *target_name(const rt_pad_tgt *t, char *b, size_t n)
{
    const rt_key *k;
    switch (t->kind) {
    case RT_PADT_KEY: k = rt_input_key((uint32_t)t->arg); return k ? k->name : "?";
    case RT_PADT_MOUSE: snprintf(b, n, "mouse%d", t->arg == 1 ? 1 : t->arg == 3 ? 2 : 3); return b;
    case RT_PADT_WHEEL: return t->arg > 0 ? "wheelup" : "wheeldown";
    default: return "none";
    }
}

void rt_pad_defaults(rt_pad *p)
{
    memset(p, 0, sizeof *p);
    p->look_speed = RT_PAD_LOOK_SPEED;
    for (int i = 0; i < RT_PAD_NBTN; i++) parse_target(k_default[i], &p->map[i]);
}

int rt_pad_load(rt_pad *p, const char *dir)
{
    rt_pad_defaults(p);
    if (!dir || !dir[0]) return 0;
    char path[1100], line[128];
    snprintf(path, sizeof path, "%s/controls.txt", dir);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    int bad = 0;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0] || line[0] == '#') continue;
        char *eq = strchr(line, '=');
        if (!eq) {
            rt_log("pad controls: ignored line \"%s\"", line);
            bad++;
            continue;
        }
        *eq = 0;
        const char *v = eq + 1;
        if (!strcmp(line, "look_speed")) {
            char *end;
            long n = strtol(v, &end, 10);
            if (end == v || *end || n < 1 || n > 20000) {
                rt_log("pad controls: bad value \"%s\" for look_speed, default kept", v);
                bad++;
            } else {
                p->look_speed = (int)n;
            }
            continue;
        }
        int idx = -1;
        for (int i = 0; i < RT_PAD_NBTN; i++)
            if (!strcmp(line, k_names[i])) idx = i;
        if (idx < 0) {
            rt_log("pad controls: unknown button \"%s\"", line);
            bad++;
            continue;
        }
        rt_pad_tgt t;
        if (parse_target(v, &t)) {
            rt_log("pad controls: bad target \"%s\" for %s, default kept", v, line);
            bad++;
            continue;
        }
        p->map[idx] = t;
    }
    fclose(f);
    return bad;
}

int rt_pad_mappings(const char *dir)
{
    if (!dir || !dir[0]) return 0;
    char path[1100];
    snprintf(path, sizeof path, "%s/gamecontrollerdb.txt", dir);
    if (access(path, F_OK)) return 0;
    int n = SDL_AddGamepadMappingsFromFile(path);
    if (n < 0) {
        rt_log("pad mappings failed: %s", SDL_GetError());
        return -1;
    }
    rt_log("pad mappings %d", n);
    return n;
}

static int emit(const rt_pad_tgt *t, int down, rt_pad_pt pt, SDL_Event *out, int cap)
{
    if (cap < 1 || t->kind == RT_PADT_NONE) return 0;
    SDL_Event *e = out;
    memset(e, 0, sizeof *e);
    switch (t->kind) {
    case RT_PADT_KEY:
        e->type = down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
        e->key.scancode = (SDL_Scancode)t->arg;
        e->key.down = down;
        return 1;
    case RT_PADT_MOUSE:
        e->type = down ? SDL_EVENT_MOUSE_BUTTON_DOWN : SDL_EVENT_MOUSE_BUTTON_UP;
        e->button.button = (Uint8)t->arg;
        e->button.down = down;
        e->button.x = pt.x;
        e->button.y = pt.y;
        return 1;
    default:
        if (!down) return 0;
        e->type = SDL_EVENT_MOUSE_WHEEL;
        e->wheel.integer_y = t->arg;
        e->wheel.y = (float)t->arg;
        e->wheel.mouse_x = pt.x;
        e->wheel.mouse_y = pt.y;
        return 1;
    }
}

static void log_press(int idx, const rt_pad_tgt *t)
{
    char lab[24] = "", tb[16];
    if (idx < 14) {
        int kind = rt_pad_kind();
        SDL_GamepadButtonLabel l = SDL_GetGamepadButtonLabelForType(kind == RT_PAD_PLAYSTATION ? SDL_GAMEPAD_TYPE_PS5 : SDL_GAMEPAD_TYPE_XBOXONE, k_sdl[idx]);
        if (l >= SDL_GAMEPAD_BUTTON_LABEL_A && l <= SDL_GAMEPAD_BUTTON_LABEL_Y) snprintf(lab, sizeof lab, " (%s)", k_face[0][l - SDL_GAMEPAD_BUTTON_LABEL_A]);
        else if (l >= SDL_GAMEPAD_BUTTON_LABEL_CROSS) snprintf(lab, sizeof lab, " (%s)", k_face[1][l - SDL_GAMEPAD_BUTTON_LABEL_CROSS]);
    }
    rt_log("pad %s%s -> %s", k_names[idx], lab, target_name(t, tb, sizeof tb));
}

int rt_pad_button(rt_pad *p, int idx, int down, rt_pad_pt pt, SDL_Event *out, int cap)
{
    if (idx < 0 || idx >= RT_PAD_NBTN || p->down[idx] == (down != 0)) return 0;
    p->down[idx] = down != 0;
    if (down) log_press(idx, &p->map[idx]);
    return emit(&p->map[idx], down, pt, out, cap);
}

static int dir_key(rt_pad *p, int d, int on, rt_pad_pt pt, SDL_Event *out, int cap)
{
    if (p->dir[d] == on) return 0;
    p->dir[d] = (uint8_t)on;
    return emit(&(rt_pad_tgt){RT_PADT_KEY, k_dir_key[d]}, on, pt, out, cap);
}

int rt_pad_axis(rt_pad *p, int axis, int value, rt_pad_pt pt, SDL_Event *out, int cap)
{
    int n = 0;
    switch (axis) {
    case SDL_GAMEPAD_AXIS_LEFTX:
        n += dir_key(p, 1, value < -RT_PAD_DEAD, pt, out + n, cap - n);
        n += dir_key(p, 3, value > RT_PAD_DEAD, pt, out + n, cap - n);
        break;
    case SDL_GAMEPAD_AXIS_LEFTY:
        n += dir_key(p, 0, value < -RT_PAD_DEAD, pt, out + n, cap - n);
        n += dir_key(p, 2, value > RT_PAD_DEAD, pt, out + n, cap - n);
        break;
    case SDL_GAMEPAD_AXIS_RIGHTX: p->rx = value; break;
    case SDL_GAMEPAD_AXIS_RIGHTY: p->ry = value; break;
    case SDL_GAMEPAD_AXIS_LEFT_TRIGGER: n += rt_pad_button(p, RT_PAD_LT, value > RT_PAD_TRIGGER, pt, out, cap); break;
    case SDL_GAMEPAD_AXIS_RIGHT_TRIGGER: n += rt_pad_button(p, RT_PAD_RT, value > RT_PAD_TRIGGER, pt, out, cap); break;
    default: break;
    }
    return n;
}

int rt_pad_release(rt_pad *p, rt_pad_pt pt, SDL_Event *out, int cap)
{
    int n = 0;
    for (int i = 0; i < RT_PAD_NBTN; i++) n += rt_pad_button(p, i, 0, pt, out + n, cap - n);
    for (int d = 0; d < 4; d++) n += dir_key(p, d, 0, pt, out + n, cap - n);
    p->rx = p->ry = 0;
    p->fx = p->fy = 0;
    return n;
}

static int whole(double *f)
{
    double r = round(*f);
    int v = (int)(fabs(*f - r) < 1e-3 ? r : trunc(*f));
    *f -= v;
    return v;
}

static double deflect(int v) { return abs(v) > RT_PAD_DEAD ? v / 32767.0 : 0.0; }

void rt_pad_look(rt_pad *p, uint32_t ms, int *dx, int *dy)
{
    double k = (double)p->look_speed * ms / 1000.0;
    p->fx += deflect(p->rx) * k;
    p->fy += deflect(p->ry) * k;
    *dx = whole(&p->fx);
    *dy = whole(&p->fy);
}

void rt_pad_dev_init(rt_pad_dev *d, const char *dir)
{
    memset(d, 0, sizeof *d);
    rt_pad_load(&d->pad, dir);
}

int rt_pad_start(const char *dir)
{
    if (!SDL_InitSubSystem(SDL_INIT_GAMEPAD)) {
        rt_log("pad unavailable: %s", SDL_GetError());
        return -1;
    }
    rt_pad_mappings(dir);
    return 0;
}

static int ignored(const rt_pad_dev *d, SDL_JoystickID id)
{
    for (int i = 0; i < d->nignored; i++)
        if (d->ignored[i] == id) return 1;
    return 0;
}

int rt_pad_poll(rt_pad_dev *d, int active, rt_pad_pt pt, uint64_t now_ns, SDL_Event *out, int cap, int *dx, int *dy)
{
    int n = 0;
    *dx = *dy = 0;
    if (d->gp && !SDL_GamepadConnected(d->gp)) {
        n += rt_pad_release(&d->pad, pt, out, cap);
        SDL_CloseGamepad(d->gp);
        d->gp = NULL;
        d->id = 0;
        d->nignored = 0;
        atomic_store(&s_detected, RT_PAD_XBOX);
        rt_log("pad removed");
    }
    int count = 0;
    SDL_JoystickID *ids = SDL_GetGamepads(&count);
    for (int i = 0; ids && i < count; i++) {
        if (d->gp) {
            if (ids[i] == d->id || ignored(d, ids[i]) || d->nignored >= 8) continue;
            d->ignored[d->nignored++] = ids[i];
            const char *nm = SDL_GetGamepadNameForID(ids[i]);
            rt_log("pad ignored %s (another pad drives)", nm ? nm : "?");
            continue;
        }
        d->gp = SDL_OpenGamepad(ids[i]);
        if (!d->gp) continue;
        d->id = ids[i];
        d->last_ns = now_ns;
        const char *nm = SDL_GetGamepadName(d->gp);
        atomic_store(&s_detected, rt_pad_detect(SDL_GetGamepadType(d->gp), nm));
        rt_log("pad added %s type=%s", nm ? nm : "?", rt_pad_type_name(rt_pad_kind()));
    }
    SDL_free(ids);
    if (!d->gp || !active) {
        if (d->gp) d->last_ns = now_ns;
        return n;
    }
    for (int i = 0; i < 14; i++) n += rt_pad_button(&d->pad, i, SDL_GetGamepadButton(d->gp, k_sdl[i]), pt, out + n, cap - n);
    for (int a = 0; a < SDL_GAMEPAD_AXIS_COUNT; a++) n += rt_pad_axis(&d->pad, a, SDL_GetGamepadAxis(d->gp, (SDL_GamepadAxis)a), pt, out + n, cap - n);
    uint64_t ms = (now_ns - d->last_ns) / 1000000u;
    d->last_ns = now_ns;
    rt_pad_look(&d->pad, ms > 100 ? 100 : (uint32_t)ms, dx, dy);
    return n;
}

static SDL_Joystick *s_joy[4];
static SDL_JoystickID s_joy_id[4];

int rt_pad_test_start(void)
{
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI, "0");
    SDL_SetHint(SDL_HINT_JOYSTICK_IOKIT, "0");
    SDL_SetHint(SDL_HINT_JOYSTICK_MFI, "0");
    return SDL_InitSubSystem(SDL_INIT_GAMEPAD) ? 0 : -1;
}

SDL_JoystickID rt_pad_test_attach(const char *name)
{
    SDL_VirtualJoystickDesc desc;
    SDL_INIT_INTERFACE(&desc);
    desc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    desc.naxes = SDL_GAMEPAD_AXIS_COUNT;
    desc.nbuttons = SDL_GAMEPAD_BUTTON_DPAD_RIGHT + 1;
    desc.button_mask = (1u << (SDL_GAMEPAD_BUTTON_DPAD_RIGHT + 1)) - 1u;
    desc.axis_mask = (1u << SDL_GAMEPAD_AXIS_COUNT) - 1u;
    desc.name = name;
    SDL_JoystickID id = SDL_AttachVirtualJoystick(&desc);
    if (!id) return 0;
    for (int i = 0; i < 4; i++)
        if (!s_joy[i]) {
            s_joy[i] = SDL_OpenJoystick(id);
            s_joy_id[i] = id;
            break;
        }
    SDL_UpdateJoysticks();
    return id;
}

static SDL_Joystick *joy_of(SDL_JoystickID id)
{
    for (int i = 0; i < 4; i++)
        if (s_joy[i] && s_joy_id[i] == id) return s_joy[i];
    return NULL;
}

void rt_pad_test_button(SDL_JoystickID id, int button, int down)
{
    SDL_Joystick *j = joy_of(id);
    if (j) SDL_SetJoystickVirtualButton(j, button, down != 0);
    SDL_UpdateJoysticks();
}

void rt_pad_test_axis(SDL_JoystickID id, int axis, int value)
{
    SDL_Joystick *j = joy_of(id);
    if (j) SDL_SetJoystickVirtualAxis(j, axis, (Sint16)value);
    SDL_UpdateJoysticks();
}

void rt_pad_test_detach(SDL_JoystickID id)
{
    for (int i = 0; i < 4; i++)
        if (s_joy[i] && s_joy_id[i] == id) {
            SDL_CloseJoystick(s_joy[i]);
            s_joy[i] = NULL;
        }
    SDL_DetachVirtualJoystick(id);
    SDL_UpdateJoysticks();
}
