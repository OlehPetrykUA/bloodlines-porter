#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "recomp_types.h"
#include "rt.h"

#define DR_MP3_IMPLEMENTATION
#define DR_MP3_NO_STDIO
#define DRMP3_DATA_CHUNK_SIZE 16384
#include "../third_party/dr_mp3/dr_mp3.h"

#define PROVIDER 0x4D503301u
#define BLOCK_STREAMS 256u
#define MAX_BLOCKS 4096u
#define MAX_ENTRIES 32u
#define CHUNK 4096u
#define HEAD 16u
#define CONTINUE 0xFFFFFFFFu
#define MAX_PCM (16u << 20)
#define MAX_MUSIC (256ull << 20)
#define MAX_CALLS 400000u
#define MAX_SEEKS 64u

enum { A_OUT_RATE = 1, A_OUT_WIDTH, A_OUT_CHANNELS, A_IN_RATE, A_IN_CHANNELS, A_IN_WIDTH, A_MIN_BLOCK };

typedef struct {
    drmp3 mp3;
    uint32_t user, fetch, scratch, id;
    uint64_t pos, src_pos;
    uint32_t hn, pend_off;
    int pending, logged;
    uint8_t head[HEAD];
} stream;

static stream **s_blocks[MAX_BLOCKS];
static uint32_t s_free_from;
static uint32_t s_opened, s_closed, s_failed, s_errors;
static uint64_t s_frames;

static const struct {
    const char *name;
    uint32_t token;
} k_attrs[] = {
    {"Output sample rate", A_OUT_RATE},
    {"Output sample width", A_OUT_WIDTH},
    {"Output channels", A_OUT_CHANNELS},
    {"Input sample rate", A_IN_RATE},
    {"Input channels", A_IN_CHANNELS},
    {"Input sample width", A_IN_WIDTH},
    {"Minimum input block size", A_MIN_BLOCK},
};

static size_t fetch_src(stream *st, uint8_t *dst, size_t want)
{
    uint32_t n = want < CHUNK ? (uint32_t)want : CHUNK;
    uint32_t args[4] = {st->user, st->scratch, n, st->pending ? st->pend_off : CONTINUE};
    uint32_t got = rt_guest_call(st->fetch, 4, args);
    st->pending = 0;
    if (got > n) got = n;
    memcpy(dst, rt_host(st->scratch), got);
    for (uint32_t i = 0; i < got; i++)
        if (st->src_pos + i == st->hn && st->hn < HEAD) st->head[st->hn++] = dst[i];
    st->src_pos += got;
    return got;
}

static size_t on_read(void *user, void *out, size_t n)
{
    stream *st = user;
    uint8_t *p = out;
    size_t done = 0;
    while (done < n) {
        size_t got;
        if (st->pos < st->src_pos) {
            got = (size_t)(st->src_pos - st->pos);
            if (got > n - done) got = n - done;
            memcpy(p + done, st->head + st->pos, got);
        } else {
            got = fetch_src(st, p + done, n - done);
            if (!got) break;
        }
        st->pos += got;
        done += got;
    }
    return done;
}

static drmp3_bool32 on_seek(void *user, int offset, drmp3_seek_origin origin)
{
    stream *st = user;
    int64_t target;
    if (origin == DRMP3_SEEK_SET) target = offset;
    else if (origin == DRMP3_SEEK_CUR) target = (int64_t)st->pos + offset;
    else return DRMP3_FALSE;
    if (target < 0) return DRMP3_FALSE;
    if ((uint64_t)target == st->pos) return DRMP3_TRUE;
    if ((uint64_t)target > st->pos) {
        uint8_t tmp[1024];
        while (st->pos < (uint64_t)target) {
            size_t want = (size_t)((uint64_t)target - st->pos);
            if (want > sizeof tmp) want = sizeof tmp;
            if (on_read(st, tmp, want) < want) return DRMP3_FALSE;
        }
        return DRMP3_TRUE;
    }
    if (st->src_pos <= st->hn) {
        st->pos = (uint64_t)target;
        return DRMP3_TRUE;
    }
    st->pending = 1;
    st->pend_off = (uint32_t)target;
    st->src_pos = st->pos = (uint64_t)target;
    return DRMP3_TRUE;
}

static stream **slot(uint32_t i, int make)
{
    uint32_t b = i / BLOCK_STREAMS;
    if (b >= MAX_BLOCKS) return NULL;
    if (!s_blocks[b] && make) s_blocks[b] = calloc(BLOCK_STREAMS, sizeof(stream *));
    return s_blocks[b] ? &s_blocks[b][i % BLOCK_STREAMS] : NULL;
}

static stream *get(uint32_t h)
{
    stream **p = h >= 1 ? slot(h - 1, 0) : NULL;
    return p ? *p : NULL;
}

static int name_is(uint32_t va, const char *s)
{
    return va && !rt_stricmp(rt_gstr(va), s);
}

static uint32_t RIB_find_files_provider(const uint32_t *a)
{
    if (name_is(a[0], "ASI codec") && name_is(a[1], "Output file types") && name_is(a[2], ".RAW") &&
        name_is(a[3], "Input file types") && name_is(a[4], ".MP3"))
        return PROVIDER;
    return 0;
}

static uint32_t ASI_stream_open(const uint32_t *a)
{
    if (!a[1]) {
        s_failed++;
        return 0;
    }
    uint32_t h = s_free_from;
    stream **sl;
    while ((sl = slot(h, 1)) && *sl) h++;
    stream *st = sl ? calloc(1, sizeof *st) : NULL;
    if (st) st->scratch = rt_heap_alloc(CHUNK, 0);
    if (!st || !st->scratch) {
        if (st) free(st);
        s_failed++;
        return 0;
    }
    st->user = a[0];
    st->fetch = a[1];
    *sl = st;
    s_free_from = h + 1;
    if (!drmp3_init(&st->mp3, on_read, on_seek, NULL, NULL, st, NULL)) {
        *sl = NULL;
        if (h < s_free_from) s_free_from = h;
        rt_heap_free(st->scratch);
        free(st);
        s_failed++;
        return 0;
    }
    st->id = ++s_opened;
    return h + 1;
}

static uint32_t ASI_stream_close(const uint32_t *a)
{
    stream *st = get(a[0]);
    if (!st) {
        s_errors++;
        return 0;
    }
    drmp3_uninit(&st->mp3);
    rt_heap_free(st->scratch);
    *slot(a[0] - 1, 0) = NULL;
    if (a[0] - 1 < s_free_from) s_free_from = a[0] - 1;
    free(st);
    s_closed++;
    return 0;
}

static uint32_t ASI_stream_process(const uint32_t *a)
{
    stream *st = get(a[0]);
    if (!st || !rt_span_ok(a[1], a[2])) {
        s_errors++;
        return 0;
    }
    uint32_t frame = 2u * st->mp3.channels;
    uint64_t want = a[2] / frame;
    if (!want) return 0;
    uint64_t n = drmp3_read_pcm_frames_s16(&st->mp3, want, rt_host(a[1]));
    if (n && !st->logged) {
        st->logged = 1;
        rt_log("mp3 open #%u rate %u ch %u", st->id, st->mp3.sampleRate, st->mp3.channels);
    }
    s_frames += n;
    return (uint32_t)(n * frame);
}

static uint32_t ASI_stream_attribute(const uint32_t *a)
{
    stream *st = get(a[0]);
    if (!st) {
        s_errors++;
        return 0;
    }
    switch (a[1]) {
    case A_OUT_RATE:
    case A_IN_RATE: return st->mp3.sampleRate;
    case A_OUT_CHANNELS:
    case A_IN_CHANNELS: return st->mp3.channels;
    case A_OUT_WIDTH:
    case A_IN_WIDTH: return 16;
    case A_MIN_BLOCK: return 0;
    default:
        rt_log("mp3 attribute token %u unknown", a[1]);
        return 0;
    }
}

static uint32_t ASI_stream_set_preference(const uint32_t *a)
{
    (void)a;
    return 0;
}

static const rt_shim k_asi[] = {
    {"mss32.dll", "ASI_stream_attribute", 2, ASI_stream_attribute, RT_SHIM_REAL},
    {"mss32.dll", "ASI_stream_open", 3, ASI_stream_open, RT_SHIM_REAL},
    {"mss32.dll", "ASI_stream_close", 1, ASI_stream_close, RT_SHIM_REAL},
    {"mss32.dll", "ASI_stream_process", 3, ASI_stream_process, RT_SHIM_REAL},
    {"mss32.dll", "ASI_stream_set_preference", 3, ASI_stream_set_preference, RT_SHIM_REAL},
};

static uint32_t RIB_request_interface(const uint32_t *a)
{
    uint32_t count = a[2], e = a[3];
    if (a[0] != PROVIDER || !name_is(a[1], "ASI stream") || count > MAX_ENTRIES || !rt_span_ok(e, 16ull * count)) return 1;
    for (uint32_t i = 0; i < count; i++, e += 16) {
        uint32_t type = MEM32(e), out = MEM32(e + 8);
        const char *name = MEM32(e + 4) ? rt_gstr(MEM32(e + 4)) : "";
        if (!rt_span_ok(out, 4)) return 1;
        uint32_t v = 0;
        if (type == 0) {
            for (uint32_t k = 0; k < sizeof k_asi / sizeof k_asi[0]; k++)
                if (!strcmp(name, k_asi[k].name)) v = rt_dynamic_shim("mss32.dll", name, &k_asi[k]);
        } else if (type == 1) {
            for (uint32_t k = 0; k < sizeof k_attrs / sizeof k_attrs[0]; k++)
                if (!strcmp(name, k_attrs[k].name)) v = k_attrs[k].token;
        }
        if (!v) {
            rt_log("mp3 interface entry type %u name %s unknown", type, name);
            return 1;
        }
        MEM32(out) = v;
    }
    return 0;
}

static uint32_t RIB_free_provider_library(const uint32_t *a)
{
    (void)a;
    return 0;
}

void rt_mp3_report(void)
{
    rt_log("mp3 decoders opened %u closed %u failed %u frames %llu errors %u", s_opened, s_closed, s_failed,
        (unsigned long long)s_frames, s_errors);
}

const rt_shim rt_mss32_asi_shims[] = {
    {"mss32.dll", "_RIB_find_files_provider@20", 5, RIB_find_files_provider, RT_SHIM_REAL},
    {"mss32.dll", "RIB_request_interface", 4, RIB_request_interface, RT_SHIM_REAL},
    {"mss32.dll", "RIB_free_provider_library", 1, RIB_free_provider_library, RT_SHIM_REAL},
};

const uint32_t rt_nmss32_asi_shims = sizeof rt_mss32_asi_shims / sizeof rt_mss32_asi_shims[0];

typedef struct {
    uint8_t *d;
    size_t len, pos;
    uint32_t max, calls, seeks, last_seek;
} tctx;

typedef struct {
    uint32_t attribute, open, close, process, preference, tok[7];
} iface;

typedef struct {
    uint8_t *d;
    size_t n, cap;
} vec;

static tctx s_t[4];
static int s_tp, s_tf;
static uint32_t s_nest_open, s_nest_fetch, s_nest_got;
static uint32_t call_va(uint32_t va, uint32_t a0, uint32_t a1, uint32_t a2);

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

static uint32_t test_fetch(const uint32_t *a)
{
    tctx *t = &s_t[a[0] & 3];
    if (s_nest_open && (a[0] & 3) == 0) {
        uint32_t open = s_nest_open;
        s_nest_open = 0;
        s_nest_got = call_va(open, 1, s_nest_fetch, 0);
    }
    if (t->calls > MAX_CALLS || t->seeks > MAX_SEEKS) return 0;
    if (a[3] != CONTINUE) {
        t->pos = a[3] < t->len ? a[3] : t->len;
        t->seeks++;
        t->last_seek = a[3];
    }
    size_t n = t->len - t->pos;
    if (n > a[2]) n = a[2];
    if (n > t->max) n = t->max;
    memcpy(rt_host(a[1]), t->d + t->pos, n);
    t->pos += n;
    t->calls++;
    return (uint32_t)n;
}

static const rt_shim k_test_fetch = {"mss32.dll", "test_fetch", 4, test_fetch, RT_SHIM_REAL};

static uint32_t call_va(uint32_t va, uint32_t a0, uint32_t a1, uint32_t a2)
{
    const char *dll, *name;
    const rt_shim *s = rt_shim_at((va - RT_SHIM_BASE) / RT_SHIM_STRIDE, &dll, &name);
    uint32_t a[3] = {a0, a1, a2};
    return s && s->fn ? s->fn(a) : 0xDEADBEEFu;
}

static uint32_t row(const char *name, uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t a4)
{
    const rt_shim *s = rt_find_shim("mss32.dll", name);
    uint32_t a[5] = {a0, a1, a2, a3, a4};
    return s && s->fn ? s->fn(a) : 0xDEADBEEFu;
}

static int load(const char *rel, uint8_t **d, size_t *n)
{
    char guest[PATH_MAX] = "C:\\VTMB\\Vampire\\", host[PATH_MAX], r[PATH_MAX];
    size_t o = strlen(guest);
    snprintf(guest + o, sizeof guest - o, "%s", rel);
    for (char *c = guest; *c; c++)
        if (*c == '/') *c = '\\';
    int exists, ov;
    if (rt_vfs_resolve(guest, host, sizeof host, &exists, &ov, r, sizeof r) || !exists) return 0;
    FILE *f = fopen(host, "rb");
    if (!f) return 0;
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    *d = malloc((size_t)len + 1);
    *n = *d && len > 0 ? fread(*d, 1, (size_t)len, f) : 0;
    fclose(f);
    return *n == (size_t)len && len > 0;
}

static void vput(vec *v, const void *p, size_t n)
{
    if (v->n + n > v->cap) {
        v->cap = (v->n + n) * 2;
        v->d = realloc(v->d, v->cap);
    }
    memcpy(v->d + v->n, p, n);
    v->n += n;
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

static int pick_music(char *rel, size_t n)
{
    char **names;
    int *dirs;
    uint32_t err;
    int cnt = rt_vfs_list("C:\\VTMB\\Vampire\\sound\\music", &names, &dirs, &err);
    off_t best = 0;
    rel[0] = 0;
    for (int i = 0; i < cnt; i++) {
        size_t len = strlen(names[i]);
        if (!dirs[i] && len > 4 && !rt_stricmp(names[i] + len - 4, ".mp3") && rt_stricmp(names[i], "vampire_theme.mp3")) {
            char guest[PATH_MAX], host[PATH_MAX], r[PATH_MAX];
            int exists, ov;
            struct stat st;
            snprintf(guest, sizeof guest, "C:\\VTMB\\Vampire\\sound\\music\\%s", names[i]);
            if (!rt_vfs_resolve(guest, host, sizeof host, &exists, &ov, r, sizeof r) && !stat(host, &st) && st.st_size > best) {
                best = st.st_size;
                snprintf(rel, n, "sound/music/%s", names[i]);
            }
        }
    }
    for (int i = 0; i < cnt; i++) free(names[i]);
    free(names);
    free(dirs);
    return best > 0;
}

static int wav_start(const char *path, uint32_t rate, uint32_t ch)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return -1;
    uint32_t zero = 0, fmt = 16, br = rate * ch * 2;
    uint16_t tag = 1, c = (uint16_t)ch, al = (uint16_t)(ch * 2), bits = 16;
    int ok = write(fd, "RIFF", 4) == 4 && write(fd, &zero, 4) == 4 && write(fd, "WAVEfmt ", 8) == 8 && write(fd, &fmt, 4) == 4 &&
             write(fd, &tag, 2) == 2 && write(fd, &c, 2) == 2 && write(fd, &rate, 4) == 4 && write(fd, &br, 4) == 4 &&
             write(fd, &al, 2) == 2 && write(fd, &bits, 2) == 2 && write(fd, "data", 4) == 4 && write(fd, &zero, 4) == 4;
    if (!ok) {
        close(fd);
        return -1;
    }
    return fd;
}

static int wav_finish(int fd, uint32_t bytes)
{
    uint32_t riff = 36u + bytes;
    int ok = pwrite(fd, &riff, 4, 4) == 4 && pwrite(fd, &bytes, 4, 40) == 4;
    close(fd);
    return ok;
}

int rt_mp3_selftest(int *pass)
{
    if (!rt_self()) {
        rt_reset_cpu();
        rt_thread_primary(RT_TIB);
    }
    static const char *const k_rel[3] = {"sound/character/dlg/chinatown/yukie/line461_col_e.mp3",
                                         "sound/area/hollywood/asian_theater/gargoylemusic.mp3",
                                         "sound/character/conversations/answering machine/muddy.mp3"};
    static const uint32_t k_ch[3] = {1, 2, 1};
    uint32_t fetch = rt_dynamic_shim("mss32.dll", "test_fetch", &k_test_fetch);
    uint32_t pbuf = rt_heap_alloc(8192, 1);
    uint32_t prov = row("_RIB_find_files_provider@20", rt_put_string("ASI codec"), rt_put_string("Output file types"),
                        rt_put_string(".raw"), rt_put_string("Input file types"), rt_put_string(".mp3"));
    uint32_t bad = row("_RIB_find_files_provider@20", rt_put_string("ASI codec"), rt_put_string("Output file types"),
                       rt_put_string(".WAV"), rt_put_string("Input file types"), rt_put_string(".MP3"));
    chk(prov != 0 && bad == 0, "RIB_find_files_provider answers ASI codec .RAW/.MP3 (any case) with a provider and .WAV with 0");

    static const char *const k_names[12] = {"ASI_stream_attribute", "ASI_stream_open", "ASI_stream_close", "ASI_stream_process",
                                            "ASI_stream_set_preference", "Output sample rate", "Output sample width",
                                            "Output channels", "Input sample rate", "Input channels", "Input sample width",
                                            "Minimum input block size"};
    uint32_t ent = rt_heap_alloc(16 * 13, 1), out = rt_heap_alloc(4 * 13, 1);
    for (uint32_t i = 0; i < 12; i++) {
        MEM32(ent + 16 * i) = i < 5 ? 0 : 1;
        MEM32(ent + 16 * i + 4) = rt_put_string(k_names[i]);
        MEM32(ent + 16 * i + 8) = out + 4 * i;
    }
    MEM32(ent + 16 * 12 + 4) = rt_put_string("Bogus attribute");
    MEM32(ent + 16 * 12 + 8) = out + 4 * 12;
    MEM32(ent + 16 * 12) = 1;
    uint32_t asi = rt_put_string("ASI stream");
    uint32_t r_wrong = row("RIB_request_interface", prov + 1, asi, 12, ent, 0);
    uint32_t r_name = row("RIB_request_interface", prov, rt_put_string("ASI codec"), 12, ent, 0);
    uint32_t r_bogus = row("RIB_request_interface", prov, asi, 13, ent, 0);
    uint32_t r_ok = row("RIB_request_interface", prov, asi, 12, ent, 0);
    iface f = {MEM32(out), MEM32(out + 4), MEM32(out + 8), MEM32(out + 12), MEM32(out + 16),
               {MEM32(out + 20), MEM32(out + 24), MEM32(out + 28), MEM32(out + 32), MEM32(out + 36), MEM32(out + 40), MEM32(out + 44)}};
    int fns = f.attribute && f.open && f.close && f.process && f.preference && f.attribute != f.open && f.open != f.close &&
              f.close != f.process && f.process != f.preference;
    int toks = 1;
    for (int i = 0; i < 7; i++) toks &= f.tok[i] >= 1 && f.tok[i] <= 7;
    chk(r_ok == 0 && fns && toks && r_wrong == 1 && r_name == 1 && r_bogus == 1,
        "RIB_request_interface fills 5 functions and 7 tokens, and returns 1 for a wrong provider, a wrong name and an unknown attribute");
    chk(row("RIB_free_provider_library", prov, 0, 0, 0, 0) == 0, "RIB_free_provider_library returns 0");

    vec pcm[3] = {{0}};
    uint32_t rate[3] = {0}, chs[3] = {0};
    uint32_t opened0 = s_opened, closed0 = s_closed, failed0 = s_failed;
    for (int i = 0; i < 3; i++) {
        if (!load(k_rel[i], &s_t[i].d, &s_t[i].len)) {
            chk(0, "%s is readable through the VFS", k_rel[i]);
            continue;
        }
        s_t[i].max = CHUNK;
        s_t[i].pos = 0;
        uint32_t h = call_va(f.open, i, fetch, 0);
        chs[i] = h ? call_va(f.attribute, h, f.tok[2], 0) : 0;
        rate[i] = h ? call_va(f.attribute, h, f.tok[0], 0) : 0;
        uint32_t width = h ? call_va(f.attribute, h, f.tok[1], 0) : 0, minb = h ? call_va(f.attribute, h, f.tok[6], 0) : 1;
        chk(h && chs[i] == k_ch[i] && rate[i] == 44100 && width == 16 && minb == 0 &&
                call_va(f.attribute, h, f.tok[4], 0) == k_ch[i] && call_va(f.attribute, h, f.tok[3], 0) == 44100,
            "%s opens as %u Hz, %u channel(s), width %u, min block %u (want 44100, %u, 16, 0)", k_rel[i], rate[i], chs[i], width, minb,
            k_ch[i]);
        uint32_t got;
        while (h && pcm[i].n < MAX_PCM && (got = call_va(f.process, h, pbuf, 4096)) != 0) vput(&pcm[i], rt_host(pbuf), got);
        chk(h && pcm[i].n > 0 && pcm[i].n % (2 * k_ch[i]) == 0 && call_va(f.process, h, pbuf, 4096) == 0 &&
                call_va(f.process, h, pbuf, 4096) == 0,
            "%s decodes %zu bytes, then every further process call returns 0 at the end of the stream", k_rel[i], pcm[i].n);
        chk(s_t[i].seeks == 0, "%s decodes without an engine seek (%u seeks, %u fetches)", k_rel[i], s_t[i].seeks, s_t[i].calls);
        if (h) call_va(f.close, h, 0, 0);
        char wav[PATH_MAX];
        snprintf(wav, sizeof wav, "%s/audio/mp3-%d.wav", rt_vfs_overlay(), i + 1);
        rt_vfs_mkparents(wav);
        if (write_wav(wav, rate[i], chs[i], pcm[i].d, pcm[i].n)) printf("decode %s %s\n", k_rel[i], wav);
        fflush(stdout);
    }

    static const uint32_t k_max[3] = {1, 777, 1};
    for (int i = 0; i < 3; i++) {
        if (!s_t[i].d) continue;
        vec b = {0};
        s_t[i].max = k_max[i];
        s_t[i].pos = 0;
        s_t[i].calls = s_t[i].seeks = 0;
        uint32_t h = call_va(f.open, i, fetch, 0), got;
        while (h && b.n < MAX_PCM && (got = call_va(f.process, h, pbuf, 4096)) != 0) vput(&b, rt_host(pbuf), got);
        chk(h && b.n == pcm[i].n && b.n > 0 && !memcmp(b.d, pcm[i].d, b.n),
            "%s read %u byte(s) per fetch gives the same %zu bytes of samples (%u fetches)", k_rel[i], k_max[i], b.n, s_t[i].calls);
        if (h) call_va(f.close, h, 0, 0);
        free(b.d);
    }

    if (s_t[1].d) {
        s_t[1].max = CHUNK;
        s_t[1].pos = 0;
        s_t[1].calls = s_t[1].seeks = 0;
        uint32_t h = call_va(f.open, 1, fetch, 0);
        uint32_t want = 20000u * 4u;
        vec a = {0}, b = {0};
        uint32_t got;
        while (h && a.n < want && (got = call_va(f.process, h, pbuf, 4096)) != 0) vput(&a, rt_host(pbuf), got);
        uint32_t seeks_before = s_t[1].seeks;
        int ok = h && drmp3_seek_to_pcm_frame(&get(h)->mp3, 0);
        while (ok && b.n < a.n && (got = call_va(f.process, h, pbuf, 4096)) != 0) vput(&b, rt_host(pbuf), got);
        chk(ok && seeks_before == 0 && s_t[1].seeks == 1 && s_t[1].last_seek == 0 && a.n >= want && b.n == a.n && !memcmp(a.d, b.d, a.n),
            "seek to the start sends one fetch with offset 0 (got %u, last %u) and decodes the same %zu bytes again", s_t[1].seeks,
            s_t[1].last_seek, a.n);
        if (h) call_va(f.close, h, 0, 0);
        free(a.d);
        free(b.d);
    }

    if (s_t[1].d) {
        s_t[1].max = CHUNK;
        s_t[1].pos = 0;
        s_t[1].calls = s_t[1].seeks = 0;
        uint32_t h = call_va(f.open, 1, fetch, 0), got;
        vec a = {0};
        while (h && a.n < 20000u * 4u && (got = call_va(f.process, h, pbuf, 4096)) != 0) vput(&a, rt_host(pbuf), got);
        stream *st = h ? get(h) : NULL;
        uint8_t buf[100];
        int s1 = st && on_seek(st, 5000, DRMP3_SEEK_SET);
        int r1 = st && on_read(st, buf, 100) == 100 && !memcmp(buf, s_t[1].d + 5000, 100);
        int c1 = s1 && r1 && s_t[1].seeks == 1 && s_t[1].last_seek == 5000;
        int s2 = st && on_seek(st, -1000, DRMP3_SEEK_CUR);
        int r2 = st && on_read(st, buf, 100) == 100 && !memcmp(buf, s_t[1].d + 4100, 100);
        int c2 = s2 && r2 && s_t[1].seeks == 2 && s_t[1].last_seek == 4100 && st->pos == 4200;
        chk(c1 && c2, "a backward seek to byte 5000 and one 1000 bytes back from there send the offsets 5000 and 4100 and read those bytes (seeks %u, last %u)",
            s_t[1].seeks, s_t[1].last_seek);
        if (h) call_va(f.close, h, 0, 0);
        free(a.d);
    }

    char music[PATH_MAX];
    free(s_t[2].d);
    s_t[2].d = NULL;
    if (pick_music(music, sizeof music) && load(music, &s_t[2].d, &s_t[2].len)) {
        s_t[2].max = CHUNK;
        s_t[2].pos = 0;
        s_t[2].calls = s_t[2].seeks = 0;
        uint32_t h = call_va(f.open, 2, fetch, 0), got;
        uint32_t mch = h ? call_va(f.attribute, h, f.tok[2], 0) : 0, mrate = h ? call_va(f.attribute, h, f.tok[0], 0) : 0;
        uint64_t head = 0, total = 0;
        while (h && head < 30000ull * 2 * mch && (got = call_va(f.process, h, pbuf, 4096)) != 0) head += got;
        int sought = h && drmp3_seek_to_pcm_frame(&get(h)->mp3, 0);
        char wav[PATH_MAX];
        snprintf(wav, sizeof wav, "%s/audio/mp3-4.wav", rt_vfs_overlay());
        rt_vfs_mkparents(wav);
        int fd = h ? wav_start(wav, mrate, mch) : -1;
        while (fd >= 0 && sought && total < MAX_MUSIC && (got = call_va(f.process, h, pbuf, 4096)) != 0 &&
               write(fd, rt_host(pbuf), got) == (ssize_t)got)
            total += got;
        int ended = h && call_va(f.process, h, pbuf, 4096) == 0;
        const uint8_t *id = s_t[2].d;
        uint32_t start = s_t[2].len > 10 && !memcmp(id, "ID3", 3) ? 10u + ((id[6] & 0x7Fu) << 21 | (id[7] & 0x7Fu) << 14 | (id[8] & 0x7Fu) << 7 | (id[9] & 0x7Fu)) : 0;
        chk(sought && fd >= 0 && s_t[2].seeks == 1 && s_t[2].last_seek >= start && s_t[2].last_seek < 4096 && total > 0 && total % (2 * mch) == 0 && ended,
            "music %s decodes %llu bytes through the ASI stream after a seek to the first frame (%u seeks, offset %u, tag ends at %u)", music,
            (unsigned long long)total, s_t[2].seeks, s_t[2].last_seek, start);
        if (fd >= 0 && wav_finish(fd, (uint32_t)total)) printf("decode %s %s\n", music, wav);
        fflush(stdout);
        if (h) call_va(f.close, h, 0, 0);
    } else chk(0, "a music track other than the menu theme is readable through the VFS");

    s_t[0].calls = s_t[0].seeks = 0;
    uint32_t live = 0, extra = 0;
    uint32_t hs[400] = {0};
    for (uint32_t i = 0; i < 400; i++) {
        s_t[0].pos = 0;
        s_t[0].max = CHUNK;
        hs[i] = s_t[0].d ? call_va(f.open, 0, fetch, 0) : 0;
        if (hs[i]) live++;
        else extra++;
    }
    chk(live == 400 && extra == 0 && get(hs[399]) && get(hs[399]) != get(hs[0]),
        "400 decoders open at once with none closed, as the game never closes one (%u open, %u refused)", live, extra);
    for (uint32_t i = 0; i < 400; i++)
        if (hs[i]) call_va(f.close, hs[i], 0, 0);
    uint32_t again = s_t[0].d ? call_va(f.open, 0, fetch, 0) : 0;
    chk(again == hs[0], "a closed handle is reused first (%u, first was %u)", again, hs[0]);
    if (again) call_va(f.close, again, 0, 0);
    for (int k = 0; k < 2; k++) {
        s_t[k].pos = 0;
        s_t[k].max = CHUNK;
    }
    s_nest_got = 0;
    s_nest_fetch = fetch;
    s_nest_open = f.open;
    uint32_t outer = s_t[0].d && s_t[1].d ? call_va(f.open, 0, fetch, 0) : 0;
    s_nest_open = 0;
    chk(outer && s_nest_got && outer != s_nest_got && get(outer) && get(s_nest_got) && get(outer) != get(s_nest_got),
        "a stream opened during another open's first read gets its own handle (outer %u, inner %u)", outer, s_nest_got);
    if (outer) call_va(f.close, outer, 0, 0);
    if (s_nest_got && s_nest_got != outer) call_va(f.close, s_nest_got, 0, 0);

    uint8_t none = 0;
    s_t[3].d = &none;
    s_t[3].len = 0;
    s_t[3].max = CHUNK;
    uint32_t failed1 = s_failed;
    uint32_t empty = call_va(f.open, 3, fetch, 0);
    chk(empty == 0 && s_failed == failed1 + 1, "an empty stream gives open 0 and failed + 1");
    uint32_t err0 = s_errors;
    call_va(f.close, 1, 0, 0);
    chk(s_errors == err0 + 1, "closing a handle twice counts one error");

    uint32_t oc = s_opened - opened0, cc = s_closed - closed0;
    chk(oc == cc && oc > 0 && s_failed - failed0 == 1, "every opened decoder is closed with drmp3_uninit (opened %u closed %u failed %u)", oc, cc,
        s_failed - failed0);
    for (int i = 0; i < 3; i++) {
        free(pcm[i].d);
        free(s_t[i].d);
        s_t[i].d = NULL;
    }
    *pass = s_tp;
    return s_tf;
}
