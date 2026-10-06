import random
import unittest
import test_garrison_recall as fixtures
from test_garrison_recall import game


class RaidBalanceTests(unittest.TestCase):
    setUp = fixtures.GarrisonRecallTests.setUp
    tearDown = fixtures.GarrisonRecallTests.tearDown

    def test_resource_sites_and_existing_map_caps(self):
        for kind in ('iron_outcrop', 'scrap_field', 'water_oasis'):
            stock = game.map_tile_resources(kind, 5, random.Random(1))
            self.assertLessEqual(sum(stock.values()), 65)
        with game.get_db() as db:
            db.execute("UPDATE map_tiles SET tile_type='iron_outcrop', resource_iron=500, resource_wood=0, resource_water=0, resource_spice=0 WHERE id=?", (self.tile_id,))
            game.regenerate_map_tiles(db)
            tile = db.execute('SELECT * FROM map_tiles WHERE id=?', (self.tile_id,)).fetchone()
            cap = game.map_tile_resources('iron_outcrop', max(abs(tile['x']), abs(tile['y']),1), random.Random(tile['x']*1009+tile['y']*9176))
            self.assertEqual(tile['resource_iron'], cap['iron'])
            db.execute('UPDATE map_tiles SET resource_iron=0 WHERE id=?', (self.tile_id,))
            game.regenerate_map_tiles(db)
            self.assertEqual(db.execute('SELECT resource_iron FROM map_tiles WHERE id=?',(self.tile_id,)).fetchone()[0], 0)

    def test_raid_dispatch_and_preview_use_slower_travel(self):
        force={'knife_fighter': 3}
        with game.get_db() as db:
            db.execute("UPDATE map_tiles SET tile_type='iron_outcrop', controller_village_id=NULL, npc_strength=0, npc_units_json='{}' WHERE id=?", (self.tile_id,))
            tile=db.execute('SELECT * FROM map_tiles WHERE id=?',(self.tile_id,)).fetchone()
            village=db.execute('SELECT * FROM villages WHERE id=?',(self.village_id,)).fetchone()
            db.commit()
        args=(village['map_x'],village['map_y'],tile['x'],tile['y'],force,'atreides')
        duration=game.movement_duration_seconds(*args,mission='raid')
        self.assertEqual(duration,max(60,game.movement_duration_seconds(*args)*8))
        preview=self.client.get(f'/map/{self.tile_id}/plan',query_string={'mission':'raid','unit_knife_fighter':3}).get_json()
        self.assertEqual(preview['travel_seconds'],duration)
        self.client.post(f'/map/{self.tile_id}/send',data={'unit_knife_fighter':3})
        with game.get_db() as db:
            move=db.execute("SELECT * FROM troop_movements WHERE mission_type='raid'").fetchone()
            self.assertEqual((game.parse_time(move['arrive_at'])-game.parse_time(move['started_at'])).total_seconds(),duration)
