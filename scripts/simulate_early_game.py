"""Deterministic economy projection; no dev boosts, DB writes, or raid income.
Run from the repository with: python scripts/simulate_early_game.py
Assumes sequential building, immediate reward claims, and timely queue completion.
"""
import json
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as game


class Projection:
    def __init__(self, faction):
        self.faction = faction
        self.buildings = {key: int(key in ("iron_mine", "wood_yard", "dew_field", "spice_field", "warehouse")) for key in game.BUILDINGS}
        self.stock = dict(iron=900., wood=900., water=350., spice=35., melange=0.)
        self.units = {}
        self.seconds = 0
        self.wait_seconds = 0
        self.milestones = {}

    def advance(self, seconds):
        rates = game.resource_rates(self.buildings, self.faction, self.units)
        capacity = game.storage_capacity(self.buildings)
        refinery = rates["melange"]
        ratio = game.spice_refinery_conversion_ratio()
        sand = self.stock["spice"] + (rates["spice"] + refinery * ratio) * seconds / 3600
        refined = min(refinery * seconds / 3600, sand / ratio, capacity - self.stock["melange"])
        for resource in ("iron", "wood", "water"):
            self.stock[resource] = min(capacity, max(0, self.stock[resource] + rates[resource] * seconds / 3600))
        self.stock["spice"] = min(capacity, max(0, sand - refined * ratio))
        self.stock["melange"] += refined
        self.seconds += seconds

    def afford(self, cost):
        for _ in range(100):
            if any(amount > game.storage_capacity(self.buildings) for amount in cost.values()):
                self.build("warehouse", self.buildings["warehouse"] + 1)
                continue
            missing = {resource: amount - self.stock[resource] for resource, amount in cost.items() if amount > self.stock[resource] + 0.000001}
            if not missing:
                return
            if "melange" in missing and not self.buildings["spice_refinery"]:
                self.build("spice_refinery", 1)
                continue
            rates = game.resource_rates(self.buildings, self.faction, self.units)
            if any(rates[resource] <= 0 for resource in missing):
                if "water" in missing and rates["water"] <= 0:
                    self.build("dew_field", self.buildings["dew_field"] + 1)
                    continue
                raise RuntimeError(f"Unavailable production: {missing}")
            wait = max(missing[resource] / rates[resource] * 3600 for resource in missing) + 1
            self.wait_seconds += wait
            self.advance(wait)
        raise RuntimeError("Economy stalled")

    def spend(self, cost):
        self.afford(cost)
        for resource, amount in cost.items():
            self.stock[resource] -= amount

    def build(self, key, level):
        for prerequisite, required in game.BUILDINGS[key].get("requires", {}).items():
            self.build(prerequisite, required)
        while self.buildings[key] < level:
            current = self.buildings[key]
            self.spend(game.upgrade_cost(key, current))
            self.advance(game.construction_duration_seconds(current))
            self.buildings[key] += 1

    def train(self, key, amount):
        self.build(game.UNIT_TYPES[key]["unlock_building"], game.UNIT_TYPES[key]["unlock_level"])
        self.spend(game.unit_training_cost(key, amount))
        self.advance(game.unit_training_duration(key, amount))
        self.units[key] = self.units.get(key, 0) + amount

    def run(self):
        for step in game.TUTORIAL_STEPS:
            if "report_type" in step:
                break
            if "building" in step:
                self.build(step["building"], step["level"])
            if "unit" in step:
                self.train(step["unit"], max(step["amount"] - self.units.get(step["unit"], 0), 0))
            for resource, amount in step["reward"].items():
                self.stock[resource] += amount
            if step["title"] in ("Harness The Wind", "Raise The First Blades", "Armed Patrols"):
                self.milestones[step["title"]] = round(self.seconds / 60, 1)
        self.train("knife_fighter", max(25 - self.units.get("knife_fighter", 0), 0))
        self.milestones["Bloom capture force"] = round(self.seconds / 60, 1)
        return {"faction": self.faction, "minutes": self.milestones, "idle_minutes": round(self.wait_seconds / 60, 1),
                "water_per_hour": round(game.resource_rates(self.buildings, self.faction, self.units)["water"], 1)}


if __name__ == "__main__":
    print(json.dumps([Projection(faction).run() for faction, *_ in game.FACTIONS], indent=2))
