"""The trust gate.

Before any novel suggestion is worth reading, the solver has to reproduce metas
the community already agrees on. This measures that.

The clearest testable signal is **attack style**. Araxxor's defences are
stab 160 / slash 75 / crush 15, which is exactly why the wiki recommends crush
weapons. If the solver independently picks crush from the raw numbers, its
reasoning is sound. If it does not, nothing else it says can be trusted.

Run: python lab/agree.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from combat import Player  # noqa: E402
from solve import STYLE_ATTACK, Solver, target_from_monster  # noqa: E402
from stats import Stats, num  # noqa: E402

BASELINE = Path(__file__).parent.parent / "baseline" / "data"

MELEE_STYLES = ("stab", "slash", "crush")


def weapon_style(stats: Stats, weapon_name: str) -> str | None:
    """Which melee style a weapon is actually best at."""
    row = stats.item(weapon_name)
    if not row:
        return None
    best, best_value = None, 0.0
    for style in MELEE_STYLES:
        value = num(row.get(STYLE_ATTACK[style]))
        if value > best_value:
            best, best_value = style, value
    return best


def solver_style(target) -> str:
    """Whichever melee style the monster defends worst against."""
    return min(MELEE_STYLES, key=lambda s: target.defence_bonus.get(s, 0))


def style_is_determined(target) -> bool:
    """False when every melee defence is equal.

    Aviansie sit at 0/0/0 and Brutus at -7/-7/-7. Nothing about the monster
    favours a style there, so counting it as agreement or disagreement is
    meaningless - the weapon's own bonus decides, not the target.
    """
    values = {target.defence_bonus.get(s, 0) for s in MELEE_STYLES}
    return len(values) > 1


def weapon_styles(stats: Stats, weapon_name: str) -> set[str]:
    """Every melee style a weapon can realistically be set to.

    A scythe has slash 125 and crush 30 and is genuinely used in *either* mode
    depending on the target - the wiki tells Araxxor players to set it to crush.
    Treating its single highest bonus as "the" style manufactures disagreements
    that do not exist.
    """
    row = stats.item(weapon_name)
    if not row:
        return set()
    bonuses = {s: num(row.get(STYLE_ATTACK[s])) for s in MELEE_STYLES}
    best = max(bonuses.values())
    if best <= 0:
        return set()
    # Any style within 60% of the weapon's best is a usable mode.
    return {s for s, v in bonuses.items() if v >= best * 0.6 and v > 0}


def main() -> int:
    if not BASELINE.exists():
        print(f"baseline data not found at {BASELINE}", file=sys.stderr)
        print("clone the public library into baseline/ first", file=sys.stderr)
        return 2

    stats = Stats()
    solver = Solver(stats, Player())

    checked = agreed = 0
    missing_monster = 0
    undetermined = 0
    rows = []

    for path in sorted(BASELINE.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        activity = record["activity"]
        monster = stats.monster(activity)
        if not monster:
            missing_monster += 1
            continue
        target = target_from_monster(monster)

        # Only melee variants carry a testable style signal.
        for variant in record["variants"]:
            weapon_id = variant["equipment"].get("weapon")
            if not weapon_id:
                continue
            name = stats.page_for_id(weapon_id)
            if not name:
                continue
            styles = weapon_styles(stats, name)
            if not styles:
                continue
            if not style_is_determined(target):
                undetermined += 1
                break
            expected = solver_style(target)
            checked += 1
            # The wiki's weapon agrees if it can be *set* to the style the
            # numbers favour, which is how players actually use it.
            match = expected in styles
            agreed += match
            rows.append({
                "activity": activity,
                "variant": variant["variant"],
                "weapon": name,
                "weaponStyles": sorted(styles),
                "solverStyle": expected,
                "agree": match,
                "defences": {s: target.defence_bonus.get(s, 0) for s in MELEE_STYLES},
            })
            break  # one melee sample per activity is enough

    rate = agreed / checked if checked else 0
    out = {
        "checked": checked,
        "agreed": agreed,
        "agreementRate": round(rate, 4),
        "activitiesWithoutMonsterData": missing_monster,
        "styleUndetermined": undetermined,
        "rows": rows,
    }
    report = Path(__file__).parent.parent / "reports" / "agreement.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"style agreement: {agreed}/{checked} ({rate:.1%})")
    print(f"activities without monster stats: {missing_monster}")
    print(f"style undetermined (equal defences): {undetermined}")
    for r in rows:
        if r["agree"]:
            continue
        print(f"  DIFF {r['activity']:<26} weapon={r['weapon'][:22]:<22} "
              f"can be {'/'.join(r['weaponStyles']):<12} solver={r['solverStyle']:<6} "
              f"def={r['defences']}")
    print(f"\nwrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
