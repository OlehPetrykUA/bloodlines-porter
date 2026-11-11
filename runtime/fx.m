#include "recomp_types.h"
#include "rt.h"
#include "fx.h"
#include "toggles.h"

#import <AppKit/AppKit.h>
#import <IOKit/graphics/IOGraphicsTypes.h>
#import <Metal/Metal.h>
#import <MetalFX/MetalFX.h>
#import <QuartzCore/CAMetalDisplayLink.h>
#import <QuartzCore/CAMetalLayer.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <SDL3/SDL.h>
#include <dlfcn.h>
#include <limits.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CAP 16384
#define PRESENT_MIN (1.0 / 60.0)
#define DRAWABLES 3

@interface RTFxView : NSView
@end

@implementation RTFxView
- (CALayer *)makeBackingLayer
{
    return [CAMetalLayer layer];
}
- (BOOL)wantsUpdateLayer
{
    return YES;
}
- (NSView *)hitTest:(NSPoint)p
{
    (void)p;
    return nil;
}
@end

static id<MTLDevice> s_dev;
static id<MTLCommandQueue> s_q;
static int s_probed, s_forced, s_sup_spatial, s_sup_temporal, s_sup_interp, s_sup_m4;
static char s_why_sp[96], s_why_tp[96], s_why_in[96];
static NSWindow *s_win;
static RTFxView *s_view;
static CAMetalLayer *s_layer;
static atomic_int s_live;
static atomic_int s_rx, s_ry, s_rw, s_rh;
static int s_shown = -1, s_pw = -1, s_ph = -1;
static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t s_cv = PTHREAD_COND_INITIALIZER;
static pthread_t s_thr;
static int s_thr_on;
static id<CAMetalDrawable> s_pend;
static uint64_t s_pend_v;
static uint32_t s_pend_i;
static int s_busy;
static id<MTLSharedEvent> s_fx_ev;
static uint64_t s_fx_v, s_seq;
static void (*s_sig_orig)(id, SEL, id);
static uint64_t s_frames, s_scaled, s_skipped, s_overwritten;
static float s_cost[CAP];
static uint8_t s_ok[CAP];
static id<MTLCommandBuffer> s_last_cb;
static unsigned s_builds;
static double s_build_ms;

static id<MTLFXSpatialScaler> s_sc;
static id<MTLTexture> s_mid;
static NSUInteger s_sc_iw, s_sc_ih, s_sc_ow, s_sc_oh;

static id<MTLFXSpatialScaler> make_scaler(NSUInteger iw, NSUInteger ih, NSUInteger ow, NSUInteger oh)
{
    uint64_t t = rt_now_ns();
    MTLFXSpatialScalerDescriptor *d = [MTLFXSpatialScalerDescriptor new];
    d.colorTextureFormat = MTLPixelFormatBGRA8Unorm;
    d.outputTextureFormat = MTLPixelFormatBGRA8Unorm;
    d.inputWidth = iw;
    d.inputHeight = ih;
    d.outputWidth = ow;
    d.outputHeight = oh;
    d.colorProcessingMode = MTLFXSpatialScalerColorProcessingModePerceptual;
    id<MTLFXSpatialScaler> sc = [d newSpatialScalerWithDevice:s_dev];
    double ms = (double)(rt_now_ns() - t) / 1e6;
    s_builds++;
    s_build_ms += ms;
    rt_log("fx pipelines spatial %lux%lu -> %lux%lu built in %.2f ms (%u this run, %.2f ms in all)%s", (unsigned long)iw,
           (unsigned long)ih, (unsigned long)ow, (unsigned long)oh, ms, s_builds, s_build_ms, sc ? "" : " failed");
    return sc;
}

static id<MTLTexture> make_tex(NSUInteger w, NSUInteger h, MTLTextureUsage u)
{
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                  width:w
                                                                                 height:h
                                                                              mipmapped:NO];
    d.usage = u;
    d.storageMode = MTLStorageModePrivate;
    return [s_dev newTextureWithDescriptor:d];
}

static int ensure_scaler(NSUInteger iw, NSUInteger ih, NSUInteger ow, NSUInteger oh)
{
    if (s_sc && s_sc_iw == iw && s_sc_ih == ih && s_sc_ow == ow && s_sc_oh == oh) return 0;
    s_sc = nil;
    id<MTLFXSpatialScaler> sc = make_scaler(iw, ih, ow, oh);
    if (!sc) return -1;
    s_mid = make_tex(ow, oh, sc.outputTextureUsage | MTLTextureUsageShaderRead);
    if (!s_mid) return -1;
    sc.outputTexture = s_mid;
    sc.inputContentWidth = iw;
    sc.inputContentHeight = ih;
    s_sc = sc;
    s_sc_iw = iw;
    s_sc_ih = ih;
    s_sc_ow = ow;
    s_sc_oh = oh;
    return 0;
}

static void why(char *b, size_t n, const char *what)
{
    if (s_forced) snprintf(b, n, "MetalFX forced off by RT_FX_FORCE_UNSUPPORTED");
    else if (!s_dev) snprintf(b, n, "No Metal device");
    else snprintf(b, n, "MetalFX %s is not supported on this Mac", what);
}

static void probe(void)
{
    if (s_probed) return;
    s_probed = 1;
    const char *f = getenv("RT_FX_FORCE_UNSUPPORTED");
    s_forced = f && f[0] == '1';
    s_dev = MTLCreateSystemDefaultDevice();
    int os26 = 0;
    if (s_dev) {
        s_sup_spatial = [MTLFXSpatialScalerDescriptor supportsDevice:s_dev];
        s_sup_temporal = [MTLFXTemporalScalerDescriptor supportsDevice:s_dev];
        if (@available(macOS 26.0, *)) {
            os26 = 1;
            s_sup_interp = [MTLFXFrameInterpolatorDescriptor supportsDevice:s_dev];
            s_sup_m4 = [MTLFXSpatialScalerDescriptor supportsMetal4FX:s_dev];
        }
    }
    if (s_forced) s_sup_spatial = s_sup_temporal = s_sup_interp = s_sup_m4 = 0;
    why(s_why_sp, sizeof s_why_sp, "spatial scaling");
    why(s_why_tp, sizeof s_why_tp, "temporal scaling");
    if (s_dev && !s_forced && !os26) snprintf(s_why_in, sizeof s_why_in, "MetalFX frame interpolation needs macOS 26");
    else why(s_why_in, sizeof s_why_in, "frame interpolation");
    rt_log("fx support spatial %d temporal %d interpolator %d metal4fx %d", s_sup_spatial, s_sup_temporal, s_sup_interp,
           s_sup_m4);
    if (!s_sup_spatial) rt_log("fx unsupported spatial (%s)", s_why_sp);
    if (!s_sup_temporal) rt_log("fx unsupported temporal (%s)", s_why_tp);
    if (!s_sup_interp) rt_log("fx unsupported interpolator (%s)", s_why_in);
}

static void avail(void)
{
    if (s_sup_spatial) rt_toggle_set_available(RT_TOG_PICTURE, 5u | (RT_ENGINE_OVERRIDE ? 1u << RT_PIC_NATIVE : 0u), "");
    else rt_toggle_set_available(RT_TOG_PICTURE, 0, s_why_sp);
}

int rt_fx_probe(void)
{
    probe();
    avail();
    return s_sup_spatial;
}

void rt_fx_support(int *spatial, int *temporal, int *interp, int *m4)
{
    probe();
    *spatial = s_sup_spatial;
    *temporal = s_sup_temporal;
    *interp = s_sup_interp;
    *m4 = s_sup_m4;
}

static void link_relayer(int fx);
static void link_stop(const char *why);
static int fx_link_wanted(void);

static void layout_main(NSWindow *win, int on, int pw, int ph)
{
    if (!win) return;
    int fresh = 0;
    if (on && (!s_view || s_view.window != win)) {
        fresh = 1;
        [s_view removeFromSuperview];
        RTFxView *v = [[RTFxView alloc] initWithFrame:win.contentView.bounds];
        v.wantsLayer = YES;
        v.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
        CAMetalLayer *l = (CAMetalLayer *)v.layer;
        l.device = s_dev;
        l.pixelFormat = MTLPixelFormatBGRA8Unorm;
        l.framebufferOnly = NO;
        l.displaySyncEnabled = rt_gfx_vsync(NULL) ? YES : NO;
        l.maximumDrawableCount = DRAWABLES;
        l.opaque = YES;
        l.contentsGravity = kCAGravityResizeAspect;
        l.backgroundColor = NSColor.blackColor.CGColor;
        NSView *cv = win.contentView, *game = nil;
        for (NSView *sv in cv.subviews)
            if (sv != v && [sv.layer isKindOfClass:[CAMetalLayer class]]) {
                game = sv;
                break;
            }
        if (game) [cv addSubview:v positioned:NSWindowAbove relativeTo:game];
        else [cv addSubview:v positioned:NSWindowBelow relativeTo:nil];
        s_view = v;
        pthread_mutex_lock(&s_mu);
        s_layer = l;
        pthread_mutex_unlock(&s_mu);
    }
    if (!s_view) return;
    s_view.frame = win.contentView.bounds;
    s_layer.contentsScale = win.backingScaleFactor;
    if (pw > 0 && ph > 0) s_layer.drawableSize = CGSizeMake(pw, ph);
    int covers = on && (fresh || s_view.hidden);
    s_view.hidden = !on;
    atomic_store(&s_live, on);
    if (covers && !fx_link_wanted()) link_stop("MetalFX view now covers the game layer, link restarts under it");
    int hide = on && fx_link_wanted();
    for (NSView *sv in win.contentView.subviews)
        if (sv != s_view && [sv.layer isKindOfClass:[CAMetalLayer class]] && sv.hidden != hide) {
            sv.hidden = hide;
            rt_log("gfx covered game view %s", hide ? "hidden" : "shown");
        }
    link_relayer(fx_link_wanted());
}

static void record(uint64_t seq, float ms)
{
    pthread_mutex_lock(&s_mu);
    s_cost[seq % CAP] += ms;
    pthread_mutex_unlock(&s_mu);
}

static pthread_mutex_t s_pace_mu = PTHREAD_MUTEX_INITIALIZER;
static CFTimeInterval s_pace_last;
static uint32_t s_pace_n, s_pace_missed, s_pace_short, s_pace_on, s_pace_long, s_st_n, s_st_on, s_st_over;
static double s_pace_sum, s_pace_sq, s_pace_max, s_st_sum, s_st_sq;

void rt_gfx_pace_reset(void)
{
    pthread_mutex_lock(&s_pace_mu);
    s_pace_last = 0;
    s_pace_n = s_pace_missed = s_pace_short = s_pace_on = s_pace_long = s_st_n = s_st_on = s_st_over = 0;
    s_pace_sum = s_pace_sq = s_pace_max = s_st_sum = s_st_sq = 0;
    pthread_mutex_unlock(&s_pace_mu);
}

void rt_gfx_pace_report(void)
{
    pthread_mutex_lock(&s_pace_mu);
    double n = s_pace_n ? s_pace_n : 1, mean = s_pace_sum / n, var = s_pace_sq / n - mean * mean;
    rt_log("shown n %u missed %u mean %.2f sd %.2f short %u on %u long %u max %.2f", s_pace_n, s_pace_missed, mean,
           var > 0 ? sqrt(var) : 0, s_pace_short, s_pace_on, s_pace_long, s_pace_max);
    double sn = s_st_n ? s_st_n : 1, sm = s_st_sum / sn, sv = s_st_sq / sn - sm * sm;
    rt_log("shown steady n %u mean %.2f sd %.2f on %.1f%% over-50ms %u", s_st_n, sm, sv > 0 ? sqrt(sv) : 0, 100.0 * s_st_on / sn, s_st_over);
    pthread_mutex_unlock(&s_pace_mu);
    rt_gfx_link_report();
    rt_gfx_present_trace_dump();
}

static void pace_count(id<MTLDrawable> d)
{
    [d addPresentedHandler:^(id<MTLDrawable> shown) {
        CFTimeInterval t = shown.presentedTime;
        pthread_mutex_lock(&s_pace_mu);
        if (t <= 0) {
            s_pace_missed++;
        } else {
            if (s_pace_last > 0) {
                double ms = (t - s_pace_last) * 1000.0;
                s_pace_n++;
                s_pace_sum += ms;
                s_pace_sq += ms * ms;
                if (ms > s_pace_max) s_pace_max = ms;
                if (ms < 12.5) s_pace_short++;
                else if (ms <= 20.8) s_pace_on++;
                else s_pace_long++;
                if (ms <= 50.0) {
                    s_st_n++;
                    s_st_sum += ms;
                    s_st_sq += ms * ms;
                    s_st_on += ms >= 12.5 && ms <= 20.8;
                } else {
                    s_st_over++;
                }
            }
            s_pace_last = t;
        }
        pthread_mutex_unlock(&s_pace_mu);
    }];
}

static atomic_int s_hidden;
static void (*s_present_orig)(id, SEL);

typedef struct {
    void *drawable, *texture;
    double call, done, shown;
    int mode, count;
    double arr, take, tgt, tpres, ask;
    double shownat, gone;
} ptrec;

static char s_link_key;
static char s_gone_key;

#define PT_CAP 131072
static ptrec *s_pt;
static uint32_t s_pt_n;

@interface RTGone : NSObject {
  @public
    uint32_t i;
}
@end

@implementation RTGone
- (void)dealloc
{
    if (s_pt && i < PT_CAP) s_pt[i].gone = CACurrentMediaTime();
}
@end
typedef struct {
    uint32_t key;
    double shown;
} fxshown_t;
static fxshown_t *s_fxs;
static uint32_t s_fxs_n;
static double (*s_fxg)[2];
static float *s_fxl;
static id<MTLBuffer> s_fxl_buf;
static uint64_t s_pt_v;
static pthread_mutex_t s_pt_mu = PTHREAD_MUTEX_INITIALIZER;
static id<MTLSharedEvent> s_pt_ev;
static MTLSharedEventListener *s_pt_ls;

static void pt_record(id queue, id<CAMetalDrawable> d, uint32_t i, uint64_t v)
{
    s_pt[i] = (ptrec){(__bridge void *)d, (__bridge void *)d.texture, CACurrentMediaTime(), 0, 0, atomic_load(&s_hidden),
                      (int)d.layer.maximumDrawableCount};
    NSArray<NSNumber *> *lk = objc_getAssociatedObject(d, &s_link_key);
    if (lk.count == 5) {
        s_pt[i].ask = lk[4].doubleValue;
        s_pt[i].arr = lk[0].doubleValue;
        s_pt[i].take = lk[1].doubleValue;
        s_pt[i].tgt = lk[2].doubleValue;
        s_pt[i].tpres = lk[3].doubleValue;
    }
    ((void (*)(id, SEL, id, uint64_t))objc_msgSend)(queue, sel_registerName("signalEvent:value:"), s_pt_ev, v);
    [s_pt_ev notifyListener:s_pt_ls atValue:v block:^(id<MTLSharedEvent> e, uint64_t val) {
        (void)e;
        (void)val;
        s_pt[i].done = CACurrentMediaTime();
    }];
    [d addPresentedHandler:^(id<MTLDrawable> x) {
        s_pt[i].shown = x.presentedTime;
        s_pt[i].shownat = CACurrentMediaTime();
    }];
    RTGone *g = [RTGone new];
    g->i = i;
    objc_setAssociatedObject(d, &s_gone_key, g, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}

static void sig_hook(id self, SEL sel, id drawable)
{
    uint32_t i = PT_CAP;
    id<CAMetalDrawable> d = drawable;
    if (s_pt) {
        pthread_mutex_lock(&s_pt_mu);
        i = s_pt_n < PT_CAP ? s_pt_n++ : PT_CAP;
        uint64_t v = ++s_pt_v;
        pthread_mutex_unlock(&s_pt_mu);
        if (i < PT_CAP) pt_record(self, d, i, v);
    }
    if (atomic_load(&s_live)) {
        id<CAMetalDrawable> old;
        pthread_mutex_lock(&s_mu);
        uint64_t fv = ++s_fx_v;
        ((void (*)(id, SEL, id, uint64_t))objc_msgSend)(self, sel_registerName("signalEvent:value:"), s_fx_ev, fv);
        old = s_pend;
        s_pend = d;
        s_pend_v = fv;
        s_pend_i = i;
        if (old) s_overwritten++;
        pthread_cond_signal(&s_cv);
        pthread_mutex_unlock(&s_mu);
        old = nil;
    }
    s_sig_orig(self, sel, drawable);
}

static int sig_install(void)
{
    static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
    int rc = 0;
    pthread_mutex_lock(&mu);
    if (!s_sig_orig) {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        id q = ((id (*)(id, SEL))objc_msgSend)(dev, sel_registerName("newMTL4CommandQueue"));
        Method m = q ? class_getInstanceMethod([q class], sel_registerName("signalDrawable:")) : NULL;
        if (m) {
            s_sig_orig = (void (*)(id, SEL, id))method_getImplementation(m);
            method_setImplementation(m, (IMP)sig_hook);
            rt_log("gfx drawable signal hook on, class %s", class_getName([q class]));
        } else {
            rc = -1;
        }
    }
    pthread_mutex_unlock(&mu);
    return rc;
}

typedef struct {
    double t;
    const char *what;
    long old, now;
    char lib[24];
} ptset;

#define PS_CAP 65536
static ptset *s_ps;
static uint32_t s_ps_n, s_ps_same;
static void (*s_setsync_orig)(id, SEL, BOOL);
static void (*s_setcount_orig)(id, SEL, NSUInteger);
static void (*s_setsize_orig)(id, SEL, CGSize);

static void ps_note(const char *what, long old, long now, void *ret)
{
    pthread_mutex_lock(&s_pt_mu);
    if (old == now) {
        s_ps_same++;
    } else if (s_ps_n < PS_CAP) {
        ptset *p = &s_ps[s_ps_n++];
        p->t = CACurrentMediaTime();
        p->what = what;
        p->old = old;
        p->now = now;
        Dl_info di;
        const char *base = dladdr(ret, &di) && di.dli_fname ? strrchr(di.dli_fname, '/') : NULL;
        snprintf(p->lib, sizeof p->lib, "%s", base ? base + 1 : "?");
    }
    pthread_mutex_unlock(&s_pt_mu);
}

static void tr_setsync(id self, SEL sel, BOOL v)
{
    ps_note("sync", ((CAMetalLayer *)self).displaySyncEnabled, v, __builtin_return_address(0));
    s_setsync_orig(self, sel, v);
}

static void tr_setcount(id self, SEL sel, NSUInteger v)
{
    ps_note("count", (long)((CAMetalLayer *)self).maximumDrawableCount, (long)v, __builtin_return_address(0));
    s_setcount_orig(self, sel, v);
}

typedef struct {
    double t0, t1;
    void *tex;
    int count;
} pacq;

#define PA_CAP 131072
static pacq *s_pa;
static uint32_t s_pa_n;
static id (*s_next_orig)(id, SEL);

static id tr_next(id self, SEL sel)
{
    double t0 = CACurrentMediaTime();
    id d = s_next_orig(self, sel);
    double t1 = CACurrentMediaTime();
    pthread_mutex_lock(&s_pt_mu);
    if (s_pa_n < PA_CAP)
        s_pa[s_pa_n++] = (pacq){t0, t1, d ? (__bridge void *)((id<CAMetalDrawable>)d).texture : NULL, (int)((CAMetalLayer *)self).maximumDrawableCount};
    pthread_mutex_unlock(&s_pt_mu);
    return d;
}

static void tr_setsize(id self, SEL sel, CGSize v)
{
    CGSize o = ((CAMetalLayer *)self).drawableSize;
    ps_note("size", (long)o.width * 100000 + (long)o.height, (long)v.width * 100000 + (long)v.height, __builtin_return_address(0));
    s_setsize_orig(self, sel, v);
}

static void pt_install(void)
{
    if (!getenv("RT_PRESENT_TRACE")) return;
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
    if (sig_install()) {
        rt_log("gfx present trace: no MTL4 signalDrawable");
        return;
    }
    Class lc = [CAMetalLayer class];
    s_ps = calloc(PS_CAP, sizeof *s_ps);
    Method msync = class_getInstanceMethod(lc, @selector(setDisplaySyncEnabled:)), mcount = class_getInstanceMethod(lc, @selector(setMaximumDrawableCount:)),
           msize = class_getInstanceMethod(lc, @selector(setDrawableSize:)), mnext = class_getInstanceMethod(lc, @selector(nextDrawable));
    s_setsync_orig = (void (*)(id, SEL, BOOL))method_getImplementation(msync);
    method_setImplementation(msync, (IMP)tr_setsync);
    s_setcount_orig = (void (*)(id, SEL, NSUInteger))method_getImplementation(mcount);
    method_setImplementation(mcount, (IMP)tr_setcount);
    s_setsize_orig = (void (*)(id, SEL, CGSize))method_getImplementation(msize);
    method_setImplementation(msize, (IMP)tr_setsize);
    s_pa = calloc(PA_CAP, sizeof *s_pa);
    s_next_orig = (id (*)(id, SEL))method_getImplementation(mnext);
    method_setImplementation(mnext, (IMP)tr_next);
    s_fxs = calloc(PT_CAP, sizeof *s_fxs);
    s_fxg = calloc(PT_CAP, sizeof *s_fxg);
    s_fxl = malloc(PT_CAP * sizeof *s_fxl);
    for (uint32_t k = 0; s_fxl && k < PT_CAP; k++) s_fxl[k] = -1.0f;
    s_fxl_buf = [dev newBufferWithLength:(NSUInteger)PT_CAP * 16u * 4u options:MTLResourceStorageModeShared];
    s_pt_ev = [dev newSharedEvent];
    s_pt_ls = [[MTLSharedEventListener alloc] initWithDispatchQueue:dispatch_queue_create("pt", DISPATCH_QUEUE_SERIAL)];
    s_pt = calloc(PT_CAP, sizeof *s_pt);
    rt_log("gfx present trace on");
}

void rt_gfx_present_trace_dump(void)
{
    const char *p = getenv("RT_PRESENT_TRACE");
    if (!p || !s_pt) return;
    FILE *f = fopen(p, "w");
    if (!f) return;
    pthread_mutex_lock(&s_pt_mu);
    uint32_t n = s_pt_n;
    pthread_mutex_unlock(&s_pt_mu);
    uint32_t nf = s_fxs_n < PT_CAP ? s_fxs_n : PT_CAP;
    double *fxs = calloc(n ? n : 1, sizeof *fxs);
    for (uint32_t k = 0; k < nf; k++)
        if (s_fxs[k].key < n) fxs[s_fxs[k].key] = s_fxs[k].shown;
    fprintf(f, "i drawable texture call done shown mode count arrive take target targetpresent ask fxshown fxgpustart fxgpuend shownat gone fxluma\n");
    for (uint32_t i = 0; i < n; i++)
        fprintf(f, "%u %p %p %.6f %.6f %.6f %d %d %.6f %.6f %.6f %.6f %.6f %.6f %.6f %.6f %.6f %.6f %.1f\n", i, s_pt[i].drawable, s_pt[i].texture,
                s_pt[i].call, s_pt[i].done, s_pt[i].shown, s_pt[i].mode, s_pt[i].count, s_pt[i].arr, s_pt[i].take, s_pt[i].tgt, s_pt[i].tpres, s_pt[i].ask,
                fxs[i], s_fxg[i][0], s_fxg[i][1], s_pt[i].shownat, s_pt[i].gone, s_fxl ? s_fxl[i] : -1.0f);
    free(fxs);
    fclose(f);
    char sp[PATH_MAX];
    snprintf(sp, sizeof sp, "%s.sets", p);
    f = fopen(sp, "w");
    if (f) {
        pthread_mutex_lock(&s_pt_mu);
        fprintf(f, "t what old now lib (unchanged sets %u)\n", s_ps_same);
        for (uint32_t i = 0; i < s_ps_n; i++) fprintf(f, "%.6f %s %ld %ld %s\n", s_ps[i].t, s_ps[i].what, s_ps[i].old, s_ps[i].now, s_ps[i].lib);
        pthread_mutex_unlock(&s_pt_mu);
        fclose(f);
    }
    snprintf(sp, sizeof sp, "%s.acq", p);
    f = fopen(sp, "w");
    if (f) {
        pthread_mutex_lock(&s_pt_mu);
        fprintf(f, "t0 t1 texture count\n");
        for (uint32_t i = 0; i < s_pa_n; i++) fprintf(f, "%.6f %.6f %p %d\n", s_pa[i].t0, s_pa[i].t1, s_pa[i].tex, s_pa[i].count);
        pthread_mutex_unlock(&s_pt_mu);
        fclose(f);
    }
    rt_log("gfx present trace: %u presents written to %s", n, p);
}

static int s_pacing = 1;

#define LINK_MS 50
#define LINK_GIVE_UP_MS 150
#define LINK_STR_(x) #x
#define LINK_STR(x) LINK_STR_(x)
enum { LINK_NONE, LINK_RUNNING, LINK_STOPPING };
static int s_link_on;
static int s_link_state, s_link_plain, s_link_fx;
static CAMetalDisplayLink *s_link;
static CAMetalLayer *s_link_layer;
static CAMetalDisplayLinkUpdate *s_fx_upd;
static int fx_layer(id l);
static NSThread *s_link_thread;
static RECOMP_TLS int t_link_thread;
static CFRunLoopRef s_link_rl;
static CAMetalDisplayLinkUpdate *s_upd;
static double s_upd_t;
static pthread_mutex_t s_link_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t s_link_cv = PTHREAD_COND_INITIALIZER;
static _Atomic uint64_t s_link_n, s_link_take, s_link_skip, s_link_us, s_link_fallback, s_link_late, s_link_late_us, s_link_starts, s_link_stops;
static id (*s_lnext_orig)(id, SEL);
static _Atomic int s_live_n;
static _Atomic uint64_t s_link_starved, s_gave_up_ns, s_back_logs;
static __weak id s_lived[16];
static int s_live_i;
static pthread_mutex_t s_live_mu = PTHREAD_MUTEX_INITIALIZER;
static char s_live_key;

@interface RTLive : NSObject {
  @public
    int presented, plain;
}
@end

@implementation RTLive
- (void)dealloc
{
    atomic_fetch_sub(&s_live_n, 1);
}
@end

static RTLive *live_of(id d) { return d ? objc_getAssociatedObject(d, &s_live_key) : nil; }

static void live_tag(id d, int plain)
{
    if (!d || live_of(d)) return;
    RTLive *l = [RTLive new];
    l->plain = plain;
    objc_setAssociatedObject(d, &s_live_key, l, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    atomic_fetch_add(&s_live_n, 1);
    pthread_mutex_lock(&s_live_mu);
    s_lived[s_live_i++ % 16] = d;
    pthread_mutex_unlock(&s_live_mu);
}

static void live_dump(const char *why)
{
    char b[640];
    size_t o = 0;
    b[0] = 0;
    pthread_mutex_lock(&s_live_mu);
    for (int k = 0; k < 16 && o < sizeof b - 48; k++) {
        id d = s_lived[k];
        if (!d) continue;
        RTLive *l = live_of(d);
        o += (size_t)snprintf(b + o, sizeof b - o, " %p %s%s", (__bridge void *)((id<CAMetalDrawable>)d).texture, l && l->presented ? "presented" : "unpresented",
                              l && l->plain ? " plain" : " link");
    }
    pthread_mutex_unlock(&s_live_mu);
    rt_log("gfx live game drawables at %s: %d alive:%s", why, atomic_load(&s_live_n), b);
}

static uint32_t s_lsid[4];
static unsigned s_lsid_i;

static void lsurf_note(id<CAMetalDrawable> d)
{
    static uint32_t (*get_id)(IOSurfaceRef);
    if (!get_id) get_id = (uint32_t (*)(IOSurfaceRef))dlsym(RTLD_DEFAULT, "IOSurfaceGetID");
    IOSurfaceRef s = d.texture.iosurface;
    if (!s || !get_id) return;
    pthread_mutex_lock(&s_live_mu);
    s_lsid[s_lsid_i++ % 4] = get_id(s);
    pthread_mutex_unlock(&s_live_mu);
}

static void lsurf_dump(void)
{
    IOSurfaceRef (*lookup)(uint32_t) = (IOSurfaceRef (*)(uint32_t))dlsym(RTLD_DEFAULT, "IOSurfaceLookup");
    Boolean (*in_use)(IOSurfaceRef) = (Boolean (*)(IOSurfaceRef))dlsym(RTLD_DEFAULT, "IOSurfaceIsInUse");
    int32_t (*local)(IOSurfaceRef) = (int32_t (*)(IOSurfaceRef))dlsym(RTLD_DEFAULT, "IOSurfaceGetUseCount");
    if (!lookup || !in_use || !local) return;
    char b[320];
    size_t o = 0;
    b[0] = 0;
    pthread_mutex_lock(&s_live_mu);
    for (unsigned k = 0; k < 4 && k < s_lsid_i; k++) {
        uint32_t id = s_lsid[(s_lsid_i - 1 - k) % 4];
        IOSurfaceRef s = lookup(id);
        o += (size_t)snprintf(b + o, sizeof b - o, " %u %s local %d", id, !s ? "gone" : in_use(s) ? "in-use" : "free", s ? local(s) : 0);
        if (s) CFRelease(s);
    }
    pthread_mutex_unlock(&s_live_mu);
    rt_log("gfx link surfaces at the stall, newest first:%s; updates %llu skipped %llu", b, (unsigned long long)atomic_load(&s_link_n),
           (unsigned long long)atomic_load(&s_link_skip));
}

uint64_t rt_gfx_link_starved(void) { return atomic_load(&s_link_starved); }

int rt_gfx_link_layer(void)
{
    pthread_mutex_lock(&s_link_mu);
    int r = s_link_state == LINK_RUNNING ? (s_link_fx ? 2 : 1) : 0;
    pthread_mutex_unlock(&s_link_mu);
    return r;
}

@interface RTLinkDelegate : NSObject <CAMetalDisplayLinkDelegate>
@end

@implementation RTLinkDelegate
- (void)metalDisplayLink:(CAMetalDisplayLink *)link needsUpdate:(CAMetalDisplayLinkUpdate *)update
{
    (void)link;
    atomic_fetch_add(&s_link_n, 1);
    pthread_mutex_lock(&s_link_mu);
    if (s_upd) atomic_fetch_add(&s_link_skip, 1);
    s_upd = update;
    s_upd_t = CACurrentMediaTime();
    pthread_cond_broadcast(&s_link_cv);
    pthread_mutex_unlock(&s_link_mu);
    uint64_t g = atomic_exchange(&s_gave_up_ns, 0);
    if (g && atomic_fetch_add(&s_back_logs, 1) < 64)
        rt_log("gfx display link back %llu ms after giving up", (unsigned long long)((rt_now_ns() - g) / 1000000u));
}
@end

static RTLinkDelegate *s_link_del;

static void link_on_thread(void (^block)(void))
{
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    CFRunLoopPerformBlock(s_link_rl, kCFRunLoopDefaultMode, ^{
        block();
        dispatch_semaphore_signal(done);
    });
    CFRunLoopWakeUp(s_link_rl);
    dispatch_semaphore_wait(done, DISPATCH_TIME_FOREVER);
}

static void link_stop(const char *why)
{
    pthread_mutex_lock(&s_link_mu);
    if (s_link_state != LINK_RUNNING) {
        pthread_mutex_unlock(&s_link_mu);
        return;
    }
    s_link_state = LINK_STOPPING;
    CAMetalDisplayLink *l = s_link;
    s_link = nil;
    s_upd = nil;
    s_fx_upd = nil;
    pthread_mutex_unlock(&s_link_mu);
    link_on_thread(^{
        [l invalidate];
    });
    pthread_mutex_lock(&s_link_mu);
    s_link_state = LINK_NONE;
    pthread_cond_broadcast(&s_link_cv);
    pthread_mutex_unlock(&s_link_mu);
    atomic_fetch_add(&s_link_stops, 1);
    rt_log("gfx display link stopped: %s", why);
}

static id link_next(id self, SEL sel)
{
    if (t_link_thread) return s_lnext_orig(self, sel);
    uint64_t t0 = rt_now_ns();
    double ask = CACurrentMediaTime();
    CAMetalDisplayLinkUpdate *u = nil;
    double ut = 0;
    int other = 0, relay = 0;
    pthread_mutex_lock(&s_link_mu);
    for (;;) {
        relay = s_link_fx && s_link_layer && self != s_link_layer && !fx_layer(self);
        if (s_link_layer && self != s_link_layer && !relay) {
            other = 1;
            s_link_plain++;
            break;
        }
        if (s_link_state == LINK_RUNNING && s_upd) {
            u = s_upd;
            ut = s_upd_t;
            s_upd = nil;
            if (relay) {
                if (s_fx_upd) atomic_fetch_add(&s_link_skip, 1);
                s_fx_upd = u;
                s_link_plain++;
            }
            break;
        }
        if (s_link_state == LINK_NONE) {
            s_link_plain++;
            break;
        }
        uint64_t waited = (rt_now_ns() - t0) / 1000000u;
        if (s_link_state == LINK_RUNNING && (atomic_load(&s_hidden) || waited >= LINK_GIVE_UP_MS)) {
            pthread_mutex_unlock(&s_link_mu);
            if (!atomic_load(&s_hidden)) {
                atomic_fetch_add(&s_link_starved, 1);
                if (atomic_load(&s_link_starved) <= 12) {
                    live_dump("a link stall");
                    if (s_pt) lsurf_dump();
                }
            }
            int gave_up = !atomic_load(&s_hidden);
            link_stop(gave_up ? "no update for " LINK_STR(LINK_GIVE_UP_MS) " ms" : "window hidden");
            if (gave_up) atomic_store(&s_gave_up_ns, rt_now_ns());
            pthread_mutex_lock(&s_link_mu);
            continue;
        }
        struct timespec dl;
        clock_gettime(CLOCK_REALTIME, &dl);
        dl.tv_nsec += LINK_MS * 1000000L;
        dl.tv_sec += dl.tv_nsec / 1000000000L;
        dl.tv_nsec %= 1000000000L;
        pthread_cond_timedwait(&s_link_cv, &s_link_mu, &dl);
    }
    pthread_mutex_unlock(&s_link_mu);
    if (other) {
        uint64_t ta = rt_now_ns();
        id od = s_lnext_orig(self, sel);
        uint64_t waited_ms = (rt_now_ns() - ta) / 1000000u;
        if (waited_ms >= 500 && !atomic_load(&s_hidden)) {
            rt_log("gfx plain acquire on the %s layer waited %llu ms", fx_layer(self) ? "MetalFX" : "game", (unsigned long long)waited_ms);
            live_dump("a long plain acquire");
        }
        pthread_mutex_lock(&s_link_mu);
        s_link_plain--;
        pthread_cond_broadcast(&s_link_cv);
        pthread_mutex_unlock(&s_link_mu);
        return od;
    }
    atomic_fetch_add(&s_link_us, (rt_now_ns() - t0) / 1000);
    if (!u) {
        atomic_fetch_add(&s_link_fallback, 1);
        uint64_t tf = rt_now_ns();
        id d = s_lnext_orig(self, sel);
        uint64_t fallback_ms = (rt_now_ns() - tf) / 1000000u;
        if (fallback_ms >= 100 && !atomic_load(&s_hidden))
            rt_log("gfx fallback acquire on the %s layer waited %llu ms", fx_layer(self) ? "MetalFX" : "game", (unsigned long long)fallback_ms);
        live_tag(d, 1);
        pthread_mutex_lock(&s_link_mu);
        s_link_plain--;
        pthread_cond_broadcast(&s_link_cv);
        pthread_mutex_unlock(&s_link_mu);
        return d;
    }
    atomic_fetch_add(&s_link_take, 1);
    if (relay) {
        uint64_t tr = rt_now_ns();
        id<CAMetalDrawable> gd = s_lnext_orig(self, sel);
        uint64_t relay_ms = (rt_now_ns() - tr) / 1000000u;
        if (relay_ms >= 500 && !atomic_load(&s_hidden)) {
            rt_log("gfx plain acquire on the covered game layer waited %llu ms", (unsigned long long)relay_ms);
            live_dump("a long covered-layer acquire");
        }
        pthread_mutex_lock(&s_link_mu);
        s_link_plain--;
        pthread_cond_broadcast(&s_link_cv);
        pthread_mutex_unlock(&s_link_mu);
        live_tag(gd, 1);
        if (s_pt && gd) {
            objc_setAssociatedObject(gd, &s_link_key, @[@(ut), @(CACurrentMediaTime()), @(u.targetTimestamp), @(u.targetPresentationTimestamp), @(ask)],
                                     OBJC_ASSOCIATION_RETAIN_NONATOMIC);
            lsurf_note(gd);
        }
        return gd;
    }
    id<CAMetalDrawable> d = u.drawable;
    live_tag(d, 0);
    if (s_pt) {
        objc_setAssociatedObject(d, &s_link_key, @[@(ut), @(CACurrentMediaTime()), @(u.targetTimestamp), @(u.targetPresentationTimestamp), @(ask)],
                                 OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        lsurf_note(d);
    }
    return d;
}

static void link_relayer(int fx)
{
    pthread_mutex_lock(&s_link_mu);
    int wrong = s_link_state == LINK_RUNNING && s_link_fx != fx;
    pthread_mutex_unlock(&s_link_mu);
    if (wrong) link_stop(fx ? "picture mode MetalFX, link moves to its layer" : "picture mode off, link moves to the game layer");
}

static void link_start(CAMetalLayer *ml, int fx)
{
    pthread_mutex_lock(&s_link_mu);
    while (s_link_state == LINK_NONE && s_link_plain) pthread_cond_wait(&s_link_cv, &s_link_mu);
    int idle = s_link_state == LINK_NONE;
    if (idle) {
        s_link_state = LINK_STOPPING;
        s_link_layer = ml;
        s_link_fx = fx;
    }
    pthread_mutex_unlock(&s_link_mu);
    if (!idle) return;
    if (!s_link_thread) {
        dispatch_semaphore_t up = dispatch_semaphore_create(0);
        s_link_del = [RTLinkDelegate new];
        s_link_thread = [[NSThread alloc] initWithBlock:^{
            t_link_thread = 1;
            s_link_rl = CFRunLoopGetCurrent();
            [[NSRunLoop currentRunLoop] addPort:[NSMachPort port] forMode:NSDefaultRunLoopMode];
            dispatch_semaphore_signal(up);
            for (;;) {
                @autoreleasepool {
                    [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate distantFuture]];
                }
            }
        }];
        s_link_thread.qualityOfService = NSQualityOfServiceUserInteractive;
        [s_link_thread start];
        dispatch_semaphore_wait(up, DISPATCH_TIME_FOREVER);
    }
    CAMetalDisplayLink *l = [[CAMetalDisplayLink alloc] initWithMetalLayer:ml];
    l.delegate = s_link_del;
    l.preferredFrameRateRange = CAFrameRateRangeMake(60, 60, 60);
    l.preferredFrameLatency = 1.0f;
    link_on_thread(^{
        [l addToRunLoop:[NSRunLoop currentRunLoop] forMode:NSDefaultRunLoopMode];
    });
    pthread_mutex_lock(&s_link_mu);
    s_link = l;
    s_upd = nil;
    s_link_state = LINK_RUNNING;
    pthread_cond_broadcast(&s_link_cv);
    pthread_mutex_unlock(&s_link_mu);
    if (atomic_fetch_add(&s_link_starts, 1) == 0) rt_log("gfx display link on the %s layer: 60 Hz, frame latency 1", fx ? "MetalFX" : "game");
    else rt_log("gfx display link restarted on the %s layer", fx ? "MetalFX" : "game");
}

static _Atomic uint64_t s_cfg_n, s_cfg_linked;
static void (*s_gcount_orig)(id, SEL, NSUInteger);
static _Atomic int s_vsync = -1, s_fx_link_ok = 1;

static int fx_layer(id l) { return [[(CALayer *)l delegate] isKindOfClass:[RTFxView class]]; }

static NSWindow *cocoa_window(void *sdl_window);

static int screen_downscaled(NSScreen *sc)
{
    CGDirectDisplayID did = [sc.deviceDescription[@"NSScreenNumber"] unsignedIntValue];
    CGDisplayModeRef cur = CGDisplayCopyDisplayMode(did);
    size_t fb = cur ? CGDisplayModeGetPixelWidth(cur) : 0, panel = 0;
    if (cur) CGDisplayModeRelease(cur);
    NSArray *all = CFBridgingRelease(
        CGDisplayCopyAllDisplayModes(did, (__bridge CFDictionaryRef) @{(__bridge NSString *)kCGDisplayShowDuplicateLowResolutionModes : @YES}));
    for (id m in all)
        if (CGDisplayModeGetIOFlags((__bridge CGDisplayModeRef)m) & kDisplayModeNativeFlag)
            panel = MAX(panel, CGDisplayModeGetPixelWidth((__bridge CGDisplayModeRef)m));
    rt_log("gfx launch screen framebuffer %zu px wide, panel %zu px", fb, panel);
    return panel && fb > panel;
}

int rt_gfx_vsync(void *sdl_window)
{
    if (s_vsync < 0) {
        NSWindow *w = sdl_window ? cocoa_window(sdl_window) : nil;
        NSScreen *sc = w.screen;
        for (NSScreen *s in NSScreen.screens)
            if (!sc && w && NSPointInRect(NSMakePoint(NSMidX(w.frame), NSMidY(w.frame)), s.frame)) sc = s;
        if (!sc) sc = NSScreen.mainScreen;
        int full = rt_window_launch_full(), fixed = sc && sc.maximumRefreshInterval <= sc.minimumRefreshInterval, fx = rt_fx_mode_on();
        int down = !fixed && sc && screen_downscaled(sc), vrr_win = !full && !fixed && sc;
        s_vsync = rt_gfx_link_paced() && ((full && fixed && !fx) || down || vrr_win);
        s_fx_link_ok = !full || s_vsync;
        const char *fl = getenv("RT_FX_LINK");
        if (fl && (!strcmp(fl, "0") || !strcmp(fl, "1"))) {
            s_fx_link_ok = fl[0] == '1';
            rt_log("gfx RT_FX_LINK=%s: the MetalFX link %s", fl, s_fx_link_ok ? "moves to the MetalFX layer" : "stays on the game layer");
        }
        if (s_vsync && down)
            rt_log("gfx vsync on: variable-refresh launch screen drawn larger than its panel, FIFO swapchain from creation%s",
                   fx ? " and the MetalFX layer synced" : "");
        else if (s_vsync && vrr_win)
            rt_log("gfx vsync on: launched in a window on a variable-refresh screen, FIFO swapchain from creation%s",
                   fx ? " and the MetalFX layer synced" : "");
        else if (s_vsync) rt_log("gfx vsync on: launched in full screen on a fixed-refresh screen, FIFO swapchain from creation");
        else if (!full) rt_log("gfx vsync off: launched in a window, the game's immediate present");
        else if (!fixed)
            rt_log("gfx vsync off: launch screen refreshes every %.2f to %.2f ms, the game's immediate present", sc.minimumRefreshInterval * 1000.0,
                   sc.maximumRefreshInterval * 1000.0);
        else rt_log("gfx vsync off: launched in full screen with MetalFX, the game's immediate present");
        if (!s_fx_link_ok) rt_log("gfx MetalFX link stays on the game layer for this full-screen launch");
    }
    return s_vsync;
}

static int fx_link_wanted(void)
{
    if (s_vsync < 0) rt_gfx_vsync(NULL);
    return atomic_load(&s_live) && s_fx_link_ok;
}

void rt_gfx_vsync_set(int on) { s_vsync = on; }

static void g_setcount(id self, SEL sel, NSUInteger v)
{
    if (!fx_layer(self)) {
        atomic_fetch_add(&s_cfg_n, 1);
        pthread_mutex_lock(&s_link_mu);
        int linked = s_link_layer == self && s_link_state != LINK_NONE;
        pthread_mutex_unlock(&s_link_mu);
        if (linked) atomic_fetch_add(&s_cfg_linked, 1);
    }
    s_gcount_orig(self, sel, v);
}

static void g_install(void)
{
    Method mc = class_getInstanceMethod([CAMetalLayer class], @selector(setMaximumDrawableCount:));
    if (!mc) return;
    s_gcount_orig = (void (*)(id, SEL, NSUInteger))method_getImplementation(mc);
    method_setImplementation(mc, (IMP)g_setcount);
}

void rt_gfx_cfg_counts(uint64_t *n, uint64_t *linked)
{
    *n = atomic_load(&s_cfg_n);
    *linked = atomic_load(&s_cfg_linked);
}

void rt_gfx_window_gone(void)
{
    if (!atomic_exchange(&s_hidden, 1)) rt_log("gfx window gone, unpaced");
    pthread_mutex_lock(&s_link_mu);
    pthread_cond_broadcast(&s_link_cv);
    pthread_mutex_unlock(&s_link_mu);
}

void rt_gfx_link_report(void)
{
    if (!s_link_on) return;
    rt_log("gfx game layer reconfigured %llu times, %llu with a display link attached", (unsigned long long)atomic_load(&s_cfg_n),
           (unsigned long long)atomic_load(&s_cfg_linked));
    uint64_t t = atomic_load(&s_link_take);
    uint64_t l = atomic_load(&s_link_late);
    rt_log("gfx display link starts %llu stops %llu updates %llu taken %llu skipped %llu wait mean %.2f ms plain acquires %llu presents past the deadline %llu mean %.2f ms",
           (unsigned long long)atomic_load(&s_link_starts), (unsigned long long)atomic_load(&s_link_stops),
           (unsigned long long)atomic_load(&s_link_n), (unsigned long long)t, (unsigned long long)atomic_load(&s_link_skip),
           t ? atomic_load(&s_link_us) / 1000.0 / t : 0.0, (unsigned long long)atomic_load(&s_link_fallback), (unsigned long long)l,
           l ? atomic_load(&s_link_late_us) / 1000.0 / l : 0.0);
}

static void paced_present(id self, SEL sel)
{
    id<CAMetalDrawable> cd = (id<CAMetalDrawable>)self;
    RTLive *lv = live_of(cd);
    if (lv) lv->presented = 1;
    pthread_mutex_lock(&s_link_mu);
    int linked = s_link_on && cd.layer == s_link_layer;
    int relayed = s_link_on && s_link_fx && s_link_state != LINK_NONE;
    pthread_mutex_unlock(&s_link_mu);
    if (relayed && !linked) {
        s_present_orig(self, sel);
        return;
    }
    if (linked && !atomic_load(&s_hidden)) {
        if (!atomic_load(&s_live)) pace_count(cd);
        NSArray<NSNumber *> *lk = s_pt ? objc_getAssociatedObject(cd, &s_link_key) : nil;
        if (lk.count == 5) {
            double late = CACurrentMediaTime() - lk[2].doubleValue;
            if (late > 0) {
                atomic_fetch_add(&s_link_late, 1);
                atomic_fetch_add(&s_link_late_us, (uint64_t)(late * 1e6));
            }
        }
        s_present_orig(self, sel);
        return;
    }
    if (!s_pacing || atomic_load(&s_hidden)) {
        s_present_orig(self, sel);
        return;
    }
    id<MTLDrawable> d = self;
    if (!atomic_load(&s_live)) pace_count(d);
    [d presentAfterMinimumDuration:PRESENT_MIN];
}

static void present(id<CAMetalDrawable> gd, uint64_t fv, uint32_t ti)
{
    pthread_mutex_lock(&s_mu);
    CAMetalLayer *layer = s_layer;
    pthread_mutex_unlock(&s_mu);
    if (!layer || !atomic_load(&s_live)) {
        s_skipped++;
        return;
    }
    id<MTLTexture> src = gd.texture;
    static int s_src_logged;
    if (src.pixelFormat != MTLPixelFormatBGRA8Unorm || !(src.usage & MTLTextureUsageShaderRead)) {
        if (!s_src_logged++)
            rt_log("fx game drawable not usable as scaler input: format %lu usage 0x%lx", (unsigned long)src.pixelFormat, (unsigned long)src.usage);
        s_skipped++;
        return;
    }
    uint64_t seq = ++s_seq;
    s_cost[seq % CAP] = 0;
    s_ok[seq % CAP] = 0;
    s_frames++;
    id<CAMetalDrawable> dr = nil;
    pthread_mutex_lock(&s_link_mu);
    int linked = s_link_layer == layer && s_link_state != LINK_NONE;
    if (linked) {
        dr = s_fx_upd.drawable;
        s_fx_upd = nil;
    }
    pthread_mutex_unlock(&s_link_mu);
    if (!linked) dr = [layer nextDrawable];
    if (!dr) {
        s_skipped++;
        return;
    }
    uint64_t t = rt_now_ns();
    int dw = (int)dr.texture.width, dh = (int)dr.texture.height;
    int rx = atomic_load(&s_rx), ry = atomic_load(&s_ry), rw = atomic_load(&s_rw), rh = atomic_load(&s_rh);
    if (rw <= 0 || rh <= 0 || rx < 0 || ry < 0 || rx + rw > dw || ry + rh > dh) {
        rx = ry = 0;
        rw = dw;
        rh = dh;
    }
    if (ensure_scaler(src.width, src.height, (NSUInteger)rw, (NSUInteger)rh)) {
        s_skipped++;
        return;
    }
    static unsigned long s_geo[6];
    unsigned long geo[6] = {(unsigned long)src.width, (unsigned long)src.height, (unsigned long)rx, (unsigned long)ry, (unsigned long)rw, (unsigned long)rh};
    if (memcmp(geo, s_geo, sizeof geo)) {
        memcpy(s_geo, geo, sizeof geo);
        rt_log("fx game drawable %lux%lu usage 0x%lx -> window drawable %dx%d, picture at %d,%d %dx%d", geo[0], geo[1], (unsigned long)src.usage, dw, dh, rx, ry, rw, rh);
    }
    id<MTLCommandBuffer> cb = [s_q commandBuffer];
    [cb encodeWaitForEvent:s_fx_ev value:fv];
    s_sc.colorTexture = src;
    [s_sc encodeToCommandBuffer:cb];
    id<MTLBlitCommandEncoder> b;
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture = dr.texture;
    rp.colorAttachments[0].loadAction = MTLLoadActionClear;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 0, 1);
    rp.colorAttachments[0].storeAction = MTLStoreActionStore;
    [[cb renderCommandEncoderWithDescriptor:rp] endEncoding];
    b = [cb blitCommandEncoder];
    [b copyFromTexture:s_mid
               sourceSlice:0
               sourceLevel:0
              sourceOrigin:MTLOriginMake(0, 0, 0)
                sourceSize:MTLSizeMake((NSUInteger)rw, (NSUInteger)rh, 1)
                 toTexture:dr.texture
          destinationSlice:0
          destinationLevel:0
         destinationOrigin:MTLOriginMake((NSUInteger)rx, (NSUInteger)ry, 0)];
    id<MTLBuffer> lb = s_fxl_buf;
    if (lb && s_fxl && ti < PT_CAP)
        for (int k = 0; k < 16; k++)
            [b copyFromTexture:dr.texture
                             sourceSlice:0
                             sourceLevel:0
                            sourceOrigin:MTLOriginMake((NSUInteger)(rx + (2 * (k % 4) + 1) * rw / 8), (NSUInteger)(ry + (2 * (k / 4) + 1) * rh / 8), 0)
                              sourceSize:MTLSizeMake(1, 1, 1)
                                toBuffer:lb
                       destinationOffset:((NSUInteger)ti * 16u + (NSUInteger)k) * 4u
                  destinationBytesPerRow:4
                destinationBytesPerImage:4];
    [b endEncoding];
    if (!atomic_load(&s_hidden)) pace_count(dr);
    if (linked || atomic_load(&s_hidden)) [cb presentDrawable:dr];
    else [cb presentDrawable:dr afterMinimumDuration:PRESENT_MIN];
    if (s_fxs && ti < PT_CAP) {
        [dr addPresentedHandler:^(id<MTLDrawable> x) {
            pthread_mutex_lock(&s_pt_mu);
            if (s_fxs_n < PT_CAP) s_fxs[s_fxs_n++] = (fxshown_t){ti, x.presentedTime};
            pthread_mutex_unlock(&s_pt_mu);
        }];
    }
    [cb addCompletedHandler:^(id<MTLCommandBuffer> c) {
        if (s_fxg && ti < PT_CAP) {
            s_fxg[ti][0] = c.GPUStartTime;
            s_fxg[ti][1] = c.GPUEndTime;
        }
        if (lb && s_fxl && ti < PT_CAP) {
            const uint8_t *px = (const uint8_t *)lb.contents + (size_t)ti * 64u;
            float sum = 0;
            for (int k = 0; k < 16; k++) sum += 0.114f * px[k * 4] + 0.587f * px[k * 4 + 1] + 0.299f * px[k * 4 + 2];
            s_fxl[ti] = sum / 16.0f;
        }
        (void)gd;
    }];
    [cb commit];
    record(seq, (float)((double)(rt_now_ns() - t) / 1e6));
    pthread_mutex_lock(&s_mu);
    s_ok[seq % CAP] = 1;
    s_last_cb = cb;
    pthread_mutex_unlock(&s_mu);
    s_scaled++;
}

static void *fx_main(void *arg)
{
    (void)arg;
    pthread_setname_np("fx");
    for (;;) {
        pthread_mutex_lock(&s_mu);
        while (!s_pend) pthread_cond_wait(&s_cv, &s_mu);
        id<CAMetalDrawable> gd = s_pend;
        uint64_t fv = s_pend_v;
        uint32_t ti = s_pend_i;
        s_pend = nil;
        s_busy = 1;
        pthread_mutex_unlock(&s_mu);
        @autoreleasepool {
            present(gd, fv, ti);
        }
        gd = nil;
        pthread_mutex_lock(&s_mu);
        s_busy = 0;
        pthread_mutex_unlock(&s_mu);
    }
    return NULL;
}

int rt_fx_attach(void *sdl_window)
{
    if (!rt_fx_probe() || !sdl_window) return -1;
    NSWindow *w = (__bridge NSWindow *)SDL_GetPointerProperty(SDL_GetWindowProperties((SDL_Window *)sdl_window),
                                                              SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, NULL);
    if (!w) return -1;
    s_win = w;
    s_shown = s_pw = s_ph = -1;
    if (s_thr_on) return 0;
    s_q = [s_dev newCommandQueue];
    s_fx_ev = [s_dev newSharedEvent];
    if (!s_q || !s_fx_ev || pthread_create(&s_thr, NULL, fx_main, NULL) != 0) {
        rt_log("fx unavailable: no command queue, event or fx thread");
        return -1;
    }
    s_thr_on = 1;
    rt_log("fx attached: spatial scaler reads the game drawable on the GPU after its copy, window pixel size out");
    return 0;
}

static _Atomic uint64_t s_fit_next;
static atomic_int s_fit_busy;

static NSWindow *cocoa_window(void *sdl_window)
{
    return (__bridge NSWindow *)SDL_GetPointerProperty(SDL_GetWindowProperties((SDL_Window *)sdl_window),
                                                       SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, NULL);
}

int rt_gfx_link_paced(void)
{
    const char *pe = getenv("RT_PACE");
    return !pe || (strcmp(pe, "min") != 0 && atoi(pe) != 0);
}

int rt_gfx_layer_fit(void *sdl_window)
{
    if (!sdl_window) return 0;
    static dispatch_once_t paced;
    dispatch_once(&paced, ^{
        const char *pe = getenv("RT_PACE");
        s_pacing = !pe || strcmp(pe, "min") == 0 || atoi(pe) != 0;
        s_link_on = rt_gfx_link_paced();
        pt_install();
        Method m = s_pacing ? class_getInstanceMethod(objc_getClass("CAMetalDrawable"), @selector(present)) : NULL;
        if (m) {
            s_present_orig = (void (*)(id, SEL))method_getImplementation(m);
            method_setImplementation(m, (IMP)paced_present);
        }
        s_link_on = s_link_on && m;
        if (s_link_on) {
            Method mn = class_getInstanceMethod([CAMetalLayer class], @selector(nextDrawable));
            s_lnext_orig = (id (*)(id, SEL))method_getImplementation(mn);
            method_setImplementation(mn, (IMP)link_next);
            g_install();
        }
        rt_log("gfx pacing: %s, %d drawables",
               s_link_on ? "60 Hz display link, plain present" : s_pacing && m ? "1/60 s minimum present (RT_PACE=min)" : s_pacing ? "no CAMetalDrawable, unpaced" : "off (RT_PACE=0)",
               DRAWABLES);
    });
    uint64_t now = rt_now_ns();
    if (now < atomic_load(&s_fit_next)) return 1;
    atomic_store(&s_fit_next, now + 250000000ull);
    NSWindow *w = cocoa_window(sdl_window);
    if (!w || atomic_exchange(&s_fit_busy, 1)) return 0;
    dispatch_async(dispatch_get_main_queue(), ^{
        NSView *cv = w.contentView, *top = nil;
        int hidden = !(w.occlusionState & NSWindowOcclusionStateVisible);
        if (hidden != atomic_exchange(&s_hidden, hidden)) rt_log("gfx window %s", hidden ? "hidden, unpaced" : "visible, paced");
        static id s_active_obs;
        if (!s_active_obs) {
            __weak NSWindow *ww = w;
            s_active_obs = [NSNotificationCenter.defaultCenter addObserverForName:NSApplicationDidBecomeActiveNotification
                                                                           object:nil
                                                                            queue:NSOperationQueue.mainQueue
                                                                       usingBlock:^(NSNotification *n) {
                                                                           (void)n;
                                                                           NSWindow *g = ww;
                                                                           if (!g || NSApp.keyWindow || !g.isVisible || g.isMiniaturized) return;
                                                                           rt_log("window made key again: the app became active with no key window");
                                                                           [g makeKeyAndOrderFront:nil];
                                                                       }];
        }
        static id s_fs_obs[5];
        if (!s_fs_obs[0]) {
            NSString *names[5] = {NSWindowWillEnterFullScreenNotification, NSWindowDidEnterFullScreenNotification, NSWindowWillExitFullScreenNotification,
                                  NSWindowDidExitFullScreenNotification, NSWindowDidChangeScreenNotification};
            for (int k = 0; k < 5; k++) {
                NSString *nm = names[k];
                s_fs_obs[k] = [NSNotificationCenter.defaultCenter addObserverForName:nm
                                                                              object:w
                                                                               queue:NSOperationQueue.mainQueue
                                                                          usingBlock:^(NSNotification *n) {
                                                                              (void)n;
                                                                              rt_log("gfx window event %s at %.3f", nm.UTF8String,
                                                                                     (double)clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / 1e9);
                                                                          }];
            }
        }
        for (NSView *sv in cv.subviews) {
            if (![sv.layer isKindOfClass:[CAMetalLayer class]]) continue;
            CAMetalLayer *ml = (CAMetalLayer *)sv.layer;
            if (s_pacing && ml.maximumDrawableCount != DRAWABLES) ml.maximumDrawableCount = DRAWABLES;
            if ([sv isKindOfClass:[RTFxView class]]) {
                if (s_link_on && !hidden && fx_link_wanted()) link_start(ml, 1);
            } else {
                top = sv;
                if (s_link_on && !hidden && !fx_link_wanted()) link_start(ml, 0);
                if (![ml.contentsGravity isEqualToString:kCAGravityResizeAspect]) {
                    ml.contentsGravity = kCAGravityResizeAspect;
                    ml.backgroundColor = NSColor.blackColor.CGColor;
                    rt_log("gfx game layer: aspect fit, black bars");
                }
            }
        }
        if (top && s_view && s_view.window == w && [cv.subviews indexOfObject:s_view] < [cv.subviews indexOfObject:top])
            [cv addSubview:s_view positioned:NSWindowAbove relativeTo:top];
        atomic_store(&s_fit_busy, 0);
    });
    return 1;
}

void rt_gfx_layer_counts(void *sdl_window, int *layers, int *unfit)
{
    __block int total = 0, bad = 0;
    NSWindow *w = sdl_window ? cocoa_window(sdl_window) : nil;
    if (w) {
        void (^count)(void) = ^{
            for (NSView *sv in w.contentView.subviews)
                if (![sv isKindOfClass:[RTFxView class]] && [sv.layer isKindOfClass:[CAMetalLayer class]]) {
                    total++;
                    bad += ![sv.layer.contentsGravity isEqualToString:kCAGravityResizeAspect];
                }
        };
        if (NSThread.isMainThread) count();
        else dispatch_sync(dispatch_get_main_queue(), count);
    }
    *layers = total;
    *unfit = bad;
}

int rt_fx_mode_on(void) { return rt_toggle_get(RT_TOG_PICTURE) == RT_PIC_METALFX; }

int rt_fx_frame(int pw, int ph, const int r[4])
{
    if (!s_thr_on) return 0;
    int on = rt_fx_mode_on();
    if (on && !s_sig_orig && sig_install()) {
        static int s_hook_logged;
        if (!s_hook_logged++) rt_log("fx unavailable: no Metal 4 signalDrawable to hook");
        on = 0;
    }
    atomic_store(&s_rx, r[0]);
    atomic_store(&s_ry, r[1]);
    atomic_store(&s_rw, r[2]);
    atomic_store(&s_rh, r[3]);
    if (on != s_shown || (on && (pw != s_pw || ph != s_ph))) {
        s_shown = on;
        s_pw = pw;
        s_ph = ph;
        if (!on) {
            atomic_store(&s_live, 0);
            link_relayer(0);
        }
        NSWindow *w = s_win;
        dispatch_async(dispatch_get_main_queue(), ^{
            layout_main(w, on, pw, ph);
        });
    }
    return on && atomic_load(&s_live);
}

int rt_fx_wanted(void)
{
    return s_thr_on && s_sup_spatial && rt_fx_mode_on() && atomic_load(&s_live);
}

void rt_fx_drain(void)
{
    if (!s_thr_on) return;
    for (;;) {
        pthread_mutex_lock(&s_mu);
        int busy = s_pend != nil || s_busy;
        id<MTLCommandBuffer> cb = s_last_cb;
        pthread_mutex_unlock(&s_mu);
        if (!busy) {
            [cb waitUntilCompleted];
            return;
        }
        usleep(50);
    }
}

int rt_fx_upscale_sync(const void *bgra, uint32_t w, uint32_t h, uint32_t pitch, uint32_t ow, uint32_t oh, void *out,
                       double *gpu_ms)
{
    probe();
    if (!s_sup_spatial) return -1;
    if (!s_q) s_q = [s_dev newCommandQueue];
    id<MTLFXSpatialScaler> sc = make_scaler(w, h, ow, oh);
    if (!sc || !s_q) return -2;
    id<MTLTexture> in = make_tex(w, h, sc.colorTextureUsage | MTLTextureUsageShaderRead);
    id<MTLTexture> ot = make_tex(ow, oh, sc.outputTextureUsage | MTLTextureUsageShaderRead);
    id<MTLBuffer> up = [s_dev newBufferWithLength:(NSUInteger)w * h * 4 options:MTLResourceStorageModeShared];
    id<MTLBuffer> rb = [s_dev newBufferWithLength:(NSUInteger)ow * oh * 4 options:MTLResourceStorageModeShared];
    if (!in || !ot || !up || !rb) return -3;
    for (uint32_t y = 0; y < h; y++)
        memcpy((char *)up.contents + (size_t)y * w * 4, (const char *)bgra + (size_t)y * pitch, (size_t)w * 4);
    sc.colorTexture = in;
    sc.outputTexture = ot;
    sc.inputContentWidth = w;
    sc.inputContentHeight = h;
    id<MTLCommandBuffer> cb = [s_q commandBuffer];
    id<MTLBlitCommandEncoder> b = [cb blitCommandEncoder];
    [b copyFromBuffer:up
               sourceOffset:0
          sourceBytesPerRow:(NSUInteger)w * 4
        sourceBytesPerImage:(NSUInteger)w * h * 4
                 sourceSize:MTLSizeMake(w, h, 1)
                  toTexture:in
           destinationSlice:0
           destinationLevel:0
          destinationOrigin:MTLOriginMake(0, 0, 0)];
    [b endEncoding];
    [sc encodeToCommandBuffer:cb];
    b = [cb blitCommandEncoder];
    [b copyFromTexture:ot
                     sourceSlice:0
                     sourceLevel:0
                    sourceOrigin:MTLOriginMake(0, 0, 0)
                      sourceSize:MTLSizeMake(ow, oh, 1)
                        toBuffer:rb
               destinationOffset:0
          destinationBytesPerRow:(NSUInteger)ow * 4
        destinationBytesPerImage:(NSUInteger)ow * oh * 4];
    [b endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    if (cb.status != MTLCommandBufferStatusCompleted) return -4;
    memcpy(out, rb.contents, (size_t)ow * oh * 4);
    if (gpu_ms) *gpu_ms = (cb.GPUEndTime - cb.GPUStartTime) * 1000.0;
    return 0;
}

static int cmp_f(const void *a, const void *b)
{
    float x = *(const float *)a, y = *(const float *)b;
    return (x > y) - (x < y);
}

void rt_fx_report(void)
{
    if (!s_probed) return;
    static float tmp[CAP];
    uint64_t n = 0, first = s_seq > CAP ? s_seq - CAP + 1 : 1;
    pthread_mutex_lock(&s_mu);
    for (uint64_t q = first; q <= s_seq; q++)
        if (s_ok[q % CAP]) tmp[n++] = s_cost[q % CAP];
    pthread_mutex_unlock(&s_mu);
    double p50 = 0, p99 = 0;
    if (n) {
        qsort(tmp, n, sizeof *tmp, cmp_f);
        p50 = tmp[(n - 1) * 50 / 100];
        p99 = tmp[(n - 1) * 99 / 100];
    }
    rt_log("fx frames %llu scaled %llu smoothed 0 interpolated 0 passthrough %llu added p50 %.3f p99 %.3f",
           (unsigned long long)s_frames, (unsigned long long)s_scaled, (unsigned long long)(s_skipped + s_overwritten), p50,
           p99);
    rt_log("fx pipelines %u built in %.2f ms", s_builds, s_build_ms);
}
