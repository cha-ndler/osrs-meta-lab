"""Rank gear against a specific monster by DPS.

Approach: for each candidate weapon, fill the remaining slots by coordinate
ascent - repeatedly swap in whichever item raises DPS most, until nothing
improves. Slots are close to additive, so two passes converge.

Weapons whose effects `modifiers` cannot model are skipped and reported rather
than ranked with the wrong formula.
"""

from __future__ import annotations

from dataclasses import dataclass

from combat import (
    PRAYER_PIETY_ATT, PRAYER_PIETY_STR, PRAYER_RIGOUR_ATT, PRAYER_RIGOUR_STR,
    Player, Target, accuracy, attack_roll, defence_roll, dps, effective_level,
    max_hit_melee, max_hit_ranged,
)
from modifiers import is_rankable, scythe_hits, twisted_bow_multipliers, fang_accuracy
from stats import Stats, num

MELEE_SLOTS = ("head", "cape", "neck", "ammo", "body", "legs",
               "shield", "hands", "feet", "ring")
STYLE_ATTACK = {"stab": "stab_attack_bonus", "slash": "slash_attack_bonus",
                "crush": "crush_attack_bonus", "ranged": "range_attack_bonus",
                "magic": "magic_attack_bonus"}
STYLE_DEFENCE = {"stab": "stab_defence_bonus", "slash": "slash_defence_bonus",
                 "crush": "crush_defence_bonus", "ranged": "range_defence_bonus",
                 "magic": "magic_defence_bonus"}

TWO_HANDED = {"2h"}


@dataclass
class Loadout:
    weapon: str
    items: dict[str, str]
    style: str
    dps: float
    max_hit: int
    accuracy: float


class Solver:
    def __init__(self, stats: Stats, player: Player | None = None) -> None:
        self.stats = stats
        self.player = player or Player()
        self._slot_cache: dict[str, list[str]] = {}

    # -- helpers -----------------------------------------------------------

    def slot_items(self, slot: str, style: str = "slash", keep: int = 14) -> list[str]:
        """Shortlist a slot's candidates.

        Unpruned this is ~500 items per slot across ~1000 weapons, which is tens
        of millions of evaluations. DPS rises monotonically with both the
        style's attack bonus and the relevant strength bonus, so the optimum is
        always inside the top of one of those two rankings - taking the union
        keeps the answer while making the search finish.
        """
        key = f"{slot}:{style}:{keep}"
        if key in self._slot_cache:
            return self._slot_cache[key]

        names = self.stats.items_in_slot(slot)
        att_field = STYLE_ATTACK[style]
        str_field = "ranged_strength_bonus" if style == "ranged" else "strength_bonus"

        scored = []
        for name in names:
            row = self.stats.item(name)
            if not row:
                continue
            scored.append((name, num(row.get(att_field)), num(row.get(str_field))))

        top_att = [n for n, _, _ in sorted(scored, key=lambda s: -s[1])[:keep]]
        top_str = [n for n, _, _ in sorted(scored, key=lambda s: -s[2])[:keep]]
        shortlist = list(dict.fromkeys(top_att + top_str))
        self._slot_cache[key] = shortlist
        return shortlist

    def _sum(self, names: dict[str, str], field: str) -> float:
        total = 0.0
        for name in names.values():
            row = self.stats.item(name)
            if row:
                total += num(row.get(field))
        return total

    # -- scoring -----------------------------------------------------------

    def score(self, weapon: str, worn: dict[str, str], target: Target,
              style: str) -> tuple[float, int, float]:
        row = self.stats.item(weapon)
        if not row:
            return 0.0, 0, 0.0
        speed = num(row.get("weapon_attack_speed"), 4) or 4

        full = dict(worn)
        full["weapon"] = weapon
        att_bonus = self._sum(full, STYLE_ATTACK[style])

        if style == "ranged":
            eff_att = effective_level(self.player.ranged, self.player.ranged_boost,
                                      PRAYER_RIGOUR_ATT, 3)
            eff_str = effective_level(self.player.ranged, self.player.ranged_boost,
                                      PRAYER_RIGOUR_STR, 3)
            str_bonus = self._sum(full, "ranged_strength_bonus")
            hit = max_hit_ranged(eff_str, str_bonus)
        else:
            eff_att = effective_level(self.player.attack, self.player.attack_boost,
                                      PRAYER_PIETY_ATT, 3)
            eff_str = effective_level(self.player.strength, self.player.strength_boost,
                                      PRAYER_PIETY_STR, 3)
            str_bonus = self._sum(full, "strength_bonus")
            hit = max_hit_melee(eff_str, str_bonus)

        def_bonus = target.defence_bonus.get(style, 0)
        acc = accuracy(attack_roll(eff_att, att_bonus),
                       defence_roll(target.defence_level, def_bonus))

        name = weapon.lower()
        hits = 1.0
        if name == "scythe of vitur":
            hits = scythe_hits(target.size)
        elif name == "twisted bow":
            a_mult, d_mult = twisted_bow_multipliers(target.defence_bonus.get("magic_level", 0))
            acc = min(acc * a_mult, 1.0)
            hit = int(hit * d_mult)
        elif name == "osmumten's fang":
            acc = fang_accuracy(acc)

        return dps(acc, hit, speed) * hits, hit, acc

    # -- optimisation ------------------------------------------------------

    def best_for_weapon(self, weapon: str, target: Target, style: str,
                        passes: int = 2) -> Loadout | None:
        row = self.stats.item(weapon)
        if not row:
            return None
        slot = (row.get("equipment_slot") or "")
        if isinstance(slot, list):
            slot = slot[0] if slot else ""
        two_handed = slot.strip().lower() in TWO_HANDED

        worn: dict[str, str] = {}
        for _ in range(passes):
            for gear_slot in MELEE_SLOTS:
                if two_handed and gear_slot == "shield":
                    continue
                best_name, best_dps = worn.get(gear_slot), -1.0
                for candidate in self.slot_items(gear_slot, style):
                    trial = dict(worn)
                    trial[gear_slot] = candidate
                    value, _, _ = self.score(weapon, trial, target, style)
                    if value > best_dps:
                        best_dps, best_name = value, candidate
                if best_name:
                    worn[gear_slot] = best_name

        value, hit, acc = self.score(weapon, worn, target, style)
        return Loadout(weapon=weapon, items=worn, style=style,
                       dps=value, max_hit=hit, accuracy=acc)

    def best_overall(self, target: Target, style: str,
                     weapons: list[str] | None = None) -> tuple[list[Loadout], list[str]]:
        """Returns (ranked loadouts, weapons skipped as unmodellable)."""
        candidates = weapons or self.weapon_candidates(style)
        ranked: list[Loadout] = []
        skipped: list[str] = []
        for weapon in candidates:
            if not is_rankable(weapon):
                skipped.append(weapon)
                continue
            result = self.best_for_weapon(weapon, target, style)
            if result and result.dps > 0:
                ranked.append(result)
        ranked.sort(key=lambda l: l.dps, reverse=True)
        return ranked, skipped

    def weapon_candidates(self, style: str, keep: int = 40) -> list[str]:
        """Shortlist weapons by a cheap damage-rate proxy before full scoring."""
        field = STYLE_ATTACK[style]
        str_field = "ranged_strength_bonus" if style == "ranged" else "strength_bonus"
        scored: list[tuple[str, float]] = []
        for name, rows in self.stats.items.items():
            for row in rows:
                slot = row.get("equipment_slot")
                if isinstance(slot, list):
                    slot = slot[0] if slot else None
                if (slot or "").strip().lower() not in ("weapon", "2h"):
                    continue
                att = num(row.get(field))
                if att <= 0:
                    break
                speed = num(row.get("weapon_attack_speed"), 4) or 4
                # Damage per tick scales with strength and inversely with speed;
                # accuracy scales with the attack bonus.
                scored.append((name, (num(row.get(str_field)) + 64) * (att + 64) / speed))
                break
        scored.sort(key=lambda s: -s[1])
        return [n for n, _ in scored[:keep]]


def target_from_monster(row: dict) -> Target:
    return Target(
        name=(row.get("name") or row.get("page_name") or ""),
        hitpoints=int(num(row.get("hitpoints"), 100)),
        defence_level=num(row.get("defence_level"), 100),
        defence_bonus={
            "stab": num(row.get("stab_defence_bonus")),
            "slash": num(row.get("slash_defence_bonus")),
            "crush": num(row.get("crush_defence_bonus")),
            "ranged": num(row.get("range_defence_bonus")),
            "magic": num(row.get("magic_defence_bonus")),
            "magic_level": num(row.get("magic_level")),
        },
        size=int(num(row.get("size"), 1)),
        attributes=row.get("attribute") if isinstance(row.get("attribute"), list) else [],
        elemental_weakness=(row.get("elemental_weakness") or ""),
        elemental_weakness_percent=num(row.get("elemental_weakness_percent")),
        flat_armour=num(row.get("flat_armour")),
    )
