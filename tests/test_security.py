import hashlib
import os
import re
import unittest
from datetime import timedelta
from unittest.mock import patch
from werkzeug.security import check_password_hash, generate_password_hash
import test_garrison_recall as fixtures
import app as game


class SecurityTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_logged_out_cookie_cannot_be_replayed(self):
        stolen = self.client.get_cookie("session").value
        with self.client.session_transaction() as state:
            token_hash = hashlib.sha256(state["login_token"].encode()).hexdigest()
        with game.get_db() as db:
            row = db.execute("SELECT token_hash FROM login_sessions").fetchone()
            self.assertEqual(row[0], token_hash)
        self.client.post("/logout")
        attacker = game.app.test_client()
        attacker.set_cookie("session", stolen)
        response = attacker.get("/account")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)
        self.assertEqual(self.client.get("/logout").status_code, 405)

    def test_modifying_forms_require_token_and_reject_open_redirect(self):
        with game.get_db() as db:
            before = game.get_village(db, 1)["iron"]
        self.assertEqual(self.client.post("/upgrade/iron_mine", data={}, csrf=False).status_code, 400)
        self.assertEqual(self.client.post("/logout", data={}, csrf=False).status_code, 400)
        self.assertEqual(self.client.post("/account", data={}, csrf=False).status_code, 400)
        with game.get_db() as db:
            self.assertEqual(game.get_village(db, 1)["iron"], before)
        response = self.client.post("/inbox/mark-read", data={"next": "//attacker.example"})
        self.assertEqual(response.location, "/inbox")
        self.assertFalse(game.safe_local_redirect("/\\attacker.example"))
        self.assertFalse(game.safe_local_redirect("/\nattacker.example"))
        self.assertTrue(game.safe_local_redirect("/map/3#scroll-25"))

    def test_password_change_requires_current_password_and_revokes_all_sessions(self):
        other = game.app.test_client()
        other.post("/login", data={"username": "commander", "password": "test-password-long"})
        new_password = "a-new-private-passphrase"
        data = dict(current_password="wrong-password", new_password=new_password, confirm_password=new_password)
        self.assertEqual(self.client.post("/account", data=data).status_code, 400)
        self.assertEqual(other.get("/account").status_code, 200)
        response = self.client.post("/account", data={**data, "current_password": "test-password-long"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)
        self.assertEqual(other.get("/account").status_code, 302)
        with game.get_db() as db:
            row = db.execute("SELECT password_hash FROM users WHERE username = 'commander'").fetchone()
            self.assertTrue(row[0].startswith("scrypt:32768:8:1$"))
            self.assertTrue(check_password_hash(row[0], new_password))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0], 0)
        self.assertEqual(self.client.post("/login", data={"username": "commander", "password": new_password}).status_code, 302)

    def test_concurrent_password_reset_cannot_issue_stale_login_session(self):
        self.client.post("/logout")
        original_check = game.check_password_hash
        def reset_during_check(stored_hash, submitted_password):
            valid = original_check(stored_hash, submitted_password)
            with game.get_db() as db:
                db.execute("UPDATE users SET password_hash = ? WHERE username = 'commander'", (game.hash_account_password("new-reset-passphrase"),))
                db.execute("DELETE FROM login_sessions")
                db.commit()
            return valid
        with patch.object(game, "check_password_hash", side_effect=reset_during_check):
            response = self.client.post("/login", data={"username": "commander", "password": "test-password-long"})
        self.assertEqual(response.status_code, 400)
        with game.get_db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM login_sessions").fetchone()[0], 0)
        with self.client.session_transaction() as state:
            self.assertNotIn("user_id", state)

    def test_idle_and_absolute_session_expiry(self):
        with patch.object(game, "utc_now", return_value=game.utc_now() + timedelta(minutes=61)):
            self.assertEqual(self.client.get("/account").status_code, 302)
        self.client.post("/login", data={"username": "commander", "password": "test-password-long"})
        now = game.utc_now().timestamp()
        with game.get_db() as db:
            db.execute("UPDATE login_sessions SET expires_at = ?, last_seen = ?", (now - 1, now))
            db.commit()
        self.assertEqual(self.client.get("/account").status_code, 302)

    def test_reset_revokes_sessions_and_legacy_hash_is_upgraded(self):
        self.client.post("/logout")
        with game.get_db() as db:
            db.execute("UPDATE users SET password_hash = ? WHERE username = 'commander'", (generate_password_hash("legacy-pass", method="pbkdf2:sha256:1000"),))
            db.commit()
        self.client.post("/login", data={"username": "commander", "password": "legacy-pass"})
        with game.get_db() as db:
            self.assertTrue(db.execute("SELECT password_hash FROM users WHERE username = 'commander'").fetchone()[0].startswith("scrypt:"))
        with patch.object(game, "DEV_TOOLS_ENABLED", True), patch.dict(os.environ, {"DEV_ADMIN_TOKEN": "local-admin-secret"}):
            self.client.post("/dev/reset-password", data={"username": "commander", "new_password": "replacement-passphrase", "admin_token": "local-admin-secret"})
        self.assertEqual(self.client.get("/account").status_code, 302)

    def test_private_account_data_is_not_exposed_and_inline_scripts_have_nonce(self):
        with game.app.test_request_context():
            with self.client.session_transaction() as state:
                game.session["user_id"] = state["user_id"]
            with game.get_db() as db:
                user = game.get_current_user(db)
                self.assertNotIn("password_hash", user.keys())
        response = self.client.get("/buildings/command_center")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        nonce = re.search(r"'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)
        text = response.get_data(as_text=True)
        self.assertIn(f'<script nonce="{nonce}">', text)
        self.assertNotIn("scrypt:", text)
        # Every rendered modifying form includes a token, including the header logout.
        for form in re.findall(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', text, re.S):
            self.assertIn('name="csrf_token"', form)

    def test_production_configuration_requires_https_secret_and_hosts(self):
        with patch.dict(os.environ, {"GAME_ENV": "production"}, clear=True):
            with self.assertRaises(RuntimeError):
                game.security_config()
            os.environ["SECRET_KEY"] = "a" * 64
            with self.assertRaises(RuntimeError):
                game.security_config()
            os.environ["TRUSTED_HOSTS"] = "game.example"
            config = game.security_config()
            os.environ["FLASK_DEBUG"] = "1"
            with self.assertRaises(RuntimeError):
                game.security_config()
            os.environ["FLASK_DEBUG"] = "0"
            self.assertTrue(config["SESSION_COOKIE_SECURE"])
            self.assertEqual(config["TRUSTED_HOSTS"], ["game.example"])
            os.environ["COOKIE_SECURE"] = "0"
            with self.assertRaises(RuntimeError):
                game.security_config()
        with patch.dict(game.app.config, {"PRODUCTION": True, "TRUSTED_HOSTS": ["localhost"], "SESSION_COOKIE_SECURE": True}):
            anonymous = game.app.test_client()
            self.assertEqual(anonymous.get("/login").status_code, 400)
            self.assertEqual(anonymous.get("/login", base_url="https://untrusted.example").status_code, 400)
            response = anonymous.get("/login", base_url="https://localhost")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Secure", response.headers["Set-Cookie"])
            self.assertIn("max-age", response.headers["Strict-Transport-Security"])
            with patch.object(game, "DEV_TOOLS_ENABLED", True):
                self.assertEqual(anonymous.get("/dev/reset-password", base_url="https://localhost").status_code, 404)

    def test_another_player_cannot_read_private_battle_report(self):
        with game.get_db() as db:
            tile = db.execute("SELECT * FROM map_tiles WHERE tile_type = 'iron_outcrop' LIMIT 1").fetchone()
        self.client.post(f"/map/{tile['id']}/send", data={"unit_knife_fighter": 3})
        with game.get_db() as db:
            movement = db.execute("SELECT * FROM troop_movements WHERE mission_type = 'raid'").fetchone()
        with patch.object(game, "utc_now", return_value=game.parse_time(movement["arrive_at"]) + timedelta(seconds=1)):
            self.client.get("/dashboard")
        with game.get_db() as db:
            report_id = db.execute("SELECT id FROM battle_reports LIMIT 1").fetchone()[0]
        other = game.app.test_client()
        other.post("/register", data={"username": "OtherPlayer", "password": "another-private-passphrase", "confirm_password": "another-private-passphrase", "faction": "atreides"})
        other.post("/login", data={"username": "OtherPlayer", "password": "another-private-passphrase"})
        response = other.get(f"/inbox/{report_id}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/inbox", response.location)


if __name__ == "__main__":
    unittest.main()
