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

import * as PlayerVsNPCCalcModule from '@/lib/PlayerVsNPCCalc';
import { getMonsters, INITIAL_MONSTER_INPUTS } from '@/lib/Monsters';
import { calculateAttackSpeed, calculateEquipmentBonusesFromGear } from '@/lib/Equipment';
import { getCombatStylesForCategory } from '@/utils';
import { EquipmentCategory } from '@/enums/EquipmentCategory';
import { DEFAULT_ATTACK_SPEED } from '@/lib/constants';
import { spells } from '@/types/Spell';
import type { Monster } from '@/types/Monster';
import type { EquipmentPiece, Player, PlayerEquipment } from '@/types/Player';
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

function findMonster(name: string, version?: string): Monster {
  const match = MONSTERS.find(
    (m) => m.name === name && (!version || m.version === version),
  ) ?? MONSTERS.find((m) => m.name === name);
  if (!match) throw new Error(`monster not found: ${name}`);
  const monster = {
    ...match,
    inputs: { ...INITIAL_MONSTER_INPUTS },
  } as unknown as Monster;
  monster.monsterCurrentHp = monster.monsterCurrentHp || monster.skills.hp;
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
  spell?: string;
  prayers?: string[];
  buffs?: Record<string, unknown>;
  skills?: Record<string, number>;
  boosts?: Record<string, number>;
}

interface Request {
  monster: string;
  version?: string;
  loadouts: LoadoutRequest[];
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
  if (req.boosts) Object.assign(player.boosts, req.boosts);
  if (req.buffs) Object.assign(player.buffs, req.buffs);
  if (req.prayers) (player as unknown as { prayers: unknown[] }).prayers = req.prayers;
  if (req.spell) {
    const found = spells.find((s) => s.name === req.spell);
    if (found) (player as unknown as { spell: unknown }).spell = found;
    else missing.push(`spell:${req.spell}`);
  }

  const weapon = player.equipment.weapon;
  const styles = getCombatStylesForCategory(weapon?.category ?? EquipmentCategory.NONE);
  player.style = styles[Math.min(req.styleIndex ?? 0, styles.length - 1)];

  const calculated = calculateEquipmentBonusesFromGear(player, monster);
  player.bonuses = calculated.bonuses;
  player.offensive = calculated.offensive;
  player.defensive = calculated.defensive;
  player.attackSpeed = calculateAttackSpeed(player, monster);

  const calc = new PlayerVsNPCCalc(player, monster, { loadoutName: 'oracle' });
  return {
    dps: calc.getDps(),
    maxHit: calc.getDistribution().getMax(),
    accuracy: calc.getHitChance(),
    maxAttackRoll: calc.getMaxAttackRoll(),
    npcDefRoll: calc.getNPCDefenceRoll(),
    styleName: player.style?.name ?? null,
    styleType: player.style?.type ?? null,
    attackSpeed: player.attackSpeed,
    missing,
  };
}

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

async function main() {
  const raw = await readStdin();
  const req = JSON.parse(raw) as Request;
  const monster = findMonster(req.monster, req.version);

  const results = req.loadouts.map((loadout, i) => {
    try {
      return { i, ...score(monster, loadout) };
    } catch (err) {
      return { i, error: (err as Error).message, dps: 0 };
    }
  });

  process.stdout.write(JSON.stringify({
    monster: { name: monster.name, version: monster.version, hp: monster.skills.hp },
    results,
  }));
}

main().catch((err) => {
  process.stderr.write(`oracle failed: ${err?.stack ?? err}\n`);
  process.exit(1);
});
