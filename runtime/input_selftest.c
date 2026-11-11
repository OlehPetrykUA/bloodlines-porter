#include "input.h"
#include "toggles.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int s_pass, s_fail;

static void check(int ok, const char *name, const char *fmt, ...) __attribute__((format(printf, 3, 4)));

static void check(int ok, const char *name, const char *fmt, ...)
{
    if (ok) {
        printf("PASS %s\n", name);
        s_pass++;
        return;
    }
    char b[512] = "";
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    printf("FAIL %s: %s\n", name, b);
    s_fail++;
}

static const uint16_t k_learn[][2] = {
    {0x04, 0x1E}, {0x05, 0x30}, {0x06, 0x2E}, {0x07, 0x20}, {0x08, 0x12}, {0x09, 0x21}, {0x0A, 0x22}, {0x0B, 0x23},
    {0x0C, 0x17}, {0x0D, 0x24}, {0x0E, 0x25}, {0x0F, 0x26}, {0x10, 0x32}, {0x11, 0x31}, {0x12, 0x18}, {0x13, 0x19},
    {0x14, 0x10}, {0x15, 0x13}, {0x16, 0x1F}, {0x17, 0x14}, {0x18, 0x16}, {0x19, 0x2F}, {0x1A, 0x11}, {0x1B, 0x2D},
    {0x1C, 0x15}, {0x1D, 0x2C}, {0x1E, 0x02}, {0x1F, 0x03}, {0x20, 0x04}, {0x21, 0x05}, {0x22, 0x06}, {0x23, 0x07},
    {0x24, 0x08}, {0x25, 0x09}, {0x26, 0x0A}, {0x27, 0x0B}, {0x28, 0x1C}, {0x29, 0x01}, {0x2A, 0x0E}, {0x2B, 0x0F},
    {0x2C, 0x39}, {0x2D, 0x0C}, {0x2E, 0x0D}, {0x2F, 0x1A}, {0x30, 0x1B}, {0x31, 0x2B}, {0x32, 0x2B}, {0x33, 0x27},
    {0x34, 0x28}, {0x35, 0x29}, {0x36, 0x33}, {0x37, 0x34}, {0x38, 0x35}, {0x39, 0x3A}, {0x3A, 0x3B}, {0x3B, 0x3C},
    {0x3C, 0x3D}, {0x3D, 0x3E}, {0x3E, 0x3F}, {0x3F, 0x40}, {0x40, 0x41}, {0x41, 0x42}, {0x42, 0x43}, {0x43, 0x44},
    {0x44, 0x57}, {0x45, 0x58}, {0x46, 0xE037}, {0x47, 0x46}, {0x48, 0x45}, {0x49, 0xE052}, {0x4A, 0xE047}, {0x4B, 0xE049},
    {0x4C, 0xE053}, {0x4D, 0xE04F}, {0x4E, 0xE051}, {0x4F, 0xE04D}, {0x50, 0xE04B}, {0x51, 0xE050}, {0x52, 0xE048}, {0x53, 0xE045},
    {0x54, 0xE035}, {0x55, 0x37}, {0x56, 0x4A}, {0x57, 0x4E}, {0x58, 0xE01C}, {0x59, 0x4F}, {0x5A, 0x50}, {0x5B, 0x51},
    {0x5C, 0x4B}, {0x5D, 0x4C}, {0x5E, 0x4D}, {0x5F, 0x47}, {0x60, 0x48}, {0x61, 0x49}, {0x62, 0x52}, {0x63, 0x53},
    {0x64, 0x56}, {0x65, 0xE05D}, {0xE0, 0x1D}, {0xE1, 0x2A}, {0xE2, 0x38}, {0xE3, 0xE05B}, {0xE4, 0xE01D}, {0xE5, 0x36},
    {0xE6, 0xE038}, {0xE7, 0xE05C},
};

static const rt_present_rects R1 = {0, 0, 1280, 1024, 0, 0, 1280, 1024};

static rt_wmsg s_m[256];
static int s_n;

static void st(rt_input_state *s)
{
    rt_input_init(s);
    s->host_focus = 1;
    s_n = 0;
}

static void feed(rt_input_state *s, const SDL_Event *e)
{
    s_n += rt_input_translate(s, e, &R1, 1.0f, s_m + s_n, 256 - s_n);
}

static void key(rt_input_state *s, uint32_t sc, int down, uint16_t mod, int repeat)
{
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
    e.key.scancode = (SDL_Scancode)sc;
    e.key.mod = mod;
    e.key.down = down;
    e.key.repeat = repeat;
    feed(s, &e);
}

static void text(rt_input_state *s, const char *t)
{
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = SDL_EVENT_TEXT_INPUT;
    e.text.text = t;
    feed(s, &e);
}

static void button(rt_input_state *s, int b, int down, float x, float y)
{
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = down ? SDL_EVENT_MOUSE_BUTTON_DOWN : SDL_EVENT_MOUSE_BUTTON_UP;
    e.button.button = (Uint8)b;
    e.button.down = down;
    e.button.clicks = 1;
    e.button.x = x;
    e.button.y = y;
    feed(s, &e);
}

static void motion(rt_input_state *s, float x, float y)
{
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = SDL_EVENT_MOUSE_MOTION;
    e.motion.x = x;
    e.motion.y = y;
    feed(s, &e);
}

static int same(const rt_wmsg *want, int n)
{
    if (s_n != n) return 0;
    for (int i = 0; i < n; i++)
        if (s_m[i].msg != want[i].msg || s_m[i].wp != want[i].wp || s_m[i].lp != want[i].lp) return 0;
    return 1;
}

static const char *dump(void)
{
    static char b[400];
    int o = snprintf(b, sizeof b, "%d msgs:", s_n);
    for (int i = 0; i < s_n && o < (int)sizeof b - 40; i++)
        o += snprintf(b + o, sizeof b - (size_t)o, " 0x%X/0x%X/0x%X", s_m[i].msg, s_m[i].wp, s_m[i].lp);
    return b;
}

static void keys(void)
{
    char name[48];
    for (size_t i = 0; i < sizeof k_learn / sizeof k_learn[0]; i++) {
        const rt_key *k = rt_input_key(k_learn[i][0]);
        uint32_t want = k_learn[i][1], got = k ? (k->ext ? 0xE000u : 0) | k->scan : 0xFFFF;
        snprintf(name, sizeof name, "key usage 0x%02X scan 0x%X", k_learn[i][0], want);
        check(got == want, name, "got 0x%X", got);
    }
}

static void lparams(void)
{
    rt_input_state s;
    st(&s);
    key(&s, 0x04, 1, 0, 0);
    check(same((rt_wmsg[]){{WM_KEYDOWN, 0x41, 0x001E0001}}, 1), "lparam key down", "%s", dump());
    s_n = 0;
    key(&s, 0x04, 1, 0, 1);
    check(same((rt_wmsg[]){{WM_KEYDOWN, 0x41, 0x401E0001}}, 1), "lparam repeat sets bit 30", "%s", dump());
    s_n = 0;
    key(&s, 0x04, 0, 0, 0);
    check(same((rt_wmsg[]){{WM_KEYUP, 0x41, 0xC01E0001}}, 1), "lparam key up sets bits 30 and 31", "%s", dump());
    s_n = 0;
    key(&s, 0x52, 1, 0, 0);
    check(same((rt_wmsg[]){{WM_KEYDOWN, 38, 0x01480001}}, 1), "lparam Up has the extended bit 24", "%s", dump());
    s_n = 0;
    key(&s, 0x52, 0, 0, 0);
    check(same((rt_wmsg[]){{WM_KEYUP, 38, 0xC1480001}}, 1), "lparam Up key-up keeps bit 24 with bits 30 and 31", "%s",
          dump());
    st(&s);
    key(&s, 0xE2, 1, SDL_KMOD_LALT, 0);
    key(&s, 0x04, 1, SDL_KMOD_LALT, 0);
    key(&s, 0x04, 0, SDL_KMOD_LALT, 0);
    key(&s, 0xE2, 0, 0, 0);
    check(same((rt_wmsg[]){{WM_SYSKEYDOWN, 18, 0x20380001}, {WM_SYSKEYDOWN, 0x41, 0x201E0001},
                           {WM_SYSKEYUP, 0x41, 0xE01E0001}, {WM_SYSKEYUP, 18, 0xE0380001}}, 4),
          "lparam Alt gives WM_SYSKEY messages with bit 29", "%s", dump());
    st(&s);
    key(&s, 0xE4, 1, 0, 0);
    check(same((rt_wmsg[]){{WM_KEYDOWN, 17, 0x011D0001}}, 1), "lparam right Ctrl is VK_CONTROL with bit 24", "%s", dump());
}

static void buttons(void)
{
    static const struct {
        int b;
        uint32_t down, up, wd, wu;
        const char *name;
    } t[] = {
        {1, WM_LBUTTONDOWN, WM_LBUTTONUP, 0x1, 0x0, "left"},
        {3, WM_RBUTTONDOWN, WM_RBUTTONUP, 0x2, 0x0, "right"},
        {2, WM_MBUTTONDOWN, WM_MBUTTONUP, 0x10, 0x0, "middle"},
        {4, WM_XBUTTONDOWN, WM_XBUTTONUP, 0x10020, 0x10000, "x1"},
        {5, WM_XBUTTONDOWN, WM_XBUTTONUP, 0x20040, 0x20000, "x2"},
    };
    char name[64];
    for (size_t i = 0; i < 5; i++) {
        rt_input_state s;
        st(&s);
        button(&s, t[i].b, 1, 10.5f, 20.5f);
        button(&s, t[i].b, 0, 10.5f, 20.5f);
        snprintf(name, sizeof name, "button %s MK_ flags and XBUTTON high word", t[i].name);
        check(same((rt_wmsg[]){{t[i].down, t[i].wd, 0x0014000A}, {t[i].up, t[i].wu, 0x0014000A}}, 2), name, "%s", dump());
    }
    rt_input_state s;
    st(&s);
    key(&s, 0xE1, 1, SDL_KMOD_LSHIFT, 0);
    key(&s, 0xE0, 1, SDL_KMOD_LSHIFT | SDL_KMOD_LCTRL, 0);
    s_n = 0;
    button(&s, 1, 1, 1, 1);
    check(s_n == 1 && s_m[0].wp == (MK_LBUTTON | MK_SHIFT | MK_CONTROL), "button MK_SHIFT and MK_CONTROL from held keys", "%s",
          dump());
    st(&s);
    button(&s, 1, 1, 1, 1);
    button(&s, 1, 1, 1, 1);
    check(s_n == 1, "button second down without up posts nothing", "%s", dump());
    st(&s);
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = SDL_EVENT_MOUSE_WHEEL;
    e.wheel.integer_y = 1;
    feed(&s, &e);
    e.wheel.integer_y = -1;
    feed(&s, &e);
    check(s_n == 2 && s_m[0].msg == WM_MOUSEWHEEL && (int16_t)(s_m[0].wp >> 16) == 120 && (int16_t)(s_m[1].wp >> 16) == -120,
          "wheel one tick up and down give +120 and -120", "%s", dump());
    st(&s);
    motion(&s, 30.5f, 40.5f);
    button(&s, 1, 1, 30.5f, 40.5f);
    check(same((rt_wmsg[]){{WM_MOUSEMOVE, 0, 0x0028001E}, {WM_LBUTTONDOWN, 1, 0x0028001E}}, 2),
          "pending WM_MOUSEMOVE is posted before the click", "%s", dump());
    st(&s);
    for (int i = 0; i < 50; i++) motion(&s, (float)i, (float)i);
    s_n += rt_input_flush_move(&s, s_m + s_n, 256 - s_n);
    check(s_n == 1 && s.moves == 50 && s_m[0].lp == 0x00310031, "50 motion events give one WM_MOUSEMOVE and moves 50", "%s",
          dump());
}

static void mapping(void)
{
    static const struct {
        rt_present_rects r;
        float d;
        const char *name;
    } t[] = {
        {{0, 0, 1280, 1024, 0, 0, 1280, 1024}, 1.0f, "1280x1024 to 1280x1024 density 1"},
        {{0, 0, 1280, 1024, 0, 0, 2560, 2048}, 2.0f, "1280x1024 to 2560x2048 density 2"},
        {{0, 0, 1280, 1024, 0, 0, 1200, 960}, 1.0f, "1280x1024 to 1200x960 density 1"},
        {{0, 0, 1280, 1024, 0, 0, 640, 512}, 1.0f, "1280x1024 to 640x512 density 1"},
        {{0, 0, 1280, 1024, 100, 50, 1000, 800}, 1.0f, "given source and destination 100,50 1000x800"},
        {{0, 0, 1280, 1024, 562, 0, 2988, 2390}, 2.0f, "side bars at density 2 (1280x1024 fitted in a 4112x2390 drawable)"},
    };
    char name[128];
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        const rt_present_rects *r = &t[i].r;
        float d = t[i].d;
        int32_t x0, y0, x1, y1, xc, yc;
        rt_input_map(r, d, r->dx / d, r->dy / d, &x0, &y0);
        rt_input_map(r, d, (r->dx + r->dw - 0.5f) / d, (r->dy + r->dh - 0.5f) / d, &x1, &y1);
        rt_input_map(r, d, (r->dx + r->dw / 2.0f) / d, (r->dy + r->dh / 2.0f) / d, &xc, &yc);
        snprintf(name, sizeof name, "map %s corners and centre", t[i].name);
        check(x0 == 0 && y0 == 0 && x1 == 1279 && y1 == 1023 && abs(xc - 640) <= 1 && abs(yc - 512) <= 1, name,
              "(%d,%d) (%d,%d) (%d,%d)", x0, y0, x1, y1, xc, yc);
        int bad = 0, bx = 0, by = 0;
        for (int32_t y = 0; y < 1024; y++)
            for (int32_t x = 0; x < 1280; x++) {
                float fx, fy;
                int32_t cx, cy;
                rt_input_unmap(r, d, x, y, &fx, &fy);
                rt_input_map(r, d, fx, fy, &cx, &cy);
                if ((cx != x || cy != y) && !bad++) {
                    bx = x;
                    by = y;
                }
            }
        snprintf(name, sizeof name, "map %s unmap then map returns every pixel", t[i].name);
        check(!bad, name, "%d pixels differ, first (%d,%d)", bad, bx, by);
    }
    {
        int32_t bx0, by0, bx1, by1;
        rt_input_map(&t[5].r, 2.0f, 100, 600, &bx0, &by0);
        rt_input_map(&t[5].r, 2.0f, 2000, 600, &bx1, &by1);
        check(bx0 < 0 && bx1 >= 1280, "a pointer in a side bar maps outside the picture on both sides", "%d %d", bx0, bx1);
    }
    rt_input_state s;
    st(&s);
    motion(&s, -4.5f, -4.5f);
    check(s.x == 0 && s.y == 0, "clamp (-5,-5) to (0,0) with nothing held", "(%d,%d)", s.x, s.y);
    st(&s);
    button(&s, 1, 1, 5, 5);
    motion(&s, -4.5f, -4.5f);
    check(s.x == -5 && s.y == -5, "no clamp while a button is held", "(%d,%d)", s.x, s.y);
    st(&s);
    s.capture = 0x00010004u;
    motion(&s, -4.5f, -4.5f);
    check(s.x == -5 && s.y == -5, "no clamp while capture is set", "(%d,%d)", s.x, s.y);
}

static void chords(void)
{
    rt_input_state s;
    st(&s);
    key(&s, 0xE3, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x04, 1, SDL_KMOD_LGUI, 0);
    text(&s, "a");
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = SDL_EVENT_MOUSE_WHEEL;
    e.wheel.integer_y = 1;
    feed(&s, &e);
    key(&s, 0x04, 0, SDL_KMOD_LGUI, 0);
    key(&s, 0xE3, 0, 0, 0);
    check(s_n == 0 && s.chords == 1, "chord posts no key, character or wheel message", "%s chords %u", dump(), s.chords);
    st(&s);
    key(&s, 0xE3, 1, 0, 0);
    key(&s, 0x2B, 1, 0, 0);
    key(&s, 0x2B, 0, 0, 0);
    key(&s, 0xE3, 0, 0, 0);
    check(s_n == 0, "chord with the own flag and no SDL modifier posts nothing", "%s", dump());
    st(&s);
    key(&s, 0x04, 1, 0, 0);
    key(&s, 0xE3, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x04, 0, SDL_KMOD_LGUI, 0);
    key(&s, 0xE3, 0, 0, 0);
    check(same((rt_wmsg[]){{WM_KEYDOWN, 0x41, 0x001E0001}, {WM_KEYUP, 0x41, 0xC01E0001}}, 2),
          "key held before the chord still gets its key-up", "%s", dump());
    rt_toggles_apply();
    unsigned before = rt_toggle_log_count();
    st(&s);
    key(&s, 0xE3, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x24, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x24, 0, SDL_KMOD_LGUI, 0);
    rt_toggles_apply();
    check(s.chords == 1 && rt_toggle_log_count() == before, "Command-7 counts a chord and toggles nothing", "chords %u", s.chords);
    key(&s, 0x25, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x25, 0, SDL_KMOD_LGUI, 0);
    key(&s, 0xE3, 0, 0, 0);
    rt_toggles_apply();
    check(s.chords == 2 && s_n == 0 && rt_toggle_log_count() == before && rt_toggle_chord(0x25) < 0, "Command-8 counts a chord, posts nothing and toggles nothing",
          "chords %u %s", s.chords, dump());
    st(&s);
    key(&s, 0xE3, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x25, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0x25, 0, SDL_KMOD_LGUI, 0);
    key(&s, 0x25, 1, SDL_KMOD_LGUI, 0);
    key(&s, 0xE3, 0, 0, 0);
    rt_toggles_apply();
    check(s.chords == 2 && s_n == 0 && rt_toggle_log_count() == before, "two Command-8 presses still toggle nothing", "chords %u %s", s.chords, dump());
}

static void texts(void)
{
    rt_input_state s;
    st(&s);
    text(&s, "a\xC3\xA9\xE2\x82\xAC\xD0\xB6");
    check(s_n == 3 && s_m[0].msg == WM_CHAR && s_m[0].wp == 0x61 && s_m[1].wp == 0xE9 && s_m[2].wp == 0x80 && s.dropped == 1,
          "text a, e-acute, euro give 0x61 0xE9 0x80 and zhe is dropped and counted", "%s dropped %u", dump(), s.dropped);
    static const struct {
        uint32_t sc, ch;
        int shift;
        const char *name;
    } t[] = {{0x2A, 0x08, 0, "Backspace"}, {0x2B, 0x09, 0, "Tab"}, {0x28, 0x0D, 0, "Enter"}, {0x28, 0x0A, 1, "Shift+Enter"},
             {0x29, 0x1B, 0, "Escape"}, {0x58, 0x0D, 0, "keypad Enter"}};
    char name[80];
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        st(&s);
        if (t[i].shift) key(&s, 0xE1, 1, SDL_KMOD_LSHIFT, 0);
        s_n = 0;
        uint16_t m = t[i].shift ? SDL_KMOD_LSHIFT : 0;
        key(&s, t[i].sc, 1, m, 0);
        text(&s, t[i].ch == 0x09 ? "\t" : t[i].ch == 0x1B ? "\x1B" : t[i].ch == 0x08 ? "\x08" : "\r");
        key(&s, t[i].sc, 0, m, 0);
        snprintf(name, sizeof name, "%s gives key-down, WM_CHAR 0x%02X, key-up in that order", t[i].name, t[i].ch);
        check(s_n == 3 && s_m[0].msg == WM_KEYDOWN && s_m[1].msg == WM_CHAR && s_m[1].wp == t[i].ch &&
                  s_m[1].lp == s_m[0].lp && s_m[2].msg == WM_KEYUP,
              name, "%s", dump());
    }
    st(&s);
    text(&s, "\r\t\x7F");
    check(s_n == 0, "text event of control characters posts nothing", "%s", dump());
    st(&s);
    key(&s, 0x05, 1, 0, 0);
    text(&s, "B");
    check(s_n == 2 && s_m[1].msg == WM_CHAR && s_m[1].wp == 'B' && s_m[1].lp == s_m[0].lp,
          "text character carries the last key-down lParam", "%s", dump());
    st(&s);
    key(&s, 0x75, 1, 0, 0);
    check(s_n == 0, "unmapped scancode posts nothing", "%s", dump());
}

static void focus(void)
{
    rt_input_state s;
    st(&s);
    key(&s, 0xE1, 1, SDL_KMOD_LSHIFT, 0);
    button(&s, 1, 1, 5, 5);
    s_n = 0;
    s_n += rt_input_release_all(&s, s_m, 256);
    check(s_n == 2 && s_m[0].msg == WM_KEYUP && ((s_m[0].lp >> 16) & 0xFF) == 0x2A && s_m[1].msg == WM_LBUTTONUP && !s.focus,
          "focus lost posts the key-up and button-up of what is held", "%s", dump());
    s_n = 0;
    key(&s, 0xE1, 0, 0, 0);
    button(&s, 1, 0, 5, 5);
    check(s_n == 0, "SDL key-up and button-up after focus loss post nothing", "%s", dump());
    key(&s, 0x04, 1, 0, 0);
    motion(&s, 100, 100);
    text(&s, "a");
    check(s_n == 0 && s.x == 5, "nothing is posted and the cursor stays while unfocused", "%s cursor %d", dump(), s.x);
    st(&s);
    key(&s, 0x52, 1, 0, 0);
    s_n = rt_input_release_all(&s, s_m, 256);
    check(same((rt_wmsg[]){{WM_KEYUP, 38, 0xC1480001}}, 1), "focus lost key-up of a held Up keeps bit 24", "%s", dump());
    st(&s);
    motion(&s, 40.5f, 50.5f);
    s_n = rt_input_release_all(&s, s_m, 256);
    check(s_n == 1 && s_m[0].msg == WM_MOUSEMOVE && !s.move_pending && !rt_input_flush_move(&s, s_m + 1, 255),
          "focus lost flushes a pending move first, so none follows WM_ACTIVATEAPP 0", "%s", dump());
}

static void warps(void)
{
    rt_input_state s;
    st(&s);
    int a = rt_input_state_set_cursor(&s, 100, 100);
    s.focus = 0;
    int b = rt_input_state_set_cursor(&s, 100, 100);
    s.focus = 1;
    int c = rt_input_state_set_cursor(&s, 1280, 10);
    int d = rt_input_state_set_cursor(&s, -1, 10);
    s.host_focus = 0;
    int e = rt_input_state_set_cursor(&s, 100, 100);
    check(a && !b && !c && !d && !e && s.x == 100, "warp pending only when focused and inside the client area",
          "%d %d %d %d %d", a, b, c, d, e);
}

static void keystate(void)
{
    rt_input_state s;
    st(&s);
    key(&s, 0x04, 1, 0, 0);
    int before = rt_input_state_key_down(&s, 0x41);
    rt_input_take(&s, s_m[0].msg, s_m[0].lp);
    int after = rt_input_state_key_down(&s, 0x41);
    s_n = 0;
    key(&s, 0x04, 0, 0, 0);
    int posted_up = rt_input_state_key_down(&s, 0x41);
    rt_input_take(&s, s_m[0].msg, s_m[0].lp);
    int taken_up = rt_input_state_key_down(&s, 0x41);
    check(!before && after && posted_up && !taken_up, "GetKeyState changes when the message is taken, not when posted",
          "%d %d %d %d", before, after, posted_up, taken_up);
    st(&s);
    key(&s, 0xE6, 1, SDL_KMOD_RALT, 0);
    rt_input_take(&s, s_m[0].msg, s_m[0].lp);
    check(rt_input_state_key_down(&s, 165) && rt_input_state_key_down(&s, 18) && !rt_input_state_key_down(&s, 164),
          "GetKeyState VK_RMENU and VK_MENU for right Alt", "%s", dump());
}

static void cursors(void)
{
    rt_input_state s;
    st(&s);
    check(rt_input_shape(&s) == SDL_SYSTEM_CURSOR_DEFAULT, "cursor starts as the default arrow", "%d", rt_input_shape(&s));
    s.cursor = 0;
    check(rt_input_shape(&s) == RT_CUR_HIDDEN, "SetCursor(NULL) hides", "%d", rt_input_shape(&s));
    st(&s);
    s.show_count--;
    check(rt_input_shape(&s) == RT_CUR_HIDDEN, "ShowCursor(FALSE) from 0 hides", "%d", rt_input_shape(&s));
    s.show_count++;
    check(rt_input_shape(&s) == SDL_SYSTEM_CURSOR_DEFAULT, "ShowCursor(TRUE) back to 0 shows", "%d", rt_input_shape(&s));
    static const struct {
        uint32_t idc;
        int shape;
        const char *name;
    } t[] = {
        {32512, SDL_SYSTEM_CURSOR_DEFAULT, "IDC_ARROW"},       {32513, SDL_SYSTEM_CURSOR_TEXT, "IDC_IBEAM"},
        {32514, SDL_SYSTEM_CURSOR_WAIT, "IDC_WAIT"},           {32515, SDL_SYSTEM_CURSOR_CROSSHAIR, "IDC_CROSS"},
        {32642, SDL_SYSTEM_CURSOR_NWSE_RESIZE, "IDC_SIZENWSE"}, {32643, SDL_SYSTEM_CURSOR_NESW_RESIZE, "IDC_SIZENESW"},
        {32644, SDL_SYSTEM_CURSOR_EW_RESIZE, "IDC_SIZEWE"},    {32645, SDL_SYSTEM_CURSOR_NS_RESIZE, "IDC_SIZENS"},
        {32646, SDL_SYSTEM_CURSOR_MOVE, "IDC_SIZEALL"},        {32648, SDL_SYSTEM_CURSOR_NOT_ALLOWED, "IDC_NO"},
        {32649, SDL_SYSTEM_CURSOR_POINTER, "IDC_HAND"},        {32650, SDL_SYSTEM_CURSOR_PROGRESS, "IDC_APPSTARTING"},
        {32651, SDL_SYSTEM_CURSOR_DEFAULT, "IDC_HELP (unlisted)"},
    };
    char name[64];
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        st(&s);
        s.cursor = 0x00030000u + t[i].idc;
        snprintf(name, sizeof name, "cursor %s maps to %s", t[i].name, rt_input_shape_name(t[i].shape));
        check(rt_input_shape(&s) == t[i].shape, name, "%d", rt_input_shape(&s));
    }
}

static void scripts(void)
{
    static const char *const good[] = {"wait menu", "sleep 0.5", "sleep 3", "move 617 438", "down left", "up right",
                                       "click middle", "click x1", "click x2", "wheel -2", "wheel 64", "wheel -64", "key grave", "keydown lshift",
                                       "keyup lshift", "text \"echo \\\"M5\\\" \\\\\"", "chord a", "focus lost",
                                       "focus gained", "capture hover-1_a", "look 0 0", "look -4096 4096", "look 200 -200", "minimise 10", "minimise 0.5", "wait map", "wait map sm_pawnshop_1"};
    char name[96];
    for (size_t i = 0; i < sizeof good / sizeof good[0]; i++) {
        rt_script_cmd c;
        snprintf(name, sizeof name, "script parses '%s'", good[i]);
        check(rt_script_parse_line(good[i], 1, &c) == 0, name, "rejected");
    }
    static const char *const bad[] = {"wait console", "wait \"x\"", "move 1", "move a b", "click foo", "key nosuch",
                                      "text noquote", "text \"open", "capture a/b", "chord lgui", "jump", "sleep -1",
                                      "focus away", "wheel", "wheel 65", "wheel -65", "look 1", "look 4097 0", "look 0 -4097", "look a b", "look 1 2 3", "minimise", "minimise -1", "minimise x", "wait map a/b", "wait map a b", "wait maps"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        rt_script_cmd c;
        snprintf(name, sizeof name, "script rejects '%s'", bad[i]);
        check(rt_script_parse_line(bad[i], 1, &c) < 0, name, "accepted");
    }
    rt_script_cmd *cmds;
    uint32_t n;
    int r = rt_script_parse("wait menu\n# comment\n\nmove 1\nsleep 1\n", &cmds, &n);
    check(r == 4, "script bad line returns its line number", "got %d", r);
    r = rt_script_parse("wait menu\n# comment\n\nkey a\n", &cmds, &n);
    check(r == 0 && n == 2 && cmds[1].line == 4, "script keeps file line numbers", "r %d n %u", r, n);
    free(cmds);
    rt_script_cmd c;
    rt_script_parse_line("text \"a\\\"b\"", 1, &c);
    check(!strcmp(c.text, "a\"b"), "script text unescapes \\\"", "'%s'", c.text);

    rt_script_ctx x = {.window_id = 0, .x = 0, .y = 0, .rects = R1, .density = 1.0f};
    SDL_Event ev[16];
    rt_input_state s;
    st(&s);
    rt_script_parse_line("move 617 438", 1, &c);
    int k = rt_script_events(&c, &x, ev, 16);
    rt_script_parse_line("click left", 2, &c);
    k += rt_script_events(&c, &x, ev + k, 16 - k);
    for (int i = 0; i < k; i++) feed(&s, &ev[i]);
    rt_wmsg scripted[8];
    int ns = s_n;
    memcpy(scripted, s_m, sizeof scripted);
    st(&s);
    motion(&s, 617.5f, 438.5f);
    button(&s, 1, 1, 617.5f, 438.5f);
    button(&s, 1, 0, 617.5f, 438.5f);
    check(ns == 3 && same(scripted, ns), "scripted move and click give the same messages as real ones", "%s", dump());

    st(&s);
    x.mod = 0;
    rt_script_parse_line("chord grave", 1, &c);
    k = rt_script_events(&c, &x, ev, 16);
    for (int i = 0; i < k; i++) feed(&s, &ev[i]);
    check(k == 4 && s_n == 0 && s.chords == 1, "scripted chord posts nothing and counts one chord", "%s", dump());
    st(&s);
    rt_script_parse_line("text \"Ab\xC3\xA9\"", 1, &c);
    k = rt_script_events(&c, &x, ev, 16);
    for (int i = 0; i < k; i++) feed(&s, &ev[i]);
    check(k == 7 && s_n == 7 && s_m[0].msg == WM_KEYDOWN && s_m[1].msg == WM_CHAR && s_m[1].wp == 'A' &&
              s_m[2].msg == WM_KEYUP && s_m[4].wp == 'b' && s_m[6].msg == WM_CHAR && s_m[6].wp == 0xE9,
          "scripted text gives key-down, character, key-up per ASCII character", "%d events %s", k, dump());
    st(&s);
    rt_script_parse_line("text \"x\"", 1, &c);
    k = rt_script_events(&c, &x, ev, 16);
    rt_script_parse_line("text \"y\"", 2, &c);
    k += rt_script_events(&c, &x, ev + k, 16 - k);
    for (int i = 0; i < k; i++) feed(&s, &ev[i]);
    check(s_n == 6 && s_m[1].wp == 'x' && s_m[4].wp == 'y', "two text commands in one step keep their own characters", "%s",
          dump());
    st(&s);
    rt_script_parse_line("keydown lshift", 1, &c);
    k = rt_script_events(&c, &x, ev, 16);
    rt_script_parse_line("key return", 2, &c);
    k += rt_script_events(&c, &x, ev + k, 16 - k);
    for (int i = 0; i < k; i++) feed(&s, &ev[i]);
    check(s_n == 4 && s_m[2].msg == WM_CHAR && s_m[2].wp == 0x0A && (x.mod & SDL_KMOD_LSHIFT),
          "scripted keydown lshift then key return gives WM_CHAR 0x0A", "%s", dump());
}

static void feed_d(rt_input_state *s, const SDL_Event *e, const rt_present_rects *r, float d)
{
    s_n += rt_input_translate(s, e, r, d, s_m + s_n, 256 - s_n);
}

static void rel_motion(rt_input_state *s, float xrel, float yrel, float ax, float ay)
{
    SDL_Event e;
    memset(&e, 0, sizeof e);
    e.type = SDL_EVENT_MOUSE_MOTION;
    e.motion.xrel = xrel;
    e.motion.yrel = yrel;
    e.motion.x = ax;
    e.motion.y = ay;
    feed(s, &e);
}

static void script_look(rt_input_state *s, const char *line, const rt_present_rects *r, float d)
{
    rt_script_cmd c;
    SDL_Event ev[4];
    rt_script_ctx x = {.rects = *r, .density = d, .x = s->x, .y = s->y};
    rt_script_parse_line(line, 1, &c);
    int k = rt_script_events(&c, &x, ev, 4);
    for (int i = 0; i < k; i++) feed_d(s, &ev[i], r, d);
}

static void relative(void)
{
    rt_input_state s;
    static const rt_present_rects R2 = {0, 0, 1280, 1024, 0, 0, 2560, 2048};
    st(&s);
    s.cursor = 0;
    check(rt_input_relative_wanted(&s) && rt_input_relative_update(&s) && s.relative && s.rel_switches == 1 &&
              !rt_input_relative_update(&s) && s.rel_switches == 1,
          "relative turns on once when focused and the cursor is hidden", "%d %u", s.relative, s.rel_switches);
    st(&s);
    check(!rt_input_relative_wanted(&s) && !rt_input_relative_update(&s) && !s.relative,
          "relative stays off while the cursor is shown", "%d", s.relative);
    st(&s);
    s.cursor = 0;
    s.focus = 0;
    check(!rt_input_relative_wanted(&s), "relative is not wanted without focus", "%d", rt_input_relative_wanted(&s));
    st(&s);
    s.show_count = -1;
    check(rt_input_relative_wanted(&s), "ShowCursor(FALSE) wants relative", "%d", rt_input_relative_wanted(&s));

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 640;
    s.y = 512;
    script_look(&s, "look 2000 0", &R1, 1.0f);
    check(s.x == 2640 && s.y == 512 && s.looks == 1, "look 2000 0 from 640,512 gives x 2640, not clamped", "%d,%d",
          s.x, s.y);
    script_look(&s, "look -4096 -4096", &R1, 1.0f);
    check(s.x == -1456 && s.y == -3584, "look goes negative without clamping", "%d,%d", s.x, s.y);

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 640;
    s.y = 512;
    script_look(&s, "look 200 100", &R2, 2.0f);
    check(s.x == 740 && s.y == 562, "look 200 100 at density 2 and 2x present gives 100,50", "%d,%d", s.x, s.y);
    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 640;
    s.y = 512;
    script_look(&s, "look 200 100", &R1, 2.0f);
    check(s.x == 840 && s.y == 612, "look 200 100 at density 2 on a 1x present gives host pixels", "%d,%d", s.x, s.y);
    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 0;
    rel_motion(&s, 100.0f, 0, 5000, 0);
    rel_motion(&s, 100.0f, 0, 5000, 0);
    check(s.x == 200, "relative motion ignores the absolute position", "%d", s.x);

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 640;
    s.y = 512;
    for (int i = 0; i < 100; i++) rel_motion(&s, 0.5f, 0.25f, 0, 0);
    check(s.x == 690 && s.y == 537, "100 motions of 0.5 and 0.25 pixels give 50 and 25", "%d,%d", s.x, s.y);
    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    for (int i = 0; i < 100; i++) rel_motion(&s, -0.5f, 0, 0, 0);
    check(s.x == -50 && s.fx == 0, "fractions accumulate for negative motion too", "%d %f", s.x, s.fx);

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 640;
    s.y = 512;
    rt_script_cmd c;
    rt_script_ctx x = {.rects = R1, .density = 1.0f};
    SDL_Event ev[2];
    rt_script_parse_line("move 617 438", 1, &c);
    int k = rt_script_events(&c, &x, ev, 2);
    for (int i = 0; i < k; i++) feed(&s, &ev[i]);
    check(s.x == 640 && s.y == 512 && s.looks == 0 && s_n == 0, "a scripted move does nothing while relative", "%d,%d %s",
          s.x, s.y, dump());

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    int w = rt_input_state_set_cursor(&s, 640, 512);
    check(!w && !s.warp_pending && s.x == 640 && s.y == 512 && s.recentres == 1,
          "SetCursorPos while relative stores, does not warp, counts a recentre", "%d %d %u", w, s.warp_pending, s.recentres);
    st(&s);
    s.cursor = 0;
    s.x = 640;
    s.y = 512;
    rt_input_relative_update(&s);
    rt_input_state_read_cursor(&s);
    script_look(&s, "look 30 -20", &R1, 1.0f);
    rt_input_state_set_cursor(&s, 640, 512);
    check(s.x == 670 && s.y == 492, "motion pumped between GetCursorPos and SetCursorPos survives the recentre", "%d,%d",
          s.x, s.y);
    rt_input_state_set_cursor(&s, 640, 512);
    check(s.x == 670 && s.y == 492, "a second recentre before any read keeps the same pending motion", "%d,%d", s.x, s.y);
    rt_input_state_read_cursor(&s);
    rt_input_state_set_cursor(&s, 640, 512);
    check(s.x == 640 && s.y == 512, "a recentre right after a read leaves the centre", "%d,%d", s.x, s.y);
    st(&s);
    s.cursor = 0;
    s.x = 5;
    w = rt_input_state_set_cursor(&s, 640, 512);
    check(w && s.warp_pending && s.recentres == 0, "SetCursorPos while not relative still warps", "%d %u", w, s.recentres);

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    key(&s, 0x1A, 1, 0, 0);
    s_n = 0;
    s_n += rt_input_release_all(&s, s_m, 256);
    check(!rt_input_relative_wanted(&s) && rt_input_relative_update(&s) && !s.relative && s.rel_switches == 2,
          "focus loss turns relative off", "%d %u", s.relative, s.rel_switches);
    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    key(&s, 0x1A, 1, 0, 0);
    key(&s, 0x1A, 0, 0, 0);
    check(s_n == 2 && s_m[0].msg == WM_KEYDOWN && s_m[1].msg == WM_KEYUP && s_m[0].lp >> 16 == 0x11,
          "keys still reach the guest while relative", "%s", dump());

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    s.x = 640;
    s.y = 512;
    button(&s, 1, 1, 100.0f, 100.0f);
    check(s.x == 640 && s.y == 512 && s_n == 1 && s_m[0].msg == WM_LBUTTONDOWN && s_m[0].lp == (640u | 512u << 16),
          "a button while relative keeps the guest cursor, not the host position", "%d,%d %s", s.x, s.y, dump());
    st(&s);
    s.x = 640;
    s.y = 512;
    button(&s, 1, 1, 100.0f, 100.0f);
    check(s.x == 100 && s.y == 100, "a button while not relative still places the cursor", "%d,%d", s.x, s.y);
    st(&s);
    int32_t px, py;
    s.x = 2640;
    s.y = -50;
    rt_input_client_point(&s, &px, &py);
    check(px == 1279 && py == 0, "the point restored on leaving relative stays inside the client area", "%d,%d", px, py);

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    rel_motion(&s, 3, 4, 0, 0);
    check(s_n == 0 && s.move_pending, "relative motion queues a mouse move for the pump end", "%d %d", s_n, s.move_pending);
    st(&s);
    s.x = 640;
    s.y = 512;
    script_look(&s, "look 30 -20", &R1, 1.0f);
    check(s.x == 670 && s.y == 492 && s.looks == 0, "look outside relative moves the absolute cursor", "%d,%d", s.x, s.y);
}

static void capture_switch(void)
{
    rt_input_state s;
    st(&s);
    s.cursor = 0;
    check(rt_input_relative_wanted(&s) && rt_input_shape(&s) == RT_CUR_HIDDEN,
          "capture is always on: hidden cursor wants relative", "%d", s.relative);
    rt_input_relative_update(&s);
    s.focus = 0;
    check(!rt_input_relative_wanted(&s) && rt_input_relative_update(&s) && !s.relative,
          "focus loss turns relative off", "%d", s.relative);
    check(rt_input_shape(&s) == SDL_SYSTEM_CURSOR_DEFAULT, "focus loss shows the cursor", "%d", rt_input_shape(&s));
    s.focus = 1;
    check(rt_input_relative_wanted(&s) && rt_input_relative_update(&s) && s.relative && rt_input_shape(&s) == RT_CUR_HIDDEN,
          "focus gain brings the capture back", "%d", s.relative);
    s.host_focus = 0;
    check(!rt_input_relative_wanted(&s) && rt_input_relative_update(&s) && !s.relative && rt_input_shape(&s) == SDL_SYSTEM_CURSOR_DEFAULT,
          "no host key window releases the capture and shows the cursor", "%d", s.relative);
    s.host_focus = 1;
    rt_input_relative_update(&s);
}

static void waitmap(void)
{
    check(rt_script_wait_done(1, 0, "sp_a", ""), "wait map completes on the first ready", "no");
    check(!rt_script_wait_done(0, 0, "?", ""), "wait map waits while nothing is ready", "yes");
    check(!rt_script_wait_done(1, 1, "sp_a", ""), "wait map does not reuse a consumed ready", "yes");
    check(rt_script_wait_done(2, 1, "sp_b", ""), "wait map completes on a newer ready", "no");
    check(rt_script_wait_done(1, 0, "sp_a", "sp_a"), "wait map NAME completes on that name", "no");
    check(!rt_script_wait_done(2, 1, "sp_b", "sp_a"), "wait map NAME does not complete on another name", "yes");
    check(rt_script_wait_done(3, 1, "sp_a", NULL), "wait map with no name pointer completes", "no");
    rt_script_cmd c;
    check(rt_script_parse_line("wait map sm_pawnshop_1", 1, &c) == 0 && c.op == OP_WAITMAP && !strcmp(c.text, "sm_pawnshop_1"),
          "wait map NAME keeps the name", "'%s'", c.text);
    check(rt_script_parse_line("wait map", 1, &c) == 0 && c.op == OP_WAITMAP && !c.text[0], "wait map has no name", "'%s'", c.text);
}

static uint64_t s_pad_t;

static void padpump(rt_input_state *s, rt_pad_dev *d, int ms)
{
    s_pad_t += (uint64_t)ms * 1000000u;
    s_n += rt_input_pad_pump(s, d, s->focus, s_pad_t, &R1, 1.0f, s_m + s_n, 256 - s_n);
}

static int msgs_are(uint32_t msg, uint32_t wp, int n)
{
    return s_n == n && s_m[0].msg == msg && s_m[0].wp == wp;
}

static void pad_detect(void)
{
    static const struct {
        SDL_GamepadType t;
        const char *name;
        int want;
    } c[] = {
        {SDL_GAMEPAD_TYPE_XBOXONE, "DualSense Wireless Controller", RT_PAD_XBOX},
        {SDL_GAMEPAD_TYPE_XBOX360, NULL, RT_PAD_XBOX},
        {SDL_GAMEPAD_TYPE_PS5, "Generic Pad", RT_PAD_PLAYSTATION},
        {SDL_GAMEPAD_TYPE_PS4, "Xbox Wireless Controller", RT_PAD_PLAYSTATION},
        {SDL_GAMEPAD_TYPE_PS3, NULL, RT_PAD_PLAYSTATION},
        {SDL_GAMEPAD_TYPE_STANDARD, "DualSense Wireless Controller", RT_PAD_PLAYSTATION},
        {SDL_GAMEPAD_TYPE_STANDARD, "Wireless DualShock 4", RT_PAD_PLAYSTATION},
        {SDL_GAMEPAD_TYPE_STANDARD, "PlayStation Controller", RT_PAD_PLAYSTATION},
        {SDL_GAMEPAD_TYPE_STANDARD, "Xbox Wireless Controller", RT_PAD_XBOX},
        {SDL_GAMEPAD_TYPE_UNKNOWN, "Generic Pad", RT_PAD_XBOX},
        {SDL_GAMEPAD_TYPE_NINTENDO_SWITCH_PRO, "Pro Controller", RT_PAD_XBOX},
        {SDL_GAMEPAD_TYPE_UNKNOWN, NULL, RT_PAD_XBOX},
    };
    for (size_t i = 0; i < sizeof c / sizeof *c; i++)
        check(rt_pad_detect(c[i].t, c[i].name) == c[i].want, "pad type detection", "case %zu gave %d", i, rt_pad_detect(c[i].t, c[i].name));
    rt_toggles_defaults(0);
    check(rt_pad_kind() == RT_PAD_XBOX, "no pad on auto is Xbox", "%d", rt_pad_kind());
}

static void pad_drive(void)
{
    rt_input_state s;
    rt_pad_dev d;
    if (rt_pad_test_start()) {
        check(0, "virtual pad subsystem starts", "%s", SDL_GetError());
        return;
    }
    rt_toggles_defaults(0);
    st(&s);
    s.cursor = 0;
    s.x = 640;
    s.y = 512;
    rt_input_relative_update(&s);
    rt_pad_dev_init(&d, NULL);
    SDL_JoystickID id = rt_pad_test_attach("Xbox Wireless Controller");
    check(id != 0, "a virtual pad attaches", "%s", SDL_GetError());
    padpump(&s, &d, 10);
    check(s_n == 0 && d.gp && rt_pad_kind() == RT_PAD_XBOX, "the first pad is adopted and sends nothing", "%s kind %d", dump(), rt_pad_kind());

    rt_pad_test_button(id, SDL_GAMEPAD_BUTTON_SOUTH, 1);
    padpump(&s, &d, 10);
    check(msgs_are(WM_KEYDOWN, 32, 1), "south down gives the default key down (space)", "%s", dump());
    s_n = 0;
    rt_pad_test_button(id, SDL_GAMEPAD_BUTTON_SOUTH, 0);
    padpump(&s, &d, 10);
    check(msgs_are(WM_KEYUP, 32, 1), "south up gives the default key up", "%s", dump());

    s_n = 0;
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_LEFTX, 32767);
    padpump(&s, &d, 10);
    check(msgs_are(WM_KEYDOWN, 0x44, 1), "left X 32767 gives D down", "%s", dump());
    s_n = 0;
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_LEFTX, 0);
    padpump(&s, &d, 10);
    check(msgs_are(WM_KEYUP, 0x44, 1), "left X 0 gives D up", "%s", dump());
    s_n = 0;
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_LEFTX, 7000);
    padpump(&s, &d, 10);
    check(s_n == 0, "left X 7000 is inside the dead zone", "%s", dump());

    s_n = 0;
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER, 20000);
    padpump(&s, &d, 10);
    check(msgs_are(WM_LBUTTONDOWN, 1, 1), "right trigger 20000 gives mouse1 down", "%s", dump());
    s_n = 0;
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_RIGHT_TRIGGER, 0);
    padpump(&s, &d, 10);
    check(s_n == 1 && s_m[0].msg == WM_LBUTTONUP, "right trigger back to rest gives mouse1 up", "%s", dump());

    s_n = 0;
    s.x = 640;
    s.y = 512;
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_RIGHTX, 32767);
    for (int i = 0; i < 10; i++) padpump(&s, &d, 10);
    check(s.x >= 639 + 120 && s.x <= 641 + 120 && s.y == 512, "right X full for 10 x 10 ms at 1200 px/s moves 120 px", "%d,%d", s.x, s.y);
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_RIGHTX, 0);

    st(&s);
    s.cursor = 0;
    rt_input_relative_update(&s);
    rt_pad_dev_init(&d, NULL);
    rt_pad_test_button(id, SDL_GAMEPAD_BUTTON_SOUTH, 1);
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_LEFTX, 32767);
    padpump(&s, &d, 10);
    padpump(&s, &d, 10);
    check(s_n == 2 && s_m[0].msg == WM_KEYDOWN && s_m[1].msg == WM_KEYDOWN, "south and left stick right both hold keys", "%s", dump());
    s_n = 0;
    unsigned before = rt_toggle_log_count();
    s_n += rt_input_pad_lost(&s, &d, &R1, 1.0f, s_m + s_n, 256 - s_n);
    check(s_n == 2 && s_m[0].msg == WM_KEYUP && s_m[1].msg == WM_KEYUP, "focus loss releases both pad keys", "%s", dump());
    s_n = 0;
    s_n += rt_input_release_all(&s, s_m, 256);
    check(s_n == 0, "nothing is left for the generic release after the pad's own", "%s", dump());
    check(s.chords == 0 && rt_toggle_log_count() == before, "pad input gives no chord and no toggle", "%u %u", s.chords, rt_toggle_log_count() - before);
    rt_pad_test_button(id, SDL_GAMEPAD_BUTTON_SOUTH, 0);
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_LEFTX, 0);

    st(&s);
    s.cursor = 0;
    rt_pad_dev_init(&d, NULL);
    padpump(&s, &d, 10);
    rt_pad_test_button(id, SDL_GAMEPAD_BUTTON_SOUTH, 1);
    rt_pad_test_axis(id, SDL_GAMEPAD_AXIS_LEFTX, 32767);
    padpump(&s, &d, 10);
    s_n = 0;
    SDL_JoystickID id2 = rt_pad_test_attach("DualSense Wireless Controller");
    rt_pad_test_button(id2, SDL_GAMEPAD_BUTTON_EAST, 1);
    padpump(&s, &d, 10);
    check(s_n == 0 && d.nignored == 1, "a second pad is ignored while the first drives", "%s ignored %d", dump(), d.nignored);
    rt_pad_test_button(id2, SDL_GAMEPAD_BUTTON_EAST, 0);
    padpump(&s, &d, 10);
    s_n = 0;
    rt_pad_test_detach(id);
    padpump(&s, &d, 10);
    check(s_n == 2 && s_m[0].msg == WM_KEYUP && s_m[1].msg == WM_KEYUP, "removing the first pad releases both keys", "%s", dump());
    check(d.gp && d.id == id2 && rt_pad_kind() == RT_PAD_PLAYSTATION, "after removal the second pad drives", "kind %d", rt_pad_kind());
    s_n = 0;
    rt_pad_test_button(id2, SDL_GAMEPAD_BUTTON_EAST, 1);
    padpump(&s, &d, 10);
    check(msgs_are(WM_KEYDOWN, 0x45, 1), "the second pad's east gives the default key (e)", "%s", dump());
    rt_pad_test_detach(id2);
    padpump(&s, &d, 10);
    check(!d.gp && rt_pad_kind() == RT_PAD_XBOX, "no pad falls back to Xbox", "kind %d", rt_pad_kind());

    st(&s);
    s.cursor = 0;
    s.gui = 1;
    rt_pad_dev_init(&d, NULL);
    SDL_JoystickID id3 = rt_pad_test_attach("Generic Pad");
    padpump(&s, &d, 10);
    before = rt_toggle_log_count();
    rt_pad_test_button(id3, SDL_GAMEPAD_BUTTON_DPAD_UP, 1);
    padpump(&s, &d, 10);
    rt_toggles_apply();
    check(s_n == 0 && s.chords == 0 && rt_toggle_log_count() == before, "a pad key while a command key is held sends no chord", "%s", dump());
    rt_pad_test_detach(id3);
}

int rt_input_selftest(void)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    keys();
    lparams();
    buttons();
    mapping();
    chords();
    texts();
    focus();
    warps();
    keystate();
    cursors();
    scripts();
    relative();
    capture_switch();
    waitmap();
    pad_detect();
    pad_drive();
    printf("input selftest: %d PASS, %d FAIL\n", s_pass, s_fail);
    return s_fail ? 1 : 0;
}
