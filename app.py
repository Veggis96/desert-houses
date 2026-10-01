import json
import math
import os
import random
import sqlite3
import secrets
import hashlib
import re
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import g, abort, jsonify, Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("GAME_DB_PATH") or os.path.join(BASE_DIR, "game.db")

def security_config():
    production = os.environ.get("GAME_ENV") == "production"
    secret = os.environ.get("SECRET_KEY", "")
    hosts = [host.strip() for host in os.environ.get("TRUSTED_HOSTS", "").split(",") if host.strip()]
    if production and (len(secret) < 32 or secret == "dev-secret-change-me"):
        raise RuntimeError("Production requires a securely generated SECRET_KEY of at least 32 characters.")
    if production and not hosts:
        raise RuntimeError("Production requires TRUSTED_HOSTS (comma-separated hostnames).")
    if production and os.environ.get("FLASK_DEBUG", "0") == "1":
        raise RuntimeError("Flask debug mode must be disabled in production.")
    if production and os.environ.get("COOKIE_SECURE", "1") != "1":
        raise RuntimeError("Production requires secure HTTPS cookies.")
    return dict(PRODUCTION=production, SECRET_KEY=secret or secrets.token_hex(32),
                TRUSTED_HOSTS=hosts or None, MAX_CONTENT_LENGTH=64 * 1024,
                SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                SESSION_COOKIE_SECURE=production or os.environ.get("COOKIE_SECURE", "0") == "1",
                PERMANENT_SESSION_LIFETIME=timedelta(hours=24))


app = Flask(__name__)
app.config.update(security_config())


@app.context_processor
def inject_global_counts():
    if "user_id" not in session:
        return {"unread_report_count": 0, "resource_trend_label": resource_trend_label, "resource_display_name": resource_display_name}
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) AS count FROM battle_reports WHERE user_id = ? AND is_read = 0",
            (session["user_id"],),
        ).fetchone()
        user = get_current_user(db)
        village = get_village(db, user["id"]) if user else None
        overview = None
        if village:
            buildings = get_buildings(db, village["id"])
            research_levels = get_research_levels(db, village["id"])
            rates = resource_rates(buildings, user["faction_slug"], get_village_units(db, village["id"]), research_levels)
            capacity = storage_capacity(buildings)
            elapsed = max((utc_now() - parse_time(village["last_resource_update"])).total_seconds(), 0)
            overview = {"capacity": capacity, "rates": rates, "amounts": {
                key: min(capacity, max(0, village[key] + rate * elapsed / 3600))
                for key, rate in rates.items()
            }}
    return {"unread_report_count": row["count"] if row else 0, "resource_trend_label": resource_trend_label, "resource_display_name": resource_display_name, "resource_overview": overview}


FACTIONS = [
    ("atreides", "Atreides", "+5% wood; infantry +15% health and +20% armor."),
    ("harkonnen", "Harkonnen", "+5% iron; infantry +15% damage, +10% water upkeep."),
    ("fremen", "Fremen", "+10% water; infantry +20% speed, -20% water upkeep."),
]

BUILDINGS = {
    "iron_mine": {
        "name": "Iron Mine",
        "resource": "iron",
        "base_production": 60,
        "cost": {"iron": 45, "wood": 35, "water": 10, "spice": 0},
    },
    "wood_yard": {
        "name": "Wood Yard",
        "resource": "wood",
        "base_production": 60,
        "cost": {"iron": 35, "wood": 45, "water": 10, "spice": 0},
    },
    "dew_field": {
        "name": "Dew Field",
        "resource": "water",
        "base_production": 12,
        "cost": {"iron": 30, "wood": 30, "water": 5, "spice": 0},
    },
    "spice_field": {
        "name": "Spice Field",
        "resource": "spice",
        "base_production": 1,
        "cost": {"iron": 80, "wood": 80, "water": 25, "spice": 0},
    },
    "spice_refinery": {
        "name": "Spice Refinery",
        "resource": "melange",
        "base_production": 2.5,
        "cost": {"iron": 700, "wood": 650, "water": 300, "spice": 100, "melange": 0},
        "requires": {"spice_field": 5, "research_center": 2},
        "conversion_ratio": 4,
        "melange_from_level": 2,
        "melange_base_cost": 2,
    },
    "flight_works": {
        "name": "Flight Works",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 900, "wood": 700, "water": 350, "spice": 140, "melange": 8},
        "requires": {"command_center": 5, "research_center": 3},
        "melange_from_level": 2,
        "melange_base_cost": 5,
    },
    "warehouse": {
        "name": "Warehouse",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 60, "wood": 60, "water": 20, "spice": 0},
    },
    "windtrap": {
        "name": "Windtrap",
        "resource": "water",
        "base_production": 24,
        "cost": {"iron": 220, "wood": 260, "water": 80, "spice": 0},
        "requires": {"dew_field": 5},
    },
    "large_windtrap": {
        "name": "Large Windtrap",
        "resource": "water",
        "base_production": 70,
        "cost": {"iron": 700, "wood": 850, "water": 220, "spice": 25},
        "requires": {"windtrap": 10},
        "melange_from_level": 3,
        "melange_base_cost": 3,
    },
    "embassy": {
        "name": "Embassy",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 120, "wood": 140, "water": 60, "spice": 0},
    },
    "barracks": {
        "name": "Barracks",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 120, "wood": 100, "water": 50, "spice": 0},
        "requires": {"command_center": 2},
    },
    "command_center": {
        "name": "Command Center",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 180, "wood": 160, "water": 70, "spice": 3},
        "melange_from_level": 6,
        "melange_base_cost": 3,
    },
    "research_center": {
        "name": "Research Center",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 200, "wood": 180, "water": 80, "spice": 10},
        "requires": {"spice_field": 3},
        "melange_from_level": 5,
        "melange_base_cost": 4,
    },
    "deathstill": {
        "name": "Deathstill",
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 240, "wood": 180, "water": 120, "spice": 10},
        "requires": {"barracks": 3},
    },
    "influence_sanctuary": {
        "name": "Influence Sanctuary",
        "names": {
            "atreides": "Sisterhood Chapel",
            "harkonnen": "Whisper Chamber",
            "fremen": "Sayyadina Sanctuary",
        },
        "resource": None,
        "base_production": 0,
        "cost": {"iron": 420, "wood": 360, "water": 180, "spice": 45},
        "requires": {"research_center": 3},
        "research_requires": {"bene_gesserit_influence": 1},
    },
}

RESOURCE_BUILDING_KEYS = ("iron_mine", "wood_yard", "dew_field", "spice_field", "windtrap", "large_windtrap")
INFRASTRUCTURE_BUILDING_KEYS = tuple(key for key in BUILDINGS if key not in RESOURCE_BUILDING_KEYS)
BASE_VISUAL_SLOTS = (
    {"key": "center", "building_key": "command_center", "foundation": "images/base/foundation_center_neutral.png"},
    {"key": "north", "building_key": "spice_refinery", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "north_east", "building_key": "embassy", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "east", "building_key": "research_center", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "south_east", "building_key": "influence_sanctuary", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "south", "building_key": "warehouse", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "south_west", "building_key": "barracks", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "west", "building_key": "deathstill", "foundation": "images/base/foundation_outer_neutral.png"},
    {"key": "north_west", "building_key": "flight_works", "foundation": "images/base/foundation_outer_neutral.png"},
)

POINT_VALUE = 1000
EMBASSY_ALLIANCE_LEVEL = 10
CONSTRUCTION_QUEUE_LIMIT = 2
UNIT_QUEUE_LIMIT = 3
RESEARCH_QUEUE_LIMIT = 1
DEV_TOOLS_ENABLED = os.environ.get("DEV_TOOLS_ENABLED", "0") == "1" and not app.config["PRODUCTION"]
DEV_RESOURCE_AMOUNT = 9_999_999
MAP_RADIUS = 15
MAP_SIZE = MAP_RADIUS * 2 + 1
MAP_REGEN_INTERVAL_SECONDS = 3600

HARVEST_POLICIES = {
    "cautious": {
        "name": "Cautious Extraction",
        "description": "The Carryall extracts early at the first serious worm signs.",
        "duration_seconds": 30,
        "attention_gain": 28,
        "yield_per_harvester": 190,
        "risk_label": "Low",
        "cargo_retained_on_encounter": 0.82,
        "loss_chance": 0.03,
    },
    "balanced": {
        "name": "Balanced Harvest",
        "description": "A standard run with automatic extraction before the sand becomes critical.",
        "duration_seconds": 60,
        "attention_gain": 48,
        "yield_per_harvester": 300,
        "risk_label": "Moderate",
        "cargo_retained_on_encounter": 0.65,
        "loss_chance": 0.09,
    },
    "aggressive": {
        "name": "Aggressive Harvest",
        "description": "The crew stays through strong seismic activity for a larger load.",
        "duration_seconds": 100,
        "attention_gain": 70,
        "yield_per_harvester": 440,
        "risk_label": "High",
        "cargo_retained_on_encounter": 0.48,
        "loss_chance": 0.18,
    },
    "hold": {
        "name": "Hold Position",
        "description": "Maximum extraction. The Carryall waits until a worm attack is imminent.",
        "duration_seconds": 150,
        "attention_gain": 95,
        "yield_per_harvester": 600,
        "risk_label": "Severe",
        "cargo_retained_on_encounter": 0.32,
        "loss_chance": 0.32,
    },
}

RESEARCH_TYPES = {
    "spice_refining": {
        "name": "Melange Processing",
        "description": "Improves Spice Refinery throughput by 8% and reduces Spice Sand use by 0.1 per Melange each level.",
        "max_level": 10,
        "requires_building": {"research_center": 2, "spice_refinery": 1},
        "base_duration": 28,
        "cost": {"iron": 300, "wood": 240, "water": 150, "spice": 80, "melange": 1},
    },
    "construction_speed": {
        "name": "Construction Logistics",
        "description": "Reduces building construction time by 5% per level.",
        "max_level": 10,
        "requires_building": {"research_center": 1},
        "base_duration": 20,
        "cost": {"iron": 180, "wood": 180, "water": 80, "spice": 10},
    },
    "unit_training_speed": {
        "name": "Drill Discipline",
        "description": "Reduces unit training time by 5% per level.",
        "max_level": 10,
        "requires_building": {"research_center": 1},
        "base_duration": 20,
        "cost": {"iron": 220, "wood": 160, "water": 90, "spice": 10},
    },
    "unit_health": {
        "name": "Survival Conditioning",
        "description": "Increases unit health by 5% per level.",
        "max_level": 10,
        "requires_building": {"research_center": 1},
        "base_duration": 24,
        "cost": {"iron": 260, "wood": 160, "water": 120, "spice": 12},
    },
    "unit_damage": {
        "name": "Combat Doctrine",
        "description": "Increases unit damage by 5% per level.",
        "max_level": 10,
        "requires_building": {"research_center": 1},
        "base_duration": 24,
        "cost": {"iron": 280, "wood": 180, "water": 110, "spice": 12},
    },
    "unit_armor": {
        "name": "Armor Fitting",
        "description": "Increases unit armor by 5% per level.",
        "max_level": 10,
        "requires_building": {"research_center": 2},
        "base_duration": 28,
        "cost": {"iron": 320, "wood": 220, "water": 130, "spice": 18},
    },
    "unit_shield": {
        "name": "Holtzman Shield Theory",
        "description": "Improves shield values for shielded units by 5% per level.",
        "max_level": 10,
        "requires_building": {"research_center": 3},
        "base_duration": 32,
        "cost": {"iron": 420, "wood": 260, "water": 160, "spice": 35, "melange": 2},
    },
    "bene_gesserit_influence": {
        "name": "Bene Gesserit Influence",
        "description": "Unlocks faction influence buildings and spy training.",
        "max_level": 5,
        "requires_building": {"research_center": 3},
        "base_duration": 36,
        "cost": {"iron": 360, "wood": 300, "water": 180, "spice": 50, "melange": 3},
    },
}

UNIT_TYPES = {
    "knife_fighter": {
        "category": "military",
        "tier": 1,
        "names": {
            "atreides": "Atreides Bladesman",
            "harkonnen": "Harkonnen Enforcer",
            "fremen": "Fremen Knife Fighter",
        },
        "unlock_building": "barracks",
        "unlock_level": 1,
        "health": 40,
        "damage": 12,
        "armor": 1,
        "shield": 0,
        "shield_piercing": 0.35,
        "speed": 12,
        "carry": 20,
        "water_upkeep": 0.25,
        "training_time": 12,
        "cost": {"iron": 35, "wood": 25, "water": 8, "spice": 0},
    },
    "maula_pistol": {
        "category": "military",
        "tier": 2,
        "names": {
            "atreides": "Atreides Guard",
            "harkonnen": "Harkonnen Gunman",
            "fremen": "Fremen Maula Raider",
        },
        "unlock_building": "barracks",
        "unlock_level": 3,
        "health": 32,
        "damage": 18,
        "armor": 0,
        "shield": 0,
        "shield_piercing": 0.08,
        "speed": 10,
        "carry": 25,
        "water_upkeep": 0.35,
        "training_time": 18,
        "cost": {"iron": 55, "wood": 35, "water": 12, "spice": 0},
    },
    "elite_guard": {
        "category": "military",
        "tier": 4,
        "role": "Elite anti-shield infantry",
        "ability_summary": "Advanced infantry with personal shielding and faction-specific battlefield doctrine.",
        "abilities": [
            "Personal shield absorbs conventional damage before health.",
            "Slow-blade training bypasses part of enemy shielding.",
        ],
        "names": {
            "atreides": "Atreides Shield Guard",
            "harkonnen": "Harkonnen Devastator",
            "fremen": "Fremen Fedaykin",
        },
        "unlock_building": "barracks",
        "unlock_level": 6,
        "unlock_research": {"unit_shield": 2},
        "health": 100,
        "damage": 34,
        "armor": 4,
        "shield": 28,
        "shield_piercing": 0.25,
        "speed": 10,
        "carry": 18,
        "water_upkeep": 1.0,
        "training_time": 60,
        "cost": {"iron": 220, "wood": 160, "water": 55, "spice": 35, "melange": 4},
        "faction_stats": {
            "atreides": {"health": 110, "damage": 28, "armor": 5, "shield": 36, "shield_piercing": 0.25, "speed": 9, "water_upkeep": 0.9},
            "harkonnen": {"health": 92, "damage": 40, "armor": 4, "shield": 28, "shield_piercing": 0.15, "speed": 8, "water_upkeep": 1.3},
            "fremen": {"health": 96, "damage": 34, "armor": 3, "shield": 16, "shield_piercing": 0.55, "speed": 14, "water_upkeep": 0.8},
        },
    },
    "influence_spy": {
        "category": "influence",
        "tier": 3,
        "role": "Scouting and Influence",
        "ability_summary": "Scouts desert tiles to reveal exact resources and defender strength. Future missions will reduce enemy village loyalty.",
        "abilities": [
            "Current: Scout map tiles for hidden resources and defenders.",
            "Future: Reduce loyalty in enemy villages once conquest mechanics are added.",
        ],
        "names": {
            "atreides": "Sisterhood Agent",
            "harkonnen": "Whisper Agent",
            "fremen": "Sayyadina Scout",
        },
        "unlock_building": "influence_sanctuary",
        "unlock_level": 1,
        "unlock_research": {"bene_gesserit_influence": 1},
        "health": 24,
        "damage": 4,
        "armor": 0,
        "shield": 0,
        "shield_piercing": 0.2,
        "speed": 18,
        "carry": 5,
        "water_upkeep": 0.5,
        "training_time": 45,
        "cost": {"iron": 90, "wood": 70, "water": 35, "spice": 20, "melange": 2},
    },
    "scout_ornithopter": {
        "category": "vehicle", "tier": 3, "role": "Aerial Reconnaissance",
        "ability_summary": "Fast scouting aircraft that reveals blooms, defenders, garrisons, and resources.",
        "abilities": ["Can perform scout missions.", "Much faster than ground scouts over long distances."],
        "names": {"atreides": "Scout Ornithopter", "harkonnen": "Scout Ornithopter", "fremen": "Scout Ornithopter"},
        "unlock_building": "flight_works", "unlock_level": 1, "can_scout": True,
        "health": 55, "damage": 6, "armor": 2, "shield": 0, "speed": 34, "carry": 10,
        "shield_piercing": 0.1,
        "water_upkeep": 0.4, "training_time": 40,
        "cost": {"iron": 240, "wood": 140, "water": 60, "spice": 30, "melange": 3},
    },
    "assault_ornithopter": {
        "category": "vehicle", "tier": 4, "role": "Fast Air Assault",
        "ability_summary": "Armed ornithopter for raids, bloom capture, and rapid reinforcement.",
        "abilities": ["Can raid and capture large Spice Blooms.", "Carries a small amount of captured resources."],
        "names": {"atreides": "Assault Ornithopter", "harkonnen": "Assault Ornithopter", "fremen": "Assault Ornithopter"},
        "unlock_building": "flight_works", "unlock_level": 3, "can_raid": True,
        "health": 95, "damage": 38, "armor": 5, "shield": 2, "speed": 26, "carry": 35,
        "shield_piercing": 0.12,
        "water_upkeep": 1.0, "training_time": 70,
        "cost": {"iron": 520, "wood": 280, "water": 120, "spice": 70, "melange": 10},
    },
    "carryall": {
        "category": "vehicle", "tier": 5, "role": "Long-range Transport",
        "ability_summary": "Carries ground forces at flight speed and is required to transport a Spice Harvester.",
        "abilities": ["Transports up to 25 ground units per Carryall.", "One Carryall is required per Harvester."],
        "names": {"atreides": "Carryall", "harkonnen": "Carryall", "fremen": "Carryall"},
        "unlock_building": "flight_works", "unlock_level": 5, "is_transport": True, "transport_capacity": 25,
        "health": 150, "damage": 0, "armor": 7, "shield": 3, "speed": 24, "carry": 80,
        "shield_piercing": 0,
        "water_upkeep": 1.4, "training_time": 95,
        "cost": {"iron": 850, "wood": 440, "water": 180, "spice": 110, "melange": 18},
    },
    "spice_harvester": {
        "category": "vehicle", "tier": 5, "role": "Large Bloom Harvester",
        "ability_summary": "Extracts large Spice Blooms after capture. Must travel with a Carryall.",
        "abilities": ["Harvests up to 300 Spice Sand per mission.", "Cannot travel without a Carryall."],
        "names": {"atreides": "Spice Harvester", "harkonnen": "Spice Harvester", "fremen": "Spice Harvester"},
        "unlock_building": "flight_works", "unlock_level": 4, "is_harvester": True,
        "health": 180, "damage": 0, "armor": 8, "shield": 0, "speed": 4, "carry": 300,
        "shield_piercing": 0,
        "water_upkeep": 1.8, "training_time": 110,
        "cost": {"iron": 950, "wood": 520, "water": 220, "spice": 130, "melange": 20},
    },
}

FACTION_ABILITIES = {
    "atreides": {"description": "Infantry: +15% health and +20% armor.", "health": 1.15, "armor": 1.2},
    "harkonnen": {"description": "Infantry: +15% damage, +10% water upkeep.", "damage": 1.15, "water_upkeep": 1.1},
    "fremen": {"description": "Infantry: +20% speed, -20% water upkeep.", "speed": 1.2, "water_upkeep": 0.8},
}


class ResearchLevels(dict):
    """Research levels with the village faction used by combat calculations."""
    def __init__(self, levels, faction_slug=None):
        super().__init__(levels)
        self.faction_slug = faction_slug


def village_faction(db, village):
    row = db.execute("SELECT factions.slug FROM users JOIN factions ON factions.id = users.faction_id WHERE users.id = ?", (village["user_id"],)).fetchone()
    return row["slug"] if row else None


def faction_unit_stat(unit_key, stat, faction_slug=None):
    config = UNIT_TYPES[unit_key]
    base_value = config.get("faction_stats", {}).get(faction_slug, {}).get(stat, config.get(stat, 0))
    multiplier = FACTION_ABILITIES.get(faction_slug, {}).get(stat, 1) if config["category"] == "military" else 1
    return round(base_value * multiplier, 3)


NPC_UNIT_TYPES = {
    "raider": {"name": "Raiders", "singular": "Raider", "attack": 12, "defense": 18, "health": 35},
    "smuggler": {"name": "Smugglers", "singular": "Smuggler", "attack": 18, "defense": 12, "health": 25},
    "bandit": {"name": "Bandits", "singular": "Bandit", "attack": 8, "defense": 10, "health": 20},
}

TUTORIAL_GUIDES = {
    "atreides": "Duke Leto",
    "harkonnen": "Baron Harkonnen",
    "fremen": "Stilgar",
}

TUTORIAL_STEPS = [
    {
        "title": "Secure Iron",
        "text": "Your first duty is to strengthen the base. Upgrade the Iron Mine to level 2.",
        "building": "iron_mine",
        "level": 2,
        "reward": {"iron": 40, "wood": 55, "water": 20, "spice": 0},
    },
    {
        "title": "Gather Timber",
        "text": "Upgrade the Wood Yard to level 2 so construction can continue.",
        "building": "wood_yard",
        "level": 2,
        "reward": {"iron": 55, "wood": 40, "water": 20, "spice": 0},
    },
    {
        "title": "Protect The Water",
        "text": "Upgrade the Dew Field to level 2. Water decides how far this base can grow.",
        "building": "dew_field",
        "level": 2,
        "reward": {"iron": 50, "wood": 50, "water": 25, "spice": 0},
    },
    {
        "title": "Find The Spice",
        "text": "Upgrade the Spice Field to level 2. Even a trickle of Spice Sand matters.",
        "building": "spice_field",
        "level": 2,
        "reward": {"iron": 80, "wood": 80, "water": 35, "spice": 2},
    },
    {
        "title": "Expand Storage",
        "text": "Upgrade the Warehouse to level 2 so your stockpiles do not overflow.",
        "building": "warehouse",
        "level": 2,
        "reward": {"iron": 90, "wood": 90, "water": 40, "spice": 0},
    },
    {
        "title": "Prepare The Windtrap",
        "text": "Raise the Dew Field to level 5. This unlocks the Windtrap.",
        "building": "dew_field",
        "level": 5,
        "reward": {"iron": 260, "wood": 300, "water": 120, "spice": 5},
    },
    {
        "title": "Harness The Wind",
        "text": "Construct the Windtrap to push Water production beyond survival levels.",
        "building": "windtrap",
        "level": 1,
        "reward": {"iron": 220, "wood": 230, "water": 90, "spice": 5},
    },
    {
        "title": "Open Diplomacy",
        "text": "Construct the Embassy. Alliances begin with a formal presence.",
        "building": "embassy",
        "level": 1,
        "reward": {"iron": 180, "wood": 180, "water": 70, "spice": 5},
    },
    {
        "title": "Central Command",
        "text": "Construct the Command Center. Expansion requires command structure.",
        "building": "command_center",
        "level": 1,
        "reward": {"iron": 250, "wood": 230, "water": 100, "spice": 10},
    },
    {
        "title": "Strengthen Command",
        "text": "Upgrade the Command Center to level 2. A barracks needs order before soldiers.",
        "building": "command_center",
        "level": 2,
        "reward": {"iron": 260, "wood": 230, "water": 100, "spice": 8},
    },
    {
        "title": "Prepare Soldiers",
        "text": "Construct the Barracks. Soon you will need troops.",
        "building": "barracks",
        "level": 1,
        "reward": {"iron": 220, "wood": 180, "water": 80, "spice": 5},
    },
    {
        "title": "Study The Desert",
        "text": "Construct the Research Center. Knowledge is its own weapon.",
        "building": "research_center",
        "level": 1,
        "reward": {"iron": 280, "wood": 260, "water": 100, "spice": 15},
    },
    {
        "title": "Raise The First Blades",
        "text": "Train 5 basic melee troops from your Barracks.",
        "unit": "knife_fighter",
        "amount": 5,
        "reward": {"iron": 180, "wood": 140, "water": 70, "spice": 3},
    },
    {
        "title": "Armed Patrols",
        "text": "Upgrade the Barracks to level 3, then train 3 Maula pistol troops.",
        "building": "barracks",
        "level": 3,
        "unit": "maula_pistol",
        "amount": 3,
        "reward": {"iron": 240, "wood": 180, "water": 90, "spice": 5},
    },
    {
        "title": "Strike The Desert",
        "text": "Send a raid against a non-player map target. Scout first if you want to estimate defenders.",
        "report_type": "raid",
        "reward": {"iron": 260, "wood": 220, "water": 100, "spice": 8},
    },
    {
        "title": "Read The Aftermath",
        "text": "Open a raid report in your Inbox to review losses, defenders, and loot.",
        "report_type": "raid",
        "report_read": True,
        "reward": {"iron": 160, "wood": 160, "water": 80, "spice": 5},
    },
]


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.fromisoformat(value)


def safe_local_redirect(value):
    return isinstance(value, str) and value.startswith("/") and not value.startswith("//") and "\\" not in value and not any(ord(character) < 32 for character in value)


def compact_duration(seconds):
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def resource_display_name(resource):
    return {"spice": "Spice Sand", "melange": "Melange"}.get(resource, resource.capitalize())


def resource_trend_label(amount, rate, capacity):
    if rate > 0 and amount < capacity:
        return f"Full in {compact_duration((capacity - amount) / rate * 3600)}"
    if rate > 0:
        return "Storage full"
    if rate < 0 and amount > 0:
        return f"Empty in {compact_duration(amount / abs(rate) * 3600)}"
    if rate < 0:
        return "Empty"
    return "Stable"


def dashboard_alerts(village, rates, capacity, buildings, construction_queue, latest_reports):
    alerts = []
    nearly_full = [resource_display_name(resource) for resource in ("iron", "wood", "water", "spice", "melange") if capacity and village[resource] >= capacity * 0.9]
    if nearly_full:
        alerts.append({"level": "warning", "title": "Storage Nearly Full", "text": f"{', '.join(nearly_full)} stockpiles are close to capacity. Upgrade the Warehouse or spend resources."})
    if rates["water"] < 0:
        alerts.append({"level": "danger", "title": "Water Deficit", "text": "Water is dropping. Upgrade Dew Fields or Windtraps before expanding further."})
    if buildings.get("spice_refinery", 0) > 0 and rates["spice"] < 0:
        alerts.append({"level": "warning", "title": "Refinery Needs Sand", "text": "The refinery can consume Spice Sand faster than your fields produce it. Raid Spice Blooms to sustain full output."})
    if not construction_queue:
        alerts.append({"level": "info", "title": "Construction Idle", "text": "Your construction queue is empty. Keep buildings or fields upgrading to grow faster."})
    if buildings.get("barracks", 0) > 0:
        alerts.append({"level": "info", "title": "Train Patrols", "text": "Your Barracks is available. Train troops for raiding scouted camps."})
    unread_reports = sum(1 for report in latest_reports if not report["is_read"])
    if unread_reports:
        alerts.append({"level": "info", "title": "Unread Reports", "text": f"You have {unread_reports} recent unread report{'s' if unread_reports != 1 else ''}."})
    return alerts[:4]


def dashboard_priorities(village, rates, capacity, buildings, construction_queue, tutorial, latest_reports, building_cards, units):
    priorities = []

    def add(tone, eyebrow, title, text, endpoint, cta):
        priorities.append({
            "tone": tone,
            "eyebrow": eyebrow,
            "title": title,
            "text": text,
            "href": url_for(endpoint),
            "cta": cta,
        })

    if rates["water"] < 0:
        add("danger", "Critical", "Stabilize the water supply", f"Water is falling by {abs(rates['water']):.1f} per hour.", "resource_fields_page", "Open water fields")

    if buildings.get("spice_refinery", 0) > 0 and rates["spice"] < 0:
        add("warning", "Refinery supply", "Secure more Spice Sand", f"Full refining creates a {abs(rates['spice']):.1f} Sand/hour deficit.", "map_page", "Find Spice Blooms")

    nearly_full = [resource for resource in ("iron", "wood", "water", "spice", "melange") if capacity and village[resource] >= capacity * 0.9]
    if nearly_full:
        storage_names = ", ".join(resource_display_name(resource) for resource in nearly_full)
        storage_verb = "are" if len(nearly_full) > 1 else "is"
        add("warning", "Storage", "Spend or expand storage", f"{storage_names} {storage_verb} close to capacity.", "dashboard", "Inspect Warehouse")

    if not tutorial["complete"]:
        step = tutorial["step"]
        if "building" in step:
            endpoint = "resource_fields_page" if step["building"] in RESOURCE_BUILDING_KEYS else "dashboard"
        elif "unit" in step:
            endpoint = "barracks_page" if UNIT_TYPES[step["unit"]]["category"] == "military" else "influence_sanctuary_page"
        elif "report_type" in step:
            endpoint = "inbox" if step.get("report_read") else "map_page"
        else:
            endpoint = "dashboard"
        add("primary", f"Guidance {tutorial['index']}/{tutorial['total']}", step["title"], step["text"], endpoint, "Continue objective")

    if not construction_queue:
        candidates = [card for card in building_cards if card["can_upgrade"]]
        if candidates:
            candidate = min(candidates, key=lambda card: sum(card["cost"].values()))
            endpoint = "resource_fields_page" if candidate["key"] in RESOURCE_BUILDING_KEYS else "dashboard"
            verb = "Construct" if candidate["level"] == 0 else "Upgrade"
            add("info", "Idle builders", f"{verb} {candidate['name']}", f"Your queue is empty and this order is affordable now.", endpoint, "Open construction")
        else:
            add("info", "Idle builders", "Prepare the next upgrade", "Your construction queue is empty. Review the missing resources for the next order.", "resource_fields_page", "Review upgrades")

    unread_reports = sum(1 for report in latest_reports if not report["is_read"])
    if unread_reports:
        add("info", "Intelligence", "Review new reports", f"{unread_reports} recent report{'s are' if unread_reports != 1 else ' is'} unread.", "inbox", "Open reports")

    military_count = sum(amount for key, amount in units.items() if key in UNIT_TYPES and UNIT_TYPES[key]["category"] == "military")
    scout_count = sum(amount for key, amount in units.items() if key in UNIT_TYPES and UNIT_TYPES[key]["category"] == "influence")
    if military_count == 0 and buildings.get("barracks", 0) > 0:
        add("info", "Defense", "Train a first patrol", "A small patrol unlocks raiding and protects future expansion.", "barracks_page", "Open Barracks")
    elif scout_count > 0:
        add("info", "Operations", "Find the next target", "Use your scouts to reveal exact defenders and resources before committing troops.", "map_page", "Open Map")
    elif military_count > 0:
        add("info", "Operations", "Review known targets", "Compare nearby targets before dispatching your army.", "command_center_page", "Open Command Center")

    return priorities[:3]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(db, table_name, column_name):
    columns = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(column["name"] == column_name for column in columns)


def add_column_if_missing(db, table_name, column_definition):
    column_name = column_definition.split()[0]
    if not column_exists(db, table_name, column_name):
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def init_db():
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS factions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                faction_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (faction_id) REFERENCES factions(id)
            );

            CREATE TABLE IF NOT EXISTS villages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                iron REAL NOT NULL,
                wood REAL NOT NULL,
                water REAL NOT NULL,
                spice REAL NOT NULL,
                melange REAL NOT NULL DEFAULT 0,
                last_resource_update TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS village_buildings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                building_key TEXT NOT NULL,
                level INTEGER NOT NULL,
                UNIQUE(village_id, building_key),
                FOREIGN KEY (village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS alliances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                tag TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                leader_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (leader_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS alliance_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alliance_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL UNIQUE,
                role TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                FOREIGN KEY (alliance_id) REFERENCES alliances(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS alliance_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alliance_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER,
                FOREIGN KEY (alliance_id) REFERENCES alliances(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (decided_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS alliance_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alliance_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (alliance_id) REFERENCES alliances(id),
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS alliance_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (topic_id) REFERENCES alliance_topics(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS construction_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                building_key TEXT NOT NULL,
                target_level INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                started_at TEXT,
                finish_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS user_tutorial_progress (
                user_id INTEGER PRIMARY KEY,
                step_index INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS village_units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                unit_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                UNIQUE(village_id, unit_key),
                FOREIGN KEY (village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS unit_training_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                unit_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                started_at TEXT,
                finish_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS village_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                research_key TEXT NOT NULL,
                level INTEGER NOT NULL,
                UNIQUE(village_id, research_key),
                FOREIGN KEY (village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS research_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                research_key TEXT NOT NULL,
                target_level INTEGER NOT NULL,
                duration_seconds INTEGER NOT NULL,
                started_at TEXT,
                finish_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS map_tiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                tile_type TEXT NOT NULL,
                village_id INTEGER,
                controller_village_id INTEGER,
                garrison_json TEXT NOT NULL DEFAULT '{}',
                worm_attention REAL NOT NULL DEFAULT 0,
                worm_cooldown_until TEXT,
                npc_strength INTEGER NOT NULL DEFAULT 0,
                resource_iron REAL NOT NULL DEFAULT 0,
                resource_wood REAL NOT NULL DEFAULT 0,
                resource_water REAL NOT NULL DEFAULT 0,
                resource_spice REAL NOT NULL DEFAULT 0,
                last_regenerated_at TEXT NOT NULL,
                UNIQUE(x, y),
                FOREIGN KEY (village_id) REFERENCES villages(id),
                FOREIGN KEY (controller_village_id) REFERENCES villages(id)
            );

            CREATE TABLE IF NOT EXISTS troop_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                village_id INTEGER NOT NULL,
                target_tile_id INTEGER NOT NULL,
                target_x INTEGER NOT NULL,
                target_y INTEGER NOT NULL,
                mission_type TEXT NOT NULL,
                units_json TEXT NOT NULL,
                survivors_json TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                arrive_at TEXT NOT NULL,
                return_at TEXT,
                loot_iron REAL NOT NULL DEFAULT 0,
                loot_wood REAL NOT NULL DEFAULT 0,
                loot_water REAL NOT NULL DEFAULT 0,
                loot_spice REAL NOT NULL DEFAULT 0,
                report TEXT NOT NULL DEFAULT '',
                mission_metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (village_id) REFERENCES villages(id),
                FOREIGN KEY (target_tile_id) REFERENCES map_tiles(id)
            );

            CREATE TABLE IF NOT EXISTS battle_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                village_id INTEGER NOT NULL,
                movement_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                report_type TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                target_x INTEGER NOT NULL,
                target_y INTEGER NOT NULL,
                target_name TEXT NOT NULL,
                outcome TEXT NOT NULL,
                units_sent_json TEXT NOT NULL,
                units_survived_json TEXT NOT NULL,
                units_lost_json TEXT NOT NULL,
                enemy_before INTEGER NOT NULL DEFAULT 0,
                enemy_after INTEGER NOT NULL DEFAULT 0,
                enemy_lost INTEGER NOT NULL DEFAULT 0,
                enemy_before_json TEXT NOT NULL DEFAULT '{}',
                enemy_after_json TEXT NOT NULL DEFAULT '{}',
                enemy_lost_json TEXT NOT NULL DEFAULT '{}',
                enemy_player_before_json TEXT NOT NULL DEFAULT '{}',
                enemy_player_after_json TEXT NOT NULL DEFAULT '{}',
                enemy_player_lost_json TEXT NOT NULL DEFAULT '{}',
                enemy_faction_slug TEXT,
                combat_json TEXT NOT NULL DEFAULT '{}',
                loot_iron REAL NOT NULL DEFAULT 0,
                loot_wood REAL NOT NULL DEFAULT 0,
                loot_water REAL NOT NULL DEFAULT 0,
                loot_spice REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (village_id) REFERENCES villages(id),
                FOREIGN KEY (movement_id) REFERENCES troop_movements(id)
            );

            CREATE TABLE IF NOT EXISTS tile_intel (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tile_id INTEGER NOT NULL,
                scouted_at TEXT NOT NULL,
                npc_strength INTEGER NOT NULL DEFAULT 0,
                resource_iron REAL NOT NULL DEFAULT 0,
                resource_wood REAL NOT NULL DEFAULT 0,
                resource_water REAL NOT NULL DEFAULT 0,
                resource_spice REAL NOT NULL DEFAULT 0,
                controller_village_id INTEGER,
                garrison_json TEXT NOT NULL DEFAULT '{}',
                worm_attention REAL NOT NULL DEFAULT 0,
                worm_cooldown_until TEXT,
                UNIQUE(user_id, tile_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (tile_id) REFERENCES map_tiles(id)
            );

            CREATE TABLE IF NOT EXISTS map_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tile_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, tile_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (tile_id) REFERENCES map_tiles(id)
            );
            """
        )

        db.execute("""CREATE TABLE IF NOT EXISTS login_sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            created_at REAL NOT NULL, last_seen REAL NOT NULL, expires_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id))""")
        db.execute("CREATE INDEX IF NOT EXISTS login_sessions_user ON login_sessions(user_id)")
        db.execute("CREATE TABLE IF NOT EXISTS auth_attempts (bucket TEXT NOT NULL, attempted_at REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS auth_attempts_bucket_time ON auth_attempts(bucket, attempted_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS alliance_spice_objectives (
            alliance_id INTEGER PRIMARY KEY, target_blooms INTEGER NOT NULL DEFAULT 3,
            FOREIGN KEY (alliance_id) REFERENCES alliances(id))""")
        add_column_if_missing(db, "villages", "point_wood_spent REAL NOT NULL DEFAULT 0")
        add_column_if_missing(db, "villages", "point_water_spent REAL NOT NULL DEFAULT 0")
        add_column_if_missing(db, "villages", "points INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(db, "villages", "melange REAL NOT NULL DEFAULT 0")
        add_column_if_missing(db, "villages", "map_x INTEGER")
        add_column_if_missing(db, "villages", "map_y INTEGER")
        add_column_if_missing(db, "map_tiles", "npc_units_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "map_tiles", "controller_village_id INTEGER")
        add_column_if_missing(db, "map_tiles", "garrison_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "map_tiles", "worm_attention REAL NOT NULL DEFAULT 0")
        add_column_if_missing(db, "map_tiles", "worm_cooldown_until TEXT")
        add_column_if_missing(db, "tile_intel", "npc_units_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "tile_intel", "controller_village_id INTEGER")
        add_column_if_missing(db, "tile_intel", "garrison_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "tile_intel", "worm_attention REAL NOT NULL DEFAULT 0")
        add_column_if_missing(db, "tile_intel", "worm_cooldown_until TEXT")
        add_column_if_missing(db, "troop_movements", "mission_metadata_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_before_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_after_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_lost_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_player_before_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_player_after_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_player_lost_json TEXT NOT NULL DEFAULT '{}'")
        add_column_if_missing(db, "battle_reports", "enemy_faction_slug TEXT")
        add_column_if_missing(db, "battle_reports", "combat_json TEXT NOT NULL DEFAULT '{}'")

        for slug, name, description in FACTIONS:
            db.execute(
                "INSERT INTO factions (slug, name, description) VALUES (?, ?, ?) ON CONFLICT(slug) DO UPDATE SET description = excluded.description",
                (slug, name, description),
            )
        migrate_spice_blooms(db)


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


def get_current_user(db):
    if "user_id" not in session:
        return None
    return db.execute(
        """
        SELECT users.id, users.username, users.created_at, factions.slug AS faction_slug, factions.name AS faction_name,
               factions.description AS faction_description
        FROM users
        JOIN factions ON factions.id = users.faction_id
        WHERE users.id = ?
        """,
        (session["user_id"],),
    ).fetchone()


def create_starting_village(db, user_id):
    now = utc_now().isoformat()
    cursor = db.execute(
        """
        INSERT INTO villages (user_id, name, iron, wood, water, spice, melange, last_resource_update)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, "First Sietch", 900, 900, 350, 35, 0, now),
    )
    village_id = cursor.lastrowid
    for building_key in BUILDINGS:
        start_level = 1 if building_key in ("iron_mine", "wood_yard", "dew_field", "spice_field", "warehouse") else 0
        db.execute(
            "INSERT INTO village_buildings (village_id, building_key, level) VALUES (?, ?, ?)",
            (village_id, building_key, start_level),
        )


def get_village(db, user_id):
    return db.execute("SELECT * FROM villages WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()


def get_buildings(db, village_id):
    rows = db.execute(
        "SELECT building_key, level FROM village_buildings WHERE village_id = ?",
        (village_id,),
    ).fetchall()
    buildings = {row["building_key"]: row["level"] for row in rows}
    for building_key in BUILDINGS:
        if building_key not in buildings:
            db.execute(
                "INSERT INTO village_buildings (village_id, building_key, level) VALUES (?, ?, ?)",
                (village_id, building_key, 0),
            )
            buildings[building_key] = 0
    return buildings


def building_display_name(building_key, faction_slug):
    building = BUILDINGS[building_key]
    return building.get("names", {}).get(faction_slug, building["name"])


def research_multiplier(level):
    return 1.8 ** level


def get_research_levels(db, village_id):
    rows = db.execute(
        "SELECT research_key, level FROM village_research WHERE village_id = ?",
        (village_id,),
    ).fetchall()
    village = db.execute("SELECT * FROM villages WHERE id = ?", (village_id,)).fetchone()
    return ResearchLevels({row["research_key"]: row["level"] for row in rows}, village_faction(db, village) if village else None)


def construction_duration_seconds(current_level, research_levels=None):
    duration = min(30 + (current_level * 10), 120)
    speed_level = (research_levels if research_levels is not None else {}).get("construction_speed", 0)
    return max(int(duration * (1 - min(speed_level * 0.05, 0.5))), 5)


def research_cost(research_key, current_level):
    base_cost = RESEARCH_TYPES[research_key]["cost"]
    multiplier = research_multiplier(current_level)
    return {resource: int(amount * multiplier) for resource, amount in base_cost.items()}


def research_duration_seconds(research_key, current_level):
    return min(int(RESEARCH_TYPES[research_key]["base_duration"] * (1 + current_level * 0.35)), 180)


def get_research_queue(db, village_id):
    return db.execute(
        """
        SELECT * FROM research_queue
        WHERE village_id = ?
        ORDER BY id ASC
        """,
        (village_id,),
    ).fetchall()


def process_research_queue(db, village_id):
    now = utc_now()
    item = db.execute(
        "SELECT * FROM research_queue WHERE village_id = ? ORDER BY id ASC LIMIT 1",
        (village_id,),
    ).fetchone()
    if not item:
        return
    if not item["started_at"]:
        finish_at = now + timedelta(seconds=item["duration_seconds"])
        db.execute(
            "UPDATE research_queue SET started_at = ?, finish_at = ? WHERE id = ?",
            (now.isoformat(), finish_at.isoformat(), item["id"]),
        )
        item = db.execute("SELECT * FROM research_queue WHERE id = ?", (item["id"],)).fetchone()
    if parse_time(item["finish_at"]) > now:
        return
    db.execute(
        """
        INSERT INTO village_research (village_id, research_key, level)
        VALUES (?, ?, ?)
        ON CONFLICT(village_id, research_key) DO UPDATE SET level = excluded.level
        """,
        (village_id, item["research_key"], item["target_level"]),
    )
    db.execute("DELETE FROM research_queue WHERE id = ?", (item["id"],))


def queued_research_count(queue, research_key):
    return sum(1 for item in queue if item["research_key"] == research_key)


def effective_research_level(research_levels, queue, research_key):
    return research_levels.get(research_key, 0) + queued_research_count(queue, research_key)


def building_research_unmet(building_key, research_levels):
    requirements = BUILDINGS[building_key].get("research_requires", {})
    return {
        required_key: required_level
        for required_key, required_level in requirements.items()
        if research_levels.get(required_key, 0) < required_level
    }


def research_prerequisite_text(requirements):
    if not requirements:
        return ""
    return ", ".join(f"{RESEARCH_TYPES[key]['name']} level {level}" for key, level in requirements.items())


def research_building_unmet(research_key, buildings):
    requirements = RESEARCH_TYPES[research_key].get("requires_building", {})
    return {
        required_key: required_level
        for required_key, required_level in requirements.items()
        if buildings.get(required_key, 0) < required_level
    }


def get_construction_queue(db, village_id):
    return db.execute(
        """
        SELECT * FROM construction_queue
        WHERE village_id = ?
        ORDER BY id ASC
        """,
        (village_id,),
    ).fetchall()


def process_construction_queue(db, village_id):
    now = utc_now()
    start_anchor = now
    while True:
        item = db.execute(
            "SELECT * FROM construction_queue WHERE village_id = ? ORDER BY id ASC LIMIT 1",
            (village_id,),
        ).fetchone()
        if not item:
            return

        if not item["started_at"]:
            finish_at = start_anchor + timedelta(seconds=item["duration_seconds"])
            db.execute(
                "UPDATE construction_queue SET started_at = ?, finish_at = ? WHERE id = ?",
                (start_anchor.isoformat(), finish_at.isoformat(), item["id"]),
            )
            item = db.execute("SELECT * FROM construction_queue WHERE id = ?", (item["id"],)).fetchone()

        finish_at = parse_time(item["finish_at"])
        if finish_at > now:
            return

        db.execute(
            "UPDATE village_buildings SET level = ? WHERE village_id = ? AND building_key = ?",
            (item["target_level"], village_id, item["building_key"]),
        )
        db.execute("DELETE FROM construction_queue WHERE id = ?", (item["id"],))
        start_anchor = finish_at


def queued_building_keys(queue):
    return {item["building_key"] for item in queue}


def queued_upgrade_count(queue, building_key):
    return sum(1 for item in queue if item["building_key"] == building_key)


def effective_building_level(buildings, queue, building_key):
    return buildings[building_key] + queued_upgrade_count(queue, building_key)


def unmet_prerequisites(building_key, buildings):
    requirements = BUILDINGS[building_key].get("requires", {})
    return {
        required_key: required_level
        for required_key, required_level in requirements.items()
        if buildings.get(required_key, 0) < required_level
    }


def prerequisite_text(building_key, faction_slug=None):
    requirements = BUILDINGS[building_key].get("requires", {})
    if not requirements:
        return ""
    return ", ".join(f"{building_display_name(key, faction_slug) if faction_slug else BUILDINGS[key]['name']} level {level}" for key, level in requirements.items())


def prerequisite_text_for_buildings(requirements, faction_slug=None):
    if not requirements:
        return ""
    return ", ".join(f"{building_display_name(key, faction_slug) if faction_slug else BUILDINGS[key]['name']} level {level}" for key, level in requirements.items())


def queue_status(queue_item):
    if not queue_item["started_at"]:
        return "Waiting"
    remaining = int(max((parse_time(queue_item["finish_at"]) - utc_now()).total_seconds(), 0))
    return f"{remaining}s remaining"


def get_village_units(db, village_id):
    rows = db.execute(
        "SELECT unit_key, amount FROM village_units WHERE village_id = ?",
        (village_id,),
    ).fetchall()
    return {row["unit_key"]: row["amount"] for row in rows}


def add_village_units(db, village_id, unit_key, amount):
    if amount <= 0:
        return
    db.execute(
        """
        INSERT INTO village_units (village_id, unit_key, amount)
        VALUES (?, ?, ?)
        ON CONFLICT(village_id, unit_key) DO UPDATE SET amount = amount + excluded.amount
        """,
        (village_id, unit_key, amount),
    )


def remove_village_units(db, village_id, unit_key, amount):
    if amount <= 0:
        return
    db.execute(
        "UPDATE village_units SET amount = amount - ? WHERE village_id = ? AND unit_key = ?",
        (amount, village_id, unit_key),
    )
    db.execute("DELETE FROM village_units WHERE village_id = ? AND unit_key = ? AND amount <= 0", (village_id, unit_key))


def normalize_player_units(units):
    normalized = {}
    for key, amount in (units or {}).items():
        if key not in UNIT_TYPES:
            continue
        try:
            amount = max(int(amount), 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            normalized[key] = amount
    return normalized


def player_units_from_json(value):
    if not value:
        return {}
    try:
        return normalize_player_units(json.loads(value))
    except (TypeError, ValueError):
        return {}


def player_units_json(units):
    return json.dumps(normalize_player_units(units), sort_keys=True)


def merge_player_units(first, second):
    merged = normalize_player_units(first)
    for key, amount in normalize_player_units(second).items():
        merged[key] = merged.get(key, 0) + amount
    return merged


def normalize_npc_units(units):
    return {key: max(int(amount), 0) for key, amount in (units or {}).items() if key in NPC_UNIT_TYPES and int(amount) > 0}


def npc_units_from_json(value):
    if not value:
        return {}
    try:
        return normalize_npc_units(json.loads(value))
    except (TypeError, ValueError):
        return {}


def npc_units_json(units):
    return json.dumps(normalize_npc_units(units), sort_keys=True)


def npc_units_defense(units):
    return sum(NPC_UNIT_TYPES[key]["defense"] * amount for key, amount in normalize_npc_units(units).items())


def npc_units_attack(units):
    return sum(NPC_UNIT_TYPES[key]["attack"] * amount for key, amount in normalize_npc_units(units).items())


def npc_units_health(units):
    return sum(NPC_UNIT_TYPES[key]["health"] * amount for key, amount in normalize_npc_units(units).items())


def generate_npc_units(target_defense, rng=None):
    rng = rng or random
    target_defense = max(int(target_defense), 0)
    if target_defense <= 0:
        return {}
    units = {}
    keys = ("raider", "smuggler", "bandit")
    while npc_units_defense(units) < target_defense:
        key = rng.choices(keys, weights=(4, 2, 3), k=1)[0]
        units[key] = units.get(key, 0) + 1
        if sum(units.values()) >= 50:
            break
    return normalize_npc_units(units)


def npc_units_for_tile(tile):
    if not tile or tile["tile_type"] not in ("npc_camp", "spice_bloom_large"):
        return {}
    if tile["tile_type"] == "spice_bloom_large" and tile["controller_village_id"]:
        return {}
    units = npc_units_from_json(tile["npc_units_json"])
    if units:
        return units
    return generate_npc_units(tile["npc_strength"], random.Random(tile["x"] * 809 + tile["y"] * 1297))


def npc_units_for_source(source):
    if not source:
        return {}
    units = npc_units_from_json(source["npc_units_json"]) if "npc_units_json" in source.keys() else {}
    if units:
        return units
    if "tile_type" in source.keys() and source["tile_type"] == "npc_camp":
        return npc_units_for_tile(source)
    if "npc_strength" in source.keys() and source["npc_strength"] > 0:
        seed = source["tile_id"] if "tile_id" in source.keys() else source["id"]
        return generate_npc_units(source["npc_strength"], random.Random(seed * 1297))
    return {}


def npc_unit_losses(before_units, after_units):
    losses = {}
    for key, before_amount in normalize_npc_units(before_units).items():
        lost = before_amount - normalize_npc_units(after_units).get(key, 0)
        if lost > 0:
            losses[key] = lost
    return losses


def npc_unit_report_rows(units):
    rows = []
    for key, amount in normalize_npc_units(units).items():
        config = NPC_UNIT_TYPES[key]
        rows.append(
            {
                "key": key,
                "name": config["singular"] if amount == 1 else config["name"],
                "amount": amount,
                "attack": config["attack"],
                "defense": config["defense"],
                "health": config["health"],
            }
        )
    return rows


def npc_unit_summary(units):
    units = normalize_npc_units(units)
    return {
        "count": sum(units.values()),
        "attack": npc_units_attack(units),
        "defense": npc_units_defense(units),
        "health": npc_units_health(units),
    }


def unit_amount_total(units):
    return sum(max(int(amount), 0) for amount in (units or {}).values())


def report_summary(report):
    enemy_player_before = player_units_from_json(report["enemy_player_before_json"]) if "enemy_player_before_json" in report.keys() else {}
    if enemy_player_before:
        enemy_before_units = enemy_player_before
        enemy_lost_units = player_units_from_json(report["enemy_player_lost_json"])
    else:
        enemy_before_units = npc_units_from_json(report["enemy_before_json"]) or generate_npc_units(report["enemy_before"], random.Random(report["id"] * 17))
        enemy_lost_units = npc_units_from_json(report["enemy_lost_json"]) or generate_npc_units(report["enemy_lost"], random.Random(report["id"] * 23))
    return {
        "sent_count": unit_amount_total(json.loads(report["units_sent_json"] or "{}")),
        "lost_count": unit_amount_total(json.loads(report["units_lost_json"] or "{}")),
        "enemy_seen_count": unit_amount_total(enemy_before_units),
        "enemy_lost_count": unit_amount_total(enemy_lost_units),
    }


def tile_intel_summary(intel):
    units = npc_units_for_source(intel)
    resources = sum(intel[f"resource_{resource}"] for resource in ("iron", "wood", "water", "spice"))
    return {
        "defender_count": unit_amount_total(units),
        "defense": npc_units_defense(units),
        "attack": npc_units_attack(units),
        "loot": int(resources),
    }


def migrate_spice_blooms(db):
    """Split legacy Spice Bloom tiles into deterministic small, medium, and large deposits."""
    legacy_tiles = db.execute("SELECT * FROM map_tiles WHERE tile_type = 'spice_oasis'").fetchall()
    for tile in legacy_tiles:
        signature = abs(tile["x"] * 31 + tile["y"] * 17) % 10
        bloom_type = "spice_bloom_small" if signature < 6 else "spice_bloom_medium" if signature < 9 else "spice_bloom_large"
        distance = max(abs(tile["x"]), abs(tile["y"]), 1)
        rng = random.Random(tile["x"] * 1009 + tile["y"] * 9176)
        resources = map_tile_resources(bloom_type, distance, rng)
        units = {}
        if bloom_type == "spice_bloom_large":
            units = generate_npc_units(140 + distance * 5, rng)
        db.execute(
            """
            UPDATE map_tiles
            SET tile_type = ?, npc_strength = ?, npc_units_json = ?, resource_water = ?, resource_spice = ?
            WHERE id = ?
            """,
            (bloom_type, npc_units_defense(units), npc_units_json(units), resources["water"], resources["spice"], tile["id"]),
        )


def ensure_npc_unit_stacks(db):
    camps = db.execute(
        "SELECT * FROM map_tiles WHERE tile_type = 'npc_camp' OR (tile_type = 'spice_bloom_large' AND controller_village_id IS NULL)"
    ).fetchall()
    for camp in camps:
        units = npc_units_from_json(camp["npc_units_json"])
        if not units and camp["npc_strength"] > 0:
            units = generate_npc_units(camp["npc_strength"], random.Random(camp["x"] * 809 + camp["y"] * 1297))
            db.execute(
                "UPDATE map_tiles SET npc_units_json = ?, npc_strength = ? WHERE id = ?",
                (npc_units_json(units), npc_units_defense(units), camp["id"]),
            )


def ensure_map_tiles(db):
    existing = db.execute("SELECT COUNT(*) AS count FROM map_tiles").fetchone()["count"]
    if existing >= MAP_SIZE * MAP_SIZE:
        ensure_npc_unit_stacks(db)
        return
    rng = random.Random(4815162342)
    now = utc_now().isoformat()
    for x in range(-MAP_RADIUS, MAP_RADIUS + 1):
        for y in range(-MAP_RADIUS, MAP_RADIUS + 1):
            if x == 0 and y == 0:
                tile_type = "desert"
            else:
                roll = rng.random()
                if roll < 0.07:
                    tile_type = "npc_camp"
                elif roll < 0.10:
                    tile_type = "spice_bloom_small"
                elif roll < 0.115:
                    tile_type = "spice_bloom_medium"
                elif roll < 0.12:
                    tile_type = "spice_bloom_large"
                elif roll < 0.18:
                    tile_type = "water_oasis"
                elif roll < 0.24:
                    tile_type = "iron_outcrop"
                elif roll < 0.30:
                    tile_type = "scrap_field"
                else:
                    tile_type = "desert"
            distance = max(abs(x), abs(y), 1)
            if tile_type == "npc_camp":
                strength = rng.randint(10, 30) + distance * 2
            elif tile_type == "spice_bloom_large":
                strength = rng.randint(130, 190) + distance * 5
            else:
                strength = 0
            npc_units = generate_npc_units(strength, rng) if strength else {}
            strength = npc_units_defense(npc_units)
            resources = map_tile_resources(tile_type, distance, rng)
            db.execute(
                """
                INSERT OR IGNORE INTO map_tiles
                    (x, y, tile_type, npc_strength, npc_units_json, resource_iron, resource_wood, resource_water, resource_spice, last_regenerated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (x, y, tile_type, strength, npc_units_json(npc_units), resources["iron"], resources["wood"], resources["water"], resources["spice"], now),
            )


def map_tile_resources(tile_type, distance, rng=None):
    rng = rng or random
    base = 80 + distance * 12
    resources = {"iron": 0, "wood": 0, "water": 0, "spice": 0}
    if tile_type == "npc_camp":
        resources = {
            "iron": base + rng.randint(20, 120),
            "wood": base + rng.randint(20, 120),
            "water": int(base * 0.45) + rng.randint(10, 60),
            "spice": max(3, distance // 2) + rng.randint(0, 10),
        }
    elif tile_type == "spice_bloom_small":
        resources["spice"] = 35 + distance + rng.randint(10, 35)
        resources["water"] = 15 + rng.randint(0, 25)
    elif tile_type == "spice_bloom_medium":
        resources["spice"] = 120 + distance * 3 + rng.randint(40, 100)
        resources["water"] = 30 + rng.randint(10, 50)
    elif tile_type == "spice_bloom_large":
        resources["spice"] = 600 + distance * 12 + rng.randint(150, 350)
        resources["water"] = 60 + rng.randint(20, 100)
    elif tile_type == "water_oasis":
        resources["water"] = base + rng.randint(40, 140)
    elif tile_type == "iron_outcrop":
        resources["iron"] = base + rng.randint(40, 160)
    elif tile_type == "scrap_field":
        resources["wood"] = base + rng.randint(40, 160)
    return resources


def ensure_village_map_position(db, village):
    ensure_map_tiles(db)
    if village["map_x"] is not None and village["map_y"] is not None:
        db.execute(
            """
            UPDATE map_tiles
            SET tile_type = 'player_base', village_id = ?, controller_village_id = NULL, garrison_json = '{}', npc_strength = 0, npc_units_json = '{}',
                resource_iron = 0, resource_wood = 0, resource_water = 0, resource_spice = 0
            WHERE x = ? AND y = ?
            """,
            (village["id"], village["map_x"], village["map_y"]),
        )
        return village["map_x"], village["map_y"]
    for radius in range(0, MAP_RADIUS + 1):
        for x in range(-radius, radius + 1):
            for y in range(-radius, radius + 1):
                if max(abs(x), abs(y)) != radius:
                    continue
                tile = db.execute(
                    "SELECT * FROM map_tiles WHERE x = ? AND y = ? AND village_id IS NULL AND controller_village_id IS NULL",
                    (x, y),
                ).fetchone()
                if tile:
                    db.execute("UPDATE villages SET map_x = ?, map_y = ? WHERE id = ?", (x, y, village["id"]))
                    db.execute(
                        """
                        UPDATE map_tiles
                        SET tile_type = 'player_base', village_id = ?, controller_village_id = NULL, garrison_json = '{}', npc_strength = 0, npc_units_json = '{}',
                            resource_iron = 0, resource_wood = 0, resource_water = 0, resource_spice = 0
                        WHERE id = ?
                        """,
                        (village["id"], tile["id"]),
                    )
                    return x, y
    raise RuntimeError("No free map position available.")


def tile_display_name(tile_type):
    return {
        "player_base": "Player Base",
        "desert": "Open Desert",
        "npc_camp": "Raider Camp",
        "spice_bloom_small": "Small Spice Bloom",
        "spice_bloom_medium": "Medium Spice Bloom",
        "spice_bloom_large": "Large Spice Bloom",
        "water_oasis": "Water Oasis",
        "iron_outcrop": "Iron Outcrop",
        "scrap_field": "Wood Grove",
    }.get(tile_type, tile_type.replace("_", " ").title())


def tile_short_label(tile):
    if tile["village_id"]:
        return "Base"
    return {
        "desert": "Desert",
        "npc_camp": "Camp",
        "spice_bloom_small": "Small Bloom",
        "spice_bloom_medium": "Medium Bloom",
        "spice_bloom_large": "Large Bloom",
        "water_oasis": "Water",
        "iron_outcrop": "Iron",
        "scrap_field": "Wood",
    }.get(tile["tile_type"], tile_display_name(tile["tile_type"]))


def tile_potential_text(tile_type):
    return {
        "player_base": "Your base",
        "desert": "Low potential",
        "npc_camp": "Mixed loot possible",
        "spice_bloom_small": "Small Spice Sand deposit",
        "spice_bloom_medium": "Rich Spice Sand deposit",
        "spice_bloom_large": "Strategic Spice Sand deposit",
        "water_oasis": "Water-rich",
        "iron_outcrop": "Iron-rich",
        "scrap_field": "Wood-rich",
    }.get(tile_type, "Unknown potential")


def tile_expected_yield(tile_type, distance):
    """Return a useful pre-scout range without revealing the generated tile values."""
    base = 80 + max(int(distance), 1) * 12
    if tile_type == "npc_camp":
        return {"label": "Mixed resources", "minimum": int(base * 2.45) + 43, "maximum": int(base * 2.45) + 310}
    if tile_type == "spice_bloom_small":
        return {"label": "Spice Sand + water", "minimum": 45 + int(distance), "maximum": 100 + int(distance)}
    if tile_type == "spice_bloom_medium":
        return {"label": "Spice Sand + water", "minimum": 160 + int(distance) * 3, "maximum": 300 + int(distance) * 3}
    if tile_type == "spice_bloom_large":
        return {"label": "Spice Sand + water", "minimum": 800 + int(distance) * 12, "maximum": 1200 + int(distance) * 12}
    if tile_type == "water_oasis":
        return {"label": "Water", "minimum": base + 40, "maximum": base + 140}
    if tile_type == "iron_outcrop":
        return {"label": "Iron", "minimum": base + 40, "maximum": base + 160}
    if tile_type == "scrap_field":
        return {"label": "Wood", "minimum": base + 40, "maximum": base + 160}
    return {"label": "No notable yield", "minimum": 0, "maximum": 0}


def target_recommendation(selected_tile, has_intel, defender_summary, unit_cards, visible_loot):
    if selected_tile["village_id"]:
        return {"tone": "neutral", "label": "Settlement", "text": "Village attacks are not available."}
    if selected_tile["tile_type"] == "desert":
        return {"tone": "neutral", "label": "Low value", "text": "No resource deposit is visible here."}
    if not has_intel:
        return {"tone": "caution", "label": "Scout first", "text": "Reveal defenders and exact resources before committing troops."}
    army_attack = sum(card["amount"] * card["damage"] for card in unit_cards)
    army_durability = sum(card["amount"] * card["durability"] for card in unit_cards)
    army_carry = sum(card["amount"] * card["carry"] for card in unit_cards)
    defense = defender_summary["defense"]
    counterattack = defender_summary["attack"]
    if defense <= 0:
        text = "Unguarded target."
        if visible_loot > army_carry and army_carry:
            text += " Your full army cannot carry all visible resources."
        return {"tone": "good", "label": "Good target", "text": text}
    if army_attack < defense:
        return {"tone": "danger", "label": "Army too weak", "text": f"Available attack {army_attack}; at least {math.ceil(defense * 1.25)} is recommended."}
    if army_attack >= defense * 1.25 and army_durability >= counterattack * 1.2:
        return {"tone": "good", "label": "Good target", "text": "Your available army has a useful safety margin."}
    return {"tone": "caution", "label": "Risky target", "text": "Victory is possible, but losses may be significant."}


def recommended_targets(intel_rows, village, unit_cards):
    army_attack = sum(card["amount"] * card["damage"] for card in unit_cards)
    army_durability = sum(card["amount"] * card["durability"] for card in unit_cards)
    army_carry = sum(card["amount"] * card["carry"] for card in unit_cards)
    targets = []
    tone_rank = {"good": 0, "caution": 1, "danger": 2}

    for intel in intel_rows:
        if intel["tile_type"] in ("desert", "player_base"):
            continue
        summary = npc_unit_summary(npc_units_for_source(intel))
        loot = int(round(sum(intel[f"resource_{resource}"] for resource in ("iron", "wood", "water", "spice"))))
        distance = map_distance(village["map_x"], village["map_y"], intel["x"], intel["y"])
        if summary["defense"] <= 0:
            tone, verdict = "good", "Open target"
        elif army_attack >= summary["defense"] * 1.25 and army_durability >= summary["attack"] * 1.2:
            tone, verdict = "good", "Recommended"
        elif army_attack >= summary["defense"]:
            tone, verdict = "caution", "Losses likely"
        else:
            tone, verdict = "danger", "Too strong"
        score = loot / max(summary["defense"], 1) / (1 + distance * 0.12)
        targets.append({
            "tile_id": intel["tile_id"],
            "name": tile_display_name(intel["tile_type"]),
            "x": intel["x"],
            "y": intel["y"],
            "distance": distance,
            "loot": loot,
            "defense": summary["defense"],
            "tone": tone,
            "verdict": verdict,
            "carry": min(loot, army_carry),
            "score": score,
        })

    targets.sort(key=lambda target: (tone_rank[target["tone"]], -target["score"], target["distance"]))
    return targets[:4]


def get_tile_intel(db, user_id, tile_id):
    return db.execute(
        "SELECT * FROM tile_intel WHERE user_id = ? AND tile_id = ?",
        (user_id, tile_id),
    ).fetchone()


def save_tile_intel(db, user_id, tile):
    units = npc_units_for_tile(tile)
    db.execute(
        """
        INSERT INTO tile_intel
            (user_id, tile_id, scouted_at, npc_strength, npc_units_json, resource_iron, resource_wood, resource_water, resource_spice,
             controller_village_id, garrison_json, worm_attention, worm_cooldown_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, tile_id) DO UPDATE SET
            scouted_at = excluded.scouted_at,
            npc_strength = excluded.npc_strength,
            npc_units_json = excluded.npc_units_json,
            resource_iron = excluded.resource_iron,
            resource_wood = excluded.resource_wood,
            resource_water = excluded.resource_water,
            resource_spice = excluded.resource_spice,
            controller_village_id = excluded.controller_village_id,
            garrison_json = excluded.garrison_json,
            worm_attention = excluded.worm_attention,
            worm_cooldown_until = excluded.worm_cooldown_until
        """,
        (
            user_id,
            tile["id"],
            utc_now().isoformat(),
            npc_units_defense(units),
            npc_units_json(units),
            tile["resource_iron"],
            tile["resource_wood"],
            tile["resource_water"],
            tile["resource_spice"],
            tile["controller_village_id"],
            tile["garrison_json"],
            tile["worm_attention"],
            tile["worm_cooldown_until"],
        ),
    )


def tile_difficulty_label(tile):
    units = npc_units_for_source(tile)
    strength = npc_units_defense(units) if units else (tile["npc_strength"] if tile else 0)
    if strength <= 0:
        return "Unguarded"
    if strength < 35:
        return "Weak"
    if strength < 75:
        return "Guarded"
    return "Dangerous"


def regenerate_map_tiles(db):
    now = utc_now()
    rows = db.execute("SELECT * FROM map_tiles WHERE village_id IS NULL AND tile_type != 'desert'").fetchall()
    for tile in rows:
        last = parse_time(tile["last_regenerated_at"])
        intervals = int((now - last).total_seconds() // MAP_REGEN_INTERVAL_SECONDS)
        if intervals <= 0:
            continue
        distance = max(abs(tile["x"]), abs(tile["y"]), 1)
        cap = map_tile_resources(tile["tile_type"], distance, random.Random(tile["x"] * 1009 + tile["y"] * 9176))
        increments = {
            "iron": max(int(cap["iron"] * 0.12), 0) * intervals,
            "wood": max(int(cap["wood"] * 0.12), 0) * intervals,
            "water": max(int(cap["water"] * 0.12), 0) * intervals,
            "spice": max(int(cap["spice"] * 0.08), 0) * intervals,
        }
        npc_gain = 0
        worm_attention = max(float(tile["worm_attention"]) - intervals * 12, 0)
        worm_cooldown_until = tile["worm_cooldown_until"]
        if worm_cooldown_until and parse_time(worm_cooldown_until) <= now:
            worm_cooldown_until = None
        npc_units = npc_units_for_tile(tile)
        if tile["tile_type"] == "npc_camp" or (tile["tile_type"] == "spice_bloom_large" and not tile["controller_village_id"]):
            npc_units = normalize_npc_units(npc_units)
            cap_defense = 120
            rng = random.Random(tile["x"] * 1009 + tile["y"] * 9176 + intervals)
            for _ in range(intervals):
                if npc_units_defense(npc_units) >= cap_defense:
                    break
                key = rng.choices(("raider", "smuggler", "bandit"), weights=(4, 2, 3), k=1)[0]
                npc_units[key] = npc_units.get(key, 0) + 1
            npc_gain = max(npc_units_defense(npc_units) - tile["npc_strength"], 0)
        db.execute(
            """
            UPDATE map_tiles
            SET resource_iron = MIN(resource_iron + ?, ?), resource_wood = MIN(resource_wood + ?, ?),
                resource_water = MIN(resource_water + ?, ?), resource_spice = MIN(resource_spice + ?, ?),
                npc_strength = ?, npc_units_json = ?, worm_attention = ?, worm_cooldown_until = ?, last_regenerated_at = ?
            WHERE id = ?
            """,
            (
                increments["iron"], cap["iron"],
                increments["wood"], cap["wood"],
                increments["water"], cap["water"],
                increments["spice"], cap["spice"],
                tile["npc_strength"] + npc_gain, npc_units_json(npc_units), worm_attention, worm_cooldown_until, now.isoformat(), tile["id"],
            ),
        )


def map_distance(source_x, source_y, target_x, target_y):
    return math.hypot(target_x - source_x, target_y - source_y)


def movement_duration_seconds(source_x, source_y, target_x, target_y, sent_units, faction_slug=None):
    sent_units = normalize_player_units(sent_units)
    carryalls = sent_units.get("carryall", 0)
    transported = sum(
        amount for key, amount in sent_units.items()
        if key != "carryall" and not UNIT_TYPES[key].get("can_scout") and not UNIT_TYPES[key].get("can_raid")
    )
    if carryalls and transported <= carryalls * UNIT_TYPES["carryall"]["transport_capacity"]:
        speeds = [UNIT_TYPES["carryall"]["speed"]]
        speeds.extend(
            faction_unit_stat(key, "speed", faction_slug) for key, amount in sent_units.items()
            if amount > 0 and key != "carryall" and (UNIT_TYPES[key].get("can_scout") or UNIT_TYPES[key].get("can_raid"))
        )
        slowest_speed = min(speeds)
    else:
        slowest_speed = min(faction_unit_stat(key, "speed", faction_slug) for key, amount in sent_units.items() if amount > 0)
    distance = max(map_distance(source_x, source_y, target_x, target_y), 1)
    return max(int(distance / slowest_speed * 60), 8)


def sent_unit_stats(sent_units, research_levels):
    attack = 0
    durability = 0
    carry = 0
    for key, amount in sent_units.items():
        attack += effective_unit_stat(key, "damage", research_levels) * amount
        durability += (
            effective_unit_stat(key, "health", research_levels)
            + effective_unit_stat(key, "armor", research_levels) * 5
            + effective_unit_stat(key, "shield", research_levels) * 3
        ) * amount
        carry += UNIT_TYPES[key]["carry"] * amount
    return attack, durability, carry


def player_unit_summary(units, research_levels=None):
    units = normalize_player_units(units)
    attack, durability, carry = sent_unit_stats(units, research_levels if research_levels is not None else {})
    defense = 0
    health = 0
    for key, amount in units.items():
        damage = effective_unit_stat(key, "damage", research_levels if research_levels is not None else {})
        armor = effective_unit_stat(key, "armor", research_levels if research_levels is not None else {})
        shield = effective_unit_stat(key, "shield", research_levels if research_levels is not None else {})
        unit_health = effective_unit_stat(key, "health", research_levels if research_levels is not None else {})
        defense += (damage * 0.7 + armor * 5 + shield * 3) * amount
        health += unit_health * amount
    return {"count": sum(units.values()), "attack": round(attack, 1), "defense": round(defense, 1), "health": round(health, 1), "durability": round(durability, 1), "carry": carry}


def player_unit_report_rows(units, faction_slug, research_levels=None):
    rows = []
    for key, amount in normalize_player_units(units).items():
        rows.append({
            "key": key,
            "name": unit_display_name(key, faction_slug),
            "amount": amount,
            "attack": effective_unit_stat(key, "damage", research_levels if research_levels is not None else {}),
            "defense": round(effective_unit_stat(key, "damage", research_levels if research_levels is not None else {}) * 0.7 + effective_unit_stat(key, "armor", research_levels if research_levels is not None else {}) * 5 + effective_unit_stat(key, "shield", research_levels if research_levels is not None else {}) * 3, 1),
            "health": effective_unit_stat(key, "health", research_levels if research_levels is not None else {}),
            "shield": effective_unit_stat(key, "shield", research_levels if research_levels is not None else {}),
            "shield_piercing": round(faction_unit_stat(key, "shield_piercing", faction_slug) * 100),
        })
    return rows


def player_combat_profile(units, research_levels=None):
    research_levels = research_levels if research_levels is not None else {}
    units = normalize_player_units(units)
    attack = body = shield = piercing_attack = 0.0
    for key, amount in units.items():
        damage = effective_unit_stat(key, "damage", research_levels)
        attack += damage * amount
        body += (effective_unit_stat(key, "health", research_levels) + effective_unit_stat(key, "armor", research_levels) * 4) * amount
        shield += effective_unit_stat(key, "shield", research_levels) * amount
        piercing_attack += damage * faction_unit_stat(key, "shield_piercing", getattr(research_levels, "faction_slug", None)) * amount
    return {"attack": attack, "body": body, "shield": shield, "piercing_attack": piercing_attack, "count": sum(units.values())}


def npc_combat_profile(units):
    units = normalize_npc_units(units)
    count = sum(units.values())
    return {
        "attack": float(npc_units_attack(units)),
        "body": float(npc_units_health(units) + npc_units_defense(units) * 0.45),
        "shield": 0.0,
        "piercing_attack": 0.0,
        "count": count,
    }


def distribute_npc_losses(units, survival_ratio):
    survivors = {}
    for key, amount in normalize_npc_units(units).items():
        kept = int(amount * survival_ratio)
        if kept <= 0 and survival_ratio > 0.35 and amount > 0:
            kept = 1
        if kept > 0:
            survivors[key] = min(kept, amount)
    return survivors


def resolve_combat(attacker_units, defender_units, attacker_research=None, defender_research=None, defender_is_player=False, max_rounds=6):
    """Resolve simultaneous combat with shield absorption and slow-blade penetration."""
    attacker_units = normalize_player_units(attacker_units)
    defender_units = normalize_player_units(defender_units) if defender_is_player else normalize_npc_units(defender_units)
    attacker = player_combat_profile(attacker_units, attacker_research)
    defender = player_combat_profile(defender_units, defender_research) if defender_is_player else npc_combat_profile(defender_units)
    attacker_body = attacker["body"]
    defender_body = defender["body"]
    attacker_shield = attacker["shield"]
    defender_shield = defender["shield"]
    attacker_absorbed = defender_absorbed = 0.0
    rounds = []

    def apply_damage(body, shield, incoming, piercing):
        bypass = min(max(piercing, 0), incoming)
        shield_hit = max(incoming - bypass, 0)
        absorbed = min(shield, shield_hit)
        shield -= absorbed
        body -= bypass + max(shield_hit - absorbed, 0)
        return max(body, 0), max(shield, 0), absorbed

    if defender["body"] <= 0:
        return {
            "victory": True, "round_count": 0, "rounds": [],
            "attacker_survivors": attacker_units, "defender_survivors": {},
            "attacker_shield_absorbed": 0, "defender_shield_absorbed": 0,
            "attacker_piercing_percent": 0, "defender_piercing_percent": 0,
        }

    for round_number in range(1, max_rounds + 1):
        attacker_strength = max(attacker_body / max(attacker["body"], 1), 0.18)
        defender_strength = max(defender_body / max(defender["body"], 1), 0.18)
        attacker_damage = attacker["attack"] * attacker_strength
        defender_damage = defender["attack"] * defender_strength
        attacker_piercing = attacker["piercing_attack"] * attacker_strength
        defender_piercing = defender["piercing_attack"] * defender_strength
        next_defender_body, next_defender_shield, absorbed_by_defender = apply_damage(
            defender_body, defender_shield, attacker_damage, attacker_piercing
        )
        next_attacker_body, next_attacker_shield, absorbed_by_attacker = apply_damage(
            attacker_body, attacker_shield, defender_damage, defender_piercing
        )
        attacker_absorbed += absorbed_by_attacker
        defender_absorbed += absorbed_by_defender
        rounds.append({
            "round": round_number,
            "attacker_damage": round(attacker_damage, 1),
            "defender_damage": round(defender_damage, 1),
            "attacker_shield_absorbed": round(absorbed_by_attacker, 1),
            "defender_shield_absorbed": round(absorbed_by_defender, 1),
        })
        attacker_body, attacker_shield = next_attacker_body, next_attacker_shield
        defender_body, defender_shield = next_defender_body, next_defender_shield
        if attacker_body <= 0 or defender_body <= 0:
            break

    attacker_ratio = min(attacker_body / max(attacker["body"], 1), 1)
    defender_ratio = min(defender_body / max(defender["body"], 1), 1)
    attacker_score = attacker_body + attacker_shield + attacker["attack"] * 0.75
    defender_score = defender_body + defender_shield + defender["attack"] * 0.75
    victory = defender_body <= 0 or (attacker_body > 0 and attacker_score > defender_score * 1.05)
    attacker_survivors = distribute_losses(attacker_units, attacker_ratio)
    defender_survivors = distribute_losses(defender_units, defender_ratio) if defender_is_player else distribute_npc_losses(defender_units, defender_ratio)
    return {
        "victory": victory,
        "round_count": len(rounds),
        "rounds": rounds,
        "attacker_survivors": attacker_survivors,
        "defender_survivors": defender_survivors,
        "attacker_shield_absorbed": round(attacker_absorbed, 1),
        "defender_shield_absorbed": round(defender_absorbed, 1),
        "attacker_piercing_percent": round(attacker["piercing_attack"] / max(attacker["attack"], 1) * 100),
        "defender_piercing_percent": round(defender["piercing_attack"] / max(defender["attack"], 1) * 100),
    }


def distribute_losses(sent_units, survival_ratio):
    survivors = {}
    for key, amount in sent_units.items():
        kept = int(amount * survival_ratio)
        if kept <= 0 and survival_ratio > 0.35 and amount > 0:
            kept = 1
        if kept > 0:
            survivors[key] = min(kept, amount)
    return survivors


def unit_losses(sent_units, survivors):
    losses = {}
    for key, amount in sent_units.items():
        lost = int(amount) - int(survivors.get(key, 0))
        if lost > 0:
            losses[key] = lost
    return losses


def unit_report_rows(units, faction_slug):
    rows = []
    for key, amount in units.items():
        if amount <= 0 or key not in UNIT_TYPES:
            continue
        rows.append({"key": key, "name": unit_display_name(key, faction_slug), "amount": amount})
    return rows


def create_battle_report(db, village, movement, tile, outcome, sent_units, survivors, enemy_before_units, enemy_after_units, loot, report_type="raid", *, combat=None, enemy_player_before=None, enemy_player_after=None, enemy_faction_slug=None):
    losses = unit_losses(sent_units, survivors)
    enemy_before_units = normalize_npc_units(enemy_before_units)
    enemy_after_units = normalize_npc_units(enemy_after_units)
    enemy_lost_units = npc_unit_losses(enemy_before_units, enemy_after_units)
    enemy_before = npc_units_defense(enemy_before_units)
    enemy_after = npc_units_defense(enemy_after_units)
    enemy_lost = max(enemy_before - enemy_after, 0)
    enemy_player_before = normalize_player_units(enemy_player_before or {})
    enemy_player_after = normalize_player_units(enemy_player_after or {})
    enemy_player_lost = unit_losses(enemy_player_before, enemy_player_after)
    if enemy_player_before:
        defender_research = ResearchLevels({}, enemy_faction_slug)
        enemy_before = int(player_unit_summary(enemy_player_before, defender_research)["defense"])
        enemy_after = int(player_unit_summary(enemy_player_after, defender_research)["defense"])
        enemy_lost = max(enemy_before - enemy_after, 0)
    target_name = tile_display_name(tile["tile_type"]) if tile else "Unknown Target"
    title = f"{outcome}: {target_name} ({movement['target_x']}, {movement['target_y']})"
    db.execute(
        """
        INSERT INTO battle_reports
            (user_id, village_id, movement_id, title, report_type, created_at, target_x, target_y,
             target_name, outcome, units_sent_json, units_survived_json, units_lost_json,
             enemy_before, enemy_after, enemy_lost, enemy_before_json, enemy_after_json, enemy_lost_json,
             enemy_player_before_json, enemy_player_after_json, enemy_player_lost_json, enemy_faction_slug, combat_json,
             loot_iron, loot_wood, loot_water, loot_spice)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            village["user_id"],
            village["id"],
            movement["id"],
            title,
            report_type,
            utc_now().isoformat(),
            movement["target_x"],
            movement["target_y"],
            target_name,
            outcome,
            json.dumps(sent_units),
            json.dumps(survivors),
            json.dumps(losses),
            enemy_before,
            enemy_after,
            enemy_lost,
            npc_units_json(enemy_before_units),
            npc_units_json(enemy_after_units),
            npc_units_json(enemy_lost_units),
            player_units_json(enemy_player_before),
            player_units_json(enemy_player_after),
            player_units_json(enemy_player_lost),
            enemy_faction_slug,
            json.dumps(combat or {}, sort_keys=True),
            loot["iron"],
            loot["wood"],
            loot["water"],
            loot["spice"],
        ),
    )


def take_loot(tile, carry_capacity):
    loot = {"iron": 0, "wood": 0, "water": 0, "spice": 0}
    remaining = carry_capacity
    for resource in ("spice", "water", "iron", "wood"):
        if remaining <= 0:
            break
        available = int(tile[f"resource_{resource}"])
        amount = min(available, remaining)
        loot[resource] = amount
        remaining -= amount
    return loot


def process_troop_movements(db, village_id):
    now = utc_now()
    village = db.execute("SELECT * FROM villages WHERE id = ?", (village_id,)).fetchone()
    research_levels = get_research_levels(db, village_id)
    outbound = db.execute("SELECT * FROM troop_movements WHERE village_id = ? AND status = 'outbound' ORDER BY arrive_at ASC", (village_id,)).fetchall()
    for movement in outbound:
        if parse_time(movement["arrive_at"]) > now:
            continue
        tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (movement["target_tile_id"],)).fetchone()
        sent_units = normalize_player_units(json.loads(movement["units_json"]))
        mission = movement["mission_type"]
        if mission == "scout":
            survivors = sent_units
            loot = {resource: int(tile[f"resource_{resource}"]) if tile else 0 for resource in ("iron", "wood", "water", "spice")}
            enemy_units = npc_units_for_tile(tile)
            if tile:
                save_tile_intel(db, village["user_id"], tile)
            create_battle_report(db, village, movement, tile, "Scouted", sent_units, survivors, enemy_units, enemy_units, loot, "scout")
            return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], sent_units, village_faction(db, village)))
            db.execute("UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, report = ? WHERE id = ?", (player_units_json(survivors), return_at.isoformat(), "Scouting complete. Aircraft and agents are returning with intelligence.", movement["id"]))
            continue
        if mission == "reinforce":
            if tile and tile["tile_type"] == "spice_bloom_large" and tile["controller_village_id"] == village_id:
                garrison = merge_player_units(player_units_from_json(tile["garrison_json"]), sent_units)
                db.execute("UPDATE map_tiles SET garrison_json = ? WHERE id = ?", (player_units_json(garrison), tile["id"]))
                db.execute("UPDATE troop_movements SET status = 'complete', survivors_json = ?, report = ? WHERE id = ?", (player_units_json(sent_units), "Reinforcements joined the bloom garrison.", movement["id"]))
            else:
                return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], sent_units, village_faction(db, village)))
                db.execute("UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, report = ? WHERE id = ?", (player_units_json(sent_units), return_at.isoformat(), "Control changed before arrival. Reinforcements are returning.", movement["id"]))
            continue
        if mission == "harvest":
            valid = tile and tile["tile_type"] == "spice_bloom_large" and tile["controller_village_id"] == village_id
            harvesters = sent_units.get("spice_harvester", 0)
            carryalls = sent_units.get("carryall", 0)
            metadata = json.loads(movement["mission_metadata_json"] or "{}")
            policy_key = metadata.get("policy", "balanced")
            policy = HARVEST_POLICIES.get(policy_key, HARVEST_POLICIES["balanced"])
            cooldown_active = bool(tile and tile["worm_cooldown_until"] and parse_time(tile["worm_cooldown_until"]) > now)
            if valid and harvesters > 0 and carryalls >= harvesters and not cooldown_active:
                finish_at = now + timedelta(seconds=policy["duration_seconds"])
                db.execute(
                    "UPDATE troop_movements SET status = 'harvesting', return_at = ?, report = ? WHERE id = ?",
                    (finish_at.isoformat(), f"{policy['name']} in progress. Carryall extraction is automatic.", movement["id"]),
                )
            else:
                return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], sent_units, village_faction(db, village)))
                report = "Harvest aborted because the bloom is unavailable or no longer under your control."
                db.execute("UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, report = ? WHERE id = ?", (player_units_json(sent_units), return_at.isoformat(), report, movement["id"]))
            continue
        if mission == "capture":
            if not tile or tile["tile_type"] != "spice_bloom_large" or tile["controller_village_id"] == village_id:
                return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], sent_units, village_faction(db, village)))
                db.execute("UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, report = ? WHERE id = ?", (player_units_json(sent_units), return_at.isoformat(), "Capture no longer required. Force is returning.", movement["id"]))
                continue
            defender_is_player = bool(tile["controller_village_id"])
            enemy_before_units = npc_units_for_tile(tile) if not defender_is_player else {}
            enemy_player_units = player_units_from_json(tile["garrison_json"]) if defender_is_player else {}
            defender_research = get_research_levels(db, tile["controller_village_id"]) if defender_is_player else None
            enemy_faction_slug = getattr(defender_research, "faction_slug", None)
            combat = resolve_combat(
                sent_units,
                enemy_player_units if defender_is_player else enemy_before_units,
                research_levels,
                defender_research,
                defender_is_player,
            )
            survivors = combat["attacker_survivors"]
            defender_survivors = combat["defender_survivors"]
            zero_loot = {"iron": 0, "wood": 0, "water": 0, "spice": 0}
            if combat["victory"]:
                if defender_is_player:
                    for key, amount in defender_survivors.items():
                        add_village_units(db, tile["controller_village_id"], key, amount)
                db.execute("UPDATE map_tiles SET controller_village_id = ?, garrison_json = ?, npc_strength = 0, npc_units_json = '{}' WHERE id = ?", (village_id, player_units_json(survivors), tile["id"]))
                db.execute("UPDATE troop_movements SET status = 'complete', survivors_json = ?, report = ? WHERE id = ?", (player_units_json(survivors), "Large Spice Bloom captured. Surviving forces now form its garrison.", movement["id"]))
                enemy_after_units = {}
                enemy_player_after = defender_survivors if defender_is_player else {}
                outcome = "Captured"
            else:
                if defender_is_player:
                    db.execute("UPDATE map_tiles SET garrison_json = ? WHERE id = ?", (player_units_json(defender_survivors), tile["id"]))
                    enemy_player_after = defender_survivors
                    enemy_after_units = {}
                else:
                    db.execute("UPDATE map_tiles SET npc_strength = ?, npc_units_json = ? WHERE id = ?", (npc_units_defense(defender_survivors), npc_units_json(defender_survivors), tile["id"]))
                    enemy_after_units = defender_survivors
                    enemy_player_after = {}
                if survivors:
                    return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], survivors, village_faction(db, village)))
                    db.execute("UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, report = ? WHERE id = ?", (player_units_json(survivors), return_at.isoformat(), "Capture failed. Survivors are retreating home.", movement["id"]))
                else:
                    db.execute("UPDATE troop_movements SET status = 'complete', survivors_json = '{}', report = ? WHERE id = ?", ("Capture failed. No attackers survived.", movement["id"]))
                outcome = "Defeat"
            create_battle_report(
                db, village, movement, tile, outcome, sent_units, survivors,
                enemy_before_units, enemy_after_units, zero_loot, "capture",
                combat=combat, enemy_player_before=enemy_player_units,
                enemy_player_after=enemy_player_after, enemy_faction_slug=enemy_faction_slug,
            )
            continue
        _attack, _durability, carry = sent_unit_stats(sent_units, research_levels)
        enemy_before_units = npc_units_for_tile(tile)
        combat = resolve_combat(sent_units, enemy_before_units, research_levels)
        survivors = combat["attacker_survivors"]
        enemy_after_units = combat["defender_survivors"]
        if combat["victory"]:
            _sa, _sd, survivor_carry = sent_unit_stats(survivors, research_levels)
            loot = take_loot(tile, survivor_carry) if tile else {"iron": 0, "wood": 0, "water": 0, "spice": 0}
            enemy_after_units = {}
            if tile:
                db.execute("UPDATE map_tiles SET npc_strength = 0, npc_units_json = '{}', resource_iron = resource_iron - ?, resource_wood = resource_wood - ?, resource_water = resource_water - ?, resource_spice = resource_spice - ? WHERE id = ?", (loot["iron"], loot["wood"], loot["water"], loot["spice"], tile["id"]))
            outcome, report = "Victory", "Raid won. Survivors are returning with loot."
        else:
            loot = {"iron": 0, "wood": 0, "water": 0, "spice": 0}
            if tile:
                db.execute("UPDATE map_tiles SET npc_strength = ?, npc_units_json = ? WHERE id = ?", (npc_units_defense(enemy_after_units), npc_units_json(enemy_after_units), tile["id"]))
            outcome, report = "Defeat", "Raid failed. Survivors are retreating home." if survivors else "Raid failed. No units survived."
        create_battle_report(db, village, movement, tile, outcome, sent_units, survivors, enemy_before_units, enemy_after_units, loot, combat=combat)
        return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], survivors or sent_units, village_faction(db, village)))
        db.execute("UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, loot_iron = ?, loot_wood = ?, loot_water = ?, loot_spice = ?, report = ? WHERE id = ?", (player_units_json(survivors), return_at.isoformat(), loot["iron"], loot["wood"], loot["water"], loot["spice"], report, movement["id"]))
    harvesting = db.execute("SELECT * FROM troop_movements WHERE village_id = ? AND status = 'harvesting' ORDER BY return_at ASC", (village_id,)).fetchall()
    for movement in harvesting:
        if not movement["return_at"] or parse_time(movement["return_at"]) > now:
            continue
        tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (movement["target_tile_id"],)).fetchone()
        sent_units = player_units_from_json(movement["units_json"])
        metadata = json.loads(movement["mission_metadata_json"] or "{}")
        policy_key = metadata.get("policy", "balanced")
        policy = HARVEST_POLICIES.get(policy_key, HARVEST_POLICIES["balanced"])
        valid = tile and tile["tile_type"] == "spice_bloom_large" and tile["controller_village_id"] == village_id
        survivors = dict(sent_units)
        spice = 0
        if valid:
            harvesters = sent_units.get("spice_harvester", 0)
            requested_spice = harvesters * policy["yield_per_harvester"]
            attention_before = float(tile["worm_attention"])
            attention_after = min(attention_before + policy["attention_gain"], 100)
            encounter_chance = min(0.02 + max(attention_after - 45, 0) / 125, 0.55)
            rng = random.Random(movement["id"] * 7919 + tile["id"] * 101)
            worm_encounter = rng.random() < encounter_chance
            available_spice = int(tile["resource_spice"])
            spice = min(available_spice, requested_spice)
            cooldown_until = None
            if worm_encounter:
                spice = int(spice * policy["cargo_retained_on_encounter"])
                lost_harvesters = sum(1 for _ in range(harvesters) if rng.random() < policy["loss_chance"])
                lost_carryalls = min(sent_units.get("carryall", 0), lost_harvesters + (1 if rng.random() < policy["loss_chance"] * 0.45 else 0))
                if lost_harvesters:
                    survivors["spice_harvester"] = max(survivors.get("spice_harvester", 0) - lost_harvesters, 0)
                if lost_carryalls:
                    survivors["carryall"] = max(survivors.get("carryall", 0) - lost_carryalls, 0)
                survivors = normalize_player_units(survivors)
                cooldown_until = (now + timedelta(minutes=5)).isoformat()
                report = f"Sandworm encountered. Automatic extraction saved {sum(survivors.values())} units and {spice} Spice Sand."
                if lost_harvesters or lost_carryalls:
                    report += f" Lost {lost_harvesters} Harvester(s) and {lost_carryalls} Carryall(s)."
                attention_after = 100
            else:
                report = f"{policy['name']} complete. Automatic extraction secured {spice} Spice Sand without a worm encounter."
            db.execute(
                "UPDATE map_tiles SET resource_spice = MAX(resource_spice - ?, 0), worm_attention = ?, worm_cooldown_until = ? WHERE id = ?",
                (spice, attention_after, cooldown_until, tile["id"]),
            )
        else:
            report = "Harvest interrupted because control of the bloom changed. The flight group is returning."
        return_at = now + timedelta(seconds=movement_duration_seconds(village["map_x"], village["map_y"], movement["target_x"], movement["target_y"], sent_units, village_faction(db, village)))
        db.execute(
            "UPDATE troop_movements SET status = 'returning', survivors_json = ?, return_at = ?, loot_spice = ?, report = ? WHERE id = ?",
            (player_units_json(survivors), return_at.isoformat(), spice, report, movement["id"]),
        )

    returning = db.execute("SELECT * FROM troop_movements WHERE village_id = ? AND status = 'returning' ORDER BY return_at ASC", (village_id,)).fetchall()
    for movement in returning:
        if not movement["return_at"] or parse_time(movement["return_at"]) > now:
            continue
        survivors = player_units_from_json(movement["survivors_json"])
        for key, amount in survivors.items():
            add_village_units(db, village_id, key, amount)
        db.execute("UPDATE villages SET iron = iron + ?, wood = wood + ?, water = water + ?, spice = spice + ? WHERE id = ?", (movement["loot_iron"], movement["loot_wood"], movement["loot_water"], movement["loot_spice"], village_id))
        db.execute("UPDATE troop_movements SET status = 'complete' WHERE id = ?", (movement["id"],))

def get_unit_training_queue(db, village_id):
    return db.execute(
        """
        SELECT * FROM unit_training_queue
        WHERE village_id = ?
        ORDER BY id ASC
        """,
        (village_id,),
    ).fetchall()


def process_unit_training_queue(db, village_id):
    now = utc_now()
    start_anchor = now
    while True:
        item = db.execute(
            "SELECT * FROM unit_training_queue WHERE village_id = ? ORDER BY id ASC LIMIT 1",
            (village_id,),
        ).fetchone()
        if not item:
            return

        if not item["started_at"]:
            finish_at = start_anchor + timedelta(seconds=item["duration_seconds"])
            db.execute(
                "UPDATE unit_training_queue SET started_at = ?, finish_at = ? WHERE id = ?",
                (start_anchor.isoformat(), finish_at.isoformat(), item["id"]),
            )
            item = db.execute("SELECT * FROM unit_training_queue WHERE id = ?", (item["id"],)).fetchone()

        finish_at = parse_time(item["finish_at"])
        if finish_at > now:
            return

        db.execute(
            """
            INSERT INTO village_units (village_id, unit_key, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(village_id, unit_key) DO UPDATE SET amount = amount + excluded.amount
            """,
            (village_id, item["unit_key"], item["amount"]),
        )
        db.execute("DELETE FROM unit_training_queue WHERE id = ?", (item["id"],))
        start_anchor = finish_at


def unit_display_name(unit_key, faction_slug):
    return UNIT_TYPES[unit_key]["names"][faction_slug]


def unit_training_cost(unit_key, amount):
    base_cost = UNIT_TYPES[unit_key]["cost"]
    return {resource: value * amount for resource, value in base_cost.items()}


def unit_training_duration(unit_key, amount, research_levels=None):
    duration = UNIT_TYPES[unit_key]["training_time"] * amount
    speed_level = (research_levels if research_levels is not None else {}).get("unit_training_speed", 0)
    return max(int(duration * (1 - min(speed_level * 0.05, 0.5))), 3)


def is_unit_unlocked(unit_key, buildings, research_levels=None):
    unit = UNIT_TYPES[unit_key]
    building_ok = buildings.get(unit["unlock_building"], 0) >= unit["unlock_level"]
    research_requirements = unit.get("unlock_research", {})
    research_ok = all((research_levels if research_levels is not None else {}).get(key, 0) >= level for key, level in research_requirements.items())
    return building_ok and research_ok


def unit_research_requirement_text(unit_key):
    return research_prerequisite_text(UNIT_TYPES[unit_key].get("unlock_research", {}))


def effective_unit_stat(unit_key, stat, research_levels):
    base_value = faction_unit_stat(unit_key, stat, getattr(research_levels, "faction_slug", None))
    key_map = {
        "health": "unit_health",
        "damage": "unit_damage",
        "armor": "unit_armor",
        "shield": "unit_shield",
    }
    research_key = key_map.get(stat)
    if not research_key or base_value == 0:
        return base_value
    return round(base_value * (1 + research_levels.get(research_key, 0) * 0.05), 1)


def build_unit_cards(village, buildings, research_levels, units, unit_queue, faction_slug, category):
    unit_queue_full = len(unit_queue) >= UNIT_QUEUE_LIMIT
    cards = []
    for key, config in UNIT_TYPES.items():
        if config["category"] != category:
            continue
        cost = unit_training_cost(key, 1)
        unlocked = is_unit_unlocked(key, buildings, research_levels)
        cards.append(
            {
                "key": key,
                "name": unit_display_name(key, faction_slug),
                "role": config.get("role"),
                "ability_summary": " ".join(filter(None, [config.get("ability_summary"), FACTION_ABILITIES[faction_slug]["description"] if category == "military" else None])),
                "abilities": config.get("abilities", []),
                "owned": units.get(key, 0),
                "unlocked": unlocked,
                "unlock_building": building_display_name(config["unlock_building"], faction_slug),
                "unlock_level": config["unlock_level"],
                "unlock_research": unit_research_requirement_text(key),
                "health": effective_unit_stat(key, "health", research_levels),
                "damage": effective_unit_stat(key, "damage", research_levels),
                "armor": effective_unit_stat(key, "armor", research_levels),
                "shield": effective_unit_stat(key, "shield", research_levels),
                "shield_piercing": round(faction_unit_stat(key, "shield_piercing", faction_slug) * 100),
                "speed": faction_unit_stat(key, "speed", faction_slug),
                "carry": config["carry"],
                "water_upkeep": faction_unit_stat(key, "water_upkeep", faction_slug),
                "training_time": unit_training_duration(key, 1, research_levels),
                "cost": cost,
                "missing_resources": missing_resources(village, cost),
                "queue_full": unit_queue_full,
                "can_train": unlocked and can_afford(village, cost) and not unit_queue_full,
            }
        )
    return cards


def filter_unit_queue_by_category(unit_queue, category):
    return [item for item in unit_queue if UNIT_TYPES[item["unit_key"]]["category"] == category]


def build_research_cards(village, buildings, research_levels, research_queue, faction_slug):
    research_queue_full = len(research_queue) >= RESEARCH_QUEUE_LIMIT
    cards = []
    for key, config in RESEARCH_TYPES.items():
        level = research_levels.get(key, 0)
        effective_level = effective_research_level(research_levels, research_queue, key)
        building_unmet = research_building_unmet(key, buildings)
        cost = research_cost(key, effective_level)
        maxed = effective_level >= config["max_level"]
        can_afford_research = can_afford(village, cost)
        cards.append(
            {
                "key": key,
                "name": config["name"],
                "description": config["description"],
                "level": level,
                "target_level": effective_level + 1,
                "max_level": config["max_level"],
                "cost": cost,
                "duration": research_duration_seconds(key, effective_level),
                "requirement_text": prerequisite_text_for_buildings(building_unmet, faction_slug),
                "missing_resources": missing_resources(village, cost),
                "queue_full": research_queue_full,
                "can_research": can_afford_research and not building_unmet and not research_queue_full and not maxed,
                "queued_count": queued_research_count(research_queue, key),
                "maxed": maxed,
                "effect_before": {
                    "throughput_bonus": effective_level * 8,
                    "conversion_ratio": spice_refinery_conversion_ratio({"spice_refining": effective_level}),
                } if key == "spice_refining" else None,
                "effect_after": {
                    "throughput_bonus": min(effective_level + 1, config["max_level"]) * 8,
                    "conversion_ratio": spice_refinery_conversion_ratio({"spice_refining": min(effective_level + 1, config["max_level"])}),
                } if key == "spice_refining" else None,
            }
        )
    return cards


def open_building_endpoint(building_key):
    if building_key == "command_center":
        return "command_center_page"
    if building_key == "barracks":
        return "barracks_page"
    if building_key == "research_center":
        return "research_center_page"
    if building_key == "influence_sanctuary":
        return "influence_sanctuary_page"
    if building_key == "flight_works":
        return "flight_works_page"
    if building_key == "deathstill":
        return "deathstill_page"
    return None


def deathstill_recovery_rate(level):
    """Return the share of a unit's training-water cost recovered by a Deathstill."""
    if level <= 0:
        return 0
    return min(0.65 + (level - 1) * 0.03, 0.95)


def deathstill_water_yield(unit_key, level):
    if unit_key not in UNIT_TYPES or UNIT_TYPES[unit_key]["category"] == "vehicle":
        return 0
    water_cost = UNIT_TYPES[unit_key]["cost"].get("water", 0)
    return max(int(math.floor(water_cost * deathstill_recovery_rate(level))), 1)


def build_building_cards(village, buildings, construction_queue, research_levels, faction_slug, building_keys=None):
    cards = []
    refinery_ratio = spice_refinery_conversion_ratio(research_levels)
    effective_spice_field_level = effective_building_level(buildings, construction_queue, "spice_field")
    field_sand_rate = production_for("spice_field", effective_spice_field_level, faction_slug)
    for key in building_keys or BUILDINGS.keys():
        config = BUILDINGS[key]
        level = buildings[key]
        effective_level = effective_building_level(buildings, construction_queue, key)
        unmet = unmet_prerequisites(key, buildings)
        research_unmet = building_research_unmet(key, research_levels)
        queue_full = len(construction_queue) >= CONSTRUCTION_QUEUE_LIMIT
        cost = upgrade_cost(key, effective_level)
        can_upgrade = can_afford(village, cost) and not unmet and not research_unmet and not queue_full
        cards.append(
            {
                "key": key,
                "name": building_display_name(key, faction_slug),
                "level": level,
                "effective_level": effective_level,
                "duration": construction_duration_seconds(effective_level, research_levels),
                "production_before": spice_refinery_output_rate(effective_level, faction_slug, research_levels) if key == "spice_refinery" else production_for(key, effective_level, faction_slug),
                "production_after": spice_refinery_output_rate(effective_level + 1, faction_slug, research_levels) if key == "spice_refinery" else production_for(key, effective_level + 1, faction_slug),
                "storage_before": 1000 + effective_level * 500,
                "storage_after": 1000 + (effective_level + 1) * 500,
                "deathstill_rate_before": deathstill_recovery_rate(effective_level) * 100 if key == "deathstill" else None,
                "deathstill_rate_after": deathstill_recovery_rate(effective_level + 1) * 100 if key == "deathstill" else None,
                "unlocks": [building_display_name(other, faction_slug) for other, definition in BUILDINGS.items()
                            if definition.get("requires", {}).get(key) == effective_level + 1]
                           + (["Alliances"] if key == "embassy" and effective_level + 1 == EMBASSY_ALLIANCE_LEVEL else []),
                "resource": config["resource"],
                "production": spice_refinery_output_rate(level, faction_slug, research_levels) if key == "spice_refinery" else production_for(key, level, faction_slug),
                "conversion_ratio": spice_refinery_conversion_ratio(research_levels) if key == "spice_refinery" else None,
                "sand_demand_before": spice_refinery_output_rate(effective_level, faction_slug, research_levels) * refinery_ratio if key == "spice_refinery" else None,
                "sand_demand_after": spice_refinery_output_rate(effective_level + 1, faction_slug, research_levels) * refinery_ratio if key == "spice_refinery" else None,
                "field_sand_rate": field_sand_rate if key == "spice_refinery" else None,
                "field_coverage_before": min(field_sand_rate / max(spice_refinery_output_rate(effective_level, faction_slug, research_levels) * refinery_ratio, 0.001) * 100, 100) if key == "spice_refinery" and effective_level > 0 else 0,
                "field_coverage_after": min(field_sand_rate / max(spice_refinery_output_rate(effective_level + 1, faction_slug, research_levels) * refinery_ratio, 0.001) * 100, 100) if key == "spice_refinery" else 0,
                "cost": cost,
                "missing_resources": missing_resources(village, cost),
                "queue_full": queue_full,
                "can_upgrade": can_upgrade,
                "unmet_prerequisites": unmet,
                "prerequisite_text": prerequisite_text(key, faction_slug),
                "research_prerequisite_text": research_prerequisite_text(research_unmet),
                "queued_count": queued_upgrade_count(construction_queue, key),
                "open_endpoint": open_building_endpoint(key),
                "art_image": building_art_image(key),
            }
        )
    return cards


def building_art_image(building_key):
    png_path = os.path.join(BASE_DIR, "static", "images", "buildings", f"{building_key}.png")
    if os.path.exists(png_path):
        return f"images/buildings/{building_key}.png"
    return f"images/buildings/{building_key}.svg"


def base_building_art_image(building_key, level):
    level_art = {
        "research_center": [(5, "images/base/building_research_center_level5.png")],
        "embassy": [(10, "images/base/building_embassy_level_10.png")],
        "deathstill": [(5, "images/base/building_deathstill_level_5.png")],
        "influence_sanctuary": [(5, "images/base/building_influence_sanctuary_level_5.png")],
    }
    for minimum_level, art_path in level_art.get(building_key, []):
        if level >= minimum_level and os.path.exists(os.path.join(BASE_DIR, "static", art_path)):
            return art_path

    base_path = f"images/base/building_{building_key}.png"
    if os.path.exists(os.path.join(BASE_DIR, "static", base_path)):
        return base_path
    return building_art_image(building_key)


def build_base_visual_slots(building_cards):
    cards_by_key = {card["key"]: card for card in building_cards}
    slots = []
    for slot in BASE_VISUAL_SLOTS:
        building = cards_by_key.get(slot["building_key"])
        slots.append(
            {
                "key": slot["key"],
                "foundation": slot["foundation"],
                "building": building,
                "art_image": base_building_art_image(building["key"], building["level"]) if building else None,
            }
        )
    return slots


def resource_field_scene_image(buildings):
    field_levels = [buildings.get(key, 0) for key in ("wood_yard", "iron_mine", "dew_field", "spice_field")]
    average_level = sum(field_levels) / len(field_levels)
    if average_level >= 14:
        stage = "04_developed"
    elif average_level >= 8:
        stage = "03_medium"
    elif average_level >= 4:
        stage = "02_light"
    else:
        stage = "01_sparse"

    has_windtrap = buildings.get("windtrap", 0) > 0 or buildings.get("large_windtrap", 0) > 0
    windtrap_available = buildings.get("dew_field", 0) >= BUILDINGS["windtrap"]["requires"]["dew_field"]
    if has_windtrap:
        suffix = "_with_windtraps"
    elif windtrap_available:
        suffix = "_open_spots"
    else:
        suffix = ""
    return f"images/field_level_{stage}{suffix}.png"


def village_scene_image():
    for base_background in (
        "images/base/base_courtyard_background_v2.png",
        "images/base/base_courtyard_background.png",
    ):
        if os.path.exists(os.path.join(BASE_DIR, "static", base_background)):
            return base_background
    return "images/desert_village_background.png"


def base_nav_items(buildings, faction_slug):
    return [
        {
            "key": "base",
            "label": "Base",
            "endpoint": "dashboard",
            "level": None,
            "locked": False,
            "hint": "Main base overview",
        },
        {
            "key": "fields",
            "label": "Resource Fields",
            "endpoint": "resource_fields_page",
            "level": None,
            "locked": False,
            "hint": "Production fields",
        },
        {
            "key": "command_center",
            "label": "Command Center",
            "endpoint": "command_center_page",
            "level": buildings.get("command_center", 0),
            "locked": buildings.get("command_center", 0) <= 0,
            "hint": "Missions and reports",
        },
        {
            "key": "barracks",
            "label": "Barracks",
            "endpoint": "barracks_page",
            "level": buildings.get("barracks", 0),
            "locked": buildings.get("barracks", 0) <= 0,
            "hint": "Train military units",
        },
        {
            "key": "flight_works",
            "label": "Flight Works",
            "endpoint": "flight_works_page",
            "level": buildings.get("flight_works", 0),
            "locked": buildings.get("flight_works", 0) <= 0,
            "hint": "Aircraft and harvesters",
        },
        {
            "key": "deathstill",
            "label": "Deathstill",
            "endpoint": "deathstill_page",
            "level": buildings.get("deathstill", 0),
            "locked": buildings.get("deathstill", 0) <= 0,
            "hint": "Reclaim troop water",
        },
        {
            "key": "research_center",
            "label": "Research Center",
            "endpoint": "research_center_page",
            "level": buildings.get("research_center", 0),
            "locked": buildings.get("research_center", 0) <= 0,
            "hint": "Research upgrades",
        },
        {
            "key": "influence_sanctuary",
            "label": building_display_name("influence_sanctuary", faction_slug),
            "endpoint": "influence_sanctuary_page",
            "level": buildings.get("influence_sanctuary", 0),
            "locked": buildings.get("influence_sanctuary", 0) <= 0,
            "hint": "Influence units",
        },
    ]


def unit_water_consumption_per_hour(units, faction_slug=None):
    return sum(faction_unit_stat(key, "water_upkeep", faction_slug) * amount for key, amount in units.items() if key in UNIT_TYPES)


def production_for(building_key, level, faction_slug):
    building = BUILDINGS[building_key]
    resource = building["resource"]
    if not resource:
        return 0

    amount = building["base_production"] * (level ** 1.25)
    if faction_slug == "atreides" and resource == "wood":
        amount *= 1.05
    if faction_slug == "harkonnen" and resource == "iron":
        amount *= 1.05
    if faction_slug == "fremen" and resource == "water":
        amount *= 1.10
    return amount


def spice_refinery_output_rate(level, faction_slug, research_levels=None):
    research_level = (research_levels if research_levels is not None else {}).get("spice_refining", 0)
    return production_for("spice_refinery", level, faction_slug) * (1 + research_level * 0.08)


def spice_refinery_conversion_ratio(research_levels=None):
    research_level = (research_levels if research_levels is not None else {}).get("spice_refining", 0)
    return round(max(3.0, BUILDINGS["spice_refinery"]["conversion_ratio"] - research_level * 0.1), 1)


def water_consumption_per_hour(buildings, units=None, faction_slug=None):
    return 2 + sum(buildings.values()) * 0.5 + unit_water_consumption_per_hour(units or {}, faction_slug)


def storage_capacity(buildings):
    return 1000 + buildings.get("warehouse", 1) * 500


def update_resources(db, village, faction_slug):
    buildings = get_buildings(db, village["id"])
    units = get_village_units(db, village["id"])
    research_levels = get_research_levels(db, village["id"])
    last_update = parse_time(village["last_resource_update"])
    hours = max((utc_now() - last_update).total_seconds() / 3600, 0)
    if hours <= 0:
        return village, buildings

    production = resource_rates(buildings, faction_slug, units, research_levels)
    capacity = storage_capacity(buildings)
    refinery_level = buildings.get("spice_refinery", 0)
    refinery_rate = spice_refinery_output_rate(refinery_level, faction_slug, research_levels) if refinery_level else 0
    ratio = spice_refinery_conversion_ratio(research_levels)
    gross_spice_rate = production["spice"] + refinery_rate * ratio
    available_sand = max(village["spice"] + gross_spice_rate * hours, 0)
    refined_melange = min(
        refinery_rate * hours,
        available_sand / ratio,
        max(capacity - village["melange"], 0),
    )
    new_values = {
        "iron": min(village["iron"] + production["iron"] * hours, capacity),
        "wood": min(village["wood"] + production["wood"] * hours, capacity),
        "water": min(max(village["water"] + production["water"] * hours, 0), capacity),
        "spice": min(max(available_sand - refined_melange * ratio, 0), capacity),
        "melange": min(village["melange"] + refined_melange, capacity),
    }

    db.execute(
        """
        UPDATE villages
        SET iron = ?, wood = ?, water = ?, spice = ?, melange = ?, last_resource_update = ?
        WHERE id = ?
        """,
        (
            new_values["iron"],
            new_values["wood"],
            new_values["water"],
            new_values["spice"],
            new_values["melange"],
            utc_now().isoformat(),
            village["id"],
        ),
    )
    return get_village(db, village["user_id"]), buildings


def resource_rates(buildings, faction_slug, units=None, research_levels=None):
    rates = {"iron": 0, "wood": 0, "water": 0, "spice": 0, "melange": 0}
    for building_key, level in buildings.items():
        resource = BUILDINGS[building_key]["resource"]
        if resource:
            if building_key == "spice_refinery":
                rates[resource] += spice_refinery_output_rate(level, faction_slug, research_levels)
            else:
                rates[resource] += production_for(building_key, level, faction_slug)
    rates["water"] -= water_consumption_per_hour(buildings, units, faction_slug)
    refinery_rate = rates["melange"]
    rates["spice"] -= refinery_rate * spice_refinery_conversion_ratio(research_levels)
    return rates


def upgrade_cost(building_key, current_level):
    building = BUILDINGS[building_key]
    base_cost = building["cost"]
    multiplier = 1.6 ** current_level
    cost = {resource: int(amount * multiplier) for resource, amount in base_cost.items()}
    if current_level >= building.get("melange_from_level", 10_000):
        refinery_tier = current_level - building["melange_from_level"] + 1
        cost["melange"] = max(cost.get("melange", 0), building["melange_base_cost"] * refinery_tier)
    return cost


def can_afford(village, cost):
    return all(village[resource] >= amount for resource, amount in cost.items())


def missing_resources(village, cost):
    return {resource: max(int(math.ceil(amount - village[resource])), 0) for resource, amount in cost.items() if village[resource] < amount}


def points_from_spending(wood_spent, water_spent):
    return int((wood_spent + water_spent) // POINT_VALUE)


def record_point_spending(db, village, cost):
    wood_spent = village["point_wood_spent"] + cost.get("wood", 0)
    water_spent = village["point_water_spent"] + cost.get("water", 0)
    points = points_from_spending(wood_spent, water_spent)
    db.execute(
        """
        UPDATE villages
        SET point_wood_spent = ?, point_water_spent = ?, points = ?
        WHERE id = ?
        """,
        (wood_spent, water_spent, points, village["id"]),
    )


def get_membership(db, user_id):
    return db.execute(
        """
        SELECT alliance_members.*, alliances.name AS alliance_name, alliances.tag AS alliance_tag
        FROM alliance_members
        JOIN alliances ON alliances.id = alliance_members.alliance_id
        WHERE alliance_members.user_id = ?
        """,
        (user_id,),
    ).fetchone()


def get_alliance_stats(db, alliance_id):
    return db.execute(
        """
        SELECT alliances.*, COUNT(DISTINCT alliance_members.user_id) AS member_count,
               COALESCE(SUM(villages.points), 0) AS total_points
        FROM alliances
        LEFT JOIN alliance_members ON alliance_members.alliance_id = alliances.id
        LEFT JOIN villages ON villages.user_id = alliance_members.user_id
        WHERE alliances.id = ?
        GROUP BY alliances.id
        """,
        (alliance_id,),
    ).fetchone()


def get_alliance_member_or_redirect(db):
    user = get_current_user(db)
    membership = get_membership(db, user["id"])
    if not membership:
        return user, None
    return user, membership


def can_use_alliance_features(buildings):
    return buildings.get("embassy", 0) >= EMBASSY_ALLIANCE_LEVEL


def get_tutorial_progress(db, user_id):
    progress = db.execute(
        "SELECT * FROM user_tutorial_progress WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if progress:
        return progress
    db.execute(
        "INSERT INTO user_tutorial_progress (user_id, step_index, updated_at) VALUES (?, 0, ?)",
        (user_id, utc_now().isoformat()),
    )
    return db.execute("SELECT * FROM user_tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()


def get_tutorial_state(db, user, buildings):
    progress = get_tutorial_progress(db, user["id"])
    step_index = progress["step_index"]
    if step_index >= len(TUTORIAL_STEPS):
        return {"complete": True, "guide": TUTORIAL_GUIDES[user["faction_slug"]]}
    step = TUTORIAL_STEPS[step_index]
    is_ready = True
    if "building" in step:
        is_ready = is_ready and buildings.get(step["building"], 0) >= step["level"]
    if "unit" in step:
        village = get_village(db, user["id"])
        units = get_village_units(db, village["id"])
        is_ready = is_ready and units.get(step["unit"], 0) >= step["amount"]
    if "report_type" in step:
        query = "SELECT COUNT(*) AS count FROM battle_reports WHERE user_id = ? AND report_type = ?"
        params = [user["id"], step["report_type"]]
        if step.get("report_read"):
            query += " AND is_read = 1"
        report_count = db.execute(query, params).fetchone()["count"]
        is_ready = is_ready and report_count > 0
    return {
        "complete": False,
        "guide": TUTORIAL_GUIDES[user["faction_slug"]],
        "index": step_index + 1,
        "total": len(TUTORIAL_STEPS),
        "step": step,
        "ready": is_ready,
    }


def apply_resource_reward(db, village_id, reward):
    db.execute(
        """
        UPDATE villages
        SET iron = iron + ?, wood = wood + ?, water = water + ?, spice = spice + ?
        WHERE id = ?
        """,
        (reward["iron"], reward["wood"], reward["water"], reward["spice"], village_id),
    )


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


def hash_account_password(password):
    return generate_password_hash(password, method="scrypt:32768:8:1")


DUMMY_PASSWORD_HASH = hash_account_password("unused-login-timing-password")


def login_token_hash():
    token = session.get("login_token", "")
    return hashlib.sha256(token.encode()).hexdigest() if isinstance(token, str) and token else None


@app.before_request
def validate_login_session():
    g.csp_nonce = secrets.token_urlsafe(18)
    if app.config["PRODUCTION"] and not request.is_secure:
        abort(400, description="HTTPS is required.")
    if request.endpoint == "static" or "user_id" not in session:
        return
    now = utc_now().timestamp()
    token_hash = login_token_hash()
    with get_db() as db:
        row = db.execute("SELECT login_sessions.* FROM login_sessions JOIN users ON users.id = login_sessions.user_id WHERE token_hash = ? AND user_id = ?", (token_hash, session["user_id"])).fetchone()
        valid = row and row["expires_at"] > now and row["last_seen"] > now - 3600
        if valid:
            db.execute("UPDATE login_sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash))
        elif token_hash:
            db.execute("DELETE FROM login_sessions WHERE token_hash = ?", (token_hash,))
        db.commit()
    if not valid:
        session.clear()
        flash("Your session ended. Please log in again.")
        return redirect(url_for("login"))


@app.after_request
def security_headers(response):
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'nonce-" + nonce + "'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    if app.config["PRODUCTION"]:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.context_processor
def inject_auth_token():
    if "auth_csrf_token" not in session:
        session["auth_csrf_token"] = secrets.token_hex(32)
    return {"auth_csrf_token": session["auth_csrf_token"], "csp_nonce": getattr(g, "csp_nonce", "")}


@app.before_request
def protect_account_forms():
    if request.endpoint in ("dev_reset_password", "dev_boost_resources"):
        if not DEV_TOOLS_ENABLED or app.config["PRODUCTION"] or request.remote_addr not in ("127.0.0.1", "::1"):
            abort(404)
        if request.endpoint == "dev_reset_password" and not os.environ.get("DEV_ADMIN_TOKEN"):
            abort(404)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.endpoint:
        if request.endpoint not in ("register", "login", "dev_reset_password") and "user_id" not in session:
            return redirect(url_for("login"))
        token = session.get("auth_csrf_token", "")
        if not token or not secrets.compare_digest(token.encode(), request.form.get("csrf_token", "").encode()):
            abort(400, description="This account form expired. Refresh the page and try again.")


def allow_auth_attempt(action, username=""):
    """Persistent rate limits; direct client address only, no trusted forwarding headers."""
    address = request.remote_addr or "unknown"
    digest = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
    limits = [(action + ":ip:" + digest(address), 20 if action == "register" else 30, 3600 if action == "register" else 900)]
    if action == "login":
        limits.append(("login:account:" + digest(address + ":" + username.lower()), 8, 900))
    now = utc_now().timestamp()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM auth_attempts WHERE attempted_at < ?", (now - 3600,))
        for bucket, limit, window in limits:
            count = db.execute("SELECT COUNT(*) FROM auth_attempts WHERE bucket = ? AND attempted_at > ?", (bucket, now - window)).fetchone()[0]
            if count >= limit:
                db.commit()
                return False
        db.executemany("INSERT INTO auth_attempts (bucket, attempted_at) VALUES (?, ?)", [(bucket, now) for bucket, _, _ in limits])
        db.commit()
    return True


def valid_signup_password(password):
    return 15 <= len(password) <= 128 and bool(password.strip())


@app.route("/register", methods=("GET", "POST"))
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    username = request.form.get("username", "").strip() if request.method == "POST" else ""
    faction_slug = request.form.get("faction", "atreides")
    status = 200
    invite_code = os.environ.get("REGISTRATION_INVITE_CODE", "")
    with get_db() as db:
        factions = db.execute("SELECT * FROM factions ORDER BY id").fetchall()
        if request.method == "POST":
            password = request.form.get("password", "")
            confirmation = request.form.get("confirm_password", "")
            faction = db.execute("SELECT * FROM factions WHERE slug = ?", (faction_slug,)).fetchone()
            if not allow_auth_attempt("register"):
                flash("Too many sign-up attempts. Please try again in one hour.")
                status = 429
            elif invite_code and not secrets.compare_digest(invite_code.encode(), request.form.get("invite_code", "").encode()):
                flash("Enter the invite code provided by the game host.")
                status = 400
            elif not re.fullmatch(r"[A-Za-z0-9_-]{3,24}", username):
                flash("Username must be 3–24 letters, numbers, underscores or hyphens.")
                status = 400
            elif not valid_signup_password(password):
                flash("Use a password or passphrase of 15–128 characters.")
                status = 400
            elif password != confirmation:
                flash("Passwords do not match. Enter them again.")
                status = 400
            elif not faction:
                flash("Choose Atreides, Harkonnen or Fremen.")
                status = 400
            else:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone():
                    flash("That username is already taken. Choose another.")
                    status = 409
                else:
                    try:
                        cursor = db.execute("INSERT INTO users (username, password_hash, faction_id, created_at) VALUES (?, ?, ?, ?)",
                                            (username, hash_account_password(password), faction["id"], utc_now().isoformat()))
                        create_starting_village(db, cursor.lastrowid)
                        village = get_village(db, cursor.lastrowid)
                        ensure_village_map_position(db, village)
                        db.commit()
                        flash("Your house is founded! Log in to begin the tutorial.")
                        return redirect(url_for("login"))
                    except (sqlite3.IntegrityError, RuntimeError):
                        db.rollback()
                        flash("Your account could not be created. The world may be full; contact the game host.")
                        status = 409
    return render_template("register.html", factions=factions, entered_username=username,
                           selected_faction=faction_slug, invite_required=bool(invite_code)), status


@app.route("/login", methods=("GET", "POST"))
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    status = 200
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not allow_auth_attempt("login", username):
            flash("Too many login attempts. Please try again in 15 minutes.")
            status = 429
        else:
            with get_db() as db:
                # Exact matches preserve legacy accounts differing only by case.
                user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                if not user:
                    matches = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchall()
                    user = matches[0] if len(matches) == 1 else None
            valid = check_password_hash(user["password_hash"] if user else DUMMY_PASSWORD_HASH, password) if len(password) <= 4096 else False
            if user and valid:
                token = secrets.token_urlsafe(32)
                now = utc_now().timestamp()
                with get_db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
                    if not current or current["password_hash"] != user["password_hash"]:
                        flash("Invalid username or password.")
                        return render_template("login.html"), 400
                    db.execute("DELETE FROM login_sessions WHERE expires_at <= ? OR last_seen <= ?", (now, now - 3600))
                    if not user["password_hash"].startswith("scrypt:32768:8:1$"):
                        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_account_password(password), user["id"]))
                    db.execute("INSERT INTO login_sessions (token_hash, user_id, created_at, last_seen, expires_at) VALUES (?, ?, ?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], now, now, now + 86400))
                    db.commit()
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["login_token"] = token
                return redirect(url_for("dashboard"))
            flash("Invalid username or password.")
            status = 400
    return render_template("login.html"), status


@app.route("/logout", methods=("POST",))
def logout():
    with get_db() as db:
        db.execute("DELETE FROM login_sessions WHERE token_hash = ?", (login_token_hash(),))
        db.commit()
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=("GET", "POST"))
@login_required
def account_settings():
    with get_db() as db:
        user = get_current_user(db)
        if request.method == "POST":
            if not allow_auth_attempt("login", "password-change:" + str(user["id"])):
                flash("Too many attempts. Try again in 15 minutes.")
                return render_template("account.html", user=user), 429
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM login_sessions WHERE token_hash = ? AND user_id = ?", (login_token_hash(), user["id"])).fetchone()
            if not active:
                session.clear()
                return redirect(url_for("login"))
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
            current = request.form.get("current_password", "")
            password = request.form.get("new_password", "")
            if len(current) > 4096 or not check_password_hash(row["password_hash"], current):
                flash("Current password is incorrect.")
                return render_template("account.html", user=user), 400
            if not valid_signup_password(password) or password != request.form.get("confirm_password", ""):
                flash("Use matching new passwords of 15–128 characters.")
                return render_template("account.html", user=user), 400
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_account_password(password), user["id"]))
            db.execute("DELETE FROM login_sessions WHERE user_id = ?", (user["id"],))
            db.commit()
            session.clear()
            flash("Password changed. All devices have been signed out. Log in with your new password.")
            return redirect(url_for("login"))
    return render_template("account.html", user=user)


@app.route("/dashboard")
@login_required
def dashboard():
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_unit_training_queue(db, village["id"])
        process_research_queue(db, village["id"])
        ensure_village_map_position(db, village)
        regenerate_map_tiles(db)
        process_troop_movements(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        buildings = get_buildings(db, village["id"])
        research_levels = get_research_levels(db, village["id"])
        units = get_village_units(db, village["id"])
        rates = resource_rates(buildings, user["faction_slug"], units, research_levels)
        capacity = storage_capacity(buildings)
        membership = get_membership(db, user["id"])
        construction_queue = get_construction_queue(db, village["id"])
        tutorial = get_tutorial_state(db, user, buildings)
        latest_reports = db.execute(
            """
            SELECT * FROM battle_reports
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 3
            """,
            (user["id"],),
        ).fetchall()
        alerts = dashboard_alerts(village, rates, capacity, buildings, construction_queue, latest_reports)
        db.commit()
        all_building_cards = build_building_cards(village, buildings, construction_queue, research_levels, user["faction_slug"])
        building_cards = [card for card in all_building_cards if card["key"] in INFRASTRUCTURE_BUILDING_KEYS]
        priorities = dashboard_priorities(village, rates, capacity, buildings, construction_queue, tutorial, latest_reports, all_building_cards, units)
        base_visual_slots = build_base_visual_slots(building_cards)
    return render_template(
        "dashboard.html",
        user=user,
        village=village,
        rates=rates,
        capacity=capacity,
        buildings=building_cards,
        water_consumption=water_consumption_per_hour(buildings, units, user["faction_slug"]),
        membership=membership,
        embassy_alliance_level=EMBASSY_ALLIANCE_LEVEL,
        construction_queue=construction_queue,
        construction_queue_limit=CONSTRUCTION_QUEUE_LIMIT,
        queue_status=queue_status,
        building_defs=BUILDINGS,
        tutorial=tutorial,
        latest_reports=latest_reports,
        alerts=alerts,
        priorities=priorities,
        dev_tools_enabled=DEV_TOOLS_ENABLED,
        village_background_image=village_scene_image(),
        base_visual_slots=base_visual_slots,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="base",
    )


@app.route("/fields")
@login_required
def resource_fields_page():
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_unit_training_queue(db, village["id"])
        process_research_queue(db, village["id"])
        ensure_village_map_position(db, village)
        regenerate_map_tiles(db)
        process_troop_movements(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        buildings = get_buildings(db, village["id"])
        research_levels = get_research_levels(db, village["id"])
        units = get_village_units(db, village["id"])
        rates = resource_rates(buildings, user["faction_slug"], units, research_levels)
        capacity = storage_capacity(buildings)
        construction_queue = get_construction_queue(db, village["id"])
        field_cards = build_building_cards(village, buildings, construction_queue, research_levels, user["faction_slug"], RESOURCE_BUILDING_KEYS)
    return render_template(
        "fields.html",
        user=user,
        village=village,
        rates=rates,
        capacity=capacity,
        fields=field_cards,
        water_consumption=water_consumption_per_hour(buildings, units, user["faction_slug"]),
        construction_queue=construction_queue,
        construction_queue_limit=CONSTRUCTION_QUEUE_LIMIT,
        queue_status=queue_status,
        building_defs=BUILDINGS,
        field_background_image=resource_field_scene_image(buildings),
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="fields",
    )


@app.route("/map")
@app.route("/map/<int:tile_id>")
@login_required
def map_page(tile_id=None):
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_unit_training_queue(db, village["id"])
        process_research_queue(db, village["id"])
        ensure_village_map_position(db, village)
        regenerate_map_tiles(db)
        process_troop_movements(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        units = get_village_units(db, village["id"])
        tiles = db.execute(
            """
            SELECT map_tiles.*, villages.name AS village_name, users.username AS owner_name, factions.slug AS owner_faction_slug,
                   controlled_village.name AS controller_village_name, controlled_user.username AS controller_name,
                   controlled_faction.slug AS controller_faction_slug
            FROM map_tiles
            LEFT JOIN villages ON villages.id = map_tiles.village_id
            LEFT JOIN users ON users.id = villages.user_id
            LEFT JOIN factions ON factions.id = users.faction_id
            LEFT JOIN villages AS controlled_village ON controlled_village.id = map_tiles.controller_village_id
            LEFT JOIN users AS controlled_user ON controlled_user.id = controlled_village.user_id
            LEFT JOIN factions AS controlled_faction ON controlled_faction.id = controlled_user.faction_id
            WHERE map_tiles.x BETWEEN ? AND ? AND map_tiles.y BETWEEN ? AND ?
            ORDER BY map_tiles.y ASC, map_tiles.x ASC
            """,
            (-MAP_RADIUS, MAP_RADIUS, -MAP_RADIUS, MAP_RADIUS),
        ).fetchall()
        selected_tile = None
        if tile_id:
            selected_tile = db.execute(
                """
                SELECT map_tiles.*, villages.name AS village_name, users.username AS owner_name, factions.slug AS owner_faction_slug,
                       controlled_village.name AS controller_village_name, controlled_user.username AS controller_name,
                       controlled_faction.slug AS controller_faction_slug
                FROM map_tiles
                LEFT JOIN villages ON villages.id = map_tiles.village_id
                LEFT JOIN users ON users.id = villages.user_id
                LEFT JOIN factions ON factions.id = users.faction_id
                LEFT JOIN villages AS controlled_village ON controlled_village.id = map_tiles.controller_village_id
                LEFT JOIN users AS controlled_user ON controlled_user.id = controlled_village.user_id
                LEFT JOIN factions AS controlled_faction ON controlled_faction.id = controlled_user.faction_id
                WHERE map_tiles.id = ?
                """,
                (tile_id,),
            ).fetchone()
        elif request.args.get("x") is not None and request.args.get("y") is not None:
            try:
                target_x = int(request.args.get("x", "0"))
                target_y = int(request.args.get("y", "0"))
            except ValueError:
                flash("Coordinates must be numbers.")
                target_x = village["map_x"]
                target_y = village["map_y"]
            selected_tile = db.execute(
                """
                SELECT map_tiles.*, villages.name AS village_name, users.username AS owner_name, factions.slug AS owner_faction_slug,
                       controlled_village.name AS controller_village_name, controlled_user.username AS controller_name,
                       controlled_faction.slug AS controller_faction_slug
                FROM map_tiles
                LEFT JOIN villages ON villages.id = map_tiles.village_id
                LEFT JOIN users ON users.id = villages.user_id
                LEFT JOIN factions ON factions.id = users.faction_id
                LEFT JOIN villages AS controlled_village ON controlled_village.id = map_tiles.controller_village_id
                LEFT JOIN users AS controlled_user ON controlled_user.id = controlled_village.user_id
                LEFT JOIN factions AS controlled_faction ON controlled_faction.id = controlled_user.faction_id
                WHERE map_tiles.x = ? AND map_tiles.y = ?
                """,
                (target_x, target_y),
            ).fetchone()
            if not selected_tile:
                flash("No tile exists at those coordinates.")
        if not selected_tile:
            selected_tile = db.execute(
                """
                SELECT map_tiles.*, villages.name AS village_name, users.username AS owner_name, factions.slug AS owner_faction_slug,
                       controlled_village.name AS controller_village_name, controlled_user.username AS controller_name,
                       controlled_faction.slug AS controller_faction_slug
                FROM map_tiles
                LEFT JOIN villages ON villages.id = map_tiles.village_id
                LEFT JOIN users ON users.id = villages.user_id
                LEFT JOIN factions ON factions.id = users.faction_id
                LEFT JOIN villages AS controlled_village ON controlled_village.id = map_tiles.controller_village_id
                LEFT JOIN users AS controlled_user ON controlled_user.id = controlled_village.user_id
                LEFT JOIN factions AS controlled_faction ON controlled_faction.id = controlled_user.faction_id
                WHERE map_tiles.x = ? AND map_tiles.y = ?
                """,
                (village["map_x"], village["map_y"]),
            ).fetchone()
        selected_intel = get_tile_intel(db, user["id"], selected_tile["id"]) if selected_tile else None
        movements = db.execute(
            """
            SELECT troop_movements.*, map_tiles.tile_type
            FROM troop_movements
            JOIN map_tiles ON map_tiles.id = troop_movements.target_tile_id
            WHERE troop_movements.village_id = ? AND troop_movements.status IN ('outbound', 'harvesting', 'returning')
            ORDER BY troop_movements.arrive_at ASC
            """,
            (village["id"],),
        ).fetchall()
        movement_targets = {movement["target_tile_id"] for movement in movements}
        scouted_tile_ids = {
            row["tile_id"]
            for row in db.execute("SELECT tile_id FROM tile_intel WHERE user_id = ?", (user["id"],)).fetchall()
        }
        bookmarked_tile_ids = {
            row["tile_id"]
            for row in db.execute("SELECT tile_id FROM map_bookmarks WHERE user_id = ?", (user["id"],)).fetchall()
        }
        recent_intel = db.execute(
            """
            SELECT tile_intel.*, map_tiles.x, map_tiles.y, map_tiles.tile_type
            FROM tile_intel
            JOIN map_tiles ON map_tiles.id = tile_intel.tile_id
            WHERE tile_intel.user_id = ?
            ORDER BY tile_intel.scouted_at DESC
            LIMIT 5
            """,
            (user["id"],),
        ).fetchall()
        research_levels = get_research_levels(db, village["id"])
        unit_cards = []
        scout_cards = []
        harvest_cards = []
        for key, amount in units.items():
            if amount <= 0:
                continue
            card = {
                "key": key,
                "name": unit_display_name(key, user["faction_slug"]),
                "amount": amount,
                "speed": faction_unit_stat(key, "speed", user["faction_slug"]),
                "carry": UNIT_TYPES[key]["carry"],
                "damage": effective_unit_stat(key, "damage", research_levels),
                "shield": effective_unit_stat(key, "shield", research_levels),
                "shield_piercing": round(faction_unit_stat(key, "shield_piercing", user["faction_slug"]) * 100),
                "durability": effective_unit_stat(key, "health", research_levels)
                + effective_unit_stat(key, "armor", research_levels) * 5
                + effective_unit_stat(key, "shield", research_levels) * 3,
            }
            config = UNIT_TYPES[key]
            if config["category"] == "military" or config.get("can_raid") or config.get("is_transport"):
                unit_cards.append(card)
            if config["category"] == "influence" or config.get("can_scout"):
                scout_cards.append(card)
            if config.get("is_harvester") or config.get("is_transport"):
                harvest_cards.append(card)
        tile_rows = []
        for y in range(-MAP_RADIUS, MAP_RADIUS + 1):
            row = []
            for tile in tiles:
                if tile["y"] == y:
                    row.append(tile)
            tile_rows.append(row)
        controller_research = get_research_levels(db, selected_tile["controller_village_id"]) if selected_tile and selected_tile["controller_village_id"] else {}
        controller_faction_slug = selected_tile["controller_faction_slug"] if selected_tile and selected_tile["controller_faction_slug"] else user["faction_slug"]
    owns_selected_tile = bool(selected_tile and selected_tile["village_id"] == village["id"])
    controls_selected_bloom = bool(selected_tile and selected_tile["controller_village_id"] == village["id"])
    has_tile_intel = bool(selected_intel or owns_selected_tile or controls_selected_bloom)
    intel_source = selected_tile if owns_selected_tile or controls_selected_bloom else selected_intel
    is_large_bloom = bool(selected_tile and selected_tile["tile_type"] == "spice_bloom_large")
    if is_large_bloom and has_tile_intel and intel_source and intel_source["controller_village_id"]:
        defender_stack = player_units_from_json(intel_source["garrison_json"])
        defender_units = player_unit_report_rows(defender_stack, controller_faction_slug, controller_research)
        defender_summary = player_unit_summary(defender_stack, controller_research)
    else:
        defender_stack = npc_units_for_source(intel_source) if has_tile_intel and intel_source else {}
        defender_units = npc_unit_report_rows(defender_stack)
        defender_summary = npc_unit_summary(defender_stack)
    has_combat_force = any(card["amount"] > 0 and card["damage"] > 0 for card in unit_cards)
    can_send = selected_tile and not selected_tile["village_id"] and not is_large_bloom and has_combat_force
    can_scout = selected_tile and not selected_tile["village_id"] and any(card["amount"] > 0 for card in scout_cards)
    can_capture = is_large_bloom and not controls_selected_bloom and has_combat_force
    can_reinforce = is_large_bloom and controls_selected_bloom and any(card["amount"] > 0 for card in unit_cards)
    harvester_count = units.get("spice_harvester", 0)
    carryall_count = units.get("carryall", 0)
    worm_attention = float(intel_source["worm_attention"]) if is_large_bloom and has_tile_intel and intel_source else 0
    worm_cooldown_until = intel_source["worm_cooldown_until"] if is_large_bloom and has_tile_intel and intel_source else None
    worm_cooldown_active = bool(worm_cooldown_until and parse_time(worm_cooldown_until) > utc_now())
    can_harvest = is_large_bloom and controls_selected_bloom and harvester_count > 0 and carryall_count > 0 and not worm_cooldown_active
    distance = map_distance(village["map_x"], village["map_y"], selected_tile["x"], selected_tile["y"]) if selected_tile else 0
    fastest_raid = max((card["speed"] for card in unit_cards), default=None)
    fastest_scout = max((card["speed"] for card in scout_cards), default=None)
    raid_travel_seconds = max(int(distance / fastest_raid * 60), 8) if fastest_raid and distance else 0
    scout_travel_seconds = max(int(distance / fastest_scout * 60), 8) if fastest_scout and distance else 0
    total_carry = sum(card["amount"] * card["carry"] for card in unit_cards)
    visible_loot = int(round(sum(intel_source[f"resource_{resource}"] for resource in ("iron", "wood", "water", "spice")))) if has_tile_intel and intel_source else 0
    expected_yield = tile_expected_yield(selected_tile["tile_type"], distance) if selected_tile else None
    if controls_selected_bloom:
        recommendation = {"tone": "good", "label": "Controlled deposit", "text": "Reinforce the garrison or dispatch a Carryall and Harvester operation."}
    elif is_large_bloom and selected_tile["controller_village_id"]:
        recommendation = target_recommendation(selected_tile, has_tile_intel, defender_summary, unit_cards, visible_loot)
        recommendation["label"] = "Capture target" if recommendation["tone"] != "danger" else recommendation["label"]
    else:
        recommendation = target_recommendation(selected_tile, has_tile_intel, defender_summary, unit_cards, visible_loot) if selected_tile else None
    return render_template(
        "map.html",
        user=user,
        village=village,
        tile_rows=tile_rows,
        selected_tile=selected_tile,
        movements=movements,
        movement_targets=movement_targets,
        scouted_tile_ids=scouted_tile_ids,
        bookmarked_tile_ids=bookmarked_tile_ids,
        recent_intel=recent_intel[:6],
        unit_cards=unit_cards,
        scout_cards=scout_cards,
        harvest_cards=harvest_cards,
        can_send=can_send,
        can_scout=can_scout,
        can_capture=can_capture,
        can_reinforce=can_reinforce,
        can_harvest=can_harvest,
        is_large_bloom=is_large_bloom,
        controls_selected_bloom=controls_selected_bloom,
        harvester_count=harvester_count,
        carryall_count=carryall_count,
        harvest_policies=HARVEST_POLICIES,
        worm_attention=worm_attention,
        worm_cooldown_until=worm_cooldown_until,
        worm_cooldown_active=worm_cooldown_active,
        distance=distance,
        difficulty=tile_difficulty_label(intel_source) if has_tile_intel else "Unscouted",
        owns_selected_tile=owns_selected_tile,
        has_tile_intel=has_tile_intel,
        tile_intel=selected_intel,
        intel_source=intel_source,
        defender_units=defender_units,
        defender_summary=defender_summary,
        tile_potential=tile_potential_text(selected_tile["tile_type"]) if selected_tile else "Unknown potential",
        raid_travel_seconds=raid_travel_seconds,
        scout_travel_seconds=scout_travel_seconds,
        total_carry=total_carry,
        visible_loot=visible_loot,
        expected_yield=expected_yield,
        recommendation=recommendation,
        map_radius=MAP_RADIUS,
        tile_display_name=tile_display_name,
        tile_short_label=tile_short_label,
        queue_status=queue_status,
    )


@app.route("/map/<int:tile_id>/plan")
@login_required
def plan_map_mission(tile_id):
    """Read-only estimates from the player's own force and saved intelligence."""
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (tile_id,)).fetchone()
        if not tile or village["map_x"] is None:
            abort(404)
        mission = request.args.get("mission", "raid")
        if mission not in ("raid", "capture", "reinforce", "scout", "harvest"):
            abort(400)
        available = get_village_units(db, village["id"])
        selected = {}
        for key, config in UNIT_TYPES.items():
            try:
                amount = int(request.args.get(f"unit_{key}", "0"))
            except ValueError:
                return jsonify(error="Enter whole numbers for troops."), 400
            eligible = (key in ("carryall", "spice_harvester") if mission == "harvest" else
                        config["category"] == "influence" or config.get("can_scout") if mission == "scout" else
                        config["category"] == "military" or config.get("can_raid") or config.get("is_transport"))
            if amount < 0 or amount > available.get(key, 0) or (amount and not eligible):
                return jsonify(error="Selected troops are not available for this mission."), 400
            if amount:
                selected[key] = amount
        if not selected:
            return jsonify(error="Select troops to preview the mission."), 400
        if mission == "harvest" and (selected.get("spice_harvester", 0) <= 0 or selected.get("carryall", 0) < selected.get("spice_harvester", 0)):
            return jsonify(error="Each harvester needs a Carryall."), 400
        research = get_research_levels(db, village["id"])
        attack, durability, carry = sent_unit_stats(selected, research)
        combat_profile = player_combat_profile(selected, research)
        travel = movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], selected, user["faction_slug"])
        owned = tile["controller_village_id"] == village["id"]
        intel = tile if owned else get_tile_intel(db, user["id"], tile_id)
        now = utc_now()
        age = max(int((now - parse_time(intel["scouted_at"])).total_seconds()), 0) if intel and not owned else 0 if owned else None
        stale = age is not None and age >= 3600
        defense = None
        if intel:
            if intel["controller_village_id"]:
                defender_research = research if owned else None
                defense = player_unit_summary(player_units_from_json(intel["garrison_json"]), defender_research)["defense"]
            else:
                defense = npc_units_defense(npc_units_for_source(intel))
        if mission not in ("raid", "capture"):
            risk = "No combat estimate for this mission"
        elif defense is None:
            risk = "Unknown — scout first"
        elif stale:
            risk = "Uncertain — scouting is over an hour old"
        elif intel["controller_village_id"] and not owned:
            risk = "Uncertain — enemy research and faction bonuses are not included"
        elif attack < defense:
            risk = "High — selected attack is below known defense"
        elif attack < defense * 1.3:
            risk = "Moderate — little margin above known defense"
        else:
            risk = "Lower — margin above known defense; losses remain possible"
        harvest_duration = HARVEST_POLICIES.get(request.args.get("harvest_policy", "balanced"), HARVEST_POLICIES["balanced"])["duration_seconds"] if mission == "harvest" else 0
        return jsonify(attack=round(attack, 1), durability=round(durability, 1), carry=carry,
                       shield=round(combat_profile["shield"], 1),
                       shield_piercing=round(combat_profile["piercing_attack"] / max(combat_profile["attack"], 1) * 100),
                       travel_seconds=travel, arrival_at=(now + timedelta(seconds=travel)).isoformat(),
                       return_at=None if mission in ("capture", "reinforce") else (now + timedelta(seconds=travel * 2 + harvest_duration)).isoformat(),
                       known_defense=defense, intel_age_seconds=age, stale=stale, risk=risk,
                       note="Estimate assumes immediate dispatch. Surviving capture/reinforcement troops stay at the bloom." if mission in ("capture", "reinforce") else "Return estimate assumes survivors, immediate arrival processing, and unchanged travel speed.")


@app.route("/map/<int:tile_id>/bookmark", methods=("POST",))
@login_required
def bookmark_tile(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        user = get_current_user(db)
        tile = db.execute("SELECT id FROM map_tiles WHERE id = ?", (tile_id,)).fetchone()
        if not tile:
            flash("Map tile not found.")
            return redirect(url_for("map_page"))
        db.execute(
            "INSERT OR IGNORE INTO map_bookmarks (user_id, tile_id, created_at) VALUES (?, ?, ?)",
            (user["id"], tile_id, utc_now().isoformat()),
        )
        db.commit()
    flash("Target bookmarked.")
    return redirect(next_url)


@app.route("/map/<int:tile_id>/bookmark/remove", methods=("POST",))
@login_required
def remove_tile_bookmark(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        user = get_current_user(db)
        db.execute("DELETE FROM map_bookmarks WHERE user_id = ? AND tile_id = ?", (user["id"], tile_id))
        db.commit()
    flash("Bookmark removed.")
    return redirect(next_url)


@app.route("/map/<int:tile_id>/send", methods=("POST",))
@login_required
def send_map_raid(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_unit_training_queue(db, village["id"])
        ensure_village_map_position(db, village)
        regenerate_map_tiles(db)
        process_troop_movements(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (tile_id,)).fetchone()
        if not tile:
            flash("Map tile not found.")
            return redirect(url_for("map_page"))
        if tile["village_id"]:
            flash("You cannot raid a village tile yet.")
            return redirect(next_url)
        if tile["tile_type"] == "spice_bloom_large":
            flash("Large Spice Blooms must be captured first, then harvested with a Harvester and Carryall.")
            return redirect(next_url)
        available_units = get_village_units(db, village["id"])
        sent_units = {}
        for key, config in UNIT_TYPES.items():
            if config["category"] != "military" and not config.get("can_raid") and not config.get("is_transport"):
                continue
            try:
                amount = int(request.form.get(f"unit_{key}", "0"))
            except ValueError:
                amount = 0
            amount = max(0, min(amount, available_units.get(key, 0)))
            if amount > 0:
                sent_units[key] = amount
        if not sent_units or not any(UNIT_TYPES[key]["damage"] > 0 for key in sent_units):
            flash("Select at least one combat unit to send.")
            return redirect(next_url)
        for key, amount in sent_units.items():
            remove_village_units(db, village["id"], key, amount)
        duration = movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], sent_units, village_faction(db, village))
        now = utc_now()
        db.execute(
            """
            INSERT INTO troop_movements
                (village_id, target_tile_id, target_x, target_y, mission_type, units_json, status, started_at, arrive_at)
            VALUES (?, ?, ?, ?, 'raid', ?, 'outbound', ?, ?)
            """,
            (village["id"], tile["id"], tile["x"], tile["y"], json.dumps(sent_units), now.isoformat(), (now + timedelta(seconds=duration)).isoformat()),
        )
        db.commit()
        flash("Raid party dispatched.")
    return redirect(next_url)


@app.route("/map/<int:tile_id>/scout", methods=("POST",))
@login_required
def send_map_scout(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_unit_training_queue(db, village["id"])
        ensure_village_map_position(db, village)
        regenerate_map_tiles(db)
        process_troop_movements(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (tile_id,)).fetchone()
        if not tile:
            flash("Map tile not found.")
            return redirect(url_for("map_page"))
        if tile["village_id"]:
            flash("You cannot scout a village tile yet.")
            return redirect(next_url)
        available_units = get_village_units(db, village["id"])
        sent_units = {}
        for key, config in UNIT_TYPES.items():
            if config["category"] != "influence" and not config.get("can_scout"):
                continue
            try:
                amount = int(request.form.get(f"unit_{key}", "0"))
            except ValueError:
                amount = 0
            amount = max(0, min(amount, available_units.get(key, 0)))
            if amount > 0:
                sent_units[key] = amount
        if not sent_units:
            flash("Select at least one scout or Scout Ornithopter.")
            return redirect(next_url)
        for key, amount in sent_units.items():
            remove_village_units(db, village["id"], key, amount)
        duration = movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], sent_units, village_faction(db, village))
        now = utc_now()
        db.execute(
            """
            INSERT INTO troop_movements
                (village_id, target_tile_id, target_x, target_y, mission_type, units_json, status, started_at, arrive_at)
            VALUES (?, ?, ?, ?, 'scout', ?, 'outbound', ?, ?)
            """,
            (village["id"], tile["id"], tile["x"], tile["y"], json.dumps(sent_units), now.isoformat(), (now + timedelta(seconds=duration)).isoformat()),
        )
        db.commit()
        flash("Scouts dispatched.")
    return redirect(next_url)


def collect_operation_units(available_units, predicate):
    selected = {}
    for key, config in UNIT_TYPES.items():
        if not predicate(key, config):
            continue
        try:
            amount = int(request.form.get(f"unit_{key}", "0"))
        except ValueError:
            amount = 0
        amount = max(0, min(amount, available_units.get(key, 0)))
        if amount:
            selected[key] = amount
    return selected


def dispatch_operation(db, village, tile, mission_type, sent_units, metadata=None):
    for key, amount in sent_units.items():
        remove_village_units(db, village["id"], key, amount)
    duration = movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], sent_units, village_faction(db, village))
    now = utc_now()
    db.execute(
        """
        INSERT INTO troop_movements
            (village_id, target_tile_id, target_x, target_y, mission_type, units_json, mission_metadata_json, status, started_at, arrive_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'outbound', ?, ?)
        """,
        (village["id"], tile["id"], tile["x"], tile["y"], mission_type, player_units_json(sent_units), json.dumps(metadata or {}), now.isoformat(), (now + timedelta(seconds=duration)).isoformat()),
    )


def prepare_map_operation(db, tile_id):
    user = get_current_user(db)
    village = get_village(db, user["id"])
    village, _buildings = update_resources(db, village, user["faction_slug"])
    process_unit_training_queue(db, village["id"])
    ensure_village_map_position(db, village)
    regenerate_map_tiles(db)
    process_troop_movements(db, village["id"])
    db.commit()
    return get_village(db, user["id"]), db.execute("SELECT * FROM map_tiles WHERE id = ?", (tile_id,)).fetchone()


@app.route("/map/<int:tile_id>/capture", methods=("POST",))
@login_required
def capture_spice_bloom(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        village, tile = prepare_map_operation(db, tile_id)
        if not tile or tile["tile_type"] != "spice_bloom_large":
            flash("Only Large Spice Blooms can be captured.")
            return redirect(next_url)
        if tile["controller_village_id"] == village["id"]:
            flash("Your village already controls this bloom.")
            return redirect(next_url)
        available = get_village_units(db, village["id"])
        sent = collect_operation_units(available, lambda _key, config: config["category"] == "military" or config.get("can_raid") or config.get("is_transport"))
        if not sent or not any(UNIT_TYPES[key]["damage"] > 0 for key in sent):
            flash("Select at least one combat unit for the capture force.")
            return redirect(next_url)
        dispatch_operation(db, village, tile, "capture", sent)
        db.commit()
    flash("Capture force dispatched. Survivors will remain as the bloom garrison.")
    return redirect(next_url)


@app.route("/map/<int:tile_id>/reinforce", methods=("POST",))
@login_required
def reinforce_spice_bloom(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        village, tile = prepare_map_operation(db, tile_id)
        if not tile or tile["tile_type"] != "spice_bloom_large" or tile["controller_village_id"] != village["id"]:
            flash("You no longer control this bloom.")
            return redirect(next_url)
        available = get_village_units(db, village["id"])
        sent = collect_operation_units(available, lambda _key, config: config["category"] == "military" or config.get("can_raid") or config.get("is_transport"))
        if not sent:
            flash("Select units to reinforce the garrison.")
            return redirect(next_url)
        dispatch_operation(db, village, tile, "reinforce", sent)
        db.commit()
    flash("Reinforcements dispatched.")
    return redirect(next_url)


@app.route("/map/<int:tile_id>/recall", methods=("POST",))
@login_required
def recall_spice_bloom(tile_id):
    with get_db() as db:
        village, _tile = prepare_map_operation(db, tile_id)
        # Lock before reading ownership and quantities so concurrent recalls cannot duplicate units.
        db.execute("BEGIN IMMEDIATE")
        tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (tile_id,)).fetchone()
        if not tile or tile["tile_type"] != "spice_bloom_large" or tile["controller_village_id"] != village["id"]:
            flash("You no longer control this bloom.")
            return redirect(url_for("command_center_page"))
        garrison = player_units_from_json(tile["garrison_json"])
        if request.form.get("recall_all") == "1":
            selected = dict(garrison)
        else:
            selected = {}
            for key, available in garrison.items():
                try:
                    amount = int(request.form.get(f"unit_{key}", "0"))
                except ValueError:
                    flash("Enter whole numbers for recalled troops.")
                    return redirect(url_for("command_center_page"))
                if amount < 0 or amount > available:
                    flash("Recall quantities must be between zero and the stationed amount.")
                    return redirect(url_for("command_center_page"))
                if amount:
                    selected[key] = amount
        if not selected:
            flash("Select at least one stationed unit to recall.")
            return redirect(url_for("command_center_page"))
        remaining = {key: amount - selected.get(key, 0) for key, amount in garrison.items()}
        remaining = normalize_player_units(remaining)
        if not any(UNIT_TYPES[key]["damage"] > 0 for key in remaining) and request.form.get("confirm_undefended") != "1":
            flash("Confirm that this recall leaves the bloom undefended.")
            return redirect(url_for("command_center_page"))
        duration = movement_duration_seconds(tile["x"], tile["y"], village["map_x"], village["map_y"], selected, village_faction(db, village))
        now = utc_now()
        return_at = (now + timedelta(seconds=duration)).isoformat()
        db.execute("UPDATE map_tiles SET garrison_json = ? WHERE id = ?", (player_units_json(remaining), tile_id))
        db.execute(
            """INSERT INTO troop_movements
                (village_id, target_tile_id, target_x, target_y, mission_type, units_json,
                 survivors_json, status, started_at, arrive_at, return_at, report)
                VALUES (?, ?, ?, ?, 'recall', ?, ?, 'returning', ?, ?, ?, ?)""",
            (village["id"], tile_id, tile["x"], tile["y"], player_units_json(selected),
             player_units_json(selected), now.isoformat(), now.isoformat(), return_at,
             "Garrison recalled. Troops are returning home."),
        )
        db.commit()
    flash("Recall dispatched. Troops become available when they arrive home.")
    return redirect(url_for("command_center_page"))


@app.route("/map/<int:tile_id>/harvest", methods=("POST",))
@login_required
def harvest_spice_bloom(tile_id):
    next_url = request.form.get("next") or url_for("map_page", tile_id=tile_id)
    if not safe_local_redirect(next_url):
        next_url = url_for("map_page", tile_id=tile_id)
    with get_db() as db:
        village, tile = prepare_map_operation(db, tile_id)
        if not tile or tile["tile_type"] != "spice_bloom_large" or tile["controller_village_id"] != village["id"]:
            flash("Capture this Large Spice Bloom before harvesting it.")
            return redirect(next_url)
        if tile["worm_cooldown_until"] and parse_time(tile["worm_cooldown_until"]) > utc_now():
            flash("This bloom is inside active Worm Territory. Wait for the sand to settle.")
            return redirect(next_url)
        available = get_village_units(db, village["id"])
        sent = collect_operation_units(available, lambda key, _config: key in ("spice_harvester", "carryall"))
        harvesters = sent.get("spice_harvester", 0)
        carryalls = sent.get("carryall", 0)
        if harvesters <= 0 or carryalls < harvesters:
            flash("Each Spice Harvester requires one Carryall for transport.")
            return redirect(next_url)
        policy_key = request.form.get("harvest_policy", "balanced")
        if policy_key not in HARVEST_POLICIES:
            policy_key = "balanced"
        dispatch_operation(db, village, tile, "harvest", sent, {"policy": policy_key})
        db.commit()
    flash(f"{HARVEST_POLICIES[policy_key]['name']} dispatched with automatic Carryall extraction.")
    return redirect(next_url)


@app.route("/inbox")
@login_required
def inbox():
    report_filter = request.args.get("filter", "all")
    if report_filter not in {"all", "scout", "raid", "unread"}:
        report_filter = "all"
    with get_db() as db:
        user = get_current_user(db)
        clauses = ["battle_reports.user_id = ?"]
        params = [user["id"]]
        if report_filter in {"scout", "raid"}:
            clauses.append("battle_reports.report_type = ?")
            params.append(report_filter)
        elif report_filter == "unread":
            clauses.append("battle_reports.is_read = 0")
        reports = db.execute(
            f"""
            SELECT battle_reports.*, villages.name AS village_name
            FROM battle_reports
            JOIN villages ON villages.id = battle_reports.village_id
            WHERE {' AND '.join(clauses)}
            ORDER BY battle_reports.created_at DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
        filter_counts = {
            "all": db.execute("SELECT COUNT(*) AS count FROM battle_reports WHERE user_id = ?", (user["id"],)).fetchone()["count"],
            "scout": db.execute("SELECT COUNT(*) AS count FROM battle_reports WHERE user_id = ? AND report_type = 'scout'", (user["id"],)).fetchone()["count"],
            "raid": db.execute("SELECT COUNT(*) AS count FROM battle_reports WHERE user_id = ? AND report_type = 'raid'", (user["id"],)).fetchone()["count"],
            "unread": db.execute("SELECT COUNT(*) AS count FROM battle_reports WHERE user_id = ? AND is_read = 0", (user["id"],)).fetchone()["count"],
        }
    report_summaries = {report["id"]: report_summary(report) for report in reports}
    return render_template("inbox.html", reports=reports, report_summaries=report_summaries, report_filter=report_filter, filter_counts=filter_counts)


@app.route("/inbox/mark-read", methods=("POST",))
@login_required
def mark_reports_read():
    next_url = request.form.get("next") or url_for("inbox")
    if not safe_local_redirect(next_url):
        next_url = url_for("inbox")
    with get_db() as db:
        user = get_current_user(db)
        db.execute("UPDATE battle_reports SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user["id"],))
        db.commit()
    flash("All reports marked read.")
    return redirect(next_url)


@app.route("/inbox/<int:report_id>")
@login_required
def battle_report(report_id):
    with get_db() as db:
        user = get_current_user(db)
        report = db.execute(
            """
            SELECT battle_reports.*, villages.name AS village_name, factions.slug AS faction_slug
            FROM battle_reports
            JOIN villages ON villages.id = battle_reports.village_id
            JOIN users ON users.id = battle_reports.user_id
            JOIN factions ON factions.id = users.faction_id
            WHERE battle_reports.id = ? AND battle_reports.user_id = ?
            """,
            (report_id, user["id"]),
        ).fetchone()
        if not report:
            flash("Report not found.")
            return redirect(url_for("inbox"))
        db.execute("UPDATE battle_reports SET is_read = 1 WHERE id = ?", (report_id,))
        db.commit()
        report = db.execute(
            """
            SELECT battle_reports.*, villages.name AS village_name, factions.slug AS faction_slug
            FROM battle_reports
            JOIN villages ON villages.id = battle_reports.village_id
            JOIN users ON users.id = battle_reports.user_id
            JOIN factions ON factions.id = users.faction_id
            WHERE battle_reports.id = ? AND battle_reports.user_id = ?
            """,
            (report_id, user["id"]),
        ).fetchone()
        sent_units = json.loads(report["units_sent_json"])
        survived_units = json.loads(report["units_survived_json"])
        lost_units = json.loads(report["units_lost_json"])
        attacker_research = get_research_levels(db, report["village_id"])
        enemy_player_before = player_units_from_json(report["enemy_player_before_json"])
        enemy_player_after = player_units_from_json(report["enemy_player_after_json"])
        enemy_player_lost = player_units_from_json(report["enemy_player_lost_json"])
        enemy_is_player = bool(enemy_player_before)
        enemy_research = ResearchLevels({}, report["enemy_faction_slug"]) if enemy_is_player else None
        combat = json.loads(report["combat_json"] or "{}")
        if enemy_is_player:
            enemy_before_rows = player_unit_report_rows(enemy_player_before, report["enemy_faction_slug"], enemy_research)
            enemy_after_rows = player_unit_report_rows(enemy_player_after, report["enemy_faction_slug"], enemy_research)
            enemy_lost_rows = player_unit_report_rows(enemy_player_lost, report["enemy_faction_slug"], enemy_research)
        else:
            enemy_before_rows = npc_unit_report_rows(npc_units_from_json(report["enemy_before_json"]) or generate_npc_units(report["enemy_before"], random.Random(report["id"] * 17)))
            enemy_after_rows = npc_unit_report_rows(npc_units_from_json(report["enemy_after_json"]) or generate_npc_units(report["enemy_after"], random.Random(report["id"] * 19)))
            enemy_lost_rows = npc_unit_report_rows(npc_units_from_json(report["enemy_lost_json"]) or generate_npc_units(report["enemy_lost"], random.Random(report["id"] * 23)))
    return render_template(
        "battle_report.html",
        report=report,
        sent_units=player_unit_report_rows(sent_units, report["faction_slug"], attacker_research),
        survived_units=player_unit_report_rows(survived_units, report["faction_slug"], attacker_research),
        lost_units=player_unit_report_rows(lost_units, report["faction_slug"], attacker_research),
        enemy_before_units=enemy_before_rows,
        enemy_after_units=enemy_after_rows,
        enemy_lost_units=enemy_lost_rows,
        enemy_is_player=enemy_is_player,
        combat=combat,
    )


def prepare_village_context():
    db = get_db()
    user = get_current_user(db)
    village = get_village(db, user["id"])
    village, buildings = update_resources(db, village, user["faction_slug"])
    process_construction_queue(db, village["id"])
    process_unit_training_queue(db, village["id"])
    process_research_queue(db, village["id"])
    ensure_village_map_position(db, village)
    process_troop_movements(db, village["id"])
    db.commit()
    village = get_village(db, user["id"])
    buildings = get_buildings(db, village["id"])
    research_levels = get_research_levels(db, village["id"])
    units = get_village_units(db, village["id"])
    return db, user, village, buildings, research_levels, units


@app.route("/buildings/command_center")
@login_required
def command_center_page():
    db, user, village, buildings, research_levels, units = prepare_village_context()
    with db:
        movements = db.execute(
            """
            SELECT troop_movements.*, map_tiles.tile_type
            FROM troop_movements
            JOIN map_tiles ON map_tiles.id = troop_movements.target_tile_id
            WHERE troop_movements.village_id = ? AND troop_movements.status IN ('outbound', 'harvesting', 'returning')
            ORDER BY troop_movements.arrive_at ASC
            """,
            (village["id"],),
        ).fetchall()
        recent_reports = db.execute(
            """
            SELECT * FROM battle_reports
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 6
            """,
            (user["id"],),
        ).fetchall()
        recent_intel = db.execute(
            """
            SELECT tile_intel.*, map_tiles.x, map_tiles.y, map_tiles.tile_type
            FROM tile_intel
            JOIN map_tiles ON map_tiles.id = tile_intel.tile_id
            WHERE tile_intel.user_id = ?
            ORDER BY tile_intel.scouted_at DESC
            LIMIT 20
            """,
            (user["id"],),
        ).fetchall()
        bookmarks = db.execute(
            """
            SELECT map_bookmarks.*, map_tiles.x, map_tiles.y, map_tiles.tile_type,
                   tile_intel.scouted_at, tile_intel.npc_units_json, tile_intel.npc_strength,
                   tile_intel.resource_iron, tile_intel.resource_wood, tile_intel.resource_water, tile_intel.resource_spice
            FROM map_bookmarks
            JOIN map_tiles ON map_tiles.id = map_bookmarks.tile_id
            LEFT JOIN tile_intel ON tile_intel.tile_id = map_bookmarks.tile_id AND tile_intel.user_id = map_bookmarks.user_id
            WHERE map_bookmarks.user_id = ?
            ORDER BY map_bookmarks.created_at DESC
            LIMIT 8
            """,
            (user["id"],),
        ).fetchall()
    with db:
        controlled_blooms = db.execute(
            "SELECT * FROM map_tiles WHERE tile_type = 'spice_bloom_large' AND controller_village_id = ? ORDER BY x, y",
            (village["id"],),
        ).fetchall()
    troop_totals = {key: {"home": units.get(key, 0), "travelling": 0, "stationed": 0} for key in UNIT_TYPES}
    movement_units = {}
    for movement in movements:
        stack = player_units_from_json(movement["survivors_json"] if movement["status"] == "returning" else movement["units_json"])
        movement_units[movement["id"]] = unit_report_rows(stack, user["faction_slug"])
        for key, amount in stack.items():
            troop_totals[key]["travelling"] += amount
    garrisons = []
    for tile in controlled_blooms:
        stack = player_units_from_json(tile["garrison_json"])
        for key, amount in stack.items():
            troop_totals[key]["stationed"] += amount
        garrisons.append({"tile": tile, "units": unit_report_rows(stack, user["faction_slug"]),
                          "defended": any(UNIT_TYPES[key]["damage"] > 0 for key in stack)})
    troop_overview = [{"name": unit_display_name(key, user["faction_slug"]), **counts,
                       "total": sum(counts.values())} for key, counts in troop_totals.items() if sum(counts.values())]
    report_summaries = {report["id"]: report_summary(report) for report in recent_reports}
    intel_summaries = {intel["tile_id"]: tile_intel_summary(intel) for intel in recent_intel}
    bookmark_summaries = {bookmark["tile_id"]: tile_intel_summary(bookmark) if bookmark["scouted_at"] else None for bookmark in bookmarks}
    military_cards = []
    for key, amount in units.items():
        if amount <= 0 or UNIT_TYPES.get(key, {}).get("category") != "military":
            continue
        military_cards.append({
            "amount": amount,
            "damage": effective_unit_stat(key, "damage", research_levels),
            "durability": effective_unit_stat(key, "health", research_levels)
            + effective_unit_stat(key, "armor", research_levels) * 5
            + effective_unit_stat(key, "shield", research_levels) * 3,
            "carry": UNIT_TYPES[key]["carry"],
        })
    target_board = recommended_targets(recent_intel, village, military_cards)
    db.close()
    return render_template(
        "command_center.html",
        troop_overview=troop_overview,
        garrisons=garrisons,
        movement_units=movement_units,
        user=user,
        village=village,
        buildings=buildings,
        movements=movements,
        recent_reports=recent_reports,
        report_summaries=report_summaries,
        recent_intel=recent_intel,
        intel_summaries=intel_summaries,
        bookmarks=bookmarks,
        bookmark_summaries=bookmark_summaries,
        target_board=target_board,
        tile_display_name=tile_display_name,
        queue_status=queue_status,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="command_center",
    )


@app.route("/buildings/barracks")
@login_required
def barracks_page():
    db, user, village, buildings, research_levels, units = prepare_village_context()
    with db:
        full_unit_queue = get_unit_training_queue(db, village["id"])
        unit_queue = filter_unit_queue_by_category(full_unit_queue, "military")
        unit_cards = build_unit_cards(village, buildings, research_levels, units, full_unit_queue, user["faction_slug"], "military")
    db.close()
    return render_template(
        "unit_building.html",
        title="Barracks",
        eyebrow="Military Training",
        description="Train military units and review their stats.",
        user=user,
        village=village,
        unit_cards=unit_cards,
        unit_queue=unit_queue,
        unit_queue_limit=UNIT_QUEUE_LIMIT,
        unit_display_name=unit_display_name,
        queue_status=queue_status,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="barracks",
    )


@app.route("/buildings/flight_works")
@login_required
def flight_works_page():
    db, user, village, buildings, research_levels, units = prepare_village_context()
    with db:
        full_unit_queue = get_unit_training_queue(db, village["id"])
        unit_queue = filter_unit_queue_by_category(full_unit_queue, "vehicle")
        unit_cards = build_unit_cards(village, buildings, research_levels, units, full_unit_queue, user["faction_slug"], "vehicle")
    db.close()
    return render_template(
        "unit_building.html",
        title="Flight Works",
        eyebrow="Desert Aviation",
        description="Build ornithopters, Carryalls, and Spice Harvesters for long-range desert operations.",
        user=user,
        village=village,
        unit_cards=unit_cards,
        unit_queue=unit_queue,
        unit_queue_limit=UNIT_QUEUE_LIMIT,
        unit_display_name=unit_display_name,
        queue_status=queue_status,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="flight_works",
    )


@app.route("/buildings/influence_sanctuary")
@login_required
def influence_sanctuary_page():
    db, user, village, buildings, research_levels, units = prepare_village_context()
    with db:
        full_unit_queue = get_unit_training_queue(db, village["id"])
        unit_queue = filter_unit_queue_by_category(full_unit_queue, "influence")
        unit_cards = build_unit_cards(village, buildings, research_levels, units, full_unit_queue, user["faction_slug"], "influence")
        title = building_display_name("influence_sanctuary", user["faction_slug"])
    db.close()
    return render_template(
        "unit_building.html",
        title=title,
        eyebrow="Influence Operations",
        description="Train agents for scouting and future loyalty missions.",
        user=user,
        village=village,
        unit_cards=unit_cards,
        unit_queue=unit_queue,
        unit_queue_limit=UNIT_QUEUE_LIMIT,
        unit_display_name=unit_display_name,
        queue_status=queue_status,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="influence_sanctuary",
    )


@app.route("/buildings/deathstill")
@login_required
def deathstill_page():
    db, user, village, buildings, research_levels, units = prepare_village_context()
    level = buildings.get("deathstill", 0)
    if level <= 0:
        db.close()
        flash("Construct the Deathstill before using it.")
        return redirect(url_for("dashboard"))

    recovery_rate = deathstill_recovery_rate(level)
    capacity = storage_capacity(buildings)
    reclaimable_units = []
    for key, config in UNIT_TYPES.items():
        if config["category"] == "vehicle":
            continue
        yield_per_unit = deathstill_water_yield(key, level)
        owned = units.get(key, 0)
        reclaimable_units.append({
            "key": key,
            "name": unit_display_name(key, user["faction_slug"]),
            "category": config["category"],
            "owned": owned,
            "yield_per_unit": yield_per_unit,
            "total_yield": yield_per_unit * owned,
        })
    db.close()
    return render_template(
        "deathstill.html",
        user=user,
        village=village,
        buildings=buildings,
        level=level,
        recovery_rate=recovery_rate,
        capacity=capacity,
        free_capacity=max(capacity - village["water"], 0),
        reclaimable_units=reclaimable_units,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="deathstill",
    )


@app.route("/buildings/deathstill/render/<unit_key>", methods=("POST",))
@login_required
def render_units_in_deathstill(unit_key):
    if unit_key not in UNIT_TYPES or UNIT_TYPES[unit_key]["category"] == "vehicle":
        flash("That unit cannot be processed in the Deathstill.")
        return redirect(url_for("deathstill_page"))
    try:
        amount = int(request.form.get("amount", "1"))
    except ValueError:
        amount = 1
    amount = max(1, min(amount, 999))

    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_unit_training_queue(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        buildings = get_buildings(db, village["id"])
        level = buildings.get("deathstill", 0)
        if level <= 0:
            flash("Construct the Deathstill before using it.")
            return redirect(url_for("dashboard"))

        available = get_village_units(db, village["id"]).get(unit_key, 0)
        if amount > available:
            flash("Not enough units are present in the village.")
            return redirect(url_for("deathstill_page"))

        capacity = storage_capacity(buildings)
        free_capacity = max(capacity - village["water"], 0)
        if free_capacity <= 0:
            flash("Water storage is full. Spend water before using the Deathstill.")
            return redirect(url_for("deathstill_page"))

        potential_water = deathstill_water_yield(unit_key, level) * amount
        recovered_water = min(potential_water, free_capacity)
        remove_village_units(db, village["id"], unit_key, amount)
        db.execute("UPDATE villages SET water = water + ? WHERE id = ?", (recovered_water, village["id"]))
        db.commit()
        wasted = potential_water - recovered_water
        message = f"Rendered {amount} {unit_display_name(unit_key, user['faction_slug'])} and recovered {recovered_water:.0f} Water."
        if wasted > 0:
            message += f" {wasted:.0f} Water was lost because storage filled up."
        flash(message)
    return redirect(url_for("deathstill_page"))


@app.route("/buildings/research_center")
@login_required
def research_center_page():
    db, user, village, buildings, research_levels, units = prepare_village_context()
    with db:
        research_queue = get_research_queue(db, village["id"])
        research_cards = build_research_cards(village, buildings, research_levels, research_queue, user["faction_slug"])
    db.close()
    return render_template(
        "research_center.html",
        user=user,
        village=village,
        research_cards=research_cards,
        research_queue=research_queue,
        research_queue_limit=RESEARCH_QUEUE_LIMIT,
        research_defs=RESEARCH_TYPES,
        queue_status=queue_status,
        base_nav=base_nav_items(buildings, user["faction_slug"]),
        active_base_tab="research_center",
    )


@app.route("/leaderboard")
def leaderboard():
    with get_db() as db:
        player_rows = db.execute(
            """
            SELECT users.username, factions.name AS faction_name, alliances.tag AS alliance_tag,
                   COALESCE(SUM(villages.points), 0) AS total_points
            FROM users
            JOIN factions ON factions.id = users.faction_id
            LEFT JOIN villages ON villages.user_id = users.id
            LEFT JOIN alliance_members ON alliance_members.user_id = users.id
            LEFT JOIN alliances ON alliances.id = alliance_members.alliance_id
            GROUP BY users.id
            ORDER BY total_points DESC, users.username ASC
            LIMIT 100
            """
        ).fetchall()
        alliance_rows = db.execute(
            """
            SELECT alliances.name, alliances.tag, COUNT(alliance_members.user_id) AS member_count,
                   COALESCE(SUM(villages.points), 0) AS total_points
            FROM alliances
            LEFT JOIN alliance_members ON alliance_members.alliance_id = alliances.id
            LEFT JOIN villages ON villages.user_id = alliance_members.user_id
            GROUP BY alliances.id
            ORDER BY total_points DESC, alliances.name ASC
            LIMIT 100
            """
        ).fetchall()
    return render_template("leaderboard.html", players=player_rows, alliances=alliance_rows)


@app.route("/alliances")
@login_required
def alliances():
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        db.commit()
        membership = get_membership(db, user["id"])
        has_embassy = can_use_alliance_features(buildings)
        active_application = db.execute(
            """
            SELECT alliance_applications.*, alliances.name AS alliance_name, alliances.tag AS alliance_tag
            FROM alliance_applications
            JOIN alliances ON alliances.id = alliance_applications.alliance_id
            WHERE alliance_applications.user_id = ? AND alliance_applications.status = 'pending'
            LIMIT 1
            """,
            (user["id"],),
        ).fetchone()
        alliance_rows = db.execute(
            """
            SELECT alliances.*, COUNT(alliance_members.user_id) AS member_count,
                   COALESCE(SUM(villages.points), 0) AS total_points
            FROM alliances
            LEFT JOIN alliance_members ON alliance_members.alliance_id = alliances.id
            LEFT JOIN villages ON villages.user_id = alliance_members.user_id
            GROUP BY alliances.id
            ORDER BY alliances.name ASC
            """
        ).fetchall()
        applications = []
        if membership and membership["role"] in ("leader", "officer"):
            applications = db.execute(
                """
                SELECT alliance_applications.*, users.username
                FROM alliance_applications
                JOIN users ON users.id = alliance_applications.user_id
                WHERE alliance_applications.alliance_id = ? AND alliance_applications.status = 'pending'
                ORDER BY alliance_applications.created_at ASC
                """,
                (membership["alliance_id"],),
            ).fetchall()
    return render_template(
        "alliances.html",
        user=user,
        village=village,
        buildings=buildings,
        has_embassy=has_embassy,
        membership=membership,
        alliances=alliance_rows,
        active_application=active_application,
        applications=applications,
        embassy_alliance_level=EMBASSY_ALLIANCE_LEVEL,
    )


@app.route("/alliances/create", methods=("POST",))
@login_required
def create_alliance():
    name = request.form["alliance_name"].strip()
    tag = request.form["alliance_tag"].strip().upper()
    description = request.form.get("alliance_description", "").strip()
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        if not can_use_alliance_features(buildings):
            flash("Embassy level 10 is required to create an alliance.")
            return redirect(url_for("alliances"))
        if get_membership(db, user["id"]):
            flash("You are already in an alliance.")
            return redirect(url_for("alliances"))
        if not name or not tag or len(tag) > 8:
            flash("Alliance name and a tag up to 8 characters are required.")
            return redirect(url_for("alliances"))
        try:
            cursor = db.execute(
                """
                INSERT INTO alliances (name, tag, description, leader_user_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, tag, description, user["id"], utc_now().isoformat()),
            )
            db.execute(
                """
                INSERT INTO alliance_members (alliance_id, user_id, role, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (cursor.lastrowid, user["id"], "leader", utc_now().isoformat()),
            )
            db.execute("UPDATE alliance_applications SET status = 'cancelled' WHERE user_id = ? AND status = 'pending'", (user["id"],))
            db.commit()
            flash("Alliance created.")
        except sqlite3.IntegrityError:
            flash("Alliance name or tag is already taken.")
    return redirect(url_for("alliances"))


@app.route("/alliances/<int:alliance_id>/apply", methods=("POST",))
@login_required
def apply_to_alliance(alliance_id):
    message = request.form.get("message", "").strip()
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        if not can_use_alliance_features(buildings):
            flash("Embassy level 10 is required to apply to alliances.")
            return redirect(url_for("alliances"))
        if get_membership(db, user["id"]):
            flash("You are already in an alliance.")
            return redirect(url_for("alliances"))
        alliance = db.execute("SELECT * FROM alliances WHERE id = ?", (alliance_id,)).fetchone()
        if not alliance:
            flash("Alliance not found.")
            return redirect(url_for("alliances"))
        active = db.execute(
            "SELECT id FROM alliance_applications WHERE user_id = ? AND status = 'pending'",
            (user["id"],),
        ).fetchone()
        if active:
            flash("You already have a pending alliance application.")
            return redirect(url_for("alliances"))
        db.execute(
            """
            INSERT INTO alliance_applications (alliance_id, user_id, message, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (alliance_id, user["id"], message, utc_now().isoformat()),
        )
        db.commit()
        flash("Application sent.")
    return redirect(url_for("alliances"))


@app.route("/applications/<int:application_id>/<action>", methods=("POST",))
@login_required
def decide_application(application_id, action):
    if action not in ("approve", "reject"):
        flash("Unknown application action.")
        return redirect(url_for("alliances"))
    with get_db() as db:
        user = get_current_user(db)
        membership = get_membership(db, user["id"])
        application = db.execute(
            "SELECT * FROM alliance_applications WHERE id = ? AND status = 'pending'",
            (application_id,),
        ).fetchone()
        if not membership or membership["role"] not in ("leader", "officer") or not application:
            flash("You cannot manage that application.")
            return redirect(url_for("alliances"))
        if membership["alliance_id"] != application["alliance_id"]:
            flash("You cannot manage that application.")
            return redirect(url_for("alliances"))
        if get_membership(db, application["user_id"]):
            db.execute(
                "UPDATE alliance_applications SET status = 'cancelled', decided_at = ?, decided_by = ? WHERE id = ?",
                (utc_now().isoformat(), user["id"], application_id),
            )
            db.commit()
            flash("Applicant is already in an alliance; application cancelled.")
            return redirect(url_for("alliances"))

        new_status = "approved" if action == "approve" else "rejected"
        db.execute(
            "UPDATE alliance_applications SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (new_status, utc_now().isoformat(), user["id"], application_id),
        )
        if action == "approve":
            db.execute(
                """
                INSERT INTO alliance_members (alliance_id, user_id, role, joined_at)
                VALUES (?, ?, 'member', ?)
                """,
                (application["alliance_id"], application["user_id"], utc_now().isoformat()),
            )
            db.execute(
                """
                UPDATE alliance_applications
                SET status = 'cancelled', decided_at = ?, decided_by = ?
                WHERE user_id = ? AND status = 'pending' AND id != ?
                """,
                (utc_now().isoformat(), user["id"], application["user_id"], application_id),
            )
        db.commit()
        flash(f"Application {new_status}.")
    return redirect(url_for("alliances"))


@app.route("/alliance")
@login_required
def alliance_home():
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        alliance = get_alliance_stats(db, membership["alliance_id"])
        objective_row = db.execute("SELECT target_blooms FROM alliance_spice_objectives WHERE alliance_id = ?", (membership["alliance_id"],)).fetchone()
        target_blooms = objective_row["target_blooms"] if objective_row else 3
        alliance_blooms = db.execute("""SELECT map_tiles.*, users.username FROM map_tiles
            JOIN villages ON villages.id = map_tiles.controller_village_id
            JOIN users ON users.id = villages.user_id
            JOIN alliance_members ON alliance_members.user_id = users.id
            WHERE alliance_members.alliance_id = ? AND map_tiles.tile_type = 'spice_bloom_large'
            ORDER BY map_tiles.x, map_tiles.y""", (membership["alliance_id"],)).fetchall()

        members = db.execute(
            """
            SELECT users.id AS user_id, users.username, alliance_members.role, alliance_members.joined_at,
                   factions.name AS faction_name, COALESCE(SUM(villages.points), 0) AS points
            FROM alliance_members
            JOIN users ON users.id = alliance_members.user_id
            JOIN factions ON factions.id = users.faction_id
            LEFT JOIN villages ON villages.user_id = users.id
            WHERE alliance_members.alliance_id = ?
            GROUP BY users.id
            ORDER BY points DESC, users.username ASC
            """,
            (membership["alliance_id"],),
        ).fetchall()
        topics = db.execute(
            """
            SELECT alliance_topics.*, users.username,
                   COUNT(alliance_posts.id) AS post_count
            FROM alliance_topics
            JOIN users ON users.id = alliance_topics.created_by
            LEFT JOIN alliance_posts ON alliance_posts.topic_id = alliance_topics.id
            WHERE alliance_topics.alliance_id = ?
            GROUP BY alliance_topics.id
            ORDER BY alliance_topics.updated_at DESC
            """,
            (membership["alliance_id"],),
        ).fetchall()
        applications = []
        if membership["role"] in ("leader", "officer"):
            applications = db.execute(
                """
                SELECT alliance_applications.*, users.username
                FROM alliance_applications
                JOIN users ON users.id = alliance_applications.user_id
                WHERE alliance_applications.alliance_id = ? AND alliance_applications.status = 'pending'
                ORDER BY alliance_applications.created_at ASC
                """,
                (membership["alliance_id"],),
            ).fetchall()
    return render_template(
        "alliance_home.html",
        target_blooms=target_blooms,
        alliance_blooms=alliance_blooms,
        user=user,
        membership=membership,
        alliance=alliance,
        members=members,
        topics=topics,
        applications=applications,
    )


@app.route("/alliance/spice-objective", methods=("POST",))
@login_required
def set_spice_objective():
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership or membership["role"] not in ("leader", "officer"):
            abort(403)
        try:
            target = int(request.form.get("target_blooms", ""))
        except ValueError:
            abort(400)
        if not 1 <= target <= 20:
            abort(400)
        db.execute("INSERT INTO alliance_spice_objectives (alliance_id, target_blooms) VALUES (?, ?) ON CONFLICT(alliance_id) DO UPDATE SET target_blooms = excluded.target_blooms", (membership["alliance_id"], target))
        db.commit()
    flash("Alliance spice objective updated.")
    return redirect(url_for("alliance_home"))


@app.route("/alliance/leave", methods=("POST",))
@login_required
def leave_alliance():
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("You are not in an alliance.")
            return redirect(url_for("alliances"))
        if membership["role"] == "leader":
            flash("Transfer leadership or disband the alliance before leaving.")
            return redirect(url_for("alliance_home"))
        db.execute("DELETE FROM alliance_members WHERE alliance_id = ? AND user_id = ?", (membership["alliance_id"], user["id"]))
        db.commit()
        flash("You left the alliance.")
    return redirect(url_for("alliances"))


@app.route("/alliance/members/<int:member_user_id>/<action>", methods=("POST",))
@login_required
def manage_alliance_member(member_user_id, action):
    if action not in ("promote", "demote", "kick", "transfer"):
        flash("Unknown member action.")
        return redirect(url_for("alliance_home"))
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        target = db.execute(
            """
            SELECT alliance_members.*, users.username
            FROM alliance_members
            JOIN users ON users.id = alliance_members.user_id
            WHERE alliance_members.alliance_id = ? AND alliance_members.user_id = ?
            """,
            (membership["alliance_id"], member_user_id),
        ).fetchone()
        if not target:
            flash("Alliance member not found.")
            return redirect(url_for("alliance_home"))
        if target["user_id"] == user["id"]:
            flash("Use the leave alliance action for yourself.")
            return redirect(url_for("alliance_home"))

        if action == "kick":
            if membership["role"] not in ("leader", "officer") or target["role"] != "member":
                flash("Only normal members can be kicked by leaders or officers.")
                return redirect(url_for("alliance_home"))
            db.execute("DELETE FROM alliance_members WHERE alliance_id = ? AND user_id = ?", (membership["alliance_id"], target["user_id"]))
            db.commit()
            flash(f"{target['username']} was removed from the alliance.")
            return redirect(url_for("alliance_home"))

        if membership["role"] != "leader":
            flash("Only the leader can change member roles.")
            return redirect(url_for("alliance_home"))
        if action == "promote":
            if target["role"] != "member":
                flash("Only normal members can be promoted.")
                return redirect(url_for("alliance_home"))
            db.execute("UPDATE alliance_members SET role = 'officer' WHERE alliance_id = ? AND user_id = ?", (membership["alliance_id"], target["user_id"]))
            flash(f"{target['username']} was promoted to officer.")
        elif action == "demote":
            if target["role"] != "officer":
                flash("Only officers can be demoted.")
                return redirect(url_for("alliance_home"))
            db.execute("UPDATE alliance_members SET role = 'member' WHERE alliance_id = ? AND user_id = ?", (membership["alliance_id"], target["user_id"]))
            flash(f"{target['username']} was demoted to member.")
        elif action == "transfer":
            if target["role"] not in ("member", "officer"):
                flash("Leadership can only be transferred to an alliance member.")
                return redirect(url_for("alliance_home"))
            db.execute("UPDATE alliances SET leader_user_id = ? WHERE id = ?", (target["user_id"], membership["alliance_id"]))
            db.execute("UPDATE alliance_members SET role = 'officer' WHERE alliance_id = ? AND user_id = ?", (membership["alliance_id"], user["id"]))
            db.execute("UPDATE alliance_members SET role = 'leader' WHERE alliance_id = ? AND user_id = ?", (membership["alliance_id"], target["user_id"]))
            flash(f"Leadership transferred to {target['username']}.")
        db.commit()
    return redirect(url_for("alliance_home"))


@app.route("/alliance/topics/create", methods=("POST",))
@login_required
def create_topic():
    title = request.form["title"].strip()
    body = request.form["body"].strip()
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        if not title or not body:
            flash("Topic title and message are required.")
            return redirect(url_for("alliance_home"))
        now = utc_now().isoformat()
        cursor = db.execute(
            """
            INSERT INTO alliance_topics (alliance_id, created_by, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (membership["alliance_id"], user["id"], title, now, now),
        )
        db.execute(
            "INSERT INTO alliance_posts (topic_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (cursor.lastrowid, user["id"], body, now),
        )
        db.commit()
        flash("Topic created.")
    return redirect(url_for("alliance_home"))


@app.route("/alliance/topics/<int:topic_id>")
@login_required
def view_topic(topic_id):
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        topic = db.execute(
            """
            SELECT alliance_topics.*, users.username
            FROM alliance_topics
            JOIN users ON users.id = alliance_topics.created_by
            WHERE alliance_topics.id = ? AND alliance_topics.alliance_id = ?
            """,
            (topic_id, membership["alliance_id"]),
        ).fetchone()
        if not topic:
            flash("Topic not found.")
            return redirect(url_for("alliance_home"))
        posts = db.execute(
            """
            SELECT alliance_posts.*, users.username, alliance_members.role
            FROM alliance_posts
            JOIN users ON users.id = alliance_posts.user_id
            JOIN alliance_members ON alliance_members.user_id = users.id
            WHERE alliance_posts.topic_id = ? AND alliance_members.alliance_id = ?
            ORDER BY alliance_posts.created_at ASC
            """,
            (topic_id, membership["alliance_id"]),
        ).fetchall()
    return render_template("alliance_topic.html", topic=topic, posts=posts, membership=membership)


@app.route("/alliance/topics/<int:topic_id>/reply", methods=("POST",))
@login_required
def reply_topic(topic_id):
    body = request.form["body"].strip()
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        topic = db.execute(
            "SELECT * FROM alliance_topics WHERE id = ? AND alliance_id = ?",
            (topic_id, membership["alliance_id"]),
        ).fetchone()
        if not topic:
            flash("Topic not found.")
            return redirect(url_for("alliance_home"))
        if not body:
            flash("Reply cannot be empty.")
            return redirect(url_for("view_topic", topic_id=topic_id))
        now = utc_now().isoformat()
        db.execute(
            "INSERT INTO alliance_posts (topic_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (topic_id, user["id"], body, now),
        )
        db.execute("UPDATE alliance_topics SET updated_at = ? WHERE id = ?", (now, topic_id))
        db.commit()
        flash("Reply posted.")
    return redirect(url_for("view_topic", topic_id=topic_id))


@app.route("/alliance/topics/<int:topic_id>/delete", methods=("POST",))
@login_required
def delete_topic(topic_id):
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        if membership["role"] not in ("leader", "officer"):
            flash("Only leaders and officers can delete topics.")
            return redirect(url_for("view_topic", topic_id=topic_id))
        topic = db.execute(
            "SELECT * FROM alliance_topics WHERE id = ? AND alliance_id = ?",
            (topic_id, membership["alliance_id"]),
        ).fetchone()
        if not topic:
            flash("Topic not found.")
            return redirect(url_for("alliance_home"))
        db.execute("DELETE FROM alliance_posts WHERE topic_id = ?", (topic_id,))
        db.execute("DELETE FROM alliance_topics WHERE id = ?", (topic_id,))
        db.commit()
        flash("Topic deleted.")
    return redirect(url_for("alliance_home"))


@app.route("/alliance/posts/<int:post_id>/delete", methods=("POST",))
@login_required
def delete_post(post_id):
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("Join or create an alliance first.")
            return redirect(url_for("alliances"))
        post = db.execute(
            """
            SELECT alliance_posts.*, alliance_topics.alliance_id
            FROM alliance_posts
            JOIN alliance_topics ON alliance_topics.id = alliance_posts.topic_id
            WHERE alliance_posts.id = ?
            """,
            (post_id,),
        ).fetchone()
        if not post or post["alliance_id"] != membership["alliance_id"]:
            flash("Post not found.")
            return redirect(url_for("alliance_home"))
        if membership["role"] not in ("leader", "officer"):
            flash("Only leaders and officers can delete posts.")
            return redirect(url_for("view_topic", topic_id=post["topic_id"]))
        remaining_posts = db.execute(
            "SELECT COUNT(*) AS count FROM alliance_posts WHERE topic_id = ?",
            (post["topic_id"],),
        ).fetchone()["count"]
        if remaining_posts <= 1:
            db.execute("DELETE FROM alliance_posts WHERE topic_id = ?", (post["topic_id"],))
            db.execute("DELETE FROM alliance_topics WHERE id = ?", (post["topic_id"],))
            db.commit()
            flash("Last post deleted, so the topic was deleted too.")
            return redirect(url_for("alliance_home"))
        db.execute("DELETE FROM alliance_posts WHERE id = ?", (post_id,))
        latest = db.execute(
            "SELECT MAX(created_at) AS updated_at FROM alliance_posts WHERE topic_id = ?",
            (post["topic_id"],),
        ).fetchone()["updated_at"]
        db.execute("UPDATE alliance_topics SET updated_at = ? WHERE id = ?", (latest, post["topic_id"]))
        db.commit()
        flash("Post deleted.")
    return redirect(url_for("view_topic", topic_id=post["topic_id"]))


@app.route("/alliance/disband", methods=("POST",))
@login_required
def disband_alliance():
    confirm_tag = request.form["confirm_tag"].strip().upper()
    with get_db() as db:
        user, membership = get_alliance_member_or_redirect(db)
        if not membership:
            flash("You are not in an alliance.")
            return redirect(url_for("alliances"))
        if membership["role"] != "leader":
            flash("Only the alliance leader can disband the alliance.")
            return redirect(url_for("alliance_home"))
        alliance = db.execute("SELECT * FROM alliances WHERE id = ?", (membership["alliance_id"],)).fetchone()
        if not alliance or confirm_tag != alliance["tag"]:
            flash("Type the alliance tag exactly to confirm disbanding.")
            return redirect(url_for("alliance_home"))
        topic_ids = [row["id"] for row in db.execute("SELECT id FROM alliance_topics WHERE alliance_id = ?", (alliance["id"],)).fetchall()]
        if topic_ids:
            placeholders = ",".join("?" for _ in topic_ids)
            db.execute(f"DELETE FROM alliance_posts WHERE topic_id IN ({placeholders})", topic_ids)
        db.execute("DELETE FROM alliance_topics WHERE alliance_id = ?", (alliance["id"],))
        db.execute("DELETE FROM alliance_applications WHERE alliance_id = ?", (alliance["id"],))
        db.execute("DELETE FROM alliance_members WHERE alliance_id = ?", (alliance["id"],))
        db.execute("DELETE FROM alliance_spice_objectives WHERE alliance_id = ?", (alliance["id"],))
        db.execute("DELETE FROM alliances WHERE id = ?", (alliance["id"],))
        db.commit()
        flash("Alliance permanently disbanded.")
    return redirect(url_for("alliances"))


@app.route("/upgrade/<building_key>", methods=("POST",))
@login_required
def upgrade(building_key):
    next_url = request.form.get("next") or url_for("dashboard")
    if not safe_local_redirect(next_url):
        next_url = url_for("dashboard")
    if building_key not in BUILDINGS:
        flash("Unknown building.")
        return redirect(next_url)

    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_research_queue(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        buildings = get_buildings(db, village["id"])
        research_levels = get_research_levels(db, village["id"])
        construction_queue = get_construction_queue(db, village["id"])
        if len(construction_queue) >= CONSTRUCTION_QUEUE_LIMIT:
            flash("Construction queue is full.")
            return redirect(next_url)
        unmet = unmet_prerequisites(building_key, buildings)
        if unmet:
            flash(f"Requires {prerequisite_text(building_key, user['faction_slug'])}.")
            return redirect(next_url)
        research_unmet = building_research_unmet(building_key, research_levels)
        if research_unmet:
            flash(f"Requires {research_prerequisite_text(research_unmet)}.")
            return redirect(next_url)
        current_level = effective_building_level(buildings, construction_queue, building_key)
        cost = upgrade_cost(building_key, current_level)
        if not can_afford(village, cost):
            flash("Not enough resources for that upgrade.")
            return redirect(next_url)

        db.execute(
            """
            UPDATE villages
            SET iron = iron - ?, wood = wood - ?, water = water - ?, spice = spice - ?, melange = melange - ?
            WHERE id = ?
            """,
            (cost.get("iron", 0), cost.get("wood", 0), cost.get("water", 0), cost.get("spice", 0), cost.get("melange", 0), village["id"]),
        )
        record_point_spending(db, village, cost)
        started_at = None
        finish_at = None
        duration = construction_duration_seconds(current_level, research_levels)
        if not construction_queue:
            started_at_dt = utc_now()
            started_at = started_at_dt.isoformat()
            finish_at = (started_at_dt + timedelta(seconds=duration)).isoformat()
        db.execute(
            """
            INSERT INTO construction_queue (village_id, building_key, target_level, duration_seconds, started_at, finish_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (village["id"], building_key, current_level + 1, duration, started_at, finish_at, utc_now().isoformat()),
        )
        db.commit()
        flash(f"{BUILDINGS[building_key]['name']} queued for construction.")
    return redirect(next_url)


@app.route("/train/<unit_key>", methods=("POST",))
@login_required
def train_unit(unit_key):
    next_url = request.form.get("next") or url_for("dashboard")
    if unit_key not in UNIT_TYPES:
        flash("Unknown unit.")
        return redirect(next_url)
    try:
        amount = int(request.form.get("amount", "1"))
    except ValueError:
        amount = 1
    amount = max(1, min(amount, 999))

    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_unit_training_queue(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        buildings = get_buildings(db, village["id"])
        research_levels = get_research_levels(db, village["id"])
        queue = get_unit_training_queue(db, village["id"])

        if len(queue) >= UNIT_QUEUE_LIMIT:
            flash("Unit training queue is full.")
            return redirect(next_url)
        if not is_unit_unlocked(unit_key, buildings, research_levels):
            unit = UNIT_TYPES[unit_key]
            parts = [f"{building_display_name(unit['unlock_building'], user['faction_slug'])} level {unit['unlock_level']}"]
            research_text = unit_research_requirement_text(unit_key)
            if research_text:
                parts.append(research_text)
            flash(f"Requires {', '.join(parts)}.")
            return redirect(next_url)

        cost = unit_training_cost(unit_key, amount)
        if not can_afford(village, cost):
            flash("Not enough resources to train those units.")
            return redirect(next_url)

        db.execute(
            """
            UPDATE villages
            SET iron = iron - ?, wood = wood - ?, water = water - ?, spice = spice - ?, melange = melange - ?
            WHERE id = ?
            """,
            (cost.get("iron", 0), cost.get("wood", 0), cost.get("water", 0), cost.get("spice", 0), cost.get("melange", 0), village["id"]),
        )
        started_at = None
        finish_at = None
        duration = unit_training_duration(unit_key, amount, research_levels)
        if not queue:
            started_at_dt = utc_now()
            started_at = started_at_dt.isoformat()
            finish_at = (started_at_dt + timedelta(seconds=duration)).isoformat()
        db.execute(
            """
            INSERT INTO unit_training_queue (village_id, unit_key, amount, duration_seconds, started_at, finish_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (village["id"], unit_key, amount, duration, started_at, finish_at, utc_now().isoformat()),
        )
        db.commit()
        flash(f"{amount} {unit_display_name(unit_key, user['faction_slug'])} queued for training.")
    return redirect(next_url)


@app.route("/research/<research_key>", methods=("POST",))
@login_required
def start_research(research_key):
    next_url = request.form.get("next") or url_for("dashboard")
    if research_key not in RESEARCH_TYPES:
        flash("Unknown research.")
        return redirect(next_url)

    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_unit_training_queue(db, village["id"])
        process_research_queue(db, village["id"])
        db.commit()
        village = get_village(db, user["id"])
        buildings = get_buildings(db, village["id"])
        research_levels = get_research_levels(db, village["id"])
        queue = get_research_queue(db, village["id"])

        if len(queue) >= RESEARCH_QUEUE_LIMIT:
            flash("Research queue is full.")
            return redirect(next_url)
        effective_level = effective_research_level(research_levels, queue, research_key)
        if effective_level >= RESEARCH_TYPES[research_key]["max_level"]:
            flash("That research is already maxed.")
            return redirect(next_url)
        building_unmet = research_building_unmet(research_key, buildings)
        if building_unmet:
            flash(f"Requires {prerequisite_text_for_buildings(building_unmet, user['faction_slug'])}.")
            return redirect(next_url)
        cost = research_cost(research_key, effective_level)
        if not can_afford(village, cost):
            flash("Not enough resources for that research.")
            return redirect(next_url)

        db.execute(
            """
            UPDATE villages
            SET iron = iron - ?, wood = wood - ?, water = water - ?, spice = spice - ?, melange = melange - ?
            WHERE id = ?
            """,
            (cost.get("iron", 0), cost.get("wood", 0), cost.get("water", 0), cost.get("spice", 0), cost.get("melange", 0), village["id"]),
        )
        record_point_spending(db, village, cost)
        duration = research_duration_seconds(research_key, effective_level)
        started_at = utc_now()
        db.execute(
            """
            INSERT INTO research_queue (village_id, research_key, target_level, duration_seconds, started_at, finish_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                village["id"],
                research_key,
                effective_level + 1,
                duration,
                started_at.isoformat(),
                (started_at + timedelta(seconds=duration)).isoformat(),
                utc_now().isoformat(),
            ),
        )
        db.commit()
        flash(f"{RESEARCH_TYPES[research_key]['name']} queued for research.")
    return redirect(next_url)


@app.route("/tutorial/claim", methods=("POST",))
@login_required
def claim_tutorial_reward():
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        village, buildings = update_resources(db, village, user["faction_slug"])
        process_construction_queue(db, village["id"])
        process_research_queue(db, village["id"])
        buildings = get_buildings(db, village["id"])
        progress = get_tutorial_progress(db, user["id"])
        if progress["step_index"] >= len(TUTORIAL_STEPS):
            flash("Tutorial already complete.")
            return redirect(url_for("dashboard"))
        step = TUTORIAL_STEPS[progress["step_index"]]
        objective_ready = get_tutorial_state(db, user, buildings)["ready"]
        if not objective_ready:
            flash("Tutorial objective is not complete yet.")
            return redirect(url_for("dashboard"))
        apply_resource_reward(db, village["id"], step["reward"])
        db.execute(
            "UPDATE user_tutorial_progress SET step_index = step_index + 1, updated_at = ? WHERE user_id = ?",
            (utc_now().isoformat(), user["id"]),
        )
        db.commit()
        flash("Tutorial reward claimed.")
    return redirect(url_for("dashboard"))


@app.route("/dev/boost-resources", methods=("POST",))
@login_required
def dev_boost_resources():
    if not DEV_TOOLS_ENABLED or request.remote_addr not in ("127.0.0.1", "::1"):
        abort(404)
    with get_db() as db:
        user = get_current_user(db)
        village = get_village(db, user["id"])
        db.execute(
            "UPDATE villages SET iron = ?, wood = ?, water = ?, spice = ?, melange = ? WHERE id = ?",
            (DEV_RESOURCE_AMOUNT, DEV_RESOURCE_AMOUNT, DEV_RESOURCE_AMOUNT, DEV_RESOURCE_AMOUNT, DEV_RESOURCE_AMOUNT, village["id"]),
        )
        db.commit()
        flash("Dev resources boosted.")
    return redirect(url_for("dashboard"))


@app.route("/dev/reset-password", methods=("GET", "POST"))
def dev_reset_password():
    admin_token = os.environ.get("DEV_ADMIN_TOKEN", "")
    if not DEV_TOOLS_ENABLED or not admin_token or request.remote_addr not in ("127.0.0.1", "::1"):
        abort(404)
    if request.method == "POST" and not secrets.compare_digest(request.form.get("admin_token", "").encode(), admin_token.encode()):
        abort(403)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        new_password = request.form.get("new_password", "")
        if not username or not valid_signup_password(new_password):
            flash("Username and a new password of 15–128 characters are required.")
            return redirect(url_for("dev_reset_password"))
        with get_db() as db:
            user = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if not user:
                flash("User not found.")
                return redirect(url_for("dev_reset_password"))
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_account_password(new_password), user["id"]),
            )
            db.execute("DELETE FROM login_sessions WHERE user_id = ?", (user["id"],))
            db.commit()
        flash("Password reset. Log in with the new password.")
        return redirect(url_for("login"))
    return render_template("dev_reset_password.html")


if __name__ == "__main__":
    init_db()
    app.run(debug=not app.config["PRODUCTION"] and os.environ.get("FLASK_DEBUG", "0") == "1")

