#include "input.h"
#include "toggles.h"

#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const rt_key rt_keys[] = {
    {0x04, 0x1E, 0, 0x41, "a"},           {0x05, 0x30, 0, 0x42, "b"},          {0x06, 0x2E, 0, 0x43, "c"},
    {0x07, 0x20, 0, 0x44, "d"},           {0x08, 0x12, 0, 0x45, "e"},          {0x09, 0x21, 0, 0x46, "f"},
    {0x0A, 0x22, 0, 0x47, "g"},           {0x0B, 0x23, 0, 0x48, "h"},          {0x0C, 0x17, 0, 0x49, "i"},
    {0x0D, 0x24, 0, 0x4A, "j"},           {0x0E, 0x25, 0, 0x4B, "k"},          {0x0F, 0x26, 0, 0x4C, "l"},
    {0x10, 0x32, 0, 0x4D, "m"},           {0x11, 0x31, 0, 0x4E, "n"},          {0x12, 0x18, 0, 0x4F, "o"},
    {0x13, 0x19, 0, 0x50, "p"},           {0x14, 0x10, 0, 0x51, "q"},          {0x15, 0x13, 0, 0x52, "r"},
    {0x16, 0x1F, 0, 0x53, "s"},           {0x17, 0x14, 0, 0x54, "t"},          {0x18, 0x16, 0, 0x55, "u"},
    {0x19, 0x2F, 0, 0x56, "v"},           {0x1A, 0x11, 0, 0x57, "w"},          {0x1B, 0x2D, 0, 0x58, "x"},
    {0x1C, 0x15, 0, 0x59, "y"},           {0x1D, 0x2C, 0, 0x5A, "z"},          {0x1E, 0x02, 0, 0x31, "1"},
    {0x1F, 0x03, 0, 0x32, "2"},           {0x20, 0x04, 0, 0x33, "3"},          {0x21, 0x05, 0, 0x34, "4"},
    {0x22, 0x06, 0, 0x35, "5"},           {0x23, 0x07, 0, 0x36, "6"},          {0x24, 0x08, 0, 0x37, "7"},
    {0x25, 0x09, 0, 0x38, "8"},           {0x26, 0x0A, 0, 0x39, "9"},          {0x27, 0x0B, 0, 0x30, "0"},
    {0x28, 0x1C, 0, 13, "return"},        {0x29, 0x01, 0, 27, "escape"},       {0x2A, 0x0E, 0, 8, "backspace"},
    {0x2B, 0x0F, 0, 9, "tab"},            {0x2C, 0x39, 0, 32, "space"},        {0x2D, 0x0C, 0, 189, "minus"},
    {0x2E, 0x0D, 0, 187, "equals"},       {0x2F, 0x1A, 0, 219, "leftbracket"}, {0x30, 0x1B, 0, 221, "rightbracket"},
    {0x31, 0x2B, 0, 220, "backslash"},    {0x32, 0x2B, 0, 220, "nonushash"},   {0x33, 0x27, 0, 186, "semicolon"},
    {0x34, 0x28, 0, 222, "apostrophe"},   {0x35, 0x29, 0, 192, "grave"},       {0x36, 0x33, 0, 188, "comma"},
    {0x37, 0x34, 0, 190, "period"},       {0x38, 0x35, 0, 191, "slash"},       {0x39, 0x3A, 0, 20, "capslock"},
    {0x3A, 0x3B, 0, 112, "f1"},           {0x3B, 0x3C, 0, 113, "f2"},          {0x3C, 0x3D, 0, 114, "f3"},
    {0x3D, 0x3E, 0, 115, "f4"},           {0x3E, 0x3F, 0, 116, "f5"},          {0x3F, 0x40, 0, 117, "f6"},
    {0x40, 0x41, 0, 118, "f7"},           {0x41, 0x42, 0, 119, "f8"},          {0x42, 0x43, 0, 120, "f9"},
    {0x43, 0x44, 0, 121, "f10"},          {0x44, 0x57, 0, 122, "f11"},         {0x45, 0x58, 0, 123, "f12"},
    {0x46, 0x37, 1, 44, "printscreen"},   {0x47, 0x46, 0, 145, "scrolllock"},  {0x48, 0x45, 0, 19, "pause"},
    {0x49, 0x52, 1, 45, "insert"},        {0x4A, 0x47, 1, 36, "home"},         {0x4B, 0x49, 1, 33, "pageup"},
    {0x4C, 0x53, 1, 46, "delete"},        {0x4D, 0x4F, 1, 35, "end"},          {0x4E, 0x51, 1, 34, "pagedown"},
    {0x4F, 0x4D, 1, 39, "right"},         {0x50, 0x4B, 1, 37, "left"},         {0x51, 0x50, 1, 40, "down"},
    {0x52, 0x48, 1, 38, "up"},            {0x53, 0x45, 1, 144, "numlockclear"}, {0x54, 0x35, 1, 111, "kpdivide"},
    {0x55, 0x37, 0, 106, "kpmultiply"},   {0x56, 0x4A, 0, 109, "kpminus"},     {0x57, 0x4E, 0, 107, "kpplus"},
    {0x58, 0x1C, 1, 13, "kpenter"},       {0x59, 0x4F, 0, 97, "kp1"},          {0x5A, 0x50, 0, 98, "kp2"},
    {0x5B, 0x51, 0, 99, "kp3"},           {0x5C, 0x4B, 0, 100, "kp4"},         {0x5D, 0x4C, 0, 101, "kp5"},
    {0x5E, 0x4D, 0, 102, "kp6"},          {0x5F, 0x47, 0, 103, "kp7"},         {0x60, 0x48, 0, 104, "kp8"},
    {0x61, 0x49, 0, 105, "kp9"},          {0x62, 0x52, 0, 96, "kp0"},          {0x63, 0x53, 0, 110, "kpperiod"},
    {0x64, 0x56, 0, 226, "nonusbackslash"}, {0x65, 0x5D, 1, 93, "application"},
    {0xE0, 0x1D, 0, 162, "lctrl"},        {0xE1, 0x2A, 0, 160, "lshift"},      {0xE2, 0x38, 0, 164, "lalt"},
    {0xE3, 0x5B, 1, 91, "lgui"},          {0xE4, 0x1D, 1, 163, "rctrl"},       {0xE5, 0x36, 0, 161, "rshift"},
    {0xE6, 0x38, 1, 165, "ralt"},         {0xE7, 0x5C, 1, 92, "rgui"},
};

const uint32_t rt_nkeys = sizeof rt_keys / sizeof rt_keys[0];

static const char *const k_shape_names[] = {"default", "text", "wait", "crosshair", "progress", "nwse_resize",
                                             "nesw_resize", "ew_resize", "ns_resize", "move", "not_allowed", "pointer"};

static const struct {
    uint16_t idc;
    uint8_t shape;
} k_idc[] = {
    {32512, SDL_SYSTEM_CURSOR_DEFAULT},     {32513, SDL_SYSTEM_CURSOR_TEXT},        {32514, SDL_SYSTEM_CURSOR_WAIT},
    {32515, SDL_SYSTEM_CURSOR_CROSSHAIR},   {32642, SDL_SYSTEM_CURSOR_NWSE_RESIZE}, {32643, SDL_SYSTEM_CURSOR_NESW_RESIZE},
    {32644, SDL_SYSTEM_CURSOR_EW_RESIZE},   {32645, SDL_SYSTEM_CURSOR_NS_RESIZE},   {32646, SDL_SYSTEM_CURSOR_MOVE},
    {32648, SDL_SYSTEM_CURSOR_NOT_ALLOWED}, {32649, SDL_SYSTEM_CURSOR_POINTER},     {32650, SDL_SYSTEM_CURSOR_PROGRESS},
};

const rt_key *rt_input_key(uint32_t scancode)
{
    for (uint32_t i = 0; i < rt_nkeys; i++)
        if (rt_keys[i].sdl == scancode) return &rt_keys[i];
    return NULL;
}

const rt_key *rt_input_key_named(const char *name)
{
    for (uint32_t i = 0; i < rt_nkeys; i++)
        if (!strcmp(rt_keys[i].name, name)) return &rt_keys[i];
    return NULL;
}

static uint32_t kidx(const rt_key *k) { return k->scan | (uint32_t)k->ext << 8; }

static uint32_t generic_vk(uint32_t vk)
{
    if (vk == 160 || vk == 161) return 16;
    if (vk == 162 || vk == 163) return 17;
    if (vk == 164 || vk == 165) return 18;
    return vk;
}

static const rt_key *by_idx(uint32_t idx)
{
    for (uint32_t i = 0; i < rt_nkeys; i++)
        if (kidx(&rt_keys[i]) == idx) return &rt_keys[i];
    return NULL;
}

void rt_input_init(rt_input_state *s)
{
    memset(s, 0, sizeof *s);
    s->focus = 1;
    s->cursor = 0x00007F00u;
    s->applied = RT_CUR_UNSET;
    s->client_w = 1280;
    s->client_h = 1024;
}

void rt_input_map(const rt_present_rects *r, float density, float x, float y, int32_t *cx, int32_t *cy)
{
    double rho = density > 0 ? density : 1.0;
    *cx = r->sx + (int32_t)floor(((double)x * rho - r->dx) * r->sw / (r->dw ? r->dw : 1));
    *cy = r->sy + (int32_t)floor(((double)y * rho - r->dy) * r->sh / (r->dh ? r->dh : 1));
}

void rt_input_unmap(const rt_present_rects *r, float density, int32_t cx, int32_t cy, float *x, float *y)
{
    double rho = density > 0 ? density : 1.0;
    *x = (float)((r->dx + (cx - r->sx + 0.5) * r->dw / (r->sw ? r->sw : 1)) / rho);
    *y = (float)((r->dy + (cy - r->sy + 0.5) * r->dh / (r->sh ? r->sh : 1)) / rho);
}

static int held(const rt_input_state *s, uint32_t a, uint32_t b) { return s->held[a] || s->held[b]; }

static uint32_t mk(const rt_input_state *s)
{
    return s->buttons | (held(s, 0x2A, 0x36) ? MK_SHIFT : 0) | (held(s, 0x1D, 0x11D) ? MK_CONTROL : 0);
}

static int alt_held(const rt_input_state *s) { return held(s, 0x38, 0x138); }

static uint32_t lp_xy(int32_t x, int32_t y) { return (uint32_t)(uint16_t)x | (uint32_t)(uint16_t)y << 16; }

static void put(rt_wmsg *out, int cap, int *n, uint32_t msg, uint32_t wp, uint32_t lp)
{
    if (*n < cap) out[*n] = (rt_wmsg){msg, wp, lp};
    (*n)++;
}

static void emit(rt_input_state *s, rt_wmsg *out, int cap, int *n, uint32_t msg, uint32_t wp, uint32_t lp)
{
    if (s->move_pending) {
        s->move_pending = 0;
        put(out, cap, n, WM_MOUSEMOVE, mk(s), lp_xy(s->x, s->y));
    }
    put(out, cap, n, msg, wp, lp);
}

int rt_input_flush_move(rt_input_state *s, rt_wmsg *out, int cap)
{
    int n = 0;
    if (s->move_pending) {
        s->move_pending = 0;
        put(out, cap, &n, WM_MOUSEMOVE, mk(s), lp_xy(s->x, s->y));
    }
    return n;
}

static void place(rt_input_state *s, const rt_present_rects *r, float density, float x, float y)
{
    int32_t cx, cy;
    rt_input_map(r, density, x, y, &cx, &cy);
    if (!s->buttons && !s->capture) {
        cx = cx < 0 ? 0 : cx >= s->client_w ? s->client_w - 1 : cx;
        cy = cy < 0 ? 0 : cy >= s->client_h ? s->client_h - 1 : cy;
    }
    s->x = cx;
    s->y = cy;
}

static int32_t whole(double *f)
{
    double r = round(*f);
    int32_t v = (int32_t)(fabs(*f - r) < 1e-3 ? r : trunc(*f));
    *f -= v;
    return v;
}

static const struct {
    uint32_t bit, down, up, xb;
} k_btn[6] = {
    {0, 0, 0, 0},
    {MK_LBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP, 0},
    {MK_MBUTTON, WM_MBUTTONDOWN, WM_MBUTTONUP, 0},
    {MK_RBUTTON, WM_RBUTTONDOWN, WM_RBUTTONUP, 0},
    {MK_XBUTTON1, WM_XBUTTONDOWN, WM_XBUTTONUP, 1},
    {MK_XBUTTON2, WM_XBUTTONDOWN, WM_XBUTTONUP, 2},
};

static uint32_t control_char(const rt_input_state *s, uint32_t sdl)
{
    switch (sdl) {
    case 0x2A: return 0x08;
    case 0x2B: return 0x09;
    case 0x29: return 0x1B;
    case 0x28:
    case 0x58: return held(s, 0x2A, 0x36) ? 0x0A : 0x0D;
    default: return 0;
    }
}

static int utf8_next(const unsigned char **p)
{
    const unsigned char *q = *p;
    int c = *q++, n = c < 0x80 ? 0 : c < 0xE0 ? 1 : c < 0xF0 ? 2 : 3;
    if (n) c &= 0x3F >> n;
    for (int i = 0; i < n && (*q & 0xC0) == 0x80; i++) c = c << 6 | (*q++ & 0x3F);
    *p = q;
    return c;
}

int rt_input_translate(rt_input_state *s, const SDL_Event *e, const rt_present_rects *r, float density, rt_wmsg *out,
                       int cap)
{
    int n = 0;
    switch (e->type) {
    case SDL_EVENT_MOUSE_MOTION:
        s->moves++;
        if (!s->focus) break;
        if (s->relative) {
            if (e->motion.xrel != 0 || e->motion.yrel != 0) {
                double rho = density > 0 ? density : 1.0;
                s->fx += (double)e->motion.xrel * rho * r->sw / (r->dw ? r->dw : 1);
                s->fy += (double)e->motion.yrel * rho * r->sh / (r->dh ? r->dh : 1);
                s->x += whole(&s->fx);
                s->y += whole(&s->fy);
                s->looks++;
                s->move_pending = 1;
            }
            break;
        }
        place(s, r, density, e->motion.x, e->motion.y);
        s->move_pending = 1;
        break;
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP: {
        uint32_t b = e->button.button;
        if (!s->focus || b < 1 || b > 5) break;
        int down = e->type == SDL_EVENT_MOUSE_BUTTON_DOWN;
        if (!!(s->buttons & k_btn[b].bit) == down) break;
        n += rt_input_flush_move(s, out + (n < cap ? n : cap), n < cap ? cap - n : 0);
        if (!s->relative) place(s, r, density, e->button.x, e->button.y);
        if (down) s->buttons |= k_btn[b].bit;
        else s->buttons &= ~k_btn[b].bit;
        emit(s, out, cap, &n, down ? k_btn[b].down : k_btn[b].up, mk(s) | k_btn[b].xb << 16, lp_xy(s->x, s->y));
        s->nbuttons++;
        break;
    }
    case SDL_EVENT_MOUSE_WHEEL: {
        int32_t t = e->wheel.integer_y;
        if (!s->focus || s->gui) break;
        for (int32_t i = 0; i < (t < 0 ? -t : t); i++)
            emit(s, out, cap, &n, WM_MOUSEWHEEL, (uint32_t)(uint16_t)(t < 0 ? -WHEEL_DELTA : WHEEL_DELTA) << 16 | mk(s),
                 lp_xy(s->x + s->origin_x, s->y + s->origin_y));
        break;
    }
    case SDL_EVENT_KEY_DOWN:
    case SDL_EVENT_KEY_UP: {
        uint32_t sc = e->key.scancode;
        int down = e->type == SDL_EVENT_KEY_DOWN;
        if (sc == SDL_SCANCODE_LGUI || sc == SDL_SCANCODE_RGUI) {
            s->gui = down;
            break;
        }
        const rt_key *k = rt_input_key(sc);
        if (down) {
            if (!s->focus) break;
            if ((e->key.mod & SDL_KMOD_GUI) || s->gui) {
                if (!e->key.repeat) {
                    s->chords++;
                    rt_log("input host chord");
                    int tg = rt_toggle_chord(sc);
                    if (tg >= 0) rt_toggle_request(tg, RT_SRC_CHORD);
                }
                break;
            }
            if (!k) {
                if (sc < RT_KEYIDX && !s->unmapped[sc]) {
                    s->unmapped[sc] = 1;
                    rt_log("input unmapped scancode %u", sc);
                }
                break;
            }
            uint32_t i = kidx(k), prev = s->held[i];
            s->held[i] = 1;
            int alt = alt_held(s);
            uint32_t lp = 1u | (uint32_t)k->scan << 16 | (uint32_t)k->ext << 24 | (uint32_t)alt << 29 | prev << 30;
            emit(s, out, cap, &n, alt ? WM_SYSKEYDOWN : WM_KEYDOWN, generic_vk(k->vk), lp);
            s->keys++;
            s->last_lp = lp;
            uint32_t ch = alt ? 0 : control_char(s, sc);
            if (ch) {
                emit(s, out, cap, &n, WM_CHAR, ch, lp);
                s->chars++;
            }
        } else {
            if (!k || !s->held[kidx(k)]) break;
            int alt = alt_held(s);
            s->held[kidx(k)] = 0;
            uint32_t lp = 1u | (uint32_t)k->scan << 16 | (uint32_t)k->ext << 24 | (uint32_t)alt << 29 | 3u << 30;
            emit(s, out, cap, &n, alt ? WM_SYSKEYUP : WM_KEYUP, generic_vk(k->vk), lp);
            s->keys++;
        }
        break;
    }
    case SDL_EVENT_TEXT_INPUT: {
        if (!s->focus || s->gui || !e->text.text) break;
        for (const unsigned char *p = (const unsigned char *)e->text.text; *p;) {
            int c = utf8_next(&p);
            if (c < 0x20 || c == 0x7F) continue;
            int b = c < 0x10000 ? rt_cp1252_from_wide((uint16_t)c) : -1;
            if (b < 0) {
                s->dropped++;
                continue;
            }
            emit(s, out, cap, &n, WM_CHAR, (uint32_t)b, s->last_lp);
            s->chars++;
        }
        break;
    }
    default:
        break;
    }
    return n;
}

static rt_pad_pt pad_point(const rt_input_state *s, const rt_present_rects *r, float density)
{
    rt_pad_pt p;
    rt_input_unmap(r, density, s->x, s->y, &p.x, &p.y);
    return p;
}

int rt_input_pad_apply(rt_input_state *s, const SDL_Event *ev, int k, int dx, int dy, const rt_present_rects *r, float density,
                       rt_wmsg *out, int cap)
{
    int n = 0;
    for (int i = 0; i < k && n < cap; i++) {
        if (s->gui && ev[i].type == SDL_EVENT_KEY_DOWN) continue;
        n += rt_input_translate(s, &ev[i], r, density, out + n, cap - n);
    }
    if ((dx || dy) && n < cap) {
        double rho = density > 0 ? density : 1.0;
        rt_pad_pt p = pad_point(s, r, density);
        SDL_Event e;
        memset(&e, 0, sizeof e);
        e.type = SDL_EVENT_MOUSE_MOTION;
        e.motion.xrel = (float)(dx / rho);
        e.motion.yrel = (float)(dy / rho);
        e.motion.x = p.x + e.motion.xrel;
        e.motion.y = p.y + e.motion.yrel;
        n += rt_input_translate(s, &e, r, density, out + n, cap - n);
    }
    return n < cap ? n : cap;
}

int rt_input_pad_pump(rt_input_state *s, rt_pad_dev *d, int active, uint64_t now_ns, const rt_present_rects *r, float density,
                      rt_wmsg *out, int cap)
{
    SDL_Event ev[RT_PAD_EVENTS];
    int dx, dy;
    int k = rt_pad_poll(d, active, pad_point(s, r, density), now_ns, ev, RT_PAD_EVENTS, &dx, &dy);
    return rt_input_pad_apply(s, ev, k, dx, dy, r, density, out, cap);
}

int rt_input_pad_lost(rt_input_state *s, rt_pad_dev *d, const rt_present_rects *r, float density, rt_wmsg *out, int cap)
{
    SDL_Event ev[RT_PAD_EVENTS];
    int k = rt_pad_release(&d->pad, pad_point(s, r, density), ev, RT_PAD_EVENTS);
    return rt_input_pad_apply(s, ev, k, 0, 0, r, density, out, cap);
}

int rt_input_release_all(rt_input_state *s, rt_wmsg *out, int cap)
{
    int n = rt_input_flush_move(s, out, cap);
    for (uint32_t i = 0; i < RT_KEYIDX; i++) {
        if (!s->held[i]) continue;
        s->held[i] = 0;
        const rt_key *k = by_idx(i);
        if (!k) continue;
        uint32_t lp = 1u | (uint32_t)k->scan << 16 | (uint32_t)k->ext << 24 | 3u << 30;
        emit(s, out, cap, &n, WM_KEYUP, generic_vk(k->vk), lp);
        s->keys++;
    }
    for (uint32_t b = 1; b <= 5; b++) {
        if (!(s->buttons & k_btn[b].bit)) continue;
        s->buttons &= ~k_btn[b].bit;
        emit(s, out, cap, &n, k_btn[b].up, mk(s) | k_btn[b].xb << 16, lp_xy(s->x, s->y));
        s->nbuttons++;
    }
    s->gui = 0;
    s->warp_pending = 0;
    s->focus = 0;
    return n;
}

void rt_input_take(rt_input_state *s, uint32_t msg, uint32_t lp)
{
    if (msg != WM_KEYDOWN && msg != WM_KEYUP && msg != WM_SYSKEYDOWN && msg != WM_SYSKEYUP) return;
    uint32_t i = (lp >> 16) & 0x1FFu;
    s->taken[i] = msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN;
}

int rt_input_state_key_down(const rt_input_state *s, uint32_t vk)
{
    for (uint32_t i = 0; i < rt_nkeys; i++)
        if (s->taken[kidx(&rt_keys[i])] && (rt_keys[i].vk == vk || generic_vk(rt_keys[i].vk) == vk)) return 1;
    return 0;
}

void rt_input_state_read_cursor(rt_input_state *s)
{
    s->read_x = s->x;
    s->read_y = s->y;
}

int rt_input_state_set_cursor(rt_input_state *s, int32_t x, int32_t y)
{
    if (s->relative) {
        s->x = x + (s->x - s->read_x);
        s->y = y + (s->y - s->read_y);
        s->read_x = x;
        s->read_y = y;
        s->recentres++;
        s->warp_pending = 0;
        return 0;
    }
    s->x = x;
    s->y = y;
    int inside = x >= 0 && y >= 0 && x < s->client_w && y < s->client_h;
    s->warp_pending = s->focus && s->host_focus && inside;
    s->warp_x = x;
    s->warp_y = y;
    return s->warp_pending;
}

static int guest_shape(const rt_input_state *s)
{
    if (!s->cursor || s->show_count < 0) return RT_CUR_HIDDEN;
    for (size_t i = 0; i < sizeof k_idc / sizeof k_idc[0]; i++)
        if (k_idc[i].idc == (s->cursor & 0xFFFFu)) return k_idc[i].shape;
    return SDL_SYSTEM_CURSOR_DEFAULT;
}

int rt_input_relative_wanted(const rt_input_state *s)
{
    return s->focus && s->host_focus && guest_shape(s) == RT_CUR_HIDDEN;
}

int rt_input_relative_update(rt_input_state *s)
{
    int want = rt_input_relative_wanted(s);
    if (want == s->relative) return 0;
    s->relative = want;
    rt_input_state_read_cursor(s);
    s->rel_switches++;
    s->fx = s->fy = 0;
    return 1;
}

void rt_input_client_point(const rt_input_state *s, int32_t *x, int32_t *y)
{
    *x = s->x < 0 ? 0 : s->x >= s->client_w ? s->client_w - 1 : s->x;
    *y = s->y < 0 ? 0 : s->y >= s->client_h ? s->client_h - 1 : s->y;
}

int rt_input_shape(const rt_input_state *s)
{
    int g = guest_shape(s);
    return g == RT_CUR_HIDDEN && (!s->focus || !s->host_focus) ? SDL_SYSTEM_CURSOR_DEFAULT : g;
}

const char *rt_input_shape_name(int shape)
{
    if (shape < 0) return "hidden";
    return shape < (int)(sizeof k_shape_names / sizeof k_shape_names[0]) ? k_shape_names[shape] : "default";
}

const char *rt_input_msg_name(uint32_t msg, char *buf, size_t n)
{
    switch (msg) {
    case 0x0005: return "WM_SIZE";
    case WM_ACTIVATE: return "WM_ACTIVATE";
    case 0x0010: return "WM_CLOSE";
    case WM_ACTIVATEAPP: return "WM_ACTIVATEAPP";
    case WM_KEYDOWN: return "WM_KEYDOWN";
    case WM_KEYUP: return "WM_KEYUP";
    case WM_CHAR: return "WM_CHAR";
    case WM_SYSKEYDOWN: return "WM_SYSKEYDOWN";
    case WM_SYSKEYUP: return "WM_SYSKEYUP";
    case WM_MOUSEMOVE: return "WM_MOUSEMOVE";
    case WM_LBUTTONDOWN: return "WM_LBUTTONDOWN";
    case WM_LBUTTONUP: return "WM_LBUTTONUP";
    case WM_RBUTTONDOWN: return "WM_RBUTTONDOWN";
    case WM_RBUTTONUP: return "WM_RBUTTONUP";
    case WM_MBUTTONDOWN: return "WM_MBUTTONDOWN";
    case WM_MBUTTONUP: return "WM_MBUTTONUP";
    case WM_MOUSEWHEEL: return "WM_MOUSEWHEEL";
    case WM_XBUTTONDOWN: return "WM_XBUTTONDOWN";
    case WM_XBUTTONUP: return "WM_XBUTTONUP";
    default: snprintf(buf, n, "WM_0x%X", msg); return buf;
    }
}

static rt_input_state S;
static rt_pad_dev s_padv;
static int s_pad_started;

__attribute__((constructor)) static void input_state_init(void)
{
    rt_input_init(&S);
    rt_pad_dev_init(&s_padv, NULL);
}
static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static uint32_t s_hwnd;
static SDL_Window *s_win;
static int s_scale_set;
static SDL_Cursor *s_cursors[SDL_SYSTEM_CURSOR_COUNT];

#define LAT_MAX 256
static struct {
    int armed, state;
    uint64_t inj, apply, read;
    uint32_t presents, pumps, pn_inj, pn_read, n;
    int32_t rc_x, rc_y;
    int64_t ax, ay, cx, cy;
    double rd_ms[LAT_MAX], pr_ms[LAT_MAX];
    uint32_t rd_f[LAT_MAX], pr_f[LAT_MAX];
} L;

void rt_input_lat_inject(uint64_t ts)
{
    pthread_mutex_lock(&s_mu);
    if (!L.armed) {
        L.armed = 1;
        L.rc_x = S.x;
        L.rc_y = S.y;
    }
    L.inj = ts;
    L.pn_inj = L.presents;
    L.state = 1;
    pthread_mutex_unlock(&s_mu);
}

void rt_input_lat_present(void)
{
    pthread_mutex_lock(&s_mu);
    L.presents++;
    if (L.state == 3 && L.n < LAT_MAX) {
        uint64_t now = SDL_GetTicksNS();
        L.rd_ms[L.n] = (double)(L.read - L.inj) / 1e6;
        L.rd_f[L.n] = L.pn_read - L.pn_inj;
        L.pr_ms[L.n] = (double)(now - L.inj) / 1e6;
        L.pr_f[L.n] = L.presents - L.pn_inj;
        L.n++;
    }
    if (L.state == 3) L.state = 0;
    pthread_mutex_unlock(&s_mu);
}

static void lat_sort(double *ms, uint32_t *f, uint32_t n, double *p50, uint32_t *p50f, double *mx, uint32_t *mxf)
{
    double a[LAT_MAX];
    uint32_t b[LAT_MAX];
    memcpy(a, ms, n * sizeof *a);
    memcpy(b, f, n * sizeof *b);
    for (uint32_t i = 1; i < n; i++) {
        double v = a[i];
        uint32_t w = b[i];
        uint32_t j = i;
        for (; j && a[j - 1] > v; j--) a[j] = a[j - 1], b[j] = b[j - 1];
        a[j] = v;
        b[j] = w;
    }
    *p50 = n ? a[n / 2] : 0;
    *p50f = n ? b[n / 2] : 0;
    *mx = n ? a[n - 1] : 0;
    uint32_t m = 0;
    for (uint32_t i = 0; i < n; i++) m = f[i] > m ? f[i] : m;
    *mxf = m;
}

void rt_input_post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp)
{
    if (msg != WM_MOUSEMOVE) {
        char b[24];
        rt_log("input %s wp 0x%X lp 0x%X", rt_input_msg_name(msg, b, sizeof b), wp, lp);
    }
    rt_msg_post(hwnd, msg, wp, lp);
}

static void post_all(const rt_wmsg *m, int n, int cap)
{
    if (n > cap) {
        rt_log("input dropped %d messages over one event's limit", n - cap);
        n = cap;
    }
    for (int i = 0; i < n; i++) rt_input_post(s_hwnd, m[i].msg, m[i].wp, m[i].lp);
}

static void geometry_locked(void)
{
    rt_win *w = s_hwnd ? rt_win_get(s_hwnd) : NULL;
    if (!w) return;
    S.client_w = w->w > 0 ? w->w : 1;
    S.client_h = w->h > 0 ? w->h : 1;
    S.origin_x = w->x;
    S.origin_y = w->y;
}

void rt_input_geometry(rt_present_rects *r, float *density)
{
    pthread_mutex_lock(&s_mu);
    geometry_locked();
    int cw = S.client_w, ch = S.client_h;
    pthread_mutex_unlock(&s_mu);
    *density = s_win ? SDL_GetWindowPixelDensity(s_win) : 1.0f;
    if (*density <= 0) *density = 1.0f;
    if (rt_gfx_present_rects(r)) return;
    int pw = 0, ph = 0;
    if (!s_win || !SDL_GetWindowSizeInPixels(s_win, &pw, &ph) || pw <= 0 || ph <= 0) {
        pw = (int)lroundf((float)cw * *density);
        ph = (int)lroundf((float)ch * *density);
    }
    *r = (rt_present_rects){0, 0, cw, ch, 0, 0, pw, ph};
}

void rt_input_main_window(uint32_t hwnd, void *sdl)
{
    pthread_mutex_lock(&s_mu);
    s_hwnd = hwnd;
    s_win = sdl;
    if (sdl && !s_scale_set) {
        const char *e = getenv("RT_MOUSE_SYSTEM_SCALE");
        int sys = !(e && !strcmp(e, "0"));
        s_scale_set = 1;
        SDL_SetHint(SDL_HINT_MOUSE_RELATIVE_SYSTEM_SCALE, sys ? "1" : "0");
        rt_log("input relative scale %s", sys ? "system" : "raw");
    }
    int start = sdl && !s_pad_started;
    s_pad_started = s_pad_started || start;
    pthread_mutex_unlock(&s_mu);
    if (start) {
        const char *dir = rt_settings_dir();
        pthread_mutex_lock(&s_mu);
        rt_pad_load(&s_padv.pad, dir);
        pthread_mutex_unlock(&s_mu);
        rt_pad_start(dir);
    }
}

uint32_t rt_input_window_id(void) { return s_win ? (uint32_t)SDL_GetWindowID(s_win) : 0; }

int rt_input_main_shown(void)
{
    rt_win *w = s_hwnd ? rt_win_get(s_hwnd) : NULL;
    return w && w->visible && s_win;
}

void rt_input_client_cursor(int32_t *x, int32_t *y)
{
    pthread_mutex_lock(&s_mu);
    *x = S.x;
    *y = S.y;
    pthread_mutex_unlock(&s_mu);
}

void rt_input_event(const SDL_Event *e)
{
    if (!s_win) return;
    uint32_t id = (uint32_t)SDL_GetWindowID(s_win), wid;
    switch (e->type) {
    case SDL_EVENT_MOUSE_MOTION: wid = e->motion.windowID; break;
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP: wid = e->button.windowID; break;
    case SDL_EVENT_MOUSE_WHEEL: wid = e->wheel.windowID; break;
    case SDL_EVENT_KEY_DOWN:
    case SDL_EVENT_KEY_UP: wid = e->key.windowID; break;
    case SDL_EVENT_TEXT_INPUT: wid = e->text.windowID; break;
    default: return;
    }
    if (wid != id) return;
    rt_present_rects r;
    float d;
    rt_input_geometry(&r, &d);
    rt_wmsg m[64];
    pthread_mutex_lock(&s_mu);
    int32_t bx = S.x, by = S.y;
    uint32_t bl = S.looks;
    int n = rt_input_translate(&S, e, &r, d, m, 64);
    if (L.armed && S.relative) {
        L.ax += S.x - bx;
        L.ay += S.y - by;
    }
    if (L.state == 1 && e->type == SDL_EVENT_MOUSE_MOTION && e->motion.timestamp == L.inj && S.looks != bl) {
        L.apply = SDL_GetTicksNS();
        L.state = 2;
    }
    pthread_mutex_unlock(&s_mu);
    post_all(m, n, 64);
}

void rt_input_focus(int on)
{
    rt_wmsg m[RT_KEYIDX + 8];
    int n = 0;
    if (!on && s_win) {
        rt_present_rects r;
        float d;
        rt_input_geometry(&r, &d);
        pthread_mutex_lock(&s_mu);
        n = rt_input_pad_lost(&S, &s_padv, &r, d, m, 64);
        pthread_mutex_unlock(&s_mu);
        post_all(m, n, 64);
        n = 0;
    }
    pthread_mutex_lock(&s_mu);
    if (on) S.focus = 1;
    else n = rt_input_release_all(&S, m, RT_KEYIDX + 8);
    pthread_mutex_unlock(&s_mu);
    post_all(m, n, RT_KEYIDX + 8);
}

static void pad_pump(void)
{
    rt_present_rects r;
    float d;
    rt_wmsg m[128];
    rt_input_geometry(&r, &d);
    pthread_mutex_lock(&s_mu);
    int n = rt_input_pad_pump(&S, &s_padv, S.focus, rt_now_ns(), &r, d, m, 128);
    pthread_mutex_unlock(&s_mu);
    post_all(m, n, 128);
}

void rt_input_pump_end(void)
{
    rt_toggles_apply();
    if (!s_win) return;
    if (s_pad_started) pad_pump();
    rt_wmsg m[1];
    int warp = 0, shape, rel_changed;
    int32_t wx = 0, wy = 0, ox = 0, oy = 0;
    int hf = (SDL_GetWindowFlags(s_win) & SDL_WINDOW_INPUT_FOCUS) != 0;
    pthread_mutex_lock(&s_mu);
    int n = rt_input_flush_move(&S, m, 1);
    L.pumps++;
    S.host_focus = hf;
    if (S.warp_pending) {
        warp = 1;
        wx = S.warp_x;
        wy = S.warp_y;
        S.warp_pending = 0;
        S.warps++;
    }
    rel_changed = rt_input_relative_update(&S);
    int rel = S.relative;
    if (rel_changed && !rel) rt_input_client_point(&S, &ox, &oy);
    shape = rt_input_shape(&S);
    int changed = shape != S.applied;
    S.applied = shape;
    pthread_mutex_unlock(&s_mu);
    post_all(m, n, 1);
    if (rel_changed) {
        if (!rel) {
            rt_present_rects r;
            float d, x, y;
            rt_input_geometry(&r, &d);
            rt_input_unmap(&r, d, ox, oy, &x, &y);
            SDL_WarpMouseInWindow(s_win, x, y);
        }
        if (!SDL_SetWindowRelativeMouseMode(s_win, rel != 0)) rt_log("input relative mode failed: %s", SDL_GetError());
        rt_log("input relative %s", rel ? "on" : "off");
    }
    if (warp) {
        rt_present_rects r;
        float d, x, y;
        rt_input_geometry(&r, &d);
        rt_input_unmap(&r, d, wx, wy, &x, &y);
        SDL_WarpMouseInWindow(s_win, x, y);
    }
    if (changed) {
        if (shape == RT_CUR_HIDDEN) {
            SDL_HideCursor();
        } else {
            if (!s_cursors[shape]) s_cursors[shape] = SDL_CreateSystemCursor((SDL_SystemCursor)shape);
            if (s_cursors[shape]) SDL_SetCursor(s_cursors[shape]);
            SDL_ShowCursor();
        }
        rt_log("cursor %s", rt_input_shape_name(shape));
    }
}

void rt_input_minimise(int on)
{
    if (!s_win) return;
    if (on ? SDL_MinimizeWindow(s_win) : SDL_RestoreWindow(s_win)) rt_log("window %s", on ? "minimised" : "restored");
    else rt_log("window %s failed: %s", on ? "minimise" : "restore", SDL_GetError());
}

void rt_input_taken(const uint32_t m[7])
{
    pthread_mutex_lock(&s_mu);
    rt_input_take(&S, m[1], m[3]);
    pthread_mutex_unlock(&s_mu);
}

void rt_input_get_cursor(int32_t *x, int32_t *y)
{
    pthread_mutex_lock(&s_mu);
    geometry_locked();
    rt_input_state_read_cursor(&S);
    if (L.state == 2 && (S.x != L.rc_x || S.y != L.rc_y)) {
        L.read = SDL_GetTicksNS();
        L.pn_read = L.presents;
        L.state = 3;
    }
    *x = S.x + S.origin_x;
    *y = S.y + S.origin_y;
    pthread_mutex_unlock(&s_mu);
}

void rt_input_set_cursor(int32_t x, int32_t y)
{
    pthread_mutex_lock(&s_mu);
    geometry_locked();
    int32_t cx = x - S.origin_x, cy = y - S.origin_y;
    int rel = S.relative;
    if (rel && L.armed) {
        L.cx += S.read_x - L.rc_x;
        L.cy += S.read_y - L.rc_y;
    }
    L.rc_x = cx;
    L.rc_y = cy;
    int w = rt_input_state_set_cursor(&S, cx, cy);
    static int32_t last_x = INT32_MIN, last_y = INT32_MIN, last_w = -1;
    int same = cx == last_x && cy == last_y && w == last_w;
    last_x = cx;
    last_y = cy;
    last_w = w;
    pthread_mutex_unlock(&s_mu);
    if (rel || same) return;
    if (w) rt_log("cursor warp %d,%d", cx, cy);
    else rt_log("cursor set %d,%d (no warp)", cx, cy);
}

int rt_input_key_down(uint32_t vk)
{
    pthread_mutex_lock(&s_mu);
    int r = rt_input_state_key_down(&S, vk);
    pthread_mutex_unlock(&s_mu);
    return r;
}

uint32_t rt_input_cursor_shape(uint32_t handle)
{
    pthread_mutex_lock(&s_mu);
    uint32_t old = S.cursor;
    S.cursor = handle;
    pthread_mutex_unlock(&s_mu);
    return old;
}

int rt_input_show_cursor(int show)
{
    pthread_mutex_lock(&s_mu);
    int r = S.show_count += show ? 1 : -1;
    pthread_mutex_unlock(&s_mu);
    return r;
}

void rt_input_capture(uint32_t hwnd)
{
    pthread_mutex_lock(&s_mu);
    S.capture = hwnd;
    pthread_mutex_unlock(&s_mu);
}

void rt_input_report(void)
{
    pthread_mutex_lock(&s_mu);
    rt_input_state c = S;
    pthread_mutex_unlock(&s_mu);
    rt_log("input moves %u buttons %u keys %u chars %u dropped-chars %u chords %u warps %u", c.moves, c.nbuttons,
           c.keys, c.chars, c.dropped, c.chords, c.warps);
    rt_log("input relative switches %u recentres %u looks %u", c.rel_switches, c.recentres, c.looks);
    pthread_mutex_lock(&s_mu);
    double rp, rm, pp, pm;
    uint32_t rpf, rmf, ppf, pmf;
    lat_sort(L.rd_ms, L.rd_f, L.n, &rp, &rpf, &rm, &rmf);
    lat_sort(L.pr_ms, L.pr_f, L.n, &pp, &ppf, &pm, &pmf);
    int64_t lx = L.ax - L.cx - (c.x - L.rc_x), ly = L.ay - L.cy - (c.y - L.rc_y);
    uint32_t n = L.n, pumps = L.pumps, presents = L.presents;
    int armed = L.armed;
    pthread_mutex_unlock(&s_mu);
    if (!armed) return;
    rt_log("input latency n %u read p50 %.1fms/%uf max %.1fms/%uf present p50 %.1fms/%uf max %.1fms/%uf lost-px %lld "
           "pumps/present %.2f",
           n, rp, rpf, rm, rmf, pp, ppf, pm, pmf, (long long)(llabs(lx) + llabs(ly)),
           presents ? (double)pumps / presents : 0.0);
}
