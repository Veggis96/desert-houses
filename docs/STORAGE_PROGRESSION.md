# Storage progression

Warehouse capacity previously grew by 500 per level while building costs grew by 60% per level. Warehouse level 10 had capacity 6,000, but level 11 cost 6,597 Iron and Wood, blocking progression.

Capacity is now the greater of the old linear capacity and the next Warehouse upgrade's largest resource cost plus 25% headroom, rounded up to 500. Levels 0-9 are unchanged; level 10 holds 8,500 and level 11 holds 13,500 per resource. Building costs are unchanged. Existing Warehouse levels automatically use the new capacity, without resetting player resources or queues.

Upgrade previews use the same capacity function. Building and research cards show the minimum Warehouse level when a cost exceeds current storage. A player can expand storage rather than waiting indefinitely for an impossible stockpile.

Tests audit Warehouse self-progression through level 100, all building costs through level 30, every research tier, and the minimum storage requirement calculation. Buildings have no configured maximum level; the finite audit range is intentionally stated rather than implying unlimited simulation.
