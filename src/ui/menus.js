// menus.js — full-screen interfaces: inventory, map, quests, shops, dialogue…

import { UI, COL, FONT } from './ui.js';
import { clamp, lerp, TAU, roundRect, radial, makeRNG, dist } from '../core/util.js';
import { getIcon, iconFor } from '../render/icons.js';
import {
  ITEMS, RARITY, RECIPES, displayName, rarityColor, effStats, itemPower, upgradeCost, shopStock,
} from '../game/items.js';
import { SPELLS } from '../game/entities.js';
import { TILE, BIOME_NAME } from '../world/worldgen.js';
import { greeting, options as dlgOptions, rumor } from '../game/dialogue.js';
import { audio } from '../core/audio.js';
import { ACHIEVEMENTS, achievementProgress } from '../game/achievements.js';

const STAT_LABEL = {
  atk: '攻撃', def: '防御', hp: '最大HP', mp: '最大MP', mag: '魔力', spd: '素早さ',
  crit: '会心率', leech: '吸収', sta: 'スタミナ', fire: '炎属性', ice: '氷属性', shock: '雷属性',
};
const SLOT_LABEL = { weapon: '武器', head: '頭', body: '胴', off: '盾', trinket: '装飾' };

export class Menus {
  constructor(ui) {
    this.ui = ui;
    this.stack = [];
    this.data = {};
    this.sel = null;
    this.tab = 0;
    this.fade = 0;
    this.mapView = { x: 0, y: 0, zoom: 1 };
  }

  get isOpen() { return this.stack.length > 0; }
  get top() { return this.stack[this.stack.length - 1]; }

  open(name, data = {}) {
    this.stack.push(name);
    this.data = { ...this.data, ...data };
    this.sel = null;
    this.tab = data.tab ?? 0;
    this.ui.resetScroll(name);
    audio.sfx('uiBig');
  }
  close() {
    this.stack.pop();
    this.sel = null;
    audio.sfx('ui');
  }
  closeAll() { this.stack.length = 0; this.sel = null; }

  // ————————————————————————————————————————————————— frame

  draw(ctx, g, dt) {
    const ui = this.ui;
    const name = this.top;
    if (!name) return;
    this.fade = lerp(this.fade, 1, 1 - Math.pow(0.001, dt));
    ui.dim(0.52 * this.fade + (name === 'title' || name === 'death' ? 0.3 : 0));
    switch (name) {
      case 'title': this.drawTitle(ctx, g); break;
      case 'confirmNew': this.drawTitle(ctx, g); break;
      case 'pause': this.drawPause(ctx, g); break;
      case 'inventory': this.drawInventory(ctx, g); break;
      case 'status': this.drawStatus(ctx, g); break;
      case 'chronicle': this.drawChronicle(ctx, g); break;
      case 'map': this.drawMap(ctx, g, dt); break;
      case 'quests': this.drawQuests(ctx, g); break;
      case 'dialogue': this.drawDialogue(ctx, g); break;
      case 'shop': this.drawShop(ctx, g); break;
      case 'craft': this.drawCraft(ctx, g); break;
      case 'upgrade': this.drawUpgrade(ctx, g); break;
      case 'board': this.drawBoard(ctx, g); break;
      case 'settings': this.drawSettings(ctx, g); break;
      case 'death': this.drawDeath(ctx, g); break;
      case 'victory': this.drawVictory(ctx, g); break;
      case 'spellbook': this.drawSpellbook(ctx, g); break;
    }
  }

  frame(ctx, title, o = {}) {
    const ui = this.ui;
    const w = Math.min(ui.w - 20, o.w || 560);
    const h = Math.min(ui.h - 20, o.h || 720);
    const x = (ui.w - w) / 2, y = (ui.h - h) / 2;
    ui.panel(x, y, w, h, { r: 18, fill: 'rgba(22,19,17,0.96)' });
    // header
    ui.text(title, x + 20, y + 30, { size: 20, col: COL.gold, weight: 800 });
    const ctxx = ctx;
    ctxx.save();
    ctxx.strokeStyle = 'rgba(228,206,160,0.22)';
    ctxx.lineWidth = 1;
    ctxx.beginPath(); ctxx.moveTo(x + 20, y + 48); ctxx.lineTo(x + w - 20, y + 48); ctxx.stroke();
    ctxx.restore();
    // close
    const closed = ui.iconButton(x + w - 26, y + 28, 17, (c) => {
      c.strokeStyle = COL.ink; c.lineWidth = 2.2; c.lineCap = 'round';
      c.beginPath(); c.moveTo(-5, -5); c.lineTo(5, 5); c.moveTo(5, -5); c.lineTo(-5, 5); c.stroke();
    });
    return { x, y, w, h, closed, cx: x + 20, cy: y + 62, cw: w - 40 };
  }

  tabs(ctx, f, labels) {
    const ui = this.ui;
    const n = labels.length;
    const bw = f.cw / n;
    for (let i = 0; i < n; i++) {
      const x = f.cx + i * bw;
      if (ui.button(x + 2, f.cy, bw - 4, 34, labels[i], { size: 13, selected: this.tab === i, r: 9 })) {
        this.tab = i;
        ui.resetScroll(this.top + i);
      }
    }
    return f.cy + 44;
  }

  // ————————————————————————————————————————————————— title

  drawTitle(ctx, g) {
    const ui = this.ui;
    const w = ui.w, h = ui.h;
    // backdrop art
    const grd = ctx.createLinearGradient(0, 0, 0, h);
    grd.addColorStop(0, '#0d1526');
    grd.addColorStop(0.45, '#20283c');
    grd.addColorStop(0.62, '#4a3a48');
    grd.addColorStop(1, '#120e12');
    ctx.fillStyle = grd;
    ctx.fillRect(0, 0, w, h);
    // sun
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    radial(ctx, w * 0.5, h * 0.62, Math.min(w, h) * 0.5, 'rgba(255,170,110,0.35)', 'rgba(255,120,80,0)');
    ctx.restore();
    // mountains
    const t = performance.now() / 1000;
    for (let layer = 3; layer >= 0; layer--) {
      ctx.fillStyle = `rgba(${18 + layer * 12},${16 + layer * 14},${26 + layer * 16},${0.9})`;
      ctx.beginPath();
      ctx.moveTo(0, h);
      const baseY = h * (0.60 + layer * 0.06);
      for (let x = 0; x <= w; x += 12) {
        const n = Math.sin(x * 0.006 + layer * 3) * 26 + Math.sin(x * 0.017 + layer) * 14 + Math.sin(x * 0.03 + layer * 2) * 7;
        ctx.lineTo(x, baseY - n * (1 + layer * 0.3));
      }
      ctx.lineTo(w, h);
      ctx.closePath();
      ctx.fill();
    }
    // title
    const cy = h * 0.24;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = `800 ${Math.min(58, w * 0.13)}px ${FONT}`;
    ctx.lineWidth = 8; ctx.lineJoin = 'round';
    ctx.strokeStyle = 'rgba(0,0,0,0.55)';
    ctx.strokeText('アエテリア戦記', w / 2, cy);
    const tg = ctx.createLinearGradient(0, cy - 40, 0, cy + 12);
    tg.addColorStop(0, '#fff3d0');
    tg.addColorStop(0.5, '#e8c874');
    tg.addColorStop(1, '#b08840');
    ctx.fillStyle = tg;
    ctx.fillText('アエテリア戦記', w / 2, cy);
    ctx.restore();
    ui.text('— 決定版・果てなき大陸の物語 —', w / 2, cy + 30, { size: 14, align: 'center', col: 'rgba(230,220,200,0.8)' });

    const bw = Math.min(300, w - 60);
    const bx = (w - bw) / 2;
    const hasSave = g.hasSave();
    const short = h < 520;
    const bh = short ? 42 : 52;
    const gap = short ? 9 : 11;
    const count = hasSave ? 3 : 2;
    const totalH = count * bh + (count - 1) * gap;
    let by = Math.min(h * 0.52, h - 58 - totalH);
    if (hasSave && ui.button(bx, by, bw, bh, 'つづきから', { primary: true, size: short ? 16 : 18, big: true })) {
      g.loadGame();
      this.closeAll();
    }
    if (hasSave) by += bh + gap;
    if (ui.button(bx, by, bw, bh, hasSave ? 'はじめから' : '冒険を始める', { primary: !hasSave, size: short ? 16 : 18, big: true })) {
      if (hasSave) this.open('confirmNew');
      else { g.newGame(); this.closeAll(); }
    }
    by += bh + gap;
    if (ui.button(bx, by, bw, bh, '設定', { size: short ? 15 : 16 })) this.open('settings');
    by += bh + 6;
    ui.text('タップ/スワイプで移動・戦闘。キーボードにも対応。', w / 2, by + 14, { size: 12, align: 'center', col: 'rgba(220,210,190,0.6)' });
    ui.text(`seed: ${g.seed}`, w / 2, h - 18, { size: 11, align: 'center', col: 'rgba(200,190,170,0.35)' });

    if (this.stack.includes('confirmNew')) {
      // simple confirm overlay
      ui.dim(0.6);
      const cw = Math.min(340, w - 40), ch = 180;
      const cx = (w - cw) / 2, cyy = (h - ch) / 2;
      ui.panel(cx, cyy, cw, ch, { r: 16 });
      ui.text('新しく始めますか？', cx + cw / 2, cyy + 46, { size: 17, align: 'center' });
      ui.text('現在のセーブは失われます。', cx + cw / 2, cyy + 74, { size: 13, align: 'center', col: COL.ink2 });
      if (ui.button(cx + 20, cyy + 110, cw / 2 - 30, 44, 'はじめる', { danger: true })) {
        g.newGame(); this.closeAll();
      }
      if (ui.button(cx + cw / 2 + 10, cyy + 110, cw / 2 - 30, 44, 'もどる')) this.close();
    }
  }

  // ————————————————————————————————————————————————— pause menu

  drawPause(ctx, g) {
    const ui = this.ui;
    const f = this.frame(ctx, 'メニュー', { w: 420, h: 520 });
    if (f.closed) { this.close(); return; }
    const items = [
      ['持ち物・装備', () => this.open('inventory')],
      ['ステータス', () => this.open('status')],
      ['冒険の記録', () => this.open('chronicle')],
      ['魔法', () => this.open('spellbook')],
      ['クエスト', () => this.open('quests')],
      ['地図', () => this.open('map')],
      ['設定', () => this.open('settings')],
      ['セーブして終了', () => { g.save(); this.closeAll(); this.open('title'); }],
    ];
    let y = f.cy + 4;
    const rowH = Math.min(58, (f.y + f.h - 42 - y) / items.length);
    for (const [label, fn] of items) {
      if (ui.button(f.cx, y, f.cw, Math.max(40, rowH - 7), label, { size: 15, align: 'left' })) fn();
      y += rowH;
    }
    ui.text(`プレイ時間 ${Math.floor(g.player.playtime / 60)}分 ・ 討伐 ${g.player.kills}`, f.cx, f.y + f.h - 26, { size: 12, col: COL.dim });
  }

  // ————————————————————————————————————————————————— inventory

  drawInventory(ctx, g) {
    const ui = this.ui;
    const p = g.player;
    const f = this.frame(ctx, '持ち物', { w: 600, h: 760 });
    if (f.closed) { this.close(); return; }
    let y = this.tabs(ctx, f, ['装備', '道具', '素材']);

    // equipped row
    if (this.tab === 0) {
      const slots = ['weapon', 'head', 'body', 'off', 'trinket'];
      const sw = f.cw / 5;
      for (let i = 0; i < 5; i++) {
        const it = p.equip[slots[i]];
        const x = f.cx + i * sw;
        const boxH = 66;
        ui.panel(x + 3, y, sw - 6, boxH, { r: 10, fill: 'rgba(40,34,30,0.9)', edge: it ? rarityColor(it) + '66' : COL.edge2 });
        if (it) {
          const ic = iconFor(it);
          ctx.drawImage(ic, x + sw / 2 - 20, y + 4, 40, 40);
          ui.text(SLOT_LABEL[slots[i]], x + sw / 2, y + boxH - 12, { size: 10, align: 'center', col: COL.dim });
          if (it.plus) ui.text('+' + it.plus, x + sw - 12, y + 14, { size: 11, align: 'right', col: COL.gold });
        } else {
          ui.text(SLOT_LABEL[slots[i]], x + sw / 2, y + boxH / 2, { size: 11, align: 'center', col: COL.dim });
        }
        const inside = ui.input.ui.x > x && ui.input.ui.x < x + sw && ui.input.ui.y > y && ui.input.ui.y < y + boxH;
        if (inside && ui.input.ui.released && !ui.input.ui.moved && it) {
          this.sel = { item: it, equipped: true, slot: slots[i] };
          audio.sfx('ui');
        }
      }
      y += 78;
    }

    // list
    const listH = f.y + f.h - y - (this.sel ? 210 : 24);
    const filtered = this.filterInv(g, this.tab);
    const rowH = 56;
    const off = ui.beginScroll('inv' + this.tab, f.cx, y, f.cw, listH, filtered.length * rowH + 8);
    let ry = y - off + 4;
    for (const entry of filtered) {
      if (ry > y - rowH && ry < y + listH) this.itemRow(ctx, g, entry, f.cx, ry, f.cw, rowH);
      ry += rowH;
    }
    ui.endScroll('inv' + this.tab);
    if (!filtered.length) ui.text('なにもない', f.cx + f.cw / 2, y + 40, { size: 14, align: 'center', col: COL.dim });

    // detail
    if (this.sel) this.itemDetail(ctx, g, f, this.sel);
  }

  filterInv(g, tab) {
    const inv = g.player.inv;
    if (tab === 0) return inv.filter((e) => e.item && (e.item.type === 'weapon' || e.item.type === 'armor' || e.item.type === 'trinket'));
    if (tab === 1) return inv.filter((e) => e.id && ITEMS[e.id] && (ITEMS[e.id].type === 'consumable' || ITEMS[e.id].type === 'quest'));
    return inv.filter((e) => e.id && ITEMS[e.id] && ITEMS[e.id].type === 'material');
  }

  itemRow(ctx, g, entry, x, y, w, h, opt = {}) {
    const ui = this.ui;
    const it = entry.item || ITEMS[entry.id];
    if (!it) return;
    const selected = this.sel && (this.sel.entry === entry || this.sel.item === it);
    const inside = ui.input.ui.x > x && ui.input.ui.x < x + w && ui.input.ui.y > y && ui.input.ui.y < y + h;
    ui.panel(x, y + 2, w, h - 6, {
      r: 10, fill: inside && ui.input.ui.down ? 'rgba(70,60,48,0.9)' : 'rgba(38,33,29,0.82)',
      edge: selected ? COL.gold : COL.edge2, lw: selected ? 2 : 1,
    });
    const ic = entry.item ? iconFor(entry.item) : getIcon(it.icon, it.color);
    ctx.drawImage(ic, x + 8, y + 8, 38, 38);
    const col = entry.item ? rarityColor(entry.item) : COL.ink;
    ui.text(displayName(it), x + 54, y + 20, { size: 14, col, weight: 700 });
    let sub = '';
    if (entry.item) {
      const s = effStats(entry.item);
      sub = Object.keys(s).filter((k) => s[k]).slice(0, 3)
        .map((k) => `${STAT_LABEL[k] || k} ${k === 'crit' || k === 'leech' ? Math.round(s[k] * 100) + '%' : '+' + s[k]}`).join('  ');
      if (g.player.equip[entry.item.slot] === entry.item) sub = '【装備中】 ' + sub;
    } else {
      sub = it.desc || '';
    }
    ui.text(sub, x + 54, y + 38, { size: 11, col: COL.ink2, weight: 500 });
    if (entry.n > 1) ui.text('×' + entry.n, x + w - 14, y + h / 2 - 3, { size: 15, align: 'right', col: COL.ink });
    if (opt.price != null) ui.text(opt.price + 'G', x + w - 14, y + h / 2 - 3, { size: 14, align: 'right', col: COL.gold });
    if (inside && ui.input.ui.released && !ui.input.ui.moved) {
      this.sel = { entry, item: entry.item || null, id: entry.id, price: opt.price };
      audio.sfx('ui');
    }
  }

  itemDetail(ctx, g, f, sel) {
    const ui = this.ui;
    const p = g.player;
    const h = 196;
    const y = f.y + f.h - h - 14;
    ui.panel(f.cx, y, f.cw, h, { r: 14, fill: 'rgba(30,26,23,0.98)' });
    const it = sel.item || ITEMS[sel.id];
    if (!it) return;
    const ic = sel.item ? iconFor(sel.item) : getIcon(it.icon, it.color);
    ctx.drawImage(ic, f.cx + 12, y + 12, 48, 48);
    ui.text(displayName(it), f.cx + 70, y + 24, { size: 16, col: sel.item ? rarityColor(sel.item) : COL.ink, weight: 800 });
    if (sel.item) {
      ui.text(`${RARITY[sel.item.rarity].name} ・ Lv${sel.item.level} ・ ${SLOT_LABEL[sel.item.slot]}`, f.cx + 70, y + 44, { size: 11, col: COL.ink2 });
      const s = effStats(sel.item);
      const cur = p.equip[sel.item.slot];
      const cs = cur ? effStats(cur) : {};
      let sx = f.cx + 12, sy = y + 76;
      for (const k in s) {
        if (!s[k]) continue;
        const val = k === 'crit' || k === 'leech' ? (s[k] * 100).toFixed(1) + '%' : '+' + s[k];
        const diff = (s[k] || 0) - (cs[k] || 0);
        const dcol = cur && cur !== sel.item ? (diff > 0 ? COL.good : diff < 0 ? COL.bad : COL.ink2) : COL.ink;
        ui.text(`${STAT_LABEL[k] || k} ${val}`, sx, sy, { size: 12, col: dcol });
        sx += 96;
        if (sx > f.cx + f.cw - 90) { sx = f.cx + 12; sy += 18; }
      }
    } else {
      ui.paragraph(it.desc || '', f.cx + 70, y + 46, f.cw - 90, { size: 12, col: COL.ink2, lh: 17 });
    }
    ui.text(`価値 ${it.value || 0}G`, f.cx + f.cw - 14, y + 24, { size: 12, align: 'right', col: COL.gold });

    // actions
    const bw = (f.cw - 24) / 3;
    const by = y + h - 50;
    if (sel.item) {
      const equipped = p.equip[sel.item.slot] === sel.item;
      if (ui.button(f.cx + 8, by, bw - 6, 40, equipped ? '外す' : '装備する', { primary: !equipped, size: 14 })) {
        if (equipped) g.unequip(sel.item.slot);
        else g.equipItem(sel.item);
        audio.sfx('uiBig');
      }
      if (ui.button(f.cx + 8 + bw, by, bw - 6, 40, `売る (${Math.floor((it.value || 0) * 0.4)}G)`, { size: 13, disabled: equipped })) {
        if (!equipped) { g.sellItem(sel.entry); this.sel = null; }
      }
      if (ui.button(f.cx + 8 + bw * 2, by, bw - 6, 40, '捨てる', { size: 13, danger: true, disabled: equipped })) {
        if (!equipped) { g.dropItem(sel.entry); this.sel = null; }
      }
    } else {
      const usable = it.type === 'consumable';
      if (ui.button(f.cx + 8, by, bw - 6, 40, '使う', { primary: usable, size: 14, disabled: !usable })) {
        if (usable) { g.useItem(sel.id); if (g.countItem(sel.id) <= 0) this.sel = null; }
      }
      if (ui.button(f.cx + 8 + bw, by, bw - 6, 40, `売る (${Math.floor((it.value || 0) * 0.4)}G)`, { size: 13, disabled: it.type === 'quest' })) {
        if (it.type !== 'quest') { g.sellItem(sel.entry); if (g.countItem(sel.id) <= 0) this.sel = null; }
      }
      if (ui.button(f.cx + 8 + bw * 2, by, bw - 6, 40, '閉じる', { size: 13 })) this.sel = null;
    }
  }

  // ————————————————————————————————————————————————— status

  drawStatus(ctx, g) {
    const ui = this.ui;
    const p = g.player;
    const f = this.frame(ctx, 'ステータス', { w: 520, h: 640 });
    if (f.closed) { this.close(); return; }
    let y = f.cy + 10;
    // hero card
    ui.panel(f.cx, y, f.cw, 110, { r: 12, fill: 'rgba(38,32,28,0.9)' });
    g.drawHeroPortrait(ctx, f.cx + 58, y + 96, 1.9);
    ui.text('放浪の冒険者', f.cx + 120, y + 26, { size: 18, weight: 800 });
    ui.text(`Lv ${p.level}`, f.cx + 120, y + 50, { size: 14, col: COL.gold });
    ui.bar(f.cx + 120, y + 64, f.cw - 140, 8, p.xp / p.xpNext, COL.xp);
    ui.text(`次のレベルまで ${p.xpNext - p.xp} EXP`, f.cx + 120, y + 84, { size: 11, col: COL.ink2 });
    y += 124;

    const rows = [
      ['HP', `${Math.ceil(p.hp)} / ${p.maxHp}`, COL.hp],
      ['MP', `${Math.ceil(p.mp)} / ${p.maxMp}`, COL.mp],
      ['スタミナ', `${Math.ceil(p.sta)} / ${p.maxSta}`, COL.sta],
      ['攻撃力', Math.round(p.stats.atk), COL.ink],
      ['防御力', Math.round(p.stats.def), COL.ink],
      ['魔力', Math.round(p.stats.mag), COL.ink],
      ['素早さ', Math.round(p.stats.spd || 0), COL.ink],
      ['会心率', Math.round((p.stats.crit || 0) * 100) + '%', COL.ink],
      ['吸収', Math.round((p.stats.leech || 0) * 100) + '%', COL.ink],
      ['所持金', p.gold + ' G', COL.gold],
      ['討伐数', p.kills, COL.ink],
      ['探索した地点', g.world.pois.filter((q) => q.discovered).length + ' / ' + g.world.pois.length, COL.ink],
      ['プレイ時間', `${Math.floor(p.playtime / 3600)}時間 ${Math.floor(p.playtime / 60) % 60}分`, COL.ink],
    ];
    for (const [k, v, c] of rows) {
      ui.text(k, f.cx + 8, y, { size: 14, col: COL.ink2 });
      ui.text(String(v), f.cx + f.cw - 8, y, { size: 14, align: 'right', col: c });
      ctx.save();
      ctx.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx.beginPath(); ctx.moveTo(f.cx, y + 14); ctx.lineTo(f.cx + f.cw, y + 14); ctx.stroke();
      ctx.restore();
      y += 30;
    }
  }

  drawChronicle(ctx, g) {
    const ui = this.ui;
    const f = this.frame(ctx, '冒険の記録', { w: 560, h: 720 });
    if (f.closed) { this.close(); return; }
    const unlocked = ACHIEVEMENTS.filter((a) => g.achievements.has(a.id)).length;
    let y = f.cy + 4;
    ui.panel(f.cx, y, f.cw, 68, { r: 12, fill: 'rgba(44,37,29,0.92)', edge: 'rgba(232,200,116,0.45)' });
    ui.text('アエテリア踏破録', f.cx + 14, y + 22, { size: 17, col: COL.gold, weight: 800 });
    ui.text(`${unlocked} / ${ACHIEVEMENTS.length} 達成`, f.cx + f.cw - 14, y + 22, { size: 13, align: 'right', col: COL.ink2, weight: 700 });
    ui.bar(f.cx + 14, y + 44, f.cw - 28, 9, unlocked / ACHIEVEMENTS.length, COL.gold);
    y += 80;

    const rowH = 82;
    const listH = f.y + f.h - y - 18;
    const off = ui.beginScroll('chronicle', f.cx, y, f.cw, listH, ACHIEVEMENTS.length * rowH + 6);
    let ry = y - off + 3;
    for (const def of ACHIEVEMENTS) {
      if (ry > y - rowH && ry < y + listH) {
        const done = g.achievements.has(def.id);
        const progress = achievementProgress(def, g);
        ui.panel(f.cx, ry, f.cw, rowH - 8, {
          r: 10, fill: done ? 'rgba(55,47,34,0.94)' : 'rgba(31,28,26,0.82)',
          edge: done ? 'rgba(255,220,130,0.48)' : COL.edge2,
        });
        ctx.save();
        ctx.beginPath(); ctx.arc(f.cx + 30, ry + 33, 20, 0, TAU);
        ctx.fillStyle = done ? 'rgba(151,111,48,0.9)' : 'rgba(62,58,54,0.9)'; ctx.fill();
        ctx.strokeStyle = done ? COL.gold : COL.edge2; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.restore();
        ui.text(done ? def.icon : '？', f.cx + 30, ry + 33, { size: 15, align: 'center', col: done ? '#fff2bd' : COL.dim, weight: 800 });
        ui.text(def.name, f.cx + 60, ry + 20, { size: 15, col: done ? COL.gold : COL.ink, weight: 800 });
        ui.text(def.desc, f.cx + 60, ry + 41, { size: 11, col: COL.ink2, weight: 500 });
        ui.bar(f.cx + 60, ry + 56, f.cw - 136, 6, progress.ratio, done ? COL.gold : '#7194a4');
        ui.text(done ? '達成' : `${Math.min(progress.value, progress.target)} / ${progress.target}`, f.cx + f.cw - 12, ry + 58, {
          size: 10, align: 'right', col: done ? COL.good : COL.dim, weight: 700,
        });
      }
      ry += rowH;
    }
    ui.endScroll('chronicle');
  }

  drawSpellbook(ctx, g) {
    const ui = this.ui;
    const p = g.player;
    const f = this.frame(ctx, '魔法', { w: 520, h: 620 });
    if (f.closed) { this.close(); return; }
    let y = f.cy + 8;
    ui.text('スロットを選んでから魔法を割り当てます', f.cx, y, { size: 12, col: COL.ink2 });
    y += 22;
    // slots
    const sw = f.cw / 4;
    for (let i = 0; i < 4; i++) {
      const id = p.spellSlots[i];
      const x = f.cx + i * sw;
      const selected = this.slotSel === i;
      ui.panel(x + 4, y, sw - 8, 72, { r: 10, edge: selected ? COL.gold : COL.edge2, lw: selected ? 2.2 : 1 });
      if (id) {
        ctx.drawImage(getIcon(SPELLS[id].icon, SPELLS[id].color), x + sw / 2 - 20, y + 6, 40, 40);
        ui.text(SPELLS[id].name, x + sw / 2, y + 58, { size: 10, align: 'center' });
      } else ui.text('空き', x + sw / 2, y + 36, { size: 12, align: 'center', col: COL.dim });
      const inside = ui.input.ui.x > x && ui.input.ui.x < x + sw && ui.input.ui.y > y && ui.input.ui.y < y + 72;
      if (inside && ui.input.ui.released && !ui.input.ui.moved) { this.slotSel = i; audio.sfx('ui'); }
    }
    y += 88;
    for (const id in SPELLS) {
      const sp = SPELLS[id];
      const known = p.knownSpells.includes(id);
      ui.panel(f.cx, y, f.cw, 62, { r: 10, fill: known ? 'rgba(38,33,29,0.85)' : 'rgba(24,21,19,0.7)' });
      ctx.save();
      ctx.globalAlpha = known ? 1 : 0.35;
      ctx.drawImage(getIcon(sp.icon, sp.color), f.cx + 8, y + 10, 42, 42);
      ctx.restore();
      ui.text(sp.name, f.cx + 58, y + 20, { size: 15, col: known ? COL.ink : COL.dim, weight: 700 });
      ui.text(known ? sp.desc : `Lv${sp.level} で習得`, f.cx + 58, y + 40, { size: 11, col: COL.ink2 });
      ui.text(`MP ${sp.mp}`, f.cx + f.cw - 12, y + 20, { size: 12, align: 'right', col: COL.mp });
      const inside = ui.input.ui.x > f.cx && ui.input.ui.x < f.cx + f.cw && ui.input.ui.y > y && ui.input.ui.y < y + 62;
      if (known && inside && ui.input.ui.released && !ui.input.ui.moved && this.slotSel != null) {
        const existing = p.spellSlots.indexOf(id);
        if (existing >= 0) p.spellSlots[existing] = null;
        p.spellSlots[this.slotSel] = id;
        audio.sfx('uiBig');
      }
      y += 70;
    }
  }

  // ————————————————————————————————————————————————— map

  drawMap(ctx, g, dt) {
    const ui = this.ui;
    const f = this.frame(ctx, '大陸地図', { w: 640, h: 780 });
    if (f.closed) { this.close(); return; }
    const mw = f.cw, mh = f.h - 150;
    const mx = f.cx, my = f.cy + 4;
    ui.panel(mx, my, mw, mh, { r: 10, fill: 'rgba(12,14,18,1)' });
    ctx.save();
    roundRect(ctx, mx, my, mw, mh, 10);
    ctx.clip();
    const W = g.world.w;
    const scale = Math.min(mw / W, mh / W) * this.mapView.zoom;
    // centre on player
    const px = g.player.x / TILE, py = g.player.y / TILE;
    const ox = mx + mw / 2 - px * scale + this.mapView.x;
    const oy = my + mh / 2 - py * scale + this.mapView.y;
    ctx.imageSmoothingEnabled = this.mapView.zoom < 2;
    if (g.mapCanvas) ctx.drawImage(g.mapCanvas, ox, oy, W * scale, W * scale);
    if (g.fogCanvas) {
      ctx.globalAlpha = 0.86;
      ctx.drawImage(g.fogCanvas, ox, oy, W * scale, W * scale);
      ctx.globalAlpha = 1;
    }
    // settlements
    for (const s of g.world.settlements) {
      if (!s.discovered) continue;
      const x = ox + s.x * scale, y = oy + s.y * scale;
      ctx.fillStyle = '#e8c874';
      ctx.beginPath();
      ctx.moveTo(x, y - 6); ctx.lineTo(x + 5, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 5, y);
      ctx.closePath(); ctx.fill();
      ui.text(s.name, x, y - 12, { size: 10, align: 'center', col: '#ffeec0' });
    }
    // POIs
    for (const poi of g.world.pois) {
      if (!poi.discovered) continue;
      const x = ox + poi.tx * scale, y = oy + poi.ty * scale;
      const col = poi.kind === 'shrine' ? '#8fe0ff' : poi.kind === 'dungeon' || poi.kind === 'lair' ? '#c58cff' : poi.kind === 'camp' ? '#ff8a6a' : '#d8d0b8';
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(x, y, 4, 0, TAU); ctx.fill();
      if (poi.kind === 'shrine' && poi.activated) {
        ctx.strokeStyle = 'rgba(140,230,255,0.8)';
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(x, y, 8 + Math.sin(g.time * 3) * 2, 0, TAU); ctx.stroke();
      }
    }
    // quest markers
    for (const m of g.quests.markers()) {
      const x = ox + (m.x / TILE) * scale, y = oy + (m.y / TILE) * scale;
      ctx.fillStyle = m.main ? '#ffd76a' : '#8fd0ff';
      ctx.beginPath();
      ctx.moveTo(x, y - 9); ctx.lineTo(x + 6, y - 1); ctx.lineTo(x, y + 3); ctx.lineTo(x - 6, y - 1);
      ctx.closePath(); ctx.fill();
    }
    // player
    ctx.save();
    ctx.translate(ox + px * scale, oy + py * scale);
    ctx.rotate(g.player.face + Math.PI / 2);
    ctx.fillStyle = '#fff';
    ctx.strokeStyle = '#000'; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(0, -8); ctx.lineTo(5.5, 6); ctx.lineTo(0, 3); ctx.lineTo(-5.5, 6);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    ctx.restore();
    ctx.restore();

    // pan & zoom
    const inp = ui.input;
    const insideMap = inp.ui.x > mx && inp.ui.x < mx + mw && inp.ui.y > my && inp.ui.y < my + mh;
    if (insideMap && inp.ui.down) {
      this.mapView.x += inp.ui.dragX;
      this.mapView.y += inp.ui.dragY;
    }
    // keep the continent on screen
    const span = W * scale;
    this.mapView.x = clamp(this.mapView.x, -(span * 0.5 + mw * 0.4), span * 0.5 + mw * 0.4);
    this.mapView.y = clamp(this.mapView.y, -(span * 0.5 + mh * 0.4), span * 0.5 + mh * 0.4);
    if (insideMap && inp.ui.wheel) {
      this.mapView.zoom = clamp(this.mapView.zoom * (1 - inp.ui.wheel * 0.001), 1, 6);
    }
    // tap to fast travel
    if (insideMap && inp.ui.released && !inp.ui.moved) {
      const wx = (inp.ui.x - ox) / scale, wy = (inp.ui.y - oy) / scale;
      let best = null, bd = 14 / scale;
      for (const poi of g.world.pois) {
        if (poi.kind !== 'shrine' || !poi.activated) continue;
        const d = dist(wx, wy, poi.tx, poi.ty);
        if (d < bd) { bd = d; best = poi; }
      }
      for (const s of g.world.settlements) {
        if (!s.discovered) continue;
        const d = dist(wx, wy, s.x, s.y);
        if (d < bd) { bd = d; best = { travel: s, name: s.name + s.label, x: (s.x + 0.5) * TILE, y: (s.y + 0.5) * TILE }; }
      }
      if (best) this.travelTarget = best;
    }

    // zoom buttons
    if (ui.button(mx + mw - 44, my + mh - 88, 34, 34, '＋', { r: 8 })) this.mapView.zoom = clamp(this.mapView.zoom * 1.5, 1, 6);
    if (ui.button(mx + mw - 44, my + mh - 46, 34, 34, '－', { r: 8 })) {
      this.mapView.zoom = clamp(this.mapView.zoom / 1.5, 1, 6);
      if (this.mapView.zoom <= 1.01) { this.mapView.x = 0; this.mapView.y = 0; }
    }

    // legend / travel
    const by = f.y + f.h - 96;
    if (this.travelTarget) {
      const t = this.travelTarget;
      ui.text(`${t.name} へ転移しますか？`, f.cx, by, { size: 14 });
      if (ui.button(f.cx, by + 16, f.cw / 2 - 8, 44, '転移する', { primary: true })) {
        g.fastTravel(t.x, t.y, t.name);
        this.travelTarget = null;
        this.closeAll();
      }
      if (ui.button(f.cx + f.cw / 2 + 8, by + 16, f.cw / 2 - 8, 44, 'やめる')) this.travelTarget = null;
    } else {
      ui.text('祠や街をタップすると転移できます（発見済みのみ）', f.cx, by + 6, { size: 12, col: COL.ink2 });
      const b = g.biomeName();
      ui.text(`現在地: ${b}`, f.cx, by + 30, { size: 13, col: COL.ink });
    }
  }

  // ————————————————————————————————————————————————— quests

  drawQuests(ctx, g) {
    const ui = this.ui;
    const f = this.frame(ctx, 'クエスト', { w: 560, h: 700 });
    if (f.closed) { this.close(); return; }
    let y = this.tabs(ctx, f, ['進行中', '達成済み']);
    const list = this.tab === 0 ? g.quests.active : g.quests.done;
    const listH = f.y + f.h - y - 20;
    const rows = list.map((q) => ({ q, h: 92 }));
    const total = rows.reduce((a, r) => a + r.h, 0);
    const off = ui.beginScroll('q' + this.tab, f.cx, y, f.cw, listH, total + 8);
    let ry = y - off + 4;
    for (const r of rows) {
      const q = r.q;
      if (ry > y - r.h && ry < y + listH) {
        ui.panel(f.cx, ry, f.cw, r.h - 8, { r: 10, fill: 'rgba(36,31,28,0.85)', edge: q.main ? 'rgba(232,200,116,0.4)' : COL.edge2 });
        ui.text((q.main ? '★ ' : '') + (q.title || '依頼'), f.cx + 12, ry + 22, { size: 15, col: q.main ? COL.gold : COL.ink, weight: 700 });
        ui.paragraph(q.desc || '', f.cx + 12, ry + 44, f.cw - 120, { size: 11, col: COL.ink2, lh: 15, weight: 500 });
        if (q.count > 1) ui.text(`${q.progress || 0}/${q.count}`, f.cx + f.cw - 14, ry + 22, { size: 14, align: 'right', col: COL.ink });
        if (q.state === 'complete') ui.text('達成', f.cx + f.cw - 14, ry + 46, { size: 12, align: 'right', col: COL.good });
        if (q.reward) {
          ui.text(`報酬 ${q.reward.gold || 0}G / ${q.reward.xp || 0}EXP`, f.cx + 12, ry + r.h - 22, { size: 11, col: COL.gold });
        }
        if (this.tab === 0 && q.state !== 'complete') {
          const tracked = q.id === g.quests.trackedId;
          if (ui.button(f.cx + f.cw - 92, ry + r.h - 40, 80, 28, tracked ? '追跡中' : '追跡', {
            size: 11, primary: tracked, selected: tracked, r: 8,
          }) && !tracked) {
            g.quests.track(q.id);
            g.toast(`追跡: ${q.title}`, '#fff1a8');
            g.save(true);
          }
        }
      }
      ry += r.h;
    }
    ui.endScroll('q' + this.tab);
    if (!list.length) ui.text(this.tab === 0 ? 'クエストはありません' : 'まだ達成していません', f.cx + f.cw / 2, y + 40, { size: 14, align: 'center', col: COL.dim });
  }

  // ————————————————————————————————————————————————— dialogue

  drawDialogue(ctx, g) {
    const ui = this.ui;
    const npc = this.data.npc;
    if (!npc) { this.close(); return; }
    const w = Math.min(ui.w - 20, 620);
    const x = (ui.w - w) / 2;
    const opts = this.data.options || dlgOptions(npc, g);
    const bodyLines = ui.wrap(this.data.text || '', w - 110, 15, 500);
    const h = 130 + bodyLines.length * 22 + opts.length * 48;
    const y = ui.h - h - 16;
    ui.panel(x, y, w, h, { r: 16, fill: 'rgba(22,19,17,0.97)' });
    // portrait
    ctx.save();
    ctx.beginPath(); roundRect(ctx, x + 14, y + 14, 72, 72, 10); ctx.clip();
    ctx.fillStyle = 'rgba(50,44,38,0.9)';
    ctx.fillRect(x + 14, y + 14, 72, 72);
    g.drawNPCPortrait(ctx, npc, x + 50, y + 84, 1.55);
    ctx.restore();
    ctx.strokeStyle = COL.edge; ctx.lineWidth = 1.4;
    roundRect(ctx, x + 14, y + 14, 72, 72, 10); ctx.stroke();

    ui.text(npc.name, x + 100, y + 30, { size: 17, col: COL.gold, weight: 800 });
    ui.text(npc.roleLabel + (npc.settlement ? ` ・ ${npc.settlement.name}` : ''), x + 100, y + 50, { size: 11, col: COL.ink2 });
    let ty = y + 78;
    for (const line of bodyLines) { ui.text(line, x + 20, ty, { size: 15, col: COL.ink, weight: 500 }); ty += 22; }
    ty += 10;
    for (const o of opts) {
      if (ui.button(x + 16, ty, w - 32, 42, o.label, { size: 14, align: 'left', primary: o.act === 'turnin' })) {
        g.dialogueAction(npc, o);
      }
      ty += 48;
    }
  }

  // ————————————————————————————————————————————————— shop

  drawShop(ctx, g) {
    const ui = this.ui;
    const p = g.player;
    const f = this.frame(ctx, this.data.shopName || '商店', { w: 600, h: 760 });
    if (f.closed) { this.close(); return; }
    let y = this.tabs(ctx, f, ['買う', '売る']);
    ui.text(`所持金 ${p.gold} G`, f.cx + f.cw, y - 52, { size: 13, align: 'right', col: COL.gold });

    const listH = f.y + f.h - y - (this.sel ? 210 : 24);
    const rowH = 56;
    if (this.tab === 0) {
      const stock = this.data.stock || [];
      const off = ui.beginScroll('shopBuy', f.cx, y, f.cw, listH, stock.length * rowH + 8);
      let ry = y - off + 4;
      for (const s of stock) {
        const item = s.equip || ITEMS[s.id];
        if (!item) continue;
        const price = g.buyPrice(item);
        if (ry > y - rowH && ry < y + listH) {
          this.itemRow(ctx, g, { item: s.equip, id: s.id, n: 1, _shop: s }, f.cx, ry, f.cw, rowH, { price });
        }
        ry += rowH;
      }
      ui.endScroll('shopBuy');
    } else {
      const sellable = p.inv.filter((e) => {
        const it = e.item || ITEMS[e.id];
        return it && it.type !== 'quest' && p.equip[it.slot] !== e.item;
      });
      const off = ui.beginScroll('shopSell', f.cx, y, f.cw, listH, sellable.length * rowH + 8);
      let ry = y - off + 4;
      for (const e of sellable) {
        const it = e.item || ITEMS[e.id];
        if (ry > y - rowH && ry < y + listH) this.itemRow(ctx, g, e, f.cx, ry, f.cw, rowH, { price: g.sellPrice(it) });
        ry += rowH;
      }
      ui.endScroll('shopSell');
      if (!sellable.length) ui.text('売れるものがない', f.cx + f.cw / 2, y + 40, { size: 14, align: 'center', col: COL.dim });
    }

    // detail + buy/sell action
    if (this.sel) {
      const it = this.sel.item || ITEMS[this.sel.id];
      if (!it) { this.sel = null; return; }
      const dh = 196;
      const dy = f.y + f.h - dh - 14;
      ui.panel(f.cx, dy, f.cw, dh, { r: 14, fill: 'rgba(30,26,23,0.98)' });
      const ic = this.sel.item ? iconFor(this.sel.item) : getIcon(it.icon, it.color);
      ctx.drawImage(ic, f.cx + 12, dy + 12, 48, 48);
      ui.text(displayName(it), f.cx + 70, dy + 24, { size: 16, col: this.sel.item ? rarityColor(this.sel.item) : COL.ink, weight: 800 });
      if (this.sel.item) {
        const s = effStats(this.sel.item);
        const cur = p.equip[this.sel.item.slot];
        const cs = cur ? effStats(cur) : {};
        let sx = f.cx + 12, sy = dy + 76;
        for (const k in s) {
          if (!s[k]) continue;
          const diff = (s[k] || 0) - (cs[k] || 0);
          const val = k === 'crit' || k === 'leech' ? (s[k] * 100).toFixed(1) + '%' : '+' + s[k];
          ui.text(`${STAT_LABEL[k] || k} ${val}`, sx, sy, { size: 12, col: diff > 0 ? COL.good : diff < 0 ? COL.bad : COL.ink });
          sx += 96;
          if (sx > f.cx + f.cw - 90) { sx = f.cx + 12; sy += 18; }
        }
        if (cur && cur !== this.sel.item) ui.text(`現在: ${displayName(cur)}`, f.cx + 70, dy + 46, { size: 11, col: COL.ink2 });
      } else {
        ui.paragraph(it.desc || '', f.cx + 70, dy + 46, f.cw - 90, { size: 12, col: COL.ink2, lh: 17 });
      }
      const by = dy + dh - 50;
      if (this.tab === 0) {
        const price = g.buyPrice(it);
        const can = p.gold >= price;
        if (ui.button(f.cx + 8, by, f.cw / 2 - 14, 40, `買う (${price}G)`, { primary: can, disabled: !can })) {
          g.buyItem(this.sel.entry ? this.sel.entry._shop : this.sel._shop);
        }
      } else {
        const price = g.sellPrice(it);
        if (ui.button(f.cx + 8, by, f.cw / 2 - 14, 40, `売る (${price}G)`, { primary: true })) {
          g.sellItem(this.sel.entry);
          this.sel = null;
          return;
        }
      }
      if (ui.button(f.cx + f.cw / 2 + 6, by, f.cw / 2 - 14, 40, '閉じる')) this.sel = null;
    }
  }

  // ————————————————————————————————————————————————— crafting

  drawCraft(ctx, g) {
    const ui = this.ui;
    const p = g.player;
    const station = this.data.station || 'any';
    const f = this.frame(ctx, station === 'fire' ? '焚き火で調理' : '調合', { w: 560, h: 700 });
    if (f.closed) { this.close(); return; }
    let y = f.cy + 6;
    const list = RECIPES.filter((r) => r.station === 'any' || r.station === station || (station === 'alchemist' && r.station === 'any'));
    const rowH = 84;
    const listH = f.y + f.h - y - 20;
    const off = ui.beginScroll('craft', f.cx, y, f.cw, listH, list.length * rowH + 8);
    let ry = y - off + 4;
    for (const r of list) {
      const out = ITEMS[r.out];
      if (ry > y - rowH && ry < y + listH) {
        let can = true;
        for (const k in r.in) if (g.countItem(k) < r.in[k]) can = false;
        ui.panel(f.cx, ry, f.cw, rowH - 8, { r: 10, fill: 'rgba(36,31,28,0.85)', edge: can ? 'rgba(160,220,160,0.3)' : COL.edge2 });
        ctx.drawImage(getIcon(out.icon, out.color), f.cx + 10, ry + 14, 44, 44);
        ui.text(out.name, f.cx + 64, ry + 24, { size: 15, weight: 700, col: can ? COL.ink : COL.dim });
        const mats = Object.keys(r.in).map((k) => `${ITEMS[k].name} ${g.countItem(k)}/${r.in[k]}`).join('  ');
        ui.text(mats, f.cx + 64, ry + 46, { size: 11, col: can ? COL.ink2 : COL.bad });
        if (ui.button(f.cx + f.cw - 92, ry + 20, 80, 36, '作る', { primary: can, disabled: !can, size: 13 })) {
          g.craft(r);
        }
      }
      ry += rowH;
    }
    ui.endScroll('craft');
  }

  // ————————————————————————————————————————————————— upgrade

  drawUpgrade(ctx, g) {
    const ui = this.ui;
    const p = g.player;
    const f = this.frame(ctx, '武具の強化', { w: 560, h: 700 });
    if (f.closed) { this.close(); return; }
    let y = f.cy + 6;
    ui.text(`所持金 ${p.gold} G`, f.cx + f.cw, y - 40, { size: 13, align: 'right', col: COL.gold });
    const list = p.inv.filter((e) => e.item);
    const rowH = 56;
    const listH = f.y + f.h - y - (this.sel ? 200 : 20);
    const off = ui.beginScroll('upg', f.cx, y, f.cw, listH, list.length * rowH + 8);
    let ry = y - off + 4;
    for (const e of list) {
      if (ry > y - rowH && ry < y + listH) this.itemRow(ctx, g, e, f.cx, ry, f.cw, rowH);
      ry += rowH;
    }
    ui.endScroll('upg');
    if (this.sel && this.sel.item) {
      const it = this.sel.item;
      const cost = upgradeCost(it);
      const dh = 186;
      const dy = f.y + f.h - dh - 14;
      ui.panel(f.cx, dy, f.cw, dh, { r: 14, fill: 'rgba(30,26,23,0.98)' });
      ui.text(displayName(it) + ' → +' + ((it.plus || 0) + 1), f.cx + 14, dy + 26, { size: 16, col: rarityColor(it), weight: 800 });
      const cur = effStats(it);
      const next = effStats({ ...it, plus: (it.plus || 0) + 1 });
      let sx = f.cx + 14, sy = dy + 56;
      for (const k in cur) {
        if (!cur[k]) continue;
        const a = k === 'crit' || k === 'leech' ? (cur[k] * 100).toFixed(1) : cur[k];
        const b = k === 'crit' || k === 'leech' ? (next[k] * 100).toFixed(1) : next[k];
        ui.text(`${STAT_LABEL[k] || k} ${a} → ${b}`, sx, sy, { size: 12, col: COL.good });
        sx += 120;
        if (sx > f.cx + f.cw - 110) { sx = f.cx + 14; sy += 20; }
      }
      const matStr = Object.keys(cost.mats).map((k) => `${ITEMS[k].name} ${g.countItem(k)}/${cost.mats[k]}`).join('  ');
      ui.text(`費用 ${cost.gold}G   ${matStr}`, f.cx + 14, dy + 112, { size: 12, col: COL.ink2 });
      let can = p.gold >= cost.gold;
      for (const k in cost.mats) if (g.countItem(k) < cost.mats[k]) can = false;
      if (ui.button(f.cx + 14, dy + dh - 50, f.cw / 2 - 20, 40, '強化する', { primary: can, disabled: !can })) g.upgrade(it);
      if (ui.button(f.cx + f.cw / 2 + 6, dy + dh - 50, f.cw / 2 - 20, 40, '閉じる')) this.sel = null;
    }
  }

  // ————————————————————————————————————————————————— quest board

  drawBoard(ctx, g) {
    const ui = this.ui;
    const f = this.frame(ctx, '依頼掲示板', { w: 560, h: 700 });
    if (f.closed) { this.close(); return; }
    let y = f.cy + 6;
    const list = this.data.board || [];
    const rowH = 110;
    const listH = f.y + f.h - y - 20;
    const off = ui.beginScroll('board', f.cx, y, f.cw, listH, list.length * rowH + 8);
    let ry = y - off + 4;
    for (const q of list) {
      const taken = g.quests.isTaken(q.id);
      if (ry > y - rowH && ry < y + listH) {
        ui.panel(f.cx, ry, f.cw, rowH - 8, { r: 10, fill: 'rgba(36,31,28,0.85)' });
        ui.text(q.title, f.cx + 12, ry + 22, { size: 15, weight: 700, col: taken ? COL.dim : COL.ink });
        ui.paragraph(q.desc, f.cx + 12, ry + 44, f.cw - 130, { size: 11, col: COL.ink2, lh: 15, weight: 500 });
        ui.text(`推奨Lv ${q.level}`, f.cx + f.cw - 14, ry + 22, { size: 12, align: 'right', col: COL.ink2 });
        ui.text(`報酬 ${q.reward.gold}G / ${q.reward.xp}EXP`, f.cx + 12, ry + rowH - 26, { size: 11, col: COL.gold });
        if (ui.button(f.cx + f.cw - 104, ry + rowH - 44, 92, 34, taken ? '受注済' : '受ける', { primary: !taken, disabled: taken, size: 13 })) {
          g.quests.addSide(q);
        }
      }
      ry += rowH;
    }
    ui.endScroll('board');
    if (!list.length) ui.text('今は依頼がない', f.cx + f.cw / 2, y + 40, { size: 14, align: 'center', col: COL.dim });
  }

  // ————————————————————————————————————————————————— settings

  drawSettings(ctx, g) {
    const ui = this.ui;
    const f = this.frame(ctx, '設定', { w: 480, h: 700 });
    if (f.closed) { this.close(); return; }
    let y = f.cy + 16;
    const slider = (label, val, setter) => {
      ui.text(label, f.cx, y, { size: 14 });
      ui.text(Math.round(val * 100) + '%', f.cx + f.cw, y, { size: 13, align: 'right', col: COL.ink2 });
      const bx = f.cx, bw = f.cw, byy = y + 22;
      ui.bar(bx, byy, bw, 12, val, COL.gold);
      const inp = ui.input;
      const inside = inp.ui.x > bx - 10 && inp.ui.x < bx + bw + 10 && inp.ui.y > byy - 16 && inp.ui.y < byy + 28;
      if (inside && inp.ui.down) setter(clamp((inp.ui.x - bx) / bw, 0, 1));
      // knob
      ctx.save();
      ctx.beginPath(); ctx.arc(bx + bw * val, byy + 6, 9, 0, TAU);
      ctx.fillStyle = '#f0e4c8'; ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,0.4)'; ctx.lineWidth = 1.2; ctx.stroke();
      ctx.restore();
      y += 52;
    };
    slider('音楽', g.settings.music, (v) => { g.settings.music = v; audio.setMusicVol(v); });
    slider('効果音', g.settings.sfx, (v) => { g.settings.sfx = v; audio.setSfxVol(v); });
    const toggle = (label, val, setter) => {
      ui.text(label, f.cx, y + 18, { size: 14 });
      if (ui.button(f.cx + f.cw - 96, y, 96, 38, val ? 'ON' : 'OFF', { primary: val, size: 14 })) setter(!val);
      y += 42;
    };
    toggle('振動', g.settings.vibrate, (v) => { g.settings.vibrate = v; });
    toggle('高画質', g.settings.hq, (v) => { g.settings.hq = v; g.applyQuality(); });
    toggle('照準アシスト', g.settings.aimAssist, (v) => { g.settings.aimAssist = v; });
    toggle('左利き操作', g.settings.leftHanded, (v) => { g.settings.leftHanded = v; });
    toggle('画面の揺れを軽減', g.settings.reduceMotion, (v) => { g.settings.reduceMotion = v; });
    toggle('FPS表示', g.settings.showFps, (v) => { g.settings.showFps = v; });
    y += 4;
    ui.header('操作方法', f.cx, y, f.cw);
    y += 24;
    const moveSide = g.settings.leftHanded ? '右側' : '左側';
    const help = [
      `移動: 画面${moveSide}をドラッグ / WASD`,
      '攻撃: 剣ボタン / J・クリック',
      '回避: 円矢印 / Shift　防御: 盾 / K',
      '調べる: ▲ / E　道具: 左下 / Q',
      '魔法: 右側のボタン / 1〜4　メニュー: ≡ / Esc',
    ];
    for (const h of help) { ui.text(h, f.cx, y, { size: 11, col: COL.ink2, weight: 500 }); y += 18; }
    if (ui.input.ui.released) g.saveSettings();
    if (ui.button(f.cx, f.y + f.h - 60, f.cw, 44, '閉じる')) this.close();
  }

  // ————————————————————————————————————————————————— death

  drawDeath(ctx, g) {
    const ui = this.ui;
    const w = ui.w, h = ui.h;
    ctx.save();
    ctx.fillStyle = 'rgba(40,0,0,0.35)';
    ctx.fillRect(0, 0, w, h);
    ctx.restore();
    ui.text('倒れた……', w / 2, h * 0.36, { size: 34, align: 'center', col: '#e0524a', weight: 800 });
    ui.text(`所持金の一部を失った。`, w / 2, h * 0.36 + 40, { size: 14, align: 'center', col: COL.ink2 });
    const bw = Math.min(280, w - 60);
    if (ui.button((w - bw) / 2, h * 0.52, bw, 52, '最後の祠で目覚める', { primary: true, size: 17, big: true })) {
      g.respawn();
      this.closeAll();
    }
    if (ui.button((w - bw) / 2, h * 0.52 + 64, bw, 44, 'タイトルへ', { size: 15 })) {
      // Persist a real respawn instead of a zero-HP save that could reload at
      // the defeat location with 1 HP.
      g.respawn();
      this.closeAll();
      this.open('title');
    }
  }

  drawVictory(ctx, g) {
    const ui = this.ui;
    const w = ui.w, h = ui.h;
    ctx.save();
    const bg = ctx.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, 'rgba(16,28,48,0.95)');
    bg.addColorStop(0.55, 'rgba(68,47,47,0.94)');
    bg.addColorStop(1, 'rgba(12,10,18,0.98)');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'lighter';
    radial(ctx, w / 2, h * 0.29, Math.min(w, h) * 0.48, 'rgba(255,210,125,0.28)', 'rgba(255,170,90,0)');
    ctx.restore();

    const compact = h < 500;
    const top = compact ? 28 : Math.max(54, h * 0.12);
    ui.text('LEGEND COMPLETE', w / 2, top, { size: 12, align: 'center', col: '#ffe7a8', weight: 800 });
    ui.text('アエテリアに朝が還った', w / 2, top + (compact ? 30 : 42), { size: Math.min(compact ? 23 : 28, w * 0.072), align: 'center', col: COL.gold, weight: 900 });
    ui.text('灰燼竜ヴォルガを討ち、大陸の物語を完結した。', w / 2, top + (compact ? 55 : 76), { size: compact ? 10 : 12, align: 'center', col: COL.ink2, weight: 600 });

    const pw = Math.min(340, w - 42), ph = compact ? 116 : 150;
    const px = (w - pw) / 2, py = compact ? 94 : Math.min(top + 110, h * 0.36);
    ui.panel(px, py, pw, ph, { r: 16, fill: 'rgba(24,21,22,0.82)', edge: 'rgba(255,221,140,0.5)' });
    const discovered = g.world.pois.filter((p) => p.discovered).length;
    const rows = [
      ['到達レベル', `Lv ${g.player.level}`],
      ['討伐数', `${g.player.kills}`],
      ['探索', `${discovered} / ${g.world.pois.length}`],
      ['実績', `${g.achievements.size} / ${ACHIEVEMENTS.length}`],
      ['プレイ時間', `${Math.floor(g.player.playtime / 3600)}時間 ${Math.floor(g.player.playtime / 60) % 60}分`],
    ];
    let y = py + (compact ? 15 : 23);
    for (const [label, value] of rows) {
      ui.text(label, px + 16, y, { size: 12, col: COL.ink2 });
      ui.text(value, px + pw - 16, y, { size: 12, align: 'right', col: COL.ink, weight: 800 });
      y += compact ? 20 : 25;
    }

    const bw = Math.min(300, w - 60);
    const bx = (w - bw) / 2;
    const by = compact ? py + ph + 10 : Math.min(h - 126, py + ph + 28);
    const primaryH = compact ? 40 : 50;
    if (ui.button(bx, by, bw, primaryH, 'この世界を旅し続ける', { primary: true, size: compact ? 14 : 16, big: true })) {
      this.closeAll();
      g.save(true);
    }
    if (ui.button(bx, by + (compact ? 48 : 62), bw, compact ? 36 : 44, 'タイトルへ', { size: compact ? 13 : 14 })) {
      g.save(true);
      this.closeAll();
      this.open('title');
    }
  }
}
