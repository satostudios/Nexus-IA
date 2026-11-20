#version 330 core
// NEXUS Core - post pass: chromatic aberration, bloom, glitch, scanlines, grain.
out vec4 fragColor;

uniform sampler2D uScene;
uniform vec2  uRes;
uniform float uTime;
uniform float uCA;      // treble -> RGB separation
uniform float uBloom;   // energy -> bloom
uniform float uGlitch;  // occasional slice displacement
uniform float uGrain;
uniform float uScan;

float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }

void main() {
    vec2 uv = gl_FragCoord.xy / uRes;

    // Glitch: a few horizontal slices jump sideways for a frame.
    float slice = floor(uv.y * 72.0);
    float tick = floor(uTime * 14.0);
    float on = step(1.0 - 0.03 * uGlitch, hash12(vec2(slice, tick)));
    uv.x += on * (hash12(vec2(tick, slice * 1.7)) - 0.5) * 0.035 * uGlitch;

    // Chromatic aberration: radial from the core plus a small lateral split.
    vec2 c = uv - vec2(0.5, 0.56);
    vec2 dir = c * (0.004 + 0.020 * uCA) + vec2(0.0012 + 0.0035 * uCA, 0.0003);
    float r = texture(uScene, uv + dir).r;
    float g = texture(uScene, uv).g;
    float b = texture(uScene, uv - dir * 1.25).b;
    vec3 col = vec3(r, g, b);

    // Bloom from the scene's mip chain (cheap, wide, soft).
    vec3 bloom = textureLod(uScene, uv, 2.0).rgb * 0.40
               + textureLod(uScene, uv, 3.5).rgb * 0.35
               + textureLod(uScene, uv, 5.0).rgb * 0.30
               + textureLod(uScene, uv, 6.5).rgb * 0.25;
    col += bloom * uBloom * vec3(0.86, 0.92, 1.08);

    col *= 1.0 - uScan * (0.5 + 0.5 * sin(gl_FragCoord.y * 3.14159));

    float n = hash12(gl_FragCoord.xy + fract(uTime * 37.0) * vec2(113.0, 71.0)) - 0.5;
    col += n * uGrain;

    vec2 vc = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
    col *= smoothstep(1.35, 0.30, length(vc));

    col = col / (1.0 + col * 0.45);
    fragColor = vec4(max(col, vec3(0.0)), 1.0);
}
