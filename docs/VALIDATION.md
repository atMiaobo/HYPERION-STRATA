# Validation checklist and reproducible commands

## Completed in the development sandbox

- [x] Native Meson build of renderer, headless renderer and C engine test
  with sound disabled; sound-enabled C source also compiled against cached mpv
  headers. The full sound-enabled Nix package build remains unverified.
- [x] Original effect settings/presets and all original sakura rendering tests.
- [x] New sunbeam, flare, grain and wash GLSL compilation and combined rendering.
- [x] Native GLES shader compilation, identity transform on a known reference
  texture and pixel differences for refraction, frost/blur, dispersion and grading.
- [x] C tests of shared program caching, disabled-layer draw counts, static idle
  classification, 128 parameters, invalid-source rollback and ordered dispatch.
- [x] Nonblocking IPC dispatch with fragmented messages and expired peers through
  a socket pair; no compositor session is needed.
- [x] Deterministic weather fixtures/simulated clock, invalid units/values,
  transitions, hybrid priority, cache corruption, offline backoff and persistence.
- [x] Screen ownership refusal, explicit replacement, external changes and
  validator failure through mocked Hyprland calls.
- [x] GUI parameter routing through IPC doubles without requiring a GTK display.
- [x] Nix package evaluation using its cached locked Nixpkgs source.
- [ ] Full `nix build` / `nix flake check`: daemon access is denied in this sandbox.
- [ ] Live GTK/Wayland desktop smoke, real Hyprland screen application, monitor
  transform/fractional-scale/hotplug validation and hardware idle-power testing.
- [ ] Advanced plugin: deliberately unimplemented, disabled and reported as such.

No automated test here claims to have captured or inspected the real desktop.
Reference backgrounds are test fixtures fed to the same generated native shader.

## Build and run tests

```sh
nix develop
meson setup build -Dsound=enabled
ninja -C build
meson test -C build --print-errorlogs
GL_OVERLAY_STILL="$PWD/build/gl-overlay-still" python3 -m unittest discover -s tests -v
./build/gl-overlay-engine-test
```

The C test deliberately attempts one invalid shader and expects its compile error.
Successful output ends with `layer/cache/idle/dynamic-params/rollback/dispatch: passed`.
On a system with Mesa software drivers, `LIBGL_ALWAYS_SOFTWARE=1` can run the
headless tests without a GPU. The Nix package runs non-graphical tests during its
check phase; GLES checks are explicitly enabled with `GL_OVERLAY_STILL`.

## Manual overlay and monitor validation

```sh
gl-overlay -s sakura -f 30
# in another terminal
gl-overlay ctl layer add rays sunbeams
gl-overlay ctl layer add flare lens-flare
gl-overlay ctl layer add grain film-grain
gl-overlay ctl layer opacity grain 0.3
gl-overlay ctl engine
gl-overlay ctl layer enable grain 0
gl-overlay ctl engine                         # grain draw count stops increasing
gl-overlay ctl base 0
gl-overlay ctl layer enable rays 0
gl-overlay ctl layer enable flare 1           # static flare only: callbacks stop after update
gl-overlay ctl layer animate flare 0
gl-overlay ctl output DP-1 fps 24
gl-overlay ctl layer output flare DP-1
gl-overlay ctl quality low
gl-overlay ctl outputs                        # buffer vs logical dimensions
gl-overlay ctl hide
gl-overlay ctl show
gl-overlay ctl pause
gl-overlay ctl resume
```

Replace output names with those printed by `outputs`. Resize, rotate, change
fractional scale and unplug/replug monitors. Verify empty input passthrough,
no keyboard focus, correct logical orientation and output assignment. Compare
frame/draw counters and GPU activity before/after static/hide/pause states.
The software C test proves no draws for disabled layers, not compositor idle power.

## Manual native shader validation

```sh
hyprctl version -j
hyprctl getoption decoration:screen_shader -j
hyprctl getoption debug:damage_tracking -j
hyperion-cadere serve
# another terminal
hyperion-cadere screen enable
hyperion-cadere screen set refraction 0.012
hyperion-cadere screen set dispersion 0.25
hyperion-cadere screen set frost 0.35
hyperion-cadere screen set blur 1
hyperion-cadere status
hyperion-cadere screen disable
hyprctl getoption decoration:screen_shader -j
hyprctl getoption debug:damage_tracking -j
```

Use a desktop with a grid, small text and different windows. Static lenses must
bend the actual grid/windows; no underlying desktop content should be invented.
Damage-tracking settings must remain unchanged. Exercise small partial window
updates around lens edges and check for stale displaced pixels: native damage
expansion is unverified and may require the advanced backend for correctness. Keep illustrated overlay rain
disabled for this test: its moving mask is independent. Exercise existing-shader
refusal, opt-in replacement, normal exit restoration and journal recovery.
Do not kill/restart a production compositor to test an unimplemented plugin.

## Measured software benchmarks

Executed with `LIBGL_ALWAYS_SOFTWARE=1`, llvmpipe (LLVM 21.1.8, 256 bits),
16 measured frames after one warmup, using the headless FBO benchmark and
`glFinish` only at benchmark boundaries. Values include GPU/software-render
completion; they are not hardware compositor measurements.

| combination | buffer | measured ms/frame |
|---|---|---:|
| sakura + sunbeams + grain + warm wash | 2560×1600 | 176.81 |
| same combination, reduced buffer | 1664×1040 | 65.03 |
| sunbeams + lens flare + grain, primary disabled | 2560×1440 | 17.87 |

The reduced-buffer run changes the headless render dimensions; it is not a live
viewport/fractional-scale performance measurement. Heavy particles are expensive
under software rendering. No 240 Hz / 144 Hz hardware FPS claim or idle-watt
estimate is made. Live JSON exposes CPU submission time and peak process RSS,
with `gpu_frame_ms: null` because no live GPU timer is implemented.

```sh
./build/gl-overlay-still -s sakura -l rays:sunbeams -l grain:film-grain \
  -l wash:grade -P wash:uTemperature=.4 -w 2560 -h 1600 -n 16 -o out.pam
./build/gl-overlay-still -s sakura -l rays:sunbeams -l grain:film-grain \
  -l wash:grade -P wash:uTemperature=.4 -w 1664 -h 1040 -n 16 -o out.pam
./build/gl-overlay-still -s grade -b -l rays:sunbeams -l flare:lens-flare \
  -l grain:film-grain -w 2560 -h 1440 -n 16 -o out.pam
```
