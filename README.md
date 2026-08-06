# osrs-meta-lab

**Experimental.** Gear optimisation for Old School RuneScape, scored by the OSRS
Wiki's own DPS calculator. Output is a starting point for investigation, not a
recommendation.

Companion to the wiki-sourced bank tag library at
[cha-ndler/osrs-bank-tag-layouts](https://github.com/cha-ndler/osrs-bank-tag-layouts),
which this reads as a baseline. Nothing flows back the other way.

## How it works

```
python lab/search.py ──▶ batched loadouts (JSON)
                          │
                          ▼
                    oracle/oracle.ts ──▶ vendor/osrs-dps-calc PlayerVsNPCCalc
                          │
                          ▼
                    dps / accuracy / max hit ──▶ ranking, reports
```

Earlier versions hand-rolled the combat maths and had to **exclude ~25 weapons**
it could not model — Dharok's, blowpipes, claws, godswords — several of which are
genuinely best-in-slot somewhere. That is gone. The
[wiki's calculator](https://github.com/weirdgloop/osrs-dps-calc) does all
scoring now, so scythe multi-hits, twisted bow scaling, Osmumten's fang re-rolls
and raid scaling are all handled correctly.

### Parity is verified, not assumed

`lab/test_parity.py` reproduces the calculator's own test values: `Abyssal whip`
at level 99 returns `maxAttackRoll 16060` and `maxHit 24`, the precise figures
asserted in their `src/tests/calc/BasicRolls.test.ts`. It runs in CI, along with
checks that prayers, spell selection and ammunition all reach the calculator
intact. It used to be a sentence in this README, which is not a thing that fails
when a submodule bump changes every number here.

## Nothing is ranked by a stand-in for DPS

The search scores every weapon the style can use, for real, in one batched call.

It used to pick which weapons deserved scoring with
`(str + 64) * (off + 64) / speed`, which is blind to every mechanic that makes a
weapon worth noticing. The Scythe of vitur placed **37th** on that proxy while
callers kept 12 to 14, so the strongest melee weapon in the game against a large
target was never once scored. Weapons whose value is a mechanic are exactly the
overlooked metas this repo exists to find — the proxy was pruning the answer.

The same blindness applied to armour: crystal, void and Dharok's carry
unremarkable stats and multiply damage. The candidate pool now includes every
item the calculator has explicit logic for, read off its source rather than
hand-listed, so it stays current when the submodule moves.

Scoring everything is affordable because the bridge batches and holds one
process open: 1,312 melee loadouts in under three seconds, and a full Vorkath
solve in four.

## What the numbers mean

`lab/agree.py` solves each target twice.

| | drawn from | what it measures |
|---|---|---|
| **same-tier** | only items the wiki's own setups for that activity name | the finding gate |
| **ceiling** | the whole game | the tier gap, for context |

The same-tier median against published setups is **+0.00%**. Constrained to the
gear the wiki already names, the solver reproduces consensus exactly. That is
the healthy result, and it is what earns the right to believe an outlier.

An earlier version reported a median **+22%** "in the solver's favour" and
treated it as signal. It was three faults and no discovery:

- **Tier.** The solver searched the whole game while wiki pages publish
  affordable mid-tier gear. Most of the gap was Torva beating a guide written
  for someone with a fire cape.
- **Prayers.** The baseline was scored with no prayers while the solver ran
  under Piety, because the combat profile was passed as a field the oracle
  ignored. Unrecognised fields are now reported rather than dropped.
- **Shields.** A two-hander occupies the shield slot, but the oracle cleared it
  on *encountering* the weapon, which only works if the shield was placed first.
  Equipment is naturally listed shield-after-weapon, so every published
  two-handed setup was scored with a free Elidinis' ward.

## Solved under a stated combat profile

Every report says which prayers and potions its numbers assume, because ranking
is not invariant to them. Unprayed, the best melee weapon at Vorkath is
Osmumten's fang; under Piety it is the Dragon hunter lance, and the Scythe of
vitur overtakes the Ghrazi rapier. Solving without prayers is not a conservative
simplification — it answers a different question, about a game nobody plays.

The defaults are what an endgame player actually brings, not the theoretical
maximum: Piety and a super combat for melee, Rigour and a ranging potion for
ranged, Augury and a saturated heart for magic. See `COMBAT_PROFILES` in
`lab/engine.py`.

## All five styles, and the raids

Melee, ranged and magic all solve. Magic was never unmodellable — it needed the
spellcasting style, which sits *behind* a staff's three melee options, so
selecting index 0 scored Ice Barrage as a club swing. Ammunition is constrained
to what each weapon accepts, because a bow holding bolts does not score badly,
it scores zero.

`lab/activities.json` maps an activity onto the monsters it is actually fought
against. Thirty-four of the ninety-nine baseline entries are encounters rather
than monsters, and they were the most interesting content in the game: every
raid, the Inferno, the Colosseum, Barrows, the Gauntlet. Coverage is now **81
activities and 134 solve targets**, against 65 and 65 before. A raid expands to
one target per boss — a single answer for "the Chambers of Xeric" would be
meaningless.

Activities with no targets record *why*, and keep two different facts apart:
Wintertodt has no DPS answer and never will, whereas Hespori (Echo) is simply
absent from the calculator's data and becomes solvable when that changes. The
Abyss and Zalcano are listed there too — the calculator has stats for both, but
every setup the wiki publishes for them is built around a pickaxe, so a DPS
comparison measures nothing. Solving the Abyss anyway produced the first false
finding this gate ever emitted: an Infernal pickaxe, +16.4%.

### Known limitations

- **No cost or obtainability.** Raw DPS ignores that a set costs 1.2b.
- **No fight mechanics.** Phases, movement, downtime and supply use are not
  modelled. A loadout that wins on paper can lose on a real kill.
- **Mode-restricted gear is filtered out of the *search*.** Last Man Standing,
  Deadman and Bounty Hunter items carry huge stats with no acquisition cost
  inside their own mode. Unfiltered, "Corrupted halberd (perfected)" wins nearly
  every boss. Barbarian Assault arrows are excluded for the same reason and were
  harder to catch: nothing in the name says minigame, and at 125 ranged strength
  against a dragon arrow's 60 they beat every real arrow in the game.

  Published setups are read through the *unfiltered* table, because a page
  naming an item for an activity is evidence the player has it there. The
  Gauntlet is why: its crystal and corrupted gear is made from shards inside the
  encounter and uses the same basic/attuned/perfected tier names as Last Man
  Standing, so filtering it everywhere deleted the Gauntlet's own gear from the
  Gauntlet's own page and left both Hunllefs unscorable.
- **Loadouts the calculator cannot model faithfully leave the search**, and any
  warning it does attach is reproduced in the report. "7.4 dps" and "7.4 dps,
  ignoring your set effect" are not the same claim.
- Player is assumed level 99 in every combat skill.

## New-monster watch

`lab/watch.py` diffs `monsters.json` against a committed snapshot. The
calculator's data is regenerated from the wiki, so a new boss appears there as
soon as it has stats — usually before a strategy page exists.

```bash
python lab/watch.py                      # what's new since the snapshot
python lab/watch.py --solve              # solve them, write reports/new/
python lab/watch.py --update-snapshot    # accept current state
```

Release-day stats are often provisional; re-run once they settle.

## Running it

```bash
git submodule update --init --depth 1
cd vendor/osrs-dps-calc && node .yarn/releases/yarn-4.9.2.cjs install && cd ../..
cd oracle && npm install && cd ..
git clone --depth 1 https://github.com/cha-ndler/osrs-bank-tag-layouts.git baseline

python lab/test_parity.py                # confirm the bridge agrees
python lab/agree.py --limit 10           # the trust gate
python lab/agree.py --activity Vorkath   # one target
```

Findings are written one per file to `reports/findings/`, each carrying the
published setup it beats, the pool it was restricted to, the profile assumed and
any warning attached. `reports/agreement.json` is the full record.

Delete `cache/meta.json` after bumping the submodule; it holds the style and
ammunition tables read from it.

## Attribution and licence

DPS calculation is [`weirdgloop/osrs-dps-calc`](https://github.com/weirdgloop/osrs-dps-calc)
by Weird Gloop and the OSRS Wiki, used **unmodified** via a pinned submodule in
`vendor/`. Equipment and monster data come from its CDN JSON.

That project is GPL-3.0, so this one is too — see `LICENSE`. No calculator source
is copied into `lab/` or `oracle/`; the submodule is consumed as a dependency.

Not affiliated with Jagex or Weird Gloop.
