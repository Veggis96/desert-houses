"""Read-only raid balance diagnostics. Never imports the game or reads auth secrets."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3


def inspect(db_path, username, limit=20):
    path = Path(db_path).expanduser().resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        users = db.execute('SELECT id, username FROM users WHERE lower(username)=lower(?)', (username,)).fetchall()
        if len(users) != 1:
            raise ValueError('Username not found or ambiguous.')
        user = users[0]
        village = db.execute('SELECT id, map_x, map_y FROM villages WHERE user_id=? LIMIT 1', (user['id'],)).fetchone()
        if not village:
            raise ValueError('Player has no village.')
        rows = db.execute('''SELECT r.created_at, r.target_x, r.target_y, r.target_name, r.outcome,
            r.units_sent_json, r.units_lost_json, r.enemy_before, r.enemy_after,
            r.loot_iron, r.loot_wood, r.loot_water, r.loot_spice,
            m.started_at, m.arrive_at, m.return_at, m.status
            FROM battle_reports r LEFT JOIN troop_movements m ON m.id=r.movement_id
            WHERE r.user_id=? AND r.report_type='raid' ORDER BY r.id DESC LIMIT ?''', (user['id'], limit)).fetchall()
        raids = []
        totals = Counter()
        targets = Counter()
        for row in rows:
            loot = {key: row['loot_' + key] for key in ('iron', 'wood', 'water', 'spice')}
            totals.update(loot)
            targets[(row['target_x'], row['target_y'])] += 1
            duration = None
            if row['started_at'] and row['return_at']:
                duration = round((datetime.fromisoformat(row['return_at']) - datetime.fromisoformat(row['started_at'])).total_seconds())
            raids.append(dict(time_utc=row['created_at'], target=[row['target_x'], row['target_y']],
                type=row['target_name'], outcome=row['outcome'], loot=loot,
                sent=json.loads(row['units_sent_json']), lost=json.loads(row['units_lost_json']),
                defense_before=row['enemy_before'], defense_after=row['enemy_after'],
                scheduled_round_trip_seconds=duration, movement_status=row['status']))
        camps = db.execute('''SELECT x,y,npc_strength,resource_iron,resource_wood,resource_water,resource_spice,
            last_regenerated_at FROM map_tiles WHERE tile_type='npc_camp'
            ORDER BY ((x-?)*(x-?)+(y-?)*(y-?)), id LIMIT 12''',
            (village['map_x'], village['map_x'], village['map_y'], village['map_y'])).fetchall()
        return dict(username=user['username'], village=[village['map_x'], village['map_y']],
            note='Latest raids only. Loot is recorded at battle, and may still be travelling. Camps show stored state; no regeneration was run.',
            sampled_raids=len(raids), sample_loot_totals=dict(totals),
            repeated_targets=[dict(target=list(target), raids=count) for target,count in targets.items() if count>1],
            raids=raids, nearby_camps=[dict(camp) for camp in camps])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username')
    parser.add_argument('--db', default=str(Path.home() / '.desert-houses' / 'game.db'))
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit must be between 1 and 100')
    try:
        result = inspect(args.db, args.username, args.limit)
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, 'Diagnostic failed: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
