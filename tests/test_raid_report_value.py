import unittest
import app as game


class RaidReportValueTests(unittest.TestCase):
    def test_losses_are_priced_per_resource(self):
        report={f'loot_{key}':0 for key in ('iron','wood','water','spice')}
        report['loot_iron']=100
        rows={row['resource']:row for row in game.raid_resource_balance(report,{'knife_fighter':2})}
        costs=game.unit_training_cost('knife_fighter',2)
        self.assertEqual(rows['iron']['net'],100-costs['iron'])
        self.assertEqual(rows['water']['net'],-costs['water'])
        self.assertEqual(rows['melange']['net'],0)

    def test_lossless_raid_preserves_loot_value(self):
        report={f'loot_{key}':50 for key in ('iron','wood','water','spice')}
        rows=game.raid_resource_balance(report,{})
        self.assertTrue(all(row['replacement']==0 for row in rows))
        self.assertEqual(sum(row['net'] for row in rows),200)
