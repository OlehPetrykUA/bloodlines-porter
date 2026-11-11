#include "rt.h"

#define TABLE(x) extern const rt_shim rt_##x##_shims[]; extern const uint32_t rt_n##x##_shims;
TABLE(kernel32)
TABLE(kernel32_file)
TABLE(kernel32_sync)
TABLE(kernel32_module)
TABLE(advapi32)
TABLE(user32)
TABLE(winmm)
TABLE(audio)
TABLE(dsound)
TABLE(mss32_asi)
TABLE(bink)
TABLE(fail)
TABLE(tier0)
TABLE(misc)
extern rt_shim rt_d3d9_bridge_shims[];
extern const uint32_t rt_nd3d9_bridge_shims;
TABLE(d3d9_null)
TABLE(dinput8_null)

const rt_shim_table rt_shim_tables[] = {
    {rt_kernel32_shims, &rt_nkernel32_shims},
    {rt_kernel32_file_shims, &rt_nkernel32_file_shims},
    {rt_kernel32_sync_shims, &rt_nkernel32_sync_shims},
    {rt_kernel32_module_shims, &rt_nkernel32_module_shims},
    {rt_advapi32_shims, &rt_nadvapi32_shims},
    {rt_user32_shims, &rt_nuser32_shims},
    {rt_winmm_shims, &rt_nwinmm_shims},
    {rt_audio_shims, &rt_naudio_shims},
    {rt_dsound_shims, &rt_ndsound_shims},
    {rt_mss32_asi_shims, &rt_nmss32_asi_shims},
    {rt_bink_shims, &rt_nbink_shims},
    {rt_fail_shims, &rt_nfail_shims},
    {rt_tier0_shims, &rt_ntier0_shims},
    {rt_misc_shims, &rt_nmisc_shims},
    {rt_d3d9_bridge_shims, &rt_nd3d9_bridge_shims},
    {rt_d3d9_null_shims, &rt_nd3d9_null_shims},
    {rt_dinput8_null_shims, &rt_ndinput8_null_shims},
};

const uint32_t rt_nshim_tables = sizeof rt_shim_tables / sizeof rt_shim_tables[0];
