// renderer.js — camera, layered world drawing, dynamic lighting, weather, post.

import { TILE, B, WATER } from '../world/worldgen.js';
import { CHUNK, CHUNK_PX } from './tiles.js';
import { getProp } from './props.js';
import { getBuilding } from './buildings.js';
import { drawHumanoid, drawShadow, CREATURE_DRAW } from './actors.js';
import {
  clamp, lerp, TAU, makeCanvas, hash2, mixRGB, css, radial, smoothstep, valueNoise2, roundRect,
} from '../core/util.js';

const ACTOR_SCALE = 1.2;   // characters read a little larger than 1 tile

const SKY = [
  { t: 0, amb: [26, 34, 62], dark: 0.72, sun: [40, 60, 110] },      // midnight
  { t: 5, amb: [58, 60, 96], dark: 0.55, sun: [120, 100, 140] },    // pre-dawn
  { t: 6.5, amb: [230, 160, 130], dark: 0.22, sun: [255, 190, 140] },// sunrise
  { t: 9, amb: [255, 246, 226], dark: 0.02, sun: [255, 250, 235] },  // morning
  { t: 13, amb: [255, 252, 240], dark: 0, sun: [255, 255, 250] },    // noon
  { t: 17, amb: [255, 234, 200], dark: 0.05, sun: [255, 226, 180] }, // afternoon
  { t: 19, amb: [244, 150, 110], dark: 0.26, sun: [255, 170, 110] }, // sunset
  { t: 20.5, amb: [120, 88, 120], dark: 0.48, sun: [140, 100, 150] },// dusk
  { t: 22, amb: [40, 46, 84], dark: 0.66, sun: [60, 70, 120] },      // night
  { t: 24, amb: [26, 34, 62], dark: 0.72, sun: [40, 60, 110] },
];

export function skyAt(hour) {
  let a = SKY[0], b = SKY[SKY.length - 1];
  for (let i = 0; i < SKY.length - 1; i++) {
    if (hour >= SKY[i].t && hour <= SKY[i + 1].t) { a = SKY[i]; b = SKY[i + 1]; break; }
  }
  const k = clamp((hour - a.t) / Math.max(0.001, b.t - a.t), 0, 1);
  const s = smoothstep(k);
  return {
    amb: mixRGB(a.amb, b.amb, s),
    dark: lerp(a.dark, b.dark, s),
    sun: mixRGB(a.sun, b.sun, s),
  };
}

export class Camera {
  constructor() {
    this.x = 0; this.y = 0;
    this.zoom = 1;
    this.shakeT = 0; this.shakeP = 0;
    this.ox = 0; this.oy = 0;
  }
  follow(tx, ty, dt, lead = 0) {
    const k = 1 - Math.pow(0.0018, dt);
    this.x += (tx - this.x) * k;
    this.y += (ty - this.y) * k;
  }
  shake(power, time) {
    this.shakeP = Math.max(this.shakeP, power);
    this.shakeT = Math.max(this.shakeT, time);
  }
  update(dt) {
    if (this.shakeT > 0) {
      this.shakeT -= dt;
      const p = this.shakeP * clamp(this.shakeT * 3, 0, 1);
      this.ox = (Math.random() - 0.5) * p * 2;
      this.oy = (Math.random() - 0.5) * p * 2;
      if (this.shakeT <= 0) { this.shakeP = 0; this.ox = this.oy = 0; }
    } else { this.ox = this.oy = 0; }
  }
}

export class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    this.cam = new Camera();
    this.light = makeCanvas(2, 2);
    this.w = 0; this.h = 0;
    this.dpr = 1;
    this.quality = 1;
    this.sortList = [];
    this.rain = [];
    this.snow = [];
    this.leaves = [];
    this.lightScale = 0.5;
    this.stars = null;
  }

  resize(w, h, dpr) {
    this.w = w; this.h = h; this.dpr = dpr;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = w + 'px';
    this.canvas.style.height = h + 'px';
    const lw = Math.max(2, Math.round(w * this.lightScale));
    const lh = Math.max(2, Math.round(h * this.lightScale));
    this.light = makeCanvas(lw, lh);
    this.cam.zoom = clamp(Math.min(w, h) / 430, 0.95, 2.1);
    // starfield
    const st = makeCanvas(w, h);
    st.ctx.fillStyle = '#fff';
    for (let i = 0; i < 160; i++) {
      const x = Math.random() * w, y = Math.random() * h * 0.85;
      const r = Math.random() * 1.2 + 0.3;
      st.ctx.globalAlpha = 0.3 + Math.random() * 0.7;
      st.ctx.beginPath(); st.ctx.arc(x, y, r, 0, TAU); st.ctx.fill();
    }
    this.stars = st.canvas;
  }

  worldToScreen(x, y) {
    const z = this.cam.zoom;
    return {
      x: (x - this.cam.x) * z + this.w / 2 + this.cam.ox,
      y: (y - this.cam.y) * z + this.h / 2 + this.cam.oy,
    };
  }
  screenToWorld(sx, sy) {
    const z = this.cam.zoom;
    return {
      x: (sx - this.w / 2 - this.cam.ox) / z + this.cam.x,
      y: (sy - this.h / 2 - this.cam.oy) / z + this.cam.y,
    };
  }

  viewRect() {
    const z = this.cam.zoom;
    const hw = this.w / 2 / z, hh = this.h / 2 / z;
    return { x0: this.cam.x - hw, y0: this.cam.y - hh, x1: this.cam.x + hw, y1: this.cam.y + hh };
  }

  // ————————————————————————————————————————————————— main entry

  render(g, dt) {
    const ctx = this.ctx;
    const z = this.cam.zoom;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.imageSmoothingEnabled = true;

    const sky = g.interior ? { amb: [86, 76, 88], dark: 0.74, sun: [90, 80, 90] } : skyAt(g.hour());
    this.sky = sky;

    // background fill (out-of-world void)
    ctx.fillStyle = g.interior ? '#0a0908' : '#0e1a2c';
    ctx.fillRect(0, 0, this.w, this.h);

    ctx.save();
    ctx.translate(this.w / 2 + this.cam.ox, this.h / 2 + this.cam.oy);
    ctx.scale(z, z);
    ctx.translate(-this.cam.x, -this.cam.y);

    const view = this.viewRect();
    this.drawTerrain(g, view);
    this.drawWater(g, view, dt);
    this.drawGroundDecals(g, view);
    this.drawSorted(g, view, dt);
    g.pt.draw(ctx, this.cam);
    ctx.restore();

    // lighting + weather + post are in screen space
    this.drawLighting(g, view, sky);
    this.drawWeather(g, dt);
    this.drawPost(g, sky);

    // floating combat text (screen space so it stays legible)
    ctx.save();
    ctx.translate(this.w / 2 + this.cam.ox, this.h / 2 + this.cam.oy);
    ctx.scale(z, z);
    ctx.translate(-this.cam.x, -this.cam.y);
    g.pt.drawTexts(ctx);
    ctx.restore();
  }

  // ————————————————————————————————————————————————— terrain

  drawTerrain(g, view) {
    const ctx = this.ctx;
    if (g.interior) { g.interior.drawFloor(ctx, view); return; }
    const rt = g.runtime;
    const c0x = Math.floor(view.x0 / CHUNK_PX), c1x = Math.floor(view.x1 / CHUNK_PX);
    const c0y = Math.floor(view.y0 / CHUNK_PX), c1y = Math.floor(view.y1 / CHUNK_PX);
    const maxC = Math.ceil(g.world.w / CHUNK);
    for (let cy = c0y; cy <= c1y; cy++) {
      for (let cx = c0x; cx <= c1x; cx++) {
        if (cx < 0 || cy < 0 || cx >= maxC || cy >= maxC) continue;
        const img = rt.getBake(cx, cy);
        ctx.drawImage(img, cx * CHUNK_PX, cy * CHUNK_PX);
      }
    }
  }

  drawWater(g, view, dt) {
    if (g.interior) return;
    const ctx = this.ctx;
    const w = g.world;
    const t = g.time;
    const t0x = Math.max(0, Math.floor(view.x0 / TILE)), t1x = Math.min(w.w - 1, Math.ceil(view.x1 / TILE));
    const t0y = Math.max(0, Math.floor(view.y0 / TILE)), t1y = Math.min(w.h - 1, Math.ceil(view.y1 / TILE));
    let budget = 900;
    ctx.save();
    for (let ty = t0y; ty <= t1y && budget > 0; ty++) {
      for (let tx = t0x; tx <= t1x && budget > 0; tx++) {
        const i = ty * w.w + tx;
        const b = w.biome[i];
        if (!WATER.has(b)) continue;
        if (w.overlay[i] === 5) continue;
        budget--;
        const px = tx * TILE, py = ty * TILE;
        const ph = hash2(tx, ty, 7) * 6.28;
        // moving highlight bands
        const k = Math.sin(t * 1.4 + ph + tx * 0.4 + ty * 0.25);
        ctx.fillStyle = `rgba(190,235,255,${0.045 + 0.05 * (k * 0.5 + 0.5)})`;
        ctx.fillRect(px, py + 6 + k * 5, TILE, 4);
        if (hash2(tx, ty, 19) > 0.86) {
          const s = (Math.sin(t * 2.2 + ph) * 0.5 + 0.5);
          ctx.fillStyle = `rgba(255,255,255,${0.10 + s * 0.35})`;
          ctx.beginPath();
          ctx.ellipse(px + 8 + hash2(tx, ty, 5) * 16, py + 10 + hash2(tx, ty, 9) * 12, 3 + s * 2, 1.2, 0, 0, TAU);
          ctx.fill();
        }
        // shoreline foam
        const landN = !w.isWaterTile(tx, ty - 1), landS = !w.isWaterTile(tx, ty + 1);
        const landW = !w.isWaterTile(tx - 1, ty), landE = !w.isWaterTile(tx + 1, ty);
        if (landN || landS || landW || landE) {
          const f = 0.28 + Math.sin(t * 1.9 + ph) * 0.16;
          ctx.fillStyle = `rgba(240,252,255,${clamp(f, 0, 1)})`;
          const o = Math.sin(t * 1.9 + ph) * 1.6;
          if (landN) ctx.fillRect(px, py - 1 + o, TILE, 3);
          if (landS) ctx.fillRect(px, py + TILE - 2 - o, TILE, 3);
          if (landW) ctx.fillRect(px - 1 + o, py, 3, TILE);
          if (landE) ctx.fillRect(px + TILE - 2 - o, py, 3, TILE);
        }
      }
    }
    ctx.restore();
  }

  drawGroundDecals(g, view) {
    const ctx = this.ctx;
    // quest markers on the ground
    for (const m of g.groundMarkers) {
      const pulse = 0.5 + Math.sin(g.time * 3) * 0.5;
      ctx.strokeStyle = `rgba(255,214,110,${0.35 + pulse * 0.35})`;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.ellipse(m.x, m.y, 22 + pulse * 5, 11 + pulse * 2.5, 0, 0, TAU);
      ctx.stroke();
    }
  }

  // ————————————————————————————————————————————————— sorted sprites

  drawSorted(g, view, dt) {
    const ctx = this.ctx;
    const list = this.sortList;
    list.length = 0;
    const pad = 140;
    const inView = (x, y) => x > view.x0 - pad && x < view.x1 + pad && y > view.y0 - pad * 2 && y < view.y1 + pad;

    // props
    if (g.interior) {
      g.interior.collectProps(list, view, g);
    } else {
      const rt = g.runtime;
      const c0x = Math.floor((view.x0 - pad) / CHUNK_PX), c1x = Math.floor((view.x1 + pad) / CHUNK_PX);
      const c0y = Math.floor((view.y0 - pad * 2) / CHUNK_PX), c1y = Math.floor((view.y1 + pad) / CHUNK_PX);
      for (let cy = c0y; cy <= c1y; cy++) {
        for (let cx = c0x; cx <= c1x; cx++) {
          const props = rt.getProps(cx, cy);
          for (const p of props) {
            if (!inView(p.x, p.y)) continue;
            if (p.harvest && rt.isHarvested(p, g.time)) continue;
            list.push({ y: p.y, kind: 'prop', o: p });
          }
        }
      }
      // buildings
      for (const s of g.world.settlements) {
        const sx = (s.x + 0.5) * TILE, sy = (s.y + 0.5) * TILE;
        if (Math.abs(sx - g.player.x) > 2600 || Math.abs(sy - g.player.y) > 2600) continue;
        for (const b of s.buildings) {
          if (!inView(b.x, b.y + b.th * TILE * 0.5)) continue;
          list.push({ y: (b.ty + b.th) * TILE, kind: 'building', o: b });
        }
      }
    }

    for (const e of g.enemies) if (e.alive && inView(e.x, e.y)) list.push({ y: e.sortY, kind: 'enemy', o: e });
    for (const n of g.npcs) if (inView(n.x, n.y)) list.push({ y: n.y, kind: 'npc', o: n });
    for (const d of g.drops) if (inView(d.x, d.y)) list.push({ y: d.y, kind: 'drop', o: d });
    for (const o of g.objects) if (inView(o.x, o.y)) list.push({ y: o.y, kind: 'object', o });
    if (g.player.alive || g.player.deathT < 3) list.push({ y: g.player.y, kind: 'player', o: g.player });
    for (const p of g.projectiles) if (inView(p.x, p.y)) list.push({ y: p.y + 1e5, kind: 'proj', o: p });

    list.sort((a, b) => a.y - b.y);

    for (const item of list) {
      switch (item.kind) {
        case 'prop': this.drawProp(ctx, item.o, g); break;
        case 'building': this.drawBuilding(ctx, item.o, g); break;
        case 'enemy': this.drawEnemy(ctx, item.o, g); break;
        case 'npc': this.drawNPC(ctx, item.o, g); break;
        case 'player': this.drawPlayer(ctx, item.o, g); break;
        case 'drop': this.drawDrop(ctx, item.o, g); break;
        case 'object': this.drawObject(ctx, item.o, g); break;
        case 'proj': item.o.draw(ctx, g.time); break;
      }
    }
  }

  drawProp(ctx, p, g) {
    const sp = getProp(p.kind, p.variant);
    const sway = p.sway ? Math.sin(g.time * 1.5 + (p.phase || 0)) * g.weather.wind * 0.055 : 0;
    ctx.save();
    ctx.translate(p.x, p.y);
    // shadow
    if (p.kind !== 'shrine' && p.kind !== 'caveMouth') {
      ctx.save();
      ctx.scale(1, 0.5);
      drawShadow(ctx, sp.w * 0.20, sp.w * 0.20, 0.26);
      ctx.restore();
    }
    if (sway) { ctx.transform(1, 0, sway, 1, 0, 0); }
    ctx.drawImage(sp.canvas, -sp.ox, -sp.oy);
    ctx.restore();
    if (p.poi && p.poi.kind === 'shrine' && p.poi.activated) {
      const pulse = 0.6 + Math.sin(g.time * 2) * 0.4;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      radial(ctx, p.x, p.y - 56, 40 + pulse * 8, `rgba(120,220,255,${0.22 * pulse})`, 'rgba(120,220,255,0)');
      ctx.restore();
    }
  }

  drawBuilding(ctx, b, g) {
    const lit = g.isNight();
    const sp = getBuilding(b, lit);
    const x = (b.tx + b.tw / 2) * TILE;
    const y = (b.ty + b.th) * TILE;
    ctx.save();
    // fade out when the hero would be hidden underneath the roof
    const p = g.player;
    const half = (b.tw * TILE) / 2 + 10;
    if (Math.abs(p.x - x) < half && p.y > y - sp.h && p.y < y - 4) {
      ctx.globalAlpha = 0.42;
    }
    ctx.translate(x, y);
    ctx.fillStyle = 'rgba(0,0,0,0.22)';
    ctx.beginPath();
    ctx.ellipse(0, -4, b.tw * TILE * 0.52, 10, 0, 0, TAU);
    ctx.fill();
    ctx.drawImage(sp.canvas, -sp.ox, -sp.oy);
    ctx.restore();
  }

  drawObject(ctx, o, g) {
    const sp = getProp(o.sprite || 'chest', o.variant || 0);
    ctx.save();
    ctx.translate(o.x, o.y);
    ctx.save(); ctx.scale(1, 0.5); drawShadow(ctx, sp.w * 0.2, sp.w * 0.2, 0.28); ctx.restore();
    if (o.opened) ctx.globalAlpha = 0.85;
    ctx.drawImage(sp.canvas, -sp.ox, -sp.oy);
    ctx.restore();
    if (o.kind === 'chest' && !o.opened) {
      const pulse = 0.5 + Math.sin(g.time * 3 + o.x) * 0.5;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      radial(ctx, o.x, o.y - 12, 26, `rgba(255,214,110,${0.10 + pulse * 0.10})`, 'rgba(255,214,110,0)');
      ctx.restore();
    }
  }

  drawDrop(ctx, d, g) {
    const bob = Math.sin(g.time * 3 + d.x) * 2;
    ctx.save();
    ctx.translate(d.x, d.y);
    ctx.fillStyle = 'rgba(0,0,0,0.3)';
    ctx.beginPath(); ctx.ellipse(0, 0, 6, 3, 0, 0, TAU); ctx.fill();
    ctx.translate(0, -d.z - 8 + bob);
    const p = d.payload;
    if (p.gold) {
      ctx.fillStyle = '#e8b93a';
      ctx.beginPath(); ctx.ellipse(0, 0, 5.5, 3.4, 0, 0, TAU); ctx.fill();
      ctx.fillStyle = '#ffd76a';
      ctx.beginPath(); ctx.ellipse(0, -1.4, 5.5, 3.4, 0, 0, TAU); ctx.fill();
    } else if (p.equip) {
      const col = ['#d7d2c8', '#7fd77f', '#6fb6ff', '#c58cff', '#ffab40'][p.equip.rarity || 0];
      ctx.fillStyle = col;
      ctx.beginPath();
      ctx.moveTo(0, -7); ctx.lineTo(5, 0); ctx.lineTo(0, 7); ctx.lineTo(-5, 0);
      ctx.closePath(); ctx.fill();
      ctx.globalCompositeOperation = 'lighter';
      radial(ctx, 0, 0, 16, col.replace(')', ',0.35)').replace('#', 'rgba(') === col ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.25)', 'rgba(255,255,255,0)');
      ctx.globalCompositeOperation = 'source-over';
    } else {
      const it = g.itemDef(p.id);
      ctx.fillStyle = (it && it.color) || '#cfd6dd';
      ctx.beginPath(); ctx.arc(0, 0, 5, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.5)';
      ctx.beginPath(); ctx.arc(-1.4, -1.4, 1.8, 0, TAU); ctx.fill();
    }
    ctx.restore();
  }

  drawActorBody(ctx, a, g, drawFn) {
    ctx.save();
    ctx.translate(a.x, a.y);
    // shadow
    ctx.save();
    ctx.translate(0, 0);
    drawShadow(ctx, (a.r || 9) * 1.1 * (a.scale || 1), (a.r || 9) * 0.45 * (a.scale || 1), a.z > 2 ? 0.18 : 0.3);
    ctx.restore();
    ctx.translate(0, -(a.z || 0));
    if (a.flash > 0) {
      ctx.save();
      ctx.globalCompositeOperation = 'source-over';
      drawFn(ctx);
      ctx.globalCompositeOperation = 'source-atop';
      ctx.fillStyle = `rgba(255,255,255,${clamp(a.flash * 4, 0, 0.85)})`;
      ctx.fillRect(-60, -90, 120, 100);
      ctx.restore();
    } else {
      drawFn(ctx);
    }
    ctx.restore();
  }

  drawPlayer(ctx, p, g) {
    if (!p.alive) {
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.globalAlpha = clamp(1 - p.deathT / 2.4, 0, 1);
      ctx.rotate(clamp(p.deathT * 2.4, 0, 1.5));
      drawHumanoid(ctx, { dir: 0, animT: 0, moving: false, pal: p.pal, equip: p.appearance, scale: ACTOR_SCALE });
      ctx.restore();
      return;
    }
    let action = null;
    if (p.state === 'attack') action = { type: 'attack', p: p.stateT / p.attackDur };
    else if (p.state === 'cast') action = { type: 'cast', p: clamp(p.stateT / p.castDur, 0, 1) };
    else if (p.state === 'block') action = { type: 'block', p: 1 };
    else if (p.state === 'bow') action = { type: 'bow', p: clamp(p.charge / 0.7, 0, 1) };

    const rolling = p.state === 'roll';
    if (p.flowT > 0) {
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const pulse = 0.75 + Math.sin(g.time * 7) * 0.2;
      radial(ctx, p.x, p.y - 13, 34 + pulse * 6, `rgba(115,220,255,${0.18 + pulse * 0.08})`, 'rgba(255,220,120,0)');
      ctx.strokeStyle = `rgba(190,245,255,${0.35 + pulse * 0.18})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.ellipse(p.x, p.y + 2, 18 + pulse * 3, 8 + pulse, 0, 0, TAU);
      ctx.stroke();
      ctx.restore();
    }
    this.drawActorBody(ctx, p, g, (c) => {
      if (rolling) {
        const k = clamp(p.stateT / 0.36, 0, 1);
        c.save();
        c.translate(0, -14);
        c.rotate(k * TAU * (p.dir === 1 ? -1 : 1));
        c.translate(0, 14);
        drawHumanoid(c, { dir: p.dir, animT: p.animT, moving: false, pal: p.pal, equip: p.appearance, scale: ACTOR_SCALE * 0.9 });
        c.restore();
      } else {
        drawHumanoid(c, {
          dir: p.dir, animT: p.animT, moving: p.moving, pal: p.pal,
          equip: p.appearance, action, scale: ACTOR_SCALE,
        });
      }
    });
    if (p.iframes > 0 && !rolling) {
      ctx.save();
      ctx.globalAlpha = 0.35 + Math.sin(g.time * 40) * 0.2;
      ctx.globalCompositeOperation = 'lighter';
      radial(ctx, p.x, p.y - 16, 22, 'rgba(160,200,255,0.3)', 'rgba(160,200,255,0)');
      ctx.restore();
    }
    if (p.state === 'bow') {
      // aim line
      ctx.save();
      ctx.strokeStyle = `rgba(255,255,255,${0.10 + clamp(p.charge, 0, 0.7) * 0.25})`;
      ctx.setLineDash([5, 7]);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(p.x + Math.cos(p.face) * 16, p.y - 14 + Math.sin(p.face) * 16);
      ctx.lineTo(p.x + Math.cos(p.face) * 240, p.y - 14 + Math.sin(p.face) * 240);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawNPC(ctx, n, g) {
    this.drawActorBody(ctx, n, g, (c) => {
      drawHumanoid(c, {
        dir: n.dir, animT: n.animT, moving: n.moving, pal: n.pal,
        equip: n.equipVis || null, scale: (n.scale || 1) * ACTOR_SCALE,
      });
    });
    // interaction hint
    if (g.nearNPC === n) {
      const bob = Math.sin(g.time * 4) * 2;
      ctx.save();
      ctx.translate(n.x, n.y - 52 + bob);
      ctx.fillStyle = 'rgba(255,240,190,0.95)';
      ctx.beginPath();
      ctx.moveTo(0, 6); ctx.lineTo(-5, 0); ctx.lineTo(5, 0);
      ctx.closePath(); ctx.fill();
      roundRect(ctx, -13, -13, 26, 14, 4); ctx.fill();
      ctx.fillStyle = '#3a2e20';
      ctx.font = 'bold 11px system-ui, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('話', 0, -6);
      ctx.restore();
    }
  }

  drawEnemy(ctx, e, g) {
    const T = e.T;
    const drawKind = T.draw;
    const telegraph = e.state === 'windup';
    if (e.state === 'bossBreath') {
      const k = clamp(e.stateT / 0.76, 0, 1);
      const spread = e.enraged ? 1.28 : 1.04;
      const reach = 235;
      ctx.save();
      ctx.fillStyle = `rgba(255,70,35,${0.08 + k * 0.18})`;
      ctx.strokeStyle = `rgba(255,175,95,${0.28 + k * 0.55})`;
      ctx.lineWidth = 2 + k * 2;
      ctx.beginPath();
      ctx.moveTo(e.x, e.y);
      ctx.arc(e.x, e.y, reach, e.face - spread * 0.5, e.face + spread * 0.5);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    } else if (e.state === 'bossMeteor') {
      const k = clamp(e.stateT / 1.04, 0, 1);
      ctx.save();
      for (const spot of e.specialTargets || []) {
        ctx.fillStyle = `rgba(255,65,25,${0.08 + k * 0.2})`;
        ctx.strokeStyle = `rgba(255,200,100,${0.35 + k * 0.6})`;
        ctx.lineWidth = 2 + k * 4;
        ctx.beginPath(); ctx.arc(spot.x, spot.y, 54, 0, TAU); ctx.fill(); ctx.stroke();
        ctx.beginPath(); ctx.arc(spot.x, spot.y, 54 * (1 - k), 0, TAU); ctx.stroke();
      }
      ctx.restore();
    } else if (e.state === 'bossRush') {
      const k = clamp(e.stateT / 0.48, 0, 1);
      ctx.save();
      ctx.strokeStyle = `rgba(255,125,70,${0.25 + k * 0.65})`;
      ctx.lineWidth = 12 + k * 10;
      ctx.lineCap = 'round';
      ctx.setLineDash([18, 14]);
      ctx.beginPath();
      ctx.moveTo(e.x, e.y);
      ctx.lineTo(e.x + Math.cos(e.rushA) * 260, e.y + Math.sin(e.rushA) * 260);
      ctx.stroke();
      ctx.restore();
    }
    this.drawActorBody(ctx, e, g, (c) => {
      if (telegraph) {
        c.save();
        const k = clamp(e.stateT / T.windup, 0, 1);
        c.globalCompositeOperation = 'lighter';
        radial(c, 0, -e.r, e.r * (1.6 + k), `rgba(255,90,60,${0.14 + k * 0.22})`, 'rgba(255,90,60,0)');
        c.restore();
      }
      if (drawKind === 'human') {
        let action = null;
        if (e.state === 'windup') action = { type: 'attack', p: clamp(e.stateT / T.windup, 0, 1) * 0.3 };
        else if (e.state === 'attack') action = { type: 'attack', p: 0.3 + clamp(e.stateT / 0.3, 0, 1) * 0.7 };
        drawHumanoid(c, {
          dir: e.dir, animT: e.animT, moving: e.moving, pal: e.pal, equip: e.equipVis,
          action, scale: e.scale * ACTOR_SCALE, undead: T.humanoid === 'skeleton',
          glowEyes: T.humanoid === 'skeleton' ? '#ff6a4a' : null,
        });
      } else {
        const fn = CREATURE_DRAW[drawKind];
        if (fn) {
          c.save();
          c.scale(ACTOR_SCALE, ACTOR_SCALE);
          fn(c, e, g.time);
          c.restore();
        }
      }
    });

    // health bar for damaged / elite enemies
    if (e.hp < e.maxHp || e.boss) {
      const w = e.boss ? 60 : 32;
      const y = e.y - e.r * (e.scale || 1) * (e.boss ? 3.4 : 2.6) - 12 - e.z;
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      roundRect(ctx, e.x - w / 2 - 1, y - 1, w + 2, 5, 2.5); ctx.fill();
      const k = clamp(e.hp / e.maxHp, 0, 1);
      ctx.fillStyle = e.boss ? '#e04a4a' : k > 0.5 ? '#7fd77f' : k > 0.25 ? '#ffd76a' : '#e0524a';
      roundRect(ctx, e.x - w / 2, y, w * k, 3, 1.5); ctx.fill();
      if (e.poise > 0) {
        const pk = clamp(e.poise / e.maxPoise, 0, 1);
        ctx.fillStyle = 'rgba(0,0,0,0.52)';
        roundRect(ctx, e.x - w / 2, y + 6, w, 3, 1.5); ctx.fill();
        ctx.fillStyle = pk > 0.78 ? '#ffe08a' : '#9fc7d6';
        roundRect(ctx, e.x - w / 2, y + 6, w * pk, 3, 1.5); ctx.fill();
      }
      ctx.restore();
    }
    if (e.aggroT > 0 && e.state === 'chase' && e.stateT < 0.6) {
      ctx.save();
      ctx.fillStyle = 'rgba(255,80,60,0.9)';
      ctx.font = 'bold 16px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('!', e.x, e.y - e.r * 3 - 16 - e.z);
      ctx.restore();
    }
  }

  // ————————————————————————————————————————————————— lighting

  drawLighting(g, view, sky) {
    const dark = g.interior ? 0.82 : sky.dark;
    const lc = this.light;
    const ctx = lc.ctx;
    const S = this.lightScale;
    const z = this.cam.zoom;
    if (dark < 0.02 && !g.interior) {
      // still apply warm ambient tint at dawn/dusk
      if (sky.amb[0] !== 255 || sky.amb[2] !== 240) {
        const c2 = this.ctx;
        c2.save();
        c2.globalCompositeOperation = 'multiply';
        c2.fillStyle = `rgba(${sky.amb[0] | 0},${sky.amb[1] | 0},${sky.amb[2] | 0},0.30)`;
        c2.fillRect(0, 0, this.w, this.h);
        c2.restore();
      }
      return;
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    // base darkness colour
    const amb = sky.amb;
    const nightCol = [
      lerp(255, amb[0] * 0.55, dark),
      lerp(255, amb[1] * 0.6, dark),
      lerp(255, amb[2] * 0.85, dark),
    ];
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = `rgb(${nightCol[0] | 0},${nightCol[1] | 0},${nightCol[2] | 0})`;
    ctx.fillRect(0, 0, lc.canvas.width, lc.canvas.height);

    ctx.globalCompositeOperation = 'lighter';
    const toLight = (wx, wy) => ({
      x: ((wx - this.cam.x) * z + this.w / 2 + this.cam.ox) * S,
      y: ((wy - this.cam.y) * z + this.h / 2 + this.cam.oy) * S,
    });

    const addLight = (wx, wy, radius, color, intensity) => {
      const p = toLight(wx, wy);
      const r = radius * z * S;
      if (p.x < -r || p.y < -r || p.x > lc.canvas.width + r || p.y > lc.canvas.height + r) return;
      const grd = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, Math.max(1, r));
      grd.addColorStop(0, `rgba(${color[0]},${color[1]},${color[2]},${intensity})`);
      grd.addColorStop(0.5, `rgba(${color[0]},${color[1]},${color[2]},${intensity * 0.45})`);
      grd.addColorStop(1, `rgba(${color[0]},${color[1]},${color[2]},0)`);
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, TAU);
      ctx.fill();
    };
    g.lightSink = addLight;

    // player lantern
    const p = g.player;
    const flick = 0.9 + Math.sin(g.time * 9) * 0.04 + Math.sin(g.time * 23) * 0.03;
    addLight(p.x, p.y - 14, (g.interior ? 230 : 150) * flick, [255, 216, 150], 1.0);

    // world lights
    g.collectLights(addLight, view);

    ctx.globalCompositeOperation = 'source-over';

    const c = this.ctx;
    c.save();
    c.globalCompositeOperation = 'multiply';
    c.imageSmoothingEnabled = true;
    c.drawImage(lc.canvas, 0, 0, this.w, this.h);
    c.restore();
  }

  // ————————————————————————————————————————————————— weather & post

  drawWeather(g, dt) {
    const ctx = this.ctx;
    const W = g.weather;
    if (W.rain > 0.02) {
      const n = Math.floor(W.rain * 220 * this.quality);
      while (this.rain.length < n) this.rain.push({ x: Math.random() * this.w, y: Math.random() * this.h, s: 0.6 + Math.random() * 0.8 });
      ctx.save();
      ctx.strokeStyle = `rgba(190,220,255,${0.25 + W.rain * 0.35})`;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      const slant = W.wind * 3;
      for (let i = 0; i < n; i++) {
        const r = this.rain[i];
        r.y += (900 + r.s * 500) * dt;
        r.x += slant * 60 * dt;
        if (r.y > this.h) { r.y = -20; r.x = Math.random() * this.w; }
        if (r.x > this.w) r.x -= this.w;
        if (r.x < 0) r.x += this.w;
        ctx.moveTo(r.x, r.y);
        ctx.lineTo(r.x + slant * 4, r.y + 16 * r.s);
      }
      ctx.stroke();
      ctx.restore();
      // splashes near the ground plane
      if (Math.random() < W.rain) {
        const x = Math.random() * this.w, y = Math.random() * this.h;
        ctx.strokeStyle = 'rgba(220,240,255,0.35)';
        ctx.beginPath(); ctx.ellipse(x, y, 5, 2, 0, 0, TAU); ctx.stroke();
      }
    }
    if (W.snow > 0.02) {
      const n = Math.floor(W.snow * 160 * this.quality);
      while (this.snow.length < n) this.snow.push({ x: Math.random() * this.w, y: Math.random() * this.h, s: 0.4 + Math.random(), p: Math.random() * 6 });
      ctx.save();
      for (let i = 0; i < n; i++) {
        const f = this.snow[i];
        f.y += (30 + f.s * 60) * dt;
        f.x += Math.sin(g.time * 1.2 + f.p) * 22 * dt + g.weather.wind * 24 * dt;
        if (f.y > this.h) { f.y = -10; f.x = Math.random() * this.w; }
        if (f.x > this.w) f.x -= this.w;
        if (f.x < 0) f.x += this.w;
        ctx.fillStyle = `rgba(255,255,255,${0.4 + f.s * 0.5})`;
        ctx.beginPath(); ctx.arc(f.x, f.y, 1 + f.s * 1.8, 0, TAU); ctx.fill();
      }
      ctx.restore();
    }
    if (W.fog > 0.02) {
      ctx.save();
      const g2 = ctx.createLinearGradient(0, 0, 0, this.h);
      g2.addColorStop(0, `rgba(200,210,225,${W.fog * 0.30})`);
      g2.addColorStop(0.55, `rgba(200,210,225,${W.fog * 0.16})`);
      g2.addColorStop(1, `rgba(190,200,220,${W.fog * 0.34})`);
      ctx.fillStyle = g2;
      ctx.fillRect(0, 0, this.w, this.h);
      ctx.restore();
    }
    if (g.ashFall > 0.02) {
      const n = Math.floor(g.ashFall * 90);
      while (this.leaves.length < n) this.leaves.push({ x: Math.random() * this.w, y: Math.random() * this.h, s: 0.4 + Math.random(), p: Math.random() * 6 });
      ctx.save();
      for (let i = 0; i < n; i++) {
        const f = this.leaves[i];
        f.y += (20 + f.s * 40) * dt;
        f.x += Math.sin(g.time * 0.8 + f.p) * 16 * dt;
        if (f.y > this.h) { f.y = -10; f.x = Math.random() * this.w; }
        ctx.fillStyle = `rgba(${230 - f.s * 60},${120 - f.s * 40},${60},${0.35 + f.s * 0.4})`;
        ctx.beginPath(); ctx.arc(f.x, f.y, 1 + f.s * 1.6, 0, TAU); ctx.fill();
      }
      ctx.restore();
    }
  }

  drawPost(g, sky) {
    const ctx = this.ctx;
    // stars at night
    if (!g.interior && sky.dark > 0.35 && this.stars) {
      ctx.save();
      ctx.globalAlpha = clamp((sky.dark - 0.35) * 1.6, 0, 0.7) * (1 - g.weather.rain);
      ctx.globalCompositeOperation = 'lighter';
      ctx.drawImage(this.stars, 0, 0);
      ctx.restore();
    }
    // vignette
    const g2 = ctx.createRadialGradient(this.w / 2, this.h / 2, Math.min(this.w, this.h) * 0.35, this.w / 2, this.h / 2, Math.max(this.w, this.h) * 0.72);
    g2.addColorStop(0, 'rgba(0,0,0,0)');
    g2.addColorStop(1, `rgba(0,0,0,${0.34 + (g.interior ? 0.2 : sky.dark * 0.2)})`);
    ctx.fillStyle = g2;
    ctx.fillRect(0, 0, this.w, this.h);

    // low-hp pulse
    const p = g.player;
    if (p.alive && p.hp / p.maxHp < 0.3) {
      const k = 1 - p.hp / p.maxHp / 0.3;
      const pulse = 0.5 + Math.sin(g.time * 5) * 0.5;
      const g3 = ctx.createRadialGradient(this.w / 2, this.h / 2, Math.min(this.w, this.h) * 0.3, this.w / 2, this.h / 2, Math.max(this.w, this.h) * 0.7);
      g3.addColorStop(0, 'rgba(180,20,20,0)');
      g3.addColorStop(1, `rgba(180,20,20,${0.15 + k * pulse * 0.35})`);
      ctx.fillStyle = g3;
      ctx.fillRect(0, 0, this.w, this.h);
    }
    if (g.flashT > 0) {
      ctx.fillStyle = `rgba(255,255,255,${clamp(g.flashT, 0, 1) * 0.7})`;
      ctx.fillRect(0, 0, this.w, this.h);
    }
    if (g.fadeT > 0) {
      ctx.fillStyle = `rgba(0,0,0,${clamp(g.fadeT, 0, 1)})`;
      ctx.fillRect(0, 0, this.w, this.h);
    }
  }
}
