#ifndef RT_MAP_H
#define RT_MAP_H

#include <stddef.h>
#include <stdint.h>

void rt_map_present(uint64_t now_ns);
uint32_t rt_map_ready(char *name, size_t n);
uint32_t rt_gfx_frame_count(void);
uint64_t rt_gfx_tex_overflow(void);

#endif
