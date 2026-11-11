#include "recomp_types.h"
#include "rt.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAXC 64
#define NCACHE 1024

typedef struct {
    char *dir;
    char **names;
    int n;
} listing;

static char s_game[PATH_MAX], s_files[PATH_MAX], s_overlay[PATH_MAX];
static char s_cwd[PATH_MAX] = "C:\\VTMB";
static listing s_cache[NCACHE];
static int s_ncache;
static pthread_mutex_t s_mu = PTHREAD_MUTEX_INITIALIZER;

uint32_t rt_cp1252_to_upper(uint32_t c)
{
    if ((c >= 'a' && c <= 'z') || (c >= 0xE0 && c <= 0xFE && c != 0xF7)) return c - 0x20;
    if (c == 0x9A || c == 0x9C || c == 0x9E) return c - 0x10;
    if (c == 0xFF) return 0x9F;
    return c;
}

uint32_t rt_cp1252_to_lower(uint32_t c)
{
    if ((c >= 'A' && c <= 'Z') || (c >= 0xC0 && c <= 0xDE && c != 0xD7)) return c + 0x20;
    if (c == 0x8A || c == 0x8C || c == 0x8E) return c + 0x10;
    if (c == 0x9F) return 0xFF;
    return c;
}

int rt_stricmp(const char *a, const char *b)
{
    for (;; a++, b++) {
        uint32_t x = rt_cp1252_to_lower((unsigned char)*a), y = rt_cp1252_to_lower((unsigned char)*b);
        if (x != y || !x) return (int)x - (int)y;
    }
}

int rt_vfs_init(const char *game, const char *overlay)
{
    if (!realpath(game, s_game)) return -1;
    snprintf(s_overlay, sizeof s_overlay, "%s", overlay);
    snprintf(s_files, sizeof s_files, "%s/files", overlay);
    return 0;
}

const char *rt_vfs_cwd(void) { return s_cwd; }

static void drop(const char *dir)
{
    for (int i = 0; i < s_ncache; i++)
        if (!strcmp(s_cache[i].dir, dir)) {
            for (int k = 0; k < s_cache[i].n; k++) free(s_cache[i].names[k]);
            free(s_cache[i].names);
            free(s_cache[i].dir);
            s_cache[i] = s_cache[--s_ncache];
            return;
        }
}

void rt_vfs_dirty(const char *host_path)
{
    char d[PATH_MAX];
    snprintf(d, sizeof d, "%s", host_path);
    char *sl = strrchr(d, '/');
    if (sl) *sl = 0;
    pthread_mutex_lock(&s_mu);
    drop(d);
    pthread_mutex_unlock(&s_mu);
}

static listing *list_dir(const char *dir)
{
    for (int i = 0; i < s_ncache; i++)
        if (!strcmp(s_cache[i].dir, dir)) return &s_cache[i];
    DIR *d = opendir(dir);
    if (!d) return NULL;
    if (s_ncache == NCACHE) drop(s_cache[0].dir);
    listing *l = &s_cache[s_ncache++];
    l->dir = strdup(dir);
    l->names = NULL;
    l->n = 0;
    int cap = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        if (l->n == cap) {
            cap = cap ? 2 * cap : 32;
            l->names = realloc(l->names, (size_t)cap * sizeof *l->names);
        }
        l->names[l->n++] = strdup(e->d_name);
    }
    closedir(d);
    return l;
}

static int find_name(const char *dir, const char *name, char *out, size_t n)
{
    pthread_mutex_lock(&s_mu);
    listing *l = list_dir(dir);
    int ok = 0;
    if (l)
        for (int i = 0; i < l->n && !ok; i++)
            if (!rt_stricmp(l->names[i], name)) {
                snprintf(out, n, "%s", l->names[i]);
                ok = 1;
            }
    pthread_mutex_unlock(&s_mu);
    return ok;
}

static int split(const char *guest, char comps[MAXC][256], int *nc)
{
    char buf[PATH_MAX * 2];
    const char *p = guest;
    if (!strncmp(p, "\\\\?\\", 4)) p += 4;
    int absolute = 0;
    if (((p[0] | 0x20) >= 'a' && (p[0] | 0x20) <= 'z') && p[1] == ':') {
        p += 2;
        absolute = *p == '\\' || *p == '/';
    } else if (*p == '\\' || *p == '/') {
        absolute = 1;
    }
    if (absolute) snprintf(buf, sizeof buf, "%s", p);
    else snprintf(buf, sizeof buf, "%s\\%s", s_cwd + 2, p);
    *nc = 0;
    char *save, *t;
    for (char *c = buf; *c; c++)
        if (*c == '/') *c = '\\';
    for (t = strtok_r(buf, "\\", &save); t; t = strtok_r(NULL, "\\", &save)) {
        if (!*t || !strcmp(t, ".")) continue;
        if (!strcmp(t, "..")) {
            if (!*nc) return 3;
            (*nc)--;
            continue;
        }
        if (*nc == MAXC) return 206;
        snprintf(comps[(*nc)++], 256, "%s", t);
    }
    if (!*nc || rt_stricmp(comps[0], "VTMB")) return 3;
    return 0;
}

uint32_t rt_vfs_resolve(const char *guest, char *host, size_t n, int *exists, int *in_overlay, char *rel, size_t reln)
{
    char comps[MAXC][256];
    int nc;
    uint32_t err = (uint32_t)split(guest, comps, &nc);
    if (err) {
        rt_log("path outside C:\\VTMB: %s", guest);
        return err;
    }
    char g[PATH_MAX], o[PATH_MAX], r[PATH_MAX] = "";
    snprintf(g, sizeof g, "%s", s_game);
    snprintf(o, sizeof o, "%s", s_files);
    int gok = 1, ook = 1;
    for (int i = 1; i < nc; i++) {
        char gn[256], on[256];
        int gf = gok && find_name(g, comps[i], gn, sizeof gn);
        int of = ook && find_name(o, comps[i], on, sizeof on);
        const char *chosen = of ? on : gf ? gn : comps[i];
        size_t len = strlen(r);
        snprintf(r + len, sizeof r - len, "%s%s", len ? "/" : "", chosen);
        len = strlen(g);
        snprintf(g + len, sizeof g - len, "/%s", gf ? gn : chosen);
        len = strlen(o);
        snprintf(o + len, sizeof o - len, "/%s", of ? on : chosen);
        gok = gf;
        ook = of;
    }
    if (nc == 1) ook = 1;
    *exists = ook || gok;
    *in_overlay = ook || !gok;
    snprintf(host, n, "%s", *in_overlay ? o : g);
    if (rel) snprintf(rel, reln, "%s", r);
    return 0;
}

static int under_game(const char *host)
{
    size_t n = strlen(s_game);
    return !strncmp(host, s_game, n) && (host[n] == '/' || host[n] == 0);
}

int rt_vfs_open(const char *host, int flags, int mode)
{
    if ((flags & (O_WRONLY | O_RDWR | O_CREAT | O_TRUNC | O_APPEND)) && under_game(host))
        rt_stop(5, 0, "refused a write open of %s inside the game folder", host);
    int fd = open(host, flags | O_CLOEXEC, mode);
    if (fd >= 0 && (flags & O_CREAT)) rt_vfs_dirty(host);
    return fd;
}

static int mkdirs(const char *path)
{
    char b[PATH_MAX];
    snprintf(b, sizeof b, "%s", path);
    for (char *p = b + 1; *p; p++)
        if (*p == '/') {
            *p = 0;
            if (mkdir(b, 0755) == 0) rt_vfs_dirty(b);
            *p = '/';
        }
    if (mkdir(b, 0755) == 0) rt_vfs_dirty(b);
    return 0;
}

int rt_vfs_cow(const char *rel, char *host, size_t n)
{
    char src[PATH_MAX], dst[PATH_MAX];
    snprintf(src, sizeof src, "%s/%s", s_game, rel);
    snprintf(dst, sizeof dst, "%s/%s", s_files, rel);
    char parent[PATH_MAX];
    snprintf(parent, sizeof parent, "%s", dst);
    char *sl = strrchr(parent, '/');
    if (sl) *sl = 0;
    mkdirs(parent);
    int in = open(src, O_RDONLY | O_CLOEXEC);
    if (in < 0) return -1;
    int out = rt_vfs_open(dst, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (out < 0) {
        close(in);
        return -1;
    }
    char buf[1 << 16];
    ssize_t k;
    while ((k = read(in, buf, sizeof buf)) > 0)
        if (write(out, buf, (size_t)k) != k) break;
    close(in);
    close(out);
    snprintf(host, n, "%s", dst);
    return 0;
}

int rt_vfs_mkparents(const char *host)
{
    char parent[PATH_MAX];
    snprintf(parent, sizeof parent, "%s", host);
    char *sl = strrchr(parent, '/');
    if (sl) *sl = 0;
    return under_game(parent) ? -1 : mkdirs(parent);
}

int rt_vfs_full(const char *guest, char *out, size_t n)
{
    char buf[PATH_MAX * 2], comps[MAXC][256];
    const char *p = guest;
    char drive = 'C';
    int absolute = 0;
    if (((p[0] | 0x20) >= 'a' && (p[0] | 0x20) <= 'z') && p[1] == ':') {
        drive = (char)rt_cp1252_to_upper((unsigned char)p[0]);
        p += 2;
        absolute = 1;
    } else if (*p == '\\' || *p == '/') {
        absolute = 1;
    }
    if (absolute) snprintf(buf, sizeof buf, "%s", p);
    else snprintf(buf, sizeof buf, "%s\\%s", s_cwd + 2, p);
    int nc = 0;
    size_t len = strlen(buf);
    int trailing = len && (buf[len - 1] == '\\' || buf[len - 1] == '/');
    char *save, *t;
    for (char *c = buf; *c; c++)
        if (*c == '/') *c = '\\';
    for (t = strtok_r(buf, "\\", &save); t; t = strtok_r(NULL, "\\", &save)) {
        if (!*t || !strcmp(t, ".")) continue;
        if (!strcmp(t, "..")) {
            if (nc) nc--;
            continue;
        }
        if (nc < MAXC) snprintf(comps[nc++], 256, "%s", t);
    }
    size_t o = (size_t)snprintf(out, n, "%c:", drive);
    for (int i = 0; i < nc && o < n; i++) o += (size_t)snprintf(out + o, n - o, "\\%s", comps[i]);
    if ((!nc || trailing) && o < n) o += (size_t)snprintf(out + o, n - o, "\\");
    return (int)o;
}

uint32_t rt_vfs_set_cwd(const char *guest)
{
    char host[PATH_MAX], rel[PATH_MAX];
    int exists, ov;
    uint32_t err = rt_vfs_resolve(guest, host, sizeof host, &exists, &ov, rel, sizeof rel);
    if (err) return err;
    struct stat st;
    if (!exists || stat(host, &st) || !S_ISDIR(st.st_mode)) return 3;
    snprintf(s_cwd, sizeof s_cwd, "C:\\VTMB%s%s", rel[0] ? "\\" : "", rel);
    for (char *c = s_cwd; *c; c++)
        if (*c == '/') *c = '\\';
    return 0;
}

static int add_names(const char *dir, char ***names, int **dirs, uint32_t *n, uint32_t *cap)
{
    pthread_mutex_lock(&s_mu);
    listing *l = list_dir(dir);
    if (!l) {
        pthread_mutex_unlock(&s_mu);
        return 0;
    }
    for (int i = 0; i < l->n; i++) {
        int dup = 0;
        for (uint32_t k = 0; k < *n && !dup; k++) dup = !rt_stricmp((*names)[k], l->names[i]);
        if (dup) continue;
        if (*n == *cap) {
            *cap = *cap ? 2 * *cap : 64;
            *names = realloc(*names, *cap * sizeof **names);
            *dirs = realloc(*dirs, *cap * sizeof **dirs);
        }
        char p[PATH_MAX];
        struct stat st;
        snprintf(p, sizeof p, "%s/%s", dir, l->names[i]);
        (*names)[*n] = strdup(l->names[i]);
        (*dirs)[*n] = !stat(p, &st) && S_ISDIR(st.st_mode);
        (*n)++;
    }
    pthread_mutex_unlock(&s_mu);
    return 1;
}

int rt_vfs_list(const char *guest_dir, char ***names, int **dirs, uint32_t *err)
{
    char host[PATH_MAX], rel[PATH_MAX], o[PATH_MAX], g[PATH_MAX];
    int exists, ov;
    *names = NULL;
    *dirs = NULL;
    *err = rt_vfs_resolve(guest_dir, host, sizeof host, &exists, &ov, rel, sizeof rel);
    if (*err) return -1;
    snprintf(o, sizeof o, "%s%s%s", s_files, rel[0] ? "/" : "", rel);
    snprintf(g, sizeof g, "%s%s%s", s_game, rel[0] ? "/" : "", rel);
    uint32_t n = 0, cap = 0;
    int any = add_names(o, names, dirs, &n, &cap);
    any |= add_names(g, names, dirs, &n, &cap);
    if (!any) {
        *err = 3;
        return -1;
    }
    return (int)n;
}

const char *rt_vfs_overlay(void) { return s_overlay; }
