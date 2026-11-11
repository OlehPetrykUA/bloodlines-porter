project('vstdlib-native', 'c', default_options: ['c_std=gnu11', 'buildtype=release', 'b_ndebug=true'])
fs = import('fs')
gen = get_option('gen_dir')
mod = get_option('module')
chunks = []
foreach f : fs.read(gen / 'chunks.txt').strip().split('\n')
  chunks += files(gen / f)
endforeach
inc = include_directories('.', '../shims', '../third_party/xboxrecomp/runtime', gen)
exact = ['-ffp-contract=off', '-fwrapv', '-fno-strict-aliasing',
  '-ffile-prefix-map=' + meson.project_source_root() + '=src', '-ffile-prefix-map=' + gen + '=gen']
lifted = static_library('lifted', chunks,
  include_directories: inc,
  c_args: exact + ['-include', meson.current_source_dir() / 'gen_prefix.h', '-Wno-everything'],
  pic: true)
defs = []
foreach r : ['recomp_lookup=rt_lookup_', 'recomp_dispatch_init=rt_dispatch_init_', 'recomp_get_count=rt_get_count_',
    'recomp_call_all=rt_call_all_', 'recomp_dispatch_flat_bytes=rt_dispatch_flat_bytes_']
  defs += '-D' + r + mod
endforeach
table = static_library('table', files(gen / 'recomp_dispatch.c', gen / 'module.c'),
  include_directories: inc,
  c_args: exact + defs + ['-Wno-everything'],
  override_options: ['c_std=gnu23'],
  pic: true)
rt_srcs = []
foreach f : fs.read('sources.txt').strip().split('\n')
  rt_srcs += files('..' / f)
endforeach
add_languages('objc', native: false)
if get_option('prebuilt')
  cc = meson.get_compiler('c')
  pre = get_option('prebuilt_prefix')
  pre_inc = include_directories(pre / 'include', is_system: true)
  pre_lib = pre / 'lib'
  deps = []
  foreach n : ['SDL3.0', 'avformat.62', 'avcodec.62', 'avutil.60']
    deps += declare_dependency(dependencies: cc.find_library(n, dirs: [pre_lib]), include_directories: pre_inc)
  endforeach
else
  deps = [dependency('sdl3'), dependency('libavformat'), dependency('libavcodec'), dependency('libavutil')]
endif
shared_library(mod, rt_srcs,
  include_directories: inc,
  c_args: exact,
  objc_args: exact + ['-fobjc-arc'],
  dependencies: deps + [dependency('appleframeworks', modules: ['Cocoa', 'CoreText', 'ImageIO', 'QuartzCore', 'Metal', 'MetalFX'])],
  link_whole: [lifted, table])
