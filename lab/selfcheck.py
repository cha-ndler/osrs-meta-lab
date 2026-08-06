"""Repository invariants, enforced in CI on every pull request.

These are the things that would quietly invalidate the lab if they changed:
the calculator drifting, the licence position slipping, or vendored source
being copied in where it can rot away from upstream.

Run: python lab/selfcheck.py
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{f' - {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    # A submodule tracking a *branch* would let upstream change every DPS number
    # this repo produces with no diff here. It must be an exact commit.
    out = subprocess.run(
        ["git", "ls-tree", "HEAD", "vendor/osrs-dps-calc"],
        capture_output=True, text=True, cwd=ROOT,
    ).stdout.strip()
    pinned = bool(re.match(r"^160000 commit [0-9a-f]{40}\t", out))
    check("calculator submodule pinned to a commit", pinned,
          out.split()[2][:12] if pinned else repr(out))

    gitmodules = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    check("submodule points at upstream calculator",
          "weirdgloop/osrs-dps-calc" in gitmodules)

    # The calculator is GPL-3.0, so this repo must be too.
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    check("LICENSE is GPL-3.0",
          "GNU GENERAL PUBLIC LICENSE" in licence and "Version 3" in licence)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README credits upstream and states the licence",
          "weirdgloop/osrs-dps-calc" in readme and "GPL-3.0" in readme)

    # Consumed as a dependency, unmodified. Copying it in would change the
    # licence position and let the logic drift from upstream unnoticed.
    # Split so this file does not match its own search terms.
    markers = ("class " + "PlayerVsNPCCalc", "class " + "BaseCalc")
    offenders = []
    for root in ("lab", "oracle"):
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.resolve() == pathlib.Path(__file__).resolve():
                continue
            if path.is_file() and path.suffix in {".ts", ".py"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if any(m in text for m in markers):
                    offenders.append(str(path.relative_to(ROOT)))
    check("no calculator source copied into lab/ or oracle/",
          not offenders, ", ".join(offenders))

    # Mode-restricted gear filtering is what keeps "Corrupted halberd
    # (perfected)" from being the answer to every boss. Losing it silently
    # would make every report wrong but still plausible-looking.
    engine = (ROOT / "lab" / "engine.py").read_text(encoding="utf-8")
    check("mode-restricted gear filter present",
          "EXCLUDED_NAME_MARKERS" in engine and "(perfected)" in engine)

    # Barbarian Assault arrows carry 125 ranged strength against a dragon
    # arrow's 60 and the calculator accepts them on every bow, so without this
    # the ranged answer to every boss is minigame ammunition.
    check("Barbarian Assault ammunition excluded",
          "EXCLUDED_IDS" in engine and all(str(i) in engine for i in (22227, 22230)))

    # Ranking is not invariant to prayers and potions - unprayed, the best melee
    # weapon at Vorkath is the fang; under Piety it is the Dragon hunter lance.
    # Losing the profile would silently answer a different question.
    check("combat profile declared",
          "COMBAT_PROFILES" in engine
          and all(p in engine for p in ("PIETY", "RIGOUR", "AUGURY")))

    # The whole point of scoring through the calculator is that mechanics the
    # raw stats cannot express still count. Ranking weapons by a stat proxy
    # reintroduces exactly the blindness the oracle exists to remove: the Scythe
    # of vitur placed 37th on the old one and so was never scored at all.
    #
    # Checked by parsing rather than by searching for text, because the reason
    # the proxy was removed is written out in the module docstring and a plain
    # substring search finds its own explanation.
    search_src = (ROOT / "lab" / "search.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(search_src))
               if isinstance(n, ast.FunctionDef) and n.name == "weapon_shortlist"), None)
    calls = {getattr(c.func, "id", "") for c in ast.walk(fn) if isinstance(c, ast.Call)} if fn else set()
    check("weapons ranked by the oracle, not a stat proxy", "call_oracle" in calls)

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) failed")
        return 1
    print("\nall repository invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
