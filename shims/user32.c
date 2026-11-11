#include "recomp_types.h"
#include "rt.h"

#include <stdio.h>
#include <string.h>

#define WM_CREATE 0x0001
#define WM_DESTROY 0x0002
#define WM_SIZE 0x0005
#define WM_CLOSE 0x0010
#define WM_QUIT 0x0012
#define WM_NCCREATE 0x0081
#define WM_NCDESTROY 0x0082
#define WM_NCCALCSIZE 0x0083

typedef struct {
    uint32_t atom, wndproc, style, extra, cursor;
    char name[128];
} cls;

static cls s_cls[128];
static int s_ncls;
static uint32_t s_focus, s_capture;
static uint32_t s_atom_next = 0xC000u;

static void primary_only(const char *name)
{
    if (!rt_is_primary()) rt_stop(5, 0, "window call %s from a thread other than the primary thread", name);
}

static const char *msg_name(uint32_t m)
{
    switch (m) {
    case WM_CLOSE: return "WM_CLOSE";
    case WM_DESTROY: return "WM_DESTROY";
    case WM_QUIT: return "WM_QUIT";
    default: return NULL;
    }
}

static uint32_t call_proc(uint32_t proc, uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp)
{
    const char *n = msg_name(msg);
    if (n) rt_log("message %s to 0x%08X", n, hwnd);
    uint32_t args[4] = {hwnd, msg, wp, lp};
    return rt_guest_call(proc, 4, args);
}

static uint32_t send(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp)
{
    rt_win *w = rt_win_get(hwnd);
    if (!w || !w->wndproc) return 0;
    return call_proc(w->wndproc, hwnd, msg, wp, lp);
}

static cls *find_cls(uint32_t name_or_atom)
{
    for (int i = 0; i < s_ncls; i++) {
        if (name_or_atom < 0x10000u ? s_cls[i].atom == name_or_atom : !rt_stricmp(s_cls[i].name, rt_gstr(name_or_atom)))
            return &s_cls[i];
    }
    return NULL;
}

static uint32_t RegisterClassA(const uint32_t *a)
{
    uint32_t wc = a[0];
    if (s_ncls == 128) return 0;
    cls *c = &s_cls[s_ncls++];
    c->style = MEM32(wc);
    c->wndproc = MEM32(wc + 4);
    c->extra = MEM32(wc + 12);
    c->cursor = MEM32(wc + 24);
    uint32_t name = MEM32(wc + 36);
    if (name < 0x10000u) snprintf(c->name, sizeof c->name, "#%u", name);
    else snprintf(c->name, sizeof c->name, "%s", rt_gstr(name));
    c->atom = s_atom_next++;
    rt_log("RegisterClassA \"%s\" wndproc 0x%08X atom 0x%X", c->name, c->wndproc, c->atom);
    return c->atom;
}

static uint32_t UnregisterClassA(const uint32_t *a)
{
    cls *c = find_cls(a[0]);
    if (!c) {
        rt_set_last_error(1411);
        return 0;
    }
    *c = s_cls[--s_ncls];
    return 1;
}

static uint32_t CreateWindowExA(const uint32_t *a)
{
    primary_only("CreateWindowExA");
    cls *c = find_cls(a[1]);
    if (!c) {
        rt_set_last_error(1407);
        return 0;
    }
    rt_win *w = rt_win_new();
    w->exstyle = a[0];
    w->style = a[3];
    w->cls = c->atom;
    w->wndproc = c->wndproc;
    w->x = (int)a[4] == (int)0x80000000 ? 0 : (int)a[4];
    w->y = (int)a[5] == (int)0x80000000 ? 0 : (int)a[5];
    w->w = (int)a[6] == (int)0x80000000 ? 640 : (int)a[6];
    w->h = (int)a[7] == (int)0x80000000 ? 480 : (int)a[7];
    w->parent = a[8];
    w->id = a[9];
    w->instance = a[10];
    const char *t = rt_gstr(a[2]);
    size_t o = 0;
    for (const unsigned char *p = (const unsigned char *)t; *p && o < sizeof w->title - 3; p++) {
        if (*p < 0x80) {
            w->title[o++] = (char)*p;
        } else {
            w->title[o++] = (char)(0xC0 | (*p >> 6));
            w->title[o++] = (char)(0x80 | (*p & 0x3F));
        }
    }
    w->title[o] = 0;
    w->toplevel = !(w->style & 0x40000000u) && (!w->parent || w->parent == 0x00010000u);
    if (w->toplevel) rt_win_sdl(w);
    rt_log("CreateWindowExA class \"%s\" title \"%s\" %dx%d style 0x%08X -> 0x%08X", c->name, w->title, w->w, w->h,
           w->style, w->hwnd);
    uint32_t cs = rt_heap_alloc(48 + 16, 1);
    uint32_t vals[12] = {a[11], a[10], a[9], a[8], (uint32_t)w->h, (uint32_t)w->w, (uint32_t)w->y, (uint32_t)w->x,
                         a[3], a[2], a[1], a[0]};
    memcpy(rt_host(cs), vals, sizeof vals);
    uint32_t hwnd = w->hwnd;
    if (!send(hwnd, WM_NCCREATE, 0, cs)) {
        rt_log("WM_NCCREATE returned FALSE");
        rt_win *x = rt_win_get(hwnd);
        if (x) rt_win_free(x);
        rt_heap_free(cs);
        return 0;
    }
    uint32_t rc = cs + 48;
    MEM32(rc) = (uint32_t)w->x;
    MEM32(rc + 4) = (uint32_t)w->y;
    MEM32(rc + 8) = (uint32_t)(w->x + w->w);
    MEM32(rc + 12) = (uint32_t)(w->y + w->h);
    send(hwnd, WM_NCCALCSIZE, 0, rc);
    if (send(hwnd, WM_CREATE, 0, cs) == 0xFFFFFFFFu) {
        rt_log("WM_CREATE returned -1");
        rt_win *x = rt_win_get(hwnd);
        if (x) rt_win_free(x);
        rt_heap_free(cs);
        return 0;
    }
    rt_heap_free(cs);
    w = rt_win_get(hwnd);
    if (w && (w->style & 0x10000000u)) rt_win_show(w, 1);
    return hwnd;
}

static uint32_t DestroyWindow(const uint32_t *a)
{
    primary_only("DestroyWindow");
    rt_win *w = rt_win_get(a[0]);
    if (!w) {
        rt_set_last_error(1400);
        return 0;
    }
    uint32_t h = w->hwnd;
    send(h, WM_DESTROY, 0, 0);
    send(h, WM_NCDESTROY, 0, 0);
    w = rt_win_get(h);
    if (w) rt_win_free(w);
    return 1;
}

static uint32_t ShowWindow(const uint32_t *a)
{
    primary_only("ShowWindow");
    rt_win *w = rt_win_get(a[0]);
    if (!w) return 0;
    int was = w->visible;
    rt_log("window 0x%08X ShowWindow %u, was %s", a[0], a[1], was ? "visible" : "hidden");
    rt_win_show(w, a[1] != 0);
    return (uint32_t)was;
}

static uint32_t UpdateWindow(const uint32_t *a) { return rt_win_get(a[0]) != NULL; }

static uint32_t SetWindowPos(const uint32_t *a)
{
    primary_only("SetWindowPos");
    rt_win *w = rt_win_get(a[0]);
    if (!w) return 0;
    uint32_t f = a[6];
    if (!(f & 0x2u)) {
        w->x = (int)a[2];
        w->y = (int)a[3];
    }
    if (!(f & 0x1u) && ((int)a[4] != w->w || (int)a[5] != w->h)) {
        rt_win_resize(w, (int)a[4], (int)a[5]);
        rt_log("window 0x%08X resized to %dx%d", w->hwnd, w->w, w->h);
    }
    if (f & 0xC0u) rt_log("window 0x%08X SetWindowPos %s", a[0], f & 0x40u ? "show" : "hide");
    if (f & 0x40u) rt_win_show(w, 1);
    if (f & 0x80u) rt_win_show(w, 0);
    return 1;
}

static uint32_t MoveWindow(const uint32_t *a)
{
    uint32_t b[7] = {a[0], 0, a[1], a[2], a[3], a[4], 0};
    return SetWindowPos(b);
}

static uint32_t rect(uint32_t p, int l, int t, int r, int b)
{
    MEM32(p) = (uint32_t)l;
    MEM32(p + 4) = (uint32_t)t;
    MEM32(p + 8) = (uint32_t)r;
    MEM32(p + 12) = (uint32_t)b;
    return 1;
}

static uint32_t GetClientRect(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    if (!w) {
        int dw, dh;
        rt_desktop_size(&dw, &dh);
        return a[0] == 0x00010000u ? rect(a[1], 0, 0, dw, dh) : 0;
    }
    return rect(a[1], 0, 0, w->w, w->h);
}

static uint32_t GetWindowRect(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    if (!w) {
        int dw, dh;
        rt_desktop_size(&dw, &dh);
        return a[0] == 0x00010000u ? rect(a[1], 0, 0, dw, dh) : 0;
    }
    return rect(a[1], w->x, w->y, w->x + w->w, w->y + w->h);
}

static uint32_t AdjustWindowRect(const uint32_t *a) { (void)a; return 1; }

static uint32_t ClientToScreen(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    if (!w) return 0;
    MEM32(a[1]) += (uint32_t)w->x;
    MEM32(a[1] + 4) += (uint32_t)w->y;
    return 1;
}

static uint32_t ScreenToClient(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    if (!w) return 0;
    MEM32(a[1]) -= (uint32_t)w->x;
    MEM32(a[1] + 4) -= (uint32_t)w->y;
    return 1;
}

static void store_msg(uint32_t p, const uint32_t m[7]) { memcpy(rt_host(p), m, 28); }

static uint32_t PeekMessageA(const uint32_t *a)
{
    primary_only("PeekMessageA");
    rt_pump(1);
    uint32_t m[7];
    if (!rt_msg_take(a[1], a[2], a[3], a[4] & 1u, m)) return 0;
    store_msg(a[0], m);
    return 1;
}

static uint32_t GetMessageA(const uint32_t *a)
{
    primary_only("GetMessageA");
    uint32_t m[7];
    for (;;) {
        rt_pump(1);
        if (rt_msg_take(a[1], a[2], a[3], 1, m)) break;
        rt_block_begin("GetMessageA", 0xFFFFFFFFu);
        rt_window_wait_event(16);
        rt_block_end();
    }
    store_msg(a[0], m);
    if (m[1] == WM_QUIT) rt_log("message WM_QUIT (%u) taken by GetMessageA", m[2]);
    return m[1] != WM_QUIT;
}

static uint32_t TranslateMessage(const uint32_t *a) { (void)a; return 0; }

static uint32_t DispatchMessageA(const uint32_t *a)
{
    primary_only("DispatchMessageA");
    uint32_t p = a[0];
    return send(MEM32(p), MEM32(p + 4), MEM32(p + 8), MEM32(p + 12));
}

static uint32_t DefWindowProcA(const uint32_t *a)
{
    switch (a[1]) {
    case WM_CLOSE: {
        uint32_t b[1] = {a[0]};
        DestroyWindow(b);
        return 0;
    }
    case WM_NCCREATE: return 1;
    default: return 0;
    }
}

static uint32_t CallWindowProcA(const uint32_t *a) { return call_proc(a[0], a[1], a[2], a[3], a[4]); }

static uint32_t PostMessageA(const uint32_t *a)
{
    primary_only("PostMessageA");
    rt_msg_post(a[0], a[1], a[2], a[3]);
    return 1;
}

static uint32_t SendMessageA(const uint32_t *a)
{
    primary_only("SendMessageA");
    return send(a[0], a[1], a[2], a[3]);
}

static uint32_t PostQuitMessage(const uint32_t *a)
{
    rt_log("PostQuitMessage(%u)", a[0]);
    rt_msg_post(0, WM_QUIT, a[0], 0);
    return 0;
}

static uint32_t *long_slot(rt_win *w, int32_t i)
{
    switch (i) {
    case -4: return &w->wndproc;
    case -6: return &w->instance;
    case -8: return &w->parent;
    case -12: return &w->id;
    case -16: return &w->style;
    case -20: return &w->exstyle;
    case -21: return &w->userdata;
    default: return i >= 0 && i < 64 && !(i & 3) ? &w->extra[i / 4] : NULL;
    }
}

static uint32_t GetWindowLongA(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    uint32_t *s = w ? long_slot(w, (int32_t)a[1]) : NULL;
    if (!s) {
        rt_set_last_error(w ? 1413 : 1400);
        return 0;
    }
    return *s;
}

static uint32_t SetWindowLongA(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    uint32_t *s = w ? long_slot(w, (int32_t)a[1]) : NULL;
    if (!s) {
        rt_set_last_error(w ? 1413 : 1400);
        return 0;
    }
    uint32_t old = *s;
    *s = a[2];
    if ((int32_t)a[1] == -16 && w) rt_log("SetWindowLongA style 0x%08X", a[2]);
    return old;
}

static uint32_t GetDC(const uint32_t *a) { (void)a; return 0x00020000u; }

static uint32_t ReleaseDC(const uint32_t *a) { (void)a; return 1; }

static uint32_t ShowCursor(const uint32_t *a) { return (uint32_t)rt_input_show_cursor(a[0] != 0); }

static uint32_t SetCursor(const uint32_t *a) { return rt_input_cursor_shape(a[0]); }

static uint32_t LoadCursorA(const uint32_t *a)
{
    static int logged;
    if (a[0] && !logged) {
        logged = 1;
        rt_log("LoadCursorA module 0x%08X cursor 0x%08X mapped to the default arrow", a[0], a[1]);
    }
    return 0x00030000u + (a[0] ? 0x7F00u : a[1] & 0xFFFFu);
}

static uint32_t LoadIconA(const uint32_t *a) { return 0x00040000u + (a[1] & 0xFFFFu); }

static uint32_t GetSystemMetrics(const uint32_t *a)
{
    int w, h;
    rt_desktop_size(&w, &h);
    switch (a[0]) {
    case 0: case 16: case 78: return (uint32_t)w;
    case 1: case 17: case 79: return (uint32_t)h;
    case 80: return 1;
    default: return 0;
    }
}

static uint32_t MessageBoxA(const uint32_t *a)
{
    rt_log("MessageBoxA caption \"%s\" text \"%s\"", rt_gstr(a[2]), rt_gstr(a[1]));
    return 1;
}

static uint32_t ChangeDisplaySettingsA(const uint32_t *a)
{
    (void)a;
    rt_log("ChangeDisplaySettingsA refused (windowed only)");
    return (uint32_t)-1;
}

static uint32_t GetDesktopWindow(const uint32_t *a) { (void)a; return 0x00010000u; }

static uint32_t GetForegroundWindow(const uint32_t *a) { (void)a; return s_focus; }

static uint32_t SetForegroundWindow(const uint32_t *a)
{
    s_focus = a[0];
    return 1;
}

static uint32_t SetFocus(const uint32_t *a)
{
    uint32_t old = s_focus;
    s_focus = a[0];
    return old;
}

static uint32_t GetFocus(const uint32_t *a) { (void)a; return s_focus; }

static uint32_t SetActiveWindow(const uint32_t *a) { return SetFocus(a); }

static uint32_t SetCapture(const uint32_t *a)
{
    uint32_t old = s_capture;
    s_capture = a[0];
    rt_input_capture(a[0]);
    return old;
}

static uint32_t ReleaseCapture(const uint32_t *a)
{
    (void)a;
    s_capture = 0;
    rt_input_capture(0);
    return 1;
}

static uint32_t IsIconic(const uint32_t *a) { (void)a; return 0; }

static uint32_t IsWindowVisible(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    return w && w->visible;
}

static uint32_t GetCursorPos(const uint32_t *a)
{
    if (!rt_span_ok(a[0], 8)) {
        rt_set_last_error(998);
        return 0;
    }
    int32_t x, y;
    rt_input_get_cursor(&x, &y);
    MEM32(a[0]) = (uint32_t)x;
    MEM32(a[0] + 4) = (uint32_t)y;
    return 1;
}

static uint32_t SetCursorPos(const uint32_t *a)
{
    rt_input_set_cursor((int32_t)a[0], (int32_t)a[1]);
    return 1;
}

static uint32_t GetKeyState(const uint32_t *a) { return rt_input_key_down(a[0] & 0xFFu) ? 0xFFFF8000u : 0; }

static uint32_t SetWindowTextA(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    if (!w) return 0;
    snprintf(w->title, sizeof w->title, "%s", rt_gstr(a[1]));
    return 1;
}

static uint32_t GetParent(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    return w ? w->parent : 0;
}

static uint32_t InvalidateRect(const uint32_t *a) { (void)a; return 1; }

static uint32_t ValidateRect(const uint32_t *a) { (void)a; return 1; }

static uint32_t FindWindowA(const uint32_t *a) { (void)a; return 0; }

static uint32_t RegisterWindowMessageA(const uint32_t *a)
{
    (void)a;
    return s_atom_next++;
}

static uint32_t SystemParametersInfoA(const uint32_t *a)
{
    rt_log("SystemParametersInfoA action 0x%X", a[0]);
    if (a[2] && a[0] == 0x30) {
        int w, h;
        rt_desktop_size(&w, &h);
        rect(a[2], 0, 0, w, h);
    }
    return 1;
}

static uint32_t GetWindowThreadProcessId(const uint32_t *a)
{
    if (a[1]) MEM32(a[1]) = 0x10;
    rt_thread *t = rt_self();
    return t ? t->tid : 0x100;
}

static uint32_t fmt(char *out, uint32_t cap, const char *f, uint32_t args)
{
    uint32_t o = 0;
    while (*f && o + 1 < cap) {
        if (*f != '%') {
            out[o++] = *f++;
            continue;
        }
        f++;
        if (*f == '%') {
            out[o++] = *f++;
            continue;
        }
        char spec[16] = "%";
        int k = 1;
        while (*f && strchr("-#0 +", *f) && k < 6) spec[k++] = *f++;
        while (*f >= '0' && *f <= '9' && k < 10) spec[k++] = *f++;
        if (*f == '.') {
            spec[k++] = *f++;
            while (*f >= '0' && *f <= '9' && k < 13) spec[k++] = *f++;
        }
        while (*f == 'l' || *f == 'h') f++;
        char t = *f ? *f++ : 0;
        char b[1024];
        uint32_t v = MEM32(args);
        args += 4;
        switch (t) {
        case 'd': case 'i': case 'u': case 'x': case 'X': case 'c':
            spec[k++] = t;
            spec[k] = 0;
            if (t == 'd' || t == 'i') snprintf(b, sizeof b, spec, (int32_t)v);
            else snprintf(b, sizeof b, spec, v);
            break;
        case 's':
            spec[k++] = 's';
            spec[k] = 0;
            snprintf(b, sizeof b, spec, v ? rt_gstr(v) : "(null)");
            break;
        default:
            b[0] = 0;
            break;
        }
        for (char *p = b; *p && o + 1 < cap; p++) out[o++] = *p;
    }
    out[o] = 0;
    return o;
}

static uint32_t wsprintfA(const uint32_t *a)
{
    char b[1025];
    uint32_t n = fmt(b, sizeof b, rt_gstr(a[1]), (uint32_t)((const unsigned char *)&a[2] - (const unsigned char *)rt_host(0)));
    if (!rt_span_ok(a[0], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[0]), b, n + 1);
    return n;
}

static uint32_t wvsprintfA(const uint32_t *a)
{
    char b[1025];
    uint32_t n = fmt(b, sizeof b, rt_gstr(a[1]), a[2]);
    if (!rt_span_ok(a[0], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[0]), b, n + 1);
    return n;
}

static uint32_t GetSysColor(const uint32_t *a) { (void)a; return 0; }

static uint32_t LoadStringA(const uint32_t *a)
{
    if (a[3]) MEM8(a[2]) = 0;
    return 0;
}

static uint32_t MsgWaitForMultipleObjects(const uint32_t *a)
{
    uint32_t ms = a[3];
    if (rt_is_primary()) {
        rt_pump(1);
        uint32_t m[7];
        if (rt_msg_take(0, 0, 0, 0, m)) return a[0];
    }
    if (!rt_span_ok(a[1], 4ull * a[0])) {
        rt_set_last_error(998);
        return 0xFFFFFFFFu;
    }
    uint32_t slice = ms < 16 ? ms : 16;
    uint64_t end = rt_now_ns() + (uint64_t)ms * 1000000u;
    for (;;) {
        if (!a[0] && ms == 0) return 0x102u;
        uint32_t r = rt_wait(a[0], (const uint32_t *)rt_host(a[1]), (int)a[2], slice);
        if (a[0] && (r != 0x102u || ms == 0)) return r;
        if (rt_is_primary()) {
            rt_pump(1);
            uint32_t m[7];
            if (rt_msg_take(0, 0, 0, 0, m)) return a[0];
        }
        if (ms != 0xFFFFFFFFu && rt_now_ns() >= end) return 0x102u;
    }
}

static uint32_t EnumWindows(const uint32_t *a)
{
    uint32_t proc = a[0], lp = a[1];
    for (uint32_t h = 0x00010004u; h < 0x00010000u + 4u * 256u; h += 4) {
        rt_win *w = rt_win_get(h);
        if (!w || !w->toplevel) continue;
        uint32_t args[2] = {h, lp};
        if (!rt_guest_call(proc, 2, args)) break;
    }
    return 1;
}

static uint32_t EnumChildWindows(const uint32_t *a)
{
    uint32_t parent = a[0], proc = a[1], lp = a[2];
    for (uint32_t h = 0x00010004u; h < 0x00010000u + 4u * 256u; h += 4) {
        rt_win *w = rt_win_get(h);
        if (!w || w->parent != parent || !parent) continue;
        uint32_t args[2] = {h, lp};
        if (!rt_guest_call(proc, 2, args)) break;
    }
    return 1;
}

static uint32_t GetClassNameA(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    cls *c = w ? find_cls(w->cls) : NULL;
    if (!c || !a[2]) return 0;
    uint32_t n = (uint32_t)strlen(c->name);
    if (n > a[2] - 1) n = a[2] - 1;
    if (!rt_span_ok(a[1], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[1]), c->name, n);
    MEM8(a[1] + n) = 0;
    return n;
}

static uint32_t GetWindowTextA(const uint32_t *a)
{
    rt_win *w = rt_win_get(a[0]);
    if (!w || !a[2]) return 0;
    uint32_t n = (uint32_t)strlen(w->title);
    if (n > a[2] - 1) n = a[2] - 1;
    if (!rt_span_ok(a[1], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[1]), w->title, n);
    MEM8(a[1] + n) = 0;
    return n;
}

const rt_shim rt_user32_shims[] = {
    {"user32.dll", "EnumChildWindows", 3, EnumChildWindows},
    {"user32.dll", "GetClassNameA", 3, GetClassNameA},
    {"user32.dll", "GetWindowTextA", 3, GetWindowTextA},
    {"user32.dll", "EnumWindows", 2, EnumWindows},
    {"user32.dll", "AdjustWindowRect", 3, AdjustWindowRect},
    {"user32.dll", "AdjustWindowRectEx", 4, AdjustWindowRect},
    {"user32.dll", "CallWindowProcA", 5, CallWindowProcA},
    {"user32.dll", "ChangeDisplaySettingsA", 2, ChangeDisplaySettingsA, RT_SHIM_FAIL},
    {"user32.dll", "ClientToScreen", 2, ClientToScreen},
    {"user32.dll", "CreateWindowExA", 12, CreateWindowExA},
    {"user32.dll", "DefWindowProcA", 4, DefWindowProcA},
    {"user32.dll", "DestroyWindow", 1, DestroyWindow},
    {"user32.dll", "DispatchMessageA", 1, DispatchMessageA},
    {"user32.dll", "FindWindowA", 2, FindWindowA},
    {"user32.dll", "GetClientRect", 2, GetClientRect},
    {"user32.dll", "GetCursorPos", 1, GetCursorPos},
    {"user32.dll", "GetDC", 1, GetDC},
    {"user32.dll", "GetDesktopWindow", 0, GetDesktopWindow},
    {"user32.dll", "GetFocus", 0, GetFocus},
    {"user32.dll", "GetForegroundWindow", 0, GetForegroundWindow},
    {"user32.dll", "GetKeyState", 1, GetKeyState},
    {"user32.dll", "GetMessageA", 4, GetMessageA},
    {"user32.dll", "GetParent", 1, GetParent},
    {"user32.dll", "GetSysColor", 1, GetSysColor},
    {"user32.dll", "GetSystemMetrics", 1, GetSystemMetrics},
    {"user32.dll", "GetWindowLongA", 2, GetWindowLongA},
    {"user32.dll", "GetWindowRect", 2, GetWindowRect},
    {"user32.dll", "GetWindowThreadProcessId", 2, GetWindowThreadProcessId},
    {"user32.dll", "InvalidateRect", 3, InvalidateRect},
    {"user32.dll", "IsIconic", 1, IsIconic},
    {"user32.dll", "IsWindowVisible", 1, IsWindowVisible},
    {"user32.dll", "LoadCursorA", 2, LoadCursorA},
    {"user32.dll", "LoadIconA", 2, LoadIconA},
    {"user32.dll", "LoadStringA", 4, LoadStringA},
    {"user32.dll", "MessageBoxA", 4, MessageBoxA},
    {"user32.dll", "MoveWindow", 6, MoveWindow},
    {"user32.dll", "MsgWaitForMultipleObjects", 5, MsgWaitForMultipleObjects},
    {"user32.dll", "PeekMessageA", 5, PeekMessageA},
    {"user32.dll", "PostMessageA", 4, PostMessageA},
    {"user32.dll", "PostQuitMessage", 1, PostQuitMessage},
    {"user32.dll", "RegisterClassA", 1, RegisterClassA},
    {"user32.dll", "RegisterWindowMessageA", 1, RegisterWindowMessageA},
    {"user32.dll", "ReleaseCapture", 0, ReleaseCapture},
    {"user32.dll", "ReleaseDC", 2, ReleaseDC},
    {"user32.dll", "ScreenToClient", 2, ScreenToClient},
    {"user32.dll", "SendMessageA", 4, SendMessageA},
    {"user32.dll", "SetActiveWindow", 1, SetActiveWindow},
    {"user32.dll", "SetCapture", 1, SetCapture},
    {"user32.dll", "SetCursor", 1, SetCursor},
    {"user32.dll", "SetCursorPos", 2, SetCursorPos},
    {"user32.dll", "SetFocus", 1, SetFocus},
    {"user32.dll", "SetForegroundWindow", 1, SetForegroundWindow},
    {"user32.dll", "SetWindowLongA", 3, SetWindowLongA},
    {"user32.dll", "SetWindowPos", 7, SetWindowPos},
    {"user32.dll", "SetWindowTextA", 2, SetWindowTextA},
    {"user32.dll", "ShowCursor", 1, ShowCursor},
    {"user32.dll", "ShowWindow", 2, ShowWindow},
    {"user32.dll", "SystemParametersInfoA", 4, SystemParametersInfoA},
    {"user32.dll", "TranslateMessage", 1, TranslateMessage},
    {"user32.dll", "UnregisterClassA", 2, UnregisterClassA},
    {"user32.dll", "UpdateWindow", 1, UpdateWindow},
    {"user32.dll", "ValidateRect", 2, ValidateRect},
    {"user32.dll", "wsprintfA", 0, wsprintfA},
    {"user32.dll", "wvsprintfA", 3, wvsprintfA},
};

const uint32_t rt_nuser32_shims = sizeof rt_user32_shims / sizeof rt_user32_shims[0];
