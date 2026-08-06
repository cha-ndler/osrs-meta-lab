"""Parity with the wiki calculator, asserted rather than claimed.

The README's central claim is that this repo does not do its own combat maths -
it asks the calculator and reports the answer. That claim was previously
verified by hand, once, and written down as prose. Prose does not fail when a
submodule bump changes every number in the repository.

These are the calculator's own test values, from its
`src/tests/calc/BasicRolls.test.ts`. Requires the submodule; skipped without it.

Run: python lab/test_parity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from engine import CDN, Data, call_oracle  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got}" + ("" if ok else f" (want {want})"))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    if not CDN.exists():
        print("calculator submodule absent - skipping parity checks")
        return 0

    data = Data()

    # The calculator's own asserted figures for a maxed player with an Abyssal
    # whip. Both sides of the roll are pinned: accuracy and damage can drift
    # independently, and either one alone would invalidate every report.
    whip = call_oracle("Abyssal demon", [{"gear": {"weapon": 4151}, "styleIndex": 0}])[0]
    check("Abyssal whip maxAttackRoll", whip["maxAttackRoll"], 16060)
    check("Abyssal whip maxHit", whip["maxHit"], 24)

    # Prayer has to reach the calculator as a numeric enum member. Passing the
    # name straight through used to crash several frames later reading
    # `.combatStyle` off undefined, so the two spellings are pinned equal.
    gear = {"weapon": 22324, "head": 25177, "body": 24420, "legs": 24421}
    by_name = call_oracle("Vorkath", [{"gear": gear, "prayers": ["PIETY"]}])[0]
    by_value = call_oracle("Vorkath", [{"gear": gear, "prayers": [13]}])[0]
    check("prayer by name matches prayer by enum value",
          round(by_name["dps"], 6), round(by_value["dps"], 6))
    unprayed = call_oracle("Vorkath", [{"gear": gear}])[0]
    check("Piety changes the result", by_name["dps"] > unprayed["dps"], True)

    # A staff's spellcasting style sits behind its melee ones, so an index-based
    # search scores Ice Barrage as a club swing. This pins the lookup that
    # replaced the guess.
    kodai = data.by_id[21006]
    magic_styles = data.styles_for(kodai, "magic")
    check("staff exposes a magic style", bool(magic_styles), True)
    if magic_styles:
        cast = call_oracle("Vorkath", [{
            "gear": {"weapon": 21006}, "styleIndex": magic_styles[0],
            "spell": "Ice Barrage",
        }])[0]
        check("Ice Barrage casts as magic", cast["styleType"], "magic")
        # 34, not the spell's listed 30: the Kodai wand carries 150 magic
        # strength, which is 15%, and 30 x 1.15 truncates to 34. Pinning the
        # equipped figure rather than the base one is deliberate - it is the
        # number that proves the spell and the weapon's bonus both landed.
        check("Ice Barrage max hit with Kodai wand", cast["maxHit"], 34)

    # Wrong ammo scores zero rather than raising, so the issue is the only
    # signal that separates "bad loadout" from "impossible loadout".
    bad = call_oracle("Vorkath", [{"gear": {"weapon": 20997, "ammo": 21944}}])[0]
    check("mismatched ammo is reported", "equipment_slot_ammo_wrong" in bad["issues"], True)

    # Barbarian Assault arrows outclass every real arrow and the calculator
    # accepts them on any bow, so they must not be in the pool at all.
    check("Barbarian Assault arrows excluded from equipment",
          [i for i in data.equipment if i.id in (22227, 22228, 22229, 22230)], [])

    if FAILURES:
        print(f"\n{len(FAILURES)} parity check(s) failed")
        return 1
    print("\nthe oracle agrees with the calculator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
