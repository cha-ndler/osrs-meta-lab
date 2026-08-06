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

The oracle reproduces the calculator's own test values exactly. `Abyssal whip` at
level 99 returns `maxAttackRoll 16060` and `maxHit 24` — the precise figures
asserted in their `src/tests/calc/BasicRolls.test.ts`.

## What the numbers currently mean

`lab/agree.py` scores each activity's published setup and the solver's best
through the same oracle.

**Current sample: median +22% in the solver's favour.** That is *not* evidence
that consensus is wrong, and it should not be read that way. The solver searches
unconstrained best-in-slot while wiki pages routinely publish realistic,
affordable, mid-tier gear. Most of that 22% is Torva-and-rancour beating a guide
written for someone with a fire cape.

**So the lab does not yet identify undiscovered metas.** To do that the
comparison has to be tier-matched — solver and baseline drawing from the same
gear pool — otherwise every activity looks like a finding and none of them are.
That is the next piece of work.

What *is* trustworthy today:

- The DPS engine, at parity with the wiki calculator.
- The search, which independently picks crush at Araxxor (`crush 15` against
  `stab 160`) and lands on Inquisitor's mace — the wiki's own crush
  recommendation, derived from the raw numbers.
- The new-monster watch.

### Known limitations

- **Melee only.** Magic loadouts score near zero without a spell selected and
  ranged needs ammo modelled; comparing either against a melee solve produced a
  nonsense "+2961%" on Adamant dragon before it was excluded. Those styles need
  their own handling.
- **No cost, obtainability or mechanics.** Raw DPS ignores that a set costs 1.2b,
  or that a fight is phase-gated.
- **Mode-restricted gear is filtered out.** Last Man Standing, Deadman and
  Bounty Hunter items carry huge stats with no acquisition cost inside their own
  mode. Unfiltered, "Corrupted halberd (perfected)" wins nearly every boss —
  technically true, entirely useless.
- Player is assumed level 99 with no prayers or potions.

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

python lab/agree.py --limit 10
```

A solve costs roughly 20 oracle calls per monster and style. Every weapon
advances through the same slot together so one call covers the whole fleet;
scoring per weapon instead spawned hundreds of Node processes and startup
dominated the runtime.

## Attribution and licence

DPS calculation is [`weirdgloop/osrs-dps-calc`](https://github.com/weirdgloop/osrs-dps-calc)
by Weird Gloop and the OSRS Wiki, used **unmodified** via a pinned submodule in
`vendor/`. Equipment and monster data come from its CDN JSON.

That project is GPL-3.0, so this one is too — see `LICENSE`. No calculator source
is copied into `lab/` or `oracle/`; the submodule is consumed as a dependency.

Not affiliated with Jagex or Weird Gloop.
