import json
import unittest
from datetime import timedelta

import test_garrison_recall as fixtures
import app as game


class CombatV2Tests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_elite_units_are_faction_specific_and_research_locked(self):
        buildings = {key: 0 for key in game.BUILDINGS}
        buildings["barracks"] = 6
        self.assertFalse(game.is_unit_unlocked("elite_guard", buildings, game.ResearchLevels({"unit_shield": 1}, "atreides")))
        self.assertTrue(game.is_unit_unlocked("elite_guard", buildings, game.ResearchLevels({"unit_shield": 2}, "atreides")))
        self.assertGreater(game.faction_unit_stat("elite_guard", "shield", "atreides"), game.faction_unit_stat("elite_guard", "shield", "fremen"))
        self.assertGreater(game.faction_unit_stat("elite_guard", "damage", "harkonnen"), game.faction_unit_stat("elite_guard", "damage", "atreides"))
        self.assertGreater(game.faction_unit_stat("elite_guard", "shield_piercing", "fremen"), game.faction_unit_stat("elite_guard", "shield_piercing", "harkonnen"))

    def test_round_combat_absorbs_shield_and_tracks_penetration(self):
        attacker = game.ResearchLevels({"unit_damage": 2}, "fremen")
        defender = game.ResearchLevels({"unit_shield": 3}, "atreides")
        result = game.resolve_combat(
            {"elite_guard": 5},
            {"elite_guard": 4},
            attacker,
            defender,
            defender_is_player=True,
        )
        self.assertGreaterEqual(result["round_count"], 1)
        self.assertGreater(result["attacker_shield_absorbed"], 0)
        self.assertGreater(result["defender_shield_absorbed"], 0)
        self.assertGreater(result["attacker_piercing_percent"], result["defender_piercing_percent"])
        self.assertIn("elite_guard", result["attacker_survivors"])

    def test_combat_report_persists_rounds_and_renders_summary(self):
        with game.get_db() as db:
            village = db.execute("SELECT * FROM villages WHERE id = ?", (self.village_id,)).fetchone()
            tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (self.tile_id,)).fetchone()
            now = game.utc_now().isoformat()
            cursor = db.execute(
                """INSERT INTO troop_movements
                   (village_id, target_tile_id, target_x, target_y, mission_type, units_json, status, started_at, arrive_at)
                   VALUES (?, ?, ?, ?, 'raid', ?, 'complete', ?, ?)""",
                (self.village_id, self.tile_id, tile["x"], tile["y"], game.player_units_json({"elite_guard": 4}), now, now),
            )
            movement = db.execute("SELECT * FROM troop_movements WHERE id = ?", (cursor.lastrowid,)).fetchone()
            combat = game.resolve_combat(
                {"elite_guard": 4}, {"raider": 2}, game.ResearchLevels({"unit_shield": 2}, "atreides")
            )
            game.create_battle_report(
                db, village, movement, tile, "Victory", {"elite_guard": 4}, combat["attacker_survivors"],
                {"raider": 2}, combat["defender_survivors"], {"iron": 0, "wood": 0, "water": 0, "spice": 0},
                combat=combat,
            )
            db.commit()
            report = db.execute("SELECT * FROM battle_reports ORDER BY id DESC LIMIT 1").fetchone()
            report_id = report["id"]
            saved = json.loads(report["combat_json"])
            self.assertEqual(saved["round_count"], combat["round_count"])
            self.assertGreater(saved["attacker_shield_absorbed"], 0)

        response = self.client.get(f"/inbox/{report_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Combat V2", response.data)
        self.assertIn(b"Shield and damage summary", response.data)
        self.assertIn(b"Atreides Shield Guard", response.data)

    def test_player_garrison_capture_uses_round_combat_and_player_report(self):
        with game.get_db() as db:
            faction_id = db.execute("SELECT id FROM factions WHERE slug = 'harkonnen'").fetchone()[0]
            defender_user = db.execute(
                "INSERT INTO users (username, password_hash, faction_id, created_at) VALUES (?, ?, ?, ?)",
                ("defender", game.hash_account_password("defender-password-long"), faction_id, game.utc_now().isoformat()),
            ).lastrowid
            game.create_starting_village(db, defender_user)
            defender_village = game.get_village(db, defender_user)
            db.execute(
                "UPDATE map_tiles SET tile_type = 'spice_bloom_large', controller_village_id = ?, garrison_json = ?, npc_strength = 0, npc_units_json = '{}' WHERE id = ?",
                (defender_village["id"], game.player_units_json({"elite_guard": 2}), self.tile_id),
            )
            village = db.execute("SELECT * FROM villages WHERE id = ?", (self.village_id,)).fetchone()
            now = game.utc_now()
            db.execute(
                """INSERT INTO troop_movements
                   (village_id, target_tile_id, target_x, target_y, mission_type, units_json, status, started_at, arrive_at)
                   VALUES (?, ?, ?, ?, 'capture', ?, 'outbound', ?, ?)""",
                (self.village_id, self.tile_id, 1, 1, game.player_units_json({"elite_guard": 8}),
                 (now - timedelta(seconds=20)).isoformat(), (now - timedelta(seconds=10)).isoformat()),
            )
            db.commit()
            game.process_troop_movements(db, self.village_id)
            db.commit()
            tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (self.tile_id,)).fetchone()
            report = db.execute("SELECT * FROM battle_reports WHERE report_type = 'capture' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(tile["controller_village_id"], self.village_id)
            self.assertEqual(game.player_units_from_json(report["enemy_player_before_json"]), {"elite_guard": 2})
            self.assertGreater(json.loads(report["combat_json"])["round_count"], 0)


if __name__ == "__main__":
    unittest.main()
