# thinmail

Thin web inbox for a Stalwart mailbox: login page, recent mail, 2FA codes one click away.
For use on a LAN with a self-hosted Stalwart; look borrowed from the Quickshell config
github.com/fxnarji/lapdotfiles (`.config/quickshell/Theme.qml`). Repo: Fxn-AgentPool/thinmail (public).

## Layout
- `src/thinmail/codes.py`: `find_code(subject, text)`, a scoring heuristic (keywords nearby,
  length, alone on a line; penalties for order/phone/amount/date context, URLs blanked out).
  Add cases to `tests/test_codes.py` when a real mail is misread.
- `src/thinmail/mail.py`: MIME parsing (`parse` never raises), HTML→text, action-link detection,
  `Mailbox`: one IMAP connection per web session, cache by (folder, UIDVALIDITY, UID),
  `recent_merged` = Inbox + Junk.
- `src/thinmail/web.py`: `Config.load` (env / KEY=value file), in-memory sessions, login throttle,
  `Handler` routes (`/login`, `/logout`, `/api/folders`, `/api/messages[?folder=*]`,
  `/api/messages/<uid>[/html|/delete]`), CSP/headers.
- `src/thinmail/static/`: `login.html`, `app.html` (`{{key}}` placeholders, HTML-escaped),
  `style.css` (Theme.qml tokens), `app.js` (no framework; DOM nodes only, never innerHTML for mail).
- `deploy/`: compose example, systemd unit, config example. `Dockerfile` at the root.
- `scripts/build.sh [--binary]`: `dist/thinmail.pyz` (zipapp) and `dist/thinmail` (PyInstaller onefile).
  Tag `vX.Y.Z` → `.github/workflows/release.yml` builds x86_64 + aarch64 on ubuntu-22.04 and publishes them.
- `scripts/test-stalwart.sh up|down`: throwaway Stalwart 1.x in Docker; `scripts/send_samples.py`.

## Develop
- Env: `uv venv && uv pip install -e '.[test]'`
- Test: `.venv/bin/pytest -q` (unit tests with a fake mailbox, no network)
- Integration: `scripts/test-stalwart.sh up`, `scripts/send_samples.py 127.0.0.1:10025 alice@test.lan`,
  run thinmail (`THINMAIL_IMAP_HOST=127.0.0.1 THINMAIL_IMAP_PORT=10993 THINMAIL_IMAP_VERIFY=0`),
  then `tests/http_smoke.py URL alice@test.lan alice-test-pw-123` (it deletes the "Order" sample, so
  resend `--only 4` before a second run). `scripts/test-stalwart.sh down` afterwards (RAM).
- Browser check: Playwright venv in `~/lab/mail-thin/browser` (`shot.py`, `live.py`),
  `PLAYWRIGHT_BROWSERS_PATH=/data/cache/ms-playwright`; screenshots into `~/lab/mail-thin/shots`.

## Gotchas
- Static files are read once at startup: restart the server after editing `static/`.
- Referrer-Policy must not be `no-referrer`: Chrome then sends `Origin: null` on form POSTs and the
  CSRF check refuses the login. It's `same-origin`, plus `Sec-Fetch-Site: same-origin` is accepted.
- The CSP (`script-src 'self'`) also blocks Playwright's string `wait_for_function`; poll from Python.
- Stalwart 1.x setup has no TOML any more: `config.json` names only the data store; domain/accounts go
  through the management API (`stalwart-cli`, github.com/stalwartlabs/cli). Account `credentials` is a
  map `{"0": {"@type": "Password", "secret": ...}}`, not a list. Recovery mode
  (`STALWART_RECOVERY_MODE=1`, `STALWART_RECOVERY_ADMIN=admin:pw`) skips the web wizard.
- Stalwart files unsigned test mail into "Junk Mail"; that's why the default view merges Inbox + Junk.
  Folder roles come from IMAP LIST special-use flags (`\Junk`, `\Trash`).
- Docker's port proxy accepts TCP before Stalwart listens: wait for an SMTP/IMAP greeting, not a socket.

## Status
- 2026-11-11: release artefacts: executable (8.6 MB, ~36 MB RAM) and .pyz (25 KB) pass the 27 smoke checks.
- 2026-11-11: v0.1.0. 30 unit tests; end-to-end against Stalwart 1.1.0 (27/27 smoke checks, native
  and in the Docker image, ~18 MB RAM); headless Chromium checked login, list, copy (keyboard and chip),
  HTML sandbox view, phone layout, live arrival (≤ 6 s). Not yet run against a production server.
