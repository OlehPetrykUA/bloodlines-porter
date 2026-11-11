#include "recomp_types.h"
#include "rt.h"
#include "toggles.h"

#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>

static int list_imports(void)
{
    for (uint32_t i = 0; i < rt_nimports; i++)
        printf("%s!%s 0x%08X %s\n", rt_imports[i]->dll, rt_imports[i]->name, rt_imports[i]->va,
               rt_bound(i) ? "shim" : "stub");
    return 0;
}

static int probe_stub(const char *dll, const char *name, char **words)
{
    for (uint32_t i = 0; i < rt_nimports; i++)
        if (!strcasecmp(rt_imports[i]->dll, dll) && !strcmp(rt_imports[i]->name, name)) {
            rt_reset_cpu();
            for (int k = 3; k >= 0; k--) {
                g_esp -= 4;
                MEM32(g_esp) = (uint32_t)strtoul(words[k], NULL, 0);
            }
            g_esp -= 4;
            MEM32(g_esp) = 0x0000BEEFu;
            rt_call(rt_imports[i]->va);
            fprintf(stderr, "rt: stub %s!%s returned\n", dll, name);
            return 1;
        }
    fprintf(stderr, "rt: no import %s!%s\n", dll, name);
    return 1;
}

static int check(int quiet, uint32_t *total)
{
    uint64_t bytes = 0;
    uint32_t n[4] = {0}, shims = 0, bad = 0;
    for (uint32_t m = 0; m < rt_nmodules; m++) {
        const rt_module *mod = rt_modules[m];
        bytes += mod->size;
        for (uint32_t i = 0; i < mod->nslots; i++) {
            const rt_slot *s = &mod->slots[i];
            uint32_t v = MEM32(s->iat);
            const rt_module *ex = rt_owner(v);
            int ok = v == s->va;
            if (s->kind == RT_SLOT_CODE) ok &= recomp_lookup(v) != NULL || recomp_lookup_manual(v) != NULL;
            else if (s->kind == RT_SLOT_DATA) ok &= ex != NULL && ex != mod;
            else if (s->kind == RT_SLOT_STUB) {
                ok &= recomp_lookup_kernel(v) != NULL;
                shims += ok && rt_bound((v - RT_SHIM_BASE) / RT_SHIM_STRIDE) != NULL;
            } else ok &= ex == NULL && v < RT_VIRT_HI;
            if (ok) n[s->kind]++;
            else {
                bad++;
                printf("unbound %s slot 0x%08X (%s!%s): holds 0x%08X, planned 0x%08X\n", mod->name, s->iat, s->dll,
                       s->name, v, s->va);
            }
        }
    }
    if (total) *total = n[0] + n[1] + n[2] + n[3] + bad;
    if (!quiet)
        printf("mapped %u images (%llu bytes); iat slots: %u code, %u data, %u shim, %u stub, %u cell, %u unbound; "
               "guest code run: none\n", rt_nmodules, (unsigned long long)bytes, n[0], n[1], shims, n[2] - shims, n[3],
               bad);
    return bad ? 1 : 0;
}

static int probe_load(int argc, char **argv)
{
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    uint32_t err = 0, h = rt_module_load(argv[2], 0, &err);
    printf("load %s 0x%08X error %u\n", argv[2], h, h ? 0 : err);
    for (int i = 3; i < argc; i++) {
        uint32_t arg = argv[i][0] == '#' ? (uint32_t)strtoul(argv[i] + 1, NULL, 0) : rt_put_string(argv[i]);
        uint32_t r = h ? rt_module_export(h, arg) : 0;
        printf("proc %s 0x%08X error %u\n", argv[i], r, r ? 0 : 127);
    }
    return 0;
}

static int mkdir_p(const char *p)
{
    char b[PATH_MAX];
    snprintf(b, sizeof b, "%s", p);
    for (char *c = b + 1; *c; c++)
        if (*c == '/') {
            *c = 0;
            mkdir(b, 0755);
            *c = '/';
        }
    return mkdir(b, 0755) && errno != EEXIST ? -1 : 0;
}

static void real_path(const char *p, char *out)
{
    char head[PATH_MAX], tail[PATH_MAX] = "";
    snprintf(head, sizeof head, "%s", p);
    while (!realpath(head, out)) {
        char *sl = strrchr(head, '/');
        if (!sl || sl == head) {
            snprintf(out, PATH_MAX, "%s%s", head, tail);
            return;
        }
        char t[PATH_MAX];
        snprintf(t, sizeof t, "%s%s", sl, tail);
        snprintf(tail, sizeof tail, "%s", t);
        *sl = 0;
    }
    size_t n = strlen(out);
    snprintf(out + n, PATH_MAX - n, "%s", tail);
}

static int refuse(const char *why)
{
    fprintf(stderr, "rt: run refused: %s\n", why);
    return 1;
}

static int rm_cb(const char *p, const struct stat *s, int flag, struct FTW *w)
{
    (void)s;
    (void)flag;
    (void)w;
    return remove(p);
}

static void default_game(char *out, size_t n, const char *fallback)
{
    const char *g = getenv("BN_GAME");
    snprintf(out, n, "%s", g && *g ? g : fallback);
}

static int audio_selftest(int argc, char **argv)
{
    char game[PATH_MAX], overlay[PATH_MAX] = "";
    default_game(game, sizeof game, ".");
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--game") && i + 1 < argc) snprintf(game, sizeof game, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--overlay") && i + 1 < argc) snprintf(overlay, sizeof overlay, "%s", argv[++i]);
        else return refuse("usage: --audio-selftest [--game DIR] [--overlay DIR]");
    }
    int temp = !overlay[0];
    if (temp) {
        const char *tmp = getenv("TMPDIR");
        snprintf(overlay, sizeof overlay, "%s/bl-audio-selftest-XXXXXX", tmp && *tmp ? tmp : "/tmp");
        if (!mkdtemp(overlay)) return refuse("temporary overlay cannot be created");
    } else if (mkdir_p(overlay)) return refuse("overlay folder cannot be created");
    if (rt_vfs_init(game, overlay)) rt_vfs_init(".", overlay);
    int r = rt_audio_selftest(game);
    if (temp) nftw(overlay, rm_cb, 8, FTW_DEPTH | FTW_PHYS);
    return r;
}

static int up_selftest(int argc, char **argv)
{
    const char *game = NULL, *overlay = NULL;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--game") && i + 1 < argc) game = argv[++i];
        else if (!strcmp(argv[i], "--overlay") && i + 1 < argc) overlay = argv[++i];
        else return refuse("usage: --up-selftest [--game DIR] [--overlay DIR]");
    }
    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    rt_reset_cpu();
    return rt_up_selftest(game, overlay);
}

static int py21_compile(int argc, char **argv)
{
    char game[PATH_MAX], overlay[PATH_MAX] = "", modarg[512];
    const char *dir = NULL, *mod = NULL;
    default_game(game, sizeof game, ".");
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "--game") && i + 1 < argc) snprintf(game, sizeof game, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--mod") && i + 1 < argc) mod = argv[++i];
        else if (!dir && argv[i][0] != '-') dir = argv[i];
        else return refuse("usage: --py21-compile DIR [--game DIR] [--mod NAME]");
    }
    if (!dir) return refuse("usage: --py21-compile DIR [--game DIR] [--mod NAME]");
    if (mod) {
        char name[128];
        snprintf(modarg, sizeof modarg, "-game %s", mod);
        if (rt_up_parse_mod(modarg, name, sizeof name) <= 0) return refuse("--mod needs a plain folder name");
        rt_up_set_mod(name);
    }
    const char *tmp = getenv("TMPDIR");
    snprintf(overlay, sizeof overlay, "%s/bl-py21-XXXXXX", tmp && *tmp ? tmp : "/tmp");
    if (!mkdtemp(overlay)) return refuse("temporary overlay cannot be created");
    if (rt_vfs_init(game, overlay)) rt_vfs_init(".", overlay);
    int r = rt_py21_compile(dir);
    if (!r && mod) r = rt_py21_moddir(rt_up_moddir_script());
    nftw(overlay, rm_cb, 8, FTW_DEPTH | FTW_PHYS);
    return r ? 1 : 0;
}

static rlim_t s_fd_was, s_fd_now;

static void raise_fd_limit(void)
{
    struct rlimit r;
    if (getrlimit(RLIMIT_NOFILE, &r)) return;
    s_fd_was = s_fd_now = r.rlim_cur;
    rlim_t want = r.rlim_max < OPEN_MAX ? r.rlim_max : OPEN_MAX;
    if (r.rlim_cur >= want) return;
    r.rlim_cur = want;
    if (!setrlimit(RLIMIT_NOFILE, &r)) s_fd_now = want;
}

static int run(int argc, char **argv)
{
    int until = 0, gfx = RT_GFX_DXVK, display = -1, quiet = 0, nosound = 0, capture = 0, player = 0, novid = 0;
    const char *display_arg = "", *aspect_arg = NULL;
    double close_after = 0, capture_after = 0;
    char overlay[PATH_MAX], game[PATH_MAX];
    const char *home = getenv("HOME");
    snprintf(overlay, sizeof overlay, "%s/Library/Application Support/" RT_SUPPORT_NAME, home ? home : ".");
    default_game(game, sizeof game, "");
    int i = 2;
    for (; i < argc; i++) {
        if (!strcmp(argv[i], "--until") && i + 1 < argc && !strcmp(argv[i + 1], "console")) {
            until = 1;
            i++;
        } else if (!strcmp(argv[i], "--until") && i + 1 < argc && !strcmp(argv[i + 1], "menu")) {
            until = 2;
            i++;
        } else if (!strcmp(argv[i], "--gfx") && i + 1 < argc && !strcmp(argv[i + 1], "null")) {
            gfx = RT_GFX_NULL;
            i++;
        } else if (!strcmp(argv[i], "--gfx") && i + 1 < argc && !strcmp(argv[i + 1], "dxvk")) {
            gfx = RT_GFX_DXVK;
            i++;
        } else if (!strcmp(argv[i], "--display") && i + 1 < argc) {
            char *end;
            display_arg = argv[++i];
            long d = strtol(display_arg, &end, 10);
            display = *end || !*display_arg || d < 0 || d > 64 ? -2 : (int)d;
            rt_window_set_display(display);
        } else if (!strcmp(argv[i], "--guest-quiet")) {
            quiet = 1;
        } else if (!strcmp(argv[i], "--nosound")) {
            nosound = 1;
        } else if (!strcmp(argv[i], "--sound")) {
        } else if (!strcmp(argv[i], "--audio-capture")) {
            capture = 1;
        } else if (!strcmp(argv[i], "--player")) {
            player = 1;
        } else if (!strcmp(argv[i], "--novid")) {
            novid = 1;
        } else if (!strcmp(argv[i], "--aspect") && i + 1 < argc) {
            aspect_arg = argv[++i];
        } else if (!strcmp(argv[i], "--capture-after") && i + 1 < argc) {
            capture_after = atof(argv[++i]);
        } else if (!strcmp(argv[i], "--close-after") && i + 1 < argc) {
            close_after = atof(argv[++i]);
        } else if (!strcmp(argv[i], "--input-script") && i + 1 < argc) {
            const char *sp = argv[++i];
            int bad = rt_input_script_load(sp);
            char why[PATH_MAX + 64];
            if (bad < 0) snprintf(why, sizeof why, "input script %s cannot be read", sp);
            else snprintf(why, sizeof why, "input script %s line %d does not parse", sp, bad);
            if (bad) return refuse(why);
        } else if (!strcmp(argv[i], "--overlay") && i + 1 < argc) {
            snprintf(overlay, sizeof overlay, "%s", argv[++i]);
        } else if (!strcmp(argv[i], "--game") && i + 1 < argc) {
            snprintf(game, sizeof game, "%s", argv[++i]);
        } else if (!strcmp(argv[i], "--")) {
            i++;
            break;
        } else {
            fprintf(stderr, "rt: unknown --run option %s\n", argv[i]);
            return 2;
        }
    }
    rt_audio_config(nosound, capture);
    rt_bink_config(novid);
    rt_set_player(player);
    rt_set_guest_quiet(quiet);
    int rest = i;
    char path[PATH_MAX], rgame[PATH_MAX], roverlay[PATH_MAX];
    if (!game[0]) return refuse("no game folder: pass --game DIR or set BN_GAME");
    if (!realpath(game, rgame)) return refuse("game folder not found");
    if (overlay[0] != '/') {
        char cwd[PATH_MAX], abs[PATH_MAX];
        if (!getcwd(cwd, sizeof cwd)) return refuse("current directory unknown");
        snprintf(abs, sizeof abs, "%s/%s", cwd, overlay);
        snprintf(overlay, sizeof overlay, "%s", abs);
    }
    size_t ol = strlen(overlay);
    if (strstr(overlay, "/../") || (ol >= 3 && !strcmp(overlay + ol - 3, "/..")))
        return refuse("the overlay path contains a .. component");
    real_path(overlay, roverlay);
    size_t gl = strlen(rgame);
    if (!strncmp(roverlay, rgame, gl) && (roverlay[gl] == 0 || roverlay[gl] == '/'))
        return refuse("the overlay is the game folder or inside it");
    if (mkdir_p(overlay)) return refuse("overlay folder cannot be created");
    snprintf(path, sizeof path, "%s/files", overlay);
    if (mkdir_p(path)) return refuse("overlay files folder cannot be created");
    snprintf(path, sizeof path, "%s/logs", overlay);
    if (mkdir_p(path)) return refuse("overlay logs folder cannot be created");
    if (rt_trace_open(path)) return refuse("run log cannot be opened");
    rt_trace_on(1);
    rt_log("open-file limit %llu (was %llu)", (unsigned long long)s_fd_now, (unsigned long long)s_fd_was);
    if (nosound) rt_log("audio off (--nosound)");
    if (player) rt_log("player mode");
    snprintf(path, sizeof path, "%s/registry.reg", overlay);
    if (access(path, F_OK)) {
        FILE *f = fopen(path, "w");
        if (!f) return refuse("registry.reg cannot be created");
        fputs("REGEDIT4\n", f);
        fclose(f);
    }
    if (rt_vfs_init(game, overlay)) return refuse("game folder not found");
    rt_settings_load(overlay, player);
    if (aspect_arg && rt_toggle_parse(RT_TOG_ASPECT, aspect_arg)) return refuse("--aspect takes 16:9, 21:9, fit, engine, W:H or WxH");
    int wi = display < -1 ? -2 : rt_window_init();
    if (wi == -2) {
        char why[128];
        snprintf(why, sizeof why, "display %s not found", display_arg);
        return refuse(why);
    }
    if (wi) return refuse("SDL_Init failed");
    char args[4096] = "";
    size_t o = 0;
    const char *marker = NULL;
    int nd = 0;
    const char *const *k_default_args = rt_default_args(quiet, &nd);
    for (int k = 0; k < nd + argc - rest; k++) {
        const char *s = k < nd ? k_default_args[k] : argv[rest + k - nd];
        o += (size_t)snprintf(args + o, sizeof args - o, "%s%s", o ? " " : "", s);
        if (o >= sizeof args) return refuse("arguments too long");
        int prev = k > 0 && !strcmp(k - 1 < nd ? k_default_args[k - 1] : argv[rest + k - 1 - nd], "+echo");
        if (prev) marker = s;
    }
    char cmd[4200];
    snprintf(cmd, sizeof cmd, "\"C:\\VTMB\\vampire.exe\" %s", args);
    if (marker) rt_set_marker(marker);
    rt_devmenu_init();
    rt_hud_init();
    if (rt_gfx_init(gfx, overlay)) return refuse("graphics files missing (see the rt: gfx line above); --gfx null runs without them");
    if (capture_after > 0) rt_gfx_set_capture(capture_after);
    rt_stage(1, "gfx %s%s %s", gfx == RT_GFX_DXVK ? "dxvk" : "null", quiet ? " guest-quiet" : "", cmd);

    rt_arena_use(rt_arena_new());
    rt_load();
    uint32_t slots = 0;
    if (check(1, &slots)) return refuse("unbound IAT slots");
    rt_stage(2, "%u images, %u slots, 0 unbound", rt_nmodules, slots);

    rt_heap_init(RT_ALLOC_LO, RT_ALLOC_HI);
    char exe[PATH_MAX];
    snprintf(exe, sizeof exe, "%s/vampire.exe", game);
    int fd = open(exe, O_RDONLY);
    if (fd < 0 || read(fd, rt_host(RT_EXE_BASE), 0x400) != 0x400) return refuse("vampire.exe headers unreadable");
    close(fd);
    if (rt_up_init(args)) return refuse("-game needs a plain folder name");
    int lw, lh;
    rt_aspect_launch_size(&lw, &lh);
    rt_aspect_engine_init(lw, lh);
    mprotect(rt_host(RT_EXE_BASE), 0x4000, PROT_READ);
    for (uint32_t m = 0; m < rt_nmodules; m++)
        if (rt_modules[m]->tls_end > rt_modules[m]->tls_start) MEM32(rt_modules[m]->tls_index) = 0;
    rt_state *st = rt_st();
    st->cmdline = rt_put_string(cmd);
    st->cmd_args = st->cmdline + (uint32_t)(strlen(cmd) - strlen(args));
    static const char env[] = "PATH=C:\\VTMB\\Bin;C:\\WINDOWS\\system32;C:\\WINDOWS\0SystemRoot=C:\\WINDOWS\0"
                              "windir=C:\\WINDOWS\0\0";
    st->env_a = rt_heap_alloc(sizeof env, 0);
    memcpy(rt_host(st->env_a), env, sizeof env);
    snprintf(path, sizeof path, "%s/registry.reg", overlay);
    if (rt_reg_load(path)) return refuse("registry.reg does not parse");
    rt_reset_cpu();
    rt_thread_primary(RT_TIB);
    rt_watchdog_start(until, close_after, capture_after);
    rt_stage(3, "tid 0x%X teb 0x%08X stack 0x%08X-0x%08X", rt_self()->tid, RT_TIB, RT_STACK_LO, RT_STACK_TOP);

    uint32_t err = 0, launcher = rt_module_load("C:\\VTMB\\Bin\\launcher.dll", 1, &err);
    if (!launcher) rt_stop(5, 0, "launcher.dll did not load (error %u)", err);
    uint32_t entry = rt_module_export(launcher, rt_put_string("LauncherMain"));
    if (!entry) rt_stop(5, 0, "launcher.dll has no LauncherMain export");
    uint32_t a[4] = {RT_EXE_BASE, 0, st->cmd_args, 1};
    rt_stage(5, "0x%08X args 0x%08X 0x%08X 0x%08X %u \"%s\"", entry, a[0], a[1], a[2], a[3], args);
    uint32_t r = rt_guest_call(entry, 4, a);
    rt_log("guest LauncherMain returned %u", r);
    rt_exit((int)r);
}

int main(int argc, char **argv)
{
    raise_fd_limit();
    if (argc >= 2 && !strcmp(argv[1], "--run")) return run(argc, argv);
    if (argc == 2 && !strcmp(argv[1], "--input-selftest")) return rt_input_selftest();
    if (argc >= 3 && !strcmp(argv[1], "--m9-selftest")) return rt_m9_selftest(argc, argv);
    rt_arena_use(rt_arena_new());
    rt_load();
    if (argc == 2 && !strcmp(argv[1], "--list-imports")) return list_imports();
    if ((argc == 2 || (argc == 4 && !strcmp(argv[2], "--overlay"))) && !strcmp(argv[1], "--gfx-selftest")) {
        char o[PATH_MAX];
        snprintf(o, sizeof o, "%s", argc == 4 ? argv[3] : "gfx-selftest");
        snprintf(o + strlen(o), sizeof o - strlen(o), "/logs");
        if (mkdir_p(o)) return refuse("overlay logs folder cannot be created");
        o[strlen(o) - 5] = 0;
        return rt_gfx_selftest(o);
    }
    if (argc >= 2 && !strcmp(argv[1], "--audio-selftest")) return audio_selftest(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "--up-selftest")) return up_selftest(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "--up-list-fixes")) {
        if (argc != 2 && !(argc == 4 && !strcmp(argv[2], "--game"))) return refuse("usage: --up-list-fixes [--game DIR]");
        return rt_up_list_fixes(argc == 4 ? argv[3] : NULL);
    }
    if (argc >= 3 && !strcmp(argv[1], "--py21-compile")) return py21_compile(argc, argv);
    if (argc == 8 && !strcmp(argv[1], "--probe-stub")) return probe_stub(argv[2], argv[3], argv + 4);
    if (argc >= 3 && !strcmp(argv[1], "--probe-load")) return probe_load(argc, argv);
    if (argc == 3 && !strcmp(argv[1], "--probe-read")) {
        uint32_t v = MEM32((uint32_t)strtoul(argv[2], NULL, 0));
        printf("read 0x%08X\n", v);
        return 1;
    }
    if (argc != 1) {
        fprintf(stderr,
                "usage: %s [--list-imports | --probe-stub DLL NAME w0 w1 w2 w3 | --probe-read VA | --probe-load PATH "
                "[NAME|#N]... | --gfx-selftest [--overlay DIR] | --input-selftest | --m9-selftest CASE [--player] --overlay DIR | --audio-selftest [--game DIR] [--overlay DIR] | --up-selftest [--game DIR] [--overlay DIR] | --up-list-fixes [--game DIR] | --py21-compile DIR [--game DIR] [--mod NAME] | --run [--gfx dxvk|null] [--guest-quiet] [--nosound] [--audio-capture] [--player] [--novid] [--aspect 16:9|21:9|fit|engine|WxH] [--until console|menu] [--capture-after S] "
                "[--close-after S] [--display N] [--input-script FILE] [--overlay DIR] [--game DIR] [-- ARGS]]\n",
                argv[0]);
        return 2;
    }
    return check(0, NULL);
}
