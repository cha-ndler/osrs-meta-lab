/**
 * Batched headless bridge to the OSRS Wiki's own DPS calculator.
 *
 * Reads a JSON request on stdin, writes a JSON response on stdout. One process
 * handles thousands of loadouts, because spawning a process per evaluation
 * would dominate the search runtime.
 *
 * Deliberately avoids `@/state`, which pulls in React, localforage and
 * react-toastify for the web UI. `emptyPlayer()` below mirrors that module's
 * `generateEmptyPlayer` so the import graph stays confined to calculation code.
 *
 * The calculator is GPL-3.0, by Weird Gloop and the OSRS Wiki, and is used
 * unmodified via the pinned submodule in vendor/.
 */

import { createInterface } from 'node:readline';
import * as PlayerVsNPCCalcModule from '@/lib/PlayerVsNPCCalc';
import { getMonsters, INITIAL_MONSTER_INPUTS } from '@/lib/Monsters';
import {
  AmmoApplicability, ammoApplicability, calculateAttackSpeed,
  calculateEquipmentBonusesFromGear,
} from '@/lib/Equipment';
import { getCombatStylesForCategory, PotionMap } from '@/utils';
import { EquipmentCategory } from '@/enums/EquipmentCategory';
import { Prayer } from '@/enums/Prayer';
import PotionEnum from '@/enums/Potion';
import { DEFAULT_ATTACK_SPEED } from '@/lib/constants';
import { spells } from '@/types/Spell';
import type { Monster } from '@/types/Monster';
import type { EquipmentPiece, Player, PlayerEquipment, PlayerSkills } from '@/types/Player';
import type { PlayerCombatStyle } from '@/types/PlayerCombatStyle';
import equipmentJson from '../vendor/osrs-dps-calc/cdn/json/equipment.json';

/**
 * Unwrap CommonJS default exports.
 *
 * tsx's ESM loader wraps the calculator's default export twice, so the class
 * arrives at `module.default.default`. Unwrapping until the expected type
 * appears keeps this working whichever way the loader resolves it.
 */
function unwrapDefault<T>(mod: unknown, want: 'function' | 'object' = 'function'): T {
  let cur = mod;
  for (let i = 0; i < 4; i += 1) {
    if (typeof cur === want && !(want === 'object' && cur && 'default' in (cur as object))) {
      return cur as T;
    }
    const next = (cur as { default?: unknown } | null)?.default;
    if (next === undefined) break;
    cur = next;
  }
  return cur as T;
}

type CalcInstance = {
  getDps(): number;
  getMaxAttackRoll(): number;
  getHitChance(): number;
  getNPCDefenceRoll(): number;
  getDistribution(): { getMax(): number };
  userIssues: { type: string; message: string }[];
};

const PlayerVsNPCCalc = unwrapDefault<new (p: Player, m: Monster, o?: object) => CalcInstance>(
  PlayerVsNPCCalcModule,
);

const EQUIPMENT = unwrapDefault<EquipmentPiece[]>(equipmentJson, 'object');

const byId = new Map<number, EquipmentPiece>();
const byName = new Map<string, EquipmentPiece>();
for (const piece of EQUIPMENT) {
  if (!byId.has(piece.id)) byId.set(piece.id, piece);
  const key = `${piece.name}|${piece.version ?? ''}`;
  if (!byName.has(key)) byName.set(key, piece);
  if (!byName.has(piece.name)) byName.set(piece.name, piece);
}

const MONSTERS = getMonsters();

const POTION = unwrapDefault<Record<string, unknown>>(PotionEnum, 'object');

/**
 * Resolve a name onto a numeric enum member.
 *
 * TypeScript numeric enums compile to a two-way map (`{ PIETY: 13, 13: 'PIETY' }`),
 * so a name lookup that yields a number is the member. Callers pass names rather
 * than raw indices because `PrayerMap` is keyed by the numeric value: handing the
 * calculator a string produces `undefined` and it dies several frames later
 * reading `.combatStyle` off it, which is a miserable way to learn you typo'd a
 * prayer.
 */
function enumValue(source: Record<string, unknown>, name: string): number | null {
  const direct = source[name];
  if (typeof direct === 'number') return direct;
  const upper = source[name.toUpperCase().replace(/[\s-]+/g, '_')];
  return typeof upper === 'number' ? upper : null;
}

function resolvePrayers(names: (string | number)[], missing: string[]): Prayer[] {
  const out: Prayer[] = [];
  for (const ref of names) {
    if (typeof ref === 'number') { out.push(ref as Prayer); continue; }
    const value = enumValue(Prayer as unknown as Record<string, unknown>, ref);
    if (value === null) missing.push(`prayer:${ref}`);
    else out.push(value as Prayer);
  }
  return out;
}

/**
 * Apply potion boosts to a player's skills.
 *
 * Mirrors `recomputeBoosts` in the calculator's `state.tsx`, which lives in the
 * UI layer rather than the calc - the calc only ever reads `player.boosts`.
 * Boosts take the *highest* value per skill rather than summing, so stacking
 * super attack under a super combat does not double-count.
 */
function applyPotions(player: Player, names: (string | number)[], missing: string[]): void {
  const boosts: Record<string, number> = {
    atk: 0, def: 0, magic: 0, prayer: 0, ranged: 0, str: 0, mining: 0, herblore: 0,
  };
  const chosen: number[] = [];
  for (const ref of names) {
    const value = typeof ref === 'number' ? ref : enumValue(POTION, ref);
    if (value === null || !(value in PotionMap)) { missing.push(`potion:${ref}`); continue; }
    chosen.push(value);
    const result = PotionMap[value as keyof typeof PotionMap]
      .calculateFn(player.skills as PlayerSkills) as Record<string, number>;
    for (const [skill, amount] of Object.entries(result)) {
      if (amount > (boosts[skill] ?? 0)) boosts[skill] = amount;
    }
  }
  Object.assign(player.boosts, boosts);
  (player.buffs as unknown as { potions: number[] }).potions = chosen;
}

const SLOTS: (keyof PlayerEquipment)[] = [
  'head', 'cape', 'neck', 'ammo', 'weapon', 'body',
  'shield', 'legs', 'hands', 'feet', 'ring',
];

function emptyEquipment(): PlayerEquipment {
  const eq = {} as PlayerEquipment;
  for (const slot of SLOTS) eq[slot] = null;
  return eq;
}

/** Mirror of state.tsx `generateEmptyPlayer`, minus the UI dependencies. */
function emptyPlayer(): Player {
  return {
    name: 'oracle',
    style: getCombatStylesForCategory(EquipmentCategory.NONE)[0],
    skills: {
      atk: 99, def: 99, hp: 99, magic: 99, prayer: 99,
      ranged: 99, str: 99, mining: 99, herblore: 99,
    },
    boosts: {
      atk: 0, def: 0, hp: 0, magic: 0, prayer: 0,
      ranged: 0, str: 0, mining: 0, herblore: 0,
    },
    equipment: emptyEquipment(),
    attackSpeed: DEFAULT_ATTACK_SPEED,
    prayers: [],
    bonuses: { str: 0, ranged_str: 0, magic_str: 0, prayer: 0 },
    defensive: { stab: 0, slash: 0, crush: 0, magic: 0, ranged: 0 },
    offensive: { stab: 0, slash: 0, crush: 0, magic: 0, ranged: 0 },
    buffs: {
      potions: [],
      onSlayerTask: false,
      inWilderness: false,
      kandarinDiary: true,
      chargeSpell: false,
      markOfDarknessSpell: false,
      forinthrySurge: false,
      soulreaperStacks: 0,
      baAttackerLevel: 0,
      chinchompaDistance: 4,
      usingSunfireRunes: false,
    },
    spell: null,
  } as unknown as Player;
}

function findMonster(
  name: string,
  version?: string,
  inputs?: Record<string, unknown>,
): Monster {
  const match = MONSTERS.find(
    (m) => m.name === name && (!version || m.version === version),
  ) ?? MONSTERS.find((m) => m.name === name);
  if (!match) throw new Error(`monster not found: ${name}`);
  // `defenceReductions` is a nested object, so a shallow merge would drop every
  // reduction the caller did not restate. Merge that level explicitly.
  const { defenceReductions, ...rest } = (inputs ?? {}) as Record<string, unknown>;
  const monster = {
    ...match,
    inputs: {
      ...INITIAL_MONSTER_INPUTS,
      ...rest,
      defenceReductions: {
        ...INITIAL_MONSTER_INPUTS.defenceReductions,
        ...(defenceReductions as object ?? {}),
      },
    },
  } as unknown as Monster;
  monster.inputs.monsterCurrentHp = (inputs?.monsterCurrentHp as number)
    || monster.skills.hp;
  return monster;
}

function resolvePiece(ref: number | string): EquipmentPiece | null {
  if (ref === null || ref === undefined) return null;
  if (typeof ref === 'number') return byId.get(ref) ?? null;
  return byName.get(ref) ?? null;
}

interface LoadoutRequest {
  /** Slot -> item id or exact name. Unknown ids are skipped, not guessed. */
  gear: Record<string, number | string>;
  /** Index into the weapon category's styles; defaults to the first. */
  styleIndex?: number;
  /** Pick the style by type ("magic", "ranged", "slash", ...) instead of index. */
  styleType?: string;
  spell?: string;
  /** Prayer names ("PIETY") or raw enum values. Unknown names land in `missing`. */
  prayers?: (string | number)[];
  /** Potion names ("SUPER_COMBAT") or raw enum values. */
  potions?: (string | number)[];
  buffs?: Record<string, unknown>;
  skills?: Record<string, number>;
  boosts?: Record<string, number>;
}

interface Request {
  /** Emit the static style and ammo tables instead of scoring anything. */
  dump?: 'meta';
  monster: string;
  version?: string;
  /**
   * Prayers and potions applied to every loadout in the batch, so a scan of a
   * thousand weapons does not repeat the same combat profile a thousand times.
   * A loadout that names its own takes precedence.
   */
  profile?: { prayers?: (string | number)[]; potions?: (string | number)[] };
  /** Encounter settings: ToA invocation, party size, defence reductions, ... */
  inputs?: Record<string, unknown>;
  loadouts: LoadoutRequest[];
}

/**
 * Choose which combat style to attack with.
 *
 * Selecting by index is a trap: a style's position is a per-category accident.
 * A staff lists three melee options before its spellcasting one, so a search
 * that assumes index 0 scores Ice Barrage as a club swing and concludes magic
 * is worthless. `styleType` asks for what was actually meant. An unsatisfiable
 * request is recorded rather than quietly falling back, because falling back is
 * how that bug hid in the first place.
 */
function pickStyle(
  styles: PlayerCombatStyle[],
  req: LoadoutRequest,
  missing: string[],
): PlayerCombatStyle {
  if (req.styleType) {
    const want = req.styleType.toLowerCase();
    const match = styles.find((s) => (s.type ?? '').toLowerCase() === want);
    if (match) return match;
    missing.push(`styleType:${req.styleType}`);
  }
  return styles[Math.min(req.styleIndex ?? 0, styles.length - 1)];
}

function score(monster: Monster, req: LoadoutRequest) {
  const player = emptyPlayer();
  const missing: string[] = [];

  for (const [slot, ref] of Object.entries(req.gear ?? {})) {
    if (!SLOTS.includes(slot as keyof PlayerEquipment)) continue;
    const piece = resolvePiece(ref);
    if (!piece) {
      missing.push(`${slot}:${ref}`);
      continue;
    }
    // A two-hander occupies the shield slot; leaving a shield on would inflate
    // the result with bonuses the game would not grant.
    if (slot === 'weapon' && piece.isTwoHanded) player.equipment.shield = null;
    (player.equipment as Record<string, EquipmentPiece | null>)[slot] = piece;
  }

  if (req.skills) Object.assign(player.skills, req.skills);
  // Potions derive from skills, so they land after any skill override and before
  // an explicit boosts override, which stays the final word.
  if (req.potions) applyPotions(player, req.potions, missing);
  if (req.boosts) Object.assign(player.boosts, req.boosts);
  if (req.buffs) Object.assign(player.buffs, req.buffs);
  if (req.prayers) player.prayers = resolvePrayers(req.prayers, missing);
  if (req.spell) {
    const found = spells.find((s) => s.name === req.spell);
    if (found) (player as unknown as { spell: unknown }).spell = found;
    else missing.push(`spell:${req.spell}`);
  }

  const weapon = player.equipment.weapon;
  const styles = getCombatStylesForCategory(weapon?.category ?? EquipmentCategory.NONE);
  player.style = pickStyle(styles, req, missing);

  const calculated = calculateEquipmentBonusesFromGear(player, monster);
  player.bonuses = calculated.bonuses;
  player.offensive = calculated.offensive;
  player.defensive = calculated.defensive;
  player.attackSpeed = calculateAttackSpeed(player, monster);

  const calc = new PlayerVsNPCCalc(player, monster, { loadoutName: 'oracle' });
  const dps = calc.getDps();
  // The calculator raises issues for loadouts it cannot model faithfully - wrong
  // ammo, an unsupported set effect, a spell the monster is immune to. Dropping
  // them would publish "the calculator says 7.4 dps" when what it actually said
  // was "7.4 dps, but I am ignoring your set effect". Wrong ammo scores zero and
  // loses on its own; an unmodelled set effect scores plausibly and does not.
  return {
    dps,
    maxHit: calc.getDistribution().getMax(),
    accuracy: calc.getHitChance(),
    maxAttackRoll: calc.getMaxAttackRoll(),
    npcDefRoll: calc.getNPCDefenceRoll(),
    styleName: player.style?.name ?? null,
    styleType: player.style?.type ?? null,
    attackSpeed: player.attackSpeed,
    issues: (calc.userIssues ?? []).map((issue) => issue.type),
    missing,
  };
}

/**
 * Static tables the Python side needs but cannot derive.
 *
 * Both answer questions about the *rules*, not about a fight, so they are the
 * same for every monster and get cached rather than recomputed per solve.
 *
 * `styles` exists so the search can ask "which of this weapon's styles are
 * slash?" instead of guessing an index. `ammo` exists because a bow paired with
 * bolts silently scores zero: the pairing table upstream is module-private, but
 * `ammoApplicability` is exported, so the map is rebuilt by probing it.
 */
function dumpMeta() {
  const styles: Record<string, unknown> = {};
  for (const category of Object.values(EquipmentCategory)) {
    styles[category as string] = getCombatStylesForCategory(category as EquipmentCategory)
      .map((s, index) => ({ index, name: s.name, type: s.type, stance: s.stance }));
  }

  const ammoSlot = EQUIPMENT.filter((e) => e.slot === 'ammo');
  const ammo: Record<number, number[]> = {};
  for (const weapon of EQUIPMENT) {
    if (weapon.slot !== 'weapon') continue;
    // ALLOWED means "needs no ammo"; only weapons with a real requirement get
    // an entry, so an empty list is meaningfully different from a missing key.
    if (ammoApplicability(weapon.id, undefined) !== AmmoApplicability.INVALID) continue;
    const valid = ammoSlot
      .filter((a) => ammoApplicability(weapon.id, a.id) === AmmoApplicability.INCLUDED)
      .map((a) => a.id);
    ammo[weapon.id] = valid;
  }
  return { styles, ammo };
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

function handle(req: Request): object {
  if (req.dump === 'meta') return dumpMeta();

  const monster = findMonster(req.monster, req.version, req.inputs);
  const results = req.loadouts.map((loadout, i) => {
    const merged = req.profile ? { ...req.profile, ...loadout } : loadout;
    try {
      return { i, ...score(monster, merged) };
    } catch (err) {
      return { i, error: (err as Error).message, dps: 0 };
    }
  });
  return {
    monster: { name: monster.name, version: monster.version, hp: monster.skills.hp },
    results,
  };
}

/**
 * Serve requests over stdin until it closes, one JSON object per line.
 *
 * Coordinate ascent is sequential by construction - the next slot cannot be
 * chosen until the last one is settled - so a solve makes roughly twenty
 * separate calls. Paying Node and tsx startup for each of them cost more than
 * the arithmetic did: a single Vorkath solve spent about forty seconds, nearly
 * all of it launching processes. Holding one process open turns that back into
 * the couple of seconds of work it actually is.
 */
async function serve(): Promise<void> {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let response: object;
    try {
      response = handle(JSON.parse(trimmed) as Request);
    } catch (err) {
      response = { error: (err as Error).message, results: [] };
    }
    process.stdout.write(`${JSON.stringify(response)}\n`);
  }
}

async function main() {
  // One-shot mode stays the default so the bridge is still usable by hand:
  // `echo '{...}' | npm run oracle` behaves as it always did.
  if (process.argv.includes('--serve')) {
    await serve();
    return;
  }
  const raw = await readStdin();
  process.stdout.write(JSON.stringify(handle(JSON.parse(raw) as Request)));
}

main().catch((err) => {
  process.stderr.write(`oracle failed: ${err?.stack ?? err}\n`);
  process.exit(1);
});
