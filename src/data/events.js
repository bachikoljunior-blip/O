// events.js — event type ids and the payload contract for each.
// L1.
//
// The float slots f0..f5 and int slots i0..i1 mean different things per type.
// Documenting them here — rather than in whichever system happens to emit —
// is what keeps the presentation layer readable.

export const EV = {
  NONE: 0,
  /** f0=damage f1..f3=impact normal f4=weight f5=impactSpeed i0=attackIdx i1=flags */
  HIT: 1,
  /** f0..f2=position f4=weight i0=attackIdx */
  SWING: 2,
  /** f0..f2=position f4=weight i0=attackIdx */
  PARRY: 3,
  /** f0..f2=position f4=weight */
  BLOCK: 4,
  /** f0..f2=position f4=weight */
  STANCE_BREAK: 5,
  /** f0..f2=position f4=weight */
  STAGGER: 6,
  /** f0..f2=position f4=weight i0=enemyIdx */
  DEATH: 7,
  /** f0..f2=position f4=distance */
  DODGE: 8,
  /** f0=amount f1..f3=position */
  HEAL: 9,
  /** f0..f2=position f4=speed  — footfall, used for surface-aware sfx */
  STEP: 10,
  /** f0..f2=position i0=surfaceId */
  LAND: 11,
  /** f0=staminaMissing — the "you tried but could not" cue */
  EXHAUSTED: 12,
  /** f0..f2=position i0=projectileKind */
  PROJECTILE_SPAWN: 13,
  /** f0..f2=position i0=projectileKind */
  PROJECTILE_HIT: 14,
  /** f0..f2=position i0=partIdx i1=enemyIdx  — component-tag combat */
  PART_BREAK: 15,
  /** f0=newHp f1=maxHp */
  PLAYER_VITALS: 16,
};

export const EV_NAME = [];
for (const k of Object.keys(EV)) EV_NAME[EV[k]] = k;
