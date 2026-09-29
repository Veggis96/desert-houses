# Account security review

## Implemented protections

- Explicit salted scrypt password hashing (`32768:8:1`) and legacy-hash upgrades at login. Plaintext passwords are never persisted, placed in sessions, rendered, or logged by the app.
- New passwords/passphrases are 15–128 characters; changing a password requires the current password, matching confirmation and CSRF token.
- Server-checked sessions with hashed tokens, one-hour idle expiry and 24-hour absolute expiry. Replayed cookies fail after logout. Password changes and local resets revoke all sessions for that user.
- CSRF protection on all modifying routes and forms, including logout. CSRF is not disabled in tests; browser-style fixtures submit real tokens and rejection tests omit them explicitly.
- Parameterized account queries, sign-up validation and SQLite-backed login/sign-up limits. Exact legacy username matches remain supported.
- Template user data omits password hashes. Private report and alliance access remains scoped to the current player/membership.
- Public usernames and faction/game rankings are intentional game data. No email addresses or payment details are collected.
- No-store dynamic pages, script CSP nonces, anti-framing, content-type, referrer and permission headers; production HSTS.
- Production requires a persistent secret of at least 32 characters, trusted hostnames, Secure cookies and HTTPS. Production rejects all development tools.
- Flask updated to security release 3.1.3. Secrets, SQLite files and database sidecars are excluded from Git.

## What deployment must still provide

The code cannot guarantee security by itself. Public hosting must use HTTPS, a production WSGI server, restricted backend access, operating-system permissions on the database and signing secrets, encrypted storage/backups, and security updates. SQLite data is not encrypted by this application. Use `GAME_DB_PATH` to keep the production save outside the website checkout and shared/synced folders; migrate existing saves deliberately rather than creating a blank database by accident.

The current workstation checkout is in OneDrive. No real player account data should be assumed to be isolated from that sync service. The production database directory and backups need to be selected when the host is chosen; this update has not moved or changed any real save.

Reverse-proxy HTTPS/IP handling must be configured for the known proxy only. The application does not trust forwarded headers by default. Rate limits currently operate on the direct client address, so a proxy may aggregate players into one bucket.

Email verification/recovery, MFA, breach-password screening, automated dependency scanning and an independent penetration test are not provided by this pass. Do not promise absolute security or advertise this as a complete certification.

## Verification

Security tests cover revoked-cookie replay, idle/absolute expiry, cross-site form rejection, redirect confinement, current-password checks, all-device sign-out, legacy hash upgrades, reset revocation, private report access, exclusion of hashes from template data, CSP nonces, production configuration, trusted hosts and HTTPS cookies. The full gameplay/account suite runs on isolated temporary databases. No production deployment or live infrastructure assessment has been performed.

## References

- [Flask security considerations](https://flask.palletsprojects.com/en/stable/web-security/)
- [Flask 3.1.3 security release](https://github.com/pallets/flask/releases/tag/3.1.3)
- [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
