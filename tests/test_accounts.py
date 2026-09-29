import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from werkzeug.security import check_password_hash
import test_garrison_recall as fixtures
import app as game


class AccountTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def signup(self, **changes):
        self.client.post("/logout")
        self.client.get("/register")
        with self.client.session_transaction() as state:
            token = state["auth_csrf_token"]
        data = dict(username="NewPlayer", password="long-test-passphrase", confirm_password="long-test-passphrase", faction="fremen", csrf_token=token)
        data.update(changes)
        return self.client.post("/register", data=data)

    def test_successful_signup_hashes_password_and_places_one_village(self):
        with game.get_db() as db:
            db.execute("UPDATE map_tiles SET controller_village_id = ?, garrison_json = ? WHERE x = -1 AND y = -1", (self.village_id, game.player_units_json({"knife_fighter": 9})))
            db.commit()
        response = self.signup()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)
        with game.get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username = 'NewPlayer'").fetchone()
            self.assertTrue(check_password_hash(user["password_hash"], "long-test-passphrase"))
            self.assertNotEqual(user["password_hash"], "long-test-passphrase")
            villages = db.execute("SELECT * FROM villages WHERE user_id = ?", (user["id"],)).fetchall()
            self.assertEqual(len(villages), 1)
            self.assertIsNotNone(villages[0]["map_x"])
            tile = db.execute("SELECT * FROM map_tiles WHERE village_id = ?", (villages[0]["id"],)).fetchone()
            self.assertIsNotNone(tile)
            self.assertEqual(db.execute("SELECT controller_village_id FROM map_tiles WHERE x = -1 AND y = -1").fetchone()[0], self.village_id)

    def test_validation_and_confirmation_do_not_create_accounts(self):
        cases = ({"username": "ab"}, {"username": "a" * 25}, {"username": "bad name"},
                 {"password": "short", "confirm_password": "short"}, {"password": "a" * 129},
                 {"password": " " * 10}, {"confirm_password": "different-password"}, {"faction": "unknown"})
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.signup(**changes)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(b'value="long-test-passphrase"', response.data)
        with game.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)

    def test_duplicate_case_and_case_insensitive_login(self):
        self.assertEqual(self.signup(username="COMMANDER").status_code, 409)
        self.client.get("/login")
        with self.client.session_transaction() as state:
            token = state["auth_csrf_token"]
        response = self.client.post("/login", data={"username": "COMMANDER", "password": "test-password-long", "csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)

    def test_missing_and_malformed_form_tokens_are_rejected(self):
        self.client.post("/logout")
        self.assertEqual(self.client.post("/register", data={}, csrf=False).status_code, 400)
        self.client.get("/login")
        self.assertEqual(self.client.post("/login", data={"csrf_token": "unicode-ø"}).status_code, 400)
        self.assertEqual(self.client.post("/login", data={"csrf_token": "wrong"}).status_code, 400)

    def test_optional_invite_code_is_required_and_not_rendered(self):
        with patch.dict(os.environ, {"REGISTRATION_INVITE_CODE": "private-invite-ø"}):
            response = self.signup()
            self.assertEqual(response.status_code, 400)
            self.assertIn(b'name="invite_code"', response.data)
            self.assertNotIn("private-invite-ø".encode(), response.data)
            self.assertEqual(self.signup(invite_code="private-invite-ø").status_code, 302)

    def test_world_full_rolls_back_account_and_village(self):
        with patch.object(game, "ensure_village_map_position", side_effect=RuntimeError("No free map position available.")):
            self.assertEqual(self.signup().status_code, 409)
        with game.get_db() as db:
            self.assertIsNone(db.execute("SELECT id FROM users WHERE username = 'NewPlayer'").fetchone())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM villages").fetchone()[0], 1)

    def test_login_limit_expires(self):
        self.client.post("/logout")
        self.client.get("/login")
        with self.client.session_transaction() as state:
            token = state["auth_csrf_token"]
        with game.get_db() as db:
            db.execute("DELETE FROM auth_attempts")
            db.commit()
        data = dict(username="commander", password="wrong-password", csrf_token=token)
        for _ in range(8):
            self.assertEqual(self.client.post("/login", data=data).status_code, 400)
        self.assertEqual(self.client.post("/login", data=data).status_code, 429)
        with patch.object(game, "utc_now", return_value=game.utc_now() + timedelta(minutes=16)):
            self.assertEqual(self.client.post("/login", data={**data, "password": "test-password-long"}).status_code, 302)

    def test_registration_limit_is_scoped_to_client_address(self):
        with game.get_db() as db:
            db.execute("DELETE FROM auth_attempts")
            db.commit()
        for _ in range(20):
            self.assertEqual(self.signup(username="x").status_code, 400)
        self.assertEqual(self.signup(username="x").status_code, 429)
        with self.client.session_transaction() as state:
            token = state["auth_csrf_token"]
        response = self.client.post("/register", data={"username": "x", "csrf_token": token}, environ_overrides={"REMOTE_ADDR": "192.0.2.5"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
