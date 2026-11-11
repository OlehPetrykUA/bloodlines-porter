project('bloodlines-native', 'c', default_options: ['c_std=gnu11', 'buildtype=release', 'b_ndebug=true'])
fs = import('fs')
gen = get_option('gen_root')
inc = include_directories('../runtime', '../shims', '../third_party/xboxrecomp/runtime')
exact = ['-ffp-contract=off', '-fwrapv', '-fno-strict-aliasing',
  '-ffile-prefix-map=' + meson.project_source_root() + '=src', '-ffile-prefix-map=' + gen + '=gen']
lifted = exact + ['-include', meson.current_source_dir() / '../runtime/gen_prefix.h', '-Wno-everything']
renames = ['recomp_lookup=rt_lookup_', 'recomp_dispatch_init=rt_dispatch_init_', 'recomp_get_count=rt_get_count_',
  'recomp_call_all=rt_call_all_', 'recomp_dispatch_flat_bytes=rt_dispatch_flat_bytes_']
opt1 = fs.read(gen / 'optimization1.txt').strip().split('\n')
o0 = fs.read(gen / 'o0.txt').strip().split('\n')
libs = []
foreach m : fs.read(gen / 'modules.txt').strip().split('\n')
  chunks = []
  slow = []
  foreach f : fs.read(gen / m / 'gen' / 'chunks.txt').strip().split('\n')
    if o0.contains(m / f)
      slow += gen / m / 'gen' / f
    else
      chunks += gen / m / 'gen' / f
    endif
  endforeach
  libs += static_library('m_' + m, chunks, include_directories: inc, c_args: lifted,
    override_options: opt1.contains(m) ? ['optimization=1'] : [])
  if slow.length() > 0
    libs += static_library('o0_' + m, slow, include_directories: inc, c_args: lifted,
      override_options: ['optimization=0'])
  endif
  defs = []
  foreach r : renames
    defs += '-D' + r + m
  endforeach
  libs += static_library('d_' + m, gen / m / 'gen' / 'recomp_dispatch.c', gen / m / 'gen' / 'module.c',
    include_directories: inc,
    c_args: exact + defs + ['-Wno-everything'],
    override_options: ['c_std=gnu23'])
endforeach
foreach u : ['binkw32', 'mss32', 'win32']
  srcs = []
  foreach f : fs.read(gen / 'stubs' / u + '.txt').strip().split('\n')
    srcs += gen / 'stubs' / f
  endforeach
  libs += static_library('stubs_' + u, srcs, include_directories: inc)
endforeach
rt_srcs = []
foreach f : fs.read('../runtime/sources.txt').strip().split('\n')
  rt_srcs += files('..' / f)
endforeach
cc = meson.get_compiler('c')
run_args = []
rpaths = ['-Wl,-rpath,/opt/homebrew/opt/sdl3/lib', '-Wl,-rpath,/opt/homebrew/opt/vulkan-loader/lib']
if get_option('prebuilt')
  pre = get_option('prebuilt_prefix')
  pre_inc = include_directories(pre / 'include', is_system: true)
  pre_lib = pre / 'lib'
  run_args = ['-Wl,-rpath,' + pre_lib]
  rpaths = []
  sdl3 = declare_dependency(dependencies: cc.find_library('SDL3.0', dirs: [pre_lib]), include_directories: pre_inc)
  ffmpeg = []
  foreach n : ['avformat.62', 'avcodec.62', 'avutil.60']
    ffmpeg += declare_dependency(dependencies: cc.find_library(n, dirs: [pre_lib]), include_directories: pre_inc)
  endforeach
else
  sdl3 = dependency('sdl3')
  ffmpeg = [dependency('libavformat'), dependency('libavcodec'), dependency('libavutil')]
endif
check = cc.run('''#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <stdio.h>
int main(void)
{
    const char *n[] = {"binkvideo", "binkaudio_dct", "binkaudio_rdft"};
    int bad = av_find_input_format("bink") == NULL;
    if (bad) puts("bink demuxer");
    for (int i = 0; i < 3; i++)
        if (!avcodec_find_decoder_by_name(n[i])) {
            puts(n[i]);
            bad = 1;
        }
    return bad;
}
''', dependencies: ffmpeg, args: run_args, name: 'FFmpeg has the bink demuxer and the binkvideo, binkaudio_dct and binkaudio_rdft decoders')
if not check.compiled() or check.returncode() != 0
  error('FFmpeg is missing: ' + check.stdout())
endif
add_languages('objc', native: false)
apple = dependency('appleframeworks', modules: ['Cocoa', 'CoreText', 'ImageIO', 'QuartzCore', 'Metal', 'MetalFX'])
libs += static_library('runtime', rt_srcs, include_directories: inc, c_args: exact, objc_args: exact + ['-fobjc-arc'],
  dependencies: [sdl3, apple] + ffmpeg)
executable('bloodlines-native', '../runtime/main.c', gen / 'link.c',
  include_directories: inc, c_args: exact, link_whole: libs, dependencies: [sdl3] + ffmpeg,
  link_args: ['-Wl,-stack_size,0x10000000'] + rpaths)
