"""The trust gate, measured in DPS.

The old version compared attack *styles* and reported 73.7%, which was
meaningless: eight of its ten disagreements were the Scythe of vitur, chosen at
Araxxor for its multi-hit rather than for matching a defence hole.

This compares what actually matters. For each activity the wiki publishes a
setup; that setup and the solver's best are scored through the *same* oracle,
and the delta is reported.

The healthy result is that most published setups sit at or near the optimum.
That is what earns the right to believe the outliers. A solver that beats
consensus everywhere is broken, not brilliant.

Run: python lab/agree.py [--limit N] [--style crush]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine import Data, call_oracle  # noqa: E402
from search import solve, weapon_shortlist  # noqa: E402

ROOT = Path(__file__).parent.parent
BASELINE = ROOT / "baseline" / "data"
REPORTS = ROOT / "reports"

# A finding must clear this margin to be worth your attention.
FINDING_THRESHOLD = 0.02

MELEE_STYLES = ("stab", "slash", "crush")


def baseline_gear(variant: dict, data: Data) -> dict[str, int]:
    """Map the public library's item ids onto calculator slots."""
    mapping = {
        "head": "head", "cape": "cape", "neck": "neck", "ammo": "ammo",
        "weapon": "weapon", "torso": "body", "shield": "shield",
        "legs": "legs", "gloves": "hands", "boots": "feet", "ring": "ring",
    }
    out: dict[str, int] = {}
    for ours, theirs in mapping.items():
        item_id = variant.get("equipment", {}).get(ours)
        if item_id and item_id in data.by_id:
            out[theirs] = item_id
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N activities")
    ap.add_argument("--weapons", type=int, default=14)
    ap.add_argument("--slots", type=int, default=6)
    args = ap.parse_args()

    if not BASELINE.exists():
        print(f"baseline missing at {BASELINE}", file=sys.stderr)
        return 2

    data = Data()
    rows: list[dict] = []
    skipped_no_monster: list[str] = []
    skipped_no_melee: list[str] = []
    started = time.time()

    paths = sorted(BASELINE.glob("*.json"))
    if args.limit:
        paths = paths[: args.limit]

    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        activity = record["activity"]
        monster = data.monster(activity)
        if not monster:
            skipped_no_monster.append(activity)
            continue

        # Melee only. A magic loadout scores near zero without a spell selected
        # and a ranged one needs ammo, so comparing either against a melee solve
        # is apples to oranges - Adamant dragon's magic variant scored 0.15 dps
        # and produced a nonsense "+2961%" finding. Those styles need their own
        # handling before they can be judged.
        variants = []
        for v in record["variants"]:
            weapon_id = v.get("equipment", {}).get("weapon")
            item = data.by_id.get(weapon_id) if weapon_id else None
            if not item:
                continue
            melee = max(item.offence("stab"), item.offence("slash"), item.offence("crush"))
            other = max(item.offence("ranged"), item.offence("magic"))
            if melee > 0 and melee >= other:
                variants.append(v)
        if not variants:
            skipped_no_melee.append(activity)
            continue
        # Score *every* melee variant in every attack style and keep the best.
        # Pages publish budget and mid-tier setups alongside the maxed one, so
        # picking a single variant compares unconstrained best-in-slot against
        # someone's starter gear and manufactures a 20% "finding" every time.
        batch, refs = [], []
        for v in variants:
            gear_v = baseline_gear(v, data)
            if "weapon" not in gear_v:
                continue
            for i in range(5):
                batch.append({"gear": gear_v, "styleIndex": i})
                refs.append(v)
        if not batch:
            continue

        try:
            scored = call_oracle(activity, batch)
        except Exception as exc:  # oracle refused this monster/loadout
            skipped_no_monster.append(f"{activity} (oracle: {str(exc)[:60]})")
            continue
        usable = [s for s in scored if not s.get("error") and s.get("dps")]
        if not usable:
            continue
        base = max(usable, key=lambda s: s["dps"])
        variant = refs[base["i"]]

        style = (base.get("styleType") or "slash").lower()
        if style not in MELEE_STYLES:
            style = "slash"

        best = solve(data, activity, style,
                     weapon_keep=args.weapons, slot_keep=args.slots, passes=1)
        if not best:
            continue
        top = best[0]
        delta = (top.dps - base["dps"]) / base["dps"] if base["dps"] else 0

        rows.append({
            "activity": activity,
            "variant": variant["variant"],
            "baselineDps": round(base["dps"], 4),
            "baselineStyle": base.get("styleName"),
            "solverDps": round(top.dps, 4),
            "solverWeapon": top.weapon,
            "solverStyle": top.style_name,
            "delta": round(delta, 4),
            "isFinding": delta >= FINDING_THRESHOLD,
            "gear": top.gear,
        })
        flag = "FINDING" if delta >= FINDING_THRESHOLD else "       "
        print(f"  {flag} {activity:<30} base {base['dps']:6.3f} -> "
              f"best {top.dps:6.3f} ({delta:+.1%})  {top.weapon}")

    deltas = [r["delta"] for r in rows]
    findings = [r for r in rows if r["isFinding"]]
    summary = {
        "checked": len(rows),
        "medianDelta": round(statistics.median(deltas), 4) if deltas else None,
        "meanDelta": round(statistics.fmean(deltas), 4) if deltas else None,
        "atOrBelowBaseline": len([d for d in deltas if d <= 0]),
        "findings": len(findings),
        "threshold": FINDING_THRESHOLD,
        "skippedNoMonster": skipped_no_monster,
        "skippedNoMeleeVariant": skipped_no_melee,
        "elapsedSeconds": round(time.time() - started, 1),
        "rows": rows,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "agreement.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nchecked {len(rows)} activities in {summary['elapsedSeconds']}s")
    print(f"median delta vs published setup: {summary['medianDelta']:+.2%}"
          if summary["medianDelta"] is not None else "no data")
    print(f"solver at or below baseline: {summary['atOrBelowBaseline']}/{len(rows)}")
    print(f"candidate findings (>= {FINDING_THRESHOLD:.0%}): {len(findings)}")
    print(f"activities with no monster match: {len(skipped_no_monster)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
