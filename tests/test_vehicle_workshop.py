import unittest
from pathlib import Path

import test_garrison_recall as fixtures
import app as game


class VehicleWorkshopTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_base_has_twelve_integrated_plots_and_vehicle_art(self):
        self.assertEqual(len(game.BASE_VISUAL_SLOTS), 12)
        self.assertEqual(sum(slot["building_key"] is None for slot in game.BASE_VISUAL_SLOTS), 2)
        self.assertIn("vehicle_workshop", {slot["building_key"] for slot in game.BASE_VISUAL_SLOTS})
        root = Path(game.BASE_DIR) / "static" / "images" / "base"
        self.assertTrue((root / "base_courtyard_background_v4.png").is_file())
        self.assertTrue((root / "integrated" / "vehicle_workshop.png").is_file())

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"base_courtyard_background_v4.png", response.data)
        self.assertIn(b"Vehicle Workshop", response.data)
        self.assertEqual(response.data.count(b"building-spot base-plot"), 12)
        self.assertEqual(response.data.count(b"empty-plot"), 2)

    def test_workshop_unlocks_ground_vehicle_roster(self):
        buildings = {key: 0 for key in game.BUILDINGS}
        buildings["vehicle_workshop"] = 1
        self.assertTrue(game.is_unit_unlocked("desert_raider_bike", buildings))
        self.assertFalse(game.is_unit_unlocked("armored_troop_carrier", buildings))
        buildings["vehicle_workshop"] = 3
        self.assertTrue(game.is_unit_unlocked("armored_troop_carrier", buildings))
        self.assertEqual(game.deathstill_water_yield("armored_troop_carrier", 10), 0)

        with game.get_db() as db:
            db.execute(
                "UPDATE village_buildings SET level = 3 WHERE village_id = ? AND building_key = 'vehicle_workshop'",
                (self.village_id,),
            )
            db.commit()
        response = self.client.get("/buildings/vehicle_workshop")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Vehicle Workshop", response.data)
        self.assertIn(b"Atreides Desert Speeder", response.data)
        self.assertIn(b"Atreides Armored Carrier", response.data)
        self.assertIn(b"Transports up to 16 infantry", response.data)

    def test_armored_carrier_protects_infantry_during_first_round(self):
        research = game.ResearchLevels({}, "atreides")
        force = {"armored_troop_carrier": 1, "rpg_trooper": 12}
        guard = game.armored_deployment_guard(force, research)
        self.assertGreater(guard, 0)
        combat = game.resolve_combat(
            force,
            {"assault_ornithopter": 3},
            research,
            game.ResearchLevels({}, "harkonnen"),
            defender_is_player=True,
        )
        self.assertGreater(combat["attacker_deployment_absorbed"], 0)
        self.assertGreater(combat["rounds"][0]["attacker_deployment_absorbed"], 0)
        if len(combat["rounds"]) > 1:
            self.assertEqual(combat["rounds"][1]["attacker_deployment_absorbed"], 0)


if __name__ == "__main__":
    unittest.main()
