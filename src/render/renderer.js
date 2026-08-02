// レンダラ：影 → 空 → 地形/プロップ/草/アクター → 水 → パーティクル → ポストプロセス

import {
  Program, Batch, Geometry, RenderTarget, ShadowMap, fullscreenQuad, INST_FLOATS,
} from '../core/gl.js';
import {
  MESH_VS, MESH_FS, SHADOW_VS, SHADOW_FS, SKY_VS, SKY_FS,
  WATER_VS, WATER_FS, PARTICLE_VS, PARTICLE_FS,
  POST_VS, BRIGHT_FS, BLUR_FS, COMPOSITE_FS, RAYS_FS, LINDEPTH_FS,
} from './shaders.js';
import { buildModels } from './mesh.js';
import { m4, v3, Frustum, clamp, clamp01, lerp } from '../core/math.js';
import { writeInstance } from './instance.js';
import { CHUNK } from '../world/terrain.js';

const IDENTITY_INSTANCE = (() => {
  const a = new Float32Array(INST_FLOATS);
  writeInstance(a, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1);
  return a;
})();

export class Renderer {
  constructor(gl, quality) {
    this.gl = gl;
    this.q = quality;
    this.models = buildModels(gl);
    this.batches = new Map();
    this.frustum = new Frustum();
    this.stats = { draws: 0, instances: 0, chunks: 0, tris: 0 };

    this.progMesh = new Program(gl, MESH_VS, MESH_FS, 'mesh');
    this.progShadow = new Program(gl, SHADOW_VS, SHADOW_FS, 'shadow');
    this.progSky = new Program(gl, SKY_VS, SKY_FS, 'sky');
    this.progWater = new Program(gl, WATER_VS, WATER_FS, 'water');
    this.progParticle = new Program(gl, PARTICLE_VS, PARTICLE_FS, 'particle');
    this.progBright = new Program(gl, POST_VS, BRIGHT_FS, 'bright');
    this.progBlur = new Program(gl, POST_VS, BLUR_FS, 'blur');
    this.progRays = new Program(gl, POST_VS, RAYS_FS, 'rays');
    this.progLinDepth = new Program(gl, POST_VS, LINDEPTH_FS, 'lindepth');
    this.progComposite = new Program(gl, POST_VS, COMPOSITE_FS, 'composite');
    this.sunUV = [0.5, 0.5, -1];
    this.rayStrength = 0;

    this.quad = fullscreenQuad(gl);
    this.shadow = new ShadowMap(gl, quality.shadowSize);
    this.lightVP = m4.new();
    this.lightView = m4.new();
    this.lightProj = m4.new();

    this.waterSize = 900;
    this.water = this.buildWaterGrid(gl, this.waterSize, 92);
    this.scene = null;
    this.bloomA = null;
    this.bloomB = null;
    this.width = 1; this.height = 1;

    this.damage = 0;
    this.aberration = 0;
  }

  buildWaterGrid(gl, size, res) {
    const verts = new Float32Array((res + 1) * (res + 1) * 2);
    let o = 0;
    for (let j = 0; j <= res; j++) {
      for (let i = 0; i <= res; i++) {
        // 中心を密に（遠方は粗く）
        const u = (i / res) * 2 - 1, v = (j / res) * 2 - 1;
        verts[o++] = Math.sign(u) * u * u * size;
        verts[o++] = Math.sign(v) * v * v * size;
      }
    }
    const idx = [];
    for (let j = 0; j < res; j++) {
      for (let i = 0; i < res; i++) {
        const a = j * (res + 1) + i;
        idx.push(a, a + res + 1, a + 1, a + 1, a + res + 1, a + res + 2);
      }
    }
    const vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
    const ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    const arr = new Uint32Array(idx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, arr, gl.STATIC_DRAW);
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bindVertexArray(null);
    return { vao, count: arr.length };
  }

  resize(w, h) {
    const gl = this.gl;
    this.width = w; this.height = h;
    const s = this.q.renderScale;
    const rw = Math.max(2, Math.round(w * s));
    const rh = Math.max(2, Math.round(h * s));
    const float = !!gl.getExtension('EXT_color_buffer_half_float');
    const bw = Math.max(2, rw >> 2), bh = Math.max(2, rh >> 2);
    if (!this.scene) {
      this.scene = new RenderTarget(gl, rw, rh, { float, depth: true, depthTexture: true });
      this.linDepth = new RenderTarget(gl, rw, rh, { depth: false, filter: gl.NEAREST });
      this.bloomA = new RenderTarget(gl, bw, bh, { float, depth: false });
      this.bloomB = new RenderTarget(gl, bw, bh, { float, depth: false });
      this.rays = new RenderTarget(gl, bw, bh, { float, depth: false });
    } else {
      this.scene.resize(rw, rh);
      this.linDepth.resize(rw, rh);
      this.bloomA.resize(bw, bh);
      this.bloomB.resize(bw, bh);
      this.rays.resize(bw, bh);
    }
  }

  applyQuality() {
    this.shadow.resize(this.q.shadowSize);
    this.resize(this.width, this.height);
  }

  batchFor(model) {
    let b = this.batches.get(model);
    if (!b) {
      const geo = this.models[model];
      if (!geo) return null;
      b = new Batch(this.gl, geo, 64);
      this.batches.set(model, b);
    }
    return b;
  }

  begin() {
    for (const b of this.batches.values()) b.reset();
    this.stats.draws = 0;
    this.stats.instances = 0;
    this.stats.chunks = 0;
  }

  /* ------------------------------------------------- シーン用ユニフォーム */
  setSceneUniforms(prog, game) {
    const sky = game.sky;
    prog.v3('u_zenith', sky.zenith);
    prog.v3('u_horizon', sky.horizon);
    prog.v3('u_sunColor', sky.sunColor);
    prog.v3('u_sunDir', sky.sunDir);
    prog.f('u_night', sky.night);
    prog.v3('u_camPos', game.camera.pos);
    prog.v3('u_ambSky', sky.ambSky);
    prog.v3('u_ambGnd', sky.ambGnd);
    prog.f('u_fogDensity', sky.fogDensity);
    prog.f('u_fogHeight', 12);
    prog.f('u_time', game.time.now);
    prog.f('u_cloudShadow', this.q.cloudShadows ? sky.cloud * 0.62 * (1 - sky.night * 0.7) : 0);
    prog.f('u_cloudTime', game.time.now);
    this.setLights(prog, game);
  }

  /** 近い順に点光源を送る */
  setLights(prog, game) {
    const gl = this.gl;
    const lights = game.collectLights(this.q.maxLights);
    prog.i('u_lightCount', lights.count);
    if (lights.count > 0) {
      gl.uniform4fv(prog.u('u_lightPos[0]'), lights.pos.subarray(0, lights.count * 4));
      gl.uniform4fv(prog.u('u_lightColor[0]'), lights.col.subarray(0, lights.count * 4));
    }
  }

  setShadowUniforms(prog) {
    prog.m4('u_lightVP', this.lightVP);
    prog.tex('u_shadow', 4, this.shadow.tex);
    prog.f('u_shadowTexel', 1 / this.shadow.size);
    prog.f('u_shadowRange', this.q.shadowRange);
  }

  /* -------------------------------------------------------- 影行列 */
  computeLightMatrix(game) {
    const sky = game.sky;
    const p = game.player;
    const R = this.q.shadowRange;
    const cx = p.x + Math.sin(game.camera.yaw) * R * 0.35;
    const cz = p.z + Math.cos(game.camera.yaw) * R * 0.35;
    const cy = game.world.height(cx, cz);
    let dir = sky.sunDir;
    // 光が水平に近すぎると影が破綻するので最低仰角を確保する
    if (dir[1] < 0.14) {
      const hor = Math.hypot(dir[0], dir[2]) || 1;
      const k = Math.sqrt(Math.max(0, 1 - 0.14 * 0.14)) / hor;
      dir = [dir[0] * k, 0.14, dir[2] * k];
    }
    const dist = R * 1.6;
    const eye = v3.new(cx + dir[0] * dist, cy + Math.abs(dir[1]) * dist + 30, cz + dir[2] * dist);
    const center = v3.new(cx, cy, cz);
    m4.lookAt(this.lightView, eye, center, UP);
    m4.ortho(this.lightProj, -R, R, -R, R, 1, dist * 2 + 120);
    m4.mul(this.lightVP, this.lightProj, this.lightView);
  }

  /* ------------------------------------------------------- 描画本体 */
  render(game) {
    const gl = this.gl;
    const cam = game.camera;
    this.frustum.fromMatrix(cam.viewProj);

    // 収集
    this.begin();
    game.emitInstances(this);
    for (const b of this.batches.values()) b.upload();

    const inDungeon = !!game.dungeon;

    // ---- 影パス ----
    this.computeLightMatrix(game);
    if (this.q.shadows && !inDungeon) {
      this.shadow.bind();
      gl.enable(gl.DEPTH_TEST);
      gl.depthMask(true);
      gl.disable(gl.BLEND);
      gl.enable(gl.CULL_FACE);
      gl.cullFace(gl.FRONT);
      const sp = this.progShadow.use();
      sp.m4('u_lightVP', this.lightVP);
      sp.f('u_time', game.time.now);
      sp.f('u_wind', game.sky.wind);
      this.drawTerrain(game, true);
      for (const [name, b] of this.batches) {
        if (name === 'grass' && !this.q.grassShadow) continue;
        b.draw();
      }
      gl.cullFace(gl.BACK);
    }

    // ---- シーン ----
    this.scene.bind();
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.enable(gl.CULL_FACE);

    // 空
    gl.depthMask(false);
    const skp = this.progSky.use();
    skp.m4('u_invViewProj', cam.invViewProj);
    skp.v3('u_camPos', cam.pos);
    skp.v3('u_zenith', game.sky.zenith);
    skp.v3('u_horizon', game.sky.horizon);
    skp.v3('u_sunColor', game.sky.sunColor);
    skp.v3('u_sunDir', game.sky.sunDir);
    skp.v3('u_moonDir', game.sky.moonDir);
    skp.f('u_night', game.sky.night);
    skp.f('u_time', game.time.now);
    skp.f('u_cloud', game.sky.cloud);
    skp.f('u_quality', this.q.skyQuality);
    this.quad.draw();
    gl.depthMask(true);

    // 地形・プロップ・アクター
    const mp = this.progMesh.use();
    mp.m4('u_viewProj', cam.viewProj);
    mp.f('u_wind', game.sky.wind);
    mp.f('u_alphaScale', 1);
    this.setSceneUniforms(mp, game);
    this.setShadowUniforms(mp);

    if (!inDungeon) this.drawTerrain(game, false);
    for (const [name, b] of this.batches) {
      if (name === 'grass') continue;
      if (b.count) { b.draw(); this.stats.draws++; this.stats.instances += b.count; }
    }
    // 草（距離帯ごとにアルファを変えてフェード）
    if (!inDungeon) this.drawGrass(game, mp);

    // 不透明シーンのデプスを線形化して別ターゲットへ（水の厚み計算に使う）
    if (this.q.water && !inDungeon) {
      this.linDepth.bind();
      gl.disable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
      const lp = this.progLinDepth.use();
      lp.tex('u_depthTex', 6, this.scene.depthTex);
      lp.v2('u_nearFar', cam.near, cam.far);
      this.quad.draw();
      gl.enable(gl.DEPTH_TEST);
      gl.enable(gl.CULL_FACE);
      this.scene.bind();
      this.drawWater(game);
    }

    // パーティクル
    this.drawParticles(game);

    // ---- ポスト ----
    this.postProcess(game);
  }

  drawTerrain(game, shadowPass) {
    const gl = this.gl;
    const chunks = game.terrain.visible;
    for (const c of chunks) {
      if (!c.geo) continue;
      if (!shadowPass && !this.frustum.sphere(c.center[0], c.center[1], c.center[2], c.radius)) continue;
      if (shadowPass) {
        const dx = c.center[0] - game.player.x, dz = c.center[2] - game.player.z;
        if (Math.hypot(dx, dz) > this.q.shadowRange + CHUNK) continue;
        if (c.lod > 1) continue;
      }
      if (!c.batch) {
        c.batch = new Batch(gl, c.geo, 1);
        c.batch.data.set(IDENTITY_INSTANCE, 0);
        c.batch.count = 1;
        c.batch.upload();
      }
      c.batch.draw();
      this.stats.chunks++;
      this.stats.draws++;
    }
  }

  drawGrass(game, mp) {
    const gb = this.batchFor('grass');
    if (!gb) return;
    const chunks = game.terrain.grassChunks;
    if (!chunks.length) return;
    const px = game.player.x, pz = game.player.z;
    const bands = [[0, 0.42, 1.0], [0.42, 0.72, 0.75], [0.72, 1.0, 0.4]];
    const maxD = this.q.grassDist;
    for (const [lo, hi, alpha] of bands) {
      gb.reset();
      for (const c of chunks) {
        if (!c.grass || !c.grass.length) continue;
        const d = Math.hypot(c.center[0] - px, c.center[2] - pz) / maxD;
        if (d < lo || d >= hi) continue;
        if (!this.frustum.sphere(c.center[0], c.center[1], c.center[2], c.radius)) continue;
        gb.append(c.grass);
      }
      if (!gb.count) continue;
      gb.upload();
      mp.f('u_alphaScale', alpha);
      gb.draw();
      this.stats.draws++;
      this.stats.instances += gb.count;
    }
    mp.f('u_alphaScale', 1);
  }

  drawWater(game) {
    const gl = this.gl;
    const cam = game.camera;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.disable(gl.CULL_FACE);
    const wp = this.progWater.use();
    wp.m4('u_viewProj', cam.viewProj);
    wp.v3('u_camPos', cam.pos);
    wp.v2('u_origin', Math.round(cam.pos[0] / 4) * 4, Math.round(cam.pos[2] / 4) * 4);
    wp.f('u_time', game.time.now);
    wp.f('u_seaLevel', 0);
    wp.f('u_waveScale', lerp(0.6, 2.2, clamp01(game.sky.wind / 2)));
    wp.f('u_planeSize', this.waterSize);
    wp.v3('u_deepColor', 0.02, 0.07, 0.11);
    wp.v3('u_shallowColor', 0.08, 0.20, 0.22);
    wp.v3('u_zenith', game.sky.zenith);
    wp.v3('u_horizon', game.sky.horizon);
    wp.v3('u_sunColor', game.sky.sunColor);
    wp.v3('u_sunDir', game.sky.sunDir);
    wp.f('u_night', game.sky.night);
    wp.f('u_fogDensity', game.sky.fogDensity);
    wp.f('u_cloudShadow', this.q.cloudShadows ? game.sky.cloud * 0.62 * (1 - game.sky.night * 0.7) : 0);
    wp.f('u_cloudTime', game.time.now);
    wp.tex('u_depth', 5, this.linDepth.tex);
    wp.v2('u_resolution', this.scene.w, this.scene.h);
    wp.v2('u_nearFar', cam.near, cam.far);
    gl.bindVertexArray(this.water.vao);
    gl.drawElements(gl.TRIANGLES, this.water.count, gl.UNSIGNED_INT, 0);
    gl.enable(gl.CULL_FACE);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    this.stats.draws++;
  }

  drawParticles(game) {
    const gl = this.gl;
    const cam = game.camera;
    const vao = game.fx.build(cam.pos[0], cam.pos[1], cam.pos[2]);
    if (!vao.count) return;
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthMask(false);
    gl.disable(gl.CULL_FACE);
    const pp = this.progParticle.use();
    pp.m4('u_viewProj', cam.viewProj);
    // カメラの右/上ベクトル（ビュー行列の転置）
    const v = cam.view;
    pp.v3('u_camRight', v[0], v[4], v[8]);
    pp.v3('u_camUp', v[1], v[5], v[9]);
    vao.flush(gl.TRIANGLES);
    gl.enable(gl.CULL_FACE);
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    this.stats.draws++;
  }

  /** 太陽のスクリーン座標と薄明光線の強さを求める */
  updateSunUV(game) {
    const cam = game.camera;
    const sd = game.sky.sunDir;
    const out = this.sunUV;
    cam.project(cam.pos[0] + sd[0] * 900, cam.pos[1] + sd[1] * 900, cam.pos[2] + sd[2] * 900, out);
    if (out[2] <= 0 || sd[1] < -0.02) { this.rayStrength = 0; return; }
    // 画面中心から離れるほど、また太陽が低い/雲が厚いほど弱める
    const off = Math.max(Math.abs(out[0] - 0.5), Math.abs(out[1] - 0.5));
    const onScreen = 1 - clamp01((off - 0.5) / 0.55);
    const elev = clamp01(sd[1] * 3.2);
    const horizonBoost = 1 - clamp01(Math.abs(sd[1] - 0.12) * 3.0);
    this.rayStrength = this.q.rayStrength * onScreen * (0.35 + horizonBoost * 0.65)
      * (1 - game.sky.night) * (1 - game.sky.cloud * 0.45) * clamp01(elev * 4);
  }

  postProcess(game) {
    const gl = this.gl;
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.disable(gl.BLEND);

    if (this.q.bloom) {
      this.bloomA.bind();
      const bp = this.progBright.use();
      bp.tex('u_tex', 0, this.scene.tex);
      bp.f('u_threshold', 1.15);
      this.quad.draw();

      // 薄明光線：明部を太陽方向へ引き伸ばす
      this.updateSunUV(game);
      if (this.q.godRays && this.rayStrength > 0.002) {
        this.rays.bind();
        const rp = this.progRays.use();
        rp.tex('u_tex', 0, this.bloomA.tex);
        rp.v2('u_sunUV', this.sunUV[0], this.sunUV[1]);
        rp.f('u_decay', 0.93);
        this.quad.draw();
      }

      const blur = this.progBlur.use();
      for (let i = 0; i < this.q.bloomPasses; i++) {
        this.bloomB.bind();
        blur.tex('u_tex', 0, this.bloomA.tex);
        blur.v2('u_dir', 1 / this.bloomA.w, 0);
        this.quad.draw();
        this.bloomA.bind();
        blur.tex('u_tex', 0, this.bloomB.tex);
        blur.v2('u_dir', 0, 1 / this.bloomA.h);
        this.quad.draw();
      }
    } else {
      this.rayStrength = 0;
    }

    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, this.width, this.height);
    const cp = this.progComposite.use();
    cp.tex('u_scene', 0, this.scene.tex);
    cp.tex('u_bloom', 1, this.q.bloom ? this.bloomA.tex : this.scene.tex);
    cp.f('u_bloomStrength', this.q.bloom ? this.q.bloomStrength : 0);
    const rayOn = this.q.godRays && this.rayStrength > 0.002;
    cp.tex('u_rays', 2, rayOn ? this.rays.tex : this.scene.tex);
    cp.f('u_rayStrength', rayOn ? this.rayStrength : 0);
    const sc = game.sky.sunColor;
    const m = Math.max(0.001, Math.max(sc[0], Math.max(sc[1], sc[2])));
    cp.v3('u_rayColor', sc[0] / m, sc[1] / m, sc[2] / m);
    cp.f('u_exposure', game.sky.exposure * (game.exposureMul || 1));
    cp.f('u_vignette', 0.42);
    cp.f('u_damage', this.damage);
    const hpRatio = game.player.hp / game.player.maxHp;
    cp.f('u_desat', clamp01((0.30 - hpRatio) * 3.2) * 0.85 + (game.player.dead ? 0.6 : 0));
    cp.v3('u_grade', game.sky.grade);
    cp.f('u_time', game.time.now);
    cp.f('u_aberration', this.aberration);
    cp.f('u_underwater', this.underwaterMix || 0);
    this.quad.draw();
  }
}

const UP = v3.new(0, 1, 0);

export { clamp, clamp01, lerp };
