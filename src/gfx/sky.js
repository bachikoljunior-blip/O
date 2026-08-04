// sky.js — sky dome, sun, fog and weather.
// L4.
//
// On a phone screen, atmospheric perspective does more for the sense of scale
// than geometry does. Fog is not a way to hide the draw distance here; it is
// the primary depth cue, and the weather presets exist mostly to move it.

import * as THREE from '../../vendor/three.module.js';
import { skyAt, createSkyState, WEATHER } from '../data/sky.js';
import { regionWeights, blendPalette } from '../data/regions.js';
import { lerp } from '../core/math.js';

const SKY_VERT = `
varying vec3 vDir;
void main() {
  vDir = normalize(position);
  vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_Position = p.xyww;   // force to the far plane: the dome never occludes
}`;

const SKY_FRAG = `
precision mediump float;
uniform vec3 uZenith;
uniform vec3 uHorizon;
uniform vec3 uSunColor;
uniform vec3 uSunDir;
uniform float uSunI;
varying vec3 vDir;
void main() {
  vec3 d = normalize(vDir);
  float h = clamp(d.y * 0.5 + 0.5, 0.0, 1.0);
  // Bias the gradient down so the horizon band is wide enough to read.
  vec3 col = mix(uHorizon, uZenith, pow(h, 0.55));
  // Sun disc plus a broad forward-scatter halo.
  float sd = max(dot(d, normalize(uSunDir)), 0.0);
  col += uSunColor * pow(sd, 220.0) * 1.8 * uSunI;
  col += uSunColor * pow(sd, 5.0) * 0.16 * uSunI;
  gl_FragColor = vec4(col, 1.0);
}`;

const _w = new Float32Array(3);
const _c = new Float32Array(3);

export function createSky(gfx) {
  const uniforms = {
    uZenith: { value: new THREE.Color(0.2, 0.3, 0.6) },
    uHorizon: { value: new THREE.Color(0.6, 0.7, 0.8) },
    uSunColor: { value: new THREE.Color(1, 1, 1) },
    uSunDir: { value: new THREE.Vector3(0, 1, 0) },
    uSunI: { value: 1 },
  };
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(1, 24, 16),
    new THREE.ShaderMaterial({
      uniforms,
      vertexShader: SKY_VERT,
      fragmentShader: SKY_FRAG,
      side: THREE.BackSide,
      // The dome sits exactly on the far plane, so a default LESS depth test
      // rejects it against the cleared buffer and the sky never appears.
      // Drawing it first with no depth interaction at all is the cheap fix.
      depthWrite: false,
      depthTest: false,
      fog: false,
    })
  );
  dome.frustumCulled = false;
  dome.renderOrder = -1000;
  gfx.scene.add(dome);

  return {
    dome, uniforms,
    state: createSkyState(),
    weather: 'clear',
    weatherBlend: 1,
    fogDensity: WEATHER.clear.fog,
  };
}

/**
 * Update sun, sky and fog for the current in-game hour and weather.
 * Region tinting is applied to the fog so crossing a culture boundary is
 * visible in the air itself, not just in the props.
 */
export function updateSky(sky, gfx, hour, px, pz, seed, dt) {
  const st = skyAt(hour, sky.state);
  const wx = WEATHER[sky.weather] || WEATHER.clear;

  // Sun direction from elevation and azimuth; below the horizon it just goes dark.
  const elev = st.sunElev;
  const dirX = Math.cos(st.sunAzim) * Math.max(0.08, 1 - Math.abs(elev));
  const dirY = Math.max(-0.2, elev);
  const dirZ = Math.sin(st.sunAzim) * Math.max(0.08, 1 - Math.abs(elev));

  sky.uniforms.uZenith.value.setRGB(st.zenith[0], st.zenith[1], st.zenith[2]);
  sky.uniforms.uHorizon.value.setRGB(st.horizon[0], st.horizon[1], st.horizon[2]);
  sky.uniforms.uSunColor.value.setRGB(st.sun[0], st.sun[1], st.sun[2]);
  sky.uniforms.uSunDir.value.set(dirX, dirY, dirZ).normalize();
  sky.uniforms.uSunI.value = st.sunI * wx.sunMul;

  sky.dome.position.set(gfx.camera.position.x, gfx.camera.position.y, gfx.camera.position.z);
  sky.dome.scale.setScalar(gfx.camera.far * 0.85);

  const intensity = Math.max(0, st.sunI * wx.sunMul);
  gfx.sun.color.setRGB(st.sun[0], st.sun[1], st.sun[2]);
  gfx.sun.intensity = intensity;
  gfx.sun.position.set(px + dirX * 90, py(dirY), pz + dirZ * 90);
  gfx.sun.target.position.set(px, 0, pz);
  gfx.sun.target.updateMatrixWorld();
  gfx.sun.castShadow = intensity > 0.12 && gfx.scaleIdx < 3;

  gfx.hemi.color.setRGB(st.amb[0], st.amb[1], st.amb[2]);
  gfx.hemi.intensity = 0.35 + (1 - st.dark) * 0.7;

  // Fog: horizon colour, tinted by the local culture, densified by weather.
  regionWeights(px, pz, seed, _w);
  blendPalette(_w, 'fog', _c);
  const target = wx.fog;
  sky.fogDensity = lerp(sky.fogDensity, target, Math.min(1, dt * 0.6));
  gfx.scene.fog.density = sky.fogDensity;
  gfx.scene.fog.color.setRGB(
    lerp(st.horizon[0], _c[0] / 255, 0.45) * wx.fogBoost[0],
    lerp(st.horizon[1], _c[1] / 255, 0.45) * wx.fogBoost[1],
    lerp(st.horizon[2], _c[2] / 255, 0.45) * wx.fogBoost[2]
  );

  function py(dy) { return 40 + dy * 110; }
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
