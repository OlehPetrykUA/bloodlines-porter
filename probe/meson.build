project('bloodlines-probe', 'c', default_options : ['c_std=c11', 'warning_level=3', 'buildtype=release'])

sdl3 = dependency('sdl3')
vulkan_headers = dependency('vulkan').partial_dependency(compile_args : true, includes : true)
executable('probe', 'probe.c', dependencies : [sdl3, vulkan_headers])
