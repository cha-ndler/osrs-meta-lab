"""The trust gate, measured in DPS.

For each activity the wiki publishes one or more setups. Those setups and the
solver's best are scored through the *same* oracle and the delta is reported.
The healthy result is that most published setups sit at or near the optimum:
that is what earns the right to believe the outliers. A solver that beats
consensus everywhere is broken, not brilliant.

Two solves per activity, and the difference between them is the whole point.

  constrained   drawn only from items the wiki's own setups for this activity
                already name. Same gear, rearranged. This is the finding gate.

  unconstrained best-in-slot from the entire game.

The earlier version reported only the second and found a median +22% "in the
solver's favour", which measured nothing: wiki pages routinely publish
affordable mid-tier gear, so most of that gap was Torva beating a guide written
for someone with a fire cape. Every activity looked like a finding and none of
them were. Constraining the pool removes the tier gap by construction, without
needing prices or a judgement about what tier a page was aiming at.

Run: python lab/agree.py [--limit N] [--activity NAME]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import report  # noqa: E402
from engine import (  # noqa: E402
    COMBAT_PROFILES, Data, blocking, call_oracle, describe_profile, warnings_in,
)
from search import GEAR_SLOTS, solve, weapon_shortlist  # noqa: E402

ROOT = Path(__file__).parent.parent
BASELINE = ROOT / "baseline" / "data"
REPORTS = ROOT / "reports"

# A finding must clear this margin to be worth your attention.
FINDING_THRESHOLD = 0.02

STYLES = tuple(COMBAT_PROFILES)

# The public library's slot names, mapped onto the calculator's.
SLOT_MAP = {
    "head": "head", "cape": "cape", "neck": "neck", "ammo": "ammo",
    "weapon": "weapon", "torso": "body", "shield": "shield",
    "legs": "legs", "gloves": "hands", "boots": "feet", "ring": "ring",
}


def baseline_gear(variant: dict, data: Data) -> dict[str, int]:
    """A published setup's equipment, by calculator slot.

    Resolved against the unfiltered item table. The mode-restricted filter is
    there to stop the solver choosing gear it cannot bring, but a wiki page
    naming an item for an activity is evidence the player has it there - and
    applying the filter here deleted the Gauntlet's own crystal and corrupted
    gear from the Gauntlet's own setups.
    """
    out: dict[str, int] = {}
    for theirs, ours in SLOT_MAP.items():
        item_id = variant.get("equipment", {}).get(theirs)
        if item_id and item_id in data.by_id_unfiltered:
            out[ours] = item_id
    return out


def published_pool(record: dict, data: Data) -> dict[str, list]:
    """Every item any published setup for this activity equips, by slot.

    The union across variants, not one chosen variant: pages publish budget,
    mid-tier and maxed setups side by side, and picking one would reintroduce
    the tier gap this exists to remove.
    """
    pool: dict[str, dict[int, object]] = {slot: {} for slot in GEAR_SLOTS}
    for variant in record["variants"]:
        for slot, item_id in baseline_gear(variant, data).items():
            if slot in pool:
                pool[slot][item_id] = data.by_id_unfiltered[item_id]
    return {slot: list(items.values()) for slot, items in pool.items()}


def published_weapons(record: dict, data: Data) -> set[int]:
    out: set[int] = set()
    for variant in record["variants"]:
        weapon = baseline_gear(variant, data).get("weapon")
        if weapon:
            out.add(weapon)
    return out


def score_baseline(target: dict, record: dict,
                   data: Data) -> tuple[dict | None, str | None]:
    """Best published setup, over every variant and every attack style.

    Scoring all of them and keeping the maximum is the fair comparison: a page's
    budget setup is not a claim about the optimum, so holding it up against a
    solved loadout would manufacture a finding every time.
    """
    batch, refs = [], []
    for variant in record["variants"]:
        gear = baseline_gear(variant, data)
        if "weapon" not in gear:
            continue
        weapon = data.by_id_unfiltered[gear["weapon"]]
        for style in STYLES:
            for entry in data.style_entries(weapon, style):
                spells: list[str | None] = [None]
                if style == "magic" and data.needs_spell(weapon, entry):
                    spells = list(data.spells)
                for spell in spells:
                    # Prayers and potions go *in the loadout*, not as the
                    # batch-level profile: this batch deliberately mixes styles,
                    # and one profile cannot cover all of them. Setting it at
                    # batch level here would silently score the baseline
                    # unprayed while the solver ran under Piety, which inflates
                    # every delta by roughly the value of a prayer.
                    payload: dict = {"gear": gear, "styleIndex": entry["index"],
                                     **COMBAT_PROFILES[style]}
                    if spell:
                        payload["spell"] = spell
                    batch.append(payload)
                    refs.append((variant, style, spell))
    if not batch:
        return None, "no published setup lists an equipped weapon"

    scored = call_oracle(target["monster"], batch, version=target.get("version"),
                         inputs=target.get("inputs"))
    best = None
    for r in scored:
        if r.get("error") or blocking(r) or not r.get("dps"):
            continue
        if best is None or r["dps"] > best[0]["dps"]:
            best = (r, *refs[r["i"]])
    if best is None:
        # Every published setup scored nothing. That is usually an
        # immunity rather than bad gear - Vespula cannot be hurt by melee,
        # and every Chambers of Xeric setup equips a melee weapon - so say
        # so, because "no scorable setup" reads like a missing page.
        return None, (f"every published setup scored zero against "
                      f"{target['monster']}; it is likely immune to the "
                      f"styles they use")
    result, variant, style, spell = best
    return {
        "dps": result["dps"], "variant": variant["variant"], "style": style,
        "styleName": result.get("styleName"), "spell": spell or "",
        "warnings": warnings_in(result),
        # The winning variant's own gear, used to start the constrained
        # ascent from the setup it is being measured against.
        "gear": {slot: data.by_id_unfiltered[i]
                 for slot, i in baseline_gear(variant, data).items()
                 if i in data.by_id_unfiltered},
    }, None


def best_solve(data: Data, target: dict, *, pool=None, restrict=None,
               seed=None, weapons: int = 8, slots: int = 5):
    """Highest-DPS loadout across every style, optionally pool-constrained."""
    monster = target["monster"]
    version = target.get("version")
    inputs = target.get("inputs")
    best = None
    for style in STYLES:
        # Constrained solves keep every published weapon rather than a top
        # slice: there are only ever a handful, and dropping one means the
        # setup the comparison is against may not be reachable at all.
        keep = max(weapons, len(restrict)) if restrict else weapons
        candidates = weapon_shortlist(data, monster, style, keep=keep,
                                      restrict=restrict, version=version,
                                      inputs=inputs, pool=pool)
        if not candidates:
            continue
        results = solve(data, monster, style, candidates=candidates,
                        pool=pool, seed=seed, slot_keep=slots, passes=2,
                        version=version, inputs=inputs)
        if results and (best is None or results[0].dps > best[0].dps):
            best = (results[0], style)
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N activities")
    ap.add_argument("--activity", help="a single activity by name")
    ap.add_argument("--weapons", type=int, default=8)
    ap.add_argument("--slots", type=int, default=5)
    args = ap.parse_args()

    if not BASELINE.exists():
        print(f"baseline missing at {BASELINE}", file=sys.stderr)
        return 2

    data = Data()
    rows: list[dict] = []
    skipped: list[str] = []
    started = time.time()

    paths = sorted(BASELINE.glob("*.json"))
    if args.activity:
        paths = [p for p in paths
                 if json.loads(p.read_text(encoding="utf-8"))["activity"] == args.activity]
    if args.limit:
        paths = paths[: args.limit]

    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        activity = record["activity"]
        targets = data.targets(activity)
        if not targets:
            reason = data.skip_reason(activity) or "no monster"
            skipped.append(f"{activity} ({reason})")
            continue

        pool = published_pool(record, data)
        restrict = published_weapons(record, data)

        # A raid is several fights sharing one name, so it expands to one row
        # per boss. The published setup is the same for all of them, which is
        # itself worth seeing: a kit chosen for the whole raid will not be
        # optimal at every stop inside it.
        for target in targets:
            label = target["monster"]
            if label != activity:
                label = f"{activity} / {label}"

            try:
                base, why = score_baseline(target, record, data)
            except Exception as exc:
                skipped.append(f"{label} (oracle: {str(exc)[:60]})")
                continue
            if not base:
                skipped.append(f"{label} ({why})")
                continue

            constrained = best_solve(data, target, pool=pool, restrict=restrict,
                                     seed=base["gear"], weapons=args.weapons,
                                     slots=args.slots)
            unconstrained = best_solve(data, target, weapons=args.weapons,
                                       slots=args.slots)
            if not constrained or not unconstrained:
                skipped.append(f"{label} (no solve)")
                continue

            con, con_style = constrained
            unc, unc_style = unconstrained
            con_delta = (con.dps - base["dps"]) / base["dps"] if base["dps"] else 0
            unc_delta = (unc.dps - base["dps"]) / base["dps"] if base["dps"] else 0
            emit(rows, activity, target, label, base, con, con_style, unc,
                 unc_style, con_delta, unc_delta, per_boss=len(targets) > 1)

    return finish(rows, skipped, started)



def emit(rows, activity, target, label, base, con, con_style, unc, unc_style,
         con_delta, unc_delta, per_boss: bool = False) -> None:
    # A multi-boss activity publishes one kit for the whole encounter, so
    # letting the solver re-optimise per boss and calling the gap a finding
    # measures nothing: of course a loadout picked for Vespula beats one picked
    # for the whole raid. Doing it anyway flagged 31 of 35 "findings", every
    # Theatre of Blood boss among them. The rows are kept - knowing your raid
    # kit does 0.256 dps at Vespula is worth knowing - but they are per-boss
    # detail, not a disagreement with the page.
    is_finding = con_delta >= FINDING_THRESHOLD and not per_boss
    rows.append({
        "activity": activity,
        "target": target["monster"],
        "label": label,
        "baseline": {k: base[k] for k in
                     ("dps", "variant", "style", "styleName", "spell", "warnings")},
        "constrained": {
            "dps": round(con.dps, 4), "delta": round(con_delta, 4),
            "weapon": con.weapon, "style": con_style,
            "styleName": con.style_name, "spell": con.spell,
            "ammo": con.ammo, "gear": con.gear, "warnings": con.warnings,
            "profile": describe_profile(con_style),
        },
        "unconstrained": {
            "dps": round(unc.dps, 4), "delta": round(unc_delta, 4),
            "weapon": unc.weapon, "style": unc_style,
            "styleName": unc.style_name, "spell": unc.spell,
            "ammo": unc.ammo, "gear": unc.gear, "warnings": unc.warnings,
            "profile": describe_profile(unc_style),
        },
        "isFinding": is_finding,
        "perBoss": per_boss,
        "styleSwitch": con_style != base["style"],
    })
    flag = "FINDING" if is_finding else ("per-boss" if per_boss else "        ")
    print(f"  {flag} {label:<40} base {base['dps']:6.3f} -> "
          f"same-tier {con.dps:6.3f} ({con_delta:+6.1%})  "
          f"ceiling {unc.dps:6.3f} ({unc_delta:+6.1%})  {con.weapon}")


def finish(rows: list[dict], skipped: list[str], started: float) -> int:
    con_deltas = [r["constrained"]["delta"] for r in rows]
    unc_deltas = [r["unconstrained"]["delta"] for r in rows]
    findings = [r for r in rows if r["isFinding"]]
    per_boss_rows = [r for r in rows if r["perBoss"]]
    single = [r for r in rows if not r["perBoss"]]
    single_deltas = [r["constrained"]["delta"] for r in single]
    summary = {
        "checked": len(rows),
        "activities": len({r["activity"] for r in rows}),
        "profiles": {s: describe_profile(s) for s in STYLES},
        "threshold": FINDING_THRESHOLD,
        "constrained": {
            "medianDelta": round(statistics.median(con_deltas), 4) if con_deltas else None,
            "meanDelta": round(statistics.fmean(con_deltas), 4) if con_deltas else None,
            "atOrBelowBaseline": len([d for d in con_deltas if d <= 0]),
        },
        "unconstrained": {
            "medianDelta": round(statistics.median(unc_deltas), 4) if unc_deltas else None,
            "meanDelta": round(statistics.fmean(unc_deltas), 4) if unc_deltas else None,
        },
        "findings": len(findings),
        "singleTargetRows": len(single),
        "singleTargetMedianDelta": (round(statistics.median(single_deltas), 4)
                                    if single_deltas else None),
        "perBossRows": len(per_boss_rows),
        "perBossAboveThreshold": len([r for r in per_boss_rows
                                      if r["constrained"]["delta"] >= FINDING_THRESHOLD]),
        "styleSwitches": len([r for r in rows if r["styleSwitch"]]),
        "skipped": skipped,
        "elapsedSeconds": round(time.time() - started, 1),
        "rows": rows,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "agreement.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    # One file per finding. The JSON is the record; these are the part a person
    # reads, and each one has to carry enough context to be argued with - what
    # the published setup was, what the constrained pool allowed, and which
    # prayers and potions the numbers assume.
    findings_dir = REPORTS / "findings"
    if findings_dir.exists():
        for stale in findings_dir.glob("*.md"):
            stale.unlink()
    if findings:
        findings_dir.mkdir(parents=True, exist_ok=True)
        for row in findings:
            (findings_dir / f"{report.slug(row['label'])}.md").write_text(
                report.finding(row), encoding="utf-8")
        print(f"wrote {len(findings)} finding(s) to {findings_dir.relative_to(ROOT)}")

    print(f"\nchecked {len(rows)} targets across {summary['activities']} "
          f"activities in {summary['elapsedSeconds']}s")
    if con_deltas:
        print(f"same-tier median vs published: {summary['constrained']['medianDelta']:+.2%}")
        print(f"unconstrained ceiling median:  {summary['unconstrained']['medianDelta']:+.2%}")
        print(f"solver at or below baseline:   "
              f"{summary['constrained']['atOrBelowBaseline']}/{len(rows)}")
    print(f"candidate findings (>= {FINDING_THRESHOLD:.0%} same-tier, "
          f"single-target activities): {len(findings)}")
    print(f"per-boss rows of multi-boss activities: {len(per_boss_rows)} "
          f"({summary['perBossAboveThreshold']} over threshold, reported as detail "
          f"rather than findings)")
    print(f"skipped: {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
