import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from werkzeug.security import check_password_hash
import test_garrison_recall as recall_tests
import app as game


class GameSystemsTests(unittest.TestCase):
    setUp = recall_tests.GarrisonRecallTests.setUp
    tearDown = recall_tests.GarrisonRecallTests.tearDown

    def test_faction_stats_research_and_upkeep(self):
        atreides = game.ResearchLevels({}, "atreides")
        harkonnen = game.ResearchLevels({"unit_damage": 2}, "harkonnen")
        self.assertEqual(game.effective_unit_stat("knife_fighter", "health", atreides), 46)
        self.assertEqual(game.effective_unit_stat("knife_fighter", "armor", atreides), 1.2)
        self.assertEqual(game.effective_unit_stat("knife_fighter", "damage", harkonnen), 15.2)
        self.assertGreater(game.player_unit_summary({"knife_fighter": 10}, atreides)["durability"], game.player_unit_summary({"knife_fighter": 10})["durability"])
        self.assertEqual(game.unit_water_consumption_per_hour({"knife_fighter": 10}, "fremen"), 2)
        self.assertEqual(game.unit_water_consumption_per_hour({"knife_fighter": 10}, "harkonnen"), 2.75)
        self.assertLess(game.movement_duration_seconds(0, 0, 10, 0, {"knife_fighter": 5}, "fremen"), game.movement_duration_seconds(0, 0, 10, 0, {"knife_fighter": 5}))
        self.assertEqual(game.faction_unit_stat("carryall", "speed", "fremen"), game.UNIT_TYPES["carryall"]["speed"])

    def test_plan_does_not_leak_unscouted_defense_or_mutate(self):
        with game.get_db() as db:
            tile = db.execute("SELECT * FROM map_tiles WHERE tile_type = 'npc_camp' LIMIT 1").fetchone()
            before = db.execute("SELECT COUNT(*) FROM troop_movements").fetchone()[0]
        url = f"/map/{tile['id']}/plan"
        response = self.client.get(url, query_string={"mission": "raid", "unit_knife_fighter": 3})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNone(data["known_defense"])
        self.assertIsNone(data["intel_age_seconds"])
        self.assertIn("Unknown", data["risk"])
        self.assertEqual(data["attack"], 36)
        self.assertEqual(data["carry"], 60)
        with game.get_db() as db:
            village = db.execute("SELECT * FROM villages WHERE id = ?", (self.village_id,)).fetchone()
            duration = game.movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], {"knife_fighter": 3}, "atreides")
            self.assertEqual(data["travel_seconds"], duration)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM troop_movements").fetchone()[0], before)
            self.assertEqual(game.get_village_units(db, self.village_id)["knife_fighter"], 3)
            game.save_tile_intel(db, village["user_id"], tile)
            db.commit()
        fresh = self.client.get(url, query_string={"unit_knife_fighter": 3}).get_json()
        self.assertGreater(fresh["known_defense"], 0)
        with patch.object(game, "utc_now", return_value=game.utc_now() + timedelta(hours=2)):
            stale = self.client.get(url, query_string={"unit_knife_fighter": 3}).get_json()
        self.assertTrue(stale["stale"])
        self.assertIn("Uncertain", stale["risk"])
        for value in ("4", "-1", "1.2"):
            self.assertEqual(self.client.get(url, query_string={"unit_knife_fighter": value}).status_code, 400)
        self.client.get("/logout")
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_transport_preview_matches_dispatch_and_capture_stays(self):
        with game.get_db() as db:
            game.add_village_units(db, self.village_id, "carryall", 1)
            db.commit()
        data = self.client.get(f"/map/{self.tile_id}/plan", query_string={"mission": "reinforce", "unit_knife_fighter": 3, "unit_carryall": 1}).get_json()
        self.assertIsNone(data["return_at"])
        self.client.post(f"/map/{self.tile_id}/reinforce", data={"unit_knife_fighter": 3, "unit_carryall": 1})
        with game.get_db() as db:
            movement = db.execute("SELECT * FROM troop_movements WHERE mission_type = 'reinforce'").fetchone()
            actual = (game.parse_time(movement["arrive_at"]) - game.parse_time(movement["started_at"])).total_seconds()
            self.assertEqual(data["travel_seconds"], actual)

    def test_fremen_selected_ground_force_preview_matches_dispatch(self):
        with game.get_db() as db:
            db.execute("UPDATE users SET faction_id = (SELECT id FROM factions WHERE slug = 'fremen') WHERE username = 'commander'")
            village = db.execute("SELECT * FROM villages WHERE id = ?", (self.village_id,)).fetchone()
            tile = db.execute("SELECT * FROM map_tiles WHERE tile_type = 'iron_outcrop' ORDER BY ABS(x - ?) + ABS(y - ?) DESC LIMIT 1", (village["map_x"], village["map_y"])).fetchone()
            db.commit()
        data = self.client.get(f"/map/{tile['id']}/plan", query_string={"unit_knife_fighter": 3}).get_json()
        self.client.post(f"/map/{tile['id']}/send", data={"unit_knife_fighter": 3})
        with game.get_db() as db:
            movement = db.execute("SELECT * FROM troop_movements WHERE mission_type = 'raid'").fetchone()
            duration = (game.parse_time(movement["arrive_at"]) - game.parse_time(movement["started_at"])).total_seconds()
            self.assertEqual(data["travel_seconds"], duration)
            self.assertLess(duration, game.movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], {"knife_fighter": 3}))

    def test_alliance_goal_permissions_and_live_ownership(self):
        with game.get_db() as db:
            db.execute("UPDATE village_buildings SET level = 10 WHERE village_id = ? AND building_key = 'embassy'", (self.village_id,))
            db.commit()
        response = self.client.post("/alliances/create", data={"alliance_name": "Desert Alliance", "alliance_tag": "TEST", "alliance_description": "Test"})
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/alliance")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"1 / 3 Large Spice Blooms", response.data)
        self.assertEqual(self.client.post("/alliance/spice-objective", data={"target_blooms": 1}).status_code, 302)
        self.assertIn(b"Goal reached", self.client.get("/alliance").data)
        self.assertEqual(self.client.post("/alliance/spice-objective", data={"target_blooms": 21}).status_code, 400)
        with game.get_db() as db:
            db.execute("UPDATE map_tiles SET controller_village_id = NULL WHERE id = ?", (self.tile_id,))
            db.execute("UPDATE alliance_members SET role = 'member'")
            db.commit()
        self.assertIn(b"0 / 1 Large Spice Blooms", self.client.get("/alliance").data)
        self.assertEqual(self.client.post("/alliance/spice-objective", data={"target_blooms": 2}).status_code, 403)
        with game.get_db() as db:
            db.execute("DELETE FROM alliance_members")
            db.commit()
        self.assertEqual(self.client.post("/alliance/spice-objective", data={"target_blooms": 2}).status_code, 403)

    def test_dev_tools_are_opt_in_local_and_token_protected(self):
        self.assertNotEqual(game.app.secret_key, "dev-secret-change-me")
        with patch.object(game, "DEV_TOOLS_ENABLED", False):
            self.assertEqual(self.client.get("/dev/reset-password").status_code, 404)
            self.assertEqual(self.client.post("/dev/boost-resources").status_code, 404)
        with patch.object(game, "DEV_TOOLS_ENABLED", True), patch.dict(os.environ, {"DEV_ADMIN_TOKEN": "test-admin-token"}):
            data = {"username": "commander", "new_password": "updated-password", "admin_token": "test-admin-token"}
            self.assertEqual(self.client.post("/dev/reset-password", data=data, environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 404)
            self.assertEqual(self.client.post("/dev/boost-resources", environ_overrides={"REMOTE_ADDR": "192.0.2.1"}).status_code, 404)
            self.assertEqual(self.client.post("/dev/reset-password", data={**data, "admin_token": "wrong"}).status_code, 403)
            self.assertEqual(self.client.post("/dev/reset-password", data=data).status_code, 302)
            with game.get_db() as db:
                row = db.execute("SELECT password_hash FROM users WHERE username = 'commander'").fetchone()
                self.assertTrue(check_password_hash(row["password_hash"], "updated-password"))


if __name__ == "__main__":
    unittest.main()
