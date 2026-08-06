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
    out: dict[str, int] = {}
    for theirs, ours in SLOT_MAP.items():
        item_id = variant.get("equipment", {}).get(theirs)
        if item_id and item_id in data.by_id:
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
                pool[slot][item_id] = data.by_id[item_id]
    return {slot: list(items.values()) for slot, items in pool.items()}


def published_weapons(record: dict, data: Data) -> set[int]:
    out: set[int] = set()
    for variant in record["variants"]:
        weapon = baseline_gear(variant, data).get("weapon")
        if weapon:
            out.add(weapon)
    return out


def score_baseline(activity: str, record: dict, data: Data) -> dict | None:
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
        weapon = data.by_id[gear["weapon"]]
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
        return None

    scored = call_oracle(activity, batch)
    best = None
    for r in scored:
        if r.get("error") or blocking(r) or not r.get("dps"):
            continue
        if best is None or r["dps"] > best[0]["dps"]:
            best = (r, *refs[r["i"]])
    if best is None:
        return None
    result, variant, style, spell = best
    return {
        "dps": result["dps"], "variant": variant["variant"], "style": style,
        "styleName": result.get("styleName"), "spell": spell or "",
        "warnings": warnings_in(result),
    }


def best_solve(data: Data, activity: str, *, pool=None, restrict=None,
               weapons: int = 8, slots: int = 5):
    """Highest-DPS loadout across every style, optionally pool-constrained."""
    best = None
    for style in STYLES:
        candidates = weapon_shortlist(data, activity, style, keep=weapons,
                                      restrict=restrict)
        if not candidates:
            continue
        results = solve(data, activity, style, candidates=candidates,
                        pool=pool, slot_keep=slots, passes=2)
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
        if not data.monster(activity):
            skipped.append(f"{activity} (no monster)")
            continue

        try:
            base = score_baseline(activity, record, data)
        except Exception as exc:
            skipped.append(f"{activity} (oracle: {str(exc)[:60]})")
            continue
        if not base:
            skipped.append(f"{activity} (no scorable setup)")
            continue

        pool = published_pool(record, data)
        restrict = published_weapons(record, data)
        constrained = best_solve(data, activity, pool=pool, restrict=restrict,
                                 weapons=args.weapons, slots=args.slots)
        unconstrained = best_solve(data, activity, weapons=args.weapons,
                                   slots=args.slots)
        if not constrained or not unconstrained:
            skipped.append(f"{activity} (no solve)")
            continue

        con, con_style = constrained
        unc, unc_style = unconstrained
        con_delta = (con.dps - base["dps"]) / base["dps"] if base["dps"] else 0
        unc_delta = (unc.dps - base["dps"]) / base["dps"] if base["dps"] else 0

        rows.append({
            "activity": activity,
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
            "isFinding": con_delta >= FINDING_THRESHOLD,
            "styleSwitch": con_style != base["style"],
        })
        flag = "FINDING" if con_delta >= FINDING_THRESHOLD else "       "
        print(f"  {flag} {activity:<28} base {base['dps']:6.3f} -> "
              f"same-tier {con.dps:6.3f} ({con_delta:+6.1%})  "
              f"ceiling {unc.dps:6.3f} ({unc_delta:+6.1%})  {con.weapon}")

    con_deltas = [r["constrained"]["delta"] for r in rows]
    unc_deltas = [r["unconstrained"]["delta"] for r in rows]
    findings = [r for r in rows if r["isFinding"]]
    summary = {
        "checked": len(rows),
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
        "styleSwitches": len([r for r in rows if r["styleSwitch"]]),
        "skipped": skipped,
        "elapsedSeconds": round(time.time() - started, 1),
        "rows": rows,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "agreement.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nchecked {len(rows)} activities in {summary['elapsedSeconds']}s")
    if con_deltas:
        print(f"same-tier median vs published: {summary['constrained']['medianDelta']:+.2%}")
        print(f"unconstrained ceiling median:  {summary['unconstrained']['medianDelta']:+.2%}")
        print(f"solver at or below baseline:   "
              f"{summary['constrained']['atOrBelowBaseline']}/{len(rows)}")
    print(f"candidate findings (>= {FINDING_THRESHOLD:.0%} same-tier): {len(findings)}")
    print(f"skipped: {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
