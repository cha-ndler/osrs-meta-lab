"""Data layer and the Python side of the oracle bridge.

Equipment and monster stats come from the pinned `osrs-dps-calc` submodule
rather than from my own wiki scrape: 5,395 items and 2,858 monsters, already
normalised and already consumed by the calculator we score with. Using any other
source would risk the search optimising against numbers the oracle disagrees
with.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).parent.parent
VENDOR = ROOT / "vendor" / "osrs-dps-calc"
CDN = VENDOR / "cdn" / "json"
ORACLE_DIR = ROOT / "oracle"

SLOTS = ("head", "cape", "neck", "ammo", "weapon", "body",
         "shield", "legs", "hands", "feet", "ring")

# Which offensive stat each style reads, and which strength stat scales it.
STYLE_OFFENSIVE = {
    "stab": "stab", "slash": "slash", "crush": "crush",
    "ranged": "ranged", "magic": "magic",
}
STYLE_STRENGTH = {
    "stab": "str", "slash": "str", "crush": "str",
    "ranged": "ranged_str", "magic": "magic_str",
}


class OracleError(RuntimeError):
    pass


# Gear that only exists inside Last Man Standing, Deadman or Bounty Hunter.
# These carry huge stats and no acquisition cost inside their own mode, so an
# unfiltered search hands back "Corrupted halberd (perfected)" as the answer to
# almost every boss - technically true, entirely useless. The restriction is
# encoded in the item name rather than the version field.
EXCLUDED_NAME_MARKERS = (
    "(deadman mode)",
    "(bh)",
    "(last man standing)",
    # LMS gear ships in basic/attuned/perfected tiers and exists only there.
    "(basic)",
    "(attuned)",
    "(perfected)",
)
EXCLUDED_NAME_PREFIXES = (
    "corrupted ",   # LMS-only variants of the Vesta/Statius set
)


def is_standard_item(name: str) -> bool:
    lowered = (name or "").lower()
    if any(marker in lowered for marker in EXCLUDED_NAME_MARKERS):
        return False
    return not any(lowered.startswith(p) for p in EXCLUDED_NAME_PREFIXES)


@dataclass(frozen=True)
class Item:
    id: int
    name: str
    version: str
    slot: str
    speed: int
    category: str
    two_handed: bool
    offensive: dict
    bonuses: dict

    def offence(self, style: str) -> float:
        return float(self.offensive.get(STYLE_OFFENSIVE[style], 0) or 0)

    def strength(self, style: str) -> float:
        return float(self.bonuses.get(STYLE_STRENGTH[style], 0) or 0)


class Data:
    def __init__(self) -> None:
        if not CDN.exists():
            raise FileNotFoundError(
                f"{CDN} missing - run: git submodule update --init --depth 1"
            )

    @cached_property
    def equipment(self) -> list[Item]:
        raw = json.loads((CDN / "equipment.json").read_text(encoding="utf-8"))
        out = []
        for e in raw:
            if not is_standard_item(e.get("name", "")):
                continue
            out.append(Item(
                id=e.get("id", -1),
                name=e.get("name", ""),
                version=e.get("version", "") or "",
                slot=(e.get("slot") or "").lower(),
                speed=e.get("speed") or 4,
                category=e.get("category") or "",
                two_handed=bool(e.get("isTwoHanded")),
                offensive=e.get("offensive") or {},
                bonuses=e.get("bonuses") or {},
            ))
        return out

    @cached_property
    def monsters(self) -> list[dict]:
        return json.loads((CDN / "monsters.json").read_text(encoding="utf-8"))

    @cached_property
    def by_id(self) -> dict[int, Item]:
        out: dict[int, Item] = {}
        for item in self.equipment:
            out.setdefault(item.id, item)
        return out

    def in_slot(self, slot: str) -> list[Item]:
        return [i for i in self.equipment if i.slot == slot]

    def monster(self, name: str) -> dict | None:
        matches = [m for m in self.monsters if m.get("name") == name]
        if not matches:
            return None
        # Several bosses ship multiple versions; take the toughest, which is
        # what gear gets chosen against.
        return max(matches, key=lambda m: (
            (m.get("skills") or {}).get("def", 0),
            (m.get("skills") or {}).get("hp", 0),
        ))

    def shortlist(self, slot: str, style: str, keep: int = 12) -> list[Item]:
        """Top candidates for a slot.

        DPS rises monotonically with both the style's offensive bonus and the
        matching strength bonus, so the optimum sits near the top of one of
        those two rankings. Taking the union keeps the answer while making the
        search finish - unpruned this is 1,568 weapons against 1,022 heads and
        so on.
        """
        items = self.in_slot(slot)
        top_off = sorted(items, key=lambda i: -i.offence(style))[:keep]
        top_str = sorted(items, key=lambda i: -i.strength(style))[:keep]
        seen, out = set(), []
        for item in top_off + top_str:
            if item.id not in seen:
                seen.add(item.id)
                out.append(item)
        return out


def call_oracle(monster: str, loadouts: list[dict], version: str | None = None,
                timeout: int = 600) -> list[dict]:
    """Score a batch of loadouts through the wiki calculator."""
    payload = {"monster": monster, "loadouts": loadouts}
    if version:
        payload["version"] = version

    proc = subprocess.run(
        ["npx", "tsx", "--tsconfig", "./tsconfig.json", "./oracle.ts"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ORACLE_DIR,
        timeout=timeout,
        shell=True,
    )
    if proc.returncode != 0:
        raise OracleError(proc.stderr.strip()[:2000])
    try:
        return json.loads(proc.stdout)["results"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise OracleError(f"bad oracle output: {proc.stdout[:400]}") from exc
