// materials.js — the one stylised lighting model, shared by every surface.
// L4.
//
// The constraint that shapes this file: NO POST-PROCESSING. A full-resolution
// dependent read is bandwidth murder on a tile-based GPU, so anything that
// would normally be a screen-space pass — ambient occlusion, atmospheric
// scattering, colour grading — has to happen inside the forward material or
// not at all. So it happens inside the forward material.
//
// Rather than write a material from scratch and lose three.js's light, shadow
// and clipping plumbing, we patch MeshLambertMaterial through onBeforeCompile
// and inject five things:
//
//   SKY AMBIENT   irradiance interpolated between the zenith and horizon
//                 colours by the world normal, plus a ground-bounce term. One
//                 hemisphere light gives you a single number per normal; this
//                 gives you a surface that knows what part of the sky it sees.
//   WRAP DIFFUSE  N.L shifted and rescaled so the terminator is a band rather
//                 than a line. Untextured low-poly geometry under a hard
//                 Lambert terminator reads as painted clay.
//   RIM           a sun-driven Fresnel edge. On stylised geometry the
//                 silhouette carries the read, and a rim is what separates a
//                 silhouette from whatever is behind it.
//   CLOUD SHADOW  two octaves of scrolling value noise sampled by world XZ,
//                 multiplied into the direct sun. Twenty lines, and it is the
//                 single largest visual return in the file: it breaks up
//                 kilometre-scale flat lighting and makes the light move.
//   ATMOSPHERE    height fog with forward scattering, applied in LINEAR space
//                 before the tone map (three.js applies its own fog after, in
//                 display space, which is why distant terrain used to go grey
//                 rather than going to sky colour), then the time-of-day
//                 lift/gamma/gain grade.
//
// Everything is driven by uniform OBJECTS shared by reference, so one write
// per frame in updateSky() moves every material in the scene at once. Every
// expensive term is behind a uniform branch — uniform branches are coherent
// across the whole draw and effectively free — so the quality ladder can turn
// terms off without recompiling a single shader.

import * as THREE from '../../vendor/three.module.js';

/** Hash-without-sine. sin() based hashes band badly on some mobile drivers. */
export const GLSL_NOISE = /* glsl */`
float sHash( vec2 p ) {
  vec3 q = fract( p.xyx * 0.1031 );
  q += dot( q, q.yzx + 33.33 );
  return fract( ( q.x + q.y ) * q.z );
}
float sNoise( vec2 p ) {
  vec2 i = floor( p ), f = fract( p );
  vec2 u = f * f * ( 3.0 - 2.0 * f );
  float a = sHash( i ), b = sHash( i + vec2( 1.0, 0.0 ) );
  float c = sHash( i + vec2( 0.0, 1.0 ) ), d = sHash( i + vec2( 1.0, 1.0 ) );
  return mix( mix( a, b, u.x ), mix( c, d, u.x ), u.y );
}`;

/** Lift / gamma / gain, applied after the tone map. Also used by the sky dome. */
export const GLSL_GRADE = /* glsl */`
uniform vec3 uGradeLift;
uniform vec3 uGradeInvGamma;
uniform vec3 uGradeGain;
vec3 sGrade( vec3 c ) {
  c = c * uGradeGain + uGradeLift * ( 1.0 - c );
  return pow( max( c, vec3( 0.0 ) ), uGradeInvGamma );
}`;

/** Sky ambient, rim, cloud shadow and atmosphere. Needs GLSL_NOISE before it. */
export const GLSL_LIGHTING = /* glsl */`
uniform vec3  uSunDirW;
uniform vec3  uSunTint;
uniform float uSunI;
uniform vec3  uSkyZenith;
uniform vec3  uSkyHorizon;
uniform vec3  uBounce;
uniform float uAmbI;
uniform float uWrap;
uniform vec2  uRim;
uniform vec4  uCloud;
uniform float uCloudFine;
uniform vec4  uFogA;
uniform vec3  uFogColor;
uniform float uTime;

// World position of the fragment. A global rather than a local because the
// patched RE_Direct needs it and three.js calls that from inside its own loop.
vec3 sWPos;

float sCloudShadow( vec2 xz ) {
  if ( uCloud.w <= 0.0 ) return 1.0;
  vec2 p = xz * 0.0016 + uCloud.xy;
  float n = sNoise( p ) * 0.66;
  // A statement, not a ternary: a ternary lets the compiler evaluate both
  // sides, and skipping the second octave is the whole point of the branch.
  if ( uCloudFine > 0.0 ) n += sNoise( p * 2.73 + 5.3 ) * 0.34;
  else n += 0.17;
  return 1.0 - uCloud.w * smoothstep( uCloud.z, uCloud.z + 0.26, n );
}

/**
 * Cosine-weighted irradiance from a two-colour sky.
 *
 * Not a straight lerp between the horizon and zenith colours: the horizon
 * entry is a HAZE colour and is much brighter than the zenith, so a naive lerp
 * lights a wall harder than it lights the ground above it and the world goes
 * inside out. An upward normal sees mostly zenith, a wall sees half a sky
 * weighted to the horizon plus half a ground, and a downward normal sees only
 * bounce — which is what vis and the 0.25..0.70 blend encode.
 */
vec3 sSkyIrradiance( vec3 n ) {
  float t = saturate( n.y * 0.5 + 0.5 );
  vec3 sky = mix( uSkyHorizon, uSkyZenith, 0.25 + 0.45 * t );
  float vis = t * t * ( 3.0 - 2.0 * t );
  return mix( uBounce, sky, vis ) * uAmbI;
}

/** v points from the surface toward the camera. */
vec3 sRim( vec3 n, vec3 v ) {
  if ( uRim.x <= 0.0 ) return vec3( 0.0 );
  float f = pow( 1.0 - saturate( dot( n, v ) ), uRim.y );
  float back = saturate( dot( -v, uSunDirW ) );
  return uSunTint * ( f * uRim.x * ( 0.22 + 0.78 * back * back ) );
}

/**
 * Analytic optical depth through an exponentially thinning fog slab.
 * Keeping the squared distance term means the tuned FogExp2 densities in
 * data/sky.js still mean what they meant.
 */
float sFogHeight( vec3 wpos ) {
  float hf = uFogA.y;
  float base = min( exp( -hf * ( cameraPosition.y - uFogA.z ) ), 6.0 );
  float k = hf * ( wpos.y - cameraPosition.y );
  float integral = ( abs( k ) > 0.0001 ) ? ( 1.0 - exp( -k ) ) / k : 1.0;
  return base * clamp( integral, 0.0, 8.0 );
}

/** viewW points from the camera toward the fragment. */
vec3 sAtmosphere( vec3 color, vec3 wpos, vec3 viewW ) {
  float d0 = uFogA.x * length( wpos - cameraPosition );
  float f = 1.0 - exp( -d0 * d0 * sFogHeight( wpos ) );
  float ct = saturate( dot( viewW, uSunDirW ) );
  float ct2 = ct * ct;
  vec3 fogCol = uFogColor + uSunTint * ( uFogA.w * uSunI * ( 0.30 * ct + 0.70 * ct2 * ct2 ) );
  return mix( color, fogCol, saturate( f ) );
}`;

/**
 * Replace three.js's Lambert direct term with a wrapped, cloud-shadowed one.
 * The scene carries exactly one direct light (the sun), so applying the cloud
 * layer to every direct light is not an approximation here.
 */
const GLSL_DIRECT = /* glsl */`
void RE_Direct_Stylised( const in IncidentLight directLight, const in vec3 geometryPosition,
    const in vec3 geometryNormal, const in vec3 geometryViewDir,
    const in vec3 geometryClearcoatNormal, const in LambertMaterial material,
    inout ReflectedLight reflectedLight ) {
  float ndl = dot( geometryNormal, directLight.direction );
  float dotNL = saturate( ( ndl + uWrap ) / ( 1.0 + uWrap ) );
  vec3 irradiance = dotNL * directLight.color * sCloudShadow( sWPos.xz );
  reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#undef RE_Direct
#define RE_Direct RE_Direct_Stylised`;

// World position and world normal are reconstructed from the view-space
// varying three.js already interpolates, so this costs six dot products and
// not one extra varying. Interpolator bandwidth is a real cost on mobile.
const GLSL_SETUP = /* glsl */`
  sWPos = cameraPosition + ( vec4( -vViewPosition, 0.0 ) * viewMatrix ).xyz;
  vec3 sViewW = normalize( cameraPosition - sWPos );
  vec3 sNormalW = normalize( ( vec4( normal, 0.0 ) * viewMatrix ).xyz );`;

const GLSL_AMBIENT = /* glsl */`
  reflectedLight.indirectDiffuse += sSkyIrradiance( sNormalW ) * BRDF_Lambert( material.diffuseColor );`;

const GLSL_RIM = /* glsl */`
  totalEmissiveRadiance += sRim( sNormalW, sViewW ) * ( 0.35 + 0.65 * diffuseColor.rgb );`;

const GLSL_ATMO = /* glsl */`
  gl_FragColor.rgb = sAtmosphere( gl_FragColor.rgb, sWPos, -sViewW );`;

const GLSL_APPLY_GRADE = /* glsl */`
  gl_FragColor.rgb = sGrade( gl_FragColor.rgb );`;

/**
 * The shared uniform block. Hand the SAME object to every material and one
 * write per frame moves the whole scene; three.js reads `.value` at draw time.
 */
export function createLighting() {
  return {
    uniforms: {
      uSunDirW: { value: new THREE.Vector3(0, 1, 0) },
      uSunTint: { value: new THREE.Color(1, 1, 1) },
      uSunI: { value: 1 },
      uSkyZenith: { value: new THREE.Color(0.18, 0.30, 0.60) },
      uSkyHorizon: { value: new THREE.Color(0.62, 0.72, 0.82) },
      uBounce: { value: new THREE.Color(0.10, 0.09, 0.07) },
      uAmbI: { value: 0.6 },
      uWrap: { value: 0.32 },
      uRim: { value: new THREE.Vector2(0.55, 3.0) },
      /** xy = scroll offset, z = coverage threshold, w = strength (0 disables). */
      uCloud: { value: new THREE.Vector4(0, 0, 0.55, 0.35) },
      uCloudFine: { value: 1 },
      /** Opacity of the SAME cloud field painted on the sky dome. */
      uCloudDome: { value: 0.7 },
      uCloudLit: { value: new THREE.Color(1, 1, 1) },
      uCloudDark: { value: new THREE.Color(0.36, 0.40, 0.48) },
      /** x = density, y = 1/scale-height, z = base altitude, w = inscatter. */
      uFogA: { value: new THREE.Vector4(0.00018, 0.009, 193, 0.85) },
      uFogColor: { value: new THREE.Color(0.55, 0.62, 0.72) },
      uGradeLift: { value: new THREE.Vector3(0, 0, 0) },
      uGradeInvGamma: { value: new THREE.Vector3(1, 1, 1) },
      uGradeGain: { value: new THREE.Vector3(1, 1, 1) },
      uTime: { value: 0 },
    },
  };
}

let _shared = null;
/** Lazy singleton, so construction order between sky and terrain never matters. */
export function sharedLighting() {
  if (!_shared) _shared = createLighting();
  return _shared;
}

/**
 * Patch a MeshLambertMaterial in place. three.js keeps supplying lights,
 * shadows and clipping; we take over ambient, the direct term, the rim, the
 * fog and the grade.
 *
 * Every patched material injects byte-identical source, so they all share one
 * compiled program per three.js parameter set — hence the constant cache key.
 */
export function patchLambert(material, lit = sharedLighting()) {
  material.fog = false;          // superseded by sAtmosphere, in linear space
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, lit.uniforms);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>',
        '#include <common>' + GLSL_NOISE + GLSL_GRADE + GLSL_LIGHTING)
      .replace('#include <lights_lambert_pars_fragment>',
        '#include <lights_lambert_pars_fragment>' + GLSL_DIRECT)
      .replace('#include <normal_fragment_begin>',
        '#include <normal_fragment_begin>' + GLSL_SETUP)
      .replace('#include <emissivemap_fragment>',
        '#include <emissivemap_fragment>' + GLSL_RIM)
      .replace('#include <lights_fragment_end>',
        '#include <lights_fragment_end>' + GLSL_AMBIENT)
      .replace('#include <opaque_fragment>',
        '#include <opaque_fragment>' + GLSL_ATMO)
      .replace('#include <tonemapping_fragment>',
        '#include <tonemapping_fragment>' + GLSL_APPLY_GRADE);
  };
  material.customProgramCacheKey = () => 'stylised-lambert-1';
  material.needsUpdate = true;
  return material;
}

// ————————————————————————————————— water

const WATER_VERT = /* glsl */`
attribute float aDepth;
uniform float uTime;
varying vec3 vWPos;
varying float vDepth;
void main() {
  vec4 wp = modelMatrix * vec4( position, 1.0 );
  // A swell, tapered to nothing at the waterline so the shore never gaps.
  float t = clamp( aDepth * 0.34, 0.0, 1.0 );
  wp.y += ( sin( wp.x * 0.055 + uTime * 1.15 ) * 0.055
          + sin( wp.z * 0.041 - uTime * 0.87 ) * 0.045 ) * t;
  vWPos = wp.xyz;
  vDepth = aDepth;
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const WATER_FRAG = /* glsl */`
#include <common>
${GLSL_NOISE}
${GLSL_GRADE}
${GLSL_LIGHTING}
uniform vec3 uShallow;
uniform vec3 uDeep;
uniform vec3 uFoam;
uniform float uOpacity;
varying vec3 vWPos;
varying float vDepth;

/** Three travelling ripple trains at different scale, speed and heading. */
vec3 sWaveNormal( vec2 p, float t ) {
  vec2 d1 = vec2( 0.94, 0.34 ), d2 = vec2( -0.42, 0.91 ), d3 = vec2( 0.31, -0.95 );
  vec2 g = d1 * ( cos( dot( p, d1 ) * 0.42 + t * 1.15 ) * 0.055 )
         + d2 * ( cos( dot( p, d2 ) * 0.19 - t * 0.72 ) * 0.085 )
         + d3 * ( cos( dot( p, d3 ) * 1.55 + t * 2.10 ) * 0.014 );
  return normalize( vec3( -g.x, 1.0, -g.y ) );
}

void main() {
  vec3 n = sWaveNormal( vWPos.xz, uTime );
  vec3 v = normalize( cameraPosition - vWPos );
  vec3 r = reflect( -v, n );

  float fres = 0.03 + 0.97 * pow( 1.0 - saturate( dot( n, v ) ), 5.0 );

  float ry = saturate( r.y );
  vec3 sky = mix( uSkyHorizon, uSkyZenith, ry * ry );
  float sd = saturate( dot( r, uSunDirW ) );
  sky += uSunTint * ( pow( sd, 90.0 ) * 2.4 + pow( sd, 6.0 ) * 0.14 ) * uSunI;

  vec3 light = sSkyIrradiance( vec3( 0.0, 1.0, 0.0 ) )
             + uSunTint * ( uSunI * 0.30 * sCloudShadow( vWPos.xz ) );
  vec3 body = mix( uShallow, uDeep, saturate( vDepth * 0.13 ) ) * light;
  vec3 col = mix( body, sky, fres );

  // Shoreline: the seabed rising to meet the surface is the only depth cue we
  // can afford, and it is the one that makes a plane read as water.
  float shore = 1.0 - smoothstep( 0.0, 1.8, vDepth );
  float churn = sNoise( vWPos.xz * 0.42 + vec2( uTime * 0.16, -uTime * 0.11 ) );
  float foam = smoothstep( 0.42, 0.92, shore * ( 0.55 + 0.75 * churn ) );
  col = mix( col, uFoam * light, foam );

  float a = max( mix( 0.5, uOpacity, saturate( vDepth * 0.45 ) ), foam );
  gl_FragColor = vec4( sAtmosphere( col, vWPos, -v ), a );
  #include <tonemapping_fragment>
  ${GLSL_APPLY_GRADE}
  #include <colorspace_fragment>
}`;

/**
 * Water. Not a patched Lambert: it wants a Fresnel-weighted sky reflection
 * rather than a diffuse response, and it does not want shadows.
 * Geometry must supply an `aDepth` attribute in metres of water.
 */
export function createWaterMaterial(lit = sharedLighting(), opts = {}) {
  return new THREE.ShaderMaterial({
    uniforms: Object.assign({
      uShallow: { value: new THREE.Color(opts.shallow || 0x3c7f7c) },
      uDeep: { value: new THREE.Color(opts.deep || 0x11293c) },
      uFoam: { value: new THREE.Color(0xdfeaec) },
      uOpacity: { value: opts.opacity === undefined ? 0.94 : opts.opacity },
    }, lit.uniforms),
    vertexShader: WATER_VERT,
    fragmentShader: WATER_FRAG,
    transparent: opts.transparent !== false,
    side: THREE.DoubleSide,
    fog: false,
  });
}
