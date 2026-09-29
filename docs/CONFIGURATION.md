# Configuration

## Local play

Run `python app.py` from the project environment. This initializes/migrates the SQLite database. Debug mode and development tools are off by default. No default shared session secret is used; without `SECRET_KEY`, sessions expire when the process restarts.

## Local development tools

To opt in, set `DEV_TOOLS_ENABLED=1` before starting the server. Both resource boosting and password recovery accept requests only from the direct loopback address (`127.0.0.1` or `::1`). Forwarded headers are not trusted for this decision. Password recovery also requires a nonempty `DEV_ADMIN_TOKEN` configured in the server environment and entered in the reset form. The token is never embedded in the page. Do not enable these tools behind a local reverse proxy serving remote users.

Set `FLASK_DEBUG=1` only for local debugging. Keep it off for remote access. The Tailscale launcher leaves development tools disabled unless explicitly enabled in the process environment; remote addresses cannot use them.

## Production setup

Set `GAME_ENV=production`, a securely generated persistent `SECRET_KEY` of at least 32 characters, and `TRUSTED_HOSTS` containing comma-separated trusted hostnames. Production defaults to Secure cookies and rejects `COOKIE_SECURE=0`. All production requests must reach the application as HTTPS. Cookies are HttpOnly and SameSite=Lax. Development tools are unavailable in production even if enabled in the environment.

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

## Revocable sessions and protected forms

The update adds the `login_sessions` table. Existing cookie-only logins are intentionally invalidated and players must log in again. Server records store hashes of session tokens. Logout revokes the current session; password changes and local resets revoke all sessions for that player. Idle expiry is one hour, with an absolute 24-hour limit. Long game waits require logging back in, but the village keeps producing resources.

Every modifying form, including gameplay and logout, requires a session CSRF token. Logout is POST-only. Inline scripts receive per-response CSP nonces; adding scripts without these nonces will cause browsers to block them.

Set `GAME_DB_PATH` to an absolute path in a restricted server data directory when hosting. The directory must already exist. This setting selects the database path; it does not move an existing save or encrypt its contents. Keep backups encrypted and outside the served project tree.

If TLS terminates at a reverse proxy, the app must receive a trusted HTTPS scheme. No forwarding headers are trusted by default. Configure the server/proxy integration only for your known proxy and block direct public access to the backend. Without that configuration, production requests are rejected rather than processed over an unverified HTTP scheme.
