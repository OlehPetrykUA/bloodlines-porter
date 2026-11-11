#include "rt.h"

#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define PY_FILE_INPUT 257u

typedef struct {
    uint32_t module, initialize, compile, fetch, clear, tuple_size, tuple_item, as_string, as_long, run_string, import, getattr;
} py_api;

static py_api s_api;
static int s_booted;

static uint32_t rd32(uint32_t va) { return *(const uint32_t *)rt_host(va); }

static void wr32(uint32_t va, uint32_t v) { *(uint32_t *)rt_host(va) = v; }

static uint32_t gcall(uint32_t fn, int n, uint32_t a0, uint32_t a1, uint32_t a2)
{
    uint32_t a[3] = {a0, a1, a2};
    return rt_guest_call(fn, n, a);
}

static uint32_t export_of(const char *name)
{
    uint32_t va = rt_module_export(s_api.module, rt_put_string(name));
    if (!va) printf("py21 fail setup:0 missing export %s\n", name);
    return va;
}

static int api_load(void)
{
    uint32_t err = 0;
    s_api.module = rt_module_load("vampire_python21.dll", 1, &err);
    if (!s_api.module) {
        printf("py21 fail setup:0 vampire_python21.dll did not load (error %u)\n", err);
        return -1;
    }
    uint32_t flag = export_of("Py_NoSiteFlag");
    s_api.initialize = export_of("Py_Initialize");
    s_api.compile = export_of("Py_CompileString");
    s_api.fetch = export_of("PyErr_Fetch");
    s_api.clear = export_of("PyErr_Clear");
    s_api.tuple_size = export_of("PyTuple_Size");
    s_api.tuple_item = export_of("PyTuple_GetItem");
    s_api.as_string = export_of("PyString_AsString");
    s_api.as_long = export_of("PyInt_AsLong");
    s_api.run_string = export_of("PyRun_SimpleString");
    s_api.import = export_of("PyImport_ImportModule");
    s_api.getattr = export_of("PyObject_GetAttrString");
    if (!flag || !s_api.initialize || !s_api.compile || !s_api.fetch || !s_api.clear || !s_api.tuple_size || !s_api.tuple_item ||
        !s_api.as_string || !s_api.as_long || !s_api.run_string || !s_api.import || !s_api.getattr)
        return -1;
    wr32(flag, 1);
    gcall(s_api.initialize, 0, 0, 0, 0);
    return 0;
}

static int boot(void)
{
    if (s_booted) return s_booted > 0 ? 0 : -1;
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    rt_state *st = rt_st();
    st->cmdline = rt_put_string("\"C:\\VTMB\\vampire.exe\"");
    st->cmd_args = st->cmdline + (uint32_t)strlen("\"C:\\VTMB\\vampire.exe\"");
    static const char env[] = "PATH=C:\\VTMB\\Bin;C:\\WINDOWS\\system32;C:\\WINDOWS\0SystemRoot=C:\\WINDOWS\0"
                              "windir=C:\\WINDOWS\0\0";
    st->env_a = rt_heap_alloc(sizeof env, 0);
    memcpy(rt_host(st->env_a), env, sizeof env);
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (rt_modules[m]->tls_end > rt_modules[m]->tls_start) wr32(rt_modules[m]->tls_index, 0);
    rt_reset_cpu();
    rt_thread_primary(RT_TIB);
    int r = api_load();
    s_booted = r ? -1 : 1;
    return r;
}

static int is_py(const char *name)
{
    size_t n = strlen(name);
    return n > 3 && name[n - 3] == '.' && tolower((unsigned char)name[n - 2]) == 'p' && tolower((unsigned char)name[n - 1]) == 'y';
}

static int by_path(const void *a, const void *b) { return strcmp(*(char *const *)a, *(char *const *)b); }

static void collect(const char *dir, char ***list, size_t *n, size_t *cap)
{
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        char p[4096];
        snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
        struct stat st;
        if (stat(p, &st)) continue;
        if (S_ISDIR(st.st_mode)) {
            collect(p, list, n, cap);
        } else if (is_py(e->d_name)) {
            if (*n == *cap) {
                *cap = *cap ? *cap * 2 : 64;
                *list = realloc(*list, *cap * sizeof **list);
            }
            (*list)[(*n)++] = strdup(p);
        }
    }
    closedir(d);
}

static char *read_source(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *raw = malloc((size_t)sz + 2);
    size_t got = sz > 0 ? fread(raw, 1, (size_t)sz, f) : 0;
    fclose(f);
    size_t o = 0;
    for (size_t i = 0; i < got; i++) {
        if (raw[i] == '\r' && i + 1 < got && raw[i + 1] == '\n') continue;
        raw[o++] = raw[i];
    }
    if (o == 0 || raw[o - 1] != '\n') raw[o++] = '\n';
    raw[o] = 0;
    *len = o;
    return raw;
}

static void error_of(int *line, char *msg, size_t msgn)
{
    uint32_t slots = rt_alloc(12);
    *line = 0;
    snprintf(msg, msgn, "unknown error");
    gcall(s_api.fetch, 3, slots, slots + 4, slots + 8);
    uint32_t value = rd32(slots + 4);
    if (value && (int32_t)gcall(s_api.tuple_size, 1, value, 0, 0) == 2) {
        uint32_t text = gcall(s_api.tuple_item, 2, value, 0, 0);
        uint32_t info = gcall(s_api.tuple_item, 2, value, 1, 0);
        uint32_t s = text ? gcall(s_api.as_string, 1, text, 0, 0) : 0;
        if (s) snprintf(msg, msgn, "%s", rt_gstr(s));
        if (info && (int32_t)gcall(s_api.tuple_size, 1, info, 0, 0) >= 2) {
            uint32_t ln = gcall(s_api.tuple_item, 2, info, 1, 0);
            if (ln) *line = (int)gcall(s_api.as_long, 1, ln, 0, 0);
        }
    }
    gcall(s_api.clear, 0, 0, 0, 0);
    for (char *c = msg; *c; c++)
        if (*c == '\n' || *c == '\r') *c = ' ';
}

int rt_py21_compile(const char *dir)
{
    if (boot()) return -1;
    char **list = NULL;
    size_t n = 0, cap = 0;
    collect(dir, &list, &n, &cap);
    qsort(list, n, sizeof *list, by_path);
    int ok = 0, fail = 0;
    for (size_t i = 0; i < n; i++) {
        size_t len;
        char *src = read_source(list[i], &len);
        if (!src) {
            printf("py21 fail %s:0 cannot be read\n", list[i]);
            fail++;
            continue;
        }
        uint32_t text = rt_alloc((uint32_t)len + 1);
        memcpy(rt_host(text), src, len + 1);
        free(src);
        uint32_t code = gcall(s_api.compile, 3, text, rt_put_string(list[i]), PY_FILE_INPUT);
        if (code) {
            printf("py21 ok %s\n", list[i]);
            ok++;
        } else {
            int line;
            char msg[512];
            error_of(&line, msg, sizeof msg);
            printf("py21 fail %s:%d %s\n", list[i], line, msg);
            fail++;
        }
    }
    printf("py21 total %d ok %d fail %d\n", ok + fail, ok, fail);
    fflush(stdout);
    for (size_t i = 0; i < n; i++) free(list[i]);
    free(list);
    return fail;
}

int rt_py21_moddir(const char *script)
{
    if (boot()) return -1;
    int rc = (int32_t)gcall(s_api.run_string, 1, rt_put_string(script), 0, 0);
    uint32_t sys = rc ? 0 : gcall(s_api.import, 1, rt_put_string("sys"), 0, 0);
    uint32_t v = sys ? gcall(s_api.getattr, 2, sys, rt_put_string("moddir"), 0) : 0;
    uint32_t s = v ? gcall(s_api.as_string, 1, v, 0, 0) : 0;
    if (!s) gcall(s_api.clear, 0, 0, 0, 0);
    if (!s) printf("py21 fail moddir:0 sys.moddir not readable (PyRun_SimpleString returned %d)\n", rc);
    else printf("py21 moddir %s\n", rt_gstr(s));
    fflush(stdout);
    return s ? 0 : 1;
}
