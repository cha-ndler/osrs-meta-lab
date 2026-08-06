"""OSRS combat maths.

Standard formulas from the wiki's DPS documentation. Nothing here is
speculative - if the numbers are wrong the whole lab is wrong, so this file is
the one with the tightest tests.

    effective = floor(level * prayer) + style_bonus + 8
    attack    = effective * (equipment_attack + 64)
    defence   = (target_defence_level + 9) * (target_defence_bonus + 64)
    accuracy  = attack > defence ? 1 - (defence + 2) / (2 * (attack + 1))
                                 : attack / (2 * (defence + 1))
    max hit   = floor(0.5 + effective_str * (str_bonus + 64) / 640)
    dps       = accuracy * (max_hit / 2) / (speed * 0.6)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Attack styles add a small flat bonus to the relevant effective level.
STYLE_ACCURATE = 3
STYLE_AGGRESSIVE = 3
STYLE_CONTROLLED = 1
STYLE_NONE = 0

# Prayer multipliers for the top-tier prayers, which is what BiS assumes.
PRAYER_PIETY_ATT = 1.20
PRAYER_PIETY_STR = 1.23
PRAYER_RIGOUR_ATT = 1.20
PRAYER_RIGOUR_STR = 1.23
PRAYER_AUGURY_ATT = 1.25
PRAYER_AUGURY_DMG = 1.00


@dataclass
class Player:
    attack: int = 99
    strength: int = 99
    defence: int = 99
    ranged: int = 99
    magic: int = 99
    # Multiplicative boosts from potions, applied before prayer.
    attack_boost: int = 21   # super/divine combat at 99
    strength_boost: int = 21
    ranged_boost: int = 13   # divine ranging at 99
    magic_boost: int = 11    # divine magic / imbued heart equivalent


@dataclass
class Target:
    name: str = ""
    hitpoints: int = 100
    defence_level: int = 100
    defence_bonus: dict[str, float] = field(default_factory=dict)
    size: int = 1
    attributes: list[str] = field(default_factory=list)
    elemental_weakness: str = ""
    elemental_weakness_percent: float = 0.0
    flat_armour: float = 0.0


def effective_level(level: int, boost: int, prayer: float, style_bonus: int) -> int:
    return math.floor(math.floor((level + boost) * prayer)) + style_bonus + 8


def max_hit_melee(eff_strength: int, strength_bonus: float) -> int:
    return math.floor(0.5 + eff_strength * (strength_bonus + 64) / 640)


def max_hit_ranged(eff_ranged: int, ranged_strength: float) -> int:
    return math.floor(0.5 + eff_ranged * (ranged_strength + 64) / 640)


def attack_roll(eff_attack: int, equipment_attack: float) -> float:
    return eff_attack * (equipment_attack + 64)


def defence_roll(target_defence_level: float, target_defence_bonus: float) -> float:
    return (target_defence_level + 9) * (target_defence_bonus + 64)


def accuracy(att_roll: float, def_roll: float) -> float:
    if att_roll > def_roll:
        return 1 - (def_roll + 2) / (2 * (att_roll + 1))
    return att_roll / (2 * (def_roll + 1))


def dps(hit_chance: float, max_hit: int, attack_speed_ticks: float) -> float:
    """Average damage per second. A tick is 0.6s."""
    if attack_speed_ticks <= 0:
        return 0.0
    return hit_chance * (max_hit / 2) / (attack_speed_ticks * 0.6)


def time_to_kill(dps_value: float, hitpoints: int) -> float:
    return hitpoints / dps_value if dps_value > 0 else float("inf")
