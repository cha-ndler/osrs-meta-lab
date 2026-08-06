"""Data layer and the Python side of the oracle bridge.

Equipment and monster stats come from the pinned `osrs-dps-calc` submodule
rather than from my own wiki scrape: 5,395 items and 2,858 monsters, already
normalised and already consumed by the calculator we score with. Using any other
source would risk the search optimising against numbers the oracle disagrees
with.
"""

from __future__ import annotations

import atexit
import json
import re
import subprocess
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).parent.parent
VENDOR = ROOT / "vendor" / "osrs-dps-calc"
CDN = VENDOR / "cdn" / "json"
ORACLE_DIR = ROOT / "oracle"
CACHE = ROOT / "cache"

# Issues the calculator raises that make its own number meaningless. A loadout
# carrying one of these is not a bad loadout, it is an unanswered question, and
# it must leave the search rather than lose on DPS.
BLOCKING_ISSUES = frozenset({
    "equipment_slot_ammo_missing",
    "equipment_slot_ammo_wrong",
    "equipment_slot_weapon_wrong_monster",
    "spell_wrong_weapon",
    "spell_wrong_monster",
})

# Issues that leave the number usable but incomplete. These must reach the
# report rather than be silently dropped: "7.4 dps" and "7.4 dps, ignoring your
# set effect" look identical once the warning is gone.
WARNING_ISSUES = frozenset({
    "equipment_slot_body_unsupported_set_effect",
    "pvm_results_weapon_unsupported_spec",
    "monster_overall_unique_effects",
    "equipment_slot_hands_effect",
})


def blocking(result: dict) -> bool:
    return bool(BLOCKING_ISSUES.intersection(result.get("issues") or ()))


def warnings_in(result: dict) -> list[str]:
    return sorted(WARNING_ISSUES.intersection(result.get("issues") or ()))

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


# The combat setup every solve is scored under.
#
# Ranking is not invariant to this. Unprayed, the best melee weapon at Vorkath
# is Osmumten's fang; under Piety it is the Dragon hunter lance, and the Scythe
# of vitur overtakes the Ghrazi rapier. Prayer scales attack and strength by
# different factors, which moves where the accuracy-versus-damage trade-off
# sits, so solving without prayers is not a conservative simplification - it
# answers a different question, about a game nobody plays.
#
# These are the boosts an endgame player actually brings to a boss, not the
# theoretical maximum: super combat rather than an overload, which is
# raid-specific, and a ranging potion rather than the Colosseum-only super.
# Every report states the profile it was solved under.
COMBAT_PROFILES: dict[str, dict[str, list[str]]] = {
    "stab":   {"prayers": ["PIETY"],  "potions": ["SUPER_COMBAT"]},
    "slash":  {"prayers": ["PIETY"],  "potions": ["SUPER_COMBAT"]},
    "crush":  {"prayers": ["PIETY"],  "potions": ["SUPER_COMBAT"]},
    "ranged": {"prayers": ["RIGOUR"], "potions": ["RANGING"]},
    "magic":  {"prayers": ["AUGURY"], "potions": ["SATURATED_HEART"]},
}


def profile_for(style: str) -> dict[str, list[str]]:
    return COMBAT_PROFILES[style]


def describe_profile(style: str) -> str:
    p = COMBAT_PROFILES[style]
    pretty = lambda names: ", ".join(n.replace("_", " ").title() for n in names)  # noqa: E731
    return f"{pretty(p['prayers'])} + {pretty(p['potions'])}"


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

# Barbarian Assault ammunition: Bullet, Field, Blunt and Barbed arrows.
#
# Same problem as the LMS gear above, but invisible to a name filter, because
# nothing about "Barbed arrow" says minigame. They carry 125 ranged strength -
# against 60 for a dragon arrow, the best arrow in the game - and the calculator
# lists them as valid for every bow, so an unfiltered ranged search staples
# Barbarian Assault arrows to a Twisted bow at every boss. They are handed out
# free inside the minigame and cannot leave it.
#
# Excluded by id rather than by name: these names are generic enough that a
# future real item could reuse one, and silently dropping it would be worse than
# the problem being fixed.
EXCLUDED_IDS = frozenset({
    22227,  # Bullet arrow
    22228,  # Field arrow
    22229,  # Blunt arrow
    22230,  # Barbed arrow
})


def is_standard_item(name: str, item_id: int = -1) -> bool:
    if item_id in EXCLUDED_IDS:
        return False
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
            if not is_standard_item(e.get("name", ""), e.get("id", -1)):
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

    @cached_property
    def aliases(self) -> dict[int, int]:
        """Cosmetic variant id -> the item it is mechanically identical to.

        846 entries covering ornament kits, charge states and league variants.
        Without this a shortlist spends eight of its slots on Blade of saeldor
        (c) (Amlodd), (Cadarn), (Crwys) ... which are one weapon wearing seven
        different hats.

        The calculator's own `getCanonicalItemId` is not reused here: it does
        `v.includes(itemId)` against what is a flat int-to-int map, so it would
        never match. The data is unambiguous, so it is read directly.
        """
        raw = json.loads((CDN / "equipment_aliases.json").read_text(encoding="utf-8"))
        return {int(k): int(v) for k, v in raw.items()}

    def canonical(self, item_id: int) -> int:
        return self.aliases.get(item_id, item_id)

    def dedupe(self, items: list[Item]) -> list[Item]:
        """Keep one of each mechanically distinct item, preserving order.

        The survivor is the canonical id where the group contains it, so a
        report says "Scythe of vitur" rather than naming whichever ornamented
        variant happened to appear first in the file. They are the same weapon
        by upstream's own alias table, but only one of them is the one people
        mean.
        """
        groups: dict[int, Item] = {}
        order: list[int] = []
        for item in items:
            key = self.canonical(item.id)
            if key not in groups:
                groups[key] = item
                order.append(key)
            elif item.id == key:
                groups[key] = item          # the plain item outranks a variant
        return [groups[k] for k in order]

    @cached_property
    def mechanically_special(self) -> set[str]:
        """Items the calculator has explicit logic for, by name.

        Ranking a slot by its raw offensive and strength bonuses assumes DPS is
        monotonic in those two numbers. For most armour it is. For anything with
        a set effect it is not: crystal armour carries unremarkable stats and
        raises Bow of faerdhinen damage by 30%, void trades stats for a
        multiplier, Dharok's scales off missing health. Ranked on raw bonuses
        alone, none of them place, so none of them are ever scored - and they
        are precisely the overlooked-meta candidates this repo exists to find.

        Rather than hand-maintain a list that would rot with every update, read
        it off the calculator: any equipment name it mentions as a string
        literal is a name it branches on. Over-inclusion is harmless here, since
        this only ever widens the candidate pool.
        """
        names: set[str] = set()
        lib = VENDOR / "src" / "lib"
        # Both quote styles matter: names containing an apostrophe are written
        # with double quotes, which is every Barrows and Inquisitor's piece.
        patterns = (r"'([^'\\\n]{3,60})'", r'"([^"\\\n]{3,60})"')
        for path in lib.rglob("*.ts"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in patterns:
                names.update(re.findall(pattern, text))
        known = {i.name for i in self.equipment}
        found = names & known
        # An upstream refactor that moved this logic elsewhere would silently
        # shrink the pool back to raw-stat ranking and quietly undo the fix, so
        # fail loudly instead. The pinned commit yields well over 200.
        if len(found) < 100:
            raise OracleError(
                f"only {len(found)} special items found in {lib} - the "
                "extraction has drifted from upstream and recall is degraded"
            )
        return found

    def in_slot(self, slot: str) -> list[Item]:
        return [i for i in self.equipment if i.slot == slot]

    @cached_property
    def spells(self) -> list[str]:
        """Damage-dealing spells, strongest first.

        A staff with no spell selected does not attack for less, it attacks for
        nothing, and a magic search that forgets to choose one concludes the
        style is worthless - which is exactly what the earlier melee-only
        restriction was built on top of. Powered staves supply their own attack
        and ignore this list; everything else needs a member of it.

        Elemental weaknesses mean the strongest spell is not always the best
        one, so the whole list is offered to the search rather than just the top
        of it.
        """
        raw = json.loads((CDN / "spells.json").read_text(encoding="utf-8"))
        damage = [s for s in raw if (s.get("max_hit") or 0) > 0]
        damage.sort(key=lambda s: -s["max_hit"])
        return [s["name"] for s in damage]

    @cached_property
    def meta(self) -> dict:
        """Style and ammo tables, fetched from the oracle once and cached.

        These describe the rules rather than any particular fight, so they are
        identical for every monster and there is no reason to pay for them per
        solve. Delete `cache/meta.json` after bumping the submodule.
        """
        cached = CACHE / "meta.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        payload = _run_oracle({"dump": "meta"})
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def style_entries(self, item: Item, style: str) -> list[dict]:
        """An item's combat styles that attack with `style`.

        A style's position is a per-category accident - a staff lists three
        melee options before its spellcasting one - so these are looked up, not
        guessed. More than one can match, because the same attack type is
        usually offered at both an accurate and an aggressive stance, and which
        of the two wins is a property of the fight rather than of the weapon.
        """
        table = self.meta["styles"].get(item.category or "", [])
        return [s for s in table if s["type"] == style]

    def styles_for(self, item: Item, style: str) -> list[int]:
        return [s["index"] for s in self.style_entries(item, style)]

    def needs_spell(self, item: Item, entry: dict) -> bool:
        """Whether this weapon and stance should be offered a spell to cast.

        Every weapon in the game can manual-cast, so 32 of 33 categories list a
        magic style and a naive search offers all 49 damage spells to all 1,521
        weapons - a minute per monster to discover that casting Fire Surge off a
        crossbow is bad. It is bad for a reason that needs no scoring: a weapon
        with no magic attack bonus casts less accurately than any staff, and the
        spell's damage is the same either way, so it is dominated outright.

        Autocast stances are the genuine article and always qualify. Manual Cast
        qualifies only if the weapon brings some magic attack bonus of its own.
        """
        if "autocast" in (entry.get("stance") or "").lower():
            return True
        return item.offence("magic") > 0

    def ammo_for(self, weapon: Item) -> list[int] | None:
        """Ammo ids this weapon accepts, or None if it needs none.

        A bow holding bolts is not a weak loadout, it is a zero: the calculator
        drops the ammo's bonuses and the shot scores nothing. Pairing has to be
        constrained up front rather than discovered by scoring.
        """
        return self.meta["ammo"].get(str(weapon.id))

    @cached_property
    def activities(self) -> dict[str, list[dict]]:
        """Activity -> the monsters it is actually fought against.

        See lab/activities.json. An activity present with an empty list has been
        looked at and has no DPS answer, which is different from one that is
        missing because nobody got to it.
        """
        raw = json.loads((ROOT / "lab" / "activities.json").read_text(encoding="utf-8"))
        self._reasons = {k: v for k, v in (raw.get("_reasons") or {}).items()
                         if not k.startswith("_")}
        return {k: v for k, v in raw.items() if not k.startswith("_")}

    def skip_reason(self, activity: str) -> str | None:
        """Why an activity has no targets, if that was a decision rather than a gap."""
        self.activities  # noqa: B018 - populates _reasons
        return self._reasons.get(activity)

    def targets(self, activity: str) -> list[dict]:
        """Every monster an activity should be solved against.

        An activity naming a monster directly is its own target; everything else
        comes from the mapping. Raids are several fights wearing one name and a
        single answer for "the Chambers of Xeric" would be meaningless, so they
        expand to one target per boss.
        """
        if activity in self.activities:
            return self.activities[activity]
        return [{"monster": activity}] if self.monster(activity) else []

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
        """Candidates for a non-weapon slot.

        Three sources, unioned: the top of the style's offensive ranking, the
        top of its strength ranking, and every item the calculator has explicit
        logic for. The first two cover armour whose value is its raw stats,
        where DPS genuinely is monotonic in those numbers. The third covers set
        effects, which are invisible to a stat ranking - see
        `mechanically_special`.

        Weapons are not shortlisted this way. They are scored in full; see
        `search.weapon_shortlist`.
        """
        items = self.dedupe(self.in_slot(slot))
        top_off = sorted(items, key=lambda i: -i.offence(style))[:keep]
        top_str = sorted(items, key=lambda i: -i.strength(style))[:keep]
        special = [i for i in items if i.name in self.mechanically_special]
        seen, out = set(), []
        for item in top_off + top_str + special:
            if item.id not in seen:
                seen.add(item.id)
                out.append(item)
        return out


def call_oracle(monster: str, loadouts: list[dict], version: str | None = None,
                timeout: int = 600, style: str | None = None,
                inputs: dict | None = None) -> list[dict]:
    """Score a batch of loadouts through the wiki calculator.

    Passing `style` applies that style's combat profile to the whole batch. It
    is a batch-level field rather than a per-loadout one because a full weapon
    scan sends over a thousand loadouts and would otherwise repeat the same
    prayers and potions in every one of them.
    """
    payload: dict = {"monster": monster, "loadouts": loadouts}
    if version:
        payload["version"] = version
    if style:
        payload["profile"] = profile_for(style)
    if inputs:
        payload["inputs"] = inputs
    return _run_oracle(payload, timeout=timeout)["results"]


class _OracleSession:
    """One long-lived oracle process, shared by every call.

    Coordinate ascent is sequential by construction - the next slot cannot be
    chosen until the last is settled - so a single solve makes around twenty
    calls. Spawning Node and tsx for each of them dominated everything else: a
    Vorkath solve took about forty seconds, of which the arithmetic was maybe
    three. The process is started on first use and reused thereafter.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def _start(self) -> subprocess.Popen:
        # `shell=True` with a single string: on Windows `npx` is a .cmd shim
        # that CreateProcess will not launch directly. The command is a fixed
        # literal, so there is nothing here for a shell to interpolate.
        return subprocess.Popen(
            "npx tsx --tsconfig ./tsconfig.json ./oracle.ts --serve",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=ORACLE_DIR,
            shell=True,
            bufsize=1,
        )

    def send(self, payload: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = self._start()
        proc = self._proc
        assert proc.stdin and proc.stdout
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise OracleError(f"oracle died: {exc}") from exc
        if not line:
            stderr = ""
            if proc.stderr:
                stderr = proc.stderr.read()[:2000]
            self.close()
            raise OracleError(f"oracle produced no output. {stderr}")
        try:
            out = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OracleError(f"bad oracle output: {line[:400]}") from exc
        if out.get("error") and "results" not in out:
            raise OracleError(out["error"])
        return out

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None


_SESSION = _OracleSession()
atexit.register(_SESSION.close)


def _run_oracle(payload: dict, timeout: int = 600) -> dict:
    return _SESSION.send(payload)
