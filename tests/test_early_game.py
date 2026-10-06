import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as game
from browser_client import BrowserClient
from scripts.simulate_early_game import Projection


class EarlyGameTests(unittest.TestCase):
    def run_progression(self, faction):
        with tempfile.TemporaryDirectory() as temp:
            connections = []
            original_get_db = game.get_db
            def tracked_get_db():
                db = original_get_db()
                connections.append(db)
                return db
            start = game.utc_now()
            self.now = start
            with patch.object(game, "DB_PATH", str(Path(temp) / "game.db")), patch.object(game, "get_db", side_effect=tracked_get_db), patch.object(game, "utc_now", side_effect=lambda: self.now):
                try:
                    game.app.config["TESTING"] = True
                    game.app.test_client_class = BrowserClient
                    game.init_db()
                    client = game.app.test_client()
                    client.get("/register")
                    with client.session_transaction() as account_session:
                        signup_token = account_session["auth_csrf_token"]
                    client.post("/register", data={"username": "player", "password": "test-password-long", "confirm_password": "test-password-long", "csrf_token": signup_token, "faction": faction})
                    client.post("/login", data={"username": "player", "password": "test-password-long", "csrf_token": signup_token})
                    def snapshot():
                        with game.get_db() as db:
                            user = db.execute("SELECT * FROM users WHERE username = 'player'").fetchone()
                            village = game.get_village(db, user["id"])
                            return village, game.get_buildings(db, village["id"]), game.get_village_units(db, village["id"])
                    def advance(seconds):
                        while seconds > 0:
                            interval = min(seconds, 1800)
                            self.now += timedelta(seconds=interval)
                            seconds -= interval
                            self.assertEqual(client.get("/dashboard").status_code, 200)
                    def build(key, target):
                        for prerequisite, level in game.BUILDINGS[key].get("requires", {}).items():
                            build(prerequisite, level)
                        while snapshot()[1][key] < target:
                            village, buildings, _ = snapshot()
                            cost = game.upgrade_cost(key, buildings[key])
                            self.assertTrue(game.can_afford(village, cost), (key, dict(village), cost))
                            response = client.post(f"/upgrade/{key}")
                            self.assertEqual(response.status_code, 302)
                            advance(game.construction_duration_seconds(buildings[key]) + 1)
                    for index, step in enumerate(game.TUTORIAL_STEPS[:14]):
                        if "building" in step:
                            build(step["building"], step["level"])
                        if "unit" in step:
                            key, amount = step["unit"], step["amount"]
                            self.assertTrue(game.can_afford(snapshot()[0], game.unit_training_cost(key, amount)))
                            client.post(f"/train/{key}", data={"amount": amount})
                            advance(game.unit_training_duration(key, amount) + 1)
                            self.assertGreaterEqual(snapshot()[2].get(key, 0), amount)
                        client.post("/tutorial/claim")
                        with game.get_db() as db:
                            self.assertEqual(db.execute("SELECT step_index FROM user_tutorial_progress").fetchone()[0], index + 1)
                    self.assertLess((self.now - start).total_seconds(), 20 * 60)
                    # A raid reward cannot be claimed without a real raid report.
                    client.post("/tutorial/claim")
                    with game.get_db() as db:
                        self.assertEqual(db.execute("SELECT step_index FROM user_tutorial_progress").fetchone()[0], 14)
                        village = snapshot()[0]
                        tile = db.execute("SELECT * FROM map_tiles WHERE tile_type = 'iron_outcrop' ORDER BY ABS(x - ?) + ABS(y - ?) LIMIT 1", (village["map_x"], village["map_y"])).fetchone()
                    force = snapshot()[2]
                    client.post(f"/map/{tile['id']}/send", data={f"unit_{key}": amount for key, amount in force.items()})
                    duration = game.movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], force, mission="raid")
                    advance(duration + 1)
                    client.post("/tutorial/claim")
                    client.post("/tutorial/claim")  # reading is required for the next reward
                    with game.get_db() as db:
                        self.assertEqual(db.execute("SELECT step_index FROM user_tutorial_progress").fetchone()[0], 15)
                        report = db.execute("SELECT * FROM battle_reports WHERE report_type = 'raid'").fetchone()
                    self.assertEqual(client.get(f"/inbox/{report['id']}").status_code, 200)
                    client.post("/tutorial/claim")
                    advance(duration + 1)
                    with game.get_db() as db:
                        self.assertEqual(db.execute("SELECT step_index FROM user_tutorial_progress").fetchone()[0], 16)
                    # Fund the first capture army using real production, without resource boosts.
                    amount = 25 - snapshot()[2].get("knife_fighter", 0)
                    cost = game.unit_training_cost("knife_fighter", amount)
                    village, buildings, units = snapshot()
                    rates = game.resource_rates(buildings, faction, units)
                    wait = max([0] + [(needed - village[key]) / rates[key] * 3600 for key, needed in cost.items() if needed > village[key]])
                    advance(wait + 1)
                    self.assertTrue(game.can_afford(snapshot()[0], cost))
                    client.post("/train/knife_fighter", data={"amount": amount})
                    advance(game.unit_training_duration("knife_fighter", amount) + 1)
                    village, buildings, force = snapshot()
                    self.assertEqual(force["knife_fighter"], 25)
                    self.assertGreater(game.resource_rates(buildings, faction, force)["water"], 0)
                    with game.get_db() as db:
                        tile = db.execute("SELECT * FROM map_tiles WHERE tile_type = 'spice_bloom_large' AND controller_village_id IS NULL ORDER BY ABS(x - ?) + ABS(y - ?) LIMIT 1", (village["map_x"], village["map_y"])).fetchone()
                    client.post(f"/map/{tile['id']}/capture", data={f"unit_{key}": count for key, count in force.items()})
                    advance(game.movement_duration_seconds(village["map_x"], village["map_y"], tile["x"], tile["y"], force) + 1)
                    with game.get_db() as db:
                        captured = db.execute("SELECT * FROM map_tiles WHERE id = ?", (tile["id"],)).fetchone()
                        self.assertEqual(captured["controller_village_id"], village["id"])
                        self.assertGreater(sum(game.player_units_from_json(captured["garrison_json"]).values()), 0)
                    self.assertLess((self.now - start).total_seconds(), 3 * 3600)
                finally:
                    for db in connections:
                        db.close()

    def test_atreides_progression(self):
        self.run_progression("atreides")

    def test_harkonnen_progression(self):
        self.run_progression("harkonnen")

    def test_fremen_progression(self):
        self.run_progression("fremen")

    def test_projection_has_no_early_stall(self):
        for faction, *_ in game.FACTIONS:
            with self.subTest(faction=faction):
                result = Projection(faction).run()
                self.assertLess(result["minutes"]["Armed Patrols"], 20)
                self.assertLess(result["minutes"]["Bloom capture force"], 180)
                self.assertGreater(result["water_per_hour"], 0)


if __name__ == "__main__":
    unittest.main()
