#version 300 es
// STATIC proof of real screen sampling. No animation, capture or damage changes.
// Hyprland supplies the composited desktop as tex. Not an overlay mainImage shader.
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 aspect = vec2(size.x / size.y, 1.0);
    vec2 q = (v_texcoord - vec2(0.5)) * aspect;
    float radius = length(q) / 0.24;
    float mask = 1.0 - smoothstep(0.85, 1.0, radius);
    // Gradient of a shallow spherical cap; displace the actual source texture.
    vec2 normalXY = -2.0 * q / 0.24;
    vec2 offset = normalXY * 0.018 * mask / aspect;
    vec2 margin = 0.5 / size;
    vec2 sampleUV = clamp(v_texcoord + offset, margin, 1.0 - margin);
    fragColor = texture(tex, sampleUV);
}
