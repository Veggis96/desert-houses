import unittest
import test_garrison_recall as fixtures
from test_garrison_recall import game


class ConstructionPreviewTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_preview_includes_queue_wait_and_requirement_progress(self):
        self.client.post('/upgrade/iron_mine')
        with game.get_db() as db:
            village = db.execute('SELECT * FROM villages WHERE id=?', (self.village_id,)).fetchone()
            levels = game.get_buildings(db, self.village_id)
            queue = game.get_construction_queue(db, self.village_id)
            cards = {card['key']: card for card in game.build_building_cards(village, levels, queue, {}, 'atreides')}
            card = cards['barracks']
            self.assertGreater(card['queue_wait_seconds'], 0)
            self.assertGreater(game.parse_time(card['completion_at']), game.parse_time(queue[0]['finish_at']))
            requirement = next(item for item in card['requirements_progress'] if item['name'] == 'Command Center')
            self.assertEqual((requirement['current'], requirement['required'], requirement['met']), (1, 2, False))
            db.execute("UPDATE village_buildings SET level=2 WHERE village_id=? AND building_key='command_center'", (self.village_id,))
            db.commit()
        response = self.client.get('/dashboard')
        self.assertIn(b'Ready: Command Center level 2 (current 2)', response.data)
        self.assertIn(b'Estimated finish if queued now', response.data)
        self.assertIn(b'waiting for queued construction', response.data)
