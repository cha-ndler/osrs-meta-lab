"""Gear optimisation against a specific monster, scored by the wiki calculator.

Two stages. First every weapon the style can use is scored against a fixed
reference kit, in one batched oracle call, and the best survive. Then those
advance through coordinate ascent: repeatedly swap in whichever item raises DPS
most, slot by slot, until a pass changes nothing. Slots are close to additive so
this converges in two or three passes.

Nothing is ranked by a stand-in for DPS at any point. An earlier version chose
which weapons deserved a full solve using `(str + 64) * (off + 64) / speed`,
which is blind to every mechanic that makes a weapon worth noticing: the Scythe
of vitur's three hits per swing, Dharok's scaling off missing health, Osmumten's
fang re-rolling its accuracy. The scythe placed 37th on that proxy while the
caller kept 14, so the strongest melee weapon in the game against a large target
was never once scored - and weapons whose value is a mechanic are exactly the
overlooked metas this repo exists to find. The proxy was pruning the answer.

Scoring all of them costs one oracle call, because the bridge batches: 1,312
melee loadouts return in under three seconds including process startup. The
proxy was never buying anything worth its cost.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine import (  # noqa: E402
    SLOTS, Data, Item, blocking, call_oracle, describe_profile, warnings_in,
)

GEAR_SLOTS = [s for s in SLOTS if s != "weapon"]


@dataclass(frozen=True)
class Candidate:
    """A weapon plus the two choices that come with it rather than after it.

    Stance and spell are picked in the same scan that picks the weapon, because
    neither is separable from it: a scimitar offers slash at two stances, and a
    staff is only as good as the spell it casts.
    """
    weapon: Item
    style_index: int
    spell: str | None = None


@dataclass
class Result:
    weapon: str
    gear: dict[str, str]
    dps: float
    max_hit: int
    accuracy: float
    style_name: str = ""
    style_type: str = ""
    style_index: int = 0
    ammo: str = ""
    spell: str = ""
    warnings: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _loadout(weapon: Item, gear: dict[str, Item], style_index: int,
             ammo: int | None = None, spell: str | None = None) -> dict:
    payload: dict = {"weapon": weapon.id}
    for slot, item in gear.items():
        if weapon.two_handed and slot == "shield":
            continue
        if slot == "ammo":
            continue          # weapon-specific; supplied separately
        payload[slot] = item.id
    if ammo is not None:
        payload["ammo"] = ammo
    out: dict = {"gear": payload, "styleIndex": style_index}
    if spell is not None:
        out["spell"] = spell
    return out


def reference_gear(data: Data, style: str,
                   pool: dict[str, list[Item]] | None = None) -> dict[str, Item]:
    """A fixed, decent kit used to rank weapons against one another.

    Weapons are compared on equal footing rather than each at its own optimum,
    which would be circular - you cannot pick the best gear for a weapon before
    knowing whether the weapon is worth keeping. Anything surviving this stage
    gets its gear optimised properly afterwards.

    The kit must come from the same pool the solve will use. Ranking candidates
    in best-in-game gear and then making them fight in a published setup's
    armour picks the wrong ones entirely: magic weapons win the audition on
    full ancestral, then take the field in melee armour and lose to the setup
    they were supposed to beat. That produced solves scoring 40% *below* the
    published loadout they were drawn from.
    """
    out: dict[str, Item] = {}
    for slot in GEAR_SLOTS:
        if slot == "ammo":
            continue
        items = pool.get(slot) if pool is not None else data.in_slot(slot)
        if items:
            out[slot] = max(items, key=lambda i: i.offence(style) + i.strength(style))
    return out


def best_ammo(data: Data, weapon: Item,
              allowed: set[int] | None = None) -> int | None:
    """Strongest ammo the weapon accepts, or None if it takes none.

    A bow holding bolts is not a weak loadout, it is a zero - the calculator
    discards the ammo's bonuses entirely - so the pairing is constrained here
    rather than left for the search to discover.

    `allowed` narrows the choice to a published pool. Without it a same-tier
    solve would quietly reach for the best arrow in the game while claiming to
    use only the gear a wiki page names, which is the tier gap the constraint
    exists to close, reopened one slot at a time.
    """
    valid = data.ammo_for(weapon)
    if not valid:
        return None
    items = [data.by_id[a] for a in valid if a in data.by_id]
    if allowed is not None:
        items = [i for i in items if i.id in allowed]
    if not items:
        return None
    return max(items, key=lambda i: (i.strength("ranged"), i.offence("ranged"))).id


def weapon_shortlist(data: Data, monster_name: str, style: str, keep: int = 12, *,
                     inputs: dict | None = None, version: str | None = None,
                     restrict: set[int] | None = None,
                     pool: dict[str, list[Item]] | None = None) -> list[Candidate]:
    """Score every weapon that can attack with `style`; return the best.

    `restrict` limits the scan to a set of item ids and `pool` supplies the kit
    they are auditioned in; together they hold a solve to the same tier as a
    published setup.
    """
    # Restrict *before* collapsing aliases. The other order silently discards
    # the published weapon whenever an ornamented sibling sorts ahead of it:
    # the Holy scythe of vitur shares a canonical id with the plain one, won
    # the tie, and Araxxor's melee solve was left with no candidates at all
    # while its published setup was a Scythe.
    if restrict is None:
        weapons = [i for i in data.equipment if i.slot == "weapon"]
    else:
        # A restricted scan names its weapons explicitly, so it resolves them
        # against the unfiltered table. The mode-restricted filter would
        # otherwise delete the Gauntlet's own bow from a Gauntlet solve.
        weapons = [i for i in (data.by_id_unfiltered.get(w) for w in restrict)
                   if i is not None and i.slot == "weapon"]
    weapons = data.dedupe(weapons)
    reference = reference_gear(data, style, pool)
    ammo_allowed = None if pool is None else {i.id for i in pool.get("ammo", [])}

    batch: list[dict] = []
    refs: list[tuple[Item, int, str | None]] = []
    for weapon in weapons:
        entries = data.style_entries(weapon, style)
        if not entries:
            continue
        # Every spellcasting stance rolls the same accuracy and damage, so when
        # a weapon offers Autocast the other two are dominated and only add
        # spell fan-out. Keeping all three cost 21s a monster to rediscover it.
        autocast = [e for e in entries
                    if (e.get("stance") or "").lower() == "autocast"]
        if style == "magic" and autocast:
            entries = autocast
        ammo = best_ammo(data, weapon, ammo_allowed)
        if data.ammo_for(weapon) is not None and ammo is None:
            continue          # the pool supplies nothing this can fire
        for entry in entries:
            # A powered staff supplies its own attack and takes no spell, so
            # None always has to be on the table alongside any spell list.
            spells: list[str | None] = [None]
            if style == "magic" and data.needs_spell(weapon, entry):
                spells += data.spells
            for spell in spells:
                batch.append(_loadout(weapon, reference, entry["index"], ammo, spell))
                refs.append((weapon, entry["index"], spell))
    if not batch:
        return []

    scored = call_oracle(monster_name, batch, style=style, inputs=inputs,
                         version=version)
    ranked: list[tuple[float, Item, int, str | None]] = []
    for r in scored:
        if r.get("error") or blocking(r) or not r.get("dps"):
            continue
        weapon, index, spell = refs[r["i"]]
        ranked.append((r["dps"], weapon, index, spell))
    ranked.sort(key=lambda t: -t[0])

    # One entry per weapon: the stance that beat the others is the only one
    # worth carrying into the expensive stage.
    #
    # Collapsing by name as well as by id matters because charge states ship as
    # separate ids under one name - Scythe of vitur, Scythe of vitur (Charged) -
    # and they are not aliases of each other, so the alias table leaves both in.
    # This has to happen *after* scoring: collapsing beforehand would keep
    # whichever id came first in the file, which is as likely to be the
    # uncharged, useless one.
    seen: set[str] = set()
    out: list[Candidate] = []
    for _, weapon, index, spell in ranked:
        if weapon.name in seen:
            continue
        seen.add(weapon.name)
        out.append(Candidate(weapon, index, spell))
        if len(out) >= keep:
            break
    return out


def solve(data: Data, monster_name: str, style: str, *,
          candidates: list[Candidate] | None = None, passes: int = 2,
          slot_keep: int = 10, weapon_keep: int = 12,
          pool: dict[str, list[Item]] | None = None, seed: dict[str, Item] | None = None,
          inputs: dict | None = None, version: str | None = None) -> list[Result]:
    """Best loadout per candidate weapon, ranked by DPS.

    `pool` constrains which items may be equipped, which is how a solve is held
    to the same gear tier as a published setup. Left unset, the search runs
    unconstrained best-in-slot.

    `seed` starts the ascent from a known loadout instead of from nothing.
    Coordinate ascent only ever accepts a swap that improves on where it
    already is, so starting from the setup being compared against makes
    "at least as good as that setup" structural rather than something to hope
    for. It matters because the ascent is greedy and set effects are not:
    three pieces of crystal armour beat three individually stronger pieces, but
    no single swap gets there from an empty slot, so the search would settle
    just short and report the published setup as better than its own optimum.
    """
    if candidates is None:
        candidates = weapon_shortlist(data, monster_name, style, keep=weapon_keep,
                                      inputs=inputs, version=version)
    if not candidates:
        return []

    pools = pool if pool is not None else {
        slot: data.shortlist(slot, style, slot_keep) for slot in GEAR_SLOTS
    }
    # A constrained solve draws its ammunition from the same pool as everything
    # else. A weapon whose ammunition the pool does not supply cannot be fired
    # at this tier and drops out, which is the honest answer rather than
    # borrowing an arrow from outside the constraint.
    allowed_ammo = None
    if pool is not None:
        allowed_ammo = {i.id for i in pool.get("ammo", [])}
    ammo = {c.weapon.id: best_ammo(data, c.weapon, allowed_ammo) for c in candidates}
    candidates = [c for c in candidates
                  if data.ammo_for(c.weapon) is None or ammo[c.weapon.id] is not None]
    if not candidates:
        return []

    # Every weapon advances through the same slot at the same time, so one
    # oracle call covers the whole fleet. Scoring per weapon instead would spawn
    # hundreds of Node processes per monster and startup would dominate.
    start = dict(seed) if seed else {}
    start.pop("weapon", None)
    start.pop("ammo", None)          # weapon-specific; chosen by best_ammo
    gear: dict[int, dict[str, Item]] = {c.weapon.id: dict(start) for c in candidates}

    for _ in range(passes):
        for slot in GEAR_SLOTS:
            if slot == "ammo":
                continue      # weapon-specific; chosen by best_ammo
            slot_pool = pools.get(slot) or []
            if not slot_pool:
                continue
            batch: list[dict] = []
            refs: list[tuple[Item, Item]] = []
            for cand in candidates:
                weapon = cand.weapon
                if weapon.two_handed and slot == "shield":
                    continue
                for option in slot_pool:
                    trial = dict(gear[weapon.id])
                    trial[slot] = option
                    batch.append(_loadout(weapon, trial, cand.style_index,
                                          ammo[weapon.id], cand.spell))
                    refs.append((weapon, option))
            if not batch:
                continue

            scored = call_oracle(monster_name, batch, style=style, inputs=inputs,
                                 version=version)
            best: dict[int, tuple[float, Item]] = {}
            for r in scored:
                if r.get("error") or blocking(r):
                    continue
                weapon, cand = refs[r["i"]]
                current = best.get(weapon.id)
                if current is None or r["dps"] > current[0]:
                    best[weapon.id] = (r["dps"], cand)
            for weapon_id, (_, cand) in best.items():
                gear[weapon_id][slot] = cand

    finals = call_oracle(
        monster_name,
        [_loadout(c.weapon, gear[c.weapon.id], c.style_index,
                  ammo[c.weapon.id], c.spell) for c in candidates],
        style=style, inputs=inputs, version=version,
    )

    results: list[Result] = []
    for r in finals:
        if r.get("error") or blocking(r):
            continue
        cand = candidates[r["i"]]
        weapon = cand.weapon
        ammo_id = ammo[weapon.id]
        results.append(Result(
            weapon=weapon.name,
            gear={s: i.name for s, i in gear[weapon.id].items()
                  if not (weapon.two_handed and s == "shield")},
            dps=r["dps"],
            max_hit=r.get("maxHit", 0),
            accuracy=r.get("accuracy", 0),
            style_name=r.get("styleName") or "",
            style_type=r.get("styleType") or "",
            style_index=cand.style_index,
            ammo=data.by_id[ammo_id].name if ammo_id in data.by_id else "",
            spell=cand.spell or "",
            warnings=warnings_in(r),
        ))

    results.sort(key=lambda r: -r.dps)
    return results


def score_gear_ids(monster_name: str, gear_ids: dict[str, int], style: str,
                   style_index: int | None = None,
                   inputs: dict | None = None) -> dict:
    """Score an explicit set of item ids - used for the wiki baseline."""
    payload: dict = {"gear": {s: i for s, i in gear_ids.items() if i}}
    if style_index is not None:
        payload["styleIndex"] = style_index
    return call_oracle(monster_name, [payload], style=style, inputs=inputs)[0]


def profile_note(style: str) -> str:
    return describe_profile(style)
