#include "recomp_types.h"
#include "rt.h"

#include <ctype.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define UP_MAX 2048
#define UP_OUT 4
#define UP_MODNAME 100
#define UP_APPEND "sys.path.append(\""
#define UP_BASE "Vampire"

typedef struct {
    const char *mod;
    const char *cwd;
    int delete_paths;
    unsigned delay_s;
    int counter;
    int flag;
} up_py_state;

typedef struct {
    char s[UP_OUT][UP_MAX];
    int n;
    int moddir;
    int pass;
    unsigned delay_s;
} up_py_out;

static char s_mod[UP_MODNAME + 1] = UP_BASE;
static char s_cwd[PATH_MAX] = "C:\\VTMB";
static char s_script[UP_MAX];
static up_py_state s_py;
static uint32_t s_py_orig, s_create_orig, s_find_orig;
static uint32_t (*s_run)(const char *text);
static uint32_t (*s_call)(uint32_t va, int n, const uint32_t *args);

static int is_base(const char *mod) { return !rt_stricmp(mod, UP_BASE); }

static int mod_ok(const char *m)
{
    size_t n = strlen(m);
    if (!n || n > UP_MODNAME || !strcmp(m, ".") || !strcmp(m, "..") || m[0] == '-') return 0;
    for (size_t i = 0; i < n; i++)
        if (!isalnum((unsigned char)m[i]) && m[i] != '_' && m[i] != '-' && m[i] != '.') return 0;
    return 1;
}

int rt_up_parse_mod(const char *args, char *mod, size_t n)
{
    char buf[4096], *save, *t, *prev = NULL;
    snprintf(buf, sizeof buf, "%s", args ? args : "");
    snprintf(mod, n, "%s", UP_BASE);
    for (char *q = strchr(buf, '"'); q; q = strchr(q + 1, '"')) {
        char *e = strchr(q + 1, '"');
        if (!e) break;
        if (memchr(q + 1, ' ', (size_t)(e - q - 1)) || memchr(q + 1, '\t', (size_t)(e - q - 1))) {
            const char *g = q;
            while (g > buf && g[-1] == ' ') g--;
            if (g - buf >= 5 && !strncasecmp(g - 5, "-game", 5)) return -1;
        }
        q = e;
    }
    for (char *c = buf; *c; c++)
        if (*c == '"') *c = ' ';
    for (t = strtok_r(buf, " \t", &save); t; t = strtok_r(NULL, " \t", &save)) {
        if (prev && !strcasecmp(prev, "-game")) {
            if (!mod_ok(t)) return -1;
            snprintf(mod, n, "%s", t);
            return 1;
        }
        prev = t;
    }
    return prev && !strcasecmp(prev, "-game") ? -1 : 0;
}

void rt_up_set_mod(const char *name) { snprintf(s_mod, sizeof s_mod, "%s", name); }

const char *rt_up_mod(void) { return s_mod; }

const char *rt_up_moddir_script(void)
{
    snprintf(s_script, sizeof s_script, "import sys; sys.moddir = \"%s\"", s_mod);
    return s_script;
}

static int prefix_ci(const char *s, const char *p)
{
    for (; *p; s++, p++)
        if (rt_cp1252_to_lower((unsigned char)*s) != rt_cp1252_to_lower((unsigned char)*p)) return 0;
    return 1;
}

static void collapse(const char *in, char *out, size_t n)
{
    size_t o = 0;
    int sep = 0;
    for (; *in && o + 1 < n; in++) {
        if (*in == '/' || *in == '\\') {
            if (!sep) out[o++] = '\\';
            sep = 1;
        } else {
            out[o++] = *in;
            sep = 0;
        }
    }
    out[o] = 0;
}

static int transform(const char *cwd, const char *p, const char *name, char *out, size_t n)
{
    char c[UP_MAX];
    collapse(p, c, sizeof c);
    size_t cl = strlen(cwd);
    if (!prefix_ci(c, cwd) || c[cl] != '\\') return snprintf(out, n, "%s", p);
    const char *rest = strchr(c + cl + 1, '\\');
    int w = snprintf(out, n, "%s\\%s%s", cwd, name, rest ? rest : "");
    for (char *q = out; *q; q++)
        if (*q == '\\') *q = '/';
    return w;
}

static int emit(up_py_out *o, const char *s)
{
    if (o->n == UP_OUT || strlen(s) >= UP_MAX) return -1;
    snprintf(o->s[o->n++], UP_MAX, "%s", s);
    return 0;
}

static int emit_path(up_py_out *o, const up_py_state *st, const char *in, size_t pre, size_t post, const char *name)
{
    char t[UP_MAX], line[UP_MAX];
    size_t plen = post - pre - 1;
    char p[UP_MAX];
    if (plen >= sizeof p) return -1;
    memcpy(p, in + pre + 1, plen);
    p[plen] = 0;
    if (transform(st->cwd, p, name, t, sizeof t) >= (int)sizeof t) return -1;
    if (snprintf(line, sizeof line, "%.*s%s%s", (int)(pre + 1), in, t, in + post) >= (int)sizeof line) return -1;
    return emit(o, line);
}

static void up_py_state_init(up_py_state *st, const char *mod, const char *cwd, int delete_paths, unsigned delay_s)
{
    st->mod = mod;
    st->cwd = cwd;
    st->delete_paths = delete_paths;
    st->delay_s = delay_s;
    st->counter = 2;
    st->flag = 0;
}

static int up_py_rewrite(up_py_state *st, const char *in, up_py_out *o)
{
    o->n = 0;
    o->moddir = 0;
    o->pass = 0;
    o->delay_s = 0;
    if (strlen(in) > UP_MAX / 2) {
        o->pass = 1;
        return 0;
    }
    int from = !strncmp(in, "from ", 5);
    if (from && st->delay_s && strstr(in, " import *") && !strstr(in, " demo ") && !strstr(in, " fileutil ") &&
        !strstr(in, " vamputil ") && !strstr(in, " vampire "))
        o->delay_s = st->delay_s;
    if (st->counter > 0 && --st->counter == 0) {
        snprintf(o->s[0], UP_MAX, "import sys; sys.moddir = \"%s\"", st->mod);
        o->n = 1;
        o->moddir = 1;
    }
    int has = strstr(in, UP_APPEND) != NULL;
    if (st->delete_paths) {
        if (from) {
            if (strcmp(in, "from vampire import *") && strcmp(in, "from vamputil import *")) st->flag = 1;
        } else if (has && st->flag) {
            st->flag = 0;
            emit(o, is_base(st->mod) ? "sys.path = sys.path[:-1]" : "sys.path = sys.path[:-2]");
        }
    }
    const char *q1 = has ? strchr(in, '"') : NULL;
    const char *q2 = q1 ? strchr(q1 + 1, '"') : NULL;
    int bad = 0;
    if (q2) {
        size_t pre = (size_t)(q1 - in), post = (size_t)(q2 - in);
        if (!is_base(st->mod)) bad |= emit_path(o, st, in, pre, post, st->mod);
        bad |= emit_path(o, st, in, pre, post, UP_BASE);
    } else {
        bad |= emit(o, in);
    }
    if (bad) {
        o->n = 0;
        o->moddir = 0;
        o->pass = 1;
    }
    return o->n;
}

static uint32_t run_guest(const char *text)
{
    uint32_t n = (uint32_t)strlen(text) + 1u, va = rt_heap_alloc(n, 0);
    if (!va) rt_stop(5, 0, "guest heap exhausted for a Python string");
    memcpy(rt_host(va), text, n);
    uint32_t r = rt_guest_call(s_py_orig, 1, &va);
    rt_heap_free(va);
    return r;
}

static void sleep_s(unsigned s)
{
    rt_log("up loading delay %u s", s);
    rt_block_begin("sleep", s * 1000u);
    struct timespec ts = {(time_t)s, 0};
    while (nanosleep(&ts, &ts) && ts.tv_sec + ts.tv_nsec) {
    }
    rt_block_end();
}

static unsigned s_py_failed;

static uint32_t py_failed(uint32_t r, const char *text)
{
    if (r == 0xFFFFFFFFu && s_py_failed++ < 64) rt_log("python line failed: %.240s", text);
    return r;
}

static uint32_t py_run(const uint32_t *a)
{
    up_py_out o;
    up_py_rewrite(&s_py, rt_gstr(a[0]), &o);
    if (o.pass) return py_failed(s_call(s_py_orig, 1, a), rt_gstr(a[0]));
    if (o.delay_s) sleep_s(o.delay_s);
    uint32_t r = 0;
    for (int i = 0; i < o.n; i++) {
        if (o.moddir && i == 0) rt_log("python moddir %s", s_py.mod);
        r = py_failed(s_run(o.s[i]), o.s[i]);
    }
    return r;
}

static int refused(const char *guest)
{
    char full[PATH_MAX];
    rt_vfs_full(guest, full, sizeof full);
    static const char save[] = "C:\\VTMB\\Vampire\\save";
    size_t n = sizeof save - 1;
    if (prefix_ci(full, save) && (!full[n] || full[n] == '\\')) return 1;
    return !rt_stricmp(full, "C:\\VTMB\\Vampire\\cfg\\config.cfg");
}

static uint32_t create_file(const uint32_t *a)
{
    if (refused(rt_gstr(a[0]))) {
        rt_log("up refused CreateFileA %s", rt_gstr(a[0]));
        rt_set_last_error(2);
        return 0xFFFFFFFFu;
    }
    return s_call(s_create_orig, 7, a);
}

static uint32_t find_first(const uint32_t *a)
{
    if (refused(rt_gstr(a[0]))) {
        rt_log("up refused FindFirstFileA %s", rt_gstr(a[0]));
        rt_set_last_error(2);
        return 0xFFFFFFFFu;
    }
    return s_call(s_find_orig, 2, a);
}

static const rt_shim k_py_shim = {"up", "PyRun_SimpleString", 0, py_run, RT_SHIM_REAL};
static const rt_shim k_create_shim = {"up", "CreateFileA", 7, create_file, RT_SHIM_REAL};
static const rt_shim k_find_shim = {"up", "FindFirstFileA", 2, find_first, RT_SHIM_REAL};

static uint32_t slot_of(const rt_module *mod, const char *dll, const char *name, uint32_t from)
{
    for (uint32_t i = from; i < mod->nslots; i++)
        if (!strcasecmp(mod->slots[i].dll, dll) && !strcmp(mod->slots[i].name, name)) return i + 1;
    return 0;
}

uint32_t rt_up_slot(const char *module, const char *dll, const char *name)
{
    for (uint32_t m = 0; m < rt_nmodules; m++) {
        if (strcmp(rt_modules[m]->name, module)) continue;
        uint32_t i = slot_of(rt_modules[m], dll, name, 0);
        return i ? rt_modules[m]->slots[i - 1].iat : 0;
    }
    return 0;
}

int rt_up_wrap_import(const char *module, const char *dll, const char *name, const rt_shim *shim, uint32_t *orig)
{
    int n = 0;
    uint32_t wrapped = rt_dynamic_shim(shim->dll, shim->name, shim);
    for (uint32_t m = 0; m < rt_nmodules; m++) {
        const rt_module *mod = rt_modules[m];
        if (strcmp(mod->name, module)) continue;
        for (uint32_t i = slot_of(mod, dll, name, 0); i; i = slot_of(mod, dll, name, i)) {
            uint32_t iat = mod->slots[i - 1].iat, cur = MEM32(iat);
            if (cur == wrapped) continue;
            *orig = cur;
            MEM32(iat) = wrapped;
            n++;
        }
    }
    return n;
}

static int create_dirs(void)
{
    static const char *const sub[] = {"save", "cfg"};
    char p[PATH_MAX];
    for (int i = 0; i < 2; i++) {
        snprintf(p, sizeof p, "%s/files/%s/%s/x", rt_vfs_overlay(), s_mod, sub[i]);
        if (rt_vfs_mkparents(p)) rt_stop(5, 0, "Create Dirs would create %s inside the game folder", p);
    }
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (!strcmp(rt_modules[m]->name, "engine")) {
            uint32_t va = rt_modules[m]->base + 0x1a4c58u;
            if (MEM32(va) == 0x45564153u) MEM32(va) = 0x65766173u;
            if (MEM32(va) != 0x65766173u) rt_log("up engine save-dir word is 0x%08X, not 'save'", MEM32(va));
        }
    return 0;
}

static int host_of(const char *guest, char *host, size_t n)
{
    int exists, ov;
    if (rt_vfs_resolve(guest, host, n, &exists, &ov, NULL, 0)) return 0;
    return exists;
}

static void install_python(void)
{
    s_call = rt_guest_call;
    s_run = run_guest;
    up_py_state_init(&s_py, s_mod, s_cwd, rt_up_switch_on("Mods", "Delete Paths"),
                     (unsigned)rt_up_switch_num("Tweaks", "Loading Delay Sec"));
    uint32_t want = 0;
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (!strcmp(rt_modules[m]->name, "vampire_python21"))
            want = rt_module_export(rt_modules[m]->base, rt_put_string("PyRun_SimpleString"));
    static const char *const mods[] = {"vampire", "engine"};
    int n = 0;
    for (int i = 0; i < 2; i++) {
        uint32_t iat = rt_up_slot(mods[i], "vampire_python21.dll", "PyRun_SimpleString");
        if (want && iat && MEM32(iat) != want) {
            rt_log("up %s PyRun_SimpleString slot holds 0x%08X, expected 0x%08X; not wrapped", mods[i], MEM32(iat), want);
            continue;
        }
        n += rt_up_wrap_import(mods[i], "vampire_python21.dll", "PyRun_SimpleString", &k_py_shim, &s_py_orig);
    }
    rt_log("up python wrapper on %d import words", n);
}

const char rt_up_sha256_embedded[] __attribute__((weak)) = "";

const char *rt_up_build_sha(void) { return rt_up_sha256_embedded[0] ? rt_up_sha256_embedded : NULL; }

int rt_up_init(const char *args)
{
    char mod[UP_MODNAME + 1];
    s_call = rt_guest_call;
    int r = rt_up_parse_mod(args, mod, sizeof mod);
    if (r < 0) {
        rt_log("up -game needs a plain folder name");
        return -1;
    }
    rt_up_set_mod(mod);
    rt_log("mod %s", s_mod);
    if (!r || is_base(s_mod)) {
        rt_log("python moddir %s", UP_BASE);
        rt_up_fixes_guard();
        return 0;
    }
    snprintf(s_cwd, sizeof s_cwd, "%s", rt_vfs_cwd());
    char host[PATH_MAX], guest[PATH_MAX];
    rt_up_switches_reset();
    int got = 0;
    if (host_of("C:\\VTMB\\Bin\\loader\\loader.ini", host, sizeof host)) got += rt_up_switches_read(host);
    else rt_log("up loader.ini not found; switch defaults apply");
    snprintf(guest, sizeof guest, "C:\\VTMB\\%s\\cfg\\loader.ini", s_mod);
    if (host_of(guest, host, sizeof host)) got += rt_up_switches_read(host);
    rt_up_plugins_reset();
    for (uint32_t i = 0; i < rt_up_plugin_count(); i++) {
        const char *plugin, *key;
        rt_up_plugin_row(i, &plugin, &key);
        if (strcmp(key, "Enabled")) continue;
        snprintf(guest, sizeof guest, "C:\\VTMB\\Bin\\loader\\%s.vtm", plugin);
        int present = host_of(guest, host, sizeof host);
        rt_up_plugin_set(plugin, "Enabled", present != 0);
        snprintf(guest, sizeof guest, "C:\\VTMB\\Bin\\loader\\%s.ini", rt_up_plugin_ini(plugin));
        if (present && host_of(guest, host, sizeof host)) rt_up_plugin_read(host, plugin);
        rt_log("up plugin %s %s", plugin, present ? "present" : "absent");
    }
    rt_log("up switches %d set, %u known", got, rt_up_switch_count());
    rt_up_switches_log();
    rt_log("up build sha256 %s", rt_up_build_sha() ? rt_up_build_sha() : "none");
    if (rt_up_switch_on("Mods", "Fix sys.path")) install_python();
    if (rt_up_switch_on("Mods", "Fix Cfg-Saves")) {
        int n = rt_up_wrap_import("FileSystem_Stdio", "KERNEL32.dll", "CreateFileA", &k_create_shim, &s_create_orig);
        n += rt_up_wrap_import("FileSystem_Stdio", "KERNEL32.dll", "FindFirstFileA", &k_find_shim, &s_find_orig);
        rt_log("up cfg-saves wrapper on %d import words", n);
    }
    if (rt_up_switch_on("Mods", "Create Dirs")) create_dirs();
    rt_up_fixes_apply();
    return 0;
}

static int s_pass, s_fail;

#define check rt_up_check

void rt_up_check(int ok, const char *name, const char *fmt, ...)
{
    if (ok) {
        printf("PASS %s\n", name);
        s_pass++;
        return;
    }
    char b[512] = "";
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    printf("FAIL %s: %s\n", name, b);
    s_fail++;
}

static char s_rec[16][UP_MAX];
static int s_nrec;
static uint32_t s_rec_ret;

static uint32_t rec_run(const char *text)
{
    if (s_nrec < 16) snprintf(s_rec[s_nrec++], UP_MAX, "%s", text);
    printf("py> %s\n", text);
    return s_rec_ret + (uint32_t)s_nrec;
}

static uint32_t rec_call(uint32_t va, int n, const uint32_t *args)
{
    printf("call 0x%08X %d args, first 0x%08X\n", va, n, n ? args[0] : 0);
    s_nrec++;
    return 0x1234u;
}

static void feed(const char *in)
{
    uint32_t va = rt_put_string(in);
    s_nrec = 0;
    py_run(&va);
}

static int rec_is(int i, const char *want) { return i < s_nrec && !strcmp(s_rec[i], want); }

static void parse_cases(void)
{
    char m[UP_MODNAME + 1];
    check(rt_up_parse_mod("-w 1280 -game Unofficial_Patch +echo x", m, sizeof m) == 1 && !strcmp(m, "Unofficial_Patch"),
          "parse -game", "got %s", m);
    check(rt_up_parse_mod("-GAME unofficial_patch", m, sizeof m) == 1 && !strcmp(m, "unofficial_patch"), "parse is case-insensitive", "got %s", m);
    check(rt_up_parse_mod("-w 1280 +echo x", m, sizeof m) == 0 && !strcmp(m, UP_BASE), "no -game is Vampire", "got %s", m);
    check(rt_up_parse_mod("-game", m, sizeof m) < 0, "-game without a name is refused", "");
    check(rt_up_parse_mod("-game ..", m, sizeof m) < 0, "-game .. is refused", "");
    check(rt_up_parse_mod("-game a\\b", m, sizeof m) < 0, "-game with a separator is refused", "");
    check(rt_up_parse_mod("-game -console", m, sizeof m) < 0, "-game followed by an option is refused", "");
    check(rt_up_parse_mod("-game \"Unofficial_Patch\"", m, sizeof m) == 1 && !strcmp(m, "Unofficial_Patch"), "quoted name", "got %s", m);
    check(rt_up_parse_mod("-game \"Unofficial Patch\"", m, sizeof m) < 0, "quoted name with a space is refused", "got %s", m);
    check(rt_up_parse_mod("-w \"a b\" -game Unofficial_Patch", m, sizeof m) == 1 && !strcmp(m, "Unofficial_Patch"),
          "another quoted option with a space is fine", "got %s", m);
}

static int write_file(const char *path, const char *mode, const char *text)
{
    FILE *f = fopen(path, mode);
    int ok = f && fputs(text, f) >= 0;
    if (f && fclose(f)) ok = 0;
    if (!ok) check(0, "scratch file can be written", "%s", path);
    return ok;
}

static void switch_cases(const char *dir)
{
    char a[PATH_MAX], b[PATH_MAX];
    snprintf(a, sizeof a, "%s/a.ini", dir);
    snprintf(b, sizeof b, "%s/b.ini", dir);
    if (!write_file(a, "w",
                    "[Mods]\r\nFix sys.path = 1 ; comment\r\nDelete Paths = 0\r\nUnknown Key = 5\r\n[Tweaks]\r\nLists Height Scale = 1.5\r\n"
                    "Loading Delay Sec = 3\r\n[mods]\r\nfix cfg-saves = 0\r\n") ||
        !write_file(b, "w", "[Mods]\nDelete Paths = 1\nCreate Dirs = x\n"))
        return;
    rt_up_switches_reset();
    check(rt_up_switches_read(a) == 5, "first ini sets five known keys", "");
    check(rt_up_switch_on("Mods", "Fix sys.path") && !rt_up_switch_on("Mods", "Delete Paths"), "values", "");
    check(rt_up_switch_num("Tweaks", "Lists Height Scale") == 1.5, "float key", "");
    check(!rt_up_switch_on("Mods", "Fix Cfg-Saves"), "section and key match without case", "");
    check(rt_up_switches_read(b) == 1, "second ini sets one key (a non-number is skipped)", "");
    check(rt_up_switch_on("Mods", "Delete Paths"), "later file wins", "");
    check(rt_up_switch_on("Mods", "Create Dirs"), "skipped value keeps the default", "");
    check(rt_up_switch_on("Bugs", "Fix Screenshots") && rt_up_switch_on("Tweaks", "Exit by Alt+F4") &&
              rt_up_switch_on("Modern OS", "Console") && !rt_up_switch_on("Mods", "Check Restart"),
          "hidden keys take the exe default", "");
    check(rt_up_switches_read("/nonexistent/loader.ini") == -1, "missing file", "");
    check(rt_up_switch_count() == 69, "69 rows", "got %u", rt_up_switch_count());
    int sup = 0;
    const char *s, *k;
    for (uint32_t i = 0; i < rt_up_switch_count(); i++) {
        int one;
        rt_up_switch_row(i, &s, &k, &one);
        sup += one;
    }
    check(sup == 17, "seventeen rows supported (five by A, twelve by C1)", "got %d", sup);
    rt_up_switches_reset();
    check(rt_up_switch_on("Mods", "Delete Paths") && rt_up_switch_num("Tweaks", "Lists Height Scale") == 1.0, "reset restores defaults", "");
}

static void rewrite_cases(void)
{
    s_run = rec_run;
    s_call = rec_call;
    static const char cwd[] = "C:\\VTMB";
    up_py_state_init(&s_py, "Unofficial_Patch", cwd, 1, 0);
    s_rec_ret = 100;
    uint32_t va = rt_put_string("import sys; sys.path.append(\"C:\\\\VTMB\\\\vampire\\\\python\")");
    s_nrec = 0;
    uint32_t r = py_run(&va);
    check(s_nrec == 2 && rec_is(0, "import sys; sys.path.append(\"C:/VTMB/Unofficial_Patch/python\")") &&
              rec_is(1, "import sys; sys.path.append(\"C:/VTMB/Vampire/python\")"),
          "engine append: mod first, base second", "n %d", s_nrec);
    check(r == 102, "wrapper returns the last call's result", "got %u", r);
    feed("sys.path.append(\"C:\\\\VTMB\\\\Unofficial_Patch/python/lib/\")");
    check(s_nrec == 3 && rec_is(0, "import sys; sys.moddir = \"Unofficial_Patch\"") &&
              rec_is(1, "sys.path.append(\"C:/VTMB/Unofficial_Patch/python/lib/\")") &&
              rec_is(2, "sys.path.append(\"C:/VTMB/Vampire/python/lib/\")"),
          "second call sets moddir first, then the lib pair", "n %d", s_nrec);
    feed("sys.path.append(\"C:\\\\VTMB\\\\Unofficial_Patch/python/lib/\")");
    check(s_nrec == 2, "moddir runs once", "n %d", s_nrec);
    feed("sys.path.append(\"C:/VTMB/Unofficial_Patch/python/sm_pawnshop_1\")");
    check(s_nrec == 2 && rec_is(0, "sys.path.append(\"C:/VTMB/Unofficial_Patch/python/sm_pawnshop_1\")") &&
              rec_is(1, "sys.path.append(\"C:/VTMB/Vampire/python/sm_pawnshop_1\")"),
          "first map append: pair, nothing deleted", "n %d", s_nrec);
    feed("from vampire import *");
    check(s_nrec == 1 && rec_is(0, "from vampire import *"), "from vampire import * passes through", "");
    feed("from sm_pawnshop_1 import *");
    check(s_nrec == 1, "map import passes through", "");
    feed("sys.path.append(\"C:/VTMB/Unofficial_Patch/python/oceanhouse\")");
    check(s_nrec == 3 && rec_is(0, "sys.path = sys.path[:-2]") &&
              rec_is(1, "sys.path.append(\"C:/VTMB/Unofficial_Patch/python/oceanhouse\")") &&
              rec_is(2, "sys.path.append(\"C:/VTMB/Vampire/python/oceanhouse\")"),
          "next map append drops the previous pair, base pair kept", "n %d", s_nrec);
    feed("sys.path.append(\"C:/VTMB/Unofficial_Patch/python/oceanhouse\")");
    check(s_nrec == 2, "deletion happens once per map", "n %d", s_nrec);
    feed("sys.path.append(\"D:\\\\Other\\\\python\")");
    check(s_nrec == 2 && rec_is(0, "sys.path.append(\"D:\\\\Other\\\\python\")") && rec_is(1, "sys.path.append(\"D:\\\\Other\\\\python\")"),
          "a path outside the game folder is unchanged", "n %d", s_nrec);
    feed("sys.path.append(\"c:\\\\vtmb\\\\VAMPIRE\\\\python\\\\x\")");
    check(rec_is(0, "sys.path.append(\"C:/VTMB/Unofficial_Patch/python/x\")"), "cwd match ignores case", "got %s", s_rec[0]);
    feed("print 'hi'");
    check(s_nrec == 1 && rec_is(0, "print 'hi'"), "other strings pass through", "");
    unsigned failed0 = s_py_failed;
    s_rec_ret = 0xFFFFFFFEu;
    feed("print 'hi'");
    feed("print 'ok'");
    s_rec_ret = 100;
    check(s_py_failed == failed0 + 2, "a Python line that raises is counted and logged", "counted %u", s_py_failed - failed0);

    up_py_state_init(&s_py, "Vampire", cwd, 1, 0);
    feed("import sys; sys.path.append(\"C:\\\\VTMB\\\\vampire\\\\python\")");
    check(s_nrec == 1 && rec_is(0, "import sys; sys.path.append(\"C:/VTMB/Vampire/python\")"), "vanilla form: one normalised append", "n %d", s_nrec);
    feed("sys.path.append(\"C:\\\\VTMB\\\\Vampire/python/lib/\")");
    check(s_nrec == 2 && rec_is(0, "import sys; sys.moddir = \"Vampire\"") && rec_is(1, "sys.path.append(\"C:/VTMB/Vampire/python/lib/\")"),
          "vanilla moddir", "n %d", s_nrec);
    feed("from m1 import *");
    feed("sys.path.append(\"C:/VTMB/Vampire/python/m2\")");
    check(s_nrec == 2 && rec_is(0, "sys.path = sys.path[:-1]"), "vanilla deletes one entry", "n %d", s_nrec);

    up_py_state_init(&s_py, "Unofficial_Patch", cwd, 0, 0);
    feed("a = 1");
    feed("a = 2");
    feed("from m1 import *");
    feed("sys.path.append(\"C:/VTMB/Unofficial_Patch/python/m2\")");
    check(s_nrec == 2 && !rec_is(0, "sys.path = sys.path[:-2]"), "Delete Paths off deletes nothing", "n %d", s_nrec);

    up_py_state_init(&s_py, "Unofficial_Patch", cwd, 0, 0);
    char big[UP_MAX * 2];
    memset(big, 'x', sizeof big);
    memcpy(big, "a = '", 5);
    big[sizeof big - 1] = 0;
    va = rt_put_string(big);
    s_nrec = 0;
    py_run(&va);
    check(s_nrec == 1, "an oversize string goes to the original untouched", "n %d", s_nrec);

    rt_up_set_mod("Unofficial_Patch");
    check(!strcmp(rt_up_moddir_script(), "import sys; sys.moddir = \"Unofficial_Patch\""), "moddir script names the mod", "got %s", rt_up_moddir_script());
}

static void wrap_cases(void)
{
    static const struct { const char *module, *dll, *name; const rt_shim *shim; } k[] = {
        {"vampire", "vampire_python21.dll", "PyRun_SimpleString", &k_py_shim},
        {"engine", "vampire_python21.dll", "PyRun_SimpleString", &k_py_shim},
        {"FileSystem_Stdio", "KERNEL32.dll", "CreateFileA", &k_create_shim},
        {"FileSystem_Stdio", "KERNEL32.dll", "FindFirstFileA", &k_find_shim},
    };
    for (size_t i = 0; i < sizeof k / sizeof k[0]; i++) {
        uint32_t iat = rt_up_slot(k[i].module, k[i].dll, k[i].name), before, orig = 0;
        if (!iat) {
            check(0, k[i].module, "no %s slot for %s", k[i].dll, k[i].name);
            continue;
        }
        before = MEM32(iat);
        uint32_t shim = rt_dynamic_shim(k[i].shim->dll, k[i].shim->name, k[i].shim);
        check(before != shim && rt_up_wrap_import(k[i].module, k[i].dll, k[i].name, k[i].shim, &orig) >= 1 &&
                  orig == before && MEM32(iat) == shim,
              k[i].name, "slot 0x%08X held 0x%08X, orig 0x%08X, now 0x%08X", iat, before, orig, MEM32(iat));
        check(rt_up_wrap_import(k[i].module, k[i].dll, k[i].name, k[i].shim, &orig) == 0 && orig == before, "wrap twice keeps the original", "");
        MEM32(iat) = before;
    }
}

static void fs_cases(void)
{
    s_call = rec_call;
    s_create_orig = 0xFE000480u;
    s_find_orig = 0xFE000610u;
    static const char *const bad[] = {"C:\\VTMB\\Vampire\\save\\quick.sav", "c:\\vtmb\\vampire\\SAVE\\", "C:/VTMB/Vampire/save",
                                      "C:\\VTMB\\vampire\\cfg\\CONFIG.CFG", "Vampire\\save\\x"};
    static const char *const good[] = {"C:\\VTMB\\Unofficial_Patch\\save\\quick.sav", "C:\\VTMB\\Unofficial_Patch\\cfg\\config.cfg",
                                       "C:\\VTMB\\Vampire\\cfg\\autoexec.cfg", "C:\\VTMB\\Vampire\\saved\\x", "C:\\VTMB\\Vampire\\maps\\a.bsp"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        uint32_t a[7] = {rt_put_string(bad[i]), 0x80000000u, 1, 0, 3, 0x80, 0}, b[2] = {a[0], 0};
        s_nrec = 0;
        rt_set_last_error(0);
        uint32_t r = create_file(a), e = rt_get_last_error(), r2 = find_first(b);
        check(r == 0xFFFFFFFFu && e == 2 && r2 == 0xFFFFFFFFu && s_nrec == 0, bad[i], "r 0x%X e %u r2 0x%X calls %d", r, e, r2, s_nrec);
    }
    for (size_t i = 0; i < sizeof good / sizeof good[0]; i++) {
        uint32_t a[7] = {rt_put_string(good[i]), 0x80000000u, 1, 0, 3, 0x80, 0}, b[2] = {a[0], 0};
        s_nrec = 0;
        uint32_t r = create_file(a), r2 = find_first(b);
        check(r == 0x1234u && r2 == 0x1234u && s_nrec == 2, good[i], "r 0x%X r2 0x%X calls %d", r, r2, s_nrec);
    }
}

static int slurp(const char *path, char *buf, size_t n)
{
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    size_t k = fread(buf, 1, n, f);
    fclose(f);
    return (int)k;
}

static void vfs_cases(const char *dir)
{
    char gu[PATH_MAX], ov[PATH_MAX], file[PATH_MAX], over[PATH_MAX];
    snprintf(gu, sizeof gu, "%s/gu", dir);
    snprintf(ov, sizeof ov, "%s/ov", dir);
    snprintf(file, sizeof file, "%s/Unofficial_Patch/scripts", gu);
    char cmd[PATH_MAX * 2];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s' '%s'", file, ov);
    if (system(cmd)) {
        check(0, "scratch folders", "mkdir failed");
        return;
    }
    snprintf(file, sizeof file, "%s/Unofficial_Patch/scripts/kb_act.lst", gu);
    if (!write_file(file, "wb", "ORIGINAL-CONTENT-OF-GU")) return;
    chmod(file, 0444);
    if (rt_vfs_init(gu, ov)) {
        check(0, "vfs init", "failed");
        return;
    }
    const rt_shim *cf = rt_find_shim("kernel32.dll", "CreateFileA"), *wf = rt_find_shim("kernel32.dll", "WriteFile"),
                  *rf = rt_find_shim("kernel32.dll", "ReadFile"), *ch = rt_find_shim("kernel32.dll", "CloseHandle");
    if (!cf || !wf || !rf || !ch) {
        check(0, "file shims", "missing");
        return;
    }
    uint32_t name = rt_put_string("C:\\VTMB\\Unofficial_Patch\\scripts\\kb_act.lst"), buf = rt_alloc(64), got = rt_alloc(4);
    memcpy(rt_host(buf), "OVERLAY", 7);
    uint32_t oa[7] = {name, 0x40000000u, 1, 0, 3, 0x80, 0};
    uint32_t h = cf->fn(oa);
    uint32_t wa[5] = {h, buf, 7, got, 0};
    check(h != 0xFFFFFFFFu && wf->fn(wa) == 1 && MEM32(got) == 7, "write-open of the GU file", "handle 0x%X", h);
    uint32_t ca[1] = {h};
    ch->fn(ca);
    char data[64] = "";
    int n = slurp(file, data, sizeof data);
    check(n == 22 && !memcmp(data, "ORIGINAL-CONTENT-OF-GU", 22), "the GU file is unchanged", "%d bytes", n);
    snprintf(over, sizeof over, "%s/files/Unofficial_Patch/scripts/kb_act.lst", ov);
    n = slurp(over, data, sizeof data);
    check(n == 22 && !memcmp(data, "OVERLAYL-CONTENT-OF-GU", 22), "the write landed in the overlay", "%d bytes: %.22s", n, data);
    oa[1] = 0x80000000u;
    h = cf->fn(oa);
    memset(rt_host(buf), 0, 64);
    uint32_t ra[5] = {h, buf, 22, got, 0};
    check(h != 0xFFFFFFFFu && rf->fn(ra) == 1 && !memcmp(rt_host(buf), "OVERLAYL-CONTENT-OF-GU", 22), "a read after it returns the overlay copy", "");
    ca[0] = h;
    ch->fn(ca);
    n = slurp(file, data, sizeof data);
    check(n == 22 && !memcmp(data, "ORIGINAL-CONTENT-OF-GU", 22), "the GU file is still unchanged", "");
    snprintf(cmd, sizeof cmd, "chmod -R u+w '%s'", gu);
    if (system(cmd)) printf("note: chmod of the scratch GU failed\n");
    rt_up_set_mod("Unofficial_Patch");
    create_dirs();
    struct stat st;
    snprintf(over, sizeof over, "%s/files/Unofficial_Patch/save", ov);
    int a = !stat(over, &st) && S_ISDIR(st.st_mode);
    snprintf(over, sizeof over, "%s/files/Unofficial_Patch/cfg", ov);
    int b = !stat(over, &st) && S_ISDIR(st.st_mode);
    snprintf(over, sizeof over, "%s/save", gu);
    int c = !stat(over, &st);
    snprintf(over, sizeof over, "%s/Unofficial_Patch/save", gu);
    c |= !stat(over, &st);
    check(a && b && !c, "Create Dirs makes save and cfg in the overlay only", "save %d cfg %d under GU %d", a, b, c);
}

static void init_cases(const char *dir)
{
    static const struct { const char *module, *dll, *name; const rt_shim *shim; uint32_t *orig; } k[] = {
        {"vampire", "vampire_python21.dll", "PyRun_SimpleString", &k_py_shim, &s_py_orig},
        {"engine", "vampire_python21.dll", "PyRun_SimpleString", &k_py_shim, &s_py_orig},
        {"FileSystem_Stdio", "KERNEL32.dll", "CreateFileA", &k_create_shim, &s_create_orig},
        {"FileSystem_Stdio", "KERNEL32.dll", "FindFirstFileA", &k_find_shim, &s_find_orig},
    };
    enum { N = sizeof k / sizeof k[0] };
    char ini[PATH_MAX], cmd[PATH_MAX * 2];
    snprintf(ini, sizeof ini, "%s/gu/Bin/loader", dir);
    snprintf(cmd, sizeof cmd, "mkdir -p '%s'", ini);
    if (system(cmd)) {
        check(0, "rt_up_init scratch loader.ini folder", "mkdir failed");
        return;
    }
    snprintf(ini, sizeof ini, "%s/gu/Bin/loader/loader.ini", dir);
    if (!write_file(ini, "w", "[Mods]\r\nFix sys.path = 1\r\nFix Cfg-Saves = 1\r\nCreate Dirs = 0\r\n")) return;
    uint32_t iat[N], before[N], shim[N];
    for (int i = 0; i < N; i++) {
        iat[i] = rt_up_slot(k[i].module, k[i].dll, k[i].name);
        before[i] = iat[i] ? MEM32(iat[i]) : 0;
        shim[i] = rt_dynamic_shim(k[i].shim->dll, k[i].shim->name, k[i].shim);
        if (!iat[i]) check(0, k[i].name, "no %s slot for %s", k[i].dll, k[i].name);
    }
    uint32_t keep[3] = {s_py_orig, s_create_orig, s_find_orig};
    int r = rt_up_init("-game Unofficial_Patch");
    check(r == 0 && !strcmp(rt_up_mod(), "Unofficial_Patch"), "rt_up_init takes the mod folder", "r %d mod %s", r, rt_up_mod());
    for (int i = 0; i < N; i++) {
        if (!iat[i]) continue;
        check(MEM32(iat[i]) == shim[i] && *k[i].orig == before[i], k[i].module, "slot 0x%08X now 0x%08X want 0x%08X, orig 0x%08X want 0x%08X",
              iat[i], MEM32(iat[i]), shim[i], *k[i].orig, before[i]);
    }
    for (int i = 0; i < N; i++)
        if (iat[i]) MEM32(iat[i]) = before[i];
    int same = 1;
    for (int i = 0; i < N; i++) same &= !iat[i] || MEM32(iat[i]) == before[i];
    rt_hook_revert_all();
    check(same, "import slots restored after rt_up_init", "");
    s_py_orig = keep[0];
    s_create_orig = keep[1];
    s_find_orig = keep[2];
    rt_up_switches_reset();
}

static void hook_target_cases(void)
{
    static const struct { const char *module; uint32_t rva; } k[] = {
        {"vampire", 0x1c17e0}, {"vampire", 0x48e5}, {"vampire", 0x40d9}, {"vampire", 0x11004}, {"vampire", 0x11e46},
        {"vampire", 0x5024}, {"vampire", 0x5e0c}, {"vampire", 0x151b8}, {"vampire", 0x138d6}, {"vampire", 0x7e19},
        {"vampire", 0x100b4}, {"vampire", 0x32d0c0}, {"vampire", 0x9c8c}, {"vampire", 0x11667}, {"vampire", 0x7fe5},
        {"vampire", 0xb802}, {"vampire", 0x431447},
        {"client", 0x17d930}, {"client", 0x1e28e0}, {"client", 0x7b160}, {"client", 0x1b4360}, {"client", 0xf0ed0},
        {"client", 0x101e20},
    };
    for (size_t i = 0; i < sizeof k / sizeof k[0]; i++) {
        uint32_t va = rt_hook_va(k[i].module, k[i].rva);
        check(va && recomp_lookup(va) != NULL, "a hook call target is a lifted function", "%s+0x%x (0x%08X) has no entry",
              k[i].module, k[i].rva, va);
    }
}

int rt_up_selftest(const char *game, const char *overlay)
{
    (void)overlay;
    const char *tmp = getenv("TMPDIR");
    char dir[PATH_MAX];
    snprintf(dir, sizeof dir, "%s/bl-up-selftest-XXXXXX", tmp && *tmp ? tmp : "/tmp");
    if (!mkdtemp(dir)) {
        printf("FAIL scratch folder: cannot be created\n");
        return 1;
    }
    char saved_mod[UP_MODNAME + 1];
    snprintf(saved_mod, sizeof saved_mod, "%s", s_mod);
    rt_heap_selftest();
    struct rlimit fd;
    int fd_ok = !getrlimit(RLIMIT_NOFILE, &fd);
    rlim_t fd_want = fd.rlim_max < OPEN_MAX ? fd.rlim_max : OPEN_MAX;
    check(fd_ok && fd.rlim_cur >= fd_want, "the open-file limit is raised to min(OPEN_MAX, hard limit)", "soft %llu hard %llu",
          (unsigned long long)fd.rlim_cur, (unsigned long long)fd.rlim_max);
    parse_cases();
    switch_cases(dir);
    rewrite_cases();
    wrap_cases();
    fs_cases();
    vfs_cases(dir);
    init_cases(dir);
    rt_up_fixes_selftest(dir);
    hook_target_cases();
    if (game) {
        char p[PATH_MAX];
        snprintf(p, sizeof p, "%s/Bin/loader/loader.ini", game);
        rt_up_switches_reset();
        int n = rt_up_switches_read(p);
        check(n >= 60, "the game root's loader.ini parses", "%d keys from %s", n, p);
    }
    char cmd[PATH_MAX + 32];
    snprintf(cmd, sizeof cmd, "chmod -R u+w '%s'; rm -rf '%s'", dir, dir);
    if (system(cmd)) printf("note: scratch folder %s was not removed\n", dir);
    rt_up_set_mod(saved_mod);
    printf("up selftest: %d PASS, %d FAIL\n", s_pass, s_fail);
    fflush(stdout);
    return s_fail ? 1 : 0;
}
