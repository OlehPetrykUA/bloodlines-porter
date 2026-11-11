#include "recomp_types.h"
#include "rt.h"

#include <SDL3/SDL.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define DS_OK 0u
#define E_NOINTERFACE_ 0x80004002u
#define DSERR_UNSUPPORTED 0x80004001u
#define DSERR_GENERIC 0x80004005u
#define DSERR_INVALIDPARAM 0x80070057u
#define DSERR_ALLOCATED 0x8878000Au
#define DSERR_INVALIDCALL 0x88780032u
#define DSERR_PRIOLEVELNEEDED 0x88780046u
#define DSERR_BADFORMAT 0x88780064u
#define DSERR_NODRIVER 0x88780078u
#define DSERR_BUFFERLOST 0x88780096u
#define DSERR_NOAGGREGATION 0x80040110u

#define DSBCAPS_PRIMARYBUFFER 0x1u
#define DSBCAPS_STICKYFOCUS 0x4000u
#define DSBCAPS_GLOBALFOCUS 0x8000u
#define DSBPLAY_LOOPING 0x1u
#define DSBLOCK_FROMWRITECURSOR 0x1u
#define DSBLOCK_ENTIREBUFFER 0x2u
#define DSBSTATUS_PLAYING 0x1u
#define DSBSTATUS_LOOPING 0x4u
#define DSSCL_NORMAL 1u
#define DSSCL_WRITEPRIMARY 4u
#define DSCAPS_ALL_FORMATS 0xF1Fu

#define MAGIC 0xD5D5D5D5u
#define MAXO 32
#define DESC_MIN 20u
#define DSCAPS_SIZE 96u
#define DSBCAPS_SIZE 20u
#define LOGMAX 32

enum { K_DS, K_BUF, DK_N };

typedef struct {
    uint32_t proxy, coop;
    int kind, refs;
    int primary, playing, looping, locked;
    uint32_t flags, size, ring_va, rate, ch, bits, blk;
    uint8_t *ring, *snap;
    uint64_t head, wr_end, stop_abs, lk_abs, quiet_ns;
    uint32_t lk_off, lk_n1, lk_n2, lk_p1, lk_p2;
    int wr_seen, pass_open, pass_loud, focus_held;
    uint64_t pass_max, pass_start_ns, pass_lw;
} obj;

typedef struct {
    const char *name;
    uint32_t pop;
    rt_shim_fn fn;
} meth;

static obj s_o[MAXO];
static uint32_t s_vtbl[DK_N];
static int s_built;
static SDL_AudioStream *s_stream;
static obj *s_play;
static rt_wav *s_cap;
static int s_frames;
static uint64_t s_passes, s_quiet_passes, s_pass_bytes, s_pass_max, s_qmax, s_ahead_n, s_ahead_sum, s_ahead_min = ~0ull, s_ahead_max;
static uint64_t s_callbacks, s_unlocks, s_locks, s_starts;
static uint32_t s_writer, s_writer_logs;
static uint32_t s_logged;
static uint64_t s_bps = 176400u;
static _Atomic int s_unfocused;
static uint64_t s_muted_bytes;

void rt_dsound_focus(int on) { s_unfocused = !on; }

static void focus_paint(obj *b)
{
    if (!s_unfocused && b->wr_end > b->head) b->focus_held = 0;
}

static uint64_t ms_of(const obj *b, uint64_t bytes) { return bytes * 1000u / (b->rate * b->blk); }

static void lk(void)
{
    if (s_stream) SDL_LockAudioStream(s_stream);
}

static void unlk(void)
{
    if (s_stream) SDL_UnlockAudioStream(s_stream);
}

static obj *get(uint32_t va, int kind)
{
    if (!va || !rt_span_ok(va, 16) || MEM32(va + 12) != MAGIC) return NULL;
    uint32_t i = MEM32(va + 8);
    if (i >= MAXO || s_o[i].proxy != va || s_o[i].refs <= 0 || s_o[i].kind != kind) return NULL;
    return &s_o[i];
}

static uint32_t wrap(int kind, obj **out)
{
    for (uint32_t i = 0; i < MAXO; i++)
        if (s_o[i].refs <= 0) {
            uint32_t p = rt_heap_alloc(16, 1);
            if (!p) return 0;
            memset(&s_o[i], 0, sizeof s_o[i]);
            s_o[i].proxy = p;
            s_o[i].kind = kind;
            s_o[i].refs = 1;
            MEM32(p) = s_vtbl[kind];
            MEM32(p + 4) = (uint32_t)kind;
            MEM32(p + 8) = i;
            MEM32(p + 12) = MAGIC;
            *out = &s_o[i];
            return p;
        }
    return 0;
}

static void close_pass(obj *b, const char *how)
{
    if (!b->pass_open) return;
    b->pass_open = 0;
    int loud = b->pass_loud;
    b->pass_loud = 0;
    if (!loud) {
        s_quiet_passes++;
        return;
    }
    s_passes++;
    s_pass_bytes += b->pass_max;
    if (b->pass_max > s_pass_max) s_pass_max = b->pass_max;
    if (s_logged < LOGMAX) {
        s_logged++;
        rt_log("audio dsound cursor pass: %llu ms of old audio replayed, engine quiet %llu ms (%s)",
               (unsigned long long)ms_of(b, b->pass_max), (unsigned long long)((rt_now_ns() - b->pass_lw) / 1000000u), how);
    }
}

static void SDLCALL on_get(void *u, SDL_AudioStream *s, int add, int total)
{
    (void)u;
    (void)total;
    obj *b = s_play;
    if (!b || !b->size) return;
    s_callbacks++;
    add -= add % (int)b->blk;
    while (add > 0) {
        uint32_t pos = (uint32_t)(b->head % b->size), n = b->size - pos;
        if ((int)n > add) n = (uint32_t)add;
        if (b->playing && !b->looping && b->head + n >= b->stop_abs) n = (uint32_t)(b->stop_abs - b->head);
        if (b->playing && n) {
            int away = s_unfocused && !(b->flags & (DSBCAPS_GLOBALFOCUS | DSBCAPS_STICKYFOCUS));
            if (away) b->focus_held = 1;
            int mute = away || (b->focus_held && !(b->flags & (DSBCAPS_GLOBALFOCUS | DSBCAPS_STICKYFOCUS)));
            if (!mute && b->wr_seen && !b->pass_loud && b->head + n > b->wr_end)
                for (uint32_t k = b->head >= b->wr_end ? 0 : (uint32_t)(b->wr_end - b->head); k < n; k++)
                    if (b->ring[pos + k]) {
                        b->pass_loud = 1;
                        break;
                    }
            if (mute) {
                static const uint8_t z[4096];
                for (uint32_t k = 0; k < n;) {
                    uint32_t m = n - k < sizeof z ? n - k : (uint32_t)sizeof z;
                    SDL_PutAudioStreamData(s, z, (int)m);
                    if (s_cap) rt_wav_write(s_cap, z, m);
                    k += m;
                }
                s_muted_bytes += n;
            } else {
                SDL_PutAudioStreamData(s, b->ring + pos, (int)n);
                if (s_cap) rt_wav_write(s_cap, b->ring + pos, n);
            }
            b->head += n;
        } else if (!b->playing) {
            if (s_cap) {
                static uint8_t z[4096];
                uint32_t k = (uint32_t)add;
                while (k) {
                    uint32_t m = k < sizeof z ? k : (uint32_t)sizeof z;
                    rt_wav_write(s_cap, z, m);
                    k -= m;
                }
            }
            break;
        }
        add -= (int)n;
        if (b->playing && !b->looping && b->head >= b->stop_abs) {
            b->playing = 0;
            break;
        }
    }
    int q = SDL_GetAudioStreamQueued(s);
    if (q > 0 && (uint64_t)q > s_qmax) s_qmax = (uint64_t)q;
    if (b->wr_seen && b->head > b->wr_end) {
        if (!b->pass_open) {
            b->pass_open = 1;
            b->pass_max = 0;
            b->pass_lw = b->quiet_ns;
        }
        if (b->head - b->wr_end > b->pass_max) b->pass_max = b->head - b->wr_end;
    }
}

static void log_device(void)
{
    SDL_AudioDeviceID d = SDL_GetAudioStreamDevice(s_stream);
    SDL_AudioSpec sp = {0};
    int frames = 0;
    const char *n = d ? SDL_GetAudioDeviceName(d) : NULL;
    if (d) SDL_GetAudioDeviceFormat(d, &sp, &frames);
    s_frames = frames;
    rt_log("audio dsound stream device \"%s\" device %d/%d ch, period %d frames (%d ms)", n ? n : "?", sp.freq, sp.channels, frames,
           sp.freq ? frames * 1000 / sp.freq : 0);
}

static int stream_open(obj *b)
{
    if (rt_audio_init() != 0) return 0;
    SDL_AudioSpec sp = {b->bits == 8 ? SDL_AUDIO_U8 : SDL_AUDIO_S16LE, (int)b->ch, (int)b->rate};
    s_play = b;
    s_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &sp, on_get, NULL);
    if (!s_stream && rt_audio_go_silent(SDL_GetError()) == 0)
        s_stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &sp, on_get, NULL);
    if (!s_stream) {
        s_play = NULL;
        return 0;
    }
    s_bps = b->rate * b->blk;
    s_cap = rt_wav_open("dsound", (int)b->rate, (int)b->ch);
    log_device();
    return 1;
}

static void stream_close(void)
{
    if (!s_stream) return;
    SDL_LockAudioStream(s_stream);
    s_play = NULL;
    SDL_UnlockAudioStream(s_stream);
    SDL_DestroyAudioStream(s_stream);
    s_stream = NULL;
}

static uint32_t unsupported(const char *iface, int slot, const char *name)
{
    rt_log("audio dsound %s slot %d %s is not implemented", iface, slot, name);
    return DSERR_UNSUPPORTED;
}

static const uint8_t k_iid_unknown[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46};
static const uint8_t k_iid_ds[16] = {0x83, 0xFA, 0x9A, 0x27, 0x81, 0x49, 0xCE, 0x11, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60};
static const uint8_t k_iid_buf[16] = {0x85, 0xFA, 0x9A, 0x27, 0x81, 0x49, 0xCE, 0x11, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60};

static uint32_t qi(const uint32_t *a, int kind, const uint8_t *want)
{
    obj *o = get(a[0], kind);
    if (!o || !a[1] || !a[2] || !rt_span_ok(a[1], 16) || !rt_span_ok(a[2], 4)) return DSERR_INVALIDPARAM;
    if (!memcmp(rt_host(a[1]), k_iid_unknown, 16) || !memcmp(rt_host(a[1]), want, 16)) {
        o->refs++;
        MEM32(a[2]) = a[0];
        return DS_OK;
    }
    MEM32(a[2]) = 0;
    return E_NOINTERFACE_;
}

static uint32_t addref(const uint32_t *a, int kind)
{
    obj *o = get(a[0], kind);
    return o ? (uint32_t)++o->refs : 0;
}

static void drop_buffer(obj *b)
{
    if (b == s_play) {
        stream_close();
        s_cap = NULL;
    }
    if (b->ring_va) rt_heap_free(b->ring_va);
    b->ring_va = 0;
    b->ring = NULL;
    free(b->snap);
    b->snap = NULL;
}

static uint32_t release(const uint32_t *a, int kind)
{
    obj *o = get(a[0], kind);
    if (!o) return 0;
    if (--o->refs > 0) return (uint32_t)o->refs;
    if (kind == K_BUF) drop_buffer(o);
    MEM32(o->proxy + 12) = 0;
    o->refs = 0;
    return 0;
}

static uint32_t ds_qi(const uint32_t *a) { return qi(a, K_DS, k_iid_ds); }
static uint32_t ds_addref(const uint32_t *a) { return addref(a, K_DS); }
static uint32_t ds_release(const uint32_t *a) { return release(a, K_DS); }
static uint32_t buf_qi(const uint32_t *a) { return qi(a, K_BUF, k_iid_buf); }
static uint32_t buf_addref(const uint32_t *a) { return addref(a, K_BUF); }
static uint32_t buf_release(const uint32_t *a) { return release(a, K_BUF); }

static uint32_t s_coop, s_prim_va;

static uint32_t parse_fmt(uint32_t va, uint32_t *rate, uint32_t *ch, uint32_t *bits)
{
    if (!va || !rt_span_ok(va, 16)) return DSERR_INVALIDPARAM;
    uint32_t tag = MEM16(va), c = MEM16(va + 2), r = MEM32(va + 4), blk = MEM16(va + 12), bt = MEM16(va + 14);
    if (tag != 1 || (c != 1 && c != 2) || (bt != 8 && bt != 16) || r < 100 || r > 200000u || blk != c * bt / 8)
        return DSERR_BADFORMAT;
    *rate = r;
    *ch = c;
    *bits = bt;
    return DS_OK;
}

static uint32_t ds_createbuf(const uint32_t *a)
{
    obj *d = get(a[0], K_DS);
    if (!d || !a[1] || !a[2] || !rt_span_ok(a[1], DESC_MIN) || !rt_span_ok(a[2], 4)) return DSERR_INVALIDPARAM;
    if (a[3]) return DSERR_NOAGGREGATION;
    MEM32(a[2]) = 0;
    uint32_t sz = MEM32(a[1]), fl = MEM32(a[1] + 4), bytes = MEM32(a[1] + 8), wfx = MEM32(a[1] + 16);
    if (sz < DESC_MIN) return DSERR_INVALIDPARAM;
    obj *b = NULL;
    if (fl & DSBCAPS_PRIMARYBUFFER) {
        if (bytes || wfx) return DSERR_INVALIDPARAM;
        if (s_prim_va && (b = get(s_prim_va, K_BUF))) {
            b->refs++;
            MEM32(a[2]) = b->proxy;
            return DS_OK;
        }
        uint32_t p = wrap(K_BUF, &b);
        if (!p) return DSERR_GENERIC;
        b->primary = 1;
        b->flags = fl;
        s_prim_va = p;
        MEM32(a[2]) = p;
        rt_log("audio dsound CreateSoundBuffer primary flags 0x%X", fl);
        return DS_OK;
    }
    uint32_t rate, ch, bits, e = parse_fmt(wfx, &rate, &ch, &bits);
    if (e) return e;
    uint32_t blk = ch * bits / 8;
    if (bytes < 4 || bytes > 0x0FFFFFFFu || bytes % blk) return DSERR_INVALIDPARAM;
    uint32_t ring = rt_heap_alloc(bytes, 1);
    if (!ring) return DSERR_GENERIC;
    uint32_t p = wrap(K_BUF, &b);
    if (!p) {
        rt_heap_free(ring);
        return DSERR_GENERIC;
    }
    b->flags = fl;
    b->size = bytes;
    b->ring_va = ring;
    b->ring = rt_host(ring);
    b->rate = rate;
    b->ch = ch;
    b->bits = bits;
    b->blk = blk;
    if (bits == 8) memset(b->ring, 0x80, bytes);
    MEM32(a[2]) = p;
    rt_log("audio dsound CreateSoundBuffer secondary flags 0x%X bytes %u (%llu ms) %u Hz %u ch %u bit", fl, bytes,
           (unsigned long long)ms_of(b, bytes), rate, ch, bits);
    return DS_OK;
}

static uint32_t ds_getcaps(const uint32_t *a)
{
    if (!get(a[0], K_DS) || !a[1] || !rt_span_ok(a[1], DSCAPS_SIZE) || MEM32(a[1]) != DSCAPS_SIZE) return DSERR_INVALIDPARAM;
    for (uint32_t i = 4; i < DSCAPS_SIZE; i += 4) MEM32(a[1] + i) = 0;
    MEM32(a[1] + 4) = DSCAPS_ALL_FORMATS;
    MEM32(a[1] + 8) = 100;
    MEM32(a[1] + 12) = 200000;
    MEM32(a[1] + 16) = 1;
    return DS_OK;
}

static uint32_t ds_setcoop(const uint32_t *a)
{
    if (!get(a[0], K_DS) || a[2] < DSSCL_NORMAL || a[2] > DSSCL_WRITEPRIMARY) return DSERR_INVALIDPARAM;
    s_coop = a[2];
    rt_log("audio dsound SetCooperativeLevel %u", a[2]);
    return DS_OK;
}

static uint32_t buf_getcaps(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b || !a[1] || !rt_span_ok(a[1], DSBCAPS_SIZE) || MEM32(a[1]) != DSBCAPS_SIZE) return DSERR_INVALIDPARAM;
    MEM32(a[1] + 4) = (b->flags & ~0x4u) | 0x8u;
    MEM32(a[1] + 8) = b->size;
    MEM32(a[1] + 12) = 0;
    MEM32(a[1] + 16) = 0;
    return DS_OK;
}

static uint32_t buf_getpos(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b || (a[1] && !rt_span_ok(a[1], 4)) || (a[2] && !rt_span_ok(a[2], 4))) return DSERR_INVALIDPARAM;
    uint32_t play = 0, wr = 0;
    if (!b->primary) {
        lk();
        uint64_t head = b->head;
        int q = b == s_play && s_stream ? SDL_GetAudioStreamQueued(s_stream) : 0;
        unlk();
        uint64_t qb = q > 0 ? (uint64_t)q - (uint64_t)q % b->blk : 0;
        play = (uint32_t)((head > qb ? head - qb : 0) % b->size);
        wr = (uint32_t)(head % b->size);
    }
    if (a[1]) MEM32(a[1]) = play;
    if (a[2]) MEM32(a[2]) = wr;
    return DS_OK;
}

static uint32_t buf_getstatus(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b || !a[1] || !rt_span_ok(a[1], 4)) return DSERR_INVALIDPARAM;
    lk();
    MEM32(a[1]) = b->playing ? DSBSTATUS_PLAYING | (b->looping ? DSBSTATUS_LOOPING : 0u) : 0u;
    unlk();
    return DS_OK;
}

static uint32_t buf_lock(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b || !a[3] || !a[4] || !rt_span_ok(a[3], 4) || !rt_span_ok(a[4], 4)) return DSERR_INVALIDPARAM;
    if (a[5] && (!a[6] || !rt_span_ok(a[5], 4) || !rt_span_ok(a[6], 4))) return DSERR_INVALIDPARAM;
    if (b->primary) return DSERR_INVALIDCALL;
    uint32_t off = a[1], n = a[2], fl = a[7];
    lk();
    uint64_t head = b->head;
    unlk();
    if (fl & DSBLOCK_FROMWRITECURSOR) off = (uint32_t)(head % b->size);
    if (fl & DSBLOCK_ENTIREBUFFER) n = b->size;
    if (off >= b->size || !n || n > b->size) return DSERR_INVALIDPARAM;
    uint32_t n1 = b->size - off < n ? b->size - off : n, n2 = a[5] ? n - n1 : 0;
    uint64_t lo = head > b->size / 4 ? head - b->size / 4 : 0;
    b->lk_abs = lo + (off + b->size - (uint32_t)(lo % b->size)) % b->size;
    b->lk_off = off;
    b->lk_n1 = n1;
    b->lk_n2 = n2;
    b->lk_p1 = b->ring_va + off;
    b->lk_p2 = n2 ? b->ring_va : 0;
    if (!b->snap) b->snap = malloc(b->size);
    if (b->snap) {
        memcpy(b->snap + off, b->ring + off, n1);
        if (n2) memcpy(b->snap, b->ring, n2);
    }
    b->locked = 1;
    MEM32(a[3]) = b->lk_p1;
    MEM32(a[4]) = n1;
    if (a[5]) {
        MEM32(a[5]) = b->lk_p2;
        MEM32(a[6]) = n2;
    }
    s_locks++;
    return DS_OK;
}

static uint64_t painted_end(const obj *b, uint32_t n1, uint32_t n2)
{
    uint64_t e = 0;
    if (!b->snap) e = b->lk_abs + n1 + n2;
    for (uint32_t j = n2; b->snap && !e && j-- > 0;)
        if (b->ring[j] != b->snap[j]) e = b->lk_abs + n1 + j + 1;
    for (uint32_t i = n1; b->snap && !e && i-- > 0;)
        if (b->ring[b->lk_off + i] != b->snap[b->lk_off + i]) e = b->lk_abs + i + 1;
    return e + (b->blk - e % b->blk) % b->blk;
}

static uint32_t buf_unlock(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b) return DSERR_INVALIDPARAM;
    if (b->primary || !b->locked) return DSERR_INVALIDCALL;
    if (a[1] != b->lk_p1 || a[3] != b->lk_p2 || a[2] > b->lk_n1 || a[4] > b->lk_n2) return DSERR_INVALIDPARAM;
    b->locked = 0;
    uint64_t end = painted_end(b, a[2], a[2] == b->lk_n1 ? a[4] : 0);
    lk();
    if (end > b->wr_end) b->wr_end = end;
    b->wr_seen = 1;
    focus_paint(b);
    if (b->pass_open && b->wr_end > b->head) close_pass(b, "engine caught up");
    if (b->playing) {
        int q = b == s_play && s_stream ? SDL_GetAudioStreamQueued(s_stream) : 0;
        uint64_t qb = q > 0 ? (uint64_t)q - (uint64_t)q % b->blk : 0, pl = b->head > qb ? b->head - qb : 0;
        if (b->wr_end > pl) {
            uint64_t ms = ms_of(b, b->wr_end - pl);
            s_ahead_n++;
            s_ahead_sum += ms;
            if (ms < s_ahead_min) s_ahead_min = ms;
            if (ms > s_ahead_max) s_ahead_max = ms;
        }
    }
    b->quiet_ns = rt_now_ns();
    rt_thread *t = rt_self();
    uint32_t w = t ? t->tid : 0;
    if (w != s_writer && s_writer_logs < 8) {
        s_writer_logs++;
        rt_log("audio dsound writer thread 0x%X%s", w, t && t->primary ? " (primary)" : "");
    }
    s_writer = w;
    unlk();
    s_unlocks++;
    return DS_OK;
}

static uint32_t buf_play(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b || a[1] || a[2]) return DSERR_INVALIDPARAM;
    int loop = (a[3] & DSBPLAY_LOOPING) != 0;
    if (b->primary) {
        if (!loop) return DSERR_INVALIDPARAM;
        b->playing = b->looping = 1;
        return DS_OK;
    }
    lk();
    int other = s_play && s_play != b && s_play->playing;
    unlk();
    if (other) {
        rt_log("audio dsound Play: a second secondary buffer is already playing");
        return DSERR_GENERIC;
    }
    if (!s_stream || s_play != b) {
        if (s_stream) stream_close();
        if (!stream_open(b)) return DSERR_GENERIC;
    }
    lk();
    if (!b->playing || b->looping != loop) {
        b->stop_abs = b->head + (b->size - b->head % b->size);
        if (!b->playing) s_starts++;
    }
    b->playing = 1;
    b->looping = loop;
    b->quiet_ns = rt_now_ns();
    unlk();
    if (!SDL_ResumeAudioStreamDevice(s_stream)) return DSERR_GENERIC;
    if (s_starts == 1) rt_log("audio path: DirectSound, secondary buffer playing %s", loop ? "looping" : "once");
    return DS_OK;
}

static uint32_t buf_stop(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    if (!b) return DSERR_INVALIDPARAM;
    if (b->primary) {
        b->playing = b->looping = 0;
        return DS_OK;
    }
    lk();
    if (b->playing) {
        int q = b == s_play && s_stream ? SDL_GetAudioStreamQueued(s_stream) : 0;
        uint64_t qb = q > 0 ? (uint64_t)q - (uint64_t)q % b->blk : 0;
        b->head -= qb < b->head ? qb : b->head;
        if (s_stream && b == s_play) SDL_ClearAudioStream(s_stream);
        b->playing = 0;
    }
    close_pass(b, "stopped");
    unlk();
    return DS_OK;
}

static uint32_t buf_restore(const uint32_t *a) { return get(a[0], K_BUF) ? DS_OK : DSERR_INVALIDPARAM; }

static uint32_t buf_setformat(const uint32_t *a)
{
    obj *b = get(a[0], K_BUF);
    uint32_t rate, ch, bits, e;
    if (!b) return DSERR_INVALIDPARAM;
    if (!b->primary) return DSERR_INVALIDCALL;
    if (s_coop <= DSSCL_NORMAL) return DSERR_PRIOLEVELNEEDED;
    if ((e = parse_fmt(a[1], &rate, &ch, &bits)) != DS_OK) return e;
    b->rate = rate;
    b->ch = ch;
    b->bits = bits;
    rt_log("audio dsound primary SetFormat %u Hz %u ch %u bit", rate, ch, bits);
    return DS_OK;
}

#define STUB(fn, iface, slot, nm) \
    static uint32_t fn(const uint32_t *a) \
    { \
        (void)a; \
        return unsupported(iface, slot, nm); \
    }

STUB(ds_dup, "IDirectSound", 5, "DuplicateSoundBuffer")
STUB(ds_compact, "IDirectSound", 7, "Compact")
STUB(ds_getspk, "IDirectSound", 8, "GetSpeakerConfig")
STUB(ds_setspk, "IDirectSound", 9, "SetSpeakerConfig")
STUB(ds_init, "IDirectSound", 10, "Initialize")
STUB(buf_getfmt, "IDirectSoundBuffer", 5, "GetFormat")
STUB(buf_getvol, "IDirectSoundBuffer", 6, "GetVolume")
STUB(buf_getpan, "IDirectSoundBuffer", 7, "GetPan")
STUB(buf_getfreq, "IDirectSoundBuffer", 8, "GetFrequency")
STUB(buf_init, "IDirectSoundBuffer", 10, "Initialize")
STUB(buf_setpos, "IDirectSoundBuffer", 13, "SetCurrentPosition")
STUB(buf_setvol, "IDirectSoundBuffer", 15, "SetVolume")
STUB(buf_setpan, "IDirectSoundBuffer", 16, "SetPan")
STUB(buf_setfreq, "IDirectSoundBuffer", 17, "SetFrequency")

static const meth k_ds[] = {
    {"IDirectSound::QueryInterface", 3, ds_qi},
    {"IDirectSound::AddRef", 1, ds_addref},
    {"IDirectSound::Release", 1, ds_release},
    {"IDirectSound::CreateSoundBuffer", 4, ds_createbuf},
    {"IDirectSound::GetCaps", 2, ds_getcaps},
    {"IDirectSound::DuplicateSoundBuffer", 3, ds_dup},
    {"IDirectSound::SetCooperativeLevel", 3, ds_setcoop},
    {"IDirectSound::Compact", 1, ds_compact},
    {"IDirectSound::GetSpeakerConfig", 2, ds_getspk},
    {"IDirectSound::SetSpeakerConfig", 2, ds_setspk},
    {"IDirectSound::Initialize", 2, ds_init},
};

static const meth k_buf[] = {
    {"IDirectSoundBuffer::QueryInterface", 3, buf_qi},
    {"IDirectSoundBuffer::AddRef", 1, buf_addref},
    {"IDirectSoundBuffer::Release", 1, buf_release},
    {"IDirectSoundBuffer::GetCaps", 2, buf_getcaps},
    {"IDirectSoundBuffer::GetCurrentPosition", 3, buf_getpos},
    {"IDirectSoundBuffer::GetFormat", 4, buf_getfmt},
    {"IDirectSoundBuffer::GetVolume", 2, buf_getvol},
    {"IDirectSoundBuffer::GetPan", 2, buf_getpan},
    {"IDirectSoundBuffer::GetFrequency", 2, buf_getfreq},
    {"IDirectSoundBuffer::GetStatus", 2, buf_getstatus},
    {"IDirectSoundBuffer::Initialize", 3, buf_init},
    {"IDirectSoundBuffer::Lock", 8, buf_lock},
    {"IDirectSoundBuffer::Play", 4, buf_play},
    {"IDirectSoundBuffer::SetCurrentPosition", 2, buf_setpos},
    {"IDirectSoundBuffer::SetFormat", 2, buf_setformat},
    {"IDirectSoundBuffer::SetVolume", 2, buf_setvol},
    {"IDirectSoundBuffer::SetPan", 2, buf_setpan},
    {"IDirectSoundBuffer::SetFrequency", 2, buf_setfreq},
    {"IDirectSoundBuffer::Stop", 1, buf_stop},
    {"IDirectSoundBuffer::Unlock", 5, buf_unlock},
    {"IDirectSoundBuffer::Restore", 1, buf_restore},
};

static void build(void)
{
    if (s_built) return;
    s_built = 1;
    const meth *tabs[DK_N] = {k_ds, k_buf};
    const uint32_t ns[DK_N] = {sizeof k_ds / sizeof k_ds[0], sizeof k_buf / sizeof k_buf[0]};
    for (int k = 0; k < DK_N; k++) {
        rt_shim *rows = calloc(ns[k], sizeof *rows);
        uint32_t v = rt_heap_alloc(4 * ns[k], 1);
        if (!rows || !v) rt_fatal(5, "out of memory for the dsound vtables");
        for (uint32_t i = 0; i < ns[k]; i++) {
            rows[i] = (rt_shim){"dsound.dll", tabs[k][i].name, tabs[k][i].pop, tabs[k][i].fn, RT_SHIM_REAL};
            MEM32(v + 4 * i) = rt_dynamic_shim("dsound.dll", tabs[k][i].name, &rows[i]);
        }
        s_vtbl[k] = v;
    }
}

static uint32_t DirectSoundCreate(const uint32_t *a)
{
    if (!a[1] || !rt_span_ok(a[1], 4)) return DSERR_INVALIDPARAM;
    MEM32(a[1]) = 0;
    if (a[2]) return DSERR_NOAGGREGATION;
    if (rt_audio_nosound()) return DSERR_NODRIVER;
    build();
    obj *o;
    uint32_t p = wrap(K_DS, &o);
    if (!p) return DSERR_GENERIC;
    MEM32(a[1]) = p;
    rt_log("audio dsound DirectSoundCreate -> IDirectSound 0x%08X", p);
    return DS_OK;
}

void rt_dsound_report(void)
{
    if (!s_starts && !s_locks) return;
    rt_log("audio dsound path: DirectSound; starts %llu callbacks %llu locks %llu unlocks %llu", (unsigned long long)s_starts,
           (unsigned long long)s_callbacks, (unsigned long long)s_locks, (unsigned long long)s_unlocks);
    rt_log("audio dsound cursor passes %llu, old audio replayed %llu ms in all, longest %llu ms; silent passes %llu",
           (unsigned long long)s_passes, (unsigned long long)(s_pass_bytes * 1000u / s_bps),
           (unsigned long long)(s_pass_max * 1000u / s_bps), (unsigned long long)s_quiet_passes);
    rt_log("audio dsound muted while the game had no focus: %llu ms", (unsigned long long)(s_muted_bytes * 1000u / s_bps));
    rt_log("audio dsound mix-ahead at Unlock min %llu avg %llu max %llu ms over %llu; SDL queued after fill max %llu ms; device period %d frames",
           (unsigned long long)(s_ahead_n ? s_ahead_min : 0), (unsigned long long)(s_ahead_n ? s_ahead_sum / s_ahead_n : 0),
           (unsigned long long)s_ahead_max, (unsigned long long)s_ahead_n, (unsigned long long)(s_qmax * 1000u / s_bps),
           s_frames);
}

const rt_shim rt_dsound_shims[] = {
    {"dsound.dll", "DirectSoundCreate", 3, DirectSoundCreate, RT_SHIM_REAL},
};

const uint32_t rt_ndsound_shims = sizeof rt_dsound_shims / sizeof rt_dsound_shims[0];

typedef void (*res_fn)(int ok, const char *fmt, ...);

static uint32_t call(const meth *t, int slot, int n, ...)
{
    uint32_t a[8] = {0};
    va_list ap;
    va_start(ap, n);
    for (int i = 0; i < n && i < 8; i++) a[i] = va_arg(ap, uint32_t);
    va_end(ap);
    return t[slot].fn(a);
}

enum { DS_REL = 2, DS_CREATE = 3, DS_CAPS = 4, DS_COOP = 6 };
enum { B_REL = 2, B_CAPS = 3, B_POS = 4, B_STATUS = 9, B_LOCK = 11, B_PLAY = 12, B_FMT = 14, B_STOP = 18, B_UNLOCK = 19 };

static void sleep_ms(unsigned ms) { usleep(ms * 1000u); }

static uint32_t mk_desc(uint32_t flags, uint32_t bytes, uint32_t wfx)
{
    uint32_t d = rt_heap_alloc(36, 1);
    MEM32(d) = 36;
    MEM32(d + 4) = flags;
    MEM32(d + 8) = bytes;
    MEM32(d + 16) = wfx;
    return d;
}

static uint32_t mk_wfx(uint32_t tag, uint32_t ch, uint32_t rate, uint32_t bits)
{
    uint32_t w = rt_heap_alloc(18, 1);
    MEM16(w) = (uint16_t)tag;
    MEM16(w + 2) = (uint16_t)ch;
    MEM32(w + 4) = rate;
    MEM32(w + 8) = rate * ch * bits / 8;
    MEM16(w + 12) = (uint16_t)(ch * bits / 8);
    MEM16(w + 14) = (uint16_t)bits;
    return w;
}

void rt_dsound_cases(res_fn result)
{
    uint32_t out = rt_heap_alloc(64, 1), p1 = out + 4, b1 = out + 8, p2 = out + 12, b2 = out + 16;
    uint32_t r = DirectSoundCreate((uint32_t[]){0, out, 0});
    uint32_t ds = MEM32(out);
    result(rt_find_shim("dsound.dll", "DirectSoundCreate") != NULL && r == DS_OK && ds && MEM32(ds) == s_vtbl[K_DS],
           "DirectSoundCreate is in the shim table and returns an IDirectSound with a guest vtable (0x%X)", r);
    uint32_t caps = rt_heap_alloc(DSCAPS_SIZE, 1);
    MEM32(caps) = DSCAPS_SIZE;
    r = call(k_ds, DS_CAPS, 2, ds, caps);
    uint32_t r_bad = call(k_ds, DS_CAPS, 2, ds, rt_heap_alloc(8, 1));
    result(r == DS_OK && !(MEM32(caps + 4) & 0x20) && r_bad == DSERR_INVALIDPARAM,
           "GetCaps fills DSCAPS without DSCAPS_EMULDRIVER and rejects a wrong dwSize (0x%X 0x%X)", r, r_bad);

    uint32_t pd = mk_desc(DSBCAPS_PRIMARYBUFFER, 0, 0);
    r = call(k_ds, DS_CREATE, 4, ds, pd, out, 0);
    uint32_t prim = MEM32(out);
    uint32_t fmt = mk_wfx(1, 2, 44100, 16);
    uint32_t r_norm = call(k_buf, B_FMT, 2, prim, fmt);
    uint32_t r_coop = call(k_ds, DS_COOP, 3, ds, 0, 3u);
    uint32_t r_set = call(k_buf, B_FMT, 2, prim, fmt);
    uint32_t r_bad2 = call(k_buf, B_FMT, 2, prim, mk_wfx(2, 2, 44100, 16));
    uint32_t r_lock = call(k_buf, B_LOCK, 8, prim, 0, 16, p1, b1, p2, b2, 0);
    result(r == DS_OK && prim && r_norm == DSERR_PRIOLEVELNEEDED && r_coop == DS_OK && r_set == DS_OK && r_bad2 == DSERR_BADFORMAT &&
               r_lock == DSERR_INVALIDCALL,
           "primary buffer: SetFormat needs a priority level, rejects non-PCM, and Lock is refused (0x%X 0x%X 0x%X 0x%X 0x%X)", r_norm,
           r_coop, r_set, r_bad2, r_lock);

    uint32_t sd = mk_desc(0x10000u | 0x8u, 65536, fmt), r_small = 0;
    r = call(k_ds, DS_CREATE, 4, ds, sd, out, 0);
    uint32_t sec = MEM32(out);
    obj *b = get(sec, K_BUF);
    uint32_t dc = rt_heap_alloc(DSBCAPS_SIZE, 1);
    MEM32(dc) = DSBCAPS_SIZE;
    uint32_t r_caps = call(k_buf, B_CAPS, 2, sec, dc);
    r_small = call(k_ds, DS_CREATE, 4, ds, mk_desc(0, 3, fmt), out, 0);
    result(r == DS_OK && b && r_caps == DS_OK && MEM32(dc + 8) == 65536 && r_small == DSERR_INVALIDPARAM,
           "secondary buffer: 64 KB created, GetCaps reports its size, a size off the block align is refused (0x%X 0x%X)", r, r_small);
    if (!b) return;

    uint32_t base = b->ring_va;
    r = call(k_buf, B_LOCK, 8, sec, 60000u, 10000u, p1, b1, p2, b2, 0);
    result(r == DS_OK && MEM32(p1) == base + 60000 && MEM32(b1) == 5536 && MEM32(p2) == base && MEM32(b2) == 4464,
           "Lock across the end gives two regions: 5536 bytes at the offset, 4464 at the start (0x%X %u %u)", r, MEM32(b1), MEM32(b2));
    uint32_t u_bad = call(k_buf, 19, 5, sec, MEM32(p1) + 4, MEM32(b1), MEM32(p2), MEM32(b2));
    uint32_t u_ok = call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    uint32_t u_again = call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    result(u_bad == DSERR_INVALIDPARAM && u_ok == DS_OK && u_again == DSERR_INVALIDCALL,
           "Unlock rejects pointers that Lock did not return, accepts the pair, and a second Unlock is an invalid call (0x%X 0x%X 0x%X)",
           u_bad, u_ok, u_again);
    r = call(k_buf, B_LOCK, 8, sec, 100u, 50u, p1, b1, 0, 0, 0);
    uint32_t lone = MEM32(b1);
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), 50u, 0, 0);
    MEM32(p2) = 0xFFFFFFFFu;
    MEM32(b2) = 77;
    r_small = call(k_buf, B_LOCK, 8, sec, 100u, 50u, p1, b1, p2, b2, 0);
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), 50u, 0, 0);
    result(r == DS_OK && lone == 50 && r_small == DS_OK && MEM32(b1) == 50 && MEM32(p2) == 0 && MEM32(b2) == 0,
           "Lock inside the buffer gives one region and a NULL, zero-byte second region (0x%X 0x%X)", r, r_small);
    uint32_t e1 = call(k_buf, B_LOCK, 8, sec, 65536u, 4u, p1, b1, p2, b2, 0);
    uint32_t e2 = call(k_buf, B_LOCK, 8, sec, 0u, 65537u, p1, b1, p2, b2, 0);
    uint32_t e3 = call(k_buf, B_LOCK, 8, sec, 0u, 0u, p1, b1, p2, b2, 0);
    uint32_t e4 = call(k_buf, B_LOCK, 8, sec, 0u, 1u, p1, b1, p2, b2, DSBLOCK_ENTIREBUFFER);
    uint32_t n4 = MEM32(b1) + MEM32(b2);
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    uint32_t e5 = call(k_buf, B_LOCK, 8, sec, 12345u, 1u, p1, b1, p2, b2, DSBLOCK_FROMWRITECURSOR);
    uint32_t at_cursor = MEM32(p1) == base + (uint32_t)(b->head % b->size);
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    result(e1 == DSERR_INVALIDPARAM && e2 == DSERR_INVALIDPARAM && e3 == DSERR_INVALIDPARAM && e4 == DS_OK && n4 == 65536 &&
               e5 == DS_OK && at_cursor,
           "Lock refuses an offset or size out of range, DSBLOCK_ENTIREBUFFER locks 65536, FROMWRITECURSOR starts at the write cursor");

    uint64_t w0 = b->wr_end;
    call(k_buf, B_LOCK, 8, sec, 1000u, 0u, p1, b1, p2, b2, DSBLOCK_ENTIREBUFFER);
    uint64_t lk0 = b->lk_abs;
    uint8_t *q = rt_host(MEM32(p1));
    for (uint32_t i = 0; i < 4096; i++) q[i] ^= 0x5A;
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    uint64_t w1 = b->wr_end;
    call(k_buf, B_LOCK, 8, sec, 9000u, 0u, p1, b1, p2, b2, DSBLOCK_ENTIREBUFFER);
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    uint64_t w2 = b->wr_end;
    call(k_buf, B_LOCK, 8, sec, 60000u, 10000u, p1, b1, p2, b2, 0);
    uint64_t lk3 = b->lk_abs;
    q = rt_host(MEM32(p2));
    for (uint32_t i = 0; i < 100; i++) q[i] ^= 0x5A;
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    uint64_t w3 = b->wr_end;
    result(w0 == 0 && w1 == lk0 + 4096 && w2 == w1 && w3 == lk3 + 5536 + 100,
           "the painted end is the last byte changed between Lock and Unlock, not the end of the locked range: a whole-buffer Lock with 4096 "
           "bytes written ends %llu past it, an unchanged Lock moves nothing (%llu to %llu), a write in the wrapped part ends %llu past its Lock",
           (unsigned long long)(w1 - lk0), (unsigned long long)w1, (unsigned long long)w2, (unsigned long long)(w3 - lk3));

    uint32_t st = rt_heap_alloc(4, 1);
    call(k_buf, B_STATUS, 2, sec, st);
    uint32_t st0 = MEM32(st);
    uint8_t *ring = b->ring;
    call(k_buf, B_LOCK, 8, sec, 0u, 0u, p1, b1, p2, b2, DSBLOCK_ENTIREBUFFER);
    for (uint32_t i = 0; i < 65536; i += 2) {
        uint16_t v = (uint16_t)(((i / 2) % 251u) + 1u);
        memcpy(ring + i, &v, 2);
    }
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    uint64_t passes0 = s_passes;
    r = call(k_buf, B_PLAY, 4, sec, 0u, 0u, DSBPLAY_LOOPING);
    uint32_t r_prim = call(k_buf, B_PLAY, 4, prim, 0u, 0u, 0u);
    uint64_t t0 = rt_now_ns();
    call(k_buf, B_STATUS, 2, sec, st);
    uint32_t st1 = MEM32(st);
    uint32_t pos = rt_heap_alloc(8, 1);
    sleep_ms(200);
    call(k_buf, B_POS, 3, sec, pos, pos + 4);
    uint32_t play1 = MEM32(pos), wr1 = MEM32(pos + 4);
    uint64_t head1 = b->head, el1 = (rt_now_ns() - t0) / 1000000u;
    sleep_ms(600);
    call(k_buf, B_POS, 3, sec, pos, pos + 4);
    uint32_t play2 = MEM32(pos), wr2 = MEM32(pos + 4);
    uint64_t head2 = b->head, el2 = (rt_now_ns() - t0) / 1000000u;
    result(r == DS_OK && r_prim == DSERR_INVALIDPARAM && st0 == 0 && st1 == (DSBSTATUS_PLAYING | DSBSTATUS_LOOPING),
           "GetStatus is 0 before Play and PLAYING|LOOPING after Play(DSBPLAY_LOOPING); a primary Play without looping is refused (0x%X 0x%X 0x%X)",
           st0, st1, r_prim);
    uint64_t fed1 = head1 * 1000u / 176400u, fed2 = head2 * 1000u / 176400u;
    result(head1 > 0 && fed1 + 120 >= el1 && fed1 <= el1 + 300 && fed2 + 120 >= el2 && fed2 <= el2 + 300 && play1 < 65536 && wr1 < 65536 &&
               play2 < 65536 && wr2 < 65536,
           "the cursors are driven by the device: %llu ms handed over after %llu ms, %llu ms after %llu ms (play %u write %u, then play %u write %u)",
           (unsigned long long)fed1, (unsigned long long)el1, (unsigned long long)fed2, (unsigned long long)el2, play1, wr1, play2, wr2);
    result(head2 > 65536 && wr2 == (uint32_t)(head2 % 65536) && play2 <= 65535,
           "the cursor wrapped past the end of the 64 KB buffer and the write cursor is the position handed to the device (head %llu)",
           (unsigned long long)head2);
    call(k_buf, B_LOCK, 8, sec, 0u, 0u, p1, b1, p2, b2, DSBLOCK_FROMWRITECURSOR | DSBLOCK_ENTIREBUFFER);
    uint8_t *cq = rt_host(MEM32(p1));
    uint32_t cn = MEM32(b1) < 8192 ? MEM32(b1) : 8192;
    for (uint32_t i = 0; i < cn; i++) cq[i] ^= 0x5A;
    call(k_buf, B_UNLOCK, 5, sec, MEM32(p1), MEM32(b1), MEM32(p2), MEM32(b2));
    for (uint32_t i = 0; i < cn; i++) cq[i] ^= 0x5A;
    result(s_passes > passes0, "a stall longer than the buffer is counted as a cursor pass once the engine writes again (passes %llu to %llu)",
           (unsigned long long)passes0, (unsigned long long)s_passes);
    const char *wavpath = rt_wav_path(s_cap);
    uint32_t rs = call(k_buf, B_STOP, 1, sec);
    call(k_buf, B_STATUS, 2, sec, st);
    uint32_t st2 = MEM32(st);
    call(k_buf, B_POS, 3, sec, pos, pos + 4);
    uint32_t sp = MEM32(pos), sw = MEM32(pos + 4);
    sleep_ms(120);
    call(k_buf, B_POS, 3, sec, pos, pos + 4);
    result(rs == DS_OK && st2 == 0 && MEM32(pos) == sp && MEM32(pos + 4) == sw,
           "Stop clears the status and the cursors stay where they stopped (play %u write %u)", sp, sw);
    FILE *f = wavpath[0] ? fopen(wavpath, "rb") : NULL;
    uint8_t *d = NULL;
    long n = 0;
    if (f) {
        fseek(f, 0, SEEK_END);
        n = ftell(f);
        fseek(f, 44, SEEK_SET);
        n -= 44;
        d = malloc((size_t)(n > 0 ? n : 1));
        if (d && fread(d, 1, (size_t)n, f) != (size_t)n) n = 0;
        fclose(f);
    }
    long check = n < 90000 ? n : 90000;
    long bad = d ? 0 : 1;
    for (long i = 0; d && i < check; i++)
        if (d[i] != ring[i % 65536]) bad++;
    result(d && check > 65536 && !bad,
           "the audio sent to the device is the looping buffer: the first %ld bytes match the ring read from offset 0, wrapping at 65536 (%ld differ)",
           check, bad);
    free(d);

    {
        obj z;
        memset(&z, 0, sizeof z);
        z.size = 4096;
        z.blk = 4;
        z.rate = 44100;
        z.ring = calloc(1, z.size);
        z.playing = z.looping = z.wr_seen = 1;
        SDL_AudioSpec zs = {SDL_AUDIO_S16, 2, 44100};
        SDL_AudioStream *t = SDL_CreateAudioStream(&zs, &zs);
        obj *keep_play = s_play;
        rt_wav *keep_cap = s_cap;
        s_play = &z;
        s_cap = NULL;
        uint64_t q0 = s_quiet_passes, a0 = s_passes;
        on_get(NULL, t, 8192, 8192);
        z.wr_end = z.head + 64;
        close_pass(&z, "selftest");
        uint64_t q1 = s_quiet_passes, a1 = s_passes;
        memset(z.ring, 0x11, z.size);
        on_get(NULL, t, 8192, 8192);
        z.wr_end = z.head + 64;
        close_pass(&z, "selftest");
        s_play = keep_play;
        s_cap = keep_cap;
        SDL_DestroyAudioStream(t);
        free(z.ring);
        result(t && q1 == q0 + 1 && a1 == a0 && s_quiet_passes == q1 && s_passes == a1 + 1,
               "a replay of unpainted silence is counted as a silent pass, not as old audio, and an audible replay is (silent %llu to %llu, "
               "audible %llu to %llu)",
               (unsigned long long)q0, (unsigned long long)s_quiet_passes, (unsigned long long)a0, (unsigned long long)s_passes);
    }

    {
        obj z;
        memset(&z, 0, sizeof z);
        z.size = 4096;
        z.blk = 4;
        z.rate = 44100;
        z.ring = malloc(z.size);
        memset(z.ring, 0x22, z.size);
        z.playing = z.looping = 1;
        SDL_AudioSpec zs = {SDL_AUDIO_S16, 2, 44100};
        SDL_AudioStream *t = SDL_CreateAudioStream(&zs, &zs);
        obj *keep_play = s_play;
        rt_wav *keep_cap = s_cap;
        s_play = &z;
        s_cap = NULL;
        static uint8_t out[4096];
        const uint32_t fl[5] = {0x28u, 0x28u | DSBCAPS_GLOBALFOCUS, 0x28u | DSBCAPS_STICKYFOCUS, 0x28u, 0x28u};
        int got[5], loud[5];
        uint64_t moved[5];
        for (int i = 0; i < 5; i++) {
            rt_dsound_focus(i >= 3);
            z.flags = fl[i];
            if (i == 4) {
                z.wr_end = z.head + 4096;
                focus_paint(&z);
            }
            SDL_ClearAudioStream(t);
            uint64_t h0 = z.head;
            on_get(NULL, t, 2048, 2048);
            moved[i] = z.head - h0;
            got[i] = SDL_GetAudioStreamData(t, out, sizeof out);
            loud[i] = 0;
            for (int k = 0; k < got[i]; k++)
                if (out[k]) {
                    loud[i] = 1;
                    break;
                }
        }
        rt_dsound_focus(1);
        s_play = keep_play;
        s_cap = keep_cap;
        SDL_DestroyAudioStream(t);
        free(z.ring);
        result(t && got[0] == 2048 && !loud[0] && moved[0] == 2048 && loud[1] && loud[2] && !loud[3] && loud[4],
               "without the focus a buffer lacking DSBCAPS_GLOBALFOCUS and DSBCAPS_STICKYFOCUS plays silence while its cursor moves on; either flag "
               "plays its data; with the focus back it stays silent until the engine paints ahead of the cursor (bytes %d, loud %d %d %d %d %d, "
               "cursor %llu)",
               got[0], loud[0], loud[1], loud[2], loud[3], loud[4], (unsigned long long)moved[0]);
    }

    uint32_t small = 0;
    call(k_ds, DS_CREATE, 4, ds, mk_desc(0, 4096, fmt), out, 0);
    small = MEM32(out);
    obj *sb = get(small, K_BUF);
    r = call(k_buf, B_PLAY, 4, small, 0u, 0u, 0u);
    call(k_buf, B_STATUS, 2, small, st);
    uint32_t sa = MEM32(st);
    sleep_ms(500);
    call(k_buf, B_STATUS, 2, small, st);
    result(sb && r == DS_OK && sa == DSBSTATUS_PLAYING && MEM32(st) == 0,
           "Play without looping plays the 4096 bytes once and the status drops to 0 (0x%X, then 0x%X)", sa, MEM32(st));
    uint32_t rl = call(k_buf, B_REL, 1, small), rp = call(k_buf, B_REL, 1, sec), rd = call(k_ds, DS_REL, 1, ds);
    result(rl == 0 && rp == 0 && rd == 0 && !s_stream, "Release drops the buffers and the IDirectSound and closes the stream (%u %u %u)", rl,
           rp, rd);
}
