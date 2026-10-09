"""PythonAnywhere update: a verified backup must succeed before code changes."""
from pathlib import Path
import subprocess
import sys
from game_backup import snapshot


def main():
    project = Path(__file__).resolve().parents[1]
    backup, key = snapshot(Path.home() / '.desert-houses' / 'config.json')
    print('Verified backup:', backup, flush=True)
    print('Key stays private at:', key, flush=True)
    subprocess.run(['git', 'pull', '--ff-only'], cwd=project, check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt', '-r', 'requirements-backup.txt'], cwd=project, check=True)
    subprocess.run([sys.executable, 'scripts/setup-pythonanywhere.py'], cwd=project, check=True)
    print('Update and database initialization finished. Reload the Web app now.')


if __name__ == '__main__':
    main()
