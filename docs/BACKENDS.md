# Renderer contracts and refraction boundary

## Portable overlay

`src/render.c` and `src/engine.c` retain a legacy primary program plus at most
16 named layers. Programs with the same source/path share a reference-counted
cache. Each layer owns independent parameters and cached uniform locations.
The fullscreen triangle is composited directly using premultiplied
`ONE, ONE_MINUS_SRC_ALPHA`; no full-resolution intermediate is allocated per
layer. Disabled layers keep resources and issue zero draws. Shader compilation
is transactional. A failed shader retains its old program, parameters and sound
metadata. Directory watches cover every current layer and tolerate atomic saves.

`src/wayland.c` handles optional viewporter/fractional-scale protocols. EGL buffers
use ceil(logical size × preferred scale); low quality multiplies this by 0.65
when a viewport is available. The surface remains in logical orientation and the
compositor applies the output transform. Integer scaling is the fallback.
Each output has independent frame caps, opacity and enable state. One timer is
armed for the earliest parked frame callback. Static/frozen-only scenes stop
requesting callbacks. Global hide/pause clears/stops the presentation loop.

Quality is explicit: low reduces viewport-backed resolution and lighting
samples, balanced uses full resolution, high adds a sunbeam noise octave.
It does not claim to reduce every original shader's algorithmic complexity.
The legacy uncapped CLI remains available; the controller and panel use 30 FPS.
There is one soundtrack, selected by the legacy primary effect/custom track.
Additional layers do not launch independent audio players.

## Hyprland native

The installed binary audit recorded 0.55.4 and commit
`a0136d8c04687bb36eb8a28eb9d1ff92aea99704`. The runtime adapter probes
`hyprctl version -j` and `hyprctl getoption decoration:screen_shader -j`; unknown
versions use the portable backend. No plugin is loaded as part of capability
probing. The active session could not be queried inside the development sandbox.

Versioned primary sources:

- https://github.com/hyprwm/Hyprland/blob/v0.55.4/src/render/OpenGL.cpp
- https://github.com/hyprwm/Hyprland/tree/v0.55.4/src/render/shaders
- https://open-meteo.com/en/docs

The native shader uses GLES 3.0, `sampler2D tex`, `in vec2 v_texcoord`, and an
explicit fragment output. It derives source dimensions from `textureSize(tex,0)`.
The source exposes time handling and common texture-pass full-size bindings,
but their live uniform values could not be probed in this sandbox. This backend
requires neither `fullSize` nor `time`; dimensions come directly from the sampled
texture. Hyprland's source
explicitly requires damage tracking to be off when a custom shader uses time;
this project does not change that setting or declare time in native shaders.
All native fields are finite bounded numeric literals. GLSL is compiled/linked
in a separate surfaceless EGL validator before any `hyprctl keyword` mutation.
Runtime acceptance is read back, but that is not a guarantee of compositor-stage
visual correctness on untested versions. Native visual verification remains a
manual checklist item, not a reported automated desktop test.

The minimal standalone example is `data/screen/static-refraction.frag` (installed
under `share/gl-overlay/screen`). Its displacement is also tested against a
reference texture.

The combined shader samples the actual compositor-provided desktop texture for
colour processing, static droplet displacement, static edge frost/blur and
static haze. Chromatic dispersion clamps each sample to half-texel-safe UVs.
Static displacement remains opt-in and experimental: the native shader API does
not expose a damage-expansion callback. A static warp can require output damage
beyond the changed source region. That expanded-damage behavior has not been
verified live, so partial updates may leave stale displaced pixels. Correct
per-frame damage expansion belongs in the disabled advanced backend. Local
colour grading and static grain do not introduce this UV-neighborhood issue.
No damage-tracking shortcut is enabled to conceal it.

The droplet mask is intentionally static. It does not correspond to the moving
illustrated drops in the original overlay. No capture texture, synthetic scene,
readback or framebuffer-feedback trick supplies the native background.
The small PPM textures in tests are deliberately labelled reference fixtures.

Ownership is explicit. An existing external shader is refused unless the user
chooses `screen claim replace`. Configuration files are never edited. Generated
content-addressed shaders and a recovery journal are private and atomic. Clean
exit restores only if the active path still matches the owned path. An external
change relinquishes ownership; a crash leaves a recovery journal for `screen
recover`. Native shader updates are debounced and limited to one per two seconds;
there is no shader-per-frame generator. Native grain has no temporal component.

The native screen shader is global to Hyprland, so screen processing cannot be
assigned to one output through this backend. Per-output layer assignment and
renderer output caps remain available. Output colour temperature/gamma managers
must be coordinated explicitly; a detected running hyprsunset blocks ownership
of those fields until opted in. HDR, wide-gamut colourimetry and colour-manager
interactions have not been validated; the current grade operates in sampled RGB.

## Advanced moving refraction: blocked and disabled

No version-pinned plugin implementation is shipped or advertised as functional.
The runtime capability is `moving_refraction: false`. There were no matching
Hyprland development headers or safe nested-compositor harness available during
this implementation. The installed binary's ABI is version-specific:
`a0136d8c04687bb36eb8a28eb9d1ff92aea99704_aq_0.11_hu_0.13_hg_0.5_hc_0.1_hlg_0.6`.

Before enabling an advanced backend, all of these must be implemented and tested:

1. Pin the compositor revision, headers and public plugin API together. Reject
   runtime ABI mismatch before registering any render callbacks.
2. Obtain an immutable background texture before the optical overlay stage.
   Read a distinct texture from the current write target; a render event by
   itself does not establish safe ownership or ordering.
3. Share one time/mask definition between refractive displacement and optical
   highlights. Independently animating native/static and layer-shell drops is
   not coherent refraction.
4. Expand damage for displaced source neighborhoods and old/new mask bounds;
   schedule bounded animation damage locally instead of disabling global damage.
5. Recreate resources on resize, scale/transform change and hotplug; restore GL
   state after every callback and keep unrelated effects outside the plugin.
6. Exercise load/unload, monitor removal, fullscreen/direct scanout, colour
   management and compositor restart in an isolated compositor before deployment.

These are architectural blockers, not a hidden synthetic fallback. The portable
renderer, native static backend, controller and manual lighting work without it.
