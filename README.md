# thinmail

A thin web inbox for one job: getting at 2FA codes, verification codes and sign-in links
in your own mailbox. Log in with your mail account, see the newest mail with the code
already pulled out, click to copy. Made for a self-hosted [Stalwart](https://stalw.art)
server, but it speaks plain IMAP, so any IMAP server works.

- **Login page**: checks your user name and password against the IMAP server; no
  separate user database.
- **Inbox + Junk** in one list (codes often land in Junk), newest first, live (polls every
  6 s while the tab is visible). Other folders as tabs.
- **Codes and links found for you**: `482917`, `731 882`, `G-604311`, `K7Q2M`, near words
  like *code*, *verification*, *PIN*, *Bestätigungscode*; order numbers, dates, prices
  and phone numbers are skipped. "Verify" / "Sign in" / "Confirm" links get a button.
- The newest code from the last 15 minutes sits in a big card at the top. Click it, or
  press `c`, to copy it.
- Reader: text by default, the mail's HTML on request in a sandboxed frame (no scripts,
  no remote images unless you press *Images*). Opening marks a mail read; delete moves it
  to Trash.
- Keys: `j`/`k` move, `Enter` open, `c` copy code, `r` refresh, `#` delete, `Esc` close.
- Python standard library only; about 20 MB of RAM.

The look follows the Quickshell config in
[fxnarji/lapdotfiles](https://github.com/fxnarji/lapdotfiles): the same mono palette,
accent, Geist fonts and raised cards. Set `THINMAIL_ACCENT` to your matugen accent if you like.

## Run

Docker, next to Stalwart (see [`deploy/compose.example.yml`](deploy/compose.example.yml)):

```sh
git clone https://github.com/Fxn-AgentPool/thinmail && cd thinmail
docker build -t thinmail .
docker run -d --name thinmail -p 8088:8088 \
  -e THINMAIL_IMAP_HOST=mail.example.org thinmail
```

Or with Python 3.10+, no dependencies:

```sh
python3 -m venv /opt/thinmail && /opt/thinmail/bin/pip install .
THINMAIL_IMAP_HOST=mail.example.org THINMAIL_LISTEN=0.0.0.0:8088 /opt/thinmail/bin/thinmail
```

A hardened systemd unit is in [`deploy/thinmail.service`](deploy/thinmail.service).

## Settings

Environment variables, or `KEY=value` lines in `--config FILE`, `$THINMAIL_CONF`,
`~/.config/thinmail.conf` or `/etc/thinmail.conf` (environment wins). All of them are listed
in [`deploy/thinmail.conf.example`](deploy/thinmail.conf.example).

| Key | Default | |
|---|---|---|
| `THINMAIL_IMAP_HOST` | (required) | Stalwart's host name |
| `THINMAIL_IMAP_PORT` | 993 / 143 | |
| `THINMAIL_IMAP_SECURITY` | `ssl` | `ssl`, `starttls` or `plain` |
| `THINMAIL_IMAP_VERIFY` | `1` | `0` accepts a self-signed or mismatching certificate |
| `THINMAIL_IMAP_CAFILE` | | CA bundle for the server's certificate |
| `THINMAIL_LISTEN` | `127.0.0.1:8088` | `0.0.0.0:8088` to reach it from the LAN |
| `THINMAIL_SECURE_COOKIE` | `0` | `1` when served over HTTPS |
| `THINMAIL_DEFAULT_DOMAIN` | | log in as `me` instead of `me@example.org` |
| `THINMAIL_SESSION_HOURS` | `12` | idle time until you have to log in again |
| `THINMAIL_LIMIT` | `40` | mails listed per folder |
| `THINMAIL_ACCENT`, `THINMAIL_TITLE` | `#f2545b`, `thinmail` | looks |

## Security

Meant for a LAN, not the internet. What it does:

- Session cookie: random, `HttpOnly`, `SameSite=Strict`, `Secure` when configured. Sessions
  live in memory, and a restart logs you out. The password is kept in memory only, to
  reconnect to IMAP.
- POST requests must come from the site itself (`Origin` / `Sec-Fetch-Site`). After 5 failed
  logins in 10 minutes, an address must wait.
- Strict CSP; mail content is only ever inserted as text. Mail HTML is shown in an
  `<iframe sandbox>` without scripts, forms or same-origin access, and remote images
  (tracking pixels) are blocked unless you ask for them.

What it doesn't: TLS. Over plain `http://` your password crosses the LAN unencrypted. If
you have a reverse proxy with a certificate, put thinmail behind it and set
`THINMAIL_SECURE_COOKIE=1`. Copying uses the Clipboard API on HTTPS or localhost, and an
older fallback on plain HTTP, which works in current browsers.

## Develop

```sh
uv venv && uv pip install -e '.[test]'
.venv/bin/pytest -q                        # unit tests, no mail server needed
scripts/test-stalwart.sh up                # throwaway Stalwart in Docker (alice@test.lan)
scripts/send_samples.py 127.0.0.1:10025 alice@test.lan
THINMAIL_IMAP_HOST=127.0.0.1 THINMAIL_IMAP_PORT=10993 THINMAIL_IMAP_VERIFY=0 .venv/bin/thinmail
.venv/bin/python tests/http_smoke.py http://127.0.0.1:8088 alice@test.lan alice-test-pw-123
```
