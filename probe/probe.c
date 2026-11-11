#include <vulkan/vulkan.h>
#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>
#include <dirent.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef int32_t HR;
typedef void *obj;

enum {
    U_RELEASE = 2,
    D9_GETADAPTERIDENTIFIER = 5,
    D9_CREATEDEVICE = 16,
    DEV_GETAVAILABLETEXTUREMEM = 4,
    DEV_PRESENT = 17,
    DEV_CREATETEXTURE = 23,
    DEV_GETRENDERTARGETDATA = 32,
    DEV_CREATEOFFSCREENPLAINSURFACE = 36,
    DEV_GETRENDERTARGET = 38,
    DEV_BEGINSCENE = 41,
    DEV_ENDSCENE = 42,
    DEV_CLEAR = 43,
    DEV_SETRENDERSTATE = 57,
    DEV_SETTEXTURE = 65,
    DEV_SETSAMPLERSTATE = 69,
    DEV_DRAWPRIMITIVEUP = 83,
    DEV_SETFVF = 89,
    DEV_CREATEVERTEXSHADER = 91,
    DEV_SETVERTEXSHADER = 92,
    DEV_SETVERTEXSHADERCONSTANTF = 94,
    DEV_CREATEPIXELSHADER = 106,
    DEV_SETPIXELSHADER = 107,
    DEV_SETPIXELSHADERCONSTANTF = 109,
    TEX_LOCKRECT = 19,
    TEX_UNLOCKRECT = 20,
    SURF_LOCKRECT = 13,
    SURF_UNLOCKRECT = 14,
};

enum {
    D3D_SDK_VERSION = 32,
    D3DDEVTYPE_HAL = 1,
    D3DCREATE_FPU_PRESERVE = 0x2,
    D3DCREATE_SOFTWARE_VERTEXPROCESSING = 0x20,
    D3DCREATE_HARDWARE_VERTEXPROCESSING = 0x40,
    D3DFMT_A8R8G8B8 = 21,
    D3DFMT_X8R8G8B8 = 22,
    D3DSWAPEFFECT_DISCARD = 1,
    D3DPRESENT_INTERVAL_IMMEDIATE = (int)0x80000000u,
    D3DPOOL_MANAGED = 1,
    D3DPOOL_SYSTEMMEM = 2,
    D3DCLEAR_TARGET = 1,
    D3DPT_TRIANGLESTRIP = 5,
    D3DFVF_XYZ = 0x2,
    D3DFVF_TEX1 = 0x100,
    D3DRS_ZENABLE = 7,
    D3DRS_CULLMODE = 22,
    D3DRS_LIGHTING = 137,
    D3DCULL_NONE = 1,
    D3DSAMP_MAGFILTER = 5,
    D3DSAMP_MINFILTER = 6,
    D3DTEXF_POINT = 1,
    D3DLOCK_READONLY = 0x10,
};

typedef struct {
    uint32_t BackBufferWidth, BackBufferHeight, BackBufferFormat, BackBufferCount;
    uint32_t MultiSampleType, MultiSampleQuality, SwapEffect;
    void *hDeviceWindow;
    int32_t Windowed, EnableAutoDepthStencil;
    uint32_t AutoDepthStencilFormat, Flags, FullScreen_RefreshRateInHz, PresentationInterval;
} PP;

typedef struct {
    int32_t Pitch;
    void *pBits;
} LOCKED_RECT;

typedef struct {
    char Driver[512], Description[512], DeviceName[32];
    int64_t DriverVersion;
    uint32_t VendorId, DeviceId, SubSysId, Revision;
    uint8_t DeviceIdentifier[16];
    uint32_t WHQLLevel;
} ADAPTER_ID;

_Static_assert(sizeof(PP) == 64 && offsetof(PP, hDeviceWindow) == 32, "D3DPRESENT_PARAMETERS 64-bit layout");
_Static_assert(sizeof(LOCKED_RECT) == 16, "D3DLOCKED_RECT 64-bit layout");
_Static_assert(offsetof(ADAPTER_ID, DriverVersion) == 1056 && sizeof(ADAPTER_ID) == 1104, "D3DADAPTER_IDENTIFIER9 layout");

#define M(o, i, T) ((T)((*(void ***)(o))[i]))
#define W 1280
#define H 1024
#define CLEAR 0xFF202020u

static obj dev;

static const uint32_t vs20[] = {0xFFFE0200, 0x0200001F, 0x80000000, 0x900F0000, 0x0200001F, 0x80000005, 0x900F0001,
                                0x02000001, 0xC00F0000, 0x90E40000, 0x02000001, 0xE00F0000, 0x90E40001, 0x0000FFFF};
static const uint32_t ps20[] = {0xFFFF0200, 0x0200001F, 0x80000000, 0xB00F0000, 0x0200001F, 0x90000000, 0xA00F0800,
                                0x03000042, 0x800F0000, 0xB0E40000, 0xA0E40800, 0x02000001, 0x800F0800, 0x80E40000,
                                0x0000FFFF};
static const uint32_t vs11[] = {0xFFFE0101, 0x0000001F, 0x80000000, 0x900F0000, 0x0000001F, 0x80000005, 0x900F0001,
                                0x00000001, 0xC00F0000, 0x90E40000, 0x00000001, 0xE00F0000, 0x90E40001, 0x0000FFFF};
static const uint32_t ps11[] = {0xFFFF0101, 0x00000042, 0xB00F0000, 0x00000001, 0x800F0000, 0xB0E40000, 0x0000FFFF};
static const uint32_t ps14[] = {0xFFFF0104, 0x00000042, 0x800F0000, 0xB0E40000, 0x0000FFFF};

static const uint32_t texels[4] = {0xFFFF0000u, 0xFF00FF00u, 0xFF0000FFu, 0xFFFFFF00u};

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}

static size_t walk(const uint32_t *p, size_t n)
{
    if (n < 2) return 0;
    int major = (int)((p[0] >> 8) & 0xFF);
    size_t i = 1;
    while (i < n) {
        uint32_t t = p[i];
        if (t == 0x0000FFFFu) return i + 1;
        if ((t & 0xFFFF) == 0xFFFE) {
            i += 1 + ((t >> 16) & 0x7FFF);
            continue;
        }
        if (major >= 2) {
            i += 1 + ((t >> 24) & 0xF);
            continue;
        }
        i++;
        if ((t & 0xFFFF) == 81) {
            i += 5;
            continue;
        }
        while (i < n && (p[i] & 0x80000000u)) i++;
    }
    return 0;
}

static const char *model_name(uint32_t v)
{
    switch (v) {
    case 0xFFFE0101: return "vs_1_1";
    case 0xFFFE0200: return "vs_2_0";
    case 0xFFFF0101: return "ps_1_1";
    case 0xFFFF0104: return "ps_1_4";
    case 0xFFFF0200: return "ps_2_0";
    }
    return 0;
}

static const char *models[5] = {"vs_1_1", "vs_2_0", "ps_1_1", "ps_1_4", "ps_2_0"};
static const int expected[5] = {2866, 1985, 144, 14, 4186};
static int created[5], failed[5], drawn[5], drawfail[5], badwalk, ndraw, scenefail, readfail;
static obj g_vs, g_ps;
static HR quad_draw(obj vs, obj ps);
static HR present(void);

typedef struct {
    char name[160];
    obj ps;
} cand;
static cand cands[64];
static int ncand;

static int midx(const char *m)
{
    for (int i = 0; i < 5; i++)
        if (!strcmp(models[i], m)) return i;
    return -1;
}

static void sweep_file(const char *path, const char *label)
{
    FILE *f = fopen(path, "rb");
    if (!f) return;
    long sz = fseek(f, 0, SEEK_END) ? -1 : ftell(f);
    uint8_t *b = sz >= 20 && !fseek(f, 0, SEEK_SET) ? malloc((size_t)sz) : 0;
    if (!b || fread(b, 1, (size_t)sz, f) != (size_t)sz) {
        printf("probe: cannot read %s\n", path);
        readfail++;
        fclose(f);
        free(b);
        return;
    }
    fclose(f);
    uint32_t n;
    memcpy(&n, b + 4, 4);
    for (uint32_t c = 0; c < n && 20 + 8 * (long)c + 8 <= sz; c++) {
        uint32_t off, len;
        memcpy(&off, b + 20 + 8 * c, 4);
        memcpy(&len, b + 24 + 8 * c, 4);
        if (!len || (long)off + (long)len > sz || len % 4 || off % 4) continue;
        const uint32_t *tok = (const uint32_t *)(b + off);
        const char *m = model_name(tok[0]);
        if (!m) continue;
        int k = midx(m);
        if (!walk(tok, len / 4)) {
            badwalk++;
            printf("probe: walk error %s %s#%u\n", m, label, c);
            continue;
        }
        obj sh = 0;
        int vs = (tok[0] >> 16) == 0xFFFE;
        HR hr = vs ? M(dev, DEV_CREATEVERTEXSHADER, HR (*)(obj, const uint32_t *, obj *))(dev, tok, &sh)
                   : M(dev, DEV_CREATEPIXELSHADER, HR (*)(obj, const uint32_t *, obj *))(dev, tok, &sh);
        if (hr == 0 && sh) {
            created[k]++;
            HR dh = vs ? quad_draw(sh, g_ps) : quad_draw(g_vs, sh);
            if (dh) {
                drawfail[k]++;
                if (drawfail[k] <= 5) printf("probe: draw %s FAILED hr 0x%08X %s#%u\n", m, (uint32_t)dh, label, c);
            } else {
                drawn[k]++;
            }
            if (++ndraw % 128 == 0) {
                HR e = M(dev, DEV_ENDSCENE, HR (*)(obj))(dev);
                HR p = present();
                HR s = M(dev, DEV_BEGINSCENE, HR (*)(obj))(dev);
                if (e || p || s) {
                    scenefail++;
                    printf("probe: sweep EndScene 0x%08X Present 0x%08X BeginScene 0x%08X\n", (uint32_t)e, (uint32_t)p,
                           (uint32_t)s);
                }
            }
            if (k == 4 && ncand < 64) {
                snprintf(cands[ncand].name, sizeof cands[ncand].name, "%s#%u", label, c);
                cands[ncand++].ps = sh;
                continue;
            }
            M(sh, U_RELEASE, uint32_t (*)(obj))(sh);
        } else {
            failed[k]++;
            if (failed[k] <= 5) printf("probe: create %s FAILED hr 0x%08X %s#%u\n", m, (uint32_t)hr, label, c);
        }
    }
    free(b);
}

static void sweep(const char *root)
{
    static const char *dirs[3] = {"fxc", "psh", "vsh"};
    for (int d = 0; d < 3; d++) {
        char dp[1024];
        snprintf(dp, sizeof dp, "%s/%s", root, dirs[d]);
        DIR *dir = opendir(dp);
        if (!dir) continue;
        struct dirent *e;
        while ((e = readdir(dir))) {
            size_t l = strlen(e->d_name);
            if (l < 5 || strcmp(e->d_name + l - 4, ".vcs")) continue;
            char fp[2048], label[160];
            snprintf(fp, sizeof fp, "%s/%s", dp, e->d_name);
            snprintf(label, sizeof label, "%s/%s", dirs[d], e->d_name);
            sweep_file(fp, label);
        }
        closedir(dir);
    }
}

static int vulkan_info(void)
{
    PFN_vkGetInstanceProcAddr gipa = (PFN_vkGetInstanceProcAddr)SDL_Vulkan_GetVkGetInstanceProcAddr();
    if (!gipa) {
        printf("probe: vulkan: no vkGetInstanceProcAddr: %s\n", SDL_GetError());
        return 0;
    }
    PFN_vkEnumerateInstanceVersion eiv = (PFN_vkEnumerateInstanceVersion)gipa(0, "vkEnumerateInstanceVersion");
    PFN_vkCreateInstance ci = (PFN_vkCreateInstance)gipa(0, "vkCreateInstance");
    uint32_t iv = 0;
    if (eiv) eiv(&iv);
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, 0, "probe", 1, 0, 0, VK_API_VERSION_1_3};
    VkInstanceCreateInfo info = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, 0, 0, &app, 0, 0, 0, 0};
    VkInstance inst = 0;
    if (!ci || ci(&info, 0, &inst) != VK_SUCCESS) {
        printf("probe: vulkan: vkCreateInstance failed\n");
        return 0;
    }
    PFN_vkEnumeratePhysicalDevices epd = (PFN_vkEnumeratePhysicalDevices)gipa(inst, "vkEnumeratePhysicalDevices");
    PFN_vkGetPhysicalDeviceProperties2 gpp =
        (PFN_vkGetPhysicalDeviceProperties2)gipa(inst, "vkGetPhysicalDeviceProperties2");
    PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)gipa(inst, "vkDestroyInstance");
    VkPhysicalDevice pd[8];
    uint32_t n = 8;
    if (!epd || !gpp || epd(inst, &n, pd) < 0) n = 0;
    printf("probe: vulkan loader %u.%u.%u, %u physical device(s)\n", VK_API_VERSION_MAJOR(iv), VK_API_VERSION_MINOR(iv),
           VK_API_VERSION_PATCH(iv), n);
    int kk = n > 0;
    for (uint32_t i = 0; i < n; i++) {
        VkPhysicalDeviceDriverProperties drv;
        VkPhysicalDeviceProperties2 p2;
        memset(&drv, 0, sizeof drv);
        memset(&p2, 0, sizeof p2);
        drv.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES;
        p2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
        p2.pNext = &drv;
        gpp(pd[i], &p2);
        VkPhysicalDeviceProperties *p = &p2.properties;
        printf("probe: vulkan device %u \"%s\" vendor 0x%04X apiVersion %u.%u.%u driverVersion 0x%08X driverID %d \"%s\" "
               "\"%s\"\n",
               i, p->deviceName, p->vendorID, VK_API_VERSION_MAJOR(p->apiVersion), VK_API_VERSION_MINOR(p->apiVersion),
               VK_API_VERSION_PATCH(p->apiVersion), p->driverVersion, (int)drv.driverID, drv.driverName, drv.driverInfo);
        kk &= drv.driverID == VK_DRIVER_ID_MESA_KOSMICKRISP;
    }
    if (di) di(inst, 0);
    printf("probe: vulkan driver is KosmicKrisp (VK_DRIVER_ID_MESA_KOSMICKRISP %d): %s\n", VK_DRIVER_ID_MESA_KOSMICKRISP,
           kk ? "PASS" : "FAIL");
    return kk;
}

static obj sysmem;
static uint32_t *pixels;

static HR readback(void)
{
    obj rt = 0;
    LOCKED_RECT lr;
    memset(pixels, 0xA5, (size_t)W * H * 4);
    HR hr = M(sysmem, SURF_LOCKRECT, HR (*)(obj, LOCKED_RECT *, const void *, uint32_t))(sysmem, &lr, 0, 0);
    if (hr) return hr;
    for (int y = 0; y < H; y++) memset((uint8_t *)lr.pBits + (size_t)y * (size_t)lr.Pitch, 0xA5, W * 4);
    if ((hr = M(sysmem, SURF_UNLOCKRECT, HR (*)(obj))(sysmem))) return hr;
    if ((hr = M(dev, DEV_GETRENDERTARGET, HR (*)(obj, uint32_t, obj *))(dev, 0, &rt))) return hr;
    hr = M(dev, DEV_GETRENDERTARGETDATA, HR (*)(obj, obj, obj))(dev, rt, sysmem);
    M(rt, U_RELEASE, uint32_t (*)(obj))(rt);
    if (hr) return hr;
    hr = M(sysmem, SURF_LOCKRECT, HR (*)(obj, LOCKED_RECT *, const void *, uint32_t))(sysmem, &lr, 0, D3DLOCK_READONLY);
    if (hr) return hr;
    for (int y = 0; y < H; y++) memcpy(pixels + y * W, (uint8_t *)lr.pBits + (size_t)y * (size_t)lr.Pitch, W * 4);
    return M(sysmem, SURF_UNLOCKRECT, HR (*)(obj))(sysmem);
}

static HR present(void)
{
    return M(dev, DEV_PRESENT, HR (*)(obj, const void *, const void *, void *, const void *))(dev, 0, 0, 0, 0);
}

static HR quad_draw(obj vs, obj ps)
{
    static const float quad[4][5] = {{-1, 1, 0.5f, 0, 0}, {1, 1, 0.5f, 1, 0}, {-1, -1, 0.5f, 0, 1}, {1, -1, 0.5f, 1, 1}};
    HR hr = M(dev, DEV_SETVERTEXSHADER, HR (*)(obj, obj))(dev, vs);
    if (hr) return hr;
    if ((hr = M(dev, DEV_SETPIXELSHADER, HR (*)(obj, obj))(dev, ps))) return hr;
    return M(dev, DEV_DRAWPRIMITIVEUP, HR (*)(obj, uint32_t, uint32_t, const void *, uint32_t))(dev, D3DPT_TRIANGLESTRIP, 2,
                                                                                              quad, sizeof quad[0]);
}

static HR draw(obj vs, obj ps)
{
    HR hr = M(dev, DEV_CLEAR, HR (*)(obj, uint32_t, const void *, uint32_t, uint32_t, float, uint32_t))(
        dev, 0, 0, D3DCLEAR_TARGET, CLEAR, 1.0f, 0);
    if (hr) return hr;
    if ((hr = M(dev, DEV_BEGINSCENE, HR (*)(obj))(dev))) return hr;
    hr = quad_draw(vs, ps);
    HR e = M(dev, DEV_ENDSCENE, HR (*)(obj))(dev);
    if (hr) return hr;
    if (e) return e;
    return readback();
}

static int chan_ok(uint32_t a, uint32_t b)
{
    for (int s = 0; s < 32; s += 8) {
        int d = (int)((a >> s) & 0xFF) - (int)((b >> s) & 0xFF);
        if (d < -1 || d > 1) return 0;
    }
    return 1;
}

static int check_quadrants(const char *label)
{
    static const int px[4][2] = {{W / 4, H / 4}, {3 * W / 4, H / 4}, {W / 4, 3 * H / 4}, {3 * W / 4, 3 * H / 4}};
    int ok = 1;
    for (int i = 0; i < 4; i++) {
        uint32_t got = pixels[px[i][1] * W + px[i][0]] & 0x00FFFFFFu, want = texels[i] & 0x00FFFFFFu;
        int good = chan_ok(got, want);
        ok &= good;
        printf("probe: %s pixel (%d,%d) got %06X want %06X %s\n", label, px[i][0], px[i][1], got, want,
               good ? "ok" : "MISMATCH");
    }
    return ok;
}

static obj mk_vs(const uint32_t *t)
{
    obj s = 0;
    HR hr = M(dev, DEV_CREATEVERTEXSHADER, HR (*)(obj, const uint32_t *, obj *))(dev, t, &s);
    return hr ? 0 : s;
}

static obj mk_ps(const uint32_t *t)
{
    obj s = 0;
    HR hr = M(dev, DEV_CREATEPIXELSHADER, HR (*)(obj, const uint32_t *, obj *))(dev, t, &s);
    return hr ? 0 : s;
}

int main(int argc, char **argv)
{
    const char *shaders = 0, *png = 0, *lib = "libdxvk_d3d9.dylib";
    uint32_t vp = D3DCREATE_HARDWARE_VERTEXPROCESSING;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--shaders") && i + 1 < argc) shaders = argv[++i];
        else if (!strcmp(argv[i], "--png") && i + 1 < argc) png = argv[++i];
        else if (!strcmp(argv[i], "--lib") && i + 1 < argc) lib = argv[++i];
        else if (!strcmp(argv[i], "--swvp")) vp = D3DCREATE_SOFTWARE_VERTEXPROCESSING;
        else {
            fprintf(stderr, "usage: probe --lib <libdxvk_d3d9.dylib> [--shaders <dir>] [--png <file>] [--swvp]\n");
            return 2;
        }
    }
    int pass = 1;
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        printf("probe: SDL_Init failed: %s\n", SDL_GetError());
        return 1;
    }
    SDL_Window *win = SDL_CreateWindow("bloodlines-native probe", W, H, SDL_WINDOW_VULKAN);
    if (!win) {
        printf("probe: SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 1;
    }
    int kosmic = vulkan_info();
    pass &= kosmic;
    SDL_SharedObject *so = SDL_LoadObject(lib);
    if (!so) {
        printf("probe: SDL_LoadObject %s failed: %s\n", lib, SDL_GetError());
        return 1;
    }
    obj (*create9)(uint32_t) = (obj (*)(uint32_t))SDL_LoadFunction(so, "Direct3DCreate9");
    obj d3d = create9 ? create9(D3D_SDK_VERSION) : 0;
    if (!d3d) {
        printf("probe: Direct3DCreate9 FAILED\n");
        return 1;
    }
    ADAPTER_ID id;
    memset(&id, 0, sizeof id);
    HR hr = M(d3d, D9_GETADAPTERIDENTIFIER, HR (*)(obj, uint32_t, uint32_t, ADAPTER_ID *))(d3d, 0, 0, &id);
    printf("probe: adapter hr 0x%08X \"%s\" driver \"%s\" vendor 0x%04X device 0x%04X version 0x%016llX\n", (uint32_t)hr,
           id.Description, id.Driver, id.VendorId, id.DeviceId, (unsigned long long)id.DriverVersion);

    PP pp;
    memset(&pp, 0, sizeof pp);
    pp.BackBufferWidth = W;
    pp.BackBufferHeight = H;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = win;
    pp.Windowed = 1;
    pp.PresentationInterval = (uint32_t)D3DPRESENT_INTERVAL_IMMEDIATE;
    uint32_t flags = D3DCREATE_FPU_PRESERVE | vp;
    hr = M(d3d, D9_CREATEDEVICE, HR (*)(obj, uint32_t, uint32_t, void *, uint32_t, PP *, obj *))(d3d, 0, D3DDEVTYPE_HAL, win,
                                                                                               flags, &pp, &dev);
    printf("probe: CreateDevice flags 0x%02X %ux%u windowed -> hr 0x%08X %s\n", flags, W, H, (uint32_t)hr,
           hr == 0 ? "device created" : "FAILED");
    if (hr) {
        printf("probe: AC1 FAIL (no device)\n");
        return 1;
    }

    printf("probe: GetAvailableTextureMem %u MiB\n", M(dev, DEV_GETAVAILABLETEXTUREMEM, uint32_t (*)(obj))(dev) >> 20);
    pixels = malloc((size_t)W * H * 4);
    if (!pixels) {
        printf("probe: out of memory\n");
        return 1;
    }
    hr =M(dev, DEV_CREATEOFFSCREENPLAINSURFACE, HR (*)(obj, uint32_t, uint32_t, uint32_t, uint32_t, obj *, void *))(
        dev, W, H, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, &sysmem, 0);
    if (hr) {
        printf("probe: CreateOffscreenPlainSurface hr 0x%08X\n", (uint32_t)hr);
        return 1;
    }
    obj tex = 0;
    hr = M(dev, DEV_CREATETEXTURE, HR (*)(obj, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, obj *, void *))(
        dev, 2, 2, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &tex, 0);
    LOCKED_RECT lr;
    if (!hr) hr = M(tex, TEX_LOCKRECT, HR (*)(obj, uint32_t, LOCKED_RECT *, const void *, uint32_t))(tex, 0, &lr, 0, 0);
    if (hr) {
        printf("probe: texture setup hr 0x%08X\n", (uint32_t)hr);
        return 1;
    }
    for (int y = 0; y < 2; y++) memcpy((uint8_t *)lr.pBits + y * lr.Pitch, texels + 2 * y, 8);
    M(tex, TEX_UNLOCKRECT, HR (*)(obj, uint32_t))(tex, 0);
    for (uint32_t s = 0; s < 8; s++) {
        M(dev, DEV_SETTEXTURE, HR (*)(obj, uint32_t, obj))(dev, s, tex);
        M(dev, DEV_SETSAMPLERSTATE, HR (*)(obj, uint32_t, uint32_t, uint32_t))(dev, s, D3DSAMP_MINFILTER, D3DTEXF_POINT);
        M(dev, DEV_SETSAMPLERSTATE, HR (*)(obj, uint32_t, uint32_t, uint32_t))(dev, s, D3DSAMP_MAGFILTER, D3DTEXF_POINT);
    }
    M(dev, DEV_SETRENDERSTATE, HR (*)(obj, uint32_t, uint32_t))(dev, D3DRS_CULLMODE, D3DCULL_NONE);
    M(dev, DEV_SETRENDERSTATE, HR (*)(obj, uint32_t, uint32_t))(dev, D3DRS_ZENABLE, 0);
    M(dev, DEV_SETRENDERSTATE, HR (*)(obj, uint32_t, uint32_t))(dev, D3DRS_LIGHTING, 0);
    M(dev, DEV_SETFVF, HR (*)(obj, uint32_t))(dev, D3DFVF_XYZ | D3DFVF_TEX1);

    struct {
        const char *label;
        const uint32_t *vs, *ps;
    } hand[3] = {{"vs_2_0+ps_2_0", vs20, ps20}, {"vs_1_1+ps_1_1", vs11, ps11}, {"vs_1_1+ps_1_4", vs11, ps14}};
    for (int i = 0; i < 3; i++) {
        obj v = mk_vs(hand[i].vs), p = mk_ps(hand[i].ps);
        hr = v && p ? draw(v, p) : -1;
        int ok = !hr && check_quadrants(hand[i].label);
        printf("probe: draw %s hr 0x%08X readback %s\n", hand[i].label, (uint32_t)hr, ok ? "PASS" : "FAIL");
        pass &= ok;
        M(dev, DEV_SETVERTEXSHADER, HR (*)(obj, obj))(dev, 0);
        M(dev, DEV_SETPIXELSHADER, HR (*)(obj, obj))(dev, 0);
        if (v) M(v, U_RELEASE, uint32_t (*)(obj))(v);
        if (p) M(p, U_RELEASE, uint32_t (*)(obj))(p);
    }

    if (shaders && !kosmic) printf("probe: shader sweep skipped: the Vulkan driver is not KosmicKrisp\n");
    if (shaders && kosmic) {
        g_vs = mk_vs(vs20);
        g_ps = mk_ps(ps20);
        double t0 = now();
        HR b = M(dev, DEV_BEGINSCENE, HR (*)(obj))(dev);
        sweep(shaders);
        HR e = M(dev, DEV_ENDSCENE, HR (*)(obj))(dev);
        if (!g_vs || !g_ps || b || e) {
            scenefail++;
            printf("probe: sweep setup vs %p ps %p BeginScene 0x%08X EndScene 0x%08X\n", g_vs, g_ps, (uint32_t)b, (uint32_t)e);
        }
        HR sync = readback();
        double dt = now() - t0;
        int tc = 0, tf = 0, td = 0, tdf = 0;
        printf("probe: model   created failed drawn draw-failed expected\n");
        for (int i = 0; i < 5; i++) {
            int ok = created[i] == expected[i] && !failed[i] && drawn[i] == expected[i] && !drawfail[i];
            printf("probe: %-7s %7d %6d %5d %11d %8d %s\n", models[i], created[i], failed[i], drawn[i], drawfail[i],
                   expected[i], ok ? "ok" : "MISMATCH");
            tc += created[i];
            tf += failed[i];
            td += drawn[i];
            tdf += drawfail[i];
            pass &= ok;
        }
        printf("probe: create %d/9195 failed %d, drawn %d/9195 failed %d, walk-errors %d, read-errors %d, scene-errors %d, "
               "sync hr 0x%08X, time %.2f s\n",
               tc, tf, td, tdf, badwalk, readfail, scenefail, (uint32_t)sync, dt);
        pass &= !badwalk && !readfail && !scenefail && !sync;
        obj v = g_vs;
        float consts[32][4];
        for (int i = 0; i < 32; i++)
            for (int j = 0; j < 4; j++) consts[i][j] = 0.5f + 0.01f * (float)(i + j);
        M(dev, DEV_SETPIXELSHADERCONSTANTF, HR (*)(obj, uint32_t, const float *, uint32_t))(dev, 0, &consts[0][0], 32);
        int game_ok = 0;
        for (int i = 0; i < ncand && !game_ok; i++) {
            hr = draw(v, cands[i].ps);
            long diff = 0, stale = 0;
            if (!hr)
                for (long k = 0; k < (long)W * H; k++) {
                    uint32_t px = pixels[k] & 0xFFFFFF;
                    stale += px == 0xA5A5A5;
                    diff += px != (CLEAR & 0xFFFFFF) && px != 0xA5A5A5;
                }
            printf("probe: game ps_2_0 %s hr 0x%08X pixels-not-clear %ld sentinel %ld centre %06X\n", cands[i].name,
                   (uint32_t)hr, diff, stale, pixels[(H / 2) * W + W / 2] & 0xFFFFFF);
            game_ok = !hr && !stale && diff > (long)W * H / 2;
        }
        printf("probe: game ps_2_0 draw %s\n", game_ok ? "PASS" : "FAIL");
        pass &= game_ok;
    }

    hr = present();
    printf("probe: Present hr 0x%08X\n", (uint32_t)hr);
    pass &= hr == 0;
    if (png) {
        for (long k = 0; k < (long)W * H; k++) pixels[k] |= 0xFF000000u;
        SDL_Surface *s = SDL_CreateSurfaceFrom(W, H, SDL_PIXELFORMAT_XRGB8888, pixels, W * 4);
        int ok = s && SDL_SavePNG(s, png);
        printf("probe: png %s %s\n", png, ok ? "written" : SDL_GetError());
        SDL_DestroySurface(s);
    }
    SDL_Event ev;
    while (SDL_PollEvent(&ev)) {
    }
    M(dev, DEV_SETVERTEXSHADER, HR (*)(obj, obj))(dev, 0);
    M(dev, DEV_SETPIXELSHADER, HR (*)(obj, obj))(dev, 0);
    for (uint32_t s = 0; s < 8; s++) M(dev, DEV_SETTEXTURE, HR (*)(obj, uint32_t, obj))(dev, s, 0);
    for (int i = ncand - 1; i >= 0; i--) M(cands[i].ps, U_RELEASE, uint32_t (*)(obj))(cands[i].ps);
    if (g_ps) M(g_ps, U_RELEASE, uint32_t (*)(obj))(g_ps);
    if (g_vs) M(g_vs, U_RELEASE, uint32_t (*)(obj))(g_vs);
    uint32_t rtex = M(tex, U_RELEASE, uint32_t (*)(obj))(tex);
    uint32_t rsys = M(sysmem, U_RELEASE, uint32_t (*)(obj))(sysmem);
    uint32_t rdev = M(dev, U_RELEASE, uint32_t (*)(obj))(dev);
    uint32_t rd3d = M(d3d, U_RELEASE, uint32_t (*)(obj))(d3d);
    SDL_UnloadObject(so);
    SDL_DestroyWindow(win);
    SDL_Quit();
    int down = !rtex && !rsys && !rdev && !rd3d;
    printf("probe: teardown refs texture %u surface %u device %u d3d9 %u: %s\n", rtex, rsys, rdev, rd3d,
           down ? "PASS" : "FAIL");
    pass &= down;
    free(pixels);
    printf("probe: AC1 %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 3;
}
