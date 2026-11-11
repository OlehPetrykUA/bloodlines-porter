#import <Cocoa/Cocoa.h>
#import <CoreText/CoreText.h>
#import <ImageIO/ImageIO.h>

#include "rt.h"
#include "toggles.h"

#include <SDL3/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdint.h>

#define MARGIN 12.0
#define PAD_X 12.0
#define PAD_Y 8.0
#define CORNER 8.0
#define LINE_H 16.0
#define GROUP_GAP 8.0
#define READINGS 3
#define FONT_PT 11.0
#define CANVAS_W 1280
#define CANVAS_H 1024

@interface RTHudTarget : NSObject <NSMenuItemValidation>
- (void)toggle:(NSMenuItem *)item;
@end

@interface RTHudView : NSView
@property(copy) NSArray<NSAttributedString *> *lines;
@end

static RTHudTarget *s_target;
static NSMenu *s_game;
static RTHudView *s_view;
static NSTimer *s_timer;
static void *s_attached;

enum { C_FPS, C_LABEL, C_VALUE, C_KEY, C_COUNT };

static id hud_color(int c)
{
    static const uint32_t rgb[C_COUNT] = {0x50FF78, 0xBFBFBF, 0xFFFFFF, 0xA6A6A6};
    static id col[C_COUNT];
    if (!col[c]) col[c] = CFBridgingRelease(CGColorCreateSRGB((rgb[c] >> 16 & 255) / 255.0, (rgb[c] >> 8 & 255) / 255.0, (rgb[c] & 255) / 255.0, 1));
    return col[c];
}

static NSFont *hud_font(void)
{
    static NSFont *f;
    if (!f) f = [NSFont monospacedSystemFontOfSize:FONT_PT weight:NSFontWeightMedium];
    return f;
}

static void hud_run(NSMutableAttributedString *s, NSString *t, int c)
{
    if (!t.length) return;
    NSDictionary *a = @{(__bridge id)kCTFontAttributeName : hud_font(), (__bridge id)kCTForegroundColorAttributeName : hud_color(c)};
    [s appendAttributedString:[[NSAttributedString alloc] initWithString:t attributes:a]];
}

static NSAttributedString *hud_text(NSString *t, int c)
{
    NSMutableAttributedString *s = [NSMutableAttributedString new];
    hud_run(s, t, c);
    return s;
}

static NSAttributedString *hud_row(NSString *key, NSString *title, const char *label)
{
    NSString *value = [NSString stringWithUTF8String:label];
    NSMutableAttributedString *s = [NSMutableAttributedString new];
    hud_run(s, key, C_KEY);
    hud_run(s, [title stringByAppendingString:@": "], C_LABEL);
    hud_run(s, value, [value isEqualToString:@"off"] ? C_LABEL : C_VALUE);
    return s;
}

static NSArray<NSAttributedString *> *hud_lines(int state, const rt_hud_info *i)
{
    if (state == RT_HUD_OFF) return @[];
    NSAttributedString *first = hud_text([NSString stringWithFormat:@"%d FPS  p50 %.2f ms  p99 %.2f ms", i->fps, i->p50, i->p99], C_FPS);
    if (state == RT_HUD_FPS) return @[ first ];
    NSMutableArray<NSAttributedString *> *lines = [NSMutableArray arrayWithObjects:
        first,
        hud_text([NSString stringWithFormat:@"Shaders %u created, %u failed", i->created, i->failed], C_LABEL),
        hud_text([NSString stringWithFormat:@"Load %.2f", i->load], C_LABEL), nil];
    const struct { NSString *key, *title; const char *label; int id; } rows[] = {
        {@"⌘1 ", @"Overlays", i->overlays, RT_TOG_OVERLAYS},
        {@"⌘2 ", @"Picture mode", i->picture, RT_TOG_PICTURE},
    };
    for (size_t k = 0; k < sizeof rows / sizeof *rows; k++)
        if (strcmp(rows[k].label, "unavailable")) [lines addObject:hud_row(rows[k].key, rows[k].title, rows[k].label)];
    return lines;
}

static CGFloat hud_line_top(NSUInteger i) { return PAD_Y + LINE_H * i + (i >= READINGS ? GROUP_GAP : 0); }

static CGSize hud_box_size(NSArray<NSAttributedString *> *lines)
{
    CGFloat w = 0;
    for (NSAttributedString *a in lines) {
        CTLineRef l = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)a);
        CGFloat lw = CTLineGetTypographicBounds(l, NULL, NULL, NULL);
        if (lw > w) w = lw;
        CFRelease(l);
    }
    return CGSizeMake(ceil(w) + 2 * PAD_X, hud_line_top(lines.count) + PAD_Y);
}

static void hud_draw_box(CGContextRef cg, CGPoint at, NSArray<NSAttributedString *> *lines)
{
    if (!lines.count) return;
    CGSize sz = hud_box_size(lines);
    CGRect r = CGRectMake(at.x, at.y, sz.width, sz.height);
    CGPathRef p = CGPathCreateWithRoundedRect(r, CORNER, CORNER, NULL);
    CGContextSetRGBFillColor(cg, 10 / 255.0, 10 / 255.0, 10 / 255.0, 0.80);
    CGContextAddPath(cg, p);
    CGContextFillPath(cg);
    CGPathRelease(p);
    CGPathRef q = CGPathCreateWithRoundedRect(CGRectInset(r, 0.5, 0.5), CORNER - 0.5, CORNER - 0.5, NULL);
    CGContextSetRGBStrokeColor(cg, 1, 1, 1, 0.14);
    CGContextSetLineWidth(cg, 1);
    CGContextAddPath(cg, q);
    CGContextStrokePath(cg);
    CGPathRelease(q);
    CTFontRef f = (__bridge CTFontRef)hud_font();
    CGFloat asc = CTFontGetAscent(f), desc = CTFontGetDescent(f);
    for (NSUInteger i = 0; i < lines.count; i++) {
        CTLineRef l = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)lines[i]);
        CGFloat top = r.origin.y + sz.height - hud_line_top(i);
        CGContextSetTextPosition(cg, r.origin.x + PAD_X, top - (LINE_H - asc - desc) / 2 - asc);
        CTLineDraw(l, cg);
        CFRelease(l);
    }
}

@implementation RTHudView
- (NSView *)hitTest:(NSPoint)p
{
    (void)p;
    return nil;
}
- (void)drawRect:(NSRect)dirty
{
    (void)dirty;
    hud_draw_box([NSGraphicsContext currentContext].CGContext, CGPointZero, self.lines);
}
- (void)place
{
    if (!self.superview) return;
    CGSize sz = hud_box_size(self.lines);
    NSRect b = self.superview.bounds;
    CGFloat below = 0;
    if (self.window.styleMask & NSWindowStyleMaskFullScreen)
        below = MAX(NSApp.mainMenu.menuBarHeight, b.size.height - NSMaxY(self.superview.safeAreaRect));
    NSRect f = NSMakeRect(floor(b.size.width - sz.width - MARGIN), floor(b.size.height - sz.height - MARGIN - below), sz.width, sz.height);
    if (!NSEqualRects(self.frame, f)) self.frame = f;
}
@end

static const char *label_of(int id, char *buf, size_t n) { return rt_toggle_label(id, buf, n); }

static void hud_live_info(rt_hud_info *i)
{
    static char b[RT_TOG_COUNT][40];
    double fps;
    rt_fps_stats(&fps, &i->p50, &i->p99);
    i->fps = (int)lrint(fps);
    uint32_t sh[6] = {0};
    rt_gfx_shader_counts(sh);
    i->created = sh[0] + sh[1] + sh[2] + sh[3] + sh[4];
    i->failed = sh[5];
    double la[1] = {0};
    if (getloadavg(la, 1) < 1) la[0] = 0;
    i->load = la[0];
    i->overlays = label_of(RT_TOG_OVERLAYS, b[RT_TOG_OVERLAYS], sizeof b[0]);
    i->picture = label_of(RT_TOG_PICTURE, b[RT_TOG_PICTURE], sizeof b[0]);
}

static NSArray<NSAttributedString *> *hud_current(void)
{
    rt_hud_info info;
    hud_live_info(&info);
    int s = rt_toggle_get(RT_TOG_OVERLAYS);
    return hud_lines(s == RT_OVL_ALL ? RT_HUD_ALL : s == RT_OVL_FPS ? RT_HUD_FPS : RT_HUD_OFF, &info);
}

int rt_hud_live_lines(void)
{
    @autoreleasepool {
        return (int)hud_current().count;
    }
}

static BOOL hud_validate(NSMenuItem *item)
{
    int id = (int)item.tag;
    if (id < 0 || id >= RT_TOG_COUNT) return YES;
    uint32_t mask = rt_toggle_available(id, NULL);
    char label[40], value[32];
    rt_toggle_label(id, label, sizeof label);
    rt_toggle_value(id, value, sizeof value);
    item.title = [NSString stringWithFormat:@"%@: %@", [NSString stringWithUTF8String:rt_toggle_title(id)], [NSString stringWithUTF8String:label]];
    item.state = mask && strcmp(value, "off") ? NSControlStateValueOn : NSControlStateValueOff;
    item.hidden = mask == 0;
    return mask != 0;
}

@implementation RTHudTarget
- (void)toggle:(NSMenuItem *)item
{
    rt_toggle_request((int)item.tag, RT_SRC_MENU);
}
- (BOOL)validateMenuItem:(NSMenuItem *)item
{
    return hud_validate(item);
}
@end

static NSMenuItem *hud_item(int id)
{
    NSMenuItem *m = [[NSMenuItem alloc] initWithTitle:[NSString stringWithUTF8String:rt_toggle_title(id)]
                                               action:@selector(toggle:)
                                        keyEquivalent:[NSString stringWithFormat:@"%d", rt_toggle_key(id)]];
    m.keyEquivalentModifierMask = NSEventModifierFlagCommand;
    m.tag = id;
    m.target = s_target;
    return m;
}

static void hud_target(void)
{
    if (!s_target) s_target = [RTHudTarget new];
}

void rt_hud_menu_add(void *menu)
{
    hud_target();
    s_game = (__bridge NSMenu *)menu;
    for (int id = 0; id < RT_TOG_COUNT; id++)
        if (rt_toggle_key(id)) [s_game addItem:hud_item(id)];
}

static NSWindow *hud_window(void)
{
    SDL_Window *sw = (SDL_Window *)rt_gfx_device_window();
    if (!sw) return nil;
    return (__bridge NSWindow *)SDL_GetPointerProperty(SDL_GetWindowProperties(sw), SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, NULL);
}

static void hud_tick(void)
{
    int state = rt_toggle_get(RT_TOG_OVERLAYS);
    NSWindow *w = hud_window();
    if (!w || state == RT_OVL_OFF) {
        s_view.hidden = YES;
        return;
    }
    if (!s_view) {
        s_view = [[RTHudView alloc] initWithFrame:NSZeroRect];
        s_view.autoresizingMask = NSViewMinXMargin | NSViewMinYMargin;
        s_view.wantsLayer = YES;
        s_view.layerContentsRedrawPolicy = NSViewLayerContentsRedrawOnSetNeedsDisplay;
    }
    if (s_attached != (__bridge void *)w || w.contentView.subviews.lastObject != s_view) {
        [w.contentView addSubview:s_view positioned:NSWindowAbove relativeTo:nil];
        s_attached = (__bridge void *)w;
    }
    s_view.layer.contentsScale = w.backingScaleFactor;
    s_view.lines = hud_current();
    s_view.hidden = NO;
    [s_view place];
    s_view.needsDisplay = YES;
}

void rt_hud_init(void)
{
    if (!s_game) {
        rt_log("hud unavailable: Game menu missing");
        return;
    }
    s_timer = [NSTimer timerWithTimeInterval:0.5 repeats:YES block:^(NSTimer *t) {
        (void)t;
        hud_tick();
    }];
    [[NSRunLoop mainRunLoop] addTimer:s_timer forMode:NSRunLoopCommonModes];
    rt_log("hud added: Game menu keys 1, 2");
}

static void dump_menu(FILE *f, NSMenu *m)
{
    fprintf(f, "menu\t%s\n", m.title.UTF8String);
    for (NSMenuItem *it in m.itemArray) {
        const char *title = it.separatorItem ? "-" : it.title.UTF8String;
        BOOL on = it.separatorItem ? NO : it.target == s_target ? hud_validate(it) : it.enabled;
        if (it.hidden) continue;
        const char *key = it.keyEquivalent.UTF8String;
        unsigned long mask = key[0] ? (unsigned long)it.keyEquivalentModifierMask : 0;
        fprintf(f, "item\t%s\t%s\t%lu\t%d\t%s\n", title, key, mask, on ? 1 : 0, it.toolTip ? it.toolTip.UTF8String : "");
    }
}

int rt_hud_dump_menu(const char *tsv)
{
    @autoreleasepool {
        rt_fps_reset();
        hud_target();
        NSMenu *bar = [[NSMenu alloc] initWithTitle:@""];
        NSString *title = @RT_APP_TITLE;
        NSMenu *app = [[NSMenu alloc] initWithTitle:title];
        [app addItem:[[NSMenuItem alloc] initWithTitle:[@"About " stringByAppendingString:title] action:nil keyEquivalent:@""]];
        NSMenuItem *ai = [[NSMenuItem alloc] initWithTitle:title action:nil keyEquivalent:@""];
        ai.submenu = app;
        [bar addItem:ai];
        NSMenu *game = [[NSMenu alloc] initWithTitle:@"Game"];
        NSMenuItem *gi = [[NSMenuItem alloc] initWithTitle:@"Game" action:nil keyEquivalent:@""];
        gi.submenu = game;
        [bar addItem:gi];
        rt_hud_menu_add((__bridge void *)game);
        FILE *f = fopen(tsv, "w");
        if (!f) return -1;
        fprintf(f, "kind\ttitle\tkey\tmask\tenabled\ttooltip\n");
        for (NSMenuItem *it in bar.itemArray) dump_menu(f, it.submenu);
        return fclose(f) ? -1 : 0;
    }
}

static int write_png(CGImageRef img, const char *path)
{
    NSURL *u = [NSURL fileURLWithFileSystemRepresentation:path isDirectory:NO relativeToURL:nil];
    CGImageDestinationRef d = CGImageDestinationCreateWithURL((__bridge CFURLRef)u, CFSTR("public.png"), 1, NULL);
    if (!d) return -1;
    CGImageDestinationAddImage(d, img, NULL);
    int ok = CGImageDestinationFinalize(d);
    CFRelease(d);
    return ok ? 0 : -1;
}

int rt_hud_render(int state, const rt_hud_info *info, const char *png, const char *tsv)
{
    @autoreleasepool {
        NSArray<NSAttributedString *> *lines = hud_lines(state, info);
        FILE *f = fopen(tsv, "w");
        if (!f) return -1;
        for (NSAttributedString *l in lines) fprintf(f, "%s\n", l.string.UTF8String);
        if (fclose(f)) return -1;
        CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CGContextRef cg = CGBitmapContextCreate(NULL, CANVAS_W, CANVAS_H, 8, 0, cs, (CGBitmapInfo)kCGImageAlphaNoneSkipLast);
        CGColorSpaceRelease(cs);
        if (!cg) return -1;
        CGContextSetRGBFillColor(cg, 17 / 255.0, 34 / 255.0, 51 / 255.0, 1);
        CGContextFillRect(cg, CGRectMake(0, 0, CANVAS_W, CANVAS_H));
        CGContextSetShouldSmoothFonts(cg, false);
        CGSize sz = hud_box_size(lines);
        hud_draw_box(cg, CGPointMake(CANVAS_W - sz.width - MARGIN, CANVAS_H - sz.height - MARGIN), lines);
        CGImageRef img = CGBitmapContextCreateImage(cg);
        CGContextRelease(cg);
        int rc = img ? write_png(img, png) : -1;
        if (img) CGImageRelease(img);
        return rc;
    }
}
