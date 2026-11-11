#ifndef RT_H
#define RT_H

#include <stddef.h>
#include <stdint.h>

#define RT_APP_TITLE "Vampire Bloodlines"
#define RT_SUPPORT_NAME "Vampire Bloodlines"
#define RT_ARENA_SIZE 0x100000000ull
#define RT_ARENA_GUARD 0x10000u
#define RT_TIB 0x00020000u
#define RT_STATE 0x00030000u
#define RT_STACK_LO 0x00100000u
#define RT_STACK_TOP 0x001FFF00u
#define RT_EXE_BASE 0x00400000u
#define RT_HEAP_LO 0x01000000u
#define RT_HEAP_HI 0x06000000u
#define RT_VIRT_LO 0x06000000u
#define RT_VIRT_HI 0x0C000000u
#define RT_GDT 0x1FF00000u
#define RT_GDT_SIZE 0x10000u
#define RT_ALLOC_LO 0x30000000u
#define RT_ALLOC_HI 0x78000000u
#define RT_PSEUDO_MOD 0x7E000000u
#define RT_GUEST_TOP 0x80000000u
#define RT_CALLBACK_RET 0xFDFFFFF0u
#define RT_SHIM_BASE 0xFE000000u
#define RT_SHIM_STRIDE 16u
#define RT_TSC_HZ 2000000000ull

#define RT_TEB_SIZE 0x1000u
#define RT_TEB_TLS_ARRAY 0x800u
#define RT_TEB_TLS_SLOTS 0xE10u
#define RT_TLS_SLOTS 64u

typedef void (*rt_fn)(void);

typedef struct {
    uint32_t va;
    uint32_t iat;
    const char *dll;
    const char *name;
    int32_t pop;
} rt_import;

typedef struct {
    uint32_t iat;
    uint32_t va;
    uint32_t kind;
    const char *dll;
    const char *name;
} rt_slot;

typedef struct {
    uint32_t va;
    const char *name;
} rt_intrinsic;

typedef struct {
    uint32_t va;
    const char *name;
} rt_fname;

typedef uint32_t (*rt_shim_fn)(const uint32_t *a);

enum { RT_SHIM_REAL, RT_SHIM_FAIL, RT_SHIM_BRIDGE = 3 };

typedef struct {
    const char *dll;
    const char *name;
    uint32_t pop;
    rt_shim_fn fn;
    uint32_t kind;
} rt_shim;

typedef struct {
    const rt_shim *rows;
    const uint32_t *n;
} rt_shim_table;

typedef struct {
    uint32_t heap_next;
    uint32_t virt_next;
    uint32_t last_error;
    uint32_t heap_handle;
    uint32_t cmdline;
    uint32_t env_a;
    uint32_t module_path;
    uint32_t cmd_args;
} rt_state;

enum { RT_SLOT_CODE, RT_SLOT_DATA, RT_SLOT_STUB, RT_SLOT_CELL };

typedef struct {
    const char *name;
    uint32_t base, size, entry, code_lo, code_hi;
    const unsigned char *image;
    const char *image_sha256;
    rt_fn (*lookup)(uint32_t va);
    int (*dispatch_init)(void);
    const rt_intrinsic *intrinsics;
    uint32_t nintrinsics;
    const rt_slot *slots;
    uint32_t nslots;
    const char *path;
    const char *const *deps;
    uint32_t ndeps;
    const rt_fname *fnames;
    uint32_t nfnames;
    uint32_t tls_start, tls_end, tls_index, tls_zero_fill;
} rt_module;

extern const rt_module *const rt_modules[];
extern const uint32_t rt_nmodules;
extern const rt_import *const rt_imports[];
extern const uint32_t rt_nimports;
extern const rt_shim_table rt_shim_tables[];
extern const uint32_t rt_nshim_tables;

void rt_fatal(int code, const char *fmt, ...) __attribute__((noreturn, format(printf, 2, 3)));
rt_state *rt_st(void);
uint32_t rt_alloc(uint32_t size);
void *rt_host(uint32_t va);
void *rt_arena_new(void);
void rt_arena_use(void *arena);
void rt_load(void);
void rt_reset_cpu(void);
void rt_call(uint32_t va);
const rt_shim *rt_bound(uint32_t i);
const rt_shim *rt_find_shim(const char *dll, const char *name);
void rt_simd_trap(const char *text, uint32_t va) __attribute__((noreturn));

enum { RT_OBJ_FILE = 1, RT_OBJ_FIND, RT_OBJ_EVENT, RT_OBJ_MUTEX, RT_OBJ_SEM, RT_OBJ_THREAD, RT_OBJ_MAPPING };

typedef struct rt_thread rt_thread;

typedef struct {
    int type;
    int refs;
    int manual, signalled;
    uint32_t owner, count, max;
    int abandoned;
    rt_thread *thread;
    int fd;
    int src;
    char *path;
    char **names;
    int *dirs;
    uint32_t n, pos, size;
} rt_obj;

struct rt_thread {
    uint32_t tid, handle, teb, stack_lo, stack_hi, start, param;
    uint32_t exit_code, last_caller, wait_n, wait_handles[4];
    int done, suspended, primary, waiting, killed;
    uint32_t wait_ms;
    const char *wait_kind;
    rt_thread *next;
};

void rt_stop(int code, uint32_t gva, const char *fmt, ...) __attribute__((noreturn, format(printf, 3, 4)));
void rt_exit(int code) __attribute__((noreturn));
void rt_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void rt_stage(int n, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void rt_guest_text(int src, const char *text, uint32_t n);
int rt_trace_open(const char *logdir);
void rt_trace_first(uint32_t i, uint32_t caller);
void rt_trace_on(int on);
int rt_tracing(void);
int rt_stage_reached(void);
void rt_set_marker(const char *marker);
void rt_note_pump_gap(uint64_t ns);
uint64_t rt_max_pump_gap_ms(void);
const char *rt_where(uint32_t va, char *buf, size_t n);
const rt_module *rt_owner(uint32_t va);
const rt_shim *rt_shim_at(uint32_t i, const char **dll, const char **name);
uint32_t rt_dynamic_shim(const char *dll, const char *name, const rt_shim *s);
uint32_t rt_last_back_edge(void);
void rt_seh_dump(void);
uint32_t rt_put_string(const char *s);

void rt_heap_init(uint32_t lo, uint32_t hi);
uint32_t rt_heap_alloc(uint32_t size, int zero);
int rt_heap_free(uint32_t p);
uint32_t rt_heap_realloc(uint32_t p, uint32_t size, uint32_t flags);
uint32_t rt_heap_size(uint32_t p);
int rt_heap_owns(uint32_t p);
uint32_t rt_pages_reserve(uint32_t size);
int rt_pages_release(uint32_t va);
int rt_pages_query(uint32_t va, uint32_t *base, uint32_t *size);
uint32_t rt_heap_high_water(void);
uint32_t rt_heap_failures(void);
void rt_heap_selftest(void);

uint64_t rt_now_ns(void);
uint32_t rt_get_last_error(void);
void rt_set_last_error(uint32_t e);
rt_thread *rt_self(void);
int rt_is_primary(void);
void rt_thread_primary(uint32_t teb);
uint32_t rt_thread_create(uint32_t start, uint32_t param, uint32_t stack, uint32_t flags, uint32_t *tid);
void rt_thread_exit(uint32_t code) __attribute__((noreturn));
uint32_t rt_thread_resume(uint32_t h);
uint32_t rt_thread_terminate(uint32_t h, uint32_t code);
uint32_t rt_teb_new(uint32_t teb, uint32_t tid, uint32_t stack_lo, uint32_t stack_hi);
void rt_tls_zero_slot(uint32_t slot);
uint32_t rt_guest_call(uint32_t va, int n, const uint32_t *args);
void rt_lock_take(void);
void rt_block_begin(const char *kind, uint32_t ms);
void rt_block_end(void);
void rt_yield(void);
extern volatile uint64_t rt_bridge_t0;
extern const char *volatile rt_bridge_name;
int rt_hang_due(uint64_t elapsed_ms, int player);
void rt_watchdog_start(int until, double close_after_s, double capture_after_s);
void rt_stage8_reached(void);
void rt_note_event(void);
void rt_close_requested(void);
int rt_close_take(void);
void rt_engine_exit(void);

rt_obj *rt_obj_new(int type);
uint32_t rt_handle_new(rt_obj *o);
rt_obj *rt_handle_get(uint32_t h, int type);
int rt_handle_close(uint32_t h);
uint32_t rt_handle_dup(uint32_t h);
uint32_t rt_wait(uint32_t n, const uint32_t *handles, int all, uint32_t ms);
void rt_obj_lock(void);
void rt_obj_unlock(void);
void rt_obj_broadcast(void);
void rt_obj_timedwait(uint32_t ms);
void rt_dump_waits(void);

int rt_vfs_init(const char *game, const char *overlay);
uint32_t rt_vfs_resolve(const char *guest, char *host, size_t n, int *exists, int *in_overlay, char *rel, size_t reln);
int rt_vfs_open(const char *host, int flags, int mode);
int rt_vfs_cow(const char *rel, char *host, size_t n);
const char *rt_vfs_cwd(void);
uint32_t rt_vfs_set_cwd(const char *guest);
int rt_vfs_full(const char *guest, char *out, size_t n);
int rt_vfs_list(const char *guest_dir, char ***names, int **dirs, uint32_t *err);
void rt_vfs_dirty(const char *host_path);

typedef struct {
    uint32_t hwnd, cls, wndproc, style, exstyle, userdata, parent, id, instance;
    uint32_t extra[16];
    int x, y, w, h, visible, toplevel;
    void *sdl;
    char title[256];
} rt_win;

int rt_window_init(void);
int rt_window_launch_full(void);
rt_win *rt_win_new(void);
rt_win *rt_win_get(uint32_t hwnd);
void rt_win_free(rt_win *w);
void rt_win_sdl(rt_win *w);
void rt_win_resize(rt_win *w, int cw, int ch);
void rt_win_show(rt_win *w, int show);
void rt_msg_post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp);
int rt_msg_take(uint32_t hwnd, uint32_t lo, uint32_t hi, int remove, uint32_t out[7]);
int rt_window_wait_event(uint32_t ms);
void rt_desktop_size(int *w, int *h);
uint32_t rt_window_display(void);
void rt_window_display_set(uint32_t id);
void rt_display_density_set(uint32_t id, double d);
int rt_desktop_usable(int *x, int *y, int *w, int *h);
void rt_desktop_usable_set(int dw, int dh, int w, int h, int title);
void rt_window_place(void *sdl_window, int cw, int ch);
int rt_vfs_mkparents(const char *host);
const char *rt_vfs_overlay(void);
void rt_pump(int force);
int rt_pump_due(void);
void rt_window_push_close(void);
void rt_altstack(void);

int rt_reg_load(const char *path);

uint32_t rt_module_load(const char *name, int start, uint32_t *err);
uint32_t rt_module_export(uint32_t base, uint32_t name_or_ord);
void rt_module_disable_thread_calls(uint32_t base);
void rt_modules_thread_attach(void);

uint32_t rt_cp1252_to_upper(uint32_t c);
uint32_t rt_cp1252_to_lower(uint32_t c);
int rt_stricmp(const char *a, const char *b);
const char *rt_gstr(uint32_t va);
int rt_span_ok(uint32_t va, uint64_t n);

enum { K_D3D, K_DEVICE, K_SURFACE, K_VOLUME, K_TEXTURE, K_VOLTEX, K_CUBE, K_VB, K_IB, K_SWAP, K_SB, K_DECL, K_VS, K_PS, K_QUERY, K_N };

typedef struct {
    const rt_shim *rows;
    uint32_t n;
} rt_d3d9_table;

extern const rt_d3d9_table rt_d3d9_tables[K_N];
extern const rt_shim rt_d3d9_null_shims[];
uint64_t rt_pitch_of(uint32_t f, uint32_t w);
uint64_t rt_rows_of(uint32_t f, uint32_t h);

enum { RT_GFX_NULL, RT_GFX_DXVK };

int rt_gfx_init(int mode, const char *overlay);
int rt_gfx_mode(void);
uint64_t rt_gfx_window_flags(void);
uint64_t rt_gfx_flags_for(int mode, int native);
int rt_aspect_launch_native(void);
void rt_aspect_options_value(const char *name, uint32_t v);
void rt_gfx_set_capture(double s);
void rt_gfx_menu(void);
void rt_gfx_report(void);
int rt_gfx_selftest(const char *overlay);
rt_shim_fn rt_gfx_method(const char *name);
uint32_t rt_gfx_live(void);
int rt_gfx_readback(void *dev, uint32_t **pixels, uint32_t *w, uint32_t *h);
void *rt_gfx_device_window(void);
void rt_gfx_gap(uint64_t gap_ns, int describe, char *where, size_t wn, char *detail, size_t dn);
void *rt_gfx_host(uint32_t proxy);
void rt_gfx_pp_in(uint32_t g, void *h, void *hwnd);
void rt_gfx_pp_size(int windowed, int gw, int gh, uint32_t *bw, uint32_t *bh);
void rt_gfx_pp_out(const void *h, uint32_t g, uint32_t hwnd);
void rt_gfx_lr_out(const void *h, uint32_t g, uint32_t bits);
uint64_t rt_stage11_ns(void);
uint64_t rt_frames_start_ns(void);
void rt_set_guest_quiet(int quiet);
const char *const *rt_default_args(int quiet, int *n);
void rt_window_set_display(int n);
uint64_t rt_pump_ns(void);
uint64_t rt_lock_wait_ns(void);
uint64_t rt_sleep_ns(void);
uint64_t rt_cpu_swaps(void);

typedef struct {
    int sx, sy, sw, sh, dx, dy, dw, dh;
} rt_present_rects;
int rt_gfx_present_rects(rt_present_rects *r);
void rt_gfx_capture_named(const char *name);
int rt_gfx_capture_pending(void);
int rt_cp1252_from_wide(uint16_t w);

union SDL_Event;
void rt_input_main_window(uint32_t hwnd, void *sdl);
void rt_input_event(const union SDL_Event *e);
void rt_input_focus(int on);
void rt_input_pump_end(void);
void rt_input_taken(const uint32_t m[7]);
void rt_input_post(uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp);
void rt_input_get_cursor(int32_t *x, int32_t *y);
void rt_input_set_cursor(int32_t x, int32_t y);
void rt_input_client_cursor(int32_t *x, int32_t *y);
int rt_input_key_down(uint32_t vk);
uint32_t rt_input_cursor_shape(uint32_t handle);
int rt_input_show_cursor(int show);
void rt_input_capture(uint32_t hwnd);
void rt_input_geometry(rt_present_rects *r, float *density);
uint32_t rt_input_window_id(void);
int rt_input_main_shown(void);
int rt_input_script_load(const char *path);
void rt_input_script_step(void);
int rt_input_selftest(void);
void rt_input_report(void);

typedef struct rt_wav rt_wav;

void rt_audio_config(int nosound, int capture);
int rt_audio_nosound(void);
int rt_audio_init(void);
rt_wav *rt_wav_open(const char *name, int rate, int ch);
void rt_wav_write(rt_wav *w, const void *p, uint32_t n);
void rt_wav_close(rt_wav *w);
const char *rt_wav_path(const rt_wav *w);
int rt_audio_go_silent(const char *why);
void rt_dsound_report(void);
void rt_dsound_focus(int on);
void rt_dsound_cases(void (*result)(int ok, const char *fmt, ...));
void rt_audio_report(void);
int rt_audio_selftest(const char *game);
void rt_mp3_report(void);
int rt_mp3_selftest(int *pass);
void rt_bink_config(int novid);
uint32_t rt_bink_shown(uint32_t movie);
int rt_bink_selftest(int *pass);
int rt_gfx_movie_ready(void);
int rt_gfx_movie_blit(const void *xrgb, uint32_t w, uint32_t h, uint32_t pitch, uint32_t dw, uint32_t dh);
void rt_gfx_movie_end(void);
void rt_set_player(int on);
int rt_player(void);
uint64_t rt_stage6_ns(void);

void rt_devmenu_init(void);
void rt_dev_request(int source);
int rt_dev_take(void);
void rt_dev_apply(void);
int rt_dev_chord_key(uint32_t scancode);

typedef struct {
    char lib[1024], loader[1024], icd[1024];
    int bundled;
} rt_gfx_paths;
void rt_gfx_resolve_paths(const char *exe, const char *home, const char *e_lib, const char *e_loader, const char *e_icd,
                          rt_gfx_paths *o);

int rt_up_init(const char *args);
int rt_up_parse_mod(const char *args, char *mod, size_t n);
void rt_up_set_mod(const char *name);
const char *rt_up_mod(void);
const char *rt_up_moddir_script(void);
uint32_t rt_up_slot(const char *module, const char *dll, const char *name);
int rt_up_wrap_import(const char *module, const char *dll, const char *name, const rt_shim *shim, uint32_t *orig);
int rt_up_selftest(const char *game, const char *overlay);
int rt_py21_compile(const char *dir);
int rt_py21_moddir(const char *script);
uint32_t rt_up_switch_count(void);
int rt_up_switch_row(uint32_t i, const char **section, const char **key, int *supported);
void rt_up_switches_reset(void);
int rt_up_switches_read(const char *path);
double rt_up_switch_num(const char *section, const char *key);
int rt_up_switch_on(const char *section, const char *key);
void rt_up_switches_log(void);

enum { RT_HOOK_ABSENT, RT_HOOK_WRITTEN, RT_HOOK_BAKED };
uint32_t rt_hook_va(const char *module, uint32_t rva);
int rt_hook_patch(const char *id, uint32_t va, const uint8_t *old, const uint8_t *new_, uint32_t n);
int rt_hook_same(const uint8_t *p, const uint8_t *old, uint32_t n, int absoff);
int rt_hook_site(const char *id, uint32_t va, const uint8_t *old, uint32_t n, int absoff, uint8_t opcode, uint32_t cell,
                 const rt_shim *shim);
int rt_hook_ptr(const char *id, uint32_t va, uint32_t orig, uint32_t cell, const rt_shim *shim);
int rt_hook_revert(const char *id);
void rt_hook_revert_all(void);
int rt_hook_claimed(uint32_t va, uint32_t n);
uint32_t rt_hook_count(void);
int rt_hook_info(uint32_t i, const char **id, uint32_t *va, int *state);

extern const rt_shim rt_upk_door, rt_upk_hist_a, rt_upk_hist_b, rt_upk_hist_c, rt_upk_model;
extern const rt_shim rt_upk_con_a, rt_upk_con_b, rt_upk_con_c, rt_upk_con_d;
extern uint32_t (*rt_up_gc)(uint32_t, int, const uint32_t *);
uint32_t rt_up_call_this(uint32_t fn, uint32_t self, int n, const uint32_t *args);
uint32_t rt_up_tramp(const char *module, uint32_t rva, uint32_t skip);
uint32_t rt_up_gnew(const char *s);
void rt_up_console_load(void);
void rt_up_console_selftest(void);
extern const rt_shim rt_upk_nodoor, rt_upk_drop, rt_upk_stack_find, rt_upk_stack_pick, rt_upk_stack_skip;
extern const rt_shim rt_upk_corpse_think, rt_upk_corpse_fade, rt_upk_corpse_mark, rt_upk_corpse_clear, rt_upk_altf4;
void rt_up_tweaks_load(void);
void rt_up_tweaks_selftest(void);
extern const rt_shim rt_upk_cs_hide, rt_upk_cs_give, rt_upk_cs_owned, rt_upk_cs_keys, rt_upk_cs_barter;
void rt_up_container_load(void);
void rt_up_container_selftest(void);
void rt_up_model_load(void);
void rt_up_model_selftest(void);
void rt_intrinsic_selftest(void);
void rt_kernel32_text_selftest(void);
void rt_up_model_norm(const char *src, char *dst);
uint32_t rt_up_model_find(uint32_t self, const char *name);
const char *rt_up_plugin_ini(const char *plugin);
void rt_up_plugins_load(void);
void rt_up_plugins_selftest(void);

void rt_up_check(int ok, const char *name, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
int rt_up_plugin_known(const char *plugin);
void rt_up_plugins_reset(void);
void rt_up_plugin_set(const char *plugin, const char *key, double v);
double rt_up_plugin_num(const char *plugin, const char *key);
int rt_up_plugin_on(const char *plugin, const char *key);
uint32_t rt_up_plugin_count(void);
int rt_up_plugin_row(uint32_t i, const char **plugin, const char **key);
int rt_up_plugin_read(const char *path, const char *plugin);
const char *rt_up_build_sha(void);
int rt_up_list_fixes(const char *game);
void rt_up_fixes_apply(void);
void rt_up_fixes_guard(void);
void rt_up_fixes_selftest(const char *dir);
int rt_up_default_clan(const char *in, size_t n, char *out, size_t outn);
void rt_aspect_engine_init(int w, int h);
void rt_aspect_engine_set(int w, int h, int on);

#endif
