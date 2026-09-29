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
