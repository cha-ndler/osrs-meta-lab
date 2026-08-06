"""Pull real combat numbers from the OSRS Wiki's `bucket` store.

Two tables carry everything the solver needs:

* ``infobox_bonuses`` - per item and equipment slot: attack and defence bonuses,
  strength, ranged strength, magic damage, prayer, attack speed, combat style.
* ``infobox_monster`` - hitpoints, defence level, per-style defence bonuses,
  attack speed, size, attributes, and elemental weakness.

Both are cached to disk; a full item dump is ~17k rows and only needs fetching
when the wiki changes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

API = "https://oldschool.runescape.wiki/api.php"
USER_AGENT = (
    "osrs-meta-lab/0.1 (research; contact: 48898494+cha-ndler@users.noreply.github.com)"
)
CACHE = Path(__file__).parent.parent / "cache"
PAGE_SIZE = 5000
DELAY = 0.34

ITEM_FIELDS = (
    "page_name", "equipment_slot", "combat_style", "weapon_attack_speed",
    "stab_attack_bonus", "slash_attack_bonus", "crush_attack_bonus",
    "range_attack_bonus", "magic_attack_bonus",
    "stab_defence_bonus", "slash_defence_bonus", "crush_defence_bonus",
    "range_defence_bonus", "magic_defence_bonus",
    "strength_bonus", "ranged_strength_bonus", "prayer_bonus", "magic_damage_bonus",
)

MONSTER_FIELDS = (
    "page_name", "name", "hitpoints", "defence_level", "attack_speed", "size",
    "stab_defence_bonus", "slash_defence_bonus", "crush_defence_bonus",
    "range_defence_bonus", "magic_defence_bonus",
    "light_range_defence_bonus", "standard_range_defence_bonus",
    "heavy_range_defence_bonus", "flat_armour",
    "elemental_weakness", "elemental_weakness_percent",
    "attribute", "combat_level",
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_last = 0.0


def _bucket(query: str) -> list[dict]:
    global _last
    wait = DELAY - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    r = _session.get(API, params={"action": "bucket", "query": query,
                                  "format": "json", "formatversion": 2}, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload.get("bucket", []) or []


def _dump(table: str, fields: tuple[str, ...]) -> list[dict]:
    select = ",".join(f'"{f}"' for f in fields)
    rows: list[dict] = []
    offset = 0
    while True:
        batch = _bucket(
            f'bucket("{table}").select({select}).limit({PAGE_SIZE}).offset({offset}).run()'
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def _cached(name: str, build) -> Any:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = build()
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _num(value, default=0):
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Stats:
    """Item bonuses and monster defences, keyed by wiki page name."""

    def __init__(self) -> None:
        items = _cached("items", lambda: _dump("infobox_bonuses", ITEM_FIELDS))
        monsters = _cached("monsters", lambda: _dump("infobox_monster", MONSTER_FIELDS))

        self.items: dict[str, list[dict]] = {}
        for row in items:
            name = (row.get("page_name") or "").strip()
            if name:
                self.items.setdefault(name.lower(), []).append(row)

        self.monsters: dict[str, list[dict]] = {}
        for row in monsters:
            name = (row.get("page_name") or "").strip()
            if name:
                self.monsters.setdefault(name.lower(), []).append(row)

        # The public library stores item ids only, so a reverse index is needed
        # to line its gear up with the bonuses table (which is keyed by page).
        ids = _cached("item_ids", lambda: _dump(
            "infobox_item", ("item_id", "item_name", "page_name")))
        self.id_to_page: dict[int, str] = {}
        self.id_to_name: dict[int, str] = {}
        for row in ids:
            page = (row.get("page_name") or "").strip()
            iname = (row.get("item_name") or "").strip()
            raw = row.get("item_id") or []
            if not isinstance(raw, list):
                raw = [raw]
            for value in raw:
                if str(value).isdigit():
                    item_id = int(value)
                    self.id_to_page.setdefault(item_id, page)
                    self.id_to_name.setdefault(item_id, iname or page)

    def page_for_id(self, item_id: int) -> str | None:
        return self.id_to_page.get(int(item_id))

    # -- items -------------------------------------------------------------

    def item(self, name: str) -> dict | None:
        """Best-guess row for an item. Prefers the variant with real bonuses."""
        rows = self.items.get((name or "").lower())
        if not rows:
            return None
        # Charged/current versions carry the higher bonuses; take the strongest
        # row so an uncharged duplicate does not understate the item.
        return max(rows, key=lambda r: sum(
            abs(_num(r.get(f))) for f in ITEM_FIELDS if f.endswith("_bonus")
        ))

    def slot_of(self, name: str) -> str | None:
        row = self.item(name)
        if not row:
            return None
        slot = row.get("equipment_slot")
        if isinstance(slot, list):
            slot = slot[0] if slot else None
        return (slot or "").strip().lower() or None

    def items_in_slot(self, slot: str) -> list[str]:
        out = []
        for name, rows in self.items.items():
            for row in rows:
                s = row.get("equipment_slot")
                if isinstance(s, list):
                    s = s[0] if s else None
                if (s or "").strip().lower() == slot:
                    out.append(name)
                    break
        return out

    # -- monsters ----------------------------------------------------------

    def monster(self, name: str) -> dict | None:
        rows = self.monsters.get((name or "").lower())
        if not rows:
            return None
        # Several bosses have multiple versions (Vorkath awake/asleep, Araxxor
        # phases). Take the toughest, which is what gear is chosen against.
        return max(rows, key=lambda r: (_num(r.get("defence_level")),
                                        _num(r.get("hitpoints"))))

    def monster_versions(self, name: str) -> list[dict]:
        return self.monsters.get((name or "").lower(), [])


def num(value, default=0) -> float:
    return _num(value, default)
