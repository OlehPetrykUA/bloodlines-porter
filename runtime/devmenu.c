#include "recomp_types.h"
#include "rt.h"
#include "toggles.h"
#include "winstate.h"

#include <SDL3/SDL.h>
#include <dlfcn.h>
#include <stdint.h>
#include <string.h>

static void *s_send;
static void *(*s_sel)(const char *);
static void *(*s_cls)(const char *);

int rt_dev_chord_key(uint32_t scancode)
{
    (void)scancode;
    return 0;
}

void rt_dev_request(int source) { (void)source; }

int rt_dev_take(void) { return 0; }

void rt_dev_apply(void) {}

static void *msg0(void *o, const char *s) { return ((void *(*)(void *, void *))s_send)(o, s_sel(s)); }

static void *msg1(void *o, const char *s, void *a) { return ((void *(*)(void *, void *, void *))s_send)(o, s_sel(s), a); }

static void *nsstr(const char *s)
{
    return ((void *(*)(void *, void *, const char *))s_send)(s_cls("NSString"), s_sel("stringWithUTF8String:"), s);
}

#define HAVE_MAX 256

static const char *(*s_selname)(void *);
static const char *s_have[HAVE_MAX];
static int s_nhave;

static int objc_syms(void)
{
    s_send = dlsym(RTLD_DEFAULT, "objc_msgSend");
    s_sel = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "sel_registerName");
    s_cls = (void *(*)(const char *))dlsym(RTLD_DEFAULT, "objc_getClass");
    s_selname = (const char *(*)(void *))dlsym(RTLD_DEFAULT, "sel_getName");
    return s_send && s_sel && s_cls && s_selname;
}

static void *main_menu(void)
{
    void *app = msg0(s_cls("NSApplication"), "sharedApplication");
    return app ? msg0(app, "mainMenu") : NULL;
}

static long menu_count(void *menu) { return ((long (*)(void *, void *))s_send)(menu, s_sel("numberOfItems")); }

static void *menu_at(void *menu, long i) { return ((void *(*)(void *, void *, long))s_send)(menu, s_sel("itemAtIndex:"), i); }

static void collect(void *menu, int depth)
{
    if (!menu || depth > 3) return;
    long n = menu_count(menu);
    for (long i = 0; i < n; i++) {
        void *item = menu_at(menu, i);
        void *act = item ? msg0(item, "action") : NULL;
        if (act && s_nhave < HAVE_MAX) s_have[s_nhave++] = s_selname(act);
        if (item) collect(msg0(item, "submenu"), depth + 1);
    }
}

static unsigned missing_now(void *bar)
{
    s_nhave = 0;
    collect(bar, 0);
    return rt_menu_missing(s_have, s_nhave);
}

static const char *menu_title(void *menu)
{
    void *t = menu ? msg0(menu, "title") : NULL;
    return t ? ((const char *(*)(void *, void *))s_send)(t, s_sel("UTF8String")) : NULL;
}

static void *window_menu(void *bar)
{
    long n = menu_count(bar);
    for (long i = 0; i < n; i++) {
        void *sub = msg0(menu_at(bar, i), "submenu");
        const char *t = menu_title(sub);
        if (t && !strcmp(t, "Window")) return sub;
    }
    void *menu = ((void *(*)(void *, void *, void *))s_send)(msg0(s_cls("NSMenu"), "alloc"), s_sel("initWithTitle:"), nsstr("Window"));
    void *top = ((void *(*)(void *, void *, void *, void *, void *))s_send)(
        msg0(s_cls("NSMenuItem"), "alloc"), s_sel("initWithTitle:action:keyEquivalent:"), nsstr("Window"), NULL, nsstr(""));
    msg1(top, "setSubmenu:", menu);
    msg1(bar, "addItem:", top);
    msg1(msg0(s_cls("NSApplication"), "sharedApplication"), "setWindowsMenu:", menu);
    return menu;
}

unsigned rt_devmenu_window_items(void)
{
    if (!objc_syms()) return 0;
    void *bar = main_menu();
    if (!bar || menu_count(bar) < 1) return 0;
    unsigned miss = missing_now(bar);
    for (int i = 0; i < RT_MENU_ITEMS; i++) {
        if (!(miss >> i & 1u)) continue;
        const rt_menu_item *m = &rt_menu_items[i];
        void *menu = m->app ? msg0(menu_at(bar, 0), "submenu") : window_menu(bar);
        if (!menu) continue;
        void *item = ((void *(*)(void *, void *, void *, void *, void *))s_send)(
            msg0(s_cls("NSMenuItem"), "alloc"), s_sel("initWithTitle:action:keyEquivalent:"), nsstr(m->title), s_sel(m->action), nsstr(m->key));
        if (m->mask) ((void (*)(void *, void *, unsigned long))s_send)(item, s_sel("setKeyEquivalentModifierMask:"), m->mask);
        msg1(menu, "addItem:", item);
        rt_log("window menu added: %s (%s)", m->title, m->action);
    }
    return miss;
}

int rt_devmenu_has_action(const char *action)
{
    if (!objc_syms()) return 0;
    void *bar = main_menu();
    if (!bar) return 0;
    unsigned miss = missing_now(bar);
    for (int i = 0; i < RT_MENU_ITEMS; i++)
        if (!strcmp(rt_menu_items[i].action, action)) return !(miss >> i & 1u);
    return 0;
}

static int is_dead_item(void *item)
{
    void *act = msg0(item, "action");
    if (act) return !strcmp(s_selname(act), "performClose:");
    const char *t = menu_title(item);
    return t && !strncmp(t, "Settings", 8);
}

static int drop_dead_items(void *bar)
{
    int dropped = 0;
    for (long m = 0; m < menu_count(bar); m++) {
        void *sub = msg0(menu_at(bar, m), "submenu");
        if (!sub) continue;
        for (long i = menu_count(sub) - 1; i >= 0; i--) {
            if (!is_dead_item(menu_at(sub, i))) continue;
            ((void (*)(void *, void *, long))s_send)(sub, s_sel("removeItemAtIndex:"), i);
            dropped++;
        }
    }
    return dropped;
}

void rt_devmenu_init(void)
{
    if (!objc_syms()) {
        rt_log("dev menu unavailable: objc runtime symbols missing");
        return;
    }
    void *bar = main_menu();
    if (!bar) {
        rt_log("dev menu unavailable: no main menu");
        return;
    }
    void *menu = ((void *(*)(void *, void *, void *))s_send)(msg0(s_cls("NSMenu"), "alloc"), s_sel("initWithTitle:"), nsstr("Game"));
    rt_hud_menu_add(menu);
    void *top = ((void *(*)(void *, void *, void *, void *, void *))s_send)(
        msg0(s_cls("NSMenuItem"), "alloc"), s_sel("initWithTitle:action:keyEquivalent:"), nsstr("Game"), NULL, nsstr(""));
    msg1(top, "setSubmenu:", menu);
    ((void (*)(void *, void *, void *, long))s_send)(bar, s_sel("insertItem:atIndex:"), top, 1L);
    rt_log("game menu added: Overlays, Picture mode, Command-1 to 2");
    unsigned miss = rt_devmenu_window_items();
    rt_log("window menu items missing from SDL's menus and added: 0x%X", miss);
    int dropped = drop_dead_items(bar);
    rt_log("dead SDL menu items removed: %d", dropped);
}
