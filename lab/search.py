"""Gear optimisation against a specific monster, scored by the wiki calculator.

Coordinate ascent: start from a weapon, then repeatedly swap in whichever item
raises DPS most, slot by slot, until a pass changes nothing. Slots are close to
additive so this converges in two or three passes, and every candidate is scored
by the real calculator rather than a proxy.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine import Data, Item, SLOTS, call_oracle  # noqa: E402

GEAR_SLOTS = [s for s in SLOTS if s != "weapon"]

STYLE_INDEX = {"stab": 0, "slash": 1, "crush": 2}


@dataclass
class Result:
    weapon: str
    gear: dict[str, str]
    dps: float
    max_hit: int
    accuracy: float
    style_name: str = ""
    style_type: str = ""
    detail: dict = field(default_factory=dict)


def _loadout(weapon: Item, gear: dict[str, Item], style_index: int | None = None) -> dict:
    payload = {"weapon": weapon.id}
    for slot, item in gear.items():
        if weapon.two_handed and slot == "shield":
            continue
        payload[slot] = item.id
    out: dict = {"gear": payload}
    if style_index is not None:
        out["styleIndex"] = style_index
    return out


def weapon_shortlist(data: Data, style: str, keep: int = 30) -> list[Item]:
    """Cheap damage-rate proxy to pick which weapons deserve a full solve."""
    weapons = [i for i in data.equipment if i.slot == "weapon"]
    scored = []
    for w in weapons:
        off = w.offence(style)
        if off <= 0:
            continue
        speed = w.speed or 4
        scored.append((w, (w.strength(style) + 64) * (off + 64) / speed))
    scored.sort(key=lambda s: -s[1])
    return [w for w, _ in scored[:keep]]


def solve(data: Data, monster_name: str, style: str, *,
          weapons: list[Item] | None = None, passes: int = 2,
          slot_keep: int = 10, weapon_keep: int = 30,
          style_index: int | None = None) -> list[Result]:
    """Best loadout per candidate weapon, ranked by DPS."""
    candidates = weapons if weapons is not None else weapon_shortlist(data, style, weapon_keep)
    pools = {slot: data.shortlist(slot, style, slot_keep) for slot in GEAR_SLOTS}

    # Every weapon advances through the same slot at the same time, so one
    # oracle call covers the whole fleet. Scoring per weapon instead would spawn
    # hundreds of Node processes per monster and process startup would dominate
    # the runtime entirely.
    gear: dict[int, dict[str, Item]] = {w.id: {} for w in candidates}

    for _ in range(passes):
        for slot in GEAR_SLOTS:
            pool = pools[slot]
            if not pool:
                continue
            batch: list[dict] = []
            refs: list[tuple[Item, Item]] = []
            for weapon in candidates:
                if weapon.two_handed and slot == "shield":
                    continue
                for cand in pool:
                    trial = dict(gear[weapon.id])
                    trial[slot] = cand
                    batch.append(_loadout(weapon, trial, style_index))
                    refs.append((weapon, cand))
            if not batch:
                continue

            scored = call_oracle(monster_name, batch)
            best: dict[int, tuple[float, Item]] = {}
            for r in scored:
                if r.get("error"):
                    continue
                weapon, cand = refs[r["i"]]
                current = best.get(weapon.id)
                if current is None or r["dps"] > current[0]:
                    best[weapon.id] = (r["dps"], cand)
            for weapon_id, (_, cand) in best.items():
                gear[weapon_id][slot] = cand

    finals = call_oracle(
        monster_name,
        [_loadout(w, gear[w.id], style_index) for w in candidates],
    )

    results: list[Result] = []
    for r in finals:
        if r.get("error"):
            continue
        weapon = candidates[r["i"]]
        results.append(Result(
            weapon=weapon.name,
            gear={s: i.name for s, i in gear[weapon.id].items()
                  if not (weapon.two_handed and s == "shield")},
            dps=r["dps"],
            max_hit=r.get("maxHit", 0),
            accuracy=r.get("accuracy", 0),
            style_name=r.get("styleName") or "",
            style_type=r.get("styleType") or "",
        ))

    results.sort(key=lambda r: -r.dps)
    return results


def score_gear_ids(monster_name: str, gear_ids: dict[str, int],
                   style_index: int | None = None) -> dict:
    """Score an explicit set of item ids - used for the wiki baseline."""
    payload: dict = {"gear": {s: i for s, i in gear_ids.items() if i}}
    if style_index is not None:
        payload["styleIndex"] = style_index
    return call_oracle(monster_name, [payload])[0]
