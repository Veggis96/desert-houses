"""Consistent, encrypted game snapshots; restore only into a NEW directory."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from cryptography.fernet import Fernet


def private_write(path, data):
    with path.open('xb') as handle:
        handle.write(data)
    path.chmod(0o600)


def snapshot(config_path, key_path=None):
    os.umask(0o077)
    config_path = Path(config_path).resolve()
    settings = json.loads(config_path.read_text())
    database = Path(settings['GAME_DB_PATH']).resolve()
    if not database.is_file():
        raise ValueError('Configured database is missing. Refusing to create a new world.')
    key_path = Path(key_path or config_path.parent / 'backup.key').resolve()
    if not key_path.exists():
        if any((config_path.parent / 'backups').glob('*.fernet')):
            raise ValueError('Backup key is missing but backups exist. Recover the original key; refusing to replace it.')
        private_write(key_path, Fernet.generate_key())
    key_path.chmod(0o600)
    cipher = Fernet(key_path.read_bytes())
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(':memory:')) as copy:
        if source.execute('PRAGMA page_count').fetchone()[0] * source.execute('PRAGMA page_size').fetchone()[0] > 64 * 1024 * 1024:
            raise ValueError('Database exceeds the 64 MiB backup limit.')
        source.backup(copy)
        if copy.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed.')
        copy.execute('PRAGMA journal_mode=DELETE')
        raw = copy.serialize()
        # The online backup contains all committed pages, including WAL data.
        # Normalize SQLite's read/write format flags for a standalone snapshot;
        # in-memory journal_mode does not rewrite an inherited WAL header.
        if raw[18:20] == bytes([2, 2]):
            raw = raw[:18] + bytes([1, 1]) + raw[20:]
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError('Snapshot exceeds the 64 MiB in-memory backup limit; use a larger-host backup solution.')
    payload = json.dumps({'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                          'config': settings, 'database': base64.b64encode(raw).decode('ascii')}).encode()
    folder = config_path.parent / 'backups'
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    destination = folder / (datetime.now(timezone.utc).strftime('game-%Y%m%dT%H%M%S-') + secrets.token_hex(4) + '.fernet')
    private_write(destination, cipher.encrypt(payload))
    # Verify encryption and SQLite content before declaring the backup successful.
    verified = decode_snapshot(destination, key_path)
    assert verified['database'] == raw
    return destination, key_path


def decode_snapshot(path, key_path):
    payload = json.loads(Fernet(Path(key_path).read_bytes()).decrypt(Path(path).read_bytes()))
    if payload.get('version') != 1:
        raise ValueError('Unsupported backup format.')
    raw = base64.b64decode(payload['database'], validate=True)
    with closing(sqlite3.connect(':memory:')) as check:
        check.deserialize(raw)
        if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Backup database integrity check failed.')
    return {'config': payload['config'], 'database': raw}


def restore(path, key_path, destination):
    os.umask(0o077)
    payload = decode_snapshot(path, key_path)
    destination = Path(destination).resolve()
    destination.mkdir(mode=0o700, exist_ok=False)
    database = destination / 'game.db'
    with closing(sqlite3.connect(':memory:')) as source, closing(sqlite3.connect(database)) as target:
        source.deserialize(payload['database'])
        source.backup(target)
    database.chmod(0o600)
    settings = payload['config']
    settings['GAME_DB_PATH'] = str(database)
    private_write(destination / 'config.json', json.dumps(settings).encode())
    return database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--config', default=str(Path.home() / '.desert-houses' / 'config.json'))
    recovery = commands.add_parser('restore')
    recovery.add_argument('backup')
    recovery.add_argument('--key', required=True)
    recovery.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'backup':
        destination, key = snapshot(args.config)
        print('Verified encrypted backup:', destination)
        print('Keep a separate, private copy of the encryption key:', key)
    else:
        print('Restored and checked database:', restore(args.backup, args.key, args.output))


if __name__ == '__main__':
    main()
