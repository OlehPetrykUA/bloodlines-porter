#include "recomp_types.h"
#include "rt.h"

static uint32_t timeGetTime(const uint32_t *a)
{
    (void)a;
    return (uint32_t)(rt_now_ns() / 1000000u);
}

static uint32_t timePeriod(const uint32_t *a)
{
    (void)a;
    return 0;
}

static uint32_t joyGetNumDevs(const uint32_t *a)
{
    (void)a;
    return 0;
}

const rt_shim rt_winmm_shims[] = {
    {"winmm.dll", "joyGetNumDevs", 0, joyGetNumDevs},
    {"winmm.dll", "timeBeginPeriod", 1, timePeriod},
    {"winmm.dll", "timeEndPeriod", 1, timePeriod},
    {"winmm.dll", "timeGetTime", 0, timeGetTime},
};

const uint32_t rt_nwinmm_shims = sizeof rt_winmm_shims / sizeof rt_winmm_shims[0];
