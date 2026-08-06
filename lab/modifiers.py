"""Weapon and gear effects that decide real metas.

The base formulas in `combat.py` describe an ordinary weapon hitting an ordinary
monster. The weapons people actually argue about are the ones that break those
rules, so they need explicit handling.

**A weapon whose effect is not modelled here is excluded from ranking, not
guessed at.** Silently scoring a Twisted bow as if it were a plain shortbow
would put a wrong answer at the top of a report, which is worse than a gap.
"""

from __future__ import annotations

import math

# Multiplicative gear effects, applied to accuracy and/or damage.
SLAYER_HELM_MULT = 7 / 6          # on-task, melee/ranged/magic with the (i) version
SALVE_E_MULT = 7 / 6              # undead, melee
SALVE_EI_MULT = 1.20              # undead, ranged/magic with (ei)
VOID_MELEE_MULT = 1.10
VOID_RANGED_MULT = 1.10
ELITE_VOID_RANGED_DMG = 1.125
DRAGONBANE_MULT = 1.20            # dragon hunter lance / crossbow accuracy+damage
KERIS_PARTISAN_MULT = 1.33        # kalphites

# Weapons whose damage depends on state the solver does not model. Ranking these
# with the plain formula would be actively misleading.
UNMODELLED = {
    "dharok's greataxe",          # scales with missing hitpoints
    "toxic blowpipe",             # dart-dependent, plus scales/venom
    "blazing blowpipe",
    "rosewood blowpipe",
    "craw's bow",                 # wilderness-only multiplier
    "webweaver bow",
    "thammaron's sceptre",
    "accursed sceptre",
    "ursine chainmace",
    "viggora's chainmace",
    "voidwaker",                  # special-attack weapon, not a main hand
    "dragon claws",
    "elder maul",
    "dragon warhammer",
    "bandos godsword",
    "saradomin godsword",
    "zamorakian hasta",           # depends on the spear bonus vs size
    "crystal halberd",
    "colossal blade",             # scales with target size in a bespoke way
    "gadderhammer",
    "leaf-bladed battleaxe",
}

# Weapons with effects that ARE modelled below.
MODELLED_SPECIAL = {
    "scythe of vitur",
    "twisted bow",
    "tumeken's shadow",
    "osmumten's fang",
    "dragon hunter lance",
    "dragon hunter crossbow",
    "keris partisan",
}


def is_rankable(weapon_name: str) -> bool:
    name = (weapon_name or "").lower()
    return name not in UNMODELLED


def scythe_hits(target_size: int) -> float:
    """Scythe of vitur hits up to three times for 100%, 50%, 25%."""
    if target_size >= 3:
        return 1.0 + 0.5 + 0.25
    if target_size == 2:
        return 1.0 + 0.5
    return 1.0


def twisted_bow_multipliers(target_magic: float) -> tuple[float, float]:
    """(accuracy, damage) multipliers. Capped as in game."""
    magic = min(max(target_magic, 0), 350)
    acc = 140 + ((10 * 3 * magic / 10 - 10) / 100) - (((3 * magic / 10 - 100) ** 2) / 100)
    dmg = 250 + ((10 * 3 * magic / 10 - 14) / 100) - (((3 * magic / 10 - 140) ** 2) / 100)
    return min(max(acc, 0) / 100, 1.40), min(max(dmg, 0) / 100, 2.50)


def fang_accuracy(base_accuracy: float) -> float:
    """Osmumten's fang re-rolls a miss inside a narrowed range."""
    return 1 - (1 - base_accuracy) ** 2


def elemental_bonus(weakness_percent: float, using_matching_spell: bool) -> float:
    """Elemental weakness adds a flat percentage of the target's max HP scale."""
    if not using_matching_spell or not weakness_percent:
        return 1.0
    return 1.0 + (weakness_percent / 100.0)


def apply_damage_modifiers(base_max: int, *, slayer=False, salve=None, void=None,
                           dragonbane=False, keris=False) -> int:
    mult = 1.0
    # Salve and slayer helm do not stack; the game takes the salve.
    if salve == "e":
        mult *= SALVE_E_MULT
    elif salve == "ei":
        mult *= SALVE_EI_MULT
    elif slayer:
        mult *= SLAYER_HELM_MULT
    if void == "elite_ranged":
        mult *= ELITE_VOID_RANGED_DMG
    if dragonbane:
        mult *= DRAGONBANE_MULT
    if keris:
        mult *= KERIS_PARTISAN_MULT
    return math.floor(base_max * mult)
