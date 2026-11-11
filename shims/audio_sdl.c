#include "recomp_types.h"
#include "rt.h"

#include <SDL3/SDL.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <math.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define WAVE_MAPPER 0xFFFFFFFFu
#define WHDR_DONE 1u
#define RATE 44100
#define BYTES_PER_SEC 176400u
#define MAXH 64
#define MAXPREP 128
#define HANDLE 0x57A0u
#define HDR_SIZE 32u
#define HDR_FLAGS 16u
#define STEADY_NS 5000000000ull
#define MAXWAV 8

struct rt_wav {
    int fd, used;
    uint32_t bytes;
    char path[PATH_MAX];
};

typedef struct {
    uint32_t *flags;
    uint64_t end;
} record;

static int s_nosound, s_capture;
static int s_up, s_silent, s_watch, s_opened_ever, s_open;
static SDL_AudioStream *s_stream;
static uint32_t s_prep[MAXPREP], s_nprep;
static record s_q[MAXH];
static uint32_t s_qh, s_qn;
static uint64_t s_put_total, s_played, s_written, s_max_queued;
static uint32_t s_headers, s_under, s_steady, s_resets, s_devchg, s_dev_events, s_dev_seen, s_active;
static rt_wav s_wavs[MAXWAV];
static pthread_mutex_t s_wav_mu = PTHREAD_MUTEX_INITIALIZER;
static rt_wav *s_wavout;
static char s_wavout_path[PATH_MAX];
static char s_stamp[32];

void rt_audio_config(int nosound, int capture)
{
    s_nosound = nosound;
    s_capture = capture;
}

int rt_audio_nosound(void) { return s_nosound; }

static void wav_patch(rt_wav *w)
{
    uint32_t riff = 36u + w->bytes, data = w->bytes;
    pwrite(w->fd, &riff, 4, 4);
    pwrite(w->fd, &data, 4, 40);
}

rt_wav *rt_wav_open(const char *name, int rate, int ch)
{
    if (!s_capture) return NULL;
    pthread_mutex_lock(&s_wav_mu);
    rt_wav *w = NULL;
    for (int i = 0; i < MAXWAV && !w; i++)
        if (!s_wavs[i].used) w = &s_wavs[i];
    if (w) {
        if (!s_stamp[0]) {
            time_t t = time(NULL);
            struct tm tm;
            gmtime_r(&t, &tm);
            strftime(s_stamp, sizeof s_stamp, "%Y%m%dT%H%M%SZ", &tm);
        }
        w->fd = -1;
        for (int n = 1; n < 100 && w->fd < 0; n++) {
            if (n == 1) snprintf(w->path, sizeof w->path, "%s/audio/%s-%s.wav", rt_vfs_overlay(), name, s_stamp);
            else snprintf(w->path, sizeof w->path, "%s/audio/%s-%s-%d.wav", rt_vfs_overlay(), name, s_stamp, n);
            if (n == 1) rt_vfs_mkparents(w->path);
            w->fd = open(w->path, O_WRONLY | O_CREAT | O_EXCL, 0644);
        }
        if (w->fd >= 0) {
            uint8_t h[44] = {'R', 'I', 'F', 'F', 0, 0, 0, 0, 'W', 'A', 'V', 'E', 'f', 'm', 't', ' ', 16, 0, 0, 0, 1, 0,
                             (uint8_t)ch, 0, 0, 0, 0, 0, 0, 0, 0, 0, (uint8_t)(ch * 2), 0, 16, 0, 'd', 'a', 't', 'a'};
            uint32_t br = (uint32_t)(rate * ch * 2), r = (uint32_t)rate;
            memcpy(h + 24, &r, 4);
            memcpy(h + 28, &br, 4);
            write(w->fd, h, sizeof h);
            w->used = 1;
            w->bytes = 0;
        } else w = NULL;
    }
    pthread_mutex_unlock(&s_wav_mu);
    return w;
}

void rt_wav_write(rt_wav *w, const void *p, uint32_t n)
{
    if (!w) return;
    pthread_mutex_lock(&s_wav_mu);
    if (w->used && write(w->fd, p, n) == (ssize_t)n) w->bytes += n;
    pthread_mutex_unlock(&s_wav_mu);
}

void rt_wav_close(rt_wav *w)
{
    if (!w) return;
    pthread_mutex_lock(&s_wav_mu);
    if (w->used) {
        wav_patch(w);
        close(w->fd);
        w->used = 0;
    }
    pthread_mutex_unlock(&s_wav_mu);
}

static const char *fmt_name(SDL_AudioFormat f)
{
    if (f == SDL_AUDIO_S16) return "s16";
    if (f == SDL_AUDIO_S32) return "s32";
    if (f == SDL_AUDIO_F32) return "f32";
    if (f == SDL_AUDIO_U8) return "u8";
    if (f == SDL_AUDIO_S8) return "s8";
    return SDL_GetAudioFormatName(f);
}

static void log_device(const char *what)
{
    SDL_AudioDeviceID d = SDL_GetAudioStreamDevice(s_stream);
    SDL_AudioSpec sp = {0};
    const char *n = d ? SDL_GetAudioDeviceName(d) : NULL;
    if (d) SDL_GetAudioDeviceFormat(d, &sp, NULL);
    if (!strcmp(what, "open"))
        rt_log("audio open device \"%s\" app %d/2/s16 device %d/%d/%s", n ? n : "?", RATE, sp.freq, sp.channels,
               fmt_name(sp.format));
    else rt_log("audio device changed \"%s\" %d/%d/%s", n ? n : "?", sp.freq, sp.channels, fmt_name(sp.format));
}

static bool SDLCALL on_event(void *u, SDL_Event *e)
{
    (void)u;
    if ((e->type == SDL_EVENT_AUDIO_DEVICE_ADDED || e->type == SDL_EVENT_AUDIO_DEVICE_REMOVED ||
         e->type == SDL_EVENT_AUDIO_DEVICE_FORMAT_CHANGED) && !e->adevice.recording)
        __atomic_fetch_add(&s_dev_events, 1, __ATOMIC_RELAXED);
    return true;
}

static uint64_t s_sil, s_sil_ns, s_sil_frac, s_base;

static void SDLCALL on_get(void *u, SDL_AudioStream *s, int add, int total)
{
    (void)u;
    (void)total;
    int q = SDL_GetAudioStreamQueued(s);
    if (q < 0) q = 0;
    uint64_t consumed = s_put_total > (uint64_t)q ? s_put_total - (uint64_t)q : 0;
    if (s_silent) {
        uint64_t now = rt_now_ns();
        if (s_sil_ns) {
            s_sil_frac += (now - s_sil_ns) * BYTES_PER_SEC;
            s_sil += s_sil_frac / 1000000000ull;
            s_sil_frac %= 1000000000ull;
        }
        s_sil_ns = now;
        if (s_sil > s_put_total) s_sil = s_put_total;
        consumed = s_sil;
        q = consumed < s_put_total ? 1 : 0;
        SDL_ClearAudioStream(s);
    }
    if (s_base + consumed > __atomic_load_n(&s_played, __ATOMIC_RELAXED))
        __atomic_store_n(&s_played, s_base + consumed, __ATOMIC_RELEASE);
    while (s_qn && s_q[s_qh].end <= consumed) {
        __atomic_fetch_or(s_q[s_qh].flags, WHDR_DONE, __ATOMIC_RELEASE);
        s_qh = (s_qh + 1) % MAXH;
        s_qn--;
        __atomic_fetch_add(&s_headers, 1, __ATOMIC_RELAXED);
    }
    if (add > 0 && q == 0 && __atomic_load_n(&s_active, __ATOMIC_RELAXED)) {
        __atomic_fetch_add(&s_under, 1, __ATOMIC_RELAXED);
        uint64_t t0 = rt_frames_start_ns();
        if (t0 && rt_now_ns() >= t0 + STEADY_NS) __atomic_fetch_add(&s_steady, 1, __ATOMIC_RELAXED);
    }
}

static void watch_once(void)
{
    if (!s_watch) s_watch = SDL_AddEventWatch(on_event, NULL);
}

static int go_silent(const char *why)
{
    rt_log("audio silent clock (%s)", why);
    if (s_up) {
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        s_up = 0;
    }
    if (s_silent) return -1;
    SDL_SetHintWithPriority(SDL_HINT_AUDIO_DRIVER, "dummy", SDL_HINT_OVERRIDE);
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) return -1;
    s_up = 1;
    s_silent = 1;
    watch_once();
    return 0;
}

int rt_audio_init(void)
{
    if (s_nosound) return -1;
    if (s_up) return 0;
    if (!SDL_InitSubSystem(SDL_INIT_AUDIO)) return go_silent(SDL_GetError());
    s_up = 1;
    watch_once();
    return 0;
}

static int stream_open(void)
{
    SDL_AudioSpec sp = {SDL_AUDIO_S16LE, 2, RATE};
    s_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &sp, NULL, NULL);
    if (!s_stream) return 0;
    SDL_SetAudioStreamGetCallback(s_stream, on_get, NULL);
    SDL_LockAudioStream(s_stream);
    s_qh = s_qn = 0;
    s_put_total = s_sil = s_sil_ns = s_sil_frac = 0;
    SDL_UnlockAudioStream(s_stream);
    if (SDL_ResumeAudioStreamDevice(s_stream)) return 1;
    SDL_DestroyAudioStream(s_stream);
    s_stream = NULL;
    return 0;
}

static int valid(uint32_t h) { return s_open && s_stream && h == HANDLE; }

static void drain_queue(void)
{
    while (s_qn) {
        __atomic_fetch_or(s_q[s_qh].flags, WHDR_DONE, __ATOMIC_RELEASE);
        s_qh = (s_qh + 1) % MAXH;
        s_qn--;
    }
}

static uint32_t refuse(const char *why)
{
    rt_log("audio waveOutOpen refused: %s", why);
    return 1;
}

static uint32_t waveOutOpen(const uint32_t *a)
{
    if (s_open) return refuse("already open");
    if (a[1] != WAVE_MAPPER) return refuse("device is not WAVE_MAPPER");
    if (a[3] || a[5]) return refuse("callback type is not CALLBACK_NULL");
    if (!a[0] || !rt_span_ok(a[0], 4) || !a[2] || !rt_span_ok(a[2], 16)) return refuse("bad pointer");
    if (MEM16(a[2]) != 1 || MEM16(a[2] + 2) != 2 || MEM32(a[2] + 4) != RATE || MEM16(a[2] + 12) != 4 ||
        MEM16(a[2] + 14) != 16)
        return refuse("format is not PCM 44100 Hz 2 channels 16 bit");
    if (rt_audio_init() != 0) return refuse("audio is off");
    if (!stream_open()) {
        if (go_silent(SDL_GetError()) != 0 || !stream_open()) return refuse("no stream even on the dummy driver");
    }
    s_open = 1;
    s_opened_ever = 1;
    s_nprep = 0;
    __atomic_store_n(&s_active, 0, __ATOMIC_RELAXED);
    s_dev_seen = __atomic_load_n(&s_dev_events, __ATOMIC_RELAXED);
    s_wavout = rt_wav_open("waveout", RATE, 2);
    snprintf(s_wavout_path, sizeof s_wavout_path, "%s", s_wavout ? s_wavout->path : "");
    log_device("open");
    MEM32(a[0]) = HANDLE;
    return 0;
}

static int prepared(uint32_t hdr)
{
    for (uint32_t i = 0; i < s_nprep; i++)
        if (s_prep[i] == hdr) return 1;
    return 0;
}

static int queued(const uint32_t *flags)
{
    for (uint32_t i = 0; i < s_qn; i++)
        if (s_q[(s_qh + i) % MAXH].flags == flags) return 1;
    return 0;
}

static uint32_t waveOutPrepareHeader(const uint32_t *a)
{
    if (!valid(a[0]) || a[2] != HDR_SIZE || !a[1] || !rt_span_ok(a[1], HDR_SIZE)) return 1;
    if (prepared(a[1])) return 0;
    if (s_nprep == MAXPREP) return 1;
    s_prep[s_nprep++] = a[1];
    return 0;
}

static uint32_t waveOutUnprepareHeader(const uint32_t *a)
{
    if (!valid(a[0]) || a[2] != HDR_SIZE || !a[1] || !rt_span_ok(a[1], HDR_SIZE)) return 1;
    uint32_t *fl = rt_host(a[1] + HDR_FLAGS);
    SDL_LockAudioStream(s_stream);
    int q = queued(fl);
    SDL_UnlockAudioStream(s_stream);
    if (q) return 1;
    for (uint32_t i = 0; i < s_nprep; i++)
        if (s_prep[i] == a[1]) {
            s_prep[i] = s_prep[--s_nprep];
            break;
        }
    return 0;
}

static uint32_t waveOutWrite(const uint32_t *a)
{
    if (!valid(a[0]) || a[2] != HDR_SIZE || !a[1] || !rt_span_ok(a[1], HDR_SIZE) || !prepared(a[1])) return 1;
    uint32_t data = MEM32(a[1]), len = MEM32(a[1] + 4);
    uint32_t *fl = rt_host(a[1] + HDR_FLAGS);
    if (len && !rt_span_ok(data, len)) return 1;
    SDL_LockAudioStream(s_stream);
    int dup = queued(fl) || s_qn >= MAXH;
    if (!dup) __atomic_fetch_and(fl, ~WHDR_DONE, __ATOMIC_RELEASE);
    SDL_UnlockAudioStream(s_stream);
    if (dup) return 1;
    if (s_wavout) rt_wav_write(s_wavout, rt_host(data), len);
    if (len && !SDL_PutAudioStreamData(s_stream, rt_host(data), (int)len)) return 1;
    int q = SDL_GetAudioStreamQueued(s_stream);
    SDL_LockAudioStream(s_stream);
    if (s_qn < MAXH) {
        s_q[(s_qh + s_qn) % MAXH] = (record){fl, s_put_total + len};
        s_qn++;
    } else {
        __atomic_fetch_or(fl, WHDR_DONE, __ATOMIC_RELEASE);
        static int once;
        if (!once++) rt_log("audio queue overflow");
    }
    s_put_total += len;
    SDL_UnlockAudioStream(s_stream);
    s_written += len;
    __atomic_store_n(&s_active, 1, __ATOMIC_RELAXED);
    if (q > 0 && (uint64_t)q > s_max_queued) s_max_queued = (uint64_t)q;
    uint32_t ev = __atomic_load_n(&s_dev_events, __ATOMIC_RELAXED);
    if (ev != s_dev_seen) {
        s_dev_seen = ev;
        s_devchg++;
        log_device("changed");
    }
    return 0;
}

static uint32_t waveOutReset(const uint32_t *a)
{
    if (!valid(a[0])) return 1;
    SDL_LockAudioStream(s_stream);
    SDL_ClearAudioStream(s_stream);
    drain_queue();
    s_put_total = s_sil = __atomic_load_n(&s_played, __ATOMIC_RELAXED) - s_base;
    __atomic_store_n(&s_active, 0, __ATOMIC_RELAXED);
    SDL_UnlockAudioStream(s_stream);
    s_resets++;
    return 0;
}

static uint32_t waveOutClose(const uint32_t *a)
{
    if (!valid(a[0])) return 1;
    SDL_LockAudioStream(s_stream);
    drain_queue();
    s_base = __atomic_load_n(&s_played, __ATOMIC_RELAXED);
    s_put_total = s_sil = s_sil_ns = s_sil_frac = 0;
    __atomic_store_n(&s_active, 0, __ATOMIC_RELAXED);
    SDL_UnlockAudioStream(s_stream);
    SDL_DestroyAudioStream(s_stream);
    s_stream = NULL;
    s_open = 0;
    s_nprep = 0;
    return 0;
}

#define MIXER(n) \
    static uint32_t n(const uint32_t *a) \
    { \
        static int once; \
        (void)a; \
        if (!__atomic_exchange_n(&once, 1, __ATOMIC_RELAXED)) rt_log("audio mixer %s refused", #n); \
        return 1; \
    }

MIXER(mixerClose)
MIXER(mixerGetControlDetailsA)
MIXER(mixerGetDevCapsA)
MIXER(mixerGetLineControlsA)
MIXER(mixerGetLineInfoA)
MIXER(mixerOpen)
MIXER(mixerSetControlDetails)

const char *rt_wav_path(const rt_wav *w) { return w ? w->path : ""; }

int rt_audio_go_silent(const char *why) { return go_silent(why); }

void rt_audio_report(void)
{
    pthread_mutex_lock(&s_wav_mu);
    for (int i = 0; i < MAXWAV; i++)
        if (s_wavs[i].used) {
            wav_patch(&s_wavs[i]);
            close(s_wavs[i].fd);
            s_wavs[i].used = 0;
        }
    pthread_mutex_unlock(&s_wav_mu);
    rt_dsound_report();
    if (!s_opened_ever) return;
    rt_log("audio written %llu played %llu headers %u underruns %u steady %u max queued %llu ms resets %u device changes %u",
           (unsigned long long)s_written, (unsigned long long)__atomic_load_n(&s_played, __ATOMIC_ACQUIRE),
           __atomic_load_n(&s_headers, __ATOMIC_RELAXED), __atomic_load_n(&s_under, __ATOMIC_RELAXED),
           __atomic_load_n(&s_steady, __ATOMIC_RELAXED), (unsigned long long)(s_max_queued * 10u / 1764u), s_resets,
           s_devchg);
}

const rt_shim rt_audio_shims[] = {
    {"winmm.dll", "waveOutClose", 1, waveOutClose},
    {"winmm.dll", "waveOutOpen", 6, waveOutOpen},
    {"winmm.dll", "waveOutPrepareHeader", 3, waveOutPrepareHeader},
    {"winmm.dll", "waveOutReset", 1, waveOutReset},
    {"winmm.dll", "waveOutUnprepareHeader", 3, waveOutUnprepareHeader},
    {"winmm.dll", "waveOutWrite", 3, waveOutWrite},
    {"winmm.dll", "mixerClose", 1, mixerClose},
    {"winmm.dll", "mixerGetControlDetailsA", 3, mixerGetControlDetailsA},
    {"winmm.dll", "mixerGetDevCapsA", 3, mixerGetDevCapsA},
    {"winmm.dll", "mixerGetLineControlsA", 3, mixerGetLineControlsA},
    {"winmm.dll", "mixerGetLineInfoA", 3, mixerGetLineInfoA},
    {"winmm.dll", "mixerOpen", 5, mixerOpen},
    {"winmm.dll", "mixerSetControlDetails", 3, mixerSetControlDetails},
};

const uint32_t rt_naudio_shims = sizeof rt_audio_shims / sizeof rt_audio_shims[0];

static int s_pass, s_fail;

static void result(int ok, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("%s ", ok ? "PASS" : "FAIL");
    vprintf(fmt, ap);
    printf("\n");
    fflush(stdout);
    va_end(ap);
    if (ok) s_pass++;
    else s_fail++;
}

static uint32_t wo(const char *name, int n, ...)
{
    uint32_t a[8] = {0};
    va_list ap;
    va_start(ap, n);
    for (int i = 0; i < n && i < 8; i++) a[i] = va_arg(ap, uint32_t);
    va_end(ap);
    const rt_shim *s = rt_find_shim("winmm.dll", name);
    if (!s) {
        result(0, "%s is a shim", name);
        return 0xFFFFu;
    }
    return s->fn(a);
}

static uint32_t gal(uint32_t n) { return rt_heap_alloc(n, 1); }

static uint32_t s_ring, s_hdr[MAXH + 1], s_fmt, s_ph;
static uint64_t s_tone_off;

static void setup_guest(void)
{
    s_ring = gal(0x10000);
    int16_t *s = rt_host(s_ring);
    for (uint32_t i = 0; i < 16384; i++) s[2 * i] = s[2 * i + 1] = (int16_t)(12000.0 * sin(6.283185307179586 * 1000.0 * i / RATE));
    for (uint32_t i = 0; i <= MAXH; i++) {
        s_hdr[i] = gal(HDR_SIZE);
        MEM32(s_hdr[i]) = s_ring + (i % MAXH) * 0x400u;
        MEM32(s_hdr[i] + 4) = 0x400;
    }
    s_fmt = gal(20);
    s_ph = gal(4);
}

static void set_fmt(uint32_t tag, uint32_t ch, uint32_t rate, uint32_t bits)
{
    MEM16(s_fmt) = (uint16_t)tag;
    MEM16(s_fmt + 2) = (uint16_t)ch;
    MEM32(s_fmt + 4) = rate;
    MEM32(s_fmt + 8) = rate * ch * bits / 8;
    MEM16(s_fmt + 12) = (uint16_t)(ch * bits / 8);
    MEM16(s_fmt + 14) = (uint16_t)bits;
    MEM16(s_fmt + 16) = 0;
}

static int done(uint32_t i) { return (MEM32(s_hdr[i] + HDR_FLAGS) & WHDR_DONE) != 0; }

typedef struct {
    int finished, ordered, taken, write_errors;
    double sec;
} run_t;

static run_t play(int count, int check_taken)
{
    run_t r = {0, 1, 1, 0, 0};
    uint64_t base = s_put_total, t0 = rt_now_ns();
    int w = 0, d = 0;
    while (d < count && rt_now_ns() - t0 < 20000000000ull) {
        while (w < count && w - d < 16) {
            if (wo("waveOutWrite", 3, HANDLE, s_hdr[w % MAXH], HDR_SIZE)) r.write_errors++;
            w++;
        }
        for (int k = d + 1; k < w; k++)
            if (done(k % MAXH) && !done(d % MAXH)) r.ordered = 0;
        if (done(d % MAXH)) {
            if (check_taken) {
                int q = SDL_GetAudioStreamQueued(s_stream);
                if ((int64_t)(s_put_total - (uint64_t)q) < (int64_t)(base + (uint64_t)(d + 1) * 1024u)) r.taken = 0;
            }
            d++;
        } else usleep(1000);
    }
    r.finished = d == count;
    r.sec = (double)(rt_now_ns() - t0) / 1e9;
    return r;
}

static uint32_t open_default(void)
{
    set_fmt(1, 2, RATE, 16);
    return wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0u, 0u, 0u);
}

static void refusal_cases(void)
{
    set_fmt(1, 2, 22050, 16);
    uint32_t r = wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0u, 0u, 0u);
    result(r == 1, "a 22050 Hz format is refused with 1, never 4 (returned %u)", r);
    set_fmt(1, 1, RATE, 16);
    r = wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0u, 0u, 0u);
    result(r == 1, "a mono format is refused with 1 (returned %u)", r);
    set_fmt(1, 2, RATE, 8);
    r = wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0u, 0u, 0u);
    result(r == 1, "an 8-bit format is refused with 1 (returned %u)", r);
    set_fmt(1, 2, RATE, 16);
    r = wo("waveOutOpen", 6, s_ph, 0u, s_fmt, 0u, 0u, 0u);
    result(r == 1, "a device other than WAVE_MAPPER is refused with 1 (returned %u)", r);
    uint32_t r1 = wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0x00401000u, 0u, 0x30000u);
    uint32_t r2 = wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0x1234u, 0u, 0x10000u);
    uint32_t r3 = wo("waveOutOpen", 6, s_ph, WAVE_MAPPER, s_fmt, 0u, 0u, 0x20000u);
    result(r1 == 1 && r2 == 1 && r3 == 1, "CALLBACK_FUNCTION, CALLBACK_WINDOW and CALLBACK_THREAD are refused with 1 (returned %u %u %u)",
           r1, r2, r3);
    result(!s_open && !s_stream, "refused opens leave no stream");
}

static void stream_cases(void)
{
    uint32_t r = open_default();
    result(r == 0 && MEM32(s_ph) == HANDLE && s_stream && s_open, "waveOutOpen WAVE_MAPPER 44100/2/16 CALLBACK_NULL opens a stream (returned %u)", r);
    r = open_default();
    result(r == 1, "a second open is refused with 1 (returned %u)", r);
    int bad = 0;
    for (uint32_t i = 0; i < MAXH; i++) bad += (int)wo("waveOutPrepareHeader", 3, HANDLE, s_hdr[i], HDR_SIZE);
    result(bad == 0, "64 headers prepare with 0");
    r = wo("waveOutWrite", 3, HANDLE, s_hdr[MAXH], HDR_SIZE);
    result(r == 1, "Write of an unprepared header returns 1 (returned %u)", r);
    r = wo("waveOutPrepareHeader", 3, HANDLE, s_hdr[0], 16u);
    result(r == 1, "PrepareHeader with a wrong size returns 1 (returned %u)", r);

    SDL_PauseAudioStreamDevice(s_stream);
    int early = 0;
    for (int i = 0; i < 8; i++) {
        wo("waveOutWrite", 3, HANDLE, s_hdr[i], HDR_SIZE);
        early += done((uint32_t)i);
    }
    usleep(150000);
    for (int i = 0; i < 8; i++) early += done((uint32_t)i);
    result(early == 0, "no header is done before SDL has taken its bytes (device paused, %d of 8 done)", early);
    r = wo("waveOutUnprepareHeader", 3, HANDLE, s_hdr[0], HDR_SIZE);
    result(r == 1, "Unprepare of a queued header returns 1 (returned %u)", r);
    r = wo("waveOutReset", 1, HANDLE);
    int all = 1;
    for (int i = 0; i < 8; i++) all &= done((uint32_t)i);
    int sq = SDL_GetAudioStreamQueued(s_stream);
    result(r == 0 && all && sq == 0, "Reset marks every queued header done and empties the stream (returned %u, stream holds %d bytes)", r, sq);
    r = wo("waveOutUnprepareHeader", 3, HANDLE, s_hdr[0], HDR_SIZE);
    result(r == 0, "Unprepare after Reset returns 0 (returned %u)", r);
    wo("waveOutPrepareHeader", 3, HANDLE, s_hdr[0], HDR_SIZE);
    SDL_ResumeAudioStreamDevice(s_stream);

    uint64_t w0 = s_written, p0 = __atomic_load_n(&s_played, __ATOMIC_ACQUIRE);
    s_tone_off = w0;
    run_t t = play(MAXH, 1);
    result(t.finished && t.write_errors == 0, "64 headers of a 1 kHz tone come back done (%.3f s)", t.sec);
    result(t.ordered, "the 64 headers come back done in order");
    result(t.taken, "every header done had its bytes taken by SDL (written minus stream queue at that moment)");
    uint64_t w1 = s_written - w0, p1 = __atomic_load_n(&s_played, __ATOMIC_ACQUIRE) - p0;
    result(w1 == 65536 && p1 == 65536, "written and played match the bytes sent (written %llu played %llu of 65536)",
           (unsigned long long)w1, (unsigned long long)p1);

    SDL_PauseAudioStreamDevice(s_stream);
    for (int i = 0; i < 4; i++) wo("waveOutWrite", 3, HANDLE, s_hdr[i], HDR_SIZE);
    wo("waveOutReset", 1, HANDLE);
    SDL_ResumeAudioStreamDevice(s_stream);
    t = play(8, 0);
    result(t.finished && t.ordered, "after Reset the stream plays again and new headers complete in order");

    r = wo("waveOutClose", 1, HANDLE);
    uint32_t r2 = wo("waveOutWrite", 3, HANDLE, s_hdr[0], HDR_SIZE);
    result(r == 0 && r2 == 1 && !s_stream, "Close returns 0 and a Write after it returns 1 (returned %u %u)", r, r2);
}

static int rd(const char *path, uint8_t **buf, long *n)
{
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    *n = ftell(f);
    fseek(f, 0, SEEK_SET);
    *buf = *n < 0 ? NULL : malloc((size_t)*n + 1);
    int ok = *buf && fread(*buf, 1, (size_t)*n, f) == (size_t)*n;
    if (!ok) {
        free(*buf);
        *buf = NULL;
    }
    fclose(f);
    return ok;
}

static void wav_cases(void)
{
    rt_audio_report();
    uint8_t *b = NULL;
    long n = 0;
    int ok = s_wavout_path[0] && rd(s_wavout_path, &b, &n) && n >= 44;
    uint32_t riff = 0, data = 0;
    if (ok) {
        memcpy(&riff, b + 4, 4);
        memcpy(&data, b + 40, 4);
    }
    result(ok && data == s_written && riff == 36u + data && n == 44 + (long)data && !memcmp(b, "RIFF", 4) &&
               !memcmp(b + 8, "WAVEfmt ", 8) && !memcmp(b + 36, "data", 4),
           "the waveout WAV holds every written byte and its RIFF and data sizes are patched (file %ld, data %u, written %llu)",
           n, data, (unsigned long long)s_written);
    result(ok && n >= 44 + (long)s_tone_off + 65536 && !memcmp(b + 44 + s_tone_off, rt_host(s_ring), 65536),
           "the waveout WAV holds the 64 KB tone the engine passed to Write, in order");
    free(b);
    const char *tmp = getenv("TMPDIR");
    uint8_t *db = (uint8_t *)"";
    long dn = 0;
    int dr = rd(tmp && *tmp ? tmp : "/tmp", &db, &dn);
    result(!dr && !db, "reading a folder as a log fails and keeps no buffer (ret %d, length %ld)", dr, dn);
    rt_wav *w = rt_wav_open("selftest", 22050, 1);
    uint8_t blk[1000] = {0};
    char path[PATH_MAX] = "";
    if (w) {
        snprintf(path, sizeof path, "%s", w->path);
        rt_wav_write(w, blk, sizeof blk);
        rt_wav_write(w, blk, 24);
        rt_wav_close(w);
    }
    b = NULL;
    ok = w && rd(path, &b, &n) && n == 44 + 1024;
    uint32_t rate = 0, size = 0;
    if (ok) {
        memcpy(&rate, b + 24, 4);
        memcpy(&riff, b + 4, 4);
        memcpy(&size, b + 40, 4);
    }
    result(ok && rate == 22050 && size == 1024 && riff == 36u + 1024u && b[22] == 1 && b[34] == 16,
           "rt_wav_close patches the RIFF and data sizes (rate %u, data %u, riff %u)", rate, size, riff);
    free(b);
}

static void silent_case(void)
{
    SDL_SetHintWithPriority(SDL_HINT_AUDIO_DRIVER, "no_such_driver", SDL_HINT_OVERRIDE);
    if (s_up) {
        SDL_QuitSubSystem(SDL_INIT_AUDIO);
        s_up = 0;
    }
    uint32_t r = open_default();
    int ok = r == 0 && s_silent && s_stream;
    result(ok, "SDL_AUDIO_DRIVER naming no driver falls back to the silent clock and waveOutOpen returns 0 (returned %u, silent %d)", r, s_silent);
    if (!ok) {
        wo("waveOutClose", 1, HANDLE);
        return;
    }
    for (uint32_t i = 0; i < MAXH; i++) wo("waveOutPrepareHeader", 3, HANDLE, s_hdr[i], HDR_SIZE);
    run_t t = play(172, 0);
    double want = 172.0 * 1024.0 / BYTES_PER_SEC;
    result(t.finished && t.ordered && t.sec > want * 0.95 && t.sec < want * 1.05,
           "the silent clock completes 1 s of headers within 5%% (%.3f s for %.3f s of audio)", t.sec, want);
    wo("waveOutReset", 1, HANDLE);
    wo("waveOutClose", 1, HANDLE);
}

static void reopen_case(void)
{
    uint64_t w0 = s_written, p0 = __atomic_load_n(&s_played, __ATOMIC_ACQUIRE);
    int taken = 1, finished = 1, closed = 1;
    for (int k = 0; k < 2; k++) {
        closed &= open_default() == 0;
        for (uint32_t i = 0; i < MAXH; i++) wo("waveOutPrepareHeader", 3, HANDLE, s_hdr[i], HDR_SIZE);
        run_t t = play(16, 1);
        taken &= t.taken;
        finished &= t.finished && t.ordered;
        closed &= wo("waveOutClose", 1, HANDLE) == 0;
    }
    uint64_t dw = s_written - w0, dp = __atomic_load_n(&s_played, __ATOMIC_ACQUIRE) - p0;
    result(closed && finished && taken && dw == 32768 && dp == dw,
           "open, write, close, open, write: no header done before its bytes are taken, and played equals written "
           "(written %llu played %llu of 32768)", (unsigned long long)dw, (unsigned long long)dp);
}

static void reset_between_case(void)
{
    int ok = open_default() == 0;
    for (uint32_t i = 0; i < MAXH; i++) wo("waveOutPrepareHeader", 3, HANDLE, s_hdr[i], HDR_SIZE);
    SDL_PauseAudioStreamDevice(s_stream);
    for (int i = 0; i < 3; i++) wo("waveOutWrite", 3, HANDLE, s_hdr[i], HDR_SIZE);
    wo("waveOutReset", 1, HANDLE);
    int first = done(0) && done(1) && done(2) && s_qn == 0;
    for (int i = 3; i < 5; i++) wo("waveOutWrite", 3, HANDLE, s_hdr[i], HDR_SIZE);
    int second = !done(3) && !done(4) && s_qn == 2;
    SDL_ResumeAudioStreamDevice(s_stream);
    uint64_t t0 = rt_now_ns();
    while ((!done(3) || !done(4)) && rt_now_ns() - t0 < 3000000000ull) usleep(1000);
    int fin = done(3) && done(4) && s_qn == 0;
    ok &= wo("waveOutClose", 1, HANDLE) == 0;
    result(ok && first && second && fin,
           "Reset between two Writes: the first headers are done and the queue empty, the later ones complete (%d %d %d)",
           first, second, fin);
}

static void option_case(void)
{
    char exe[PATH_MAX];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n)) {
        result(0, "own executable path is known");
        return;
    }
    fflush(stdout);
    fflush(stderr);
    char game[PATH_MAX];
    snprintf(game, sizeof game, "%s/optgame", rt_vfs_overlay());
    mkdir(game, 0755);
    int finished[2] = {0, 0}, off[2] = {0, 0}, logs[2] = {0, 0};
    for (int k = 0; k < 2; k++) {
        char ovl[PATH_MAX];
        snprintf(ovl, sizeof ovl, "%s/optrun%d", rt_vfs_overlay(), k);
        char *argv[] = {"env", "SDL_VIDEO_DRIVER=dummy", exe, "--run", k ? "--nosound" : "--guest-quiet", "--gfx", "null",
                        "--close-after", "1", "--game", game, "--overlay", ovl, NULL};
        pid_t pid = fork();
        if (pid == 0) {
            int nul = open("/dev/null", O_WRONLY);
            dup2(nul, 1);
            dup2(nul, 2);
            execv("/usr/bin/env", argv);
            _exit(127);
        }
        int st = 0, got = 0;
        uint64_t t0 = rt_now_ns();
        while (!got && rt_now_ns() - t0 < 5000000000ull) {
            pid_t r = waitpid(pid, &st, WNOHANG);
            if (r == pid) got = 1;
            else if (r < 0 && errno != EINTR) break;
            else usleep(10000);
        }
        if (!got) {
            kill(pid, SIGKILL);
            while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {
            }
        }
        finished[k] = got;
        char dir[PATH_MAX];
        snprintf(dir, sizeof dir, "%s/logs", ovl);
        DIR *d = opendir(dir);
        struct dirent *e;
        while (d && (e = readdir(d))) {
            char p[PATH_MAX];
            uint8_t *b = NULL;
            long len = 0;
            snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
            if (strncmp(e->d_name, "run-", 4) || !rd(p, &b, &len)) continue;
            b[len] = 0;
            logs[k] = 1;
            off[k] |= strstr((char *)b, "rt: audio off (--nosound)") != NULL;
            free(b);
        }
        if (d) closedir(d);
    }
    result(finished[0] && finished[1] && logs[0] && logs[1] && !off[0] && off[1],
           "--run logs audio off (--nosound) with --nosound and nothing without it (default %d, --nosound %d, logs %d %d, finished %d %d)",
           off[0], off[1], logs[0], logs[1], finished[0], finished[1]);
}

static void mixer_cases(void)
{
    static const char *const names[] = {"mixerClose", "mixerGetControlDetailsA", "mixerGetDevCapsA", "mixerGetLineControlsA",
                                        "mixerGetLineInfoA", "mixerOpen", "mixerSetControlDetails"};
    int ok = 1;
    for (int i = 0; i < 7; i++) ok &= wo(names[i], 0) == 1;
    result(ok, "the 7 mixer rows return 1");
}

int rt_audio_selftest(const char *game)
{
    (void)game;
    SDL_SetHintWithPriority(SDL_HINT_AUDIO_DRIVER, "dummy", SDL_HINT_OVERRIDE);
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    rt_audio_config(0, 1);
    setup_guest();
    refusal_cases();
    stream_cases();
    wav_cases();
    reopen_case();
    reset_between_case();
    option_case();
    mixer_cases();
    rt_dsound_cases(result);
    silent_case();
    int mp3_pass = 0;
    s_fail += rt_mp3_selftest(&mp3_pass);
    s_pass += mp3_pass;
    int bink_pass = 0;
    s_fail += rt_bink_selftest(&bink_pass);
    s_pass += bink_pass;
    printf("selftest: %d PASS, %d FAIL\n", s_pass, s_fail);
    rt_audio_report();
    fflush(stdout);
    fflush(stderr);
    return s_fail ? 1 : 0;
}
