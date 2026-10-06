# Raid balance trial

Spaz's latest 20 raids yielded 4,747 resources, no losses, and no defended targets. The problem observed was collection from Iron Outcrops, Wood Groves and Water Oases.

Changes:
- Those resource sites now hold 40-55 resources plus 2 per unit of world distance. At distance 5: 50-65, previously 180-300 for Iron/Wood.
- Existing site stocks are capped on map processing, including before the next hourly refill. Empty sites are not refilled by this cap adjustment. Earned player resources are retained.
- Raids take eight times the previous travel duration, with a 60-second minimum per leg. Preview, dispatch and return use the same raid rule. Faction speed and transport advantages remain.
- Scouts, capture, reinforcement, harvesting and recall retain their travel times. Defended camp stockpiles and refill rates are unchanged. Resource sites keep the hourly 12% refill of their new smaller cap.

Pending outbound arrival timestamps are retained; raid returns calculated after updating use the new rule. Already scheduled returns are retained.

This is an initial trial, not a final economy target. Multiple armies can still raid concurrently. Compare another sample of playtest reports after deployment before further changes. Old reports do not retroactively change.
