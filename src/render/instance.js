// インスタンス属性の書き込みヘルパ（20 float / インスタンス）

import { INST_FLOATS } from '../core/gl.js';

/**
 * Y軸回転 + 非一様スケールの 3x4 行列とマテリアル値を書き込む。
 * @returns 次のオフセット
 */
export function writeInstance(a, o, x, y, z, rotY, sx, sy, sz,
  r = 1, g = 1, b = 1, alpha = 1, wind = 0, emissive = 0, colorMix = 0, detail = 0) {
  const c = Math.cos(rotY), s = Math.sin(rotY);
  a[o] = c * sx; a[o + 1] = 0; a[o + 2] = s * sz; a[o + 3] = x;
  a[o + 4] = 0; a[o + 5] = sy; a[o + 6] = 0; a[o + 7] = y;
  a[o + 8] = -s * sx; a[o + 9] = 0; a[o + 10] = c * sz; a[o + 11] = z;
  a[o + 12] = r; a[o + 13] = g; a[o + 14] = b; a[o + 15] = alpha;
  a[o + 16] = wind; a[o + 17] = emissive; a[o + 18] = colorMix; a[o + 19] = detail;
  return o + INST_FLOATS;
}

/** 任意の 4x4 行列（column-major）からインスタンスを書き込む */
export function writeInstanceMat(a, o, m, r = 1, g = 1, b = 1, alpha = 1, wind = 0, emissive = 0, colorMix = 0) {
  a[o] = m[0]; a[o + 1] = m[4]; a[o + 2] = m[8]; a[o + 3] = m[12];
  a[o + 4] = m[1]; a[o + 5] = m[5]; a[o + 6] = m[9]; a[o + 7] = m[13];
  a[o + 8] = m[2]; a[o + 9] = m[6]; a[o + 10] = m[10]; a[o + 11] = m[14];
  a[o + 12] = r; a[o + 13] = g; a[o + 14] = b; a[o + 15] = alpha;
  a[o + 16] = wind; a[o + 17] = emissive; a[o + 18] = colorMix; a[o + 19] = 0;
  return o + INST_FLOATS;
}
