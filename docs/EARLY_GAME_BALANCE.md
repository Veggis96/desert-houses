# Early-game balance pass

This pass targets an active local MVP session: a useful starter army within 20 minutes and a first NPC bloom capture within three hours. It uses the existing short construction/training timers, not production-server pacing.

## Changes

| System | Previous | New |
| --- | --- | --- |
| Iron / Wood base production | 20/hour | 60/hour |
| Dew Field base production | 8/hour | 12/hour |
| Windtrap unlock | Dew Field 10 | Dew Field 5 |
| Barracks unlock | Command Center 3 | Command Center 2 |
| Barracks base cost (Iron / Wood / Water) | 180 / 160 / 70 | 120 / 100 / 50 |
| Command Center base cost (Iron / Wood / Water / Sand) | 260 / 240 / 100 / 5 | 180 / 160 / 70 / 3 |
| Research Center unlock | Spice Field 5 | Spice Field 3 |
| Research Center base cost (Iron / Wood / Water / Sand) | 320 / 280 / 120 / 20 | 200 / 180 / 80 / 10 |
| Pistol troop Melange cost | 1 each | 0 |

Tutorial building objectives match the lower unlocks. Tutorial reward claims now enforce raid/report-reading objectives using the same checks as the displayed tutorial. Step order and existing progress indices remain intact. Starting stockpiles and tutorial rewards were retained.

Production values apply to existing villages as well as new ones. Building-cost reductions also affect later upgrades of those buildings through the existing cost multiplier. Existing buildings, queued costs, paid resources, and troop holdings are not rewritten or refunded.

## Economy projection

The deterministic projection follows the tutorial, claims rewards immediately, builds sequentially, and funds 25 melee troops plus 3 pistol troops. It keeps mines at their tutorial levels and includes prerequisite/storage upgrades as needed. It excludes raid income, combat losses, travel, and player decision time. It is a reproducible comparison, not a prediction of every player's experience or an optimal strategy.

| Milestone | Atreides | Harkonnen | Fremen |
| --- | --- | --- | --- |
| Windtrap built | 6.8 min | 6.8 min | 6.8 min |
| First 5 melee troops | 11.3 min | 11.3 min | 11.3 min |
| First 3 pistol troops | 13.7 min | 13.7 min | 13.7 min |
| Capture army funded and trained | 155.8 min | 148.6 min | 155.8 min |
| Net Water/hour with that army at home | 93.4 | 92.7 | 106.3 |

Under the same conservative strategy, the previous values needed roughly 6–8 days to fund that capture army. Investing earlier in mines or raiding would change those timings.

## Verification

The integration tests use the real Flask routes with an advancing clock and isolated temporary databases. For each faction they register a fresh account, complete the military tutorial without waiting for resources, raid a resource tile, read its report, fund extra troops through production, and capture a generated large bloom within three simulated hours. They also verify that raid and report-reading rewards cannot be claimed prematurely. Existing recall tests cover ownership, quantities, timed arrival and duplicate prevention.

No dev boosts are used. Hands-on testing is still needed for pacing, UI clarity, losses across different targets, and sustained multiplayer balance. Flight Works, refinery, advanced troop costs, combat formulas, production faction bonuses, and construction timers were retained for a later balance pass.

The subsequent faction pass adds infantry combat, travel and upkeep modifiers; the production bonuses and capture-force funding timings remain as shown.
