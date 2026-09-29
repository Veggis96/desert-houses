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
- Barracks requires Command Center level 3
- Train faction-named starter units with health, damage, armor, shield, speed, carry, and Water upkeep stats
- Train units through a separate sequential unit queue
- Research construction speed, unit training speed, health, damage, armor, shields, and Bene Gesserit influence
- Research runs through a separate research queue and can unlock faction-specific influence buildings
- Build faction influence buildings: Sisterhood Chapel, Whisper Chamber, or Sayyadina Sanctuary
- Train faction influence agents after unlocking Bene Gesserit Influence
- Build Scout and Assault Ornithopters, Carryalls, and Spice Harvesters in the Flight Works
- Raid Small and Medium Spice Blooms with regular forces
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

- Timed construction queue
- Deathstill unit sacrifice mechanic
- Higher-tier shielded units and combat resolution
- Spy missions, Reverend Mother loyalty reduction, and village capture
- Recall and rebalance remote bloom garrisons
- Officer promotion/demotion and alliance management tools
- Role management for promoting officers and removing inactive members
