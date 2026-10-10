import unittest
import test_garrison_recall as fixtures
from test_garrison_recall import game


class InboxPageTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def seed_reports(self):
        self.client.post(f"/map/{self.tile_id}/recall", data={"unit_knife_fighter": "1"})
        with game.get_db() as db:
            user_id = db.execute("SELECT id FROM users WHERE username='commander'").fetchone()[0]
            movement_id = db.execute("SELECT id FROM troop_movements LIMIT 1").fetchone()[0]
            for index in range(105):
                db.execute(
                    """INSERT INTO battle_reports
                    (user_id, village_id, movement_id, title, report_type, created_at,
                    target_x, target_y, target_name, outcome, units_sent_json,
                    units_survived_json, units_lost_json)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 1, 'Camp', 'Victory', '{}', '{}', '{}')""",
                    (user_id, self.village_id, movement_id, f"Report-{index:03d}",
                     "scout" if index % 2 else "raid", "2026-10-10T10:00:00"),
                )
            db.commit()

    def test_full_history_and_stable_order(self):
        self.seed_reports()
        html = self.client.get("/inbox").get_data(as_text=True)
        self.assertIn("Report-104", html)
        self.assertIn("Report-085", html)
        self.assertNotIn("Report-084", html)
        self.assertIn("Page 1 of 6", html)
        oldest = self.client.get("/inbox?page=6").get_data(as_text=True)
        self.assertIn("Report-000", oldest)
        self.assertNotIn("Report-005", oldest)

    def test_filters_and_invalid_pages(self):
        self.seed_reports()
        html = self.client.get("/inbox?filter=scout&page=999").get_data(as_text=True)
        self.assertIn("Page 3 of 3", html)
        self.assertIn("Report-001", html)
        self.assertNotIn("Report-000", html)
        self.assertIn("filter=scout", html)
        for page in ("-2", "bad", "0"):
            html = self.client.get(f"/inbox?page={page}").get_data(as_text=True)
            self.assertIn("Page 1 of 6", html)
