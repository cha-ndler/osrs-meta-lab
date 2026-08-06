"""Detect newly released monsters and solve them before consensus forms.

The calculator's `monsters.json` is regenerated from the wiki, so a new boss
appears there as soon as it has stats - typically days before a strategy page
exists. Diffing it against a committed snapshot is the cheapest way to get a
loadout first.

Run: python lab/watch.py [--solve] [--update-snapshot]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import report  # noqa: E402
from engine import COMBAT_PROFILES, Data  # noqa: E402
from search import solve  # noqa: E402

ROOT = Path(__file__).parent.parent
SNAPSHOT = ROOT / "snapshots" / "monsters.json"
REPORTS = ROOT / "reports" / "new"


def key(monster: dict) -> str:
    return f"{monster.get('id')}|{monster.get('name')}|{monster.get('version') or ''}"


def load_snapshot() -> set[str]:
    if not SNAPSHOT.exists():
        return set()
    return set(json.loads(SNAPSHOT.read_text(encoding="utf-8")))


def write_snapshot(keys: set[str]) -> None:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(sorted(keys), indent=0), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--solve", action="store_true", help="solve anything new")
    ap.add_argument("--update-snapshot", action="store_true")
    ap.add_argument("--weapons", type=int, default=12)
    ap.add_argument("--slots", type=int, default=6)
    args = ap.parse_args()

    data = Data()
    current = {key(m): m for m in data.monsters}
    known = load_snapshot()

    if not known:
        print(f"no snapshot yet - seeding with {len(current)} monsters")
        write_snapshot(set(current))
        return 0

    new_keys = sorted(set(current) - known)
    print(f"{len(new_keys)} new monster entries since last snapshot")

    if args.solve and new_keys:
        REPORTS.mkdir(parents=True, exist_ok=True)
        for k in new_keys[:20]:
            monster = current[k]
            name = monster.get("name")
            if not (monster.get("skills") or {}).get("hp"):
                continue
            print(f"  solving {name} ...")
            results = {}
            for style in COMBAT_PROFILES:
                try:
                    results[style] = solve(
                        data, name, style,
                        weapon_keep=args.weapons, slot_keep=args.slots, passes=1)
                except Exception as exc:
                    print(f"    {style}: {str(exc)[:80]}")
            # Styles that produced nothing are dropped before picking a winner:
            # a style can come back empty because every candidate was rejected,
            # and "best of nothing" would still name it.
            results = {s: r for s, r in results.items() if r}
            best_style = max(results, key=lambda s: results[s][0].dps, default=None)
            if best_style:
                path = REPORTS / f"{report.slug(name)}.md"
                path.write_text(
                    report.new_monster(monster, best_style, results[best_style]),
                    encoding="utf-8")
                print(f"    wrote {path.relative_to(ROOT)}")

    for k in new_keys[:25]:
        print(f"   NEW {k}")

    if args.update_snapshot:
        write_snapshot(set(current))
        print("snapshot updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
