import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from cryptography.fernet import Fernet, InvalidToken

spec = importlib.util.spec_from_file_location('game_backup', Path(__file__).resolve().parents[1] / 'scripts/game_backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class PlayerBackupTests(unittest.TestCase):
    def test_wal_snapshot_includes_committed_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'game.db'
            source=sqlite3.connect(path)
            try:
                source.execute('PRAGMA journal_mode=WAL')
                source.execute('CREATE TABLE progress(value TEXT)')
                source.execute("INSERT INTO progress VALUES ('committed-in-wal')")
                source.commit()
                config=root/'config.json'
                config.write_text(json.dumps({'GAME_DB_PATH':str(path),'SECRET_KEY':'test-only'}))
                archive,key=backup.snapshot(config)
                restored=backup.restore(archive,key,root/'restored')
                target=sqlite3.connect(restored)
                try: self.assertEqual(target.execute('SELECT value FROM progress').fetchone()[0],'committed-in-wal')
                finally: target.close()
            finally: source.close()

    def test_encrypted_round_trip_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); dbpath=root/'game.db'
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE progress (username TEXT, level INTEGER, iron REAL)')
                db.execute("INSERT INTO progress VALUES ('playtester',10,1234.5)")
            db.close()
            config=root/'config.json'
            config.write_text(json.dumps({'GAME_DB_PATH':str(dbpath),'SECRET_KEY':'private-signing-secret'}))
            archive,key=backup.snapshot(config)
            self.assertNotIn(b'playtester',archive.read_bytes())
            self.assertNotIn(b'private-signing-secret',archive.read_bytes())
            restored=backup.restore(archive,key,root/'recovered')
            db=sqlite3.connect(restored)
            self.assertEqual(db.execute('SELECT * FROM progress').fetchone(),('playtester',10,1234.5))
            db.close()
            self.assertEqual(json.loads((root/'recovered/config.json').read_text())['SECRET_KEY'],'private-signing-secret')
            with self.assertRaises(FileExistsError): backup.restore(archive,key,root/'recovered')
            wrong=root/'wrong.key'; wrong.write_bytes(Fernet.generate_key())
            with self.assertRaises(InvalidToken): backup.restore(archive,wrong,root/'invalid')
            self.assertFalse((root/'invalid').exists())
            dbpath.unlink()
            with self.assertRaises(ValueError): backup.snapshot(config)
            self.assertFalse(dbpath.exists())
import test_garrison_recall as fixtures
from test_garrison_recall import game


class LiveSaveProtectionTests(unittest.TestCase):
    setUp=fixtures.GarrisonRecallTests.setUp
    tearDown=fixtures.GarrisonRecallTests.tearDown

    def test_reinitialization_and_backup_preserve_player_tables(self):
        self.client.post('/upgrade/iron_mine')
        self.client.get('/dashboard')
        tables=('users','villages','village_buildings','village_units','village_research','construction_queue','unit_training_queue','research_queue','user_tutorial_progress')
        with game.get_db() as db:
            before={table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in tables}
        game.init_db()
        with game.get_db() as db:
            after={table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in tables}
        self.assertEqual(before,after)
        root=Path(self.temp.name)
        config=root/'config.json'
        config.write_text(json.dumps({'GAME_DB_PATH':game.DB_PATH,'SECRET_KEY':'test-only-secret'}))
        archive,key=backup.snapshot(config)
        restored=backup.restore(archive,key,root/'restored-save')
        db=sqlite3.connect(restored)
        try:
            recovered={table:db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall() for table in tables}
            self.assertEqual(before,recovered)
        finally: db.close()
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
update_spec=importlib.util.spec_from_file_location('safe_update', Path(__file__).resolve().parents[1] / 'scripts/safe-update.py')
updater=importlib.util.module_from_spec(update_spec)
update_spec.loader.exec_module(updater)


class SafeUpdateTests(unittest.TestCase):
    def test_backup_failure_prevents_git_pull_or_migration(self):
        with patch.object(updater,'snapshot',side_effect=ValueError('Backup failed')), patch.object(updater.subprocess,'run') as command:
            with self.assertRaises(ValueError): updater.main()
            command.assert_not_called()
