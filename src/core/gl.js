// WebGL2 薄いラッパ（プログラム / ジオメトリ / インスタンス / レンダーターゲット）

export function initGL(canvas) {
  const opts = {
    alpha: false, antialias: false, depth: true, stencil: false,
    powerPreference: 'high-performance', preserveDrawingBuffer: false,
    desynchronized: true, failIfMajorPerformanceCaveat: false,
  };
  const gl = canvas.getContext('webgl2', opts);
  if (!gl) return null;
  gl.getExtension('EXT_color_buffer_half_float');
  gl.getExtension('OES_texture_float_linear');
  return gl;
}

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    const numbered = src.split('\n').map((l, i) => `${i + 1}: ${l}`).join('\n');
    console.error(log, '\n', numbered);
    throw new Error('shader compile failed: ' + log);
  }
  return sh;
}

export class Program {
  constructor(gl, vsSrc, fsSrc, name = 'program') {
    this.gl = gl;
    this.name = name;
    const vs = compile(gl, gl.VERTEX_SHADER, vsSrc);
    const fs = compile(gl, gl.FRAGMENT_SHADER, fsSrc);
    const p = gl.createProgram();
    gl.attachShader(p, vs);
    gl.attachShader(p, fs);
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(`link failed (${name}): ` + gl.getProgramInfoLog(p));
    }
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    this.p = p;
    this.loc = new Map();
  }
  use() { this.gl.useProgram(this.p); return this; }
  u(name) {
    let l = this.loc.get(name);
    if (l === undefined) {
      l = this.gl.getUniformLocation(this.p, name);
      this.loc.set(name, l);
    }
    return l;
  }
  f(n, v) { this.gl.uniform1f(this.u(n), v); return this; }
  i(n, v) { this.gl.uniform1i(this.u(n), v); return this; }
  v2(n, x, y) { this.gl.uniform2f(this.u(n), x, y); return this; }
  v3(n, x, y, z) {
    if (y === undefined) this.gl.uniform3f(this.u(n), x[0], x[1], x[2]);
    else this.gl.uniform3f(this.u(n), x, y, z);
    return this;
  }
  v4(n, x, y, z, w) { this.gl.uniform4f(this.u(n), x, y, z, w); return this; }
  m4(n, m) { this.gl.uniformMatrix4fv(this.u(n), false, m); return this; }
  tex(n, unit, texture, target) {
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(target || gl.TEXTURE_2D, texture);
    gl.uniform1i(this.u(n), unit);
    return this;
  }
}

/** 9 float/頂点でインターリーブ: pos(3) nrm(3) col(3) */
export const VERT_FLOATS = 9;
/** 20 float/インスタンス: mat3x4(12) color(4) extra(4) */
export const INST_FLOATS = 20;

export class Geometry {
  constructor(gl, vertices, indices) {
    this.gl = gl;
    this.vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);
    this.ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, this.ibo);
    const use32 = vertices.length / VERT_FLOATS > 65535;
    const idx = use32 ? new Uint32Array(indices) : new Uint16Array(indices);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    this.indexType = use32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
    this.count = idx.length;
    this.vertexCount = vertices.length / VERT_FLOATS;
  }
  dispose() {
    this.gl.deleteBuffer(this.vbo);
    this.gl.deleteBuffer(this.ibo);
  }
}

/** ジオメトリ + インスタンスバッファ + VAO */
export class Batch {
  constructor(gl, geometry, capacity = 64) {
    this.gl = gl;
    this.geo = geometry;
    this.capacity = Math.max(1, capacity);
    this.data = new Float32Array(this.capacity * INST_FLOATS);
    this.count = 0;
    this.ibuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.data.byteLength, gl.DYNAMIC_DRAW);
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, geometry.vbo);
    const stride = VERT_FLOATS * 4;
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, stride, 12);
    gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 3, gl.FLOAT, false, stride, 24);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    const istride = INST_FLOATS * 4;
    for (let i = 0; i < 5; i++) {
      const loc = 3 + i;
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 4, gl.FLOAT, false, istride, i * 16);
      gl.vertexAttribDivisor(loc, 1);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, geometry.ibo);
    gl.bindVertexArray(null);
  }
  reset() { this.count = 0; }
  /** 書き込み用オフセットを確保（容量不足なら拡張）*/
  alloc() {
    if (this.count >= this.capacity) this.grow(this.capacity * 2);
    return this.count++ * INST_FLOATS;
  }
  /** 事前に焼いたインスタンス配列をまとめて追加 */
  append(src) {
    const n = src.length / INST_FLOATS;
    if (!n) return;
    let cap = this.capacity;
    while (this.count + n > cap) cap *= 2;
    if (cap !== this.capacity) this.grow(cap);
    this.data.set(src, this.count * INST_FLOATS);
    this.count += n;
  }
  grow(cap) {
    const gl = this.gl;
    this.capacity = cap;
    const nd = new Float32Array(cap * INST_FLOATS);
    nd.set(this.data);
    this.data = nd;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.data.byteLength, gl.DYNAMIC_DRAW);
  }
  upload() {
    if (!this.count) return;
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.data, 0, this.count * INST_FLOATS);
  }
  draw() {
    if (!this.count) return 0;
    const gl = this.gl;
    gl.bindVertexArray(this.vao);
    gl.drawElementsInstanced(gl.TRIANGLES, this.geo.count, this.geo.indexType, 0, this.count);
    return this.count;
  }
  dispose() {
    this.gl.deleteBuffer(this.ibuf);
    this.gl.deleteVertexArray(this.vao);
  }
}

/** 汎用の動的インスタンス VAO（パーティクル等、任意レイアウト）*/
export class DynamicVAO {
  constructor(gl, quadVerts, layout, capacity, floatsPerInstance) {
    this.gl = gl;
    this.floats = floatsPerInstance;
    this.capacity = capacity;
    this.data = new Float32Array(capacity * floatsPerInstance);
    this.count = 0;
    this.qbuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.qbuf);
    gl.bufferData(gl.ARRAY_BUFFER, quadVerts, gl.STATIC_DRAW);
    this.ibuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    gl.bufferData(gl.ARRAY_BUFFER, this.data.byteLength, gl.DYNAMIC_DRAW);
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.qbuf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    const stride = floatsPerInstance * 4;
    for (const a of layout) {
      gl.enableVertexAttribArray(a.loc);
      gl.vertexAttribPointer(a.loc, a.size, gl.FLOAT, false, stride, a.offset * 4);
      gl.vertexAttribDivisor(a.loc, 1);
    }
    gl.bindVertexArray(null);
    this.vertexCount = quadVerts.length / 2;
  }
  reset() { this.count = 0; }
  alloc() {
    if (this.count >= this.capacity) return -1;
    return this.count++ * this.floats;
  }
  flush(mode) {
    if (!this.count) return;
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.ibuf);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, this.data, 0, this.count * this.floats);
    gl.bindVertexArray(this.vao);
    gl.drawArraysInstanced(mode ?? gl.TRIANGLES, 0, this.vertexCount, this.count);
  }
}

export class RenderTarget {
  constructor(gl, w, h, { float = false, depth = true, filter = null } = {}) {
    this.gl = gl;
    this.w = w; this.h = h;
    this.float = float;
    this.depthEnabled = depth;
    this.filter = filter ?? gl.LINEAR;
    this.fbo = gl.createFramebuffer();
    this.tex = gl.createTexture();
    if (depth) this.depth = gl.createRenderbuffer();
    this.resize(w, h);
  }
  resize(w, h) {
    const gl = this.gl;
    w = Math.max(1, w | 0); h = Math.max(1, h | 0);
    this.w = w; this.h = h;
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    const ifmt = this.float ? gl.RGBA16F : gl.RGBA8;
    const type = this.float ? gl.HALF_FLOAT : gl.UNSIGNED_BYTE;
    gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, w, h, 0, gl.RGBA, type, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, this.filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, this.filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.tex, 0);
    if (this.depthEnabled) {
      gl.bindRenderbuffer(gl.RENDERBUFFER, this.depth);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, w, h);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, this.depth);
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }
  bind() {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    gl.viewport(0, 0, this.w, this.h);
  }
}

export class ShadowMap {
  constructor(gl, size) {
    this.gl = gl;
    this.size = size;
    this.fbo = gl.createFramebuffer();
    this.tex = gl.createTexture();
    this.resize(size);
  }
  resize(size) {
    const gl = this.gl;
    this.size = size;
    gl.bindTexture(gl.TEXTURE_2D, this.tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT24, size, size, 0, gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.COMPARE_REF_TO_TEXTURE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_FUNC, gl.LEQUAL);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, this.tex, 0);
    gl.drawBuffers([gl.NONE]);
    gl.readBuffer(gl.NONE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }
  bind() {
    const gl = this.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo);
    gl.viewport(0, 0, this.size, this.size);
    gl.clear(gl.DEPTH_BUFFER_BIT);
  }
}

export function fullscreenQuad(gl) {
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);
  return {
    vao,
    draw() {
      gl.bindVertexArray(vao);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    },
  };
}
