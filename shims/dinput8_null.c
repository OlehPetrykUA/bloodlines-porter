#include "recomp_types.h"
#include "rt.h"

static uint32_t s_di, s_refs = 1;

static uint32_t release(const uint32_t *a)
{
    (void)a;
    return s_refs ? --s_refs : 0;
}

static const rt_shim k_di[] = {
    {"dinput8.dll", "IDirectInput8A::QueryInterface", 3, 0},
    {"dinput8.dll", "IDirectInput8A::AddRef", 1, 0},
    {"dinput8.dll", "IDirectInput8A::Release", 1, release},
    {"dinput8.dll", "IDirectInput8A::CreateDevice", 4, 0},
    {"dinput8.dll", "IDirectInput8A::EnumDevices", 5, 0},
    {"dinput8.dll", "IDirectInput8A::GetDeviceStatus", 2, 0},
    {"dinput8.dll", "IDirectInput8A::RunControlPanel", 3, 0},
    {"dinput8.dll", "IDirectInput8A::Initialize", 3, 0},
    {"dinput8.dll", "IDirectInput8A::FindDevice", 4, 0},
    {"dinput8.dll", "IDirectInput8A::EnumDevicesBySemantics", 6, 0},
    {"dinput8.dll", "IDirectInput8A::ConfigureDevices", 5, 0},
};

static uint32_t DirectInput8Create(const uint32_t *a)
{
    if (!a[3]) return 0x80070057u;
    if (!s_di) {
        uint32_t n = sizeof k_di / sizeof k_di[0], v = rt_heap_alloc(4 * n, 1);
        for (uint32_t i = 0; i < n; i++) MEM32(v + 4 * i) = rt_dynamic_shim("dinput8.dll", k_di[i].name, &k_di[i]);
        s_di = rt_heap_alloc(16, 1);
        MEM32(s_di) = v;
    }
    s_refs++;
    MEM32(a[3]) = s_di;
    rt_log("DirectInput8Create(0x%X) -> null IDirectInput8A 0x%08X", a[1], s_di);
    return 0;
}

const rt_shim rt_dinput8_null_shims[] = {
    {"dinput8.dll", "DirectInput8Create", 5, DirectInput8Create},
};

const uint32_t rt_ndinput8_null_shims = sizeof rt_dinput8_null_shims / sizeof rt_dinput8_null_shims[0];
