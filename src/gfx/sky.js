// sky.js — sky dome, sun, fog, weather, and the one place the shared lighting
// uniforms are driven from.
// L4.
//
// On a phone screen, atmospheric perspective does more for the sense of scale
// than geometry does. Fog is not a way to hide the draw distance here; it is
// the primary depth cue, and the weather presets exist mostly to move it.
//
// Everything gfx/materials.js injects into the forward materials — sky
// ambient, wrap, rim, cloud shadows, height fog, the time-of-day grade — is
// updated from updateSky() below. One call per frame, one write per uniform,
// and every surface in the world moves together. That is the point of holding
// the uniforms by shared reference rather than per material.

import * as THREE from '../../vendor/three.module.js';
import { skyAt, createSkyState, WEATHER, gradeAt, createGradeState } from '../data/sky.js';
import { regionWeights, blendPalette } from '../data/regions.js';
import { seaLevelMetres } from '../sim/world/heightfield.js';
import { lerp, clamp01 } from '../core/math.js';
import { sharedLighting, GLSL_NOISE, GLSL_GRADE } from './materials.js';

const SKY_VERT = `
varying vec3 vDir;
void main() {
  vDir = normalize(position);
  vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_Position = p.xyww;   // force to the far plane: the dome never occludes
}`;

// The dome is tone-mapped and colour-converted by hand. three.js does that
// automatically for its own materials but not for a ShaderMaterial, and
// without it the sky is written as if it were already display-encoded — which
// is why it used to sit several stops darker than the ground it lit.
const SKY_FRAG = `
#include <common>
${GLSL_NOISE}
${GLSL_GRADE}
uniform vec3 uSkyZenith;
uniform vec3 uSkyHorizon;
uniform vec3 uSunTint;
uniform vec3 uSunDirW;
uniform float uSunI;
uniform vec4 uCloud;
uniform float uCloudFine;
uniform float uCloudDome;
uniform vec3 uCloudLit;
uniform vec3 uCloudDark;
varying vec3 vDir;

float sCloudFbm( vec2 p ) {
  float v = sNoise( p ) * 0.62 + sNoise( p * 2.17 + 4.7 ) * 0.28;
  if ( uCloudFine <= 0.0 ) return v / 0.90;
  return v + sNoise( p * 4.63 + 9.1 ) * 0.10;
}

void main() {
  vec3 d = normalize( vDir );
  float h = clamp( d.y * 0.5 + 0.5, 0.0, 1.0 );
  // Bias the gradient down so the horizon band is wide enough to read.
  vec3 col = mix( uSkyHorizon, uSkyZenith, pow( h, 0.55 ) );
  // Sun disc plus a broad forward-scatter halo.
  float sd = max( dot( d, uSunDirW ), 0.0 );
  col += uSunTint * pow( sd, 220.0 ) * 1.8 * uSunI;
  col += uSunTint * pow( sd, 5.0 ) * 0.16 * uSunI;

  // Cloud deck. Zero extra geometry: the view ray is projected onto a flat
  // slab about 500 m up, which is the same noise scale the ground shadows
  // use, so the two read as one weather system.
  float up = max( d.y, 0.0 );
  if ( uCloudDome > 0.0 && up > 0.004 ) {
    vec2 cp = d.xz / ( up + 0.09 ) * 0.85 + uCloud.xy;
    float n = sCloudFbm( cp );
    float cov = smoothstep( uCloud.z, uCloud.z + 0.20, n );
    vec3 cc = mix( uCloudLit, uCloudDark, smoothstep( uCloud.z + 0.02, uCloud.z + 0.34, n ) );
    cc += uSunTint * pow( sd, 8.0 ) * 0.30 * uSunI;
    col = mix( col, cc, cov * smoothstep( 0.0, 0.14, up ) * uCloudDome );
  }

  gl_FragColor = vec4( col, 1.0 );
  #include <tonemapping_fragment>
  gl_FragColor.rgb = sGrade( gl_FragColor.rgb );
  #include <colorspace_fragment>
}`;

/**
 * Quality ladder for the atmosphere, indexed by gfx.scaleIdx.
 * Nothing here forces a shader recompile: every term is behind a UNIFORM
 * branch, which is coherent across the whole draw and costs nothing to skip.
 */
const QUALITY = [
  { fine: 1, shadow: 1.00, rim: 0.62, dome: 1.00 },
  { fine: 1, shadow: 1.00, rim: 0.56, dome: 1.00 },
  { fine: 0, shadow: 0.85, rim: 0.46, dome: 0.85 },
  { fine: 0, shadow: 0.55, rim: 0.30, dome: 0.60 },
  { fine: 0, shadow: 0.00, rim: 0.00, dome: 0.00 },
];

const _w = new Float32Array(3);
const _c = new Float32Array(3);
const _b = new Float32Array(3);

export function createSky(gfx, lit = sharedLighting()) {
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(1, 24, 16),
    new THREE.ShaderMaterial({
      uniforms: Object.assign({}, lit.uniforms),
      vertexShader: SKY_VERT,
      fragmentShader: SKY_FRAG,
      side: THREE.BackSide,
      // The dome sits exactly on the far plane, so a default LESS depth test
      // rejects it against the cleared buffer and the sky never appears. The
      // fix is LEQUAL rather than no test at all: now that this shader runs
      // cloud noise, drawing it LAST in the opaque pass lets the tiler reject
      // every pixel the terrain already covers, which on a typical framing is
      // most of them. It still writes no depth, so it never occludes.
      depthWrite: false,
      depthTest: true,
      depthFunc: THREE.LessEqualDepth,
      fog: false,
    })
  );
  dome.frustumCulled = false;
  dome.renderOrder = 1000;
  gfx.scene.add(dome);

  return {
    dome, lit,
    uniforms: lit.uniforms,
    state: createSkyState(),
    grade: createGradeState(),
    weather: 'clear',
    weatherBlend: 1,
    fogDensity: WEATHER.clear.fog,
    t: 0,
  };
}

/**
 * Update sun, sky, fog and every shared material uniform for the current
 * in-game hour and weather.
 * Region tinting is applied to the fog so crossing a culture boundary is
 * visible in the air itself, not just in the props.
 */
export function updateSky(sky, gfx, hour, px, pz, seed, dt) {
  const st = skyAt(hour, sky.state);
  const wx = WEATHER[sky.weather] || WEATHER.clear;
  const u = sky.lit.uniforms;
  const q = QUALITY[Math.min(Math.max(gfx.scaleIdx | 0, 0), QUALITY.length - 1)];
  sky.t += dt;

  // Sun direction from elevation and azimuth; below the horizon it just goes dark.
  const elev = st.sunElev;
  const dirX = Math.cos(st.sunAzim) * Math.max(0.08, 1 - Math.abs(elev));
  const dirY = Math.max(-0.2, elev);
  const dirZ = Math.sin(st.sunAzim) * Math.max(0.08, 1 - Math.abs(elev));
  const intensity = Math.max(0, st.sunI * wx.sunMul);
  const day = Math.min(1.3, intensity);

  u.uSkyZenith.value.setRGB(st.zenith[0], st.zenith[1], st.zenith[2]);
  u.uSkyHorizon.value.setRGB(st.horizon[0], st.horizon[1], st.horizon[2]);
  u.uSunTint.value.setRGB(st.sun[0], st.sun[1], st.sun[2]);
  u.uSunDirW.value.set(dirX, dirY, dirZ).normalize();
  u.uSunI.value = intensity;
  u.uTime.value = sky.t;

  sky.dome.position.copy(gfx.camera.position);
  sky.dome.scale.setScalar(gfx.camera.far * 0.85);

  gfx.sun.color.setRGB(st.sun[0], st.sun[1], st.sun[2]);
  gfx.sun.intensity = intensity;
  gfx.sun.position.set(px + dirX * 90, 40 + dirY * 110, pz + dirZ * 90);
  gfx.sun.target.position.set(px, 0, pz);
  gfx.sun.target.updateMatrixWorld();
  gfx.sun.castShadow = intensity > 0.12 && gfx.scaleIdx < 3;

  // The hemisphere light is now a floor rather than the whole ambient answer:
  // patched materials get a real sky gradient on top of it, and anything not
  // yet patched still reads. Keeping both is what stops a half-migrated scene
  // from having two different ambient models.
  gfx.hemi.color.setRGB(st.amb[0], st.amb[1], st.amb[2]);
  gfx.hemi.intensity = 0.28 + (1 - st.dark) * 0.32;
  u.uAmbI.value = 0.52 + (1 - st.dark) * 0.34;
  // A soft terminator at noon, softer still at dusk when the light is all bounce.
  u.uWrap.value = 0.24 + st.dark * 0.28;
  // Kept deliberately low: the rim is added as emission, so it is NOT divided
  // by pi the way the diffuse terms are. A value that looks small here is
  // already comparable to the whole lit response of the surface.
  u.uRim.value.set(q.rim * (0.10 + day * 0.22), 3.0);

  // ——— clouds: one field, three consumers (dome, ground shadow, water) ———
  const scroll = sky.t * (0.0016 + wx.wind * 0.0042);
  u.uCloud.value.set(scroll, scroll * 0.42, wx.cover,
    wx.shadow * q.shadow * clamp01(intensity * 1.6));
  u.uCloudFine.value = q.fine;
  u.uCloudDome.value = wx.dome * q.dome;
  const litK = 0.55 + day * 0.75;
  const darkK = 0.28 + day * 0.32;
  u.uCloudLit.value.setRGB(
    lerp(st.horizon[0], st.sun[0], 0.55) * litK,
    lerp(st.horizon[1], st.sun[1], 0.55) * litK,
    lerp(st.horizon[2], st.sun[2], 0.55) * litK);
  u.uCloudDark.value.setRGB(
    lerp(st.zenith[0], st.horizon[0], 0.40) * darkK,
    lerp(st.zenith[1], st.horizon[1], 0.40) * darkK,
    lerp(st.zenith[2], st.horizon[2], 0.40) * darkK);

  // Fog: horizon colour, tinted by the local culture, densified by weather.
  regionWeights(px, pz, seed, _w);
  blendPalette(_w, 'fog', _c);
  sky.fogDensity = lerp(sky.fogDensity, wx.fog, Math.min(1, dt * 0.6));
  gfx.scene.fog.density = sky.fogDensity;
  gfx.scene.fog.color.setRGB(
    lerp(st.horizon[0], _c[0] / 255, 0.45) * wx.fogBoost[0],
    lerp(st.horizon[1], _c[1] / 255, 0.45) * wx.fogBoost[1],
    lerp(st.horizon[2], _c[2] / 255, 0.45) * wx.fogBoost[2]
  );
  // Same colour for the in-shader atmosphere, so distant terrain dissolves
  // into the dome instead of into a grey that belongs to neither.
  u.uFogColor.value.copy(gfx.scene.fog.color);
  u.uFogA.value.set(sky.fogDensity, wx.fogH, seaLevelMetres(), wx.scatter);

  // Ground bounce: the underside of everything is lit by the local soil.
  blendPalette(_w, 'soil', _b);
  const bk = (0.09 + day * 0.13) / 255;
  u.uBounce.value.setRGB(_b[0] * bk, _b[1] * bk, _b[2] * bk);

  gradeAt(hour, sky.grade);
  u.uGradeLift.value.fromArray(sky.grade.lift);
  u.uGradeInvGamma.value.fromArray(sky.grade.invGamma);
  u.uGradeGain.value.fromArray(sky.grade.gain);
}

/** Weather director: biome-driven, slow, and never mid-fight abrupt. */
export function stepWeather(sky, rng, dt, moisture, temperature) {
  sky.weatherT = (sky.weatherT || 0) - dt;
  if (sky.weatherT > 0) return;
  sky.weatherT = 60 + rng() * 180;
  const r = rng();
  if (temperature < 0.25) sky.weather = r < 0.4 ? 'snow' : r < 0.7 ? 'cloud' : 'clear';
  else if (moisture > 0.68) sky.weather = r < 0.45 ? 'rain' : r < 0.7 ? 'fog' : 'cloud';
  else if (moisture < 0.28) sky.weather = r < 0.2 ? 'ash' : 'clear';
  else sky.weather = r < 0.55 ? 'clear' : r < 0.85 ? 'cloud' : 'fog';
}
