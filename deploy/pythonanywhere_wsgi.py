"""Paste this into PythonAnywhere's Web tab WSGI configuration file."""
import json
import os
from pathlib import Path
import sys

os.umask(0o077)
home = Path.home()
settings = json.loads((home / '.desert-houses' / 'config.json').read_text())
os.environ.update(settings)
project = home / 'desert-houses'
sys.path.insert(0, str(project))
from app import app as application

# PythonAnywhere supplies the WSGI request scheme. Do not blindly trust
# browser-supplied forwarding headers. Keep the app's HTTPS check enabled.
