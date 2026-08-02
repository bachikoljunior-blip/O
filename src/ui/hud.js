// hud.js — in-game heads-up display and touch controls.

import { UI, COL, FONT } from './ui.js';
import { clamp, lerp, TAU, roundRect, radial, angleTo } from '../core/util.js';
import { getIcon } from '../render/icons.js';
import { SPELLS } from '../game/entities.js';
import { TILE } from '../world/worldgen.js';

export class HUD {
  constructor(ui) {
    this.ui = ui;
    this.toasts = [];
    this.hpGhost = 1;
    this.bossFade = 0;
    this.banner = null;
    this.prompt = null;
  }

  toast(text, col = COL.ink) {
    this.toasts.push({ text, col, t: 0 });
    if (this.toasts.length > 3) this.toasts.shift();
  }

  showBanner(title, sub) {
    this.banner = { title, sub, t: 0 };
  }

  layout(w, h) {
    const safe = 10;
    const small = Math.min(w, h) < 420;
    const R = small ? 38 : 44;
    const bx = w - (small ? 66 : 76) - safe;
    const by = h - (small ? 76 : 92) - safe;
    return {
      w, h, small, safe,
      attack: { x: bx, y: by, r: R },
      dodge: { x: bx - R * 1.55, y: by + R * 0.42, r: R * 0.66 },
      block: { x: bx + R * 0.18, y: by - R * 1.62, r: R * 0.66 },
      interact: { x: bx - R * 1.42, y: by - R * 1.18, r: R * 0.62 },
      item: { x: safe + (small ? 34 : 40), y: h - safe - (small ? 34 : 40), r: small ? 30 : 34 },
      spells: [0, 1, 2, 3].map((i) => ({
        x: w - safe - (small ? 26 : 30),
        y: by - R * 2.6 - i * (small ? 54 : 62),
        r: small ? 23 : 26,
      })),
      menu: { x: w - safe - 24, y: safe + 24, r: 22 },
      map: { x: w - safe - 24, y: safe + 74, r: 22 },
    };
  }

  registerButtons(input, g, L) {
    const list = [
      { id: 'attack', ...L.attack },
      { id: 'dodge', ...L.dodge },
      { id: 'block', ...L.block },
      { id: 'interact', ...L.interact },
      { id: 'item', ...L.item },
      { id: 'menu', ...L.menu },
      { id: 'map', ...L.map },
    ];
    for (let i = 0; i < 4; i++) {
      if (g.player.spellSlots[i]) list.push({ id: 'spell' + i, ...L.spells[i] });
    }
    input.setButtons(list);
    input.setStickZone(0, L.h * 0.22, L.w * 0.56, L.h * 0.78);
  }

  draw(ctx, g, dt) {
    const ui = this.ui;
    const { w, h } = ui;
    const L = this.layout(w, h);
    this.L = L;
    const p = g.player;

    // ——— top-left vitals
    const px = L.safe + 6, py = L.safe + 6;
    const bw = L.small ? 132 : 168;
    ui.panel(px - 6, py - 6, bw + 46, 62, { r: 12, fill: 'rgba(20,17,15,0.72)' });
    // level medallion
    ctx.save();
    ctx.beginPath(); ctx.arc(px + 18, py + 24, 20, 0, TAU);
    const gr = ctx.createLinearGradient(px, py, px + 36, py + 44);
    gr.addColorStop(0, '#6b5636'); gr.addColorStop(1, '#3a2f22');
    ctx.fillStyle = gr; ctx.fill();
    ctx.strokeStyle = COL.gold; ctx.lineWidth = 1.6; ctx.stroke();
    ctx.restore();
    ui.text(String(p.level), px + 18, py + 24, { size: 19, align: 'center', col: COL.gold, weight: 800 });
    ui.text('Lv', px + 18, py + 6, { size: 9, align: 'center', col: COL.ink2, weight: 700 });

    this.hpGhost = lerp(this.hpGhost, p.hp / p.maxHp, 1 - Math.pow(0.002, dt));
    const bx0 = px + 44;
    ui.bar(bx0, py + 4, bw, 11, p.hp / p.maxHp, COL.hp, { ghost: this.hpGhost });
    ui.bar(bx0, py + 19, bw * 0.82, 7, p.mp / p.maxMp, COL.mp);
    ui.bar(bx0, py + 30, bw * 0.82, 6, p.sta / p.maxSta, COL.sta);
    ui.bar(bx0, py + 40, bw, 4, p.xp / p.xpNext, COL.xp, { bg: 'rgba(0,0,0,0.4)' });
    ui.text(`${Math.ceil(p.hp)}/${p.maxHp}`, bx0 + 4, py + 10, { size: 10, col: 'rgba(255,255,255,0.9)', weight: 700 });

    // gold
    ctx.drawImage(getIcon('gold'), px - 2, py + 44, 24, 24);
    ui.text(String(p.gold), px + 22, py + 56, { size: 13, col: COL.gold, weight: 700 });

    // buffs
    let buffX = px + 78;
    if (p.buff.t > 0) {
      ui.text(`${p.buff.atk ? 'ATK+' + p.buff.atk : 'DEF+' + p.buff.def} ${Math.ceil(p.buff.t)}s`, buffX, py + 56, { size: 11, col: '#ffb46a' });
    }

    // ——— minimap
    this.drawMinimap(ctx, g, L);

    // ——— boss bar (replaces the quest tracker while a boss is alive)
    const boss = g.activeBoss;
    this.bossFade = lerp(this.bossFade, boss && boss.alive ? 1 : 0, 1 - Math.pow(0.005, dt));
    if (this.bossFade < 0.5) this.drawQuestTracker(ctx, g, L);
    if (this.bossFade > 0.01) {
      const bw2 = Math.min(w - 40, 400);
      const bx = (w - bw2) / 2;
      const byy = L.safe + 118;
      ctx.save();
      ctx.globalAlpha = this.bossFade;
      ui.text(boss ? boss.T.name : '', w / 2, byy - 12, { size: 16, align: 'center', col: '#ffd0c0', weight: 800 });
      ui.bar(bx, byy, bw2, 12, boss ? boss.hp / boss.maxHp : 0, '#c8382e');
      ctx.restore();
    }

    // ——— touch controls
    this.drawControls(ctx, g, L);

    // ——— interaction prompt
    if (g.interactPrompt) {
      const t = g.interactPrompt;
      const y = h - (L.small ? 190 : 218);
      const tw = ui.measure(t, 15, 700) + 32;
      ui.panel(w / 2 - tw / 2, y - 17, tw, 34, { r: 17, fill: 'rgba(20,17,15,0.86)' });
      ui.text(t, w / 2, y, { size: 15, align: 'center', col: COL.ink });
    }

    // ——— toasts
    let ty = h - Math.max(150, h * 0.22);
    for (let i = this.toasts.length - 1; i >= 0; i--) {
      const t = this.toasts[i];
      t.t += dt;
      if (t.t > 3.4) { this.toasts.splice(i, 1); continue; }
      const a = clamp(Math.min(t.t * 5, (3.4 - t.t) * 2.5), 0, 1);
      ctx.save();
      ctx.globalAlpha = a;
      const tw = ui.measure(t.text, 14, 700) + 26;
      ui.panel(w / 2 - tw / 2, ty - 15, tw, 30, { r: 15, fill: 'rgba(18,15,13,0.85)', edge: 'rgba(228,206,160,0.2)' });
      ui.text(t.text, w / 2, ty, { size: 14, align: 'center', col: t.col });
      ctx.restore();
      ty -= 36;
    }

    // ——— chapter banner
    if (this.banner) {
      const b = this.banner;
      b.t += dt;
      if (b.t > 4.2) this.banner = null;
      else {
        const a = clamp(Math.min(b.t * 2, (4.2 - b.t) * 1.4), 0, 1);
        ctx.save();
        ctx.globalAlpha = a;
        const cy = h * 0.38;
        const grd = ctx.createLinearGradient(0, cy - 60, 0, cy + 60);
        grd.addColorStop(0, 'rgba(0,0,0,0)');
        grd.addColorStop(0.5, 'rgba(0,0,0,0.62)');
        grd.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = grd;
        ctx.fillRect(0, cy - 60, w, 120);
        ui.text(b.title, w / 2, cy - 10, { size: 26, align: 'center', col: COL.gold, weight: 800 });
        ui.text(b.sub, w / 2, cy + 22, { size: 14, align: 'center', col: COL.ink2 });
        ctx.restore();
      }
    }

    // ——— location name on entering a region
    if (g.locationBanner && g.locationBanner.t < 3) {
      const lb = g.locationBanner;
      lb.t += dt;
      const a = clamp(Math.min(lb.t * 3, (3 - lb.t) * 1.6), 0, 1);
      ctx.save();
      ctx.globalAlpha = a;
      const ly = Math.max(96, h * 0.17);
      ui.text(lb.name, w / 2, ly, { size: 22, align: 'center', col: COL.ink, weight: 800 });
      if (lb.sub) ui.text(lb.sub, w / 2, ly + 24, { size: 13, align: 'center', col: COL.ink2 });
      ctx.restore();
    }
  }

  drawMinimap(ctx, g, L) {
    const ui = this.ui;
    const R = L.small ? 46 : 56;
    const cx = L.w - L.safe - R - 52;
    const cy = L.safe + R + 4;
    if (L.w < 380) return this.drawClock(ctx, g, L, L.w - L.safe - 60, L.safe + 16);

    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU);
    ctx.fillStyle = 'rgba(12,14,18,0.9)';
    ctx.fill();
    ctx.save();
    ctx.clip();
    if (g.interior) {
      // dungeon minimap from tiles
      const d = g.interior;
      const scale = 2.4;
      const px = g.player.x / TILE, py = g.player.y / TILE;
      ctx.fillStyle = 'rgba(120,110,100,0.85)';
      for (let y = 0; y < d.h; y++) {
        for (let x = 0; x < d.w; x++) {
          if (d.at(x, y) === 0) continue;
          const sx = cx + (x - px) * scale, sy = cy + (y - py) * scale;
          if (Math.hypot(sx - cx, sy - cy) > R) continue;
          ctx.fillRect(sx, sy, scale, scale);
        }
      }
    } else if (g.mapCanvas) {
      const zoom = 2.2;
      const tx = g.player.x / TILE, ty = g.player.y / TILE;
      const src = R / zoom;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(g.mapCanvas, tx - src, ty - src, src * 2, src * 2, cx - R, cy - R, R * 2, R * 2);
      ctx.imageSmoothingEnabled = true;
      // markers
      const mk = (wx, wy, col, size = 3) => {
        const dx = (wx / TILE - tx) * zoom, dy = (wy / TILE - ty) * zoom;
        if (Math.hypot(dx, dy) > R - 3) return;
        ctx.fillStyle = col;
        ctx.beginPath(); ctx.arc(cx + dx, cy + dy, size, 0, TAU); ctx.fill();
      };
      for (const e of g.enemies) if (e.alive) mk(e.x, e.y, e.boss ? '#ff4a4a' : 'rgba(230,90,70,0.9)', e.boss ? 4 : 2.4);
      for (const n of g.npcs) mk(n.x, n.y, 'rgba(120,220,255,0.85)', 2);
      for (const poi of g.world.pois) {
        if (!poi.discovered) continue;
        mk(poi.x, poi.y, poi.kind === 'shrine' ? '#8fe0ff' : poi.kind === 'dungeon' || poi.kind === 'lair' ? '#c58cff' : '#e8c874', 3);
      }
      for (const m of g.quests.markers()) mk(m.x, m.y, m.main ? '#ffd76a' : '#8fd0ff', 3.4);
    }
    // player arrow
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(g.player.face + Math.PI / 2);
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.moveTo(0, -6); ctx.lineTo(4.4, 5); ctx.lineTo(0, 2.6); ctx.lineTo(-4.4, 5);
    ctx.closePath(); ctx.fill();
    ctx.restore();
    ctx.restore();

    // frame
    ctx.strokeStyle = 'rgba(228,206,160,0.5)';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke();
    ctx.restore();

    // off-screen quest arrows around the minimap
    const tx = g.player.x, ty = g.player.y;
    for (const m of g.quests.markers()) {
      const a = angleTo(tx, ty, m.x, m.y);
      const d = Math.hypot(m.x - tx, m.y - ty);
      if (d < 400) continue;
      ctx.save();
      ctx.translate(cx + Math.cos(a) * (R + 9), cy + Math.sin(a) * (R + 9));
      ctx.rotate(a);
      ctx.fillStyle = m.main ? '#ffd76a' : '#8fd0ff';
      ctx.beginPath(); ctx.moveTo(5, 0); ctx.lineTo(-4, -4); ctx.lineTo(-4, 4);
      ctx.closePath(); ctx.fill();
      ctx.restore();
    }

    this.drawClock(ctx, g, L, cx, cy + R + 14);
  }

  drawClock(ctx, g, L, x, y) {
    const ui = this.ui;
    const hour = g.hour();
    const hh = Math.floor(hour), mm = Math.floor((hour % 1) * 60);
    const label = `${g.day}日目 ${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
    ui.text(label, x, y, { size: 11, align: 'center', col: COL.ink2, weight: 700 });
    // sun/moon glyph
    ctx.save();
    ctx.translate(x - ui.measure(label, 11, 700) / 2 - 12, y);
    if (hour > 6 && hour < 19) {
      ctx.fillStyle = '#ffd76a';
      ctx.beginPath(); ctx.arc(0, 0, 5, 0, TAU); ctx.fill();
    } else {
      ctx.fillStyle = '#cfe0ff';
      ctx.beginPath(); ctx.arc(0, 0, 5, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(12,14,18,1)';
      ctx.beginPath(); ctx.arc(2.6, -1.6, 4.4, 0, TAU); ctx.fill();
    }
    ctx.restore();
    if (g.weather.rain > 0.2 || g.weather.snow > 0.2) {
      ui.text(g.weather.snow > 0.2 ? '❄' : '☂', x + ui.measure(label, 11, 700) / 2 + 10, y, { size: 12, align: 'center', col: '#bcd8ff' });
    }
  }

  drawQuestTracker(ctx, g, L) {
    const ui = this.ui;
    const q = g.quests.active.slice(0, 3);
    if (!q.length) return;
    const x = L.safe + 6;
    let y = L.safe + 96;
    for (const quest of q) {
      const done = quest.state === 'complete';
      ui.text((quest.main ? '★ ' : '・') + quest.title, x, y, {
        size: 12, col: done ? COL.good : quest.main ? COL.gold : COL.ink2, weight: 700,
      });
      const sub = done ? '達成！ 報告しよう' : quest.count > 1 ? `${quest.hint || quest.desc || ''} (${quest.progress}/${quest.count})` : (quest.hint || '');
      if (sub) ui.text(sub, x + 12, y + 15, { size: 11, col: 'rgba(200,192,176,0.8)', weight: 500 });
      y += sub ? 34 : 20;
    }
  }

  drawControls(ctx, g, L) {
    const ui = this.ui;
    const input = g.input;
    const p = g.player;

    // ——— virtual stick
    if (input.stick.active) {
      const s = input.stick;
      ctx.save();
      ctx.globalAlpha = 0.5;
      ctx.beginPath(); ctx.arc(s.ox, s.oy, 62, 0, TAU);
      ctx.fillStyle = 'rgba(20,18,16,0.35)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(240,230,210,0.5)';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath(); ctx.arc(s.ox + s.x * 62, s.oy + s.y * 62, 26, 0, TAU);
      ctx.fillStyle = 'rgba(240,230,210,0.75)';
      ctx.fill();
      ctx.restore();
    } else if (input.usingTouch) {
      ctx.save();
      ctx.globalAlpha = 0.16;
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2;
      const hx = L.w * 0.17, hy = L.h - 110;
      ctx.beginPath(); ctx.arc(hx, hy, 46, 0, TAU); ctx.stroke();
      ctx.beginPath(); ctx.arc(hx, hy, 18, 0, TAU); ctx.stroke();
      ctx.restore();
    }

    const btn = (b, drawFn, opt = {}) => {
      const held = input.down(opt.id);
      ctx.save();
      ctx.globalAlpha = opt.alpha ?? 0.92;
      ctx.beginPath(); ctx.arc(b.x, b.y, b.r, 0, TAU);
      const gr = ctx.createRadialGradient(b.x, b.y - b.r * 0.4, b.r * 0.2, b.x, b.y, b.r);
      gr.addColorStop(0, held ? 'rgba(150,120,70,0.95)' : opt.fill1 || 'rgba(58,50,44,0.85)');
      gr.addColorStop(1, held ? 'rgba(90,68,36,0.95)' : opt.fill2 || 'rgba(28,24,21,0.85)');
      ctx.fillStyle = gr;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = opt.edge || 'rgba(230,212,170,0.45)';
      ctx.stroke();
      if (opt.cool > 0) {
        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.arc(b.x, b.y, b.r, -Math.PI / 2, -Math.PI / 2 + TAU * opt.cool);
        ctx.closePath();
        ctx.fillStyle = 'rgba(0,0,0,0.55)';
        ctx.fill();
      }
      ctx.translate(b.x, b.y);
      if (held) ctx.scale(0.92, 0.92);
      drawFn(ctx, b.r);
      ctx.restore();
    };

    // attack
    btn(L.attack, (c, r) => {
      const w = p.equip.weapon;
      const icon = getIcon(w ? w.weaponKind : 'sword', '#e6ecf2');
      c.drawImage(icon, -r * 0.62, -r * 0.62, r * 1.24, r * 1.24);
    }, { id: 'attack', fill1: 'rgba(120,60,44,0.9)', fill2: 'rgba(60,26,20,0.9)', edge: 'rgba(255,190,150,0.55)' });

    // dodge
    btn(L.dodge, (c, r) => {
      c.strokeStyle = p.sta >= 24 ? '#e6ecf2' : '#7a7268';
      c.lineWidth = 2.6;
      c.beginPath(); c.arc(0, 0, r * 0.42, 0.5, 5.2); c.stroke();
      c.fillStyle = p.sta >= 24 ? '#e6ecf2' : '#7a7268';
      c.beginPath(); c.moveTo(r * 0.36, -r * 0.34); c.lineTo(r * 0.52, -r * 0.02); c.lineTo(r * 0.18, -r * 0.06);
      c.closePath(); c.fill();
    }, { id: 'dodge' });

    // block
    btn(L.block, (c, r) => {
      const icon = getIcon('shield', p.equip.off ? '#cfd6dd' : '#6a655e');
      c.drawImage(icon, -r * 0.6, -r * 0.6, r * 1.2, r * 1.2);
    }, { id: 'block', alpha: p.equip.off ? 0.92 : 0.5 });

    // interact
    btn(L.interact, (c, r) => {
      c.fillStyle = g.interactTarget ? '#ffe0a0' : '#8a8377';
      c.font = `800 ${r * 0.9}px ${FONT}`;
      c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText('▲', 0, 0);
    }, { id: 'interact', alpha: g.interactTarget ? 0.95 : 0.45 });

    // quick item
    const quick = g.quickItem();
    btn(L.item, (c, r) => {
      if (quick) {
        const it = g.itemDef(quick.id);
        c.drawImage(getIcon(it.icon, it.color), -r * 0.62, -r * 0.62, r * 1.24, r * 1.24);
        c.fillStyle = '#fff';
        c.font = `800 ${r * 0.42}px ${FONT}`;
        c.textAlign = 'right'; c.textBaseline = 'bottom';
        c.strokeStyle = 'rgba(0,0,0,0.7)'; c.lineWidth = 3; c.lineJoin = 'round';
        c.strokeText('×' + quick.n, r * 0.72, r * 0.76);
        c.fillText('×' + quick.n, r * 0.72, r * 0.76);
      } else {
        c.fillStyle = '#6a655e';
        c.font = `700 ${r * 0.6}px ${FONT}`;
        c.textAlign = 'center'; c.textBaseline = 'middle';
        c.fillText('薬', 0, 0);
      }
    }, { id: 'item', alpha: quick ? 0.92 : 0.5 });

    // spells
    for (let i = 0; i < 4; i++) {
      const id = p.spellSlots[i];
      if (!id) continue;
      const sp = SPELLS[id];
      const cool = (p.spellCool[id] || 0) / sp.cool;
      const usable = p.mp >= sp.mp && cool <= 0;
      btn(L.spells[i], (c, r) => {
        c.globalAlpha = usable ? 1 : 0.45;
        c.drawImage(getIcon(sp.icon, sp.color), -r * 0.66, -r * 0.66, r * 1.32, r * 1.32);
        c.globalAlpha = 1;
        c.fillStyle = usable ? '#9fd0ff' : '#6a7280';
        c.font = `700 ${r * 0.38}px ${FONT}`;
        c.textAlign = 'center'; c.textBaseline = 'top';
        c.fillText(String(sp.mp), 0, r * 0.42);
      }, { id: 'spell' + i, cool });
    }

    // menu + map buttons
    btn(L.menu, (c, r) => {
      c.strokeStyle = '#e6ecf2'; c.lineWidth = 2.2; c.lineCap = 'round';
      for (let i = -1; i <= 1; i++) {
        c.beginPath(); c.moveTo(-r * 0.4, i * r * 0.32); c.lineTo(r * 0.4, i * r * 0.32); c.stroke();
      }
    }, { id: 'menu', alpha: 0.8 });
    btn(L.map, (c, r) => {
      c.strokeStyle = '#e6ecf2'; c.lineWidth = 2; c.lineJoin = 'round';
      c.beginPath();
      c.moveTo(-r * 0.5, -r * 0.34); c.lineTo(-r * 0.16, -r * 0.5); c.lineTo(r * 0.16, -r * 0.3);
      c.lineTo(r * 0.5, -r * 0.46); c.lineTo(r * 0.5, r * 0.36); c.lineTo(r * 0.16, r * 0.5);
      c.lineTo(-r * 0.16, r * 0.3); c.lineTo(-r * 0.5, r * 0.46); c.closePath(); c.stroke();
      c.beginPath(); c.moveTo(-r * 0.16, -r * 0.5); c.lineTo(-r * 0.16, r * 0.3);
      c.moveTo(r * 0.16, -r * 0.3); c.lineTo(r * 0.16, r * 0.5); c.stroke();
    }, { id: 'map', alpha: 0.8 });
  }
}
