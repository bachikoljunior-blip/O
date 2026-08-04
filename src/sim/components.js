// components.js — column definitions and mask bits.
// L2: may import core + data only.

export const MAX_ENTITIES = 512;

/** Component presence bits. A system tests `(mask[e] & NEED) === NEED`. */
export const C = {
  TRANSFORM:  1 << 0,
  VELOCITY:   1 << 1,
  HEALTH:     1 << 2,
  COMBAT:     1 << 3,
  AI:         1 << 4,
  PLAYER:     1 << 5,
  PROJECTILE: 1 << 6,
  PICKUP:     1 << 7,
  NPC:        1 << 8,
  PERSISTENT: 1 << 9,
  GROUNDED:   1 << 10,
};

/** Combat states. Order is stable — saves reference these by number. */
export const S = {
  IDLE: 0,
  WINDUP: 1,
  ACTIVE: 2,
  RECOVERY: 3,
  HURT: 4,
  STAGGER: 5,
  BROKEN: 6,
  DODGE: 7,
  PARRY: 8,
  BLOCK: 9,
  DEAD: 10,
};

export const STATE_NAME = [
  'IDLE', 'WINDUP', 'ACTIVE', 'RECOVERY', 'HURT',
  'STAGGER', 'BROKEN', 'DODGE', 'PARRY', 'BLOCK', 'DEAD',
];

/** Buffered action ids. 0 means "empty slot". */
export const ACT = {
  NONE: 0,
  ATTACK: 1,
  DODGE: 2,
  BLOCK: 3,
  HEAVY: 4,
  INTERACT: 5,
};

export const BUFFER_SLOTS = 4;

/** Damage flags packed into the HIT event's i1 field. */
export const HIT_FLAG = {
  CRIT: 1 << 0,
  BLOCKED: 1 << 1,
  PARRIED: 1 << 2,
  UNDEAD: 1 << 3,
  STANCE_BREAK: 1 << 4,
  STAGGER: 1 << 5,
  KILL: 1 << 6,
  VULNERABLE: 1 << 7,
};
