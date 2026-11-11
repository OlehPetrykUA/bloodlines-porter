#include "input.h"
#include "map.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const k_buttons[] = {NULL, "left", "middle", "right", "x1", "x2"};

static int word(const char **p, char *out, size_t n)
{
    while (**p == ' ' || **p == '\t') (*p)++;
    size_t k = 0;
    while (**p && **p != ' ' && **p != '\t') {
        if (k + 1 >= n) return 0;
        out[k++] = *(*p)++;
    }
    out[k] = 0;
    return k > 0;
}

static int end_of(const char *p)
{
    while (*p == ' ' || *p == '\t') p++;
    return !*p;
}

static int int_of(const char *s, long lo, long hi, int32_t *v)
{
    char *e;
    if (!*s || !(isdigit((unsigned char)*s) || (*s == '-' && isdigit((unsigned char)s[1])))) return 0;
    long x = strtol(s, &e, 10);
    if (*e || x < lo || x > hi) return 0;
    *v = (int32_t)x;
    return 1;
}

static int seconds_of(const char *s, int32_t *us)
{
    char *e;
    if (!isdigit((unsigned char)*s)) return 0;
    double x = strtod(s, &e);
    if (*e || x < 0 || x > 2000) return 0;
    *us = (int32_t)(x * 1e6 + 0.5);
    return 1;
}

int rt_script_wait_done(uint32_t ready, uint32_t consumed, const char *last, const char *want)
{
    return ready > consumed && (!want || !*want || !strcmp(last, want));
}

int rt_script_parse_line(const char *line, uint32_t n, rt_script_cmd *c)
{
    memset(c, 0, sizeof *c);
    c->line = n;
    while (*line == ' ' || *line == '\t') line++;
    size_t len = strlen(line);
    while (len && (line[len - 1] == ' ' || line[len - 1] == '\t' || line[len - 1] == '\r' || line[len - 1] == '\n')) len--;
    if (!len || line[0] == '#') return 1;
    if (len >= sizeof c->src) return -1;
    memcpy(c->src, line, len);
    c->src[len] = 0;
    const char *p = c->src;
    char op[16], a[64], b[64];
    if (!word(&p, op, sizeof op)) return -1;
    if (!strcmp(op, "text")) {
        while (*p == ' ' || *p == '\t') p++;
        if (*p++ != '"') return -1;
        size_t k = 0;
        for (;;) {
            if (!*p) return -1;
            if (*p == '"') break;
            if (*p == '\\') {
                p++;
                if (*p != '"' && *p != '\\') return -1;
            }
            if (k + 1 >= sizeof c->text) return -1;
            c->text[k++] = *p++;
        }
        c->text[k] = 0;
        c->op = OP_TEXT;
        return end_of(p + 1) ? 0 : -1;
    }
    if (!strcmp(op, "wait")) {
        const char *q = p;
        if (word(&q, a, sizeof a) && !strcmp(a, "bink")) {
            int32_t movie, frames = 30;
            if (!word(&q, a, sizeof a) || !int_of(a, 1, 4, &movie)) return -1;
            if (word(&q, b, sizeof b) && !int_of(b, 1, 999999, &frames)) return -1;
            if (!end_of(q)) return -1;
            c->op = OP_WAITBINK;
            c->a = movie;
            c->b = frames;
            return 0;
        }
    }
    int na = word(&p, a, sizeof a), nb = na && word(&p, b, sizeof b);
    if (!end_of(p)) return -1;
    if (!strcmp(op, "wait")) {
        if (na && !strcmp(a, "map")) {
            c->op = OP_WAITMAP;
            if (nb) {
                for (const char *q = b; *q; q++)
                    if (!isalnum((unsigned char)*q) && *q != '_') return -1;
                snprintf(c->text, sizeof c->text, "%s", b);
            }
            return 0;
        }
        c->op = OP_WAIT;
        return na && !nb && !strcmp(a, "menu") ? 0 : -1;
    }
    if (!strcmp(op, "sleep")) {
        c->op = OP_SLEEP;
        return na && !nb && seconds_of(a, &c->a) ? 0 : -1;
    }
    if (!strcmp(op, "move")) {
        c->op = OP_MOVE;
        return nb && int_of(a, 0, 32767, &c->a) && int_of(b, 0, 32767, &c->b) ? 0 : -1;
    }
    if (!strcmp(op, "look")) {
        c->op = OP_LOOK;
        return nb && int_of(a, -4096, 4096, &c->a) && int_of(b, -4096, 4096, &c->b) ? 0 : -1;
    }
    if (!strcmp(op, "minimise")) {
        c->op = OP_MINIMISE;
        return na && !nb && seconds_of(a, &c->a) ? 0 : -1;
    }
    if (!strcmp(op, "down") || !strcmp(op, "up") || !strcmp(op, "click")) {
        c->op = op[0] == 'd' ? OP_DOWN : op[0] == 'u' ? OP_UP : OP_CLICK;
        if (!na || nb) return -1;
        for (int i = 1; i <= 5; i++)
            if (!strcmp(a, k_buttons[i])) {
                c->a = i;
                return 0;
            }
        return -1;
    }
    if (!strcmp(op, "wheel")) {
        c->op = OP_WHEEL;
        return na && !nb && int_of(a, -64, 64, &c->a) ? 0 : -1;
    }
    if (!strcmp(op, "key") || !strcmp(op, "keydown") || !strcmp(op, "keyup") || !strcmp(op, "chord")) {
        c->op = !strcmp(op, "key") ? OP_KEY : !strcmp(op, "keydown") ? OP_KEYDOWN : !strcmp(op, "keyup") ? OP_KEYUP : OP_CHORD;
        const rt_key *k = na && !nb ? rt_input_key_named(a) : NULL;
        if (!k || (c->op == OP_CHORD && (k->sdl == SDL_SCANCODE_LGUI || k->sdl == SDL_SCANCODE_RGUI))) return -1;
        c->a = k->sdl;
        return 0;
    }
    if (!strcmp(op, "focus")) {
        c->op = OP_FOCUS;
        if (!na || nb || (strcmp(a, "lost") && strcmp(a, "gained"))) return -1;
        c->a = a[0] == 'g';
        return 0;
    }
    if (!strcmp(op, "capture")) {
        c->op = OP_CAPTURE;
        if (!na || nb) return -1;
        for (const char *q = a; *q; q++)
            if (!isalnum((unsigned char)*q) && *q != '_' && *q != '-') return -1;
        snprintf(c->text, sizeof c->text, "%s", a);
        return 0;
    }
    return -1;
}

int rt_script_parse(const char *text, rt_script_cmd **out, uint32_t *n)
{
    *out = NULL;
    *n = 0;
    uint32_t cap = 0, line = 0;
    const char *p = text;
    while (*p) {
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        char b[512];
        line++;
        rt_script_cmd c;
        int r = len < sizeof b ? (memcpy(b, p, len), b[len] = 0, rt_script_parse_line(b, line, &c)) : -1;
        if (r < 0) {
            free(*out);
            *out = NULL;
            *n = 0;
            return (int)line;
        }
        if (!r) {
            if (*n == cap) {
                cap = cap ? 2 * cap : 64;
                rt_script_cmd *x = realloc(*out, cap * sizeof *x);
                if (!x) rt_fatal(5, "out of host memory");
                *out = x;
            }
            (*out)[(*n)++] = c;
        }
        p = e ? e + 1 : p + len;
    }
    return 0;
}

static uint16_t mod_bit(uint32_t sdl)
{
    switch (sdl) {
    case SDL_SCANCODE_LSHIFT: return SDL_KMOD_LSHIFT;
    case SDL_SCANCODE_RSHIFT: return SDL_KMOD_RSHIFT;
    case SDL_SCANCODE_LCTRL: return SDL_KMOD_LCTRL;
    case SDL_SCANCODE_RCTRL: return SDL_KMOD_RCTRL;
    case SDL_SCANCODE_LALT: return SDL_KMOD_LALT;
    case SDL_SCANCODE_RALT: return SDL_KMOD_RALT;
    case SDL_SCANCODE_LGUI: return SDL_KMOD_LGUI;
    case SDL_SCANCODE_RGUI: return SDL_KMOD_RGUI;
    default: return 0;
    }
}

static uint32_t char_key(int ch)
{
    static const struct {
        const char *chars, *name;
    } rows[] = {{" ", "space"},      {"`~", "grave"},      {"-_", "minus"},         {"=+", "equals"},
                {"[{", "leftbracket"}, {"]}", "rightbracket"}, {"\\|", "backslash"}, {";:", "semicolon"},
                {"'\"", "apostrophe"}, {",<", "comma"},     {".>", "period"},        {"/?", "slash"},
                {")", "0"}, {"!", "1"}, {"@", "2"}, {"#", "3"}, {"$", "4"}, {"%", "5"}, {"^", "6"}, {"&", "7"},
                {"*", "8"}, {"(", "9"}};
    char name[2] = {(char)tolower(ch), 0};
    const rt_key *k = isalnum(ch) ? rt_input_key_named(name) : NULL;
    for (size_t i = 0; !k && i < sizeof rows / sizeof rows[0]; i++)
        if (strchr(rows[i].chars, ch)) k = rt_input_key_named(rows[i].name);
    return k ? k->sdl : 0;
}

static void key_event(SDL_Event *e, uint32_t id, uint32_t sdl, int down, uint16_t mod)
{
    memset(e, 0, sizeof *e);
    e->type = down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
    e->key.windowID = id;
    e->key.scancode = (SDL_Scancode)sdl;
    e->key.mod = mod;
    e->key.down = down;
}

static void button_event(SDL_Event *e, rt_script_ctx *x, int b, int down)
{
    float fx, fy;
    rt_input_unmap(&x->rects, x->density, x->x, x->y, &fx, &fy);
    memset(e, 0, sizeof *e);
    e->type = down ? SDL_EVENT_MOUSE_BUTTON_DOWN : SDL_EVENT_MOUSE_BUTTON_UP;
    e->button.windowID = x->window_id;
    e->button.button = (Uint8)b;
    e->button.down = down;
    e->button.clicks = 1;
    e->button.x = fx;
    e->button.y = fy;
}

int rt_script_events(const rt_script_cmd *c, rt_script_ctx *x, SDL_Event *out, int cap)
{
    static char pool[256][8];
    static uint32_t next;
    int n = 0;
#define NEXT (n < cap ? &out[n++] : &out[cap - 1])
    switch (c->op) {
    case OP_MOVE: {
        SDL_Event *e = NEXT;
        float fx, fy;
        x->x = c->a;
        x->y = c->b;
        rt_input_unmap(&x->rects, x->density, x->x, x->y, &fx, &fy);
        memset(e, 0, sizeof *e);
        e->type = SDL_EVENT_MOUSE_MOTION;
        e->motion.windowID = x->window_id;
        e->motion.x = fx;
        e->motion.y = fy;
        break;
    }
    case OP_LOOK: {
        SDL_Event *e = NEXT;
        float fx, fy, rho = x->density > 0 ? x->density : 1.0f;
        rt_input_unmap(&x->rects, x->density, x->x, x->y, &fx, &fy);
        memset(e, 0, sizeof *e);
        e->type = SDL_EVENT_MOUSE_MOTION;
        e->motion.windowID = x->window_id;
        e->motion.xrel = (float)c->a / rho;
        e->motion.yrel = (float)c->b / rho;
        e->motion.x = fx + e->motion.xrel;
        e->motion.y = fy + e->motion.yrel;
        e->motion.timestamp = SDL_GetTicksNS();
        rt_input_lat_inject(e->motion.timestamp);
        break;
    }
    case OP_DOWN: button_event(NEXT, x, c->a, 1); break;
    case OP_UP: button_event(NEXT, x, c->a, 0); break;
    case OP_CLICK:
        button_event(NEXT, x, c->a, 1);
        button_event(NEXT, x, c->a, 0);
        break;
    case OP_WHEEL: {
        SDL_Event *e = NEXT;
        float fx, fy;
        rt_input_unmap(&x->rects, x->density, x->x, x->y, &fx, &fy);
        memset(e, 0, sizeof *e);
        e->type = SDL_EVENT_MOUSE_WHEEL;
        e->wheel.windowID = x->window_id;
        e->wheel.y = (float)c->a;
        e->wheel.integer_y = c->a;
        e->wheel.mouse_x = fx;
        e->wheel.mouse_y = fy;
        break;
    }
    case OP_KEY:
        key_event(NEXT, x->window_id, (uint32_t)c->a, 1, x->mod);
        key_event(NEXT, x->window_id, (uint32_t)c->a, 0, x->mod);
        break;
    case OP_KEYDOWN:
        x->mod |= mod_bit((uint32_t)c->a);
        key_event(NEXT, x->window_id, (uint32_t)c->a, 1, x->mod);
        break;
    case OP_KEYUP:
        x->mod &= (uint16_t)~mod_bit((uint32_t)c->a);
        key_event(NEXT, x->window_id, (uint32_t)c->a, 0, x->mod);
        break;
    case OP_CHORD: {
        uint16_t m = x->mod | SDL_KMOD_LGUI;
        key_event(NEXT, x->window_id, SDL_SCANCODE_LGUI, 1, m);
        key_event(NEXT, x->window_id, (uint32_t)c->a, 1, m);
        key_event(NEXT, x->window_id, (uint32_t)c->a, 0, m);
        key_event(NEXT, x->window_id, SDL_SCANCODE_LGUI, 0, x->mod);
        break;
    }
    case OP_TEXT: {
        for (const unsigned char *p = (const unsigned char *)c->text; *p;) {
            char *t = pool[next++ % 256u];
            const unsigned char *s = p;
            p++;
            while ((*p & 0xC0) == 0x80) p++;
            size_t len = (size_t)(p - s) < sizeof pool[0] ? (size_t)(p - s) : sizeof pool[0] - 1;
            memcpy(t, s, len);
            t[len] = 0;
            uint32_t k = len == 1 && *s >= 0x20 && *s < 0x7F ? char_key(*s) : 0;
            if (k) key_event(NEXT, x->window_id, k, 1, x->mod);
            SDL_Event *e = NEXT;
            memset(e, 0, sizeof *e);
            e->type = SDL_EVENT_TEXT_INPUT;
            e->text.windowID = x->window_id;
            e->text.text = t;
            if (k) key_event(NEXT, x->window_id, k, 0, x->mod);
        }
        break;
    }
    case OP_FOCUS: {
        SDL_Event *e = NEXT;
        memset(e, 0, sizeof *e);
        e->type = c->a ? SDL_EVENT_WINDOW_FOCUS_GAINED : SDL_EVENT_WINDOW_FOCUS_LOST;
        e->window.windowID = x->window_id;
        break;
    }
    default: break;
    }
#undef NEXT
    return n;
}

static rt_script_cmd *s_cmds;
static uint32_t s_n, s_pos;
static int s_started, s_done, s_entered, s_minimised;
static uint32_t s_consumed;
static uint64_t s_due, s_cap_since;
static rt_script_ctx s_ctx;

int rt_input_script_load(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    size_t cap = 1 << 16, len = 0;
    char *b = malloc(cap + 1);
    if (!b) rt_fatal(5, "out of host memory");
    size_t k;
    while ((k = fread(b + len, 1, cap - len, f)) > 0) {
        len += k;
        if (len == cap) {
            char *x = realloc(b, 2 * cap + 1);
            if (!x) rt_fatal(5, "out of host memory");
            b = x;
            cap *= 2;
        }
    }
    fclose(f);
    b[len] = 0;
    int bad = rt_script_parse(b, &s_cmds, &s_n);
    free(b);
    return bad;
}

void rt_input_script_step(void)
{
    if (!s_cmds || s_done) return;
    uint64_t now = rt_now_ns();
    if (!s_started) {
        if (!rt_input_main_shown()) return;
        s_started = 1;
        s_due = now;
    }
    int synced = 0;
    while (s_pos < s_n) {
        rt_script_cmd *c = &s_cmds[s_pos];
        if (now < s_due) return;
        if (!s_entered) {
            s_entered = 1;
            rt_log("script %u %s", c->line, c->src);
        }
        if (c->op == OP_WAIT) {
            if (!rt_stage11_ns()) return;
            if (s_due < now) s_due = now;
        } else if (c->op == OP_WAITBINK) {
            if (rt_bink_shown((uint32_t)c->a) < (uint32_t)c->b) return;
            if (s_due < now) s_due = now;
        } else if (c->op == OP_WAITMAP) {
            char last[64];
            uint32_t ready = rt_map_ready(last, sizeof last);
            if (!rt_script_wait_done(ready, s_consumed, last, c->text)) return;
            s_consumed = ready;
            if (s_due < now) s_due = now;
        } else if (c->op == OP_SLEEP) {
            s_due += (uint64_t)c->a * 1000u;
        } else if (c->op == OP_MINIMISE) {
            if (!s_minimised) {
                s_minimised = 1;
                s_due = now + (uint64_t)c->a * 1000u;
                rt_input_minimise(1);
                return;
            }
            s_minimised = 0;
            rt_input_minimise(0);
        } else if (c->op == OP_CAPTURE) {
            if (!s_cap_since) {
                s_cap_since = now;
                rt_gfx_capture_named(c->text);
                return;
            }
            if (rt_gfx_capture_pending() && now - s_cap_since < 5000000000ull) return;
            if (rt_gfx_capture_pending()) rt_log("script %u capture %s not taken in 5 s", c->line, c->text);
            s_cap_since = 0;
            if (s_due < now) s_due = now;
        } else {
            if (!synced) {
                synced = 1;
                s_ctx.window_id = rt_input_window_id();
                rt_input_client_cursor(&s_ctx.x, &s_ctx.y);
                rt_input_geometry(&s_ctx.rects, &s_ctx.density);
            }
            SDL_Event ev[260];
            int k = rt_script_events(c, &s_ctx, ev, 260);
            for (int i = 0; i < k; i++)
                if (!SDL_PushEvent(&ev[i])) rt_log("script %u event not queued: %s", c->line, SDL_GetError());
        }
        s_pos++;
        s_entered = 0;
    }
    s_done = 1;
    rt_log("script done");
}
