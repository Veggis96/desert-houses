import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as game


class GarrisonRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(game, "DB_PATH", str(Path(self.temp.name) / "game.db"))
        self.db_patch.start()
        self.connections = []
        original_get_db = game.get_db
        def tracked_get_db():
            db = original_get_db()
            self.connections.append(db)
            return db
        self.connection_patch = patch.object(game, "get_db", side_effect=tracked_get_db)
        self.connection_patch.start()
        game.app.config["TESTING"] = True
        game.init_db()
        self.client = game.app.test_client()
        self.client.get("/register")
        with self.client.session_transaction() as account_session:
            signup_token = account_session["auth_csrf_token"]
        self.client.post("/register", data={"username": "commander", "password": "test-password", "confirm_password": "test-password", "csrf_token": signup_token, "faction": "atreides"})
        self.client.post("/login", data={"username": "commander", "password": "test-password", "csrf_token": signup_token})
        with game.get_db() as db:
            user = db.execute("SELECT id FROM users WHERE username = 'commander'").fetchone()
            village = game.get_village(db, user["id"])
            game.ensure_village_map_position(db, village)
            self.village_id = village["id"]
            tile = db.execute("SELECT * FROM map_tiles WHERE village_id IS NULL LIMIT 1").fetchone()
            self.tile_id = tile["id"]
            db.execute("UPDATE map_tiles SET tile_type = 'spice_bloom_large', controller_village_id = ?, garrison_json = ? WHERE id = ?",
                       (self.village_id, game.player_units_json({"knife_fighter": 10, "carryall": 2}), self.tile_id))
            game.add_village_units(db, self.village_id, "knife_fighter", 3)
            db.commit()

    def tearDown(self):
        for db in self.connections:
            db.close()
        self.connection_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def recall(self, **data):
        return self.client.post(f"/map/{self.tile_id}/recall", data=data)

    def state(self):
        with game.get_db() as db:
            tile = db.execute("SELECT * FROM map_tiles WHERE id = ?", (self.tile_id,)).fetchone()
            movements = db.execute("SELECT * FROM troop_movements WHERE mission_type = 'recall'").fetchall()
            return game.player_units_from_json(tile["garrison_json"]), movements, game.get_village_units(db, self.village_id)

    def test_partial_recall_returns_once_even_if_control_changes(self):
        self.assertEqual(self.recall(unit_knife_fighter="4").status_code, 302)
        garrison, movements, home = self.state()
        self.assertEqual(garrison, {"knife_fighter": 6, "carryall": 2})
        self.assertEqual(home["knife_fighter"], 3)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0]["status"], "returning")
        self.assertGreater(game.parse_time(movements[0]["return_at"]), game.parse_time(movements[0]["started_at"]))
        with game.get_db() as db:
            db.execute("UPDATE map_tiles SET controller_village_id = NULL WHERE id = ?", (self.tile_id,))
            with patch.object(game, "utc_now", return_value=game.parse_time(movements[0]["return_at"]) + timedelta(seconds=1)):
                game.process_troop_movements(db, self.village_id)
                game.process_troop_movements(db, self.village_id)
            db.commit()
        self.assertEqual(self.state()[2]["knife_fighter"], 7)
        self.assertEqual(self.state()[1][0]["status"], "complete")

    def test_full_recall_requires_confirmation_and_cannot_duplicate(self):
        self.recall(recall_all="1")
        self.assertEqual(len(self.state()[1]), 0)
        self.recall(recall_all="1", confirm_undefended="1")
        self.assertEqual(self.state()[0], {})
        self.assertEqual(len(self.state()[1]), 1)
        self.recall(recall_all="1", confirm_undefended="1")
        self.assertEqual(len(self.state()[1]), 1)
        with game.get_db() as db:
            self.assertEqual(db.execute("SELECT controller_village_id FROM map_tiles WHERE id = ?", (self.tile_id,)).fetchone()[0], self.village_id)

    def test_invalid_quantities_leave_garrison_unchanged(self):
        for value in ("-1", "11", "abc", "1.5", "0"):
            with self.subTest(value=value):
                self.recall(unit_knife_fighter=value)
                self.assertEqual(self.state()[0]["knife_fighter"], 10)
                self.assertEqual(len(self.state()[1]), 0)

    def test_ownership_and_login_required(self):
        with game.get_db() as db:
            db.execute("UPDATE map_tiles SET controller_village_id = NULL WHERE id = ?", (self.tile_id,))
            db.commit()
        self.recall(recall_all="1", confirm_undefended="1")
        self.assertEqual(len(self.state()[1]), 0)
        self.client.get("/logout")
        response = self.recall(recall_all="1", confirm_undefended="1")
        self.assertIn("/login", response.location)

    def test_overview_counts_returning_survivors_and_renders_forms(self):
        self.recall(unit_knife_fighter="4")
        with game.get_db() as db:
            # Returning armies count survivors, not original dispatched quantities.
            db.execute("UPDATE troop_movements SET units_json = ?", (game.player_units_json({"knife_fighter": 9}),))
            db.commit()
        with patch.object(game, "render_template", wraps=game.render_template) as render:
            response = self.client.get("/buildings/command_center")
        self.assertEqual(response.status_code, 200)
        overview = render.call_args.kwargs["troop_overview"]
        fighter = next(row for row in overview if row["name"] == "Atreides Bladesman")
        self.assertEqual((fighter["home"], fighter["travelling"], fighter["stationed"], fighter["total"]), (3, 4, 6, 13))
        self.assertIn(b"Recall selected", response.data)
        self.assertIn(b"Recall all", response.data)
        self.assertIn(b"from Large Spice Bloom", response.data)
        self.assertEqual(self.client.get(f"/map/{self.tile_id}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
