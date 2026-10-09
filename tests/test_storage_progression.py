import unittest
import app as game


class StorageProgressionTests(unittest.TestCase):
    def test_warehouse_can_always_fund_its_next_tier(self):
        for level in range(101):
            capacity = game.storage_capacity({'warehouse':level})
            self.assertGreaterEqual(capacity,max(game.upgrade_cost('warehouse',level).values()),level)
            self.assertGreater(game.storage_capacity({'warehouse':level+1}),capacity)
        self.assertEqual(game.storage_capacity({'warehouse':0}),1000)
        self.assertEqual(game.storage_capacity({'warehouse':9}),5500)
        self.assertEqual(game.storage_capacity({'warehouse':10}),8500)

    def test_all_building_and_research_costs_have_reachable_storage(self):
        costs=[game.upgrade_cost(key,level) for key in game.BUILDINGS for level in range(31)]
        costs += [game.research_cost(key,level) for key,definition in game.RESEARCH_TYPES.items() for level in range(definition['max_level'])]
        for cost in costs:
            level=game.required_warehouse_level(cost)
            self.assertGreaterEqual(game.storage_capacity({'warehouse':level}),max(cost.values(),default=0))
            if level:
                self.assertLess(game.storage_capacity({'warehouse':level-1}),max(cost.values()))
