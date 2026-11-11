#define RECOMP_GENERATED_CODE
#include "recomp_types.h"
#define ebp g_ebp
#define RT_SIMD_TRAP(text, va) rt_simd_trap((text), (va))
void rt_simd_trap(const char *text, uint32_t va) __attribute__((noreturn));
void rt_fninit(void);
void rt_fnstenv(uint32_t a);
void rt_fldenv(uint32_t a);
void rt_fnsave(uint32_t a);
void rt_frstor(uint32_t a);

#define RT_ATOMIC_SPLIT(addr, bits) ((((uint32_t)(addr) & 15u) + (bits) / 8u) > 16u)
#define RT_ATOMIC_RMW(bits, fn, sym, addr, v) (__extension__ ({ \
    uint32_t _rt_a = (uint32_t)(addr); \
    uint##bits##_t _rt_v = (uint##bits##_t)(v), _rt_o; \
    uint##bits##_t *_rt_p = (uint##bits##_t *)XBOX_PTR(_rt_a); \
    if (RT_ATOMIC_SPLIT(_rt_a, bits)) { \
        _rt_o = *(volatile uint##bits##_t *)_rt_p; \
        *(volatile uint##bits##_t *)_rt_p = (uint##bits##_t)(_rt_o sym _rt_v); \
    } else { \
        _rt_o = __atomic_fetch_##fn(_rt_p, _rt_v, __ATOMIC_SEQ_CST); \
    } \
    _rt_o; }))
#define RT_ATOMIC_ADD(bits, addr, v) RT_ATOMIC_RMW(bits, add, +, addr, v)
#define RT_ATOMIC_SUB(bits, addr, v) RT_ATOMIC_RMW(bits, sub, -, addr, v)
#define RT_ATOMIC_AND(bits, addr, v) RT_ATOMIC_RMW(bits, and, &, addr, v)
#define RT_ATOMIC_OR(bits, addr, v) RT_ATOMIC_RMW(bits, or, |, addr, v)
#define RT_ATOMIC_XOR(bits, addr, v) RT_ATOMIC_RMW(bits, xor, ^, addr, v)
#define RT_ATOMIC_XCHG(bits, addr, v) (__extension__ ({ \
    uint32_t _rt_a = (uint32_t)(addr); \
    uint##bits##_t _rt_v = (uint##bits##_t)(v), _rt_o; \
    uint##bits##_t *_rt_p = (uint##bits##_t *)XBOX_PTR(_rt_a); \
    if (RT_ATOMIC_SPLIT(_rt_a, bits)) { \
        _rt_o = *(volatile uint##bits##_t *)_rt_p; \
        *(volatile uint##bits##_t *)_rt_p = _rt_v; \
    } else { \
        _rt_o = __atomic_exchange_n(_rt_p, _rt_v, __ATOMIC_SEQ_CST); \
    } \
    _rt_o; }))

static inline double rt_fdiv(double a, double b)
{
    if (b == 0.0 && a != 0.0 && isfinite(a)) g_fp_cc |= 0x0004u;
    return a / b;
}
