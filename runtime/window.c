#include "recomp_types.h"
#include "rt.h"
#include "fx.h"
#include "toggles.h"
#include "winstate.h"

#include <SDL3/SDL.h>
#include <dlfcn.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define MAX_WIN 256
#define QLEN 4096

static int s_sdl;
static rt_win *s_wins[MAX_WIN];
static uint32_t s_q[QLEN][7];
static uint32_t s_qhead, s_qn;
static _Atomic uint64_t s_last_pump;
static uint32_t s_main_hwnd;
static uint32_t s_main_id;
static int s_desk_w = 1280, s_desk_h = 720;
static int s_display = -1;
static uint64_t s_pump_ns;
static rt_winstate s_ws;

void rt_window_set_display(int n) { s_display = n; }

static int s_ws_forget;

void rt_window_settings_clear(void)
{
    rt_ws_clear(&s_ws);
    s_ws_forget = 0;
}

void rt_window_forget_saved(void) { s_ws_forget = 1; }

int rt_window_settings_parse(const char *name, const char *value) { return rt_ws_parse(&s_ws, name, value); }

void rt_window_settings_write(FILE *f)
{
    rt_winstate w = s_ws;
    if (s_ws_forget) w.have = 0;
    rt_ws_write(&w, f);
}

static uint32_t now_ms(void) { return (uint32_t)(rt_now_ns() / 1000000u); }

int rt_window_launch_full(void) { return s_ws.fullscreen; }

int rt_window_init(void)
{
    SDL_SetHint(SDL_HINT_VIDEO_MAC_FULLSCREEN_SPACES, "1");
    SDL_SetHint(SDL_HINT_VIDEO_METAL_AUTO_RESIZE_DRAWABLE, "0");
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        rt_log("SDL_Init(SDL_INIT_VIDEO) failed: %s", SDL_GetError());
        return -1;
    }
    s_sdl = 1;
    s_last_pump = rt_now_ns();
    if (s_display >= 0) {
        int n = 0;
        SDL_free(SDL_GetDisplays(&n));
        if (s_display >= n) {
            rt_log("display %d out of range: %d display(s) connected", s_display, n);
            return -2;
        }
    }
    const SDL_DisplayMode *m = SDL_GetDesktopDisplayMode(rt_window_display());
    if (m) {
        s_desk_w = m->w;
        s_desk_h = m->h;
    }
    return 0;
}

void rt_desktop_size(int *w, int *h)
{
    *w = s_desk_w;
    *h = s_desk_h;
}

static SDL_Rect s_usable_fake;
static int s_title_fake = -1;

void rt_desktop_usable_set(int dw, int dh, int w, int h, int title)
{
    if (dw > 0 && dh > 0) {
        s_desk_w = dw;
        s_desk_h = dh;
    }
    s_usable_fake = (SDL_Rect){0, 0, w, h};
    s_title_fake = title;
}

static uint32_t s_display_fake;

void rt_window_display_set(uint32_t id) { s_display_fake = id; }

uint32_t rt_window_display(void)
{
    if (s_display_fake) return s_display_fake;
    int n = 0;
    SDL_DisplayID *ids = SDL_GetDisplays(&n);
    SDL_DisplayID id = ids && s_display >= 0 && s_display < n ? ids[s_display] : SDL_GetPrimaryDisplay();
    SDL_free(ids);
    return id;
}

typedef struct {
    double x, y, w, h;
} ns_rect;

static int title_bar(void)
{
    if (s_title_fake >= 0) return s_title_fake;
    void *send = dlsym(RTLD_DEFAULT, "objc_msgSend");
    void *(*cls)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "objc_getClass");
    void *(*sel)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "sel_registerName");
    void *nsw = cls ? cls("NSWindow") : NULL;
    if (!send || !sel || !nsw) return 0;
    ns_rect c = {0, 0, 640, 480};
    ns_rect f = ((ns_rect (*)(void *, void *, ns_rect, unsigned long))send)(nsw, sel("frameRectForContentRect:styleMask:"), c, 7ul);
    return f.h > c.h ? (int)ceil(f.h - c.h) : 0;
}

int rt_desktop_usable(int *x, int *y, int *w, int *h)
{
    SDL_Rect r = s_usable_fake;
    if (r.w <= 0 && !(s_sdl && SDL_GetDisplayUsableBounds(rt_window_display(), &r))) return 0;
    int top = title_bar();
    *x = r.x;
    *y = r.y + top;
    *w = r.w;
    *h = r.h - top;
    return *w > 0 && *h > 0;
}

#define DISP_MAX 16

static int usable_list(rt_wrect *out)
{
    int n = 0, k = 0, top = title_bar();
    SDL_DisplayID *ids = SDL_GetDisplays(&n);
    SDL_DisplayID cur = rt_window_display();
    for (int pass = 0; pass < 2; pass++)
        for (int i = 0; ids && i < n && k < DISP_MAX; i++) {
            SDL_Rect b;
            if ((ids[i] == cur) != (pass == 0) || !SDL_GetDisplayUsableBounds(ids[i], &b)) continue;
            out[k++] = (rt_wrect){b.x, b.y + top, b.w, b.h - top};
        }
    SDL_free(ids);
    return k;
}

static void restore_geometry(void *sw)
{
    rt_wrect d[DISP_MAX];
    int n = usable_list(d), placed;
    rt_wrect o = rt_ws_restore(&s_ws.rect, d, n, &placed);
    SDL_SetWindowSize(sw, o.w, o.h);
    if (n) SDL_SetWindowPosition(sw, o.x, o.y);
    rt_log("window restored from settings to %d,%d %dx%d (saved %d,%d %dx%d, %s)", o.x, o.y, o.w, o.h, s_ws.rect.x, s_ws.rect.y,
           s_ws.rect.w, s_ws.rect.h, placed ? "its display" : "centred, its display is gone");
}

void rt_window_place(void *sw, int cw, int ch)
{
    int x, y, w, h;
    if (sw && s_ws.have && !s_ws.live && s_display < 0) {
        restore_geometry(sw);
        return;
    }
    if (!RT_ENGINE_OVERRIDE || !sw || !rt_desktop_usable(&x, &y, &w, &h)) return;
    if (cw <= 0 || ch <= 0 || cw > w || ch > h) return;
    SDL_SetWindowPosition(sw, x + (w - cw) / 2, y + (h - ch) / 2);
    rt_log("window %dx%d centred in the usable client area %dx%d at %d,%d, title bar %d pt", cw, ch, w, h, x, y, title_bar());
}

rt_win *rt_win_new(void)
{
    for (int i = 1; i < MAX_WIN; i++)
        if (!s_wins[i]) {
            rt_win *w = calloc(1, sizeof *w);
            if (!w) rt_fatal(5, "out of host memory");
            w->hwnd = 0x00010000u + 4u * (uint32_t)i;
            s_wins[i] = w;
            return w;
        }
    rt_fatal(5, "window table full");
}

rt_win *rt_win_get(uint32_t hwnd)
{
    if ((hwnd & 3u) || hwnd < 0x00010004u) return NULL;
    uint32_t i = (hwnd - 0x00010000u) / 4u;
    return i < MAX_WIN ? s_wins[i] : NULL;
}

void rt_win_free(rt_win *w)
{
    uint32_t i = (w->hwnd - 0x00010000u) / 4u;
    if (w->hwnd == s_main_hwnd) {
        if (w->sdl) SDL_StopTextInput(w->sdl);
        rt_input_main_window(0, NULL);
        rt_gfx_window_gone();
    }
    if (w->sdl && rt_gfx_mode() == RT_GFX_DXVK && w->sdl == rt_gfx_device_window()) {
        SDL_HideWindow(w->sdl);
        rt_log("window 0x%08X destroyed by the guest; its SDL window stays alive, hidden, for the dxvk swapchain", w->hwnd);
    } else if (w->sdl) {
        SDL_DestroyWindow(w->sdl);
    }
    if (w->hwnd == s_main_hwnd) {
        s_main_hwnd = 0;
        __atomic_store_n(&s_main_id, 0, __ATOMIC_RELEASE);
    }
    s_wins[i] = NULL;
    free(w);
}

static long window_number(SDL_Window *sw)
{
    void *ns = SDL_GetPointerProperty(SDL_GetWindowProperties(sw), SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, NULL);
    void *send = dlsym(RTLD_DEFAULT, "objc_msgSend");
    void *(*sel)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "sel_registerName");
    if (!ns || !send || !sel) return -1;
    return ((long (*)(void *, void *))send)(ns, sel("windowNumber"));
}

static void *cocoa_window(SDL_Window *sw)
{
    return SDL_GetPointerProperty(SDL_GetWindowProperties(sw), SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, NULL);
}

unsigned long rt_window_collection_behavior(void *sw)
{
    void *ns = cocoa_window(sw);
    void *send = dlsym(RTLD_DEFAULT, "objc_msgSend");
    void *(*sel)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "sel_registerName");
    if (!ns || !send || !sel) return 0;
    return ((unsigned long (*)(void *, void *))send)(ns, sel("collectionBehavior"));
}

static void set_minimum(void *sw, int cw, int ch, int *mw, int *mh)
{
    rt_win_minimum(cw, ch, mw, mh);
    if (!SDL_SetWindowMinimumSize(sw, *mw, *mh)) rt_log("SDL_SetWindowMinimumSize failed: %s", SDL_GetError());
}

void rt_window_configure(void *sw, int cw, int ch)
{
    int mw, mh;
    set_minimum(sw, cw, ch, &mw, &mh);
    if (!SDL_SetWindowResizable(sw, true)) rt_log("SDL_SetWindowResizable failed: %s", SDL_GetError());
    void *ns = cocoa_window(sw);
    void *send = dlsym(RTLD_DEFAULT, "objc_msgSend");
    void *(*sel)(const char *) = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "sel_registerName");
    if (ns && send && sel) {
        unsigned long cur = rt_window_collection_behavior(sw);
        ((void (*)(void *, void *, unsigned long))send)(ns, sel("setCollectionBehavior:"), rt_win_collection(cur));
    }
    rt_log("window resizable, minimum %dx%d points, full-screen capable (collection behavior 0x%lX)", mw, mh, rt_window_collection_behavior(sw));
}

void rt_win_sdl(rt_win *w)
{
    if (!s_sdl) return;
    SDL_WindowFlags flags = SDL_WINDOW_HIDDEN | (SDL_WindowFlags)rt_gfx_window_flags();
    SDL_Window *sw = SDL_CreateWindow(w->title, w->w, w->h, flags);
    if (!sw) {
        rt_log("SDL_CreateWindow failed: %s", SDL_GetError());
        return;
    }
    w->sdl = sw;
    if (!s_main_hwnd) {
        s_main_hwnd = w->hwnd;
        __atomic_store_n(&s_main_id, (uint32_t)SDL_GetWindowID(sw), __ATOMIC_RELEASE);
        rt_window_configure(sw, w->w, w->h);
        rt_input_main_window(w->hwnd, sw);
        int n = 0;
        SDL_DisplayID *ids = SDL_GetDisplays(&n);
        int k = s_display >= 0 && s_display < n ? s_display : 0;
        SDL_DisplayID id = ids && n ? ids[k] : SDL_GetPrimaryDisplay();
        if (s_display >= 0) SDL_SetWindowPosition(sw, (int)SDL_WINDOWPOS_CENTERED_DISPLAY(id), (int)SDL_WINDOWPOS_CENTERED_DISPLAY(id));
        rt_window_place(sw, w->w, w->h);
        SDL_DisplayID on = SDL_GetDisplayForWindow(sw);
        for (int j = 0; ids && j < n; j++)
            if (ids[j] == on) k = j;
        SDL_free(ids);
        rt_log("window 0x%08X created %dx%d vulkan %d", w->hwnd, w->w, w->h, (flags & SDL_WINDOW_VULKAN) != 0);
        rt_log("display %d \"%s\" scale %.2f density %.2f", k, SDL_GetDisplayName(on ? on : id), SDL_GetWindowDisplayScale(sw),
               SDL_GetWindowPixelDensity(sw));
    }
    s_last_pump = rt_now_ns();
}

void rt_win_resize(rt_win *w, int cw, int ch)
{
    w->w = cw;
    w->h = ch;
    int full = w->sdl && (SDL_GetWindowFlags(w->sdl) & SDL_WINDOW_FULLSCREEN);
    if (w->sdl && !full && w->hwnd == s_main_hwnd) {
        int mw, mh;
        set_minimum(w->sdl, cw, ch, &mw, &mh);
    }
    if (w->sdl && !full) SDL_SetWindowSize(w->sdl, cw, ch);
    if (w->sdl && !full && w->hwnd == s_main_hwnd) rt_window_place(w->sdl, cw, ch);
    s_last_pump = rt_now_ns();
}

void rt_win_show(rt_win *w, int show)
{
    w->visible = show;
    if (!w->sdl) return;
    if (show) {
        SDL_ShowWindow(w->sdl);
        static int text;
        if (!text && w->hwnd == s_main_hwnd) {
            text = 1;
            if (!SDL_StartTextInput(w->sdl)) rt_log("SDL_StartTextInput failed: %s", SDL_GetError());
        }
        static int logged;
        if (!logged) {
            logged = 1;
            rt_stage(6, "%dx%d", w->w, w->h);
            SDL_PumpEvents();
            int x = 0, y = 0;
            SDL_GetWindowPosition(w->sdl, &x, &y);
            rt_log("window id %ld", window_number(w->sdl));
            rt_log("window frame %d,%d,%d,%d", x, y, w->w, w->h);
        }
    } else {
        SDL_HideWindow(w->sdl);
    }
    s_last_pump = rt_now_ns();
}

void rt_msg_post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp)
{
    if (s_qn == QLEN) {
        rt_log("message queue full, dropped 0x%X", msg);
        return;
    }
    uint32_t *m = s_q[(s_qhead + s_qn++) % QLEN];
    m[0] = hwnd;
    m[1] = msg;
    m[2] = wp;
    m[3] = lp;
    m[4] = (uint32_t)(rt_now_ns() / 1000000u);
    m[5] = m[6] = 0;
}

int rt_msg_take(uint32_t hwnd, uint32_t lo, uint32_t hi, int remove, uint32_t out[7])
{
    for (uint32_t k = 0; k < s_qn; k++) {
        uint32_t *m = s_q[(s_qhead + k) % QLEN];
        if (hwnd && hwnd != 0xFFFFFFFFu && m[0] != hwnd) continue;
        if (hwnd == 0xFFFFFFFFu && m[0]) continue;
        if ((lo || hi) && (m[1] < lo || m[1] > hi)) continue;
        memcpy(out, m, 7 * sizeof *m);
        if (remove) {
            for (uint32_t j = k; j + 1 < s_qn; j++) memcpy(s_q[(s_qhead + j) % QLEN], s_q[(s_qhead + j + 1) % QLEN], 28);
            s_qn--;
            rt_input_taken(out);
        }
        return 1;
    }
    return 0;
}

static rt_win *by_sdl(SDL_WindowID id)
{
    for (int i = 1; i < MAX_WIN; i++)
        if (s_wins[i] && s_wins[i]->sdl && SDL_GetWindowID(s_wins[i]->sdl) == id) return s_wins[i];
    return NULL;
}

static rt_wrect window_rect(SDL_Window *sw)
{
    rt_wrect r = {0, 0, 0, 0};
    SDL_GetWindowPosition(sw, &r.x, &r.y);
    SDL_GetWindowSize(sw, &r.w, &r.h);
    return r;
}

static void window_display_log(SDL_Window *sw)
{
    int pw = 0, ph = 0, w = 0, h = 0;
    SDL_GetWindowSizeInPixels(sw, &pw, &ph);
    SDL_GetWindowSize(sw, &w, &h);
    SDL_DisplayID id = SDL_GetDisplayForWindow(sw);
    const char *name = id ? SDL_GetDisplayName(id) : NULL;
    rt_log("window display \"%s\" scale %.2f density %.2f, %dx%d points, %dx%d pixels", name ? name : "?", SDL_GetWindowDisplayScale(sw),
           SDL_GetWindowPixelDensity(sw), w, h, pw, ph);
}

static void window_flush(void)
{
    if (!s_ws.dirty) return;
    s_ws.dirty = 0;
    rt_settings_save();
}

static void window_tick(void)
{
    rt_win *w = s_main_hwnd ? rt_win_get(s_main_hwnd) : NULL;
    if (!w || !w->sdl) return;
    uint32_t ms = now_ms();
    rt_present_rects pr;
    if (!s_ws.live && rt_gfx_present_rects(&pr)) {
        rt_wrect cur = window_rect(w->sdl);
        rt_ws_live(&s_ws, ms, cur);
        if (s_ws.fullscreen && !SDL_SetWindowFullscreen(w->sdl, true)) {
            rt_log("SDL_SetWindowFullscreen failed: %s", SDL_GetError());
            s_ws.fullscreen = 0;
        }
        rt_log("window state live at %d,%d %dx%d, full screen %d", cur.x, cur.y, cur.w, cur.h, s_ws.fullscreen);
    }
    if (rt_ws_due(&s_ws, ms)) window_flush();
}

int rt_pump_due(void) { return s_sdl && rt_now_ns() - s_last_pump >= 16000000u; }

void rt_pump(int force)
{
    (void)force;
    if (!s_sdl || !rt_is_primary()) return;
    uint64_t now = rt_now_ns();
    if (s_main_hwnd) rt_note_pump_gap(now - s_last_pump);
    s_last_pump = now;
    SDL_PumpEvents();
    rt_input_script_step();
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        rt_note_event();
        rt_win *w = e.type >= SDL_EVENT_WINDOW_FIRST && e.type <= SDL_EVENT_WINDOW_LAST ? by_sdl(e.window.windowID) : NULL;
        switch (e.type) {
        case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
            if (w) {
                window_flush();
                rt_close_requested();
                rt_input_post(w->hwnd, 0x0010, 0, 0);
            }
            break;
        case SDL_EVENT_QUIT:
            if (s_main_hwnd) {
                window_flush();
                rt_close_requested();
                rt_input_post(s_main_hwnd, 0x0010, 0, 0);
            }
            break;
        case SDL_EVENT_WINDOW_MOVED:
        case SDL_EVENT_WINDOW_RESIZED:
            if (w && w->hwnd == s_main_hwnd) rt_ws_note(&s_ws, now_ms(), window_rect(w->sdl));
            break;
        case SDL_EVENT_WINDOW_ENTER_FULLSCREEN:
            if (w && w->hwnd == s_main_hwnd) {
                rt_ws_enter_fs(&s_ws, now_ms());
                rt_log("window full screen on, windowed %d,%d %dx%d", s_ws.rect.x, s_ws.rect.y, s_ws.rect.w, s_ws.rect.h);
                window_display_log(w->sdl);
            }
            break;
        case SDL_EVENT_WINDOW_LEAVE_FULLSCREEN:
            if (w && w->hwnd == s_main_hwnd) {
                rt_ws_leave_fs(&s_ws, now_ms(), window_rect(w->sdl));
                rt_log("window full screen off, windowed %d,%d %dx%d", s_ws.rect.x, s_ws.rect.y, s_ws.rect.w, s_ws.rect.h);
                window_display_log(w->sdl);
            }
            break;
        case SDL_EVENT_WINDOW_DISPLAY_CHANGED:
        case SDL_EVENT_WINDOW_DISPLAY_SCALE_CHANGED:
            if (w && w->hwnd == s_main_hwnd) window_display_log(w->sdl);
            break;
        case SDL_EVENT_WINDOW_MINIMIZED:
        case SDL_EVENT_WINDOW_RESTORED:
            if (w && w->hwnd == s_main_hwnd) rt_log("window event %s", e.type == SDL_EVENT_WINDOW_MINIMIZED ? "minimised" : "restored");
            break;
        case SDL_EVENT_WINDOW_FOCUS_GAINED:
        case SDL_EVENT_WINDOW_FOCUS_LOST:
            if (w) {
                int on = e.type == SDL_EVENT_WINDOW_FOCUS_GAINED;
                if (w->hwnd == s_main_hwnd) {
                    rt_input_focus(on);
                    rt_dsound_focus(on);
                }
                rt_input_post(w->hwnd, 0x001C, (uint32_t)on, 0);
                rt_input_post(w->hwnd, 0x0006, (uint32_t)on, 0);
            }
            break;
        case SDL_EVENT_WINDOW_SHOWN:
            if (w) rt_input_post(w->hwnd, 0x0005, 0, ((uint32_t)w->h << 16) | (uint32_t)(w->w & 0xFFFF));
            break;
        case SDL_EVENT_MOUSE_MOTION:
        case SDL_EVENT_MOUSE_BUTTON_DOWN:
        case SDL_EVENT_MOUSE_BUTTON_UP:
        case SDL_EVENT_MOUSE_WHEEL:
        case SDL_EVENT_KEY_DOWN:
        case SDL_EVENT_KEY_UP:
        case SDL_EVENT_TEXT_INPUT:
            rt_input_event(&e);
            break;
        default:
            break;
        }
    }
    rt_input_pump_end();
    window_tick();
    s_pump_ns += rt_now_ns() - now;
}

uint64_t rt_pump_ns(void) { return s_pump_ns; }

int rt_window_wait_event(uint32_t ms)
{
    if (!s_sdl) return 0;
    return SDL_WaitEventTimeout(NULL, (Sint32)ms);
}

void rt_window_push_close(void)
{
    uint32_t id = __atomic_load_n(&s_main_id, __ATOMIC_ACQUIRE);
    SDL_Event e;
    memset(&e, 0, sizeof e);
    if (id) {
        e.type = SDL_EVENT_WINDOW_CLOSE_REQUESTED;
        e.window.windowID = id;
    } else {
        e.type = SDL_EVENT_QUIT;
    }
    SDL_PushEvent(&e);
}
