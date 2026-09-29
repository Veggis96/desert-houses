# Desert Houses MVP

A small Flask/SQLite starting build for a Dune-inspired Travian-like browser strategy game.

## Current Features

- Register and log in
- Choose a starting faction: Atreides, Harkonnen, or Fremen
- Start with one village/base
- Produce Iron, Wood, Water, Spice Sand, and refined Spice Melange over time
- Water behaves like Travian wheat: production is reduced by base upkeep
- Queue building upgrades with a 2-slot sequential construction queue
- Short test construction timers are enabled for local iteration
- Gain development points from building spending: every combined 1000 Wood + Water spent gives 1 point
- Build Dew Field, Windtrap, Large Windtrap, Spice Refinery, Flight Works, Embassy, Barracks, Command Center, Research Center, and Deathstill infrastructure
- Refine Spice Sand into Melange automatically at a 4:1 ratio; refining stops when sand runs out or Melange storage is full
- Produce only Spice Sand from Spice Fields; sustained Melange refining requires additional Sand from raids against map blooms
- Upgrade the Spice Refinery for greater hourly throughput and research Melange Processing for +8% throughput and 0.1 less Spice Sand per Melange per level
- Spend Melange on advanced troops, shield and influence research, and high-level infrastructure upgrades
- Barracks requires Command Center level 2
- Train faction-named starter units with health, damage, armor, shield, speed, carry, and Water upkeep stats
- Train units through a separate sequential unit queue
- Research construction speed, unit training speed, health, damage, armor, shields, and Bene Gesserit influence
- Research runs through a separate research queue and can unlock faction-specific influence buildings
- Build faction influence buildings: Sisterhood Chapel, Whisper Chamber, or Sayyadina Sanctuary
- Train faction influence agents after unlocking Bene Gesserit Influence
- Build Scout and Assault Ornithopters, Carryalls, and Spice Harvesters in the Flight Works
- Render human troops in the Deathstill to recover Water; higher building levels improve recovery efficiency
- Raid Small and Medium Spice Blooms with regular forces
- Recall selected troops or the full garrison from owned blooms with timed travel home and an undefended-bloom confirmation
- View troops at home, travelling or harvesting, and stationed in the Command Center
- Capture NPC- or player-defended Large Spice Blooms; surviving attackers become a persistent garrison that can receive reinforcements
- Harvest captured Large Spice Blooms with Spice Harvesters transported by Carryalls
- Carryalls accelerate fully transported ground forces over long distances
- Choose a Cautious, Balanced, Aggressive, or Hold Position harvesting policy before departure
- Build Worm Attention through prolonged harvesting; Carryalls extract automatically, while sandworm encounters can cost cargo and vehicles
- Rest harvested blooms to reduce Worm Attention, with temporary Worm Territory after an attack
- Main base page only shows the building construction queue; research and unit queues live on their building pages
- Barracks, Research Center, and faction influence buildings have dedicated pages
- Follow a faction-specific tutorial with manual reward claims
- Use a dev-only resource boost button for local testing
- View player and alliance leaderboards
- Build an Embassy to level 10 to create alliances or apply to join one
- Alliance leaders/officers can approve or reject applications
- Alliance members can use a private alliance tab with overview, forum topics, and replies
- Alliance leaders/officers can delete alliance topics and posts
- Alliance leaders can permanently disband alliances by confirming the alliance tag
- Faction production bonuses are data-backed and easy to expand

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

The SQLite database is created automatically as `game.db` on first run.

## Suggested Next Features

- Higher-tier shielded units and combat resolution
- Spy missions, Reverend Mother loyalty reduction, and village capture
- Officer promotion/demotion and alliance management tools
- Role management for promoting officers and removing inactive members

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use an isolated temporary database.

## Early-game balance

Iron and Wood production starts at 60/hour, and Dew Field production at 12/hour before upkeep and faction bonuses. Windtraps unlock at Dew Field level 5; Barracks at Command Center level 2; Research Centers at Spice Field level 3. Starter pistol troops no longer require Melange. Advanced vehicles and refining retain their existing unlocks and costs.

See [the balance notes](docs/EARLY_GAME_BALANCE.md) for targets and verification. Run the repeatable economy projection with:

```powershell
.\.venv\Scripts\python.exe scripts/simulate_early_game.py
```

## Mission planning and factions

Map mission forms preview the selected force's attack, durability, carrying capacity, transport-aware travel time, arrival and estimated return. Capture and reinforcement survivors stay stationed. Defense estimates use saved scouting only; unknown or old intelligence is marked uncertain. The preview never dispatches troops.

- Atreides infantry: +15% health, +20% armor.
- Harkonnen infantry: +15% damage, +10% Water upkeep.
- Fremen infantry: +20% ground speed, -20% Water upkeep.

Existing production bonuses remain. Aircraft and harvesters do not gain infantry bonuses. Faction stats appear on training screens and apply to combat, movement and upkeep, including existing armies.

Alliance leaders and officers can set a target of 1–20 controlled Large Spice Blooms on the alliance page. Progress counts territory held by current members and falls when control or membership changes. This is a shared territorial goal, with no automatic resource reward.

## Development and production configuration

Development tools and debug mode are disabled by default. Session signing uses `SECRET_KEY`, or a random per-process key for local development. Set a persistent secret for stable sessions across restarts/workers. See [configuration details](docs/CONFIGURATION.md).
