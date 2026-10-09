# Protecting player progress

The live database and signing configuration stay in `/home/deserthouses/.desert-houses`, outside Git. Updates must preserve this directory. Existing player levels/resources are never reset by the update tool. Database initialization uses additive schema changes; future migrations must continue to preserve existing rows and pass save-preservation tests.

## First installation of backup tools

In a PythonAnywhere Bash console:

```bash
cd ~/desert-houses
git pull --ff-only
workon desert-houses
pip install -r requirements.txt -r requirements-backup.txt
python scripts/game_backup.py backup
```

This makes a consistent SQLite snapshot using the online backup API, checks its integrity, and encrypts both the database and signing config with Fernet. The encryption key is generated once in `~/.desert-houses/backup.key`. It is never printed. Backups are timestamped files in `~/.desert-houses/backups` with private permissions; no plaintext temporary snapshot is written to disk. SQLite serialization and Fernet require memory; this tool supports databases up to 64 MiB. Backups are not deleted automatically: check your 512 MiB hosting disk quota regularly and remove old copies only after keeping verified separate copies.

Download the `.fernet` backup through PythonAnywhere's Files tab. Also keep a private copy of `backup.key` in a secure separate location. Anyone with both can read the player data. Losing the only key makes encrypted backups unrecoverable. Copies only on PythonAnywhere do not protect against loss of that hosting account. Never paste a key, signing config, or decrypted database into chat or GitHub.

## Every future update

```bash
cd ~/desert-houses
workon desert-houses
python scripts/safe-update.py
```

The command must successfully create and verify an encrypted backup BEFORE pulling code. It then installs dependencies and runs schema initialization, which also snapshots the existing database before migration. Click Reload in the Web tab only after the command succeeds. Keep the app running for routine additive updates; updates requiring an exclusive migration need planned maintenance and a separate migration design.

Do not delete the database, signing config or encryption key. The WSGI loader now refuses a missing database, rather than serving a newly created empty world. The setup script also refuses an existing config whose database has disappeared. Errors stop the update; do not bypass them by creating a blank save.

## Restore drill / recovery

```bash
python scripts/game_backup.py restore /home/deserthouses/.desert-houses/backups/EXACT_BACKUP_NAME.fernet --key /home/deserthouses/.desert-houses/backup.key --output /home/deserthouses/recovery-check
```

The output directory MUST NOT exist. The command authenticates and integrity-checks the backup, then writes a restored `game.db` and private `config.json` there. This is a restore drill; it does not switch the live game. The restored config points at its new database location and preserves the signing secret.

For real recovery, disable the Web app first, back up the current files, inspect the recovered accounts/progress, and deliberately switch the WSGI loader to the verified recovered config before reloading. Never replace an active SQLite database or leave old journal/WAL sidecars next to a replacement. Recovery returns to the backup point; changes made afterward will not be present.

Run manual backups after substantial playtesting as well as before updates. Free PythonAnywhere accounts do not provide scheduled tasks for new accounts; no unattended off-host backup service is configured by this change.

References: [Python SQLite online backup](https://docs.python.org/3.13/library/sqlite3.html), [Fernet authenticated encryption](https://cryptography.io/en/stable/fernet/).
