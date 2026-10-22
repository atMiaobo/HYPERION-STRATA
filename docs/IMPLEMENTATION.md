# Implementation sequence

1. Preserve the legacy primary effect and add independent bounded layers in the shared EGL context. Cache locations at link time; transactional reload; deterministic premultiplied composition. Test old effects and combinations.
2. Correct idle/pacing and output configuration, including optional fractional-scale/viewport protocols. Test static scenes and fractional buffer sizing.
3. Implement a combined static Hyprland screen shader, compiler validation, explicit ownership and restoration; test actual texture displacement on reference textures. No time uniform or damage tracking changes.
4. Add procedural sunbeams, lens flares, portable grain/tint fallback and controls.
5. Add an optional standard-library Python controller: XDG persistence, profiles, weather fetch worker, transitions, manual/automatic/hybrid priority and JSON IPC.
6. Extend the existing GTK panel using its current widgets and style, retaining all original controls and Hyprland snippets.
7. Package and test; report measured timings and platform blockers honestly.

## Platform audit

The installed binary reports Hyprland 0.55.4, commit a0136d8c04687bb36eb8a28eb9d1ff92aea99704, ABI suffix aq_0.11_hu_0.13_hg_0.5_hc_0.1_hlg_0.6. Live hyprctl requests are denied in the execution sandbox, so active session version/options cannot be independently confirmed here.

Versioned primary source: https://github.com/hyprwm/Hyprland/blob/v0.55.4/src/render/OpenGL.cpp
It explicitly rejects time-dependent screen shaders with damage tracking enabled. The native backend uses tex and v_texcoord, derives dimensions using textureSize(tex, 0), and does not require time or fullSize.

Advanced moving refraction remains disabled: no version-matched development headers or isolated nested-compositor validation are available. A public render event alone does not establish safe background snapshot ownership, render-pass ordering, damage expansion or a shared moving-mask clock. Loading a speculative plugin into the desktop would not establish correctness. No plugin loader or guessed hook is shipped. See BACKENDS.md for the integration requirements.
