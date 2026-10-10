import unittest
import test_garrison_recall as fixtures
from test_garrison_recall import game


class ResourceEstimateTests(unittest.TestCase):
    def test_slowest_resource_controls_wait(self):
        overview = {"capacity": 1000, "amounts": {"iron": 0, "wood": 50},
                    "rates": {"iron": 100, "wood": 25}}
        result = game.affordability_estimate({"iron": 100, "wood": 100}, overview)
        self.assertIn("2h", result)

    def test_storage_and_nonproducing_resources(self):
        overview = {"capacity": 1000, "amounts": {"water": 10}, "rates": {"water": -2}}
        self.assertIn("Increase Water", game.affordability_estimate({"water": 20}, overview))
        self.assertIn("storage", game.affordability_estimate({"water": 1001}, overview))
        self.assertIsNone(game.affordability_estimate({"water": 5}, overview))
        self.assertIsNone(game.affordability_estimate({"water": 5}, None))


class ResourceEstimatePageTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_wait_and_deficit_visible(self):
        with game.get_db() as db:
            db.execute("UPDATE villages SET iron=0, wood=0, water=500 WHERE id=?", (self.village_id,))
            game.add_village_units(db, self.village_id, "knife_fighter", 1000)
            db.commit()
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("stockpile-warning", html)
        self.assertIn("Empty in", html)
        self.assertIn("Resources ready in about", html)
        self.assertEqual(self.client.get("/buildings/research_center").status_code, 200)
