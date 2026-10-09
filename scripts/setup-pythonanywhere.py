"""Run once in a PythonAnywhere Bash console, inside the game's virtualenv."""
import json
import os
from pathlib import Path
import secrets
import sys


def main():
    home = Path.home()
    data = home / '.desert-houses'
    config = data / 'config.json'
    os.umask(0o077)
    data.mkdir(mode=0o700, exist_ok=True)
    data.chmod(0o700)
    existing = config.exists()
    if existing:
        settings = json.loads(config.read_text())
    else:
        if (data / 'game.db').exists():
            raise RuntimeError('Existing save has no signing config. Recover config.json before setup.')
        settings = {
            'GAME_ENV': 'production',
            'SECRET_KEY': secrets.token_hex(32),
            'TRUSTED_HOSTS': home.name + '.pythonanywhere.com',
            'GAME_DB_PATH': str(data / 'game.db'),
            'COOKIE_SECURE': '1',
            'FLASK_DEBUG': '0',
            'DEV_TOOLS_ENABLED': '0',
        }
        with config.open('x') as handle:
            json.dump(settings, handle)
    config.chmod(0o600)
    os.environ.update(settings)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if existing:
        from scripts.game_backup import snapshot
        backup, _key = snapshot(config)
        print('Verified backup before database initialization:', backup)
    import app
    app.init_db()
    Path(app.DB_PATH).chmod(0o600)
    print('Production configuration and database initialized. Secrets were not printed.')
    print('Expected hostname:', settings['TRUSTED_HOSTS'])


if __name__ == '__main__':
    main()
