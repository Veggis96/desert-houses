# Free PythonAnywhere deployment

Intended address: `deserthouses.pythonanywhere.com`, subject to username availability.
Create a free account with username `deserthouses` at https://www.pythonanywhere.com/registration/register/beginner/. Choose an alternative game-related username if taken. These instructions use the US system; an EU account uses its assigned EU hostname, which must also be updated in the private config's TRUSTED_HOSTS.

## Install

Open a Bash console on PythonAnywhere:

```bash
git clone https://github.com/Veggis96/desert-houses.git ~/desert-houses
cd ~/desert-houses
mkvirtualenv --python=/usr/bin/python3.13 desert-houses
pip install -r requirements.txt -r requirements-backup.txt
python scripts/setup-pythonanywhere.py
```

Use an available Python version >=3.11 and select that same version in the Web tab. The setup creates a NEW game world. If migrating an existing save, stop here and arrange a consistent SQLite copy before initialization instead.

The setup preserves the signing secret on reruns. It stores config and database outside the checkout in `~/.desert-houses`, with directory permissions 700 and file permissions 600. Never map this directory as static files or publish its contents. This restricts OS access; it does not encrypt the SQLite database.

## Configure the website

1. Web tab > Add a new web app > free address > Manual configuration > matching Python version.
2. Set the virtualenv to `/home/YOUR_USERNAME/.virtualenvs/desert-houses`.
3. Replace the Web tab's WSGI file contents with `deploy/pythonanywhere_wsgi.py` from this repository.
4. Enable Force HTTPS in the Web tab's Security section. PythonAnywhere supplies the certificate for its free address.
5. Reload, then visit the HTTPS address.

Flask serves static assets itself initially; no static mapping is required. Never use the Flask development server for this deployment.

## Launch verification

Register a test player, log out, log back in, and check buildings and map actions. Reload the Web app and confirm the account and progress survive. Confirm HTTP redirects to HTTPS and an invalid host is rejected. If HTTPS requests return 400, inspect the error log and the platform's WSGI scheme configuration; do not disable HTTPS enforcement or add unrestricted ProxyFix headers. Live verification is required before inviting players.

## Updates and free-plan limits

Use the backup-first update command in [Player progress protection](PLAYER_PROGRESS.md). It preserves the save directory, backs up before pulling changes, and initializes the schema before you reload.

The free plan has one worker, 512 MiB storage, a monthly web-app expiry renewal, and no scheduled tasks for new accounts. It is appropriate for an initial small-player trial; capacity has not been measured. Check expiry and disk usage in the dashboard regularly.

Encrypted backup and restore tools are now available: follow [Player progress protection](PLAYER_PROGRESS.md) to install them and keep separate copies. These tools are not active on the live host until you run the installation steps.

Sources: [Flask setup](https://help.pythonanywhere.com/pages/Flask/), [HTTPS](https://help.pythonanywhere.com/pages/HTTPSSetup/), [free account limits](https://help.pythonanywhere.com/pages/FreeAccountsFeatures).
