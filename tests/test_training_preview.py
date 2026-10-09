import unittest
import test_garrison_recall as fixtures
from test_garrison_recall import game


class TrainingPreviewTests(unittest.TestCase):
    setUp=fixtures.GarrisonRecallTests.setUp
    tearDown=fixtures.GarrisonRecallTests.tearDown

    def test_affordability_and_pending_upkeep(self):
        with game.get_db() as db:
            village=game.get_village(db,db.execute("SELECT id FROM users WHERE username='commander'").fetchone()[0])
            levels=game.get_buildings(db,self.village_id)
            units=game.get_village_units(db,self.village_id)
            queued=[{'unit_key':'knife_fighter','amount':20}]
            cards=game.build_unit_cards(village,levels,{},units,queued,'fremen','military')
            card=next(card for card in cards if card['key']=='knife_fighter')
            expected=min([999]+[int(village[k]//v) for k,v in card['cost'].items() if v])
            self.assertEqual(card['max_affordable'],expected)
            projected=dict(units); projected['knife_fighter']=projected.get('knife_fighter',0)+20
            self.assertAlmostEqual(card['water_after_queue'],game.resource_rates(levels,'fremen',projected,{})['water'])
        html=self.client.get('/buildings/barracks').get_data(as_text=True)
        self.assertIn('data-training-preview',html)
        self.assertIn('Use max',html)
