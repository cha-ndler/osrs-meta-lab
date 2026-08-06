# osrs-meta-lab

**Private research. Output here is speculative and is not published anywhere.**

An attempt to derive OSRS combat metas from first principles — real item
bonuses and real monster defences — rather than from community consensus, and
to measure how far that can be trusted.

The public, wiki-sourced library lives at
[cha-ndler/osrs-bank-tag-layouts](https://github.com/cha-ndler/osrs-bank-tag-layouts).
This repo reads it as a baseline. **Nothing flows back the other way.**

## The idea

The wiki exposes the game's actual numbers through `action=bucket`:

- `infobox_bonuses` — every item's attack/defence bonuses, strength, ranged
  strength, magic damage, prayer bonus, attack speed, combat style.
- `infobox_monster` — hitpoints, defence level, per-style defence bonuses,
  attack speed, size, attributes, `elemental_weakness` and its percentage.

So gear can be scored with the real DPS formulas instead of guessed at. The
solver decides; a language model only ever explains the result afterwards and
never changes the ranking.

## Trust gate — read this before believing anything

Novel suggestions are worthless unless the model first reproduces metas that
are already settled. `lab/agree.py` measures that.

**Current: 73.7% style agreement (28 of 38 testable activities).**

Two adjustments were needed to make the metric honest at all:

- **13 activities have equal defences across all three melee styles**
  (Aviansie 0/0/0, Brutus −7/−7/−7). Nothing about the target favours a style,
  so scoring them as agreement or disagreement is meaningless. They are
  excluded, not counted as wins.
- A weapon is credited if it can be *set* to the favoured style, since that is
  how players use them.

### What the remaining disagreements actually show

Eight of the ten are the **Scythe of vitur**. Its slash bonus is 125 and crush
only 30, so a style comparison says "slash" — but the wiki tells Araxxor players
to set it to crush, and it stays best-in-slot there regardless because it hits
three times against large targets.

That is the real finding: **style agreement is a weak proxy for correctness.**
Top-tier weapons are picked for mechanics (the scythe's multi-hit, Arclight's
demon multiplier), not for matching a defence hole. The solver's own choice of
style is defensible game theory; the metric is what's crude.

The honest state: the data foundation and combat maths are sound and the
harness runs end to end, but **the agreement number is not yet strong enough to
trust novel suggestions.** The next step is comparing full DPS between the
solver's best loadout and the wiki's, rather than comparing attack styles.

## Deliberate gaps

`modifiers.py` models scythe multi-hit, twisted bow scaling, Osmumten's fang
accuracy re-roll, dragonbane, slayer/salve/void stacking and elemental weakness.

Weapons whose damage depends on state the solver does not model — Dharok's
(missing HP), blowpipes (dart-dependent), wilderness weapons, pure special-attack
weapons — are **excluded from ranking and reported**, never scored with the plain
formula. A wrong answer at the top of a report is worse than an admitted gap.

## Layout

```
lab/
  stats.py       wiki bucket -> item bonuses, monster defences, id->name (cached)
  combat.py      effective levels, accuracy, max hit, DPS
  modifiers.py   special weapons and gear multipliers; the exclusion list
  solve.py       coordinate-ascent gear optimisation vs a target
  agree.py       the trust gate
baseline/        shallow clone of the public library (gitignored)
reports/         generated output
cache/           wiki dumps (gitignored)
```

## Running it

```bash
pip install requests
git clone --depth 1 https://github.com/cha-ndler/osrs-bank-tag-layouts.git baseline
python lab/agree.py
```

First run downloads ~17k item rows and the monster table, then caches them.
