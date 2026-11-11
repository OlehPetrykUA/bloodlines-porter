#include "recomp_types.h"
#include "rt.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define INVALID 0xFFFFFFFFu
#define STD_IN 0x1Au
#define STD_OUT 0x1Bu
#define STD_ERR 0x1Cu

static uint32_t s_std[3] = {STD_IN, STD_OUT, STD_ERR};

static void filetime(uint32_t p, time_t t, long ns)
{
    if (!p) return;
    uint64_t v = ((uint64_t)t + 11644473600ull) * 10000000ull + (uint64_t)ns / 100u;
    MEM32(p) = (uint32_t)v;
    MEM32(p + 4) = (uint32_t)(v >> 32);
}

static uint32_t attrs(const struct stat *st) { return S_ISDIR(st->st_mode) ? 0x10u : 0x20u; }

static int is_std(uint32_t h) { return h >= STD_IN && h <= STD_ERR; }

static uint32_t err_of(int e)
{
    switch (e) {
    case ENOENT: return 2;
    case ENOTDIR: return 3;
    case EACCES: case EPERM: case EISDIR: return 5;
    case EEXIST: return 80;
    case ENOSPC: return 112;
    default: return 31;
    }
}

static int last_is(const char *path, const char *name)
{
    const char *s = strrchr(path, '/');
    return !rt_stricmp(s ? s + 1 : path, name);
}

static uint32_t parent_ok(const char *guest)
{
    char b[PATH_MAX], host[PATH_MAX];
    snprintf(b, sizeof b, "%s", guest);
    char *p = strrchr(b, '\\'), *q = strrchr(b, '/');
    if (q > p) p = q;
    if (!p) return 0;
    *p = 0;
    if (!b[0] || (b[1] == ':' && !b[2])) return 0;
    int exists, ov;
    uint32_t e = rt_vfs_resolve(b, host, sizeof host, &exists, &ov, NULL, 0);
    if (e) return e;
    struct stat st;
    return exists && !stat(host, &st) && S_ISDIR(st.st_mode) ? 0 : 3;
}

static uint32_t create_file(const uint32_t *a);

static uint32_t CreateFileA(const uint32_t *a)
{
    static int trace = -1;
    if (trace < 0) trace = getenv("RT_TRACE_FILES") != NULL;
    uint32_t h = create_file(a);
    if (trace)
        rt_log("file CreateFileA \"%s\" access 0x%08X disposition %u -> 0x%X error %u", rt_gstr(a[0]), a[1], a[4], h,
               rt_get_last_error());
    return h;
}

static uint32_t create_file(const uint32_t *a)
{
    const char *name = rt_gstr(a[0]);
    uint32_t access = a[1], disp = a[4], flags = a[5];
    int wr = (access & 0x50000116u) != 0;
    char host[PATH_MAX], rel[PATH_MAX];
    int exists, ov;
    uint32_t e = rt_vfs_resolve(name, host, sizeof host, &exists, &ov, rel, sizeof rel);
    if (e) {
        rt_set_last_error(e);
        return INVALID;
    }
    struct stat st;
    if (exists && !stat(host, &st) && S_ISDIR(st.st_mode)) {
        if (!(flags & 0x02000000u)) {
            rt_set_last_error(5);
            return INVALID;
        }
        rt_obj *o = rt_obj_new(RT_OBJ_FILE);
        o->path = strdup(host);
        rt_set_last_error(0);
        return rt_handle_new(o);
    }
    uint32_t le = 0;
    int fl = wr ? ((access & 0x80000000u) || (access & 1u) ? O_RDWR : O_WRONLY) : O_RDONLY;
    if (!exists) {
        if (disp == 3 || disp == 5) {
            e = parent_ok(name);
            rt_set_last_error(e ? e : 2);
            return INVALID;
        }
        if (disp < 1 || disp > 5) {
            rt_set_last_error(87);
            return INVALID;
        }
        if ((e = parent_ok(name))) {
            rt_set_last_error(e);
            return INVALID;
        }
        rt_vfs_mkparents(host);
        fl = (fl == O_RDONLY ? O_RDWR : fl) | O_CREAT | O_EXCL;
    } else {
        if (disp == 1) {
            rt_set_last_error(80);
            return INVALID;
        }
        if (disp == 2 || disp == 4) le = 183;
        int trunc = disp == 2 || disp == 5;
        if (trunc || wr) {
            if (!ov) {
                if (trunc) {
                    char o[PATH_MAX];
                    snprintf(o, sizeof o, "%s/files/%s", rt_vfs_overlay(), rel);
                    snprintf(host, sizeof host, "%s", o);
                    rt_vfs_mkparents(host);
                    fl |= O_CREAT;
                } else if (rt_vfs_cow(rel, host, sizeof host)) {
                    rt_set_last_error(5);
                    return INVALID;
                } else {
                    rt_log("copy-on-write %s", rel);
                }
            }
            if (trunc) fl = (fl == O_RDONLY ? O_RDWR : fl) | O_TRUNC;
        }
    }
    int fd = rt_vfs_open(host, fl, 0644);
    if (fd < 0) {
        rt_set_last_error(err_of(errno));
        return INVALID;
    }
    rt_obj *o = rt_obj_new(RT_OBJ_FILE);
    o->fd = fd;
    o->path = strdup(host);
    o->src = last_is(host, "console.log") ? 3 : -1;
    rt_set_last_error(le);
    return rt_handle_new(o);
}

static rt_obj *file(uint32_t h)
{
    rt_obj *o = rt_handle_get(h, RT_OBJ_FILE);
    if (o && o->fd < 0) {
        rt_set_last_error(6);
        return NULL;
    }
    return o;
}

static uint32_t ReadFile(const uint32_t *a)
{
    if (a[3]) MEM32(a[3]) = 0;
    if (a[0] == STD_IN) return 1;
    rt_obj *o = file(a[0]);
    if (!o) return 0;
    if (!rt_span_ok(a[1], a[2])) {
        rt_set_last_error(998);
        return 0;
    }
    uint32_t got = 0;
    char *dst = rt_host(a[1]);
    while (got < a[2]) {
        ssize_t k = read(o->fd, dst + got, a[2] - got);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0) {
            rt_set_last_error(err_of(errno));
            return 0;
        }
        if (!k) break;
        got += (uint32_t)k;
    }
    if (a[3]) MEM32(a[3]) = got;
    return 1;
}

static uint32_t WriteFile(const uint32_t *a)
{
    const char *src = rt_host(a[1]);
    if (a[3]) MEM32(a[3]) = 0;
    if (!rt_span_ok(a[1], a[2])) {
        rt_set_last_error(998);
        return 0;
    }

    if (is_std(a[0])) {
        rt_guest_text(a[0] == STD_ERR ? 2 : 1, src, a[2]);
        if (a[3]) MEM32(a[3]) = a[2];
        return 1;
    }
    rt_obj *o = file(a[0]);
    if (!o) return 0;
    uint32_t put = 0;
    while (put < a[2]) {
        ssize_t k = write(o->fd, src + put, a[2] - put);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) {
            rt_set_last_error(err_of(errno));
            return 0;
        }
        put += (uint32_t)k;
    }
    if (o->src == 3) rt_guest_text(3, src, a[2]);
    if (a[3]) MEM32(a[3]) = put;
    return 1;
}

static uint32_t WriteConsoleA(const uint32_t *a)
{
    if (!rt_span_ok(a[1], a[2])) {
        rt_set_last_error(998);
        return 0;
    }
    rt_guest_text(a[0] == STD_ERR ? 2 : 1, rt_host(a[1]), a[2]);
    if (a[3]) MEM32(a[3]) = a[2];
    return 1;
}

static uint32_t SetFilePointer(const uint32_t *a)
{
    rt_obj *o = file(a[0]);
    if (!o) return INVALID;
    int64_t off = a[2] ? (int64_t)(((uint64_t)MEM32(a[2]) << 32) | a[1]) : (int64_t)(int32_t)a[1];
    int wh = a[3] == 0 ? SEEK_SET : a[3] == 1 ? SEEK_CUR : SEEK_END;
    off_t r = lseek(o->fd, off, wh);
    if (r < 0) {
        rt_set_last_error(131);
        return INVALID;
    }
    if (a[2]) MEM32(a[2]) = (uint32_t)((uint64_t)r >> 32);
    rt_set_last_error(0);
    return (uint32_t)r;
}

static uint32_t SetEndOfFile(const uint32_t *a)
{
    rt_obj *o = file(a[0]);
    if (!o) return 0;
    off_t r = lseek(o->fd, 0, SEEK_CUR);
    return ftruncate(o->fd, r) == 0;
}

static uint32_t FlushFileBuffers(const uint32_t *a)
{
    if (is_std(a[0])) return 1;
    rt_obj *o = file(a[0]);
    return o && fsync(o->fd) == 0;
}

static uint32_t GetFileSize(const uint32_t *a)
{
    rt_obj *o = file(a[0]);
    struct stat st;
    if (!o || fstat(o->fd, &st)) return INVALID;
    if (a[1]) MEM32(a[1]) = (uint32_t)((uint64_t)st.st_size >> 32);
    rt_set_last_error(0);
    return (uint32_t)st.st_size;
}

static uint32_t GetFileTime(const uint32_t *a)
{
    rt_obj *o = rt_handle_get(a[0], RT_OBJ_FILE);
    struct stat st;
    if (!o || (o->fd >= 0 ? fstat(o->fd, &st) : stat(o->path, &st))) return 0;
    filetime(a[1], st.st_birthtimespec.tv_sec, st.st_birthtimespec.tv_nsec);
    filetime(a[2], st.st_atimespec.tv_sec, st.st_atimespec.tv_nsec);
    filetime(a[3], st.st_mtimespec.tv_sec, st.st_mtimespec.tv_nsec);
    return 1;
}

static uint32_t SetFileTime(const uint32_t *a) { return file(a[0]) != NULL; }

static uint32_t GetFileType(const uint32_t *a)
{
    if (is_std(a[0])) return 2;
    if (!rt_handle_get(a[0], RT_OBJ_FILE)) return 0;
    return 1;
}

static uint32_t GetFileInformationByHandle(const uint32_t *a)
{
    rt_obj *o = file(a[0]);
    struct stat st;
    if (!o || fstat(o->fd, &st)) return 0;
    uint32_t p = a[1];
    memset(rt_host(p), 0, 52);
    MEM32(p) = attrs(&st);
    filetime(p + 4, st.st_birthtimespec.tv_sec, st.st_birthtimespec.tv_nsec);
    filetime(p + 12, st.st_atimespec.tv_sec, st.st_atimespec.tv_nsec);
    filetime(p + 20, st.st_mtimespec.tv_sec, st.st_mtimespec.tv_nsec);
    MEM32(p + 28) = 0x1234ABCDu;
    MEM32(p + 32) = (uint32_t)((uint64_t)st.st_size >> 32);
    MEM32(p + 36) = (uint32_t)st.st_size;
    MEM32(p + 40) = 1;
    MEM32(p + 44) = (uint32_t)(st.st_ino >> 32);
    MEM32(p + 48) = (uint32_t)st.st_ino;
    return 1;
}

static uint32_t GetFileAttributesA(const uint32_t *a)
{
    char host[PATH_MAX];
    int exists, ov;
    uint32_t e = rt_vfs_resolve(rt_gstr(a[0]), host, sizeof host, &exists, &ov, NULL, 0);
    struct stat st;
    if (!e && (!exists || stat(host, &st))) e = parent_ok(rt_gstr(a[0])) ? 3 : 2;
    if (e) {
        rt_set_last_error(e);
        return INVALID;
    }
    return attrs(&st);
}

static uint32_t SetFileAttributesA(const uint32_t *a)
{
    char host[PATH_MAX];
    int exists, ov;
    uint32_t e = rt_vfs_resolve(rt_gstr(a[0]), host, sizeof host, &exists, &ov, NULL, 0);
    if (!e && !exists) e = 2;
    if (e) {
        rt_set_last_error(e);
        return 0;
    }
    return 1;
}

static uint32_t CreateDirectoryA(const uint32_t *a)
{
    char host[PATH_MAX];
    int exists, ov;
    const char *g = rt_gstr(a[0]);
    uint32_t e = rt_vfs_resolve(g, host, sizeof host, &exists, &ov, NULL, 0);
    if (!e && exists) e = 183;
    if (!e) e = parent_ok(g);
    if (e) {
        rt_set_last_error(e);
        return 0;
    }
    rt_vfs_mkparents(host);
    if (mkdir(host, 0755)) {
        rt_set_last_error(err_of(errno));
        return 0;
    }
    rt_vfs_dirty(host);
    return 1;
}

static uint32_t remove_entry(const char *g, int dir)
{
    char host[PATH_MAX];
    int exists, ov;
    uint32_t e = rt_vfs_resolve(g, host, sizeof host, &exists, &ov, NULL, 0);
    if (!e && !exists) e = dir ? 3 : 2;
    if (!e && !ov) {
        rt_log("refused to delete the game file %s", g);
        e = 5;
    }
    if (!e && (dir ? rmdir(host) : unlink(host))) e = err_of(errno);
    if (e) {
        rt_set_last_error(e);
        return 0;
    }
    rt_vfs_dirty(host);
    return 1;
}

static uint32_t DeleteFileA(const uint32_t *a) { return remove_entry(rt_gstr(a[0]), 0); }

static uint32_t RemoveDirectoryA(const uint32_t *a) { return remove_entry(rt_gstr(a[0]), 1); }

static uint32_t MoveFileA(const uint32_t *a)
{
    char s[PATH_MAX], d[PATH_MAX];
    int se, sov, de, dov;
    uint32_t e = rt_vfs_resolve(rt_gstr(a[0]), s, sizeof s, &se, &sov, NULL, 0);
    if (!e) e = rt_vfs_resolve(rt_gstr(a[1]), d, sizeof d, &de, &dov, NULL, 0);
    if (!e && !se) e = 2;
    if (!e && !sov) e = 5;
    if (!e && de) e = 183;
    if (!e) {
        rt_vfs_mkparents(d);
        if (rename(s, d)) e = err_of(errno);
    }
    if (e) {
        rt_set_last_error(e);
        return 0;
    }
    rt_vfs_dirty(s);
    rt_vfs_dirty(d);
    return 1;
}

static uint32_t GetFullPathNameA(const uint32_t *a)
{
    char out[PATH_MAX];
    uint32_t n = (uint32_t)rt_vfs_full(rt_gstr(a[0]), out, sizeof out);
    if (a[1] < n + 1) return n + 1;
    if (!rt_span_ok(a[2], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[2]), out, n + 1);
    if (a[3]) {
        char *p = strrchr(out, '\\');
        MEM32(a[3]) = p && p[1] ? a[2] + (uint32_t)(p + 1 - out) : 0;
    }
    return n;
}

static uint32_t GetCurrentDirectoryA(const uint32_t *a)
{
    const char *c = rt_vfs_cwd();
    uint32_t n = (uint32_t)strlen(c);
    if (a[0] < n + 1) return n + 1;
    if (!rt_span_ok(a[1], (uint64_t)n + 1)) {
        rt_set_last_error(998);
        return 0;
    }
    memcpy(rt_host(a[1]), c, n + 1);
    return n;
}

static uint32_t SetCurrentDirectoryA(const uint32_t *a)
{
    uint32_t e = rt_vfs_set_cwd(rt_gstr(a[0]));
    if (e) {
        rt_set_last_error(e);
        return 0;
    }
    return 1;
}

static uint32_t GetDriveTypeA(const uint32_t *a)
{
    const char *p = rt_gstr(a[0]);
    return !a[0] || ((p[0] | 0x20) == 'c' && p[1] == ':') ? 3 : 1;
}

static uint32_t GetLogicalDrives(const uint32_t *a) { (void)a; return 4; }

static uint32_t GetLogicalDriveStringsA(const uint32_t *a)
{
    if (a[0] < 5) return 5;
    memcpy(rt_host(a[1]), "C:\\\0", 5);
    return 4;
}

static uint32_t GetDiskFreeSpaceA(const uint32_t *a)
{
    uint32_t v[4] = {8, 512, 0x00100000u, 0x00200000u};
    for (int i = 0; i < 4; i++)
        if (a[1 + i]) MEM32(a[1 + i]) = v[i];
    return 1;
}

static int match(const char *p, const char *s)
{
    if (!*p) return !*s;
    if (*p == '*') {
        for (;; s++) {
            if (match(p + 1, s)) return 1;
            if (!*s) return 0;
        }
    }
    if (!*s) return 0;
    if (*p == '?' || rt_cp1252_to_lower((unsigned char)*p) == rt_cp1252_to_lower((unsigned char)*s))
        return match(p + 1, s + 1);
    return 0;
}

static int wild(const char *pat, const char *name)
{
    if (!strcmp(pat, "*.*") || !strcmp(pat, "*")) return 1;
    size_t n = strlen(pat);
    if (n >= 2 && pat[n - 1] == '.' && pat[n - 2] == '*' && !strchr(name, '.')) return match(pat, name) || 1;
    return match(pat, name);
}

static void fill_find(uint32_t p, const char *dir, const char *name)
{
    char g[PATH_MAX], host[PATH_MAX];
    int exists, ov;
    memset(rt_host(p), 0, 320);
    struct stat st;
    memset(&st, 0, sizeof st);
    if (!strcmp(name, ".") || !strcmp(name, "..")) st.st_mode = S_IFDIR;
    else {
        snprintf(g, sizeof g, "%s\\%s", dir, name);
        if (!rt_vfs_resolve(g, host, sizeof host, &exists, &ov, NULL, 0)) stat(host, &st);
    }
    MEM32(p) = attrs(&st);
    filetime(p + 4, st.st_birthtimespec.tv_sec, st.st_birthtimespec.tv_nsec);
    filetime(p + 12, st.st_atimespec.tv_sec, st.st_atimespec.tv_nsec);
    filetime(p + 20, st.st_mtimespec.tv_sec, st.st_mtimespec.tv_nsec);
    MEM32(p + 28) = (uint32_t)((uint64_t)st.st_size >> 32);
    MEM32(p + 32) = (uint32_t)st.st_size;
    snprintf((char *)rt_host(p + 44), 260, "%s", name);
}

static uint32_t FindFirstFileA(const uint32_t *a)
{
    char g[PATH_MAX], dir[PATH_MAX];
    snprintf(g, sizeof g, "%s", rt_gstr(a[0]));
    char *s1 = strrchr(g, '\\'), *s2 = strrchr(g, '/'), *sl = s2 > s1 ? s2 : s1;
    const char *pat;
    if (sl) {
        *sl = 0;
        pat = sl + 1;
        snprintf(dir, sizeof dir, "%s%s", g, (g[0] && !(g[1] == ':' && !g[2])) ? "" : "\\");
    } else if (g[1] == ':') {
        pat = g + 2;
        snprintf(dir, sizeof dir, "%.2s", g);
    } else {
        pat = g;
        snprintf(dir, sizeof dir, "%s", rt_vfs_cwd());
    }
    char full[PATH_MAX];
    rt_vfs_full(dir, full, sizeof full);
    char **names;
    int *dirs;
    uint32_t e;
    int n = rt_vfs_list(dir, &names, &dirs, &e);
    if (n < 0) {
        rt_set_last_error(e);
        return INVALID;
    }
    rt_obj *o = rt_obj_new(RT_OBJ_FIND);
    o->path = strdup(dir);
    o->names = malloc(((size_t)n + 2) * sizeof *o->names);
    int root = !rt_stricmp(full, "C:\\VTMB") || !rt_stricmp(full, "C:\\VTMB\\");
    if (!root) {
        if (wild(pat, ".")) o->names[o->n++] = strdup(".");
        if (wild(pat, "..")) o->names[o->n++] = strdup("..");
    }
    for (int i = 0; i < n; i++) {
        if (wild(pat, names[i])) o->names[o->n++] = names[i];
        else free(names[i]);
    }
    free(names);
    free(dirs);
    if (!o->n) {
        free(o->names);
        free(o->path);
        free(o);
        rt_set_last_error(2);
        return INVALID;
    }
    fill_find(a[1], o->path, o->names[0]);
    o->pos = 1;
    return rt_handle_new(o);
}

static uint32_t FindNextFileA(const uint32_t *a)
{
    rt_obj *o = rt_handle_get(a[0], RT_OBJ_FIND);
    if (!o) return 0;
    if (o->pos >= o->n) {
        rt_set_last_error(18);
        return 0;
    }
    fill_find(a[1], o->path, o->names[o->pos++]);
    return 1;
}

static uint32_t FindClose(const uint32_t *a)
{
    if (!rt_handle_get(a[0], RT_OBJ_FIND)) return 0;
    return (uint32_t)rt_handle_close(a[0]);
}

static uint32_t GetStdHandle(const uint32_t *a)
{
    uint32_t k = 0u - a[0];
    if (k < 10 || k > 12) {
        rt_set_last_error(6);
        return INVALID;
    }
    return s_std[k - 10];
}

static uint32_t SetStdHandle(const uint32_t *a)
{
    uint32_t k = 0u - a[0];
    if (k < 10 || k > 12) {
        rt_set_last_error(6);
        return 0;
    }
    s_std[k - 10] = a[1];
    return 1;
}

static uint32_t LockFile(const uint32_t *a) { return file(a[0]) != NULL; }

static uint32_t GetConsoleMode(const uint32_t *a)
{
    if (!is_std(a[0])) {
        rt_set_last_error(6);
        return 0;
    }
    MEM32(a[1]) = 3;
    return 1;
}

static uint32_t SetConsoleMode(const uint32_t *a) { return is_std(a[0]); }

static uint32_t SetConsoleCtrlHandler(const uint32_t *a) { (void)a; return 1; }

static uint32_t FreeConsole(const uint32_t *a) { (void)a; return 1; }

const rt_shim rt_kernel32_file_shims[] = {
    {"kernel32.dll", "CreateDirectoryA", 2, CreateDirectoryA},
    {"kernel32.dll", "CreateFileA", 7, CreateFileA},
    {"kernel32.dll", "DeleteFileA", 1, DeleteFileA},
    {"kernel32.dll", "FindClose", 1, FindClose},
    {"kernel32.dll", "FindFirstFileA", 2, FindFirstFileA},
    {"kernel32.dll", "FindNextFileA", 2, FindNextFileA},
    {"kernel32.dll", "FlushFileBuffers", 1, FlushFileBuffers},
    {"kernel32.dll", "FreeConsole", 0, FreeConsole},
    {"kernel32.dll", "GetConsoleMode", 2, GetConsoleMode},
    {"kernel32.dll", "GetCurrentDirectoryA", 2, GetCurrentDirectoryA},
    {"kernel32.dll", "GetDiskFreeSpaceA", 5, GetDiskFreeSpaceA},
    {"kernel32.dll", "GetDriveTypeA", 1, GetDriveTypeA},
    {"kernel32.dll", "GetFileAttributesA", 1, GetFileAttributesA},
    {"kernel32.dll", "GetFileInformationByHandle", 2, GetFileInformationByHandle},
    {"kernel32.dll", "GetFileSize", 2, GetFileSize},
    {"kernel32.dll", "GetFileTime", 4, GetFileTime},
    {"kernel32.dll", "GetFileType", 1, GetFileType},
    {"kernel32.dll", "GetFullPathNameA", 4, GetFullPathNameA},
    {"kernel32.dll", "GetLogicalDrives", 0, GetLogicalDrives},
    {"kernel32.dll", "GetLogicalDriveStringsA", 2, GetLogicalDriveStringsA},
    {"kernel32.dll", "GetStdHandle", 1, GetStdHandle},
    {"kernel32.dll", "LockFile", 5, LockFile},
    {"kernel32.dll", "MoveFileA", 2, MoveFileA},
    {"kernel32.dll", "ReadFile", 5, ReadFile},
    {"kernel32.dll", "RemoveDirectoryA", 1, RemoveDirectoryA},
    {"kernel32.dll", "SetConsoleCtrlHandler", 2, SetConsoleCtrlHandler},
    {"kernel32.dll", "SetConsoleMode", 2, SetConsoleMode},
    {"kernel32.dll", "SetCurrentDirectoryA", 1, SetCurrentDirectoryA},
    {"kernel32.dll", "SetEndOfFile", 1, SetEndOfFile},
    {"kernel32.dll", "SetFileAttributesA", 2, SetFileAttributesA},
    {"kernel32.dll", "SetFilePointer", 4, SetFilePointer},
    {"kernel32.dll", "SetFileTime", 4, SetFileTime},
    {"kernel32.dll", "SetStdHandle", 2, SetStdHandle},
    {"kernel32.dll", "UnlockFile", 5, LockFile},
    {"kernel32.dll", "WriteConsoleA", 5, WriteConsoleA},
    {"kernel32.dll", "WriteFile", 5, WriteFile},
};

const uint32_t rt_nkernel32_file_shims = sizeof rt_kernel32_file_shims / sizeof rt_kernel32_file_shims[0];
