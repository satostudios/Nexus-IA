#version 330 core
// NEXUS Core - scene pass (HDR). Procedural orb, orbital rings, particles.
out vec4 fragColor;

uniform vec2  uRes;
uniform float uTime;
uniform float uRot;       // integrated orb rotation
uniform float uRing;      // integrated ring precession
uniform float uFlow;      // integrated internal flow (faster while processing)
uniform float uScale;     // core scale (volume, pulse)
uniform float uDistort;   // surface distortion (speech)
uniform float uGlow;      // energy -> glow
uniform float uParticle;  // mid -> particle motion
uniform float uBass;      // bass -> orbital wobble
uniform float uActivity;  // processing -> internal motion
uniform float uPulse;     // success pulse
uniform float uWarn;      // error distortion
uniform float uBoot;      // start-up fade
uniform vec3  uTint;      // subtle state accent

const float TAU = 6.28318530718;

float hash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float hash13(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }

float vnoise(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float n000 = hash13(i);
    float n100 = hash13(i + vec3(1, 0, 0));
    float n010 = hash13(i + vec3(0, 1, 0));
    float n110 = hash13(i + vec3(1, 1, 0));
    float n001 = hash13(i + vec3(0, 0, 1));
    float n101 = hash13(i + vec3(1, 0, 1));
    float n011 = hash13(i + vec3(0, 1, 1));
    float n111 = hash13(i + vec3(1, 1, 1));
    return mix(mix(mix(n000, n100, f.x), mix(n010, n110, f.x), f.y),
               mix(mix(n001, n101, f.x), mix(n011, n111, f.x), f.y), f.z);
}

float fbm(vec3 p) {
    float a = 0.5, s = 0.0;
    for (int i = 0; i < 4; i++) {
        s += a * vnoise(p);
        p = p * 2.03 + vec3(1.7, 9.2, 3.1);
        a *= 0.5;
    }
    return s;
}

mat3 rotY(float a) { float c = cos(a), s = sin(a); return mat3(c, 0.0, -s, 0.0, 1.0, 0.0, s, 0.0, c); }
mat3 rotX(float a) { float c = cos(a), s = sin(a); return mat3(1.0, 0.0, 0.0, 0.0, c, s, 0.0, -s, c); }
mat2 rot2(float a) { float c = cos(a), s = sin(a); return mat2(c, s, -s, c); }

// Thin orbital ring: a circle of radius r inclined by `incl`, rotated by `ang` on screen.
// Returns (intensity, front) where front = 1 for the half facing the viewer.
vec3 ring(vec2 p, float r, float incl, float ang, float width, float px) {
    vec2 q = rot2(-ang) * p;
    float a = r;
    float b = max(r * abs(cos(incl)), 0.0015);
    vec2 k = q / vec2(a, b);
    float lk = max(length(k), 1e-4);
    vec2 grad = q / vec2(a * a, b * b) / lk;
    float d = abs(lk - 1.0) / max(length(grad), 1e-4);
    float w = max(width, px);
    float core = 1.0 - smoothstep(0.0, w * 1.5, d);
    float glow = w / (d + w);
    float front = step(0.0, q.y);   // one side of the ellipse passes in front of the orb
    float along = clamp(q.x / a, -1.0, 1.0);
    return vec3(core + glow * glow * 0.45, front, along);
}

void main() {
    float m = min(uRes.x, uRes.y);
    vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / (0.5 * m);
    p.y -= 0.12;
    float px = 1.0 / (0.5 * m);
    float t = uTime;

    float R = 0.35 * uScale * mix(0.55, 1.0, uBoot);
    vec2 o = p / R;
    float r = length(o);
    vec3 col = vec3(0.0);
    float inside = 0.0;
    float rr = 10.0;

    // ---------------------------------------------------------------- orb
    if (r < 1.8) {
        float ang = atan(o.y, o.x);
        vec3 dir = vec3(cos(ang), sin(ang), 0.0);
        float e1 = fbm(dir * 1.25 + vec3(0.0, 0.0, t * 0.10 + uFlow * 0.55));
        float e2 = vnoise(dir * 3.3 + vec3(t * 0.30 + uFlow * 1.4, 0.0, 1.7));
        float e3 = vnoise(dir * 7.0 + vec3(0.0, t * 5.0, 3.3));
        float lump = 1.0 + (e1 - 0.5) * (0.30 + 0.55 * uDistort) + (e2 - 0.5) * (0.05 + 0.25 * uDistort)
                   + uWarn * 0.10 * (e3 - 0.5);
        rr = r / lump;
        float aa = px / R * 1.5;
        inside = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, rr);

        if (rr < 1.0 + 2.0 * aa) {
            float rc = min(rr, 1.0);
            float z = sqrt(max(0.0, 1.0 - rc * rc));
            vec3 n = normalize(vec3(o / lump, z));
            vec3 q = rotY(uRot) * rotX(0.55 + 0.15 * sin(t * 0.07)) * n;

            vec3 w = q * 1.55 + vec3(0.0, t * 0.035, uFlow * 0.25);
            w += (vec3(fbm(w + 3.1), fbm(w + 7.7), fbm(w + 1.3)) - 0.5) * (0.55 + 1.3 * uActivity);
            float big = fbm(w);
            float cav = smoothstep(0.50, 0.32, big);
            float ridge = 1.0 - abs(big * 2.0 - 1.0);
            float fine = vnoise(q * 24.0 + vec3(t * 0.4));

            vec3 L = normalize(vec3(-0.35, 0.45, 0.82));
            float diff = clamp(dot(n, L), 0.0, 1.0);
            float rim = pow(1.0 - z, 3.0);
            float lum = 0.22 + 0.78 * diff;
            lum *= mix(1.0, 0.14, cav);
            lum *= 0.72 + 0.4 * fine;
            lum += ridge * 0.10;

            // Stochastic stipple: the surface reads as a cloud of particles.
            float g = hash12(gl_FragCoord.xy + floor(t * 24.0) * vec2(17.0, 31.0));
            float surf = mix(lum, step(g, lum), 0.62);
            surf = surf * 0.82 + rim * 0.5;

            // Rare bright sparks inside, more when active.
            float sp = step(0.9982 - 0.004 * uActivity, hash12(floor(gl_FragCoord.xy * 0.5) + floor(t * 9.0)));
            surf += sp * 1.6 * (1.0 - cav);

            col = vec3(surf) * inside * (0.92 + 0.12 * uGlow);
            col += inside * rim * 0.30 * uTint;
            col += inside * vec3(1.0) * uPulse * 0.25 * (1.0 - cav);
        }
        float halo = exp(-max(rr - 1.0, 0.0) * 5.0) * (1.0 - inside);
        col += vec3(0.72, 0.8, 1.0) * halo * (0.04 + 0.14 * uGlow + 0.35 * uPulse);
    }

    // -------------------------------------------------------------- rings
    float rings = 0.0;
    {
        float incl = radians(74.0) + uBass * 0.10 * sin(t * 1.3);
        float ang = radians(27.0) + 0.10 * sin(uRing * 0.31);
        vec3 rg = ring(p, R * 1.68, incl, ang, 0.0020, px);
        rings += rg.x * mix(1.0 - inside, 1.0, rg.y);
        // bead travelling on the ring
        float ta = uRing * 0.9;
        vec2 bead = rot2(ang) * vec2(cos(ta) * R * 1.68, sin(ta) * R * 1.68 * abs(cos(incl)));
        float bd = length(p - bead);
        rings += 0.0012 / (bd + 0.0012) * 0.45 * mix(1.0 - inside, 1.0, step(0.0, sin(ta)));
    }
    {
        float incl = radians(57.0) + uBass * 0.12 * cos(t * 1.1);
        float ang = radians(4.0) + 0.12 * cos(uRing * 0.23);
        vec3 rg = ring(p, R * 1.16, incl, ang, 0.0016, px);
        rings += rg.x * 0.75 * mix(1.0 - inside, 1.0, rg.y);
    }
    {
        // The long edge-on "slash" crossing the whole composition.
        float incl = radians(89.96);
        float ang = radians(36.0) + 0.02 * sin(uRing * 0.17);
        vec3 rg = ring(p, 2.7, incl, ang, 0.0015, px);
        float fade = smoothstep(1.0, 0.2, abs(rg.z));
        rings += rg.x * 0.9 * fade * mix(1.0 - inside, 1.0, rg.y);
    }
    col += vec3(rings) * (0.5 + 0.5 * uGlow) * mix(0.4, 1.0, uBoot);

    // ---------------------------------------------------------- particles
    for (int i = 0; i < 28; i++) {
        float fi = float(i);
        float h1 = hash11(fi * 7.13 + 0.5);
        float h2 = hash11(fi * 3.71 + 1.0);
        float h3 = hash11(fi * 1.93 + 2.0);
        float orbit = R * (1.12 + h1 * 1.7);
        float dirn = h3 > 0.5 ? 1.0 : -1.0;
        float a = h3 * TAU + dirn * (t * (0.04 + h2 * 0.10) + uFlow * 0.25 * h2) * (1.0 + uParticle * 1.8);
        float tilt = 0.25 + (h1 - 0.5) * 0.9;
        vec2 pos = rot2(radians(24.0) + (h2 - 0.5) * 0.8) * vec2(cos(a) * orbit, sin(a) * orbit * tilt);
        pos += vec2(sin(t * (0.7 + h1) + fi), cos(t * (0.9 + h2) + fi)) * 0.01 * uParticle;
        float behind = step(sin(a), 0.0) * inside;
        float d = length(p - pos);
        float s = px * (0.9 + 1.6 * h2);
        col += vec3(0.9, 0.95, 1.0) * (s * s) / (d * d + s * s) * (0.18 + 0.35 * h1) * (1.0 - behind)
             * mix(0.3, 1.0, uBoot);
    }

    // ------------------------------------------------------------- finish
    col = mix(col, col * vec3(1.0, 0.84, 0.70), uWarn * 0.55);
    col *= uBoot;
    fragColor = vec4(col, 1.0);
}
