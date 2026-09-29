# Configuration

## Local play

Run `python app.py` from the project environment. This initializes/migrates the SQLite database. Debug mode and development tools are off by default. No default shared session secret is used; without `SECRET_KEY`, sessions expire when the process restarts.

## Local development tools

To opt in, set `DEV_TOOLS_ENABLED=1` before starting the server. Both resource boosting and password recovery accept requests only from the direct loopback address (`127.0.0.1` or `::1`). Forwarded headers are not trusted for this decision. Password recovery also requires a nonempty `DEV_ADMIN_TOKEN` configured in the server environment and entered in the reset form. The token is never embedded in the page. Do not enable these tools behind a local reverse proxy serving remote users.

Set `FLASK_DEBUG=1` only for local debugging. Keep it off for remote access. The Tailscale launcher leaves development tools disabled unless explicitly enabled in the process environment; remote addresses cannot use them.

## Production setup

Set `GAME_ENV=production` and a securely generated, persistent `SECRET_KEY`. Startup rejects production mode without a secret. Set `COOKIE_SECURE=1` when serving over HTTPS; leave it off only for HTTP local development. Cookies are HttpOnly and SameSite=Lax.

Initialize/migrate before launching a production WSGI server:

```powershell
.\.venv\Scripts\python.exe -c "import app; app.init_db()"
```

Do not commit `.env`, signing secrets, admin tokens, or saved databases. Use a production WSGI server and HTTPS for public hosting. These changes restrict development tools; they do not constitute a complete multiplayer security audit or production deployment.

## Sign-up and login

Run database initialization after updating to add the persistent `auth_attempts` table. The normal `python app.py` entry point and PowerShell game launcher do this automatically.

- Registration: at most 20 submitted attempts per direct client address in one hour.
- Login: at most 30 attempts per direct address and 8 per address/username pair in 15 minutes. Limits include successful attempts and expire automatically.
- Forwarded IP headers are not trusted. Behind a reverse proxy, users may share a limit; configure trusted proxy handling before a larger deployment.
- Form CSRF tokens are tied to the session. Refresh an expired form before submitting again.
- Request bodies are capped at 64 KiB.

Set a nonempty `REGISTRATION_INVITE_CODE` in the process environment to require it during registration. Restart the server after changing this environment value. Do not commit the code or render it in the page; share it directly with invited players. Leave the variable unset for open registration.

Existing account passwords remain usable, including short legacy passwords; the new length rule applies at sign-up. Email verification and email password recovery require a mail provider and have not been added.
