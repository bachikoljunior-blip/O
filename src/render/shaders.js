// GLSL ES 3.00 シェーダ集

const COMMON = /* glsl */`
precision highp float;

float hash12(vec2 p){
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float vnoise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  f = f*f*(3.0-2.0*f);
  float a = hash12(i), b = hash12(i+vec2(1,0));
  float c = hash12(i+vec2(0,1)), d = hash12(i+vec2(1,1));
  return mix(mix(a,b,f.x), mix(c,d,f.x), f.y);
}
float fbm2(vec2 p){
  float v = 0.0, a = 0.5;
  for(int i=0;i<4;i++){ v += a*vnoise(p); p *= 2.03; a *= 0.5; }
  return v;
}
`;

const SKY_FN = /* glsl */`
uniform vec3 u_zenith;
uniform vec3 u_horizon;
uniform vec3 u_sunColor;
uniform vec3 u_sunDir;
uniform float u_night;

vec3 skyColor(vec3 dir){
  float up = clamp(dir.y, -1.0, 1.0);
  float t = pow(1.0 - clamp(up, 0.0, 1.0), 3.2);
  vec3 col = mix(u_zenith, u_horizon, t);
  float sd = max(dot(dir, u_sunDir), 0.0);
  // 太陽近傍の散乱
  col += u_sunColor * pow(sd, 6.0) * 0.30;
  col += u_sunColor * pow(sd, 40.0) * 0.55;
  // 地平線下は地面の反射色へ
  col = mix(col, u_horizon * 0.30, smoothstep(0.0, -0.25, up));
  return col;
}
`;

const SHADOW_FN = /* glsl */`
uniform highp sampler2DShadow u_shadow;
uniform mat4 u_lightVP;
uniform float u_shadowTexel;
uniform float u_shadowRange;

float shadowFactor(vec3 worldPos, float ndl, float distToCam){
  vec4 lp = u_lightVP * vec4(worldPos, 1.0);
  vec3 pc = lp.xyz / lp.w * 0.5 + 0.5;
  if(pc.x < 0.002 || pc.x > 0.998 || pc.y < 0.002 || pc.y > 0.998 || pc.z > 1.0) return 1.0;
  float bias = max(0.0045 * (1.0 - ndl), 0.0010) + distToCam * 0.0000075;
  float s = 0.0;
  for(int y=-1;y<=1;y++){
    for(int x=-1;x<=1;x++){
      vec2 off = vec2(float(x), float(y)) * u_shadowTexel;
      s += texture(u_shadow, vec3(pc.xy + off, pc.z - bias));
    }
  }
  s /= 9.0;
  // 影の適用範囲外へフェード
  float fade = smoothstep(u_shadowRange, u_shadowRange * 0.72, distToCam);
  return mix(1.0, s, fade);
}
`;

/* ------------------------------------------------------------- メッシュ */
export const MESH_VS = /* glsl */`#version 300 es
${COMMON}
layout(location=0) in vec3 a_pos;
layout(location=1) in vec3 a_nrm;
layout(location=2) in vec3 a_col;
layout(location=3) in vec4 i_m0;
layout(location=4) in vec4 i_m1;
layout(location=5) in vec4 i_m2;
layout(location=6) in vec4 i_col;   // rgb=tint, a=alpha
layout(location=7) in vec4 i_extra; // x=wind, y=emissive, z=colorMix, w=unused

uniform mat4 u_viewProj;
uniform float u_time;
uniform float u_wind;
uniform float u_alphaScale;

out vec3 v_world;
out vec3 v_nrm;
out vec3 v_col;
out float v_emissive;
out float v_alpha;
out float v_detail;

void main(){
  vec3 p = a_pos;
  vec3 origin = vec3(i_m0.w, i_m1.w, i_m2.w);
  if(i_extra.x > 0.001){
    float ph = origin.x * 0.42 + origin.z * 0.37 + u_time * 1.7;
    float sway = sin(ph) + 0.45 * sin(ph * 2.37 + 1.7) + 0.2 * sin(ph * 5.1);
    float amt = i_extra.x * u_wind * max(a_pos.y, 0.0);
    p.x += sway * amt * 0.10;
    p.z += cos(ph * 1.13) * amt * 0.07;
  }
  vec3 world = vec3(dot(i_m0, vec4(p,1.0)), dot(i_m1, vec4(p,1.0)), dot(i_m2, vec4(p,1.0)));
  vec3 n = normalize(vec3(dot(i_m0.xyz, a_nrm), dot(i_m1.xyz, a_nrm), dot(i_m2.xyz, a_nrm)));
  v_world = world;
  v_nrm = n;
  v_col = mix(a_col * i_col.rgb, i_col.rgb, i_extra.z);
  v_emissive = i_extra.y;
  v_alpha = i_col.a * u_alphaScale;
  v_detail = i_extra.w;
  gl_Position = u_viewProj * vec4(world, 1.0);
}
`;

export const MESH_FS = /* glsl */`#version 300 es
${COMMON}
${SKY_FN}
${SHADOW_FN}
in vec3 v_world;
in vec3 v_nrm;
in vec3 v_col;
in float v_emissive;
in float v_alpha;
in float v_detail;

uniform vec3 u_camPos;
uniform vec3 u_ambSky;
uniform vec3 u_ambGnd;
uniform float u_fogDensity;
uniform float u_fogHeight;
uniform float u_time;

out vec4 outColor;

void main(){
  if(v_alpha < 0.999){
    if(v_alpha < hash12(gl_FragCoord.xy + fract(u_time) * 13.0)) discard;
  }
  vec3 N = normalize(v_nrm);
  vec3 V = normalize(u_camPos - v_world);
  if(dot(N, V) < 0.0 && v_alpha < 0.999) N = -N;   // 薄板（草）の両面対応

  float ndl = max(dot(N, u_sunDir), 0.0);
  float dist = length(u_camPos - v_world);
  float sh = shadowFactor(v_world, ndl, dist);

  // 地表のディテール（頂点カラーだけでは平坦に見えるため）
  vec3 albedo = v_col;
  if(v_detail > 0.5){
    float fade = exp(-dist * 0.012);
    float d = vnoise(v_world.xz * 0.42) * 0.55 + vnoise(v_world.xz * 2.6) * 0.30
            + vnoise(v_world.xz * 11.0) * 0.15;
    d = mix(0.5, d, fade);
    albedo *= 0.74 + 0.56 * d;
    // 傾斜に応じてわずかに露出した土を混ぜる
    float bare = smoothstep(0.72, 0.30, N.y) * fade;
    albedo = mix(albedo, albedo * vec3(1.18, 0.94, 0.76), bare * 0.6);
  }

  vec3 amb = mix(u_ambGnd, u_ambSky, N.y * 0.5 + 0.5);
  vec3 col = albedo * (amb + u_sunColor * ndl * sh);

  // 簡易スペキュラ
  vec3 H = normalize(u_sunDir + V);
  col += u_sunColor * pow(max(dot(N, H), 0.0), 28.0) * 0.09 * sh;
  // リムライト（シルエットを立たせる）
  float rim = pow(1.0 - max(dot(N, V), 0.0), 4.0);
  col += mix(u_ambSky, u_sunColor, 0.5) * rim * 0.13;
  // 自己発光
  col += albedo * v_emissive;

  // 高度減衰付き指数フォグ
  float hf = exp(-max(v_world.y - u_fogHeight, 0.0) * 0.010);
  float fog = 1.0 - exp(-dist * u_fogDensity * hf);
  vec3 fogCol = skyColor(normalize(v_world - u_camPos));
  col = mix(col, fogCol, clamp(fog, 0.0, 1.0));

  outColor = vec4(col, 1.0);
}
`;

/* ------------------------------------------------------------- 影パス */
export const SHADOW_VS = /* glsl */`#version 300 es
layout(location=0) in vec3 a_pos;
layout(location=3) in vec4 i_m0;
layout(location=4) in vec4 i_m1;
layout(location=5) in vec4 i_m2;
layout(location=6) in vec4 i_col;
layout(location=7) in vec4 i_extra;
uniform mat4 u_lightVP;
uniform float u_time;
uniform float u_wind;
out float v_alpha;
void main(){
  vec3 p = a_pos;
  vec3 origin = vec3(i_m0.w, i_m1.w, i_m2.w);
  if(i_extra.x > 0.001){
    float ph = origin.x * 0.42 + origin.z * 0.37 + u_time * 1.7;
    float sway = sin(ph) + 0.45 * sin(ph * 2.37 + 1.7);
    p.x += sway * i_extra.x * u_wind * max(a_pos.y, 0.0) * 0.10;
  }
  vec3 world = vec3(dot(i_m0, vec4(p,1.0)), dot(i_m1, vec4(p,1.0)), dot(i_m2, vec4(p,1.0)));
  v_alpha = i_col.a;
  gl_Position = u_lightVP * vec4(world, 1.0);
}
`;

export const SHADOW_FS = /* glsl */`#version 300 es
precision mediump float;
in float v_alpha;
void main(){ if(v_alpha < 0.35) discard; }
`;

/* ----------------------------------------------------------------- 空 */
export const SKY_VS = /* glsl */`#version 300 es
layout(location=0) in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 1.0, 1.0); }
`;

export const SKY_FS = /* glsl */`#version 300 es
${COMMON}
${SKY_FN}
in vec2 v_uv;
uniform mat4 u_invViewProj;
uniform vec3 u_camPos;
uniform float u_time;
uniform float u_cloud;      // 雲量 0..1
uniform float u_quality;    // 0=低 1=高
uniform vec3 u_moonDir;
out vec4 outColor;

void main(){
  vec4 ndc = vec4(v_uv * 2.0 - 1.0, 1.0, 1.0);
  vec4 wp = u_invViewProj * ndc;
  vec3 dir = normalize(wp.xyz / wp.w - u_camPos);

  vec3 col = skyColor(dir);

  // 星（夜のみ）
  if(u_night > 0.02 && dir.y > -0.05){
    vec2 sp = dir.xz / max(dir.y + 0.35, 0.08);
    float st = hash12(floor(sp * 130.0));
    float tw = 0.6 + 0.4 * sin(u_time * 2.3 + st * 60.0);
    float star = smoothstep(0.9965, 1.0, st) * tw;
    col += vec3(0.85, 0.9, 1.0) * star * u_night * 2.2;
  }
  // 月
  float md = max(dot(dir, u_moonDir), 0.0);
  col += vec3(0.75, 0.82, 1.0) * pow(md, 900.0) * 3.0 * u_night;
  col += vec3(0.35, 0.42, 0.6) * pow(md, 12.0) * 0.16 * u_night;

  // 雲
  if(u_cloud > 0.01 && dir.y > 0.008){
    vec2 cp = dir.xz / dir.y * 0.55 + vec2(u_time * 0.0055, u_time * 0.0031);
    float d = fbm2(cp * 1.1);
    if(u_quality > 0.5) d = mix(d, fbm2(cp * 2.7 + 5.0), 0.35);
    float cover = smoothstep(0.52 - u_cloud * 0.34, 0.86 - u_cloud * 0.18, d);
    float horizonFade = smoothstep(0.0, 0.22, dir.y);
    float lit = clamp(dot(dir, u_sunDir) * 0.5 + 0.6, 0.0, 1.4);
    vec3 cloudCol = mix(u_horizon * 0.62, u_sunColor * 0.9 + u_zenith * 0.5, lit * 0.6);
    col = mix(col, cloudCol, cover * horizonFade * clamp(u_cloud * 1.3, 0.0, 0.94));
  }

  outColor = vec4(col, 1.0);
}
`;

/* --------------------------------------------------------------- 水面 */
export const WATER_VS = /* glsl */`#version 300 es
layout(location=0) in vec2 a_pos;   // ローカル XZ
uniform mat4 u_viewProj;
uniform vec3 u_camPos;
uniform vec2 u_origin;
uniform float u_time;
uniform float u_seaLevel;
uniform float u_waveScale;
out vec3 v_world;
out vec3 v_nrm;

void wave(vec2 p, out float h, out vec2 grad){
  h = 0.0; grad = vec2(0.0);
  vec2 dirs[4];
  dirs[0]=vec2(1.0,0.35); dirs[1]=vec2(-0.55,0.9); dirs[2]=vec2(0.7,-0.75); dirs[3]=vec2(-0.2,-1.0);
  float amps[4]; amps[0]=0.30; amps[1]=0.20; amps[2]=0.11; amps[3]=0.07;
  float freqs[4]; freqs[0]=0.075; freqs[1]=0.14; freqs[2]=0.26; freqs[3]=0.45;
  float spd[4]; spd[0]=0.7; spd[1]=1.1; spd[2]=1.7; spd[3]=2.4;
  for(int i=0;i<4;i++){
    vec2 d = normalize(dirs[i]);
    float ph = dot(p, d) * freqs[i] + u_time * spd[i];
    h += sin(ph) * amps[i];
    grad += d * cos(ph) * amps[i] * freqs[i];
  }
}

void main(){
  vec2 wp = a_pos + u_origin;
  float h; vec2 g;
  wave(wp, h, g);
  h *= u_waveScale; g *= u_waveScale;
  v_world = vec3(wp.x, u_seaLevel + h, wp.y);
  v_nrm = normalize(vec3(-g.x, 1.0, -g.y));
  gl_Position = u_viewProj * vec4(v_world, 1.0);
}
`;

export const WATER_FS = /* glsl */`#version 300 es
${COMMON}
${SKY_FN}
in vec3 v_world;
in vec3 v_nrm;
uniform vec3 u_camPos;
uniform vec3 u_deepColor;
uniform vec3 u_shallowColor;
uniform float u_fogDensity;
uniform float u_time;
out vec4 outColor;

void main(){
  vec3 N = normalize(v_nrm);
  // 細かなさざ波
  vec2 rp = v_world.xz * 0.9 + vec2(u_time * 0.35, -u_time * 0.24);
  float n1 = vnoise(rp) - 0.5;
  float n2 = vnoise(rp * 2.3 + 11.0) - 0.5;
  N = normalize(N + vec3(n1, 0.0, n2) * 0.22);

  vec3 V = normalize(u_camPos - v_world);
  float fres = pow(1.0 - max(dot(N, V), 0.0), 4.0);
  fres = mix(0.03, 1.0, fres);

  vec3 R = reflect(-V, N);
  vec3 refl = skyColor(normalize(R));
  vec3 body = mix(u_shallowColor, u_deepColor, clamp(length(u_camPos - v_world) * 0.004, 0.0, 1.0));

  vec3 col = mix(body, refl, fres);
  // 太陽のきらめき
  vec3 H = normalize(u_sunDir + V);
  col += u_sunColor * pow(max(dot(N, H), 0.0), 220.0) * 2.4;

  float dist = length(u_camPos - v_world);
  float fog = 1.0 - exp(-dist * u_fogDensity);
  col = mix(col, skyColor(normalize(v_world - u_camPos)), clamp(fog, 0.0, 1.0));

  outColor = vec4(col, clamp(0.62 + fres * 0.38, 0.0, 1.0));
}
`;

/* --------------------------------------------------------- パーティクル */
export const PARTICLE_VS = /* glsl */`#version 300 es
layout(location=0) in vec2 a_corner;
layout(location=1) in vec4 i_posSize;   // xyz=中心, w=サイズ
layout(location=2) in vec4 i_color;     // rgba
layout(location=3) in vec4 i_extra;     // x=回転, y=種別, z=伸長, w=発光
uniform mat4 u_viewProj;
uniform vec3 u_camRight;
uniform vec3 u_camUp;
out vec2 v_uv;
out vec4 v_color;
out float v_kind;
out float v_glow;
void main(){
  float c = cos(i_extra.x), s = sin(i_extra.x);
  vec2 q = vec2(a_corner.x * c - a_corner.y * s, a_corner.x * s + a_corner.y * c);
  q.y *= (1.0 + i_extra.z);
  vec3 world = i_posSize.xyz + (u_camRight * q.x + u_camUp * q.y) * i_posSize.w;
  v_uv = a_corner;
  v_color = i_color;
  v_kind = i_extra.y;
  v_glow = i_extra.w;
  gl_Position = u_viewProj * vec4(world, 1.0);
}
`;

export const PARTICLE_FS = /* glsl */`#version 300 es
${COMMON}
in vec2 v_uv;
in vec4 v_color;
in float v_kind;
in float v_glow;
out vec4 outColor;
void main(){
  float r = length(v_uv);
  float a;
  if(v_kind < 0.5){
    a = smoothstep(1.0, 0.15, r);                    // 柔らかい円
  } else if(v_kind < 1.5){
    a = smoothstep(1.0, 0.0, r) * smoothstep(1.0, 0.55, abs(v_uv.x) * 3.2); // 火花
  } else if(v_kind < 2.5){
    float n = fbm2(v_uv * 2.4 + v_color.r * 20.0);   // 煙
    a = smoothstep(1.0, 0.1, r) * (0.55 + n * 0.7);
  } else {
    a = step(r, 1.0) * (1.0 - r * 0.35);             // 硬い粒（雪・葉）
  }
  a *= v_color.a;
  if(a < 0.01) discard;
  outColor = vec4(v_color.rgb * (1.0 + v_glow * 2.0), a);
}
`;

/* --------------------------------------------------- ポストプロセス */
export const POST_VS = /* glsl */`#version 300 es
layout(location=0) in vec2 a_pos;
out vec2 v_uv;
void main(){ v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

export const BRIGHT_FS = /* glsl */`#version 300 es
precision mediump float;
in vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_threshold;
out vec4 outColor;
void main(){
  vec3 c = texture(u_tex, v_uv).rgb;
  float l = dot(c, vec3(0.2126, 0.7152, 0.0722));
  float k = max(l - u_threshold, 0.0) / max(l, 1e-4);
  outColor = vec4(c * k, 1.0);
}
`;

export const BLUR_FS = /* glsl */`#version 300 es
precision mediump float;
in vec2 v_uv;
uniform sampler2D u_tex;
uniform vec2 u_dir;   // テクセル単位の方向
out vec4 outColor;
void main(){
  vec3 sum = texture(u_tex, v_uv).rgb * 0.2270270270;
  sum += texture(u_tex, v_uv + u_dir * 1.3846153846).rgb * 0.3162162162;
  sum += texture(u_tex, v_uv - u_dir * 1.3846153846).rgb * 0.3162162162;
  sum += texture(u_tex, v_uv + u_dir * 3.2307692308).rgb * 0.0702702703;
  sum += texture(u_tex, v_uv - u_dir * 3.2307692308).rgb * 0.0702702703;
  outColor = vec4(sum, 1.0);
}
`;

export const COMPOSITE_FS = /* glsl */`#version 300 es
${COMMON}
in vec2 v_uv;
uniform sampler2D u_scene;
uniform sampler2D u_bloom;
uniform float u_bloomStrength;
uniform float u_exposure;
uniform float u_vignette;
uniform float u_damage;     // 被弾赤み
uniform float u_desat;      // 瀕死のとき彩度低下
uniform vec3 u_grade;       // カラーグレーディング（乗算）
uniform float u_time;
uniform float u_aberration;
out vec4 outColor;

vec3 aces(vec3 x){
  const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
  return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main(){
  vec2 uv = v_uv;
  vec2 d = uv - 0.5;
  float r2 = dot(d, d);
  vec3 col;
  if(u_aberration > 0.001){
    float k = u_aberration * r2;
    col.r = texture(u_scene, uv + d * k).r;
    col.g = texture(u_scene, uv).g;
    col.b = texture(u_scene, uv - d * k).b;
  } else {
    col = texture(u_scene, uv).rgb;
  }
  col += texture(u_bloom, uv).rgb * u_bloomStrength;

  col *= u_exposure;
  col = aces(col);
  col *= u_grade;

  // 彩度とコントラストを持ち上げて眠い絵を締める
  float luma = dot(col, vec3(0.2126, 0.7152, 0.0722));
  col = mix(vec3(luma), col, 1.24);
  col = clamp((col - 0.5) * 1.14 + 0.5, 0.0, 1.0);
  // シャドウ側をわずかに青へ、ハイライトを暖色へ
  col = mix(col * vec3(0.97, 0.985, 1.035), col * vec3(1.035, 1.0, 0.955), smoothstep(0.25, 0.85, luma));

  // ビネット
  col *= 1.0 - u_vignette * smoothstep(0.18, 0.78, r2);

  // 被弾フィードバック
  if(u_damage > 0.001){
    float edge = smoothstep(0.10, 0.62, r2);
    col = mix(col, vec3(0.55, 0.03, 0.03), edge * u_damage * 0.85);
  }
  if(u_desat > 0.001){
    float l = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(col, vec3(l) * vec3(1.06, 0.92, 0.92), u_desat);
  }

  // わずかなフィルムグレイン（バンディング低減）
  float g = hash12(gl_FragCoord.xy + fract(u_time) * 91.0) - 0.5;
  col += g * 0.012;

  col = pow(max(col, 0.0), vec3(1.0 / 2.2));
  outColor = vec4(col, 1.0);
}
`;
