import unittest
from datetime import timedelta
import test_garrison_recall as fixtures
from test_garrison_recall import game


class UpgradeFeedbackTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_completed_upgrade_announced_once_and_reward_ready(self):
        response = self.client.get('/dashboard')
        self.assertIn(b'Current level 1 / 2 required', response.data)
        self.client.post('/upgrade/iron_mine')
        with game.get_db() as db:
            db.execute('UPDATE construction_queue SET finish_at = ? WHERE village_id = ?',
                       ((game.utc_now() - timedelta(seconds=1)).isoformat(), self.village_id))
            db.commit()
        response = self.client.get('/dashboard')
        self.assertIn(b'Iron Mine upgraded to level 2.', response.data)
        self.assertIn(b'Reward available', response.data)
        self.assertIn(b'reward-ready', response.data)
        self.assertNotIn(b'Iron Mine upgraded to level 2.', self.client.get('/dashboard').data)
        self.client.post('/tutorial/claim')
        response = self.client.get('/dashboard')
        self.assertIn(b'Gather Timber', response.data)
        self.assertNotIn(b'Reward available', response.data)

    def test_queued_upgrade_does_not_complete_objective_early(self):
        self.client.post('/upgrade/iron_mine')
        response = self.client.get('/dashboard')
        self.assertNotIn(b'Iron Mine upgraded to level 2.', response.data)
        self.assertNotIn(b'Reward available', response.data)
        self.client.post('/tutorial/claim')
        with game.get_db() as db:
            self.assertEqual(db.execute('SELECT step_index FROM user_tutorial_progress').fetchone()[0], 0)
