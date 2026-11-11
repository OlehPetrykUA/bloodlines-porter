#include <SDL3/SDL.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/error.h>
#include <libavutil/frame.h>
#include <libavutil/log.h>
#include <libavutil/pixfmt.h>
#include <libavutil/samplefmt.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdarg.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "recomp_types.h"
#include "rt.h"

enum {
    HB_WIDTH = 0x00,
    HB_HEIGHT = 0x04,
    HB_FRAMES = 0x08,
    HB_FRAMENUM = 0x0c,
    HB_RECTS = 0x34,
    HB_SIZE = 0x80,
    BB_WIDTH = 0x00,
    BB_HEIGHT = 0x04,
    BB_SURFACE = 0x10,
    BB_BUFFER = 0x14,
    BB_PITCH = 0x18,
    BB_SIZE = 0x40,
    BIK_HDR_FRAMES = 0x08,
    BIK_HDR_FPS_NUM = 0x1c,
    BIK_HDR_FPS_DEN = 0x20,
    BIK_HDR_SIZE = 0x24,
    SURFACE_XRGB = 0x58524742,
    MAX_MOVIES = 8
};

static struct {
    int open;
    char path[260];
    AVFormatContext *fmt;
    AVCodecContext *vctx, *actx;
    AVPacket *pkt;
    AVFrame *vf, *nf, *af;
    int vi, ai, eof, w, h;
    uint32_t hbink, frames, fps_num, fps_den, shown;
    uint32_t buf, buf_va_pixels, scale_w, scale_h;
    uint8_t *yuv;
    uint8_t *rgb;
    int16_t *pcm;
    size_t pcm_cap;
    SDL_AudioStream *stream;
    uint64_t put_bytes;
    uint32_t bytes_per_s;
    rt_wav *wav;
    int started;
    uint64_t t0;
    int index;
} m;

static int s_novid, s_test;
static int s_cap_rate, s_cap_ch;
static uint8_t *s_cap;
static size_t s_cap_n, s_cap_cap;
static int s_count;
static uint32_t s_shown[MAX_MOVIES];

void rt_bink_config(int novid) { s_novid = novid; }

uint32_t rt_bink_shown(uint32_t movie) { return movie >= 1 && movie <= MAX_MOVIES ? s_shown[movie - 1] : 0; }

static uint32_t SetSoundSystem(const uint32_t *a)
{
    (void)a;
    return 1;
}

static void movie_free(void)
{
    if (m.stream) SDL_DestroyAudioStream(m.stream);
    if (m.wav) rt_wav_close(m.wav);
    av_frame_free(&m.vf);
    av_frame_free(&m.nf);
    av_frame_free(&m.af);
    av_packet_free(&m.pkt);
    avcodec_free_context(&m.vctx);
    avcodec_free_context(&m.actx);
    avformat_close_input(&m.fmt);
    free(m.yuv);
    free(m.rgb);
    free(m.pcm);
    memset(&m, 0, sizeof m);
}

static uint32_t fail_open(const char *path, const char *why, int err)
{
    char e[AV_ERROR_MAX_STRING_SIZE] = "";
    if (err < 0) av_strerror(err, e, sizeof e);
    rt_log("bink failed %s %s%s%s", path, why, e[0] ? ": " : "", e);
    movie_free();
    return 0;
}

static const char *base_name(const char *p, char *out, size_t n)
{
    const char *b = p;
    for (const char *c = p; *c; c++)
        if (*c == '\\' || *c == '/') b = c + 1;
    snprintf(out, n, "bink-%s", b);
    char *dot = strrchr(out, '.');
    if (dot) *dot = 0;
    for (char *c = out; *c; c++)
        if (*c >= 'A' && *c <= 'Z') *c += 'a' - 'A';
    return out;
}

static int open_decoder(AVCodecContext **ctx, AVStream *st)
{
    const AVCodec *c = avcodec_find_decoder(st->codecpar->codec_id);
    if (!c) return AVERROR_DECODER_NOT_FOUND;
    *ctx = avcodec_alloc_context3(c);
    if (!*ctx) return AVERROR(ENOMEM);
    int r = avcodec_parameters_to_context(*ctx, st->codecpar);
    if (r < 0) return r;
    return avcodec_open2(*ctx, c, NULL);
}

static uint32_t BinkOpen(const uint32_t *a)
{
    const char *guest = rt_gstr(a[0]);
    if (s_novid) {
        rt_log("bink skipped %s (--novid)", guest);
        return 0;
    }
    if (m.open) {
        rt_log("bink failed %s movie already open", guest);
        return 0;
    }
    if (!s_test && !rt_gfx_movie_ready()) return fail_open(guest, "no d3d9 device", 0);
    char host[1024];
    int exists = 0, ov = 0;
    if (rt_vfs_resolve(guest, host, sizeof host, &exists, &ov, NULL, 0) || !exists)
        return fail_open(guest, "file not found", 0);
    snprintf(m.path, sizeof m.path, "%s", guest);

    uint8_t hdr[BIK_HDR_SIZE];
    FILE *f = fopen(host, "rb");
    size_t got = f ? fread(hdr, 1, sizeof hdr, f) : 0;
    if (f) fclose(f);
    if (got != sizeof hdr || memcmp(hdr, "BIK", 3)) return fail_open(guest, "not a bink file", 0);
    memcpy(&m.frames, hdr + BIK_HDR_FRAMES, 4);
    memcpy(&m.fps_num, hdr + BIK_HDR_FPS_NUM, 4);
    memcpy(&m.fps_den, hdr + BIK_HDR_FPS_DEN, 4);
    if (!m.frames || !m.fps_num || !m.fps_den) return fail_open(guest, "bad header", 0);

    av_log_set_level(AV_LOG_ERROR);
    int r = avformat_open_input(&m.fmt, host, NULL, NULL);
    if (r < 0) return fail_open(guest, "avformat_open_input", r);
    r = avformat_find_stream_info(m.fmt, NULL);
    if (r < 0) return fail_open(guest, "avformat_find_stream_info", r);
    m.vi = av_find_best_stream(m.fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (m.vi < 0) return fail_open(guest, "no video stream", m.vi);
    m.ai = av_find_best_stream(m.fmt, AVMEDIA_TYPE_AUDIO, -1, -1, NULL, 0);
    r = open_decoder(&m.vctx, m.fmt->streams[m.vi]);
    if (r < 0) return fail_open(guest, "video decoder", r);
    if (m.vctx->pix_fmt != AV_PIX_FMT_YUV420P && m.vctx->pix_fmt != AV_PIX_FMT_YUVA420P)
        return fail_open(guest, "unexpected pixel format", 0);
    m.w = m.vctx->width;
    m.h = m.vctx->height;
    m.pkt = av_packet_alloc();
    m.vf = av_frame_alloc();
    m.nf = av_frame_alloc();
    m.af = av_frame_alloc();
    if (!m.pkt || !m.vf || !m.nf || !m.af) return fail_open(guest, "out of memory", 0);

    int rate = 0, ch = 0;
    int ar = m.ai >= 0 ? open_decoder(&m.actx, m.fmt->streams[m.ai]) : 0;
    if (m.ai >= 0 && ar == 0) {
        rate = m.actx->sample_rate;
        ch = m.actx->ch_layout.nb_channels;
    } else {
        if (m.ai >= 0) {
            char e[AV_ERROR_MAX_STRING_SIZE];
            av_strerror(ar, e, sizeof e);
            rt_log("bink failed %s audio decoder: %s", guest, e);
        }
        avcodec_free_context(&m.actx);
        m.ai = -1;
    }
    s_cap_rate = rate;
    s_cap_ch = ch;
    m.bytes_per_s = (uint32_t)rate * (uint32_t)ch * 2u;
    if (m.actx && !s_test && rt_audio_init() == 0) {
        SDL_AudioSpec spec = {SDL_AUDIO_S16LE, ch, rate};
        m.stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, NULL, NULL);
        if (m.stream) SDL_ResumeAudioStreamDevice(m.stream);
        else rt_log("bink audio: %s", SDL_GetError());
        char name[300];
        m.wav = rt_wav_open(base_name(guest, name, sizeof name), rate, ch);
    }

    m.yuv = malloc((size_t)m.w * m.h * 3 / 2 + (size_t)m.w + (size_t)m.h + 4);
    m.rgb = malloc((size_t)m.w * m.h * 4);
    if (!m.yuv || !m.rgb) return fail_open(guest, "out of memory", 0);
    m.hbink = rt_heap_alloc(HB_SIZE, 1);
    if (!m.hbink) return fail_open(guest, "guest heap exhausted", 0);
    MEM32(m.hbink + HB_WIDTH) = (uint32_t)m.w;
    MEM32(m.hbink + HB_HEIGHT) = (uint32_t)m.h;
    MEM32(m.hbink + HB_FRAMES) = m.frames;
    MEM32(m.hbink + HB_FRAMENUM) = 1;
    m.open = 1;
    m.index = ++s_count;
    if (m.index <= MAX_MOVIES) s_shown[m.index - 1] = 0;
    rt_log("bink open %s %dx%d %.3f fps frames %u audio %d Hz %d ch", guest, m.w, m.h, (double)m.fps_num / m.fps_den,
           m.frames, rate, ch);
    return m.hbink;
}

static uint32_t BufferOpen(const uint32_t *a)
{
    uint32_t w = a[1], h = a[2];
    if (!m.open || !w || !h || w > 16384 || h > 16384) return 0;
    uint32_t b = rt_heap_alloc(BB_SIZE, 1);
    uint32_t px = b ? rt_heap_alloc(w * h * 4, 1) : 0;
    if (!px) {
        if (b) rt_heap_free(b);
        return 0;
    }
    MEM32(b + BB_WIDTH) = w;
    MEM32(b + BB_HEIGHT) = h;
    MEM32(b + BB_SURFACE) = SURFACE_XRGB;
    MEM32(b + BB_BUFFER) = px;
    MEM32(b + BB_PITCH) = w * 4;
    m.buf = b;
    m.buf_va_pixels = px;
    m.scale_w = m.scale_h = 0;
    return b;
}

static uint32_t BufferSetScale(const uint32_t *a)
{
    m.scale_w = a[1];
    m.scale_h = a[2];
    return 1;
}

static int s_sim_queued = -1;

static int64_t audio_queued(void)
{
    if (s_test && s_sim_queued >= 0) return s_sim_queued;
    return m.stream ? SDL_GetAudioStreamQueued(m.stream) : -1;
}

static double av_drift_s(uint32_t shown, uint64_t put, int64_t queued, uint32_t bps, uint32_t num, uint32_t den)
{
    if (!bps || queued < 0) return 0;
    double played = (double)put - (double)queued;
    return (shown ? shown - 1 : 0) * (double)den / num - played / bps;
}

static uint32_t Wait(const uint32_t *a)
{
    (void)a;
    if (!m.open) return 0;
    uint64_t now = rt_now_ns();
    if (!m.started) {
        m.started = 1;
        m.t0 = now;
    }
    int64_t q = audio_queued();
    if (q > 0 && m.bytes_per_s) {
        uint64_t played = m.put_bytes > (uint64_t)q ? m.put_bytes - (uint64_t)q : 0;
        uint64_t played_ns = played * 1000000000ull / m.bytes_per_s;
        m.t0 = now > played_ns ? now - played_ns : 0;
    }
    uint32_t n = MEM32(m.hbink + HB_FRAMENUM);
    uint64_t due = m.t0 + (uint64_t)(n - 1) * 1000000000ull * m.fps_den / m.fps_num;
    if (now >= due) return 0;
    rt_wait(0, NULL, 0, 1);
    return 1;
}

static void to_pcm(const AVFrame *f)
{
    int ch = f->ch_layout.nb_channels, n = f->nb_samples;
    size_t need = (size_t)ch * n;
    if (need > m.pcm_cap) {
        int16_t *p = realloc(m.pcm, need * sizeof *p);
        if (!p) return;
        m.pcm = p;
        m.pcm_cap = need;
    }
    int planar = f->format == AV_SAMPLE_FMT_FLTP;
    if (!planar && f->format != AV_SAMPLE_FMT_FLT) return;
    for (int c = 0; c < ch; c++) {
        const float *src = (const float *)(planar ? f->extended_data[c] : f->extended_data[0]);
        for (int i = 0; i < n; i++) {
            float x = planar ? src[i] : src[(size_t)i * ch + c];
            x = x > 1.0f ? 1.0f : x < -1.0f ? -1.0f : x;
            m.pcm[(size_t)i * ch + c] = (int16_t)lrintf(x * 32767.0f);
        }
    }
    uint32_t bytes = (uint32_t)(need * sizeof(int16_t));
    if (s_test) {
        if (s_cap_n + bytes > s_cap_cap) {
            uint8_t *p = realloc(s_cap, (s_cap_n + bytes) * 2);
            if (!p) return;
            s_cap = p;
            s_cap_cap = (s_cap_n + bytes) * 2;
        }
        memcpy(s_cap + s_cap_n, m.pcm, bytes);
        s_cap_n += bytes;
    }
    if (m.stream) SDL_PutAudioStreamData(m.stream, m.pcm, (int)bytes);
    if (m.wav) rt_wav_write(m.wav, m.pcm, bytes);
    m.put_bytes += bytes;
}

static void audio_packet(const AVPacket *p)
{
    if (avcodec_send_packet(m.actx, p) < 0) return;
    while (avcodec_receive_frame(m.actx, m.af) == 0) {
        to_pcm(m.af);
        av_frame_unref(m.af);
    }
}

static int next_video(void)
{
    for (;;) {
        int r = avcodec_receive_frame(m.vctx, m.nf);
        if (r == 0) return 1;
        if (r != AVERROR(EAGAIN) || m.eof) return 0;
        r = av_read_frame(m.fmt, m.pkt);
        if (r < 0) {
            m.eof = 1;
            avcodec_send_packet(m.vctx, NULL);
            continue;
        }
        if (m.pkt->stream_index == m.vi) {
            int sr = avcodec_send_packet(m.vctx, m.pkt);
            av_packet_unref(m.pkt);
            if (sr < 0) return 0;
            continue;
        } else if (m.pkt->stream_index == m.ai && m.actx) audio_packet(m.pkt);
        av_packet_unref(m.pkt);
    }
}

static uint32_t DoFrame(const uint32_t *a)
{
    (void)a;
    if (!m.open) return 0;
    if (next_video()) {
        av_frame_unref(m.vf);
        av_frame_move_ref(m.vf, m.nf);
    }
    return 0;
}

static uint32_t Nop1(const uint32_t *a)
{
    (void)a;
    return 1;
}

static void convert_frame(void)
{
    int w = m.w, h = m.h, cw = (w + 1) / 2, chh = (h + 1) / 2;
    uint8_t *y = m.yuv, *u = y + (size_t)w * h, *v = u + (size_t)cw * chh;
    for (int r = 0; r < h; r++) memcpy(y + (size_t)r * w, m.vf->data[0] + (ptrdiff_t)r * m.vf->linesize[0], (size_t)w);
    for (int r = 0; r < chh; r++) {
        memcpy(u + (size_t)r * cw, m.vf->data[1] + (ptrdiff_t)r * m.vf->linesize[1], (size_t)cw);
        memcpy(v + (size_t)r * cw, m.vf->data[2] + (ptrdiff_t)r * m.vf->linesize[2], (size_t)cw);
    }
    SDL_Colorspace cs = m.vf->color_range == AVCOL_RANGE_JPEG ? SDL_COLORSPACE_BT601_FULL : SDL_COLORSPACE_BT601_LIMITED;
    SDL_ConvertPixelsAndColorspace(w, h, SDL_PIXELFORMAT_IYUV, cs, 0, m.yuv, w, SDL_PIXELFORMAT_XRGB8888,
                                   SDL_COLORSPACE_SRGB, 0, m.rgb, w * 4);
}

static uint32_t CopyToBuffer(const uint32_t *a)
{
    uint32_t dest = a[1], pitch = a[2], height = a[3], x = a[4], y = a[5], surface = a[6];
    if (!m.open || !m.vf->data[0] || surface != SURFACE_XRGB) return 1;
    if (!rt_span_ok(dest, (uint64_t)pitch * height)) return 1;
    convert_frame();
    uint32_t bw = pitch / 4;
    if (x >= bw || y >= height) return 0;
    uint32_t cw = (uint32_t)m.w < bw - x ? (uint32_t)m.w : bw - x;
    uint32_t rows = (uint32_t)m.h < height - y ? (uint32_t)m.h : height - y;
    for (uint32_t r = 0; r < rows; r++)
        memcpy(rt_host(dest + (y + r) * pitch + x * 4), m.rgb + (size_t)r * m.w * 4, (size_t)cw * 4);
    return 0;
}

static uint32_t GetRects(const uint32_t *a)
{
    (void)a;
    if (!m.open) return 0;
    MEM32(m.hbink + HB_RECTS) = 0;
    MEM32(m.hbink + HB_RECTS + 4) = 0;
    MEM32(m.hbink + HB_RECTS + 8) = (uint32_t)m.w;
    MEM32(m.hbink + HB_RECTS + 12) = (uint32_t)m.h;
    return 1;
}

static uint32_t BufferBlit(const uint32_t *a)
{
    (void)a;
    if (!m.open || !m.buf) return 0;
    uint32_t w = MEM32(m.buf + BB_WIDTH), h = MEM32(m.buf + BB_HEIGHT), pitch = MEM32(m.buf + BB_PITCH);
    uint32_t dw = m.scale_w ? m.scale_w : w, dh = m.scale_h ? m.scale_h : h;
    if (!s_test) rt_gfx_movie_blit(rt_host(m.buf_va_pixels), w, h, pitch, dw, dh);
    m.shown++;
    if (m.index >= 1 && m.index <= MAX_MOVIES) s_shown[m.index - 1] = m.shown;
    return 0;
}

static uint32_t NextFrame(const uint32_t *a)
{
    (void)a;
    if (m.open) MEM32(m.hbink + HB_FRAMENUM) += 1;
    return 0;
}

static uint32_t BufferClose(const uint32_t *a)
{
    (void)a;
    if (!m.buf) return 0;
    if (!s_test) rt_gfx_movie_end();
    rt_heap_free(m.buf_va_pixels);
    rt_heap_free(m.buf);
    m.buf = m.buf_va_pixels = 0;
    return 0;
}

static uint32_t Close(const uint32_t *a)
{
    (void)a;
    if (!m.open) return 0;
    double drift = m.stream ? av_drift_s(m.shown, m.put_bytes, audio_queued(), m.bytes_per_s, m.fps_num, m.fps_den) : 0;
    rt_log("bink close %s shown %u/%u skipped %d av drift %.0f ms", m.path, m.shown, m.frames, m.shown < m.frames,
           drift * 1000.0);
    if (m.buf) BufferClose(NULL);
    rt_heap_free(m.hbink);
    movie_free();
    return 0;
}

static int s_tp, s_tf;

static void chk(int ok, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("%s ", ok ? "PASS" : "FAIL");
    vprintf(fmt, ap);
    printf("\n");
    fflush(stdout);
    va_end(ap);
    if (ok) s_tp++;
    else s_tf++;
}

static int write_wav(const char *path, uint32_t rate, uint32_t ch, const uint8_t *pcm, size_t n)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return 0;
    uint32_t data = (uint32_t)n, riff = 36u + data, fmt = 16, br = rate * ch * 2;
    uint16_t tag = 1, c = (uint16_t)ch, al = (uint16_t)(ch * 2), bits = 16;
    int ok = write(fd, "RIFF", 4) == 4 && write(fd, &riff, 4) == 4 && write(fd, "WAVEfmt ", 8) == 8 && write(fd, &fmt, 4) == 4 &&
             write(fd, &tag, 2) == 2 && write(fd, &c, 2) == 2 && write(fd, &rate, 4) == 4 && write(fd, &br, 4) == 4 &&
             write(fd, &al, 2) == 2 && write(fd, &bits, 2) == 2 && write(fd, "data", 4) == 4 && write(fd, &data, 4) == 4 &&
             write(fd, pcm, n) == (ssize_t)n;
    close(fd);
    return ok;
}

static uint32_t guest_movie(const char *name)
{
    char g[260];
    snprintf(g, sizeof g, "C:\\VTMB\\Vampire\\media\\%s.bik", name);
    return rt_put_string(g);
}

static void wait_case(void)
{
    uint32_t a[7] = {guest_movie("troika")};
    uint32_t h = BinkOpen(a);
    if (!h) {
        chk(0, "troika opens for the Wait case");
        return;
    }
    a[0] = h;
    uint32_t first = Wait(a);
    NextFrame(a);
    uint32_t early = Wait(a);
    uint64_t t0 = rt_now_ns();
    int due = 0;
    while (rt_now_ns() - t0 < 300000000ull && !(due = !Wait(a))) {
    }
    uint64_t waited = rt_now_ns() - t0;
    chk(first == 0 && early != 0 && due && waited < 100000000ull && MEM32(h + HB_FRAMENUM) == 2,
        "Wait is 0 for frame 1, nonzero before frame 2 is due and 0 once it is (%.1f ms at 15 fps), NextFrame made FrameNum %u",
        waited / 1e6, MEM32(h + HB_FRAMENUM));
    m.put_bytes = 4ull * m.bytes_per_s;
    m.t0 = rt_now_ns() - 5000000000ull;
    s_sim_queued = (int)(3ull * m.bytes_per_s);
    MEM32(h + HB_FRAMENUM) = 16;
    uint32_t at_one_s = Wait(a);
    MEM32(h + HB_FRAMENUM) = 17;
    uint32_t stalled = Wait(a);
    s_sim_queued = 0;
    m.t0 = rt_now_ns() - 5000000000ull;
    uint32_t drained = Wait(a);
    s_sim_queued = -1;
    chk(at_one_s == 0 && stalled != 0 && drained == 0,
        "Wait follows the audio clock while audio is queued: with 1.0 s played and a 5 s wall stall frame 16 is due, frame 17 is not, and an empty queue falls back to wall time");
    double bias = av_drift_s(31, 2ull * m.bytes_per_s, 0, m.bytes_per_s, 15, 1);
    chk(fabs(bias) < 1e-9 && fabs(av_drift_s(61, 2ull * m.bytes_per_s, 0, m.bytes_per_s, 15, 1) - 2.0) < 1e-9,
        "av drift counts shown - 1 frames: 31 frames at 15 fps against 2.0 s played is %.0f ms", bias * 1000.0);
    Close(a);
}

static void movie_case(const char *name, int w, int h, uint32_t frames, double fps, int idx)
{
    uint32_t a[7] = {guest_movie(name)};
    uint32_t hb = BinkOpen(a);
    chk(hb && (int)MEM32(hb + HB_WIDTH) == w && (int)MEM32(hb + HB_HEIGHT) == h && MEM32(hb + HB_FRAMES) == frames &&
            MEM32(hb + HB_FRAMENUM) == 1,
        "%s.bik opens as %dx%d with %u frames and FrameNum 1 (got %u %u %u %u)", name, w, h, frames, hb ? MEM32(hb) : 0,
        hb ? MEM32(hb + 4) : 0, hb ? MEM32(hb + 8) : 0, hb ? MEM32(hb + 12) : 0);
    if (!hb) return;
    uint32_t bw = (uint32_t)w + 16, bh = (uint32_t)h + 8, pitch = bw * 4;
    a[0] = 0;
    a[1] = (uint32_t)bw;
    a[2] = (uint32_t)bh;
    a[3] = 0x44000000;
    uint32_t buf = BufferOpen(a);
    chk(buf && MEM32(buf + BB_HEIGHT) == bh && MEM32(buf + BB_PITCH) == pitch && MEM32(buf + BB_BUFFER),
        "%s.bik: BinkBufferOpen makes a %ux%u XRGB buffer with pitch %u", name, bw, bh, pitch);
    if (!buf) {
        a[0] = hb;
        Close(a);
        return;
    }
    uint32_t px = MEM32(buf + BB_BUFFER), surface = MEM32(buf + BB_SURFACE);
    uint8_t *host = rt_host(px);
    uint8_t *row0 = malloc((size_t)w * 4);
    int copy_ok = 1, bars_ok = 1, inner = 0, shifted_ok = 1, rect_ok = 1, bad_surface = 0;
    uint32_t mid = frames / 2;
    s_cap_n = 0;
    for (uint32_t n = 1; n <= frames; n++) {
        a[0] = hb;
        DoFrame(a);
        if (n == mid) {
            uint32_t c[7] = {hb, px, pitch, bh, 0, 0, surface};
            copy_ok &= CopyToBuffer(c) == 0;
            for (int y = 0; y < h; y++)
                for (int x = 0; x < w; x++)
                    if (*(uint32_t *)(host + (size_t)y * pitch + (size_t)x * 4)) inner = 1;
            memcpy(row0, host + (size_t)(h / 2) * pitch, (size_t)w * 4);
            memset(host, 0, (size_t)pitch * bh);
            c[4] = 8;
            c[5] = 4;
            copy_ok &= CopyToBuffer(c) == 0;
            for (uint32_t y = 0; y < 4; y++)
                for (uint32_t x = 0; x < pitch; x++) bars_ok &= host[(size_t)y * pitch + x] == 0;
            for (uint32_t y = 4; y < bh; y++)
                for (uint32_t x = 0; x < 32; x++) bars_ok &= host[(size_t)y * pitch + x] == 0;
            shifted_ok = !memcmp(host + (size_t)(h / 2 + 4) * pitch + 32, row0, (size_t)w * 4);
            c[6] = surface ^ 1;
            bad_surface = CopyToBuffer(c) != 0;
            uint32_t g[2] = {hb, surface};
            rect_ok = GetRects(g) == 1 && MEM32(hb + HB_RECTS) == 0 && MEM32(hb + HB_RECTS + 4) == 0 &&
                      MEM32(hb + HB_RECTS + 8) == (uint32_t)w && MEM32(hb + HB_RECTS + 12) == (uint32_t)h;
        }
        uint32_t b[3] = {buf, hb + HB_RECTS, 1};
        BufferBlit(b);
        if (n != frames) NextFrame(a);
    }
    free(row0);
    chk(copy_ok && inner, "%s.bik: CopyToBuffer writes a decoded frame (non-black) at the given pitch", name);
    chk(bars_ok && shifted_ok, "%s.bik: CopyToBuffer at x 8, y 4 shifts the picture and leaves the bars black", name);
    chk(bad_surface, "%s.bik: CopyToBuffer returns nonzero for a surface type the buffer does not have", name);
    chk(rect_ok, "%s.bik: GetRects writes one full-frame rect {0, 0, %d, %d} at +0x34", name, w, h);
    chk(MEM32(hb + HB_FRAMENUM) == frames && m.shown == frames,
        "%s.bik: %u frames decoded and shown, FrameNum ends at %u", name, m.shown, MEM32(hb + HB_FRAMENUM));
    a[0] = buf;
    BufferClose(a);
    a[0] = hb;
    Close(a);
    chk(!rt_heap_owns(hb) && !rt_heap_owns(buf) && !rt_heap_owns(px), "%s.bik: Close and BufferClose free every guest block", name);
    char wav[1100];
    snprintf(wav, sizeof wav, "%s/audio/bink-%d.wav", rt_vfs_overlay(), idx + 1);
    rt_vfs_mkparents(wav);
    chk(s_cap_n > 0 && s_cap_rate > 0 && fabs((double)s_cap_n / (2.0 * s_cap_ch * s_cap_rate) - frames / fps) < 0.15,
        "%s.bik: the audio track decodes to %.2f s at %d Hz, %d ch (video %.2f s)", name,
        s_cap_rate && s_cap_ch ? (double)s_cap_n / (2.0 * s_cap_ch * s_cap_rate) : 0.0, s_cap_rate, s_cap_ch, frames / fps);
    if (write_wav(wav, (uint32_t)s_cap_rate, (uint32_t)s_cap_ch, s_cap, s_cap_n)) printf("decode Vampire/media/%s.bik %s\n", name, wav);
    fflush(stdout);
}

int rt_bink_selftest(int *pass)
{
    if (!rt_self()) {
        rt_reset_cpu();
        rt_thread_primary(RT_TIB);
    }
    s_tp = s_tf = 0;
    s_test = 1;
    s_novid = 0;
    movie_case("activision", 640, 480, 312, 30.0, 0);
    movie_case("whitewolf", 720, 480, 661, 30000.0 / 1001.0, 1);
    movie_case("nvidiabloodlogo", 640, 480, 420, 30000.0 / 1001.0, 2);
    movie_case("troika", 800, 400, 221, 15.0, 3);
    wait_case();
    uint32_t a[7] = {guest_movie("nothere")};
    chk(BinkOpen(a) == 0 && !m.open, "a missing .bik gives 0");
    s_novid = 1;
    a[0] = guest_movie("activision");
    chk(BinkOpen(a) == 0 && !m.open, "--novid gives 0 for an existing .bik");
    s_novid = 0;
    s_test = 0;
    s_count = 0;
    memset(s_shown, 0, sizeof s_shown);
    free(s_cap);
    s_cap = NULL;
    s_cap_n = s_cap_cap = 0;
    *pass = s_tp;
    return s_tf;
}

const rt_shim rt_bink_shims[] = {
    {"binkw32.dll", "_BinkBufferBlit@12", 3, BufferBlit},
    {"binkw32.dll", "_BinkBufferClose@4", 1, BufferClose},
    {"binkw32.dll", "_BinkBufferLock@4", 1, Nop1},
    {"binkw32.dll", "_BinkBufferOpen@16", 4, BufferOpen},
    {"binkw32.dll", "_BinkBufferSetScale@12", 3, BufferSetScale},
    {"binkw32.dll", "_BinkBufferUnlock@4", 1, Nop1},
    {"binkw32.dll", "_BinkClose@4", 1, Close},
    {"binkw32.dll", "_BinkCopyToBuffer@28", 7, CopyToBuffer},
    {"binkw32.dll", "_BinkDoFrame@4", 1, DoFrame},
    {"binkw32.dll", "_BinkGetRects@8", 2, GetRects},
    {"binkw32.dll", "_BinkNextFrame@4", 1, NextFrame},
    {"binkw32.dll", "_BinkOpen@8", 2, BinkOpen},
    {"binkw32.dll", "_BinkSetSoundSystem@8", 2, SetSoundSystem},
    {"binkw32.dll", "_BinkWait@4", 1, Wait},
};

const uint32_t rt_nbink_shims = sizeof rt_bink_shims / sizeof rt_bink_shims[0];
