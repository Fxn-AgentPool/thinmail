"""The web server: login page, the inbox page and its small JSON API.

Standard library only (ThreadingHTTPServer). Sessions live in memory: the
login page checks the user name and password against the IMAP server, and the
session keeps them to reconnect. A restart logs everyone out.

Security, sized for a LAN-only tool: random session cookie (HttpOnly,
SameSite=Strict, Secure when configured), POSTs must come from this origin,
failed logins are rate limited per client address, strict CSP, and mail HTML is
only shown in a sandboxed iframe with no scripts and no remote content unless
asked for.
"""

from __future__ import annotations

import html
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .mail import AuthError, ImapSettings, Mailbox, MailError, safe_html

COOKIE = "thinmail"


# ---- configuration ----------------------------------------------------------

def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    imap: ImapSettings
    listen_host: str = "127.0.0.1"
    listen_port: int = 8088
    secure_cookie: bool = False
    session_hours: float = 12
    limit: int = 40
    accent: str = "#f2545b"
    title: str = "thinmail"
    default_domain: str = ""        # appended to user names without "@"

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        values: dict[str, str] = {}
        path = path or os.environ.get("THINMAIL_CONF")
        candidates = [Path(path)] if path else [Path.home() / ".config" / "thinmail.conf", Path("/etc/thinmail.conf")]
        for p in candidates:
            if p.is_file():
                for line in p.read_text().splitlines():
                    key, sep, value = line.strip().partition("=")
                    if sep and not key.startswith("#"):
                        values[key.strip()] = value.strip().strip('"').strip("'")
                break
        values.update({k: v for k, v in os.environ.items() if k.startswith("THINMAIL_")})
        get = values.get

        host = get("THINMAIL_IMAP_HOST")
        if not host:
            raise SystemExit("thinmail: set THINMAIL_IMAP_HOST (environment or thinmail.conf)")
        security = (get("THINMAIL_IMAP_SECURITY") or "ssl").lower()
        if security not in ("ssl", "starttls", "plain"):
            raise SystemExit("thinmail: THINMAIL_IMAP_SECURITY must be ssl, starttls or plain")
        port = int(get("THINMAIL_IMAP_PORT") or (993 if security == "ssl" else 143))
        listen = get("THINMAIL_LISTEN") or "127.0.0.1:8088"
        lhost, _, lport = listen.rpartition(":")
        accent = get("THINMAIL_ACCENT") or "#f2545b"
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
            raise SystemExit("thinmail: THINMAIL_ACCENT must look like #f2545b")
        return cls(
            imap=ImapSettings(host=host, port=port, security=security,
                              verify=_bool(get("THINMAIL_IMAP_VERIFY"), True),
                              cafile=get("THINMAIL_IMAP_CAFILE") or None),
            listen_host=lhost.strip("[]") or "127.0.0.1",
            listen_port=int(lport),
            secure_cookie=_bool(get("THINMAIL_SECURE_COOKIE"), False),
            session_hours=float(get("THINMAIL_SESSION_HOURS") or 12),
            limit=max(1, min(200, int(get("THINMAIL_LIMIT") or 40))),
            accent=accent.lower(),
            title=get("THINMAIL_TITLE") or "thinmail",
            default_domain=(get("THINMAIL_DEFAULT_DOMAIN") or "").lstrip("@"),
        )


# ---- sessions & login throttling --------------------------------------------

@dataclass
class Session:
    mailbox: Mailbox
    user: str
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class Sessions:
    IMAP_IDLE = 5 * 60         # close an unused IMAP connection after this

    def __init__(self, hours: float):
        self.ttl = hours * 3600
        self.items: dict[str, Session] = {}
        self.lock = threading.Lock()

    def add(self, s: Session) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.items[token] = s
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self.lock:
            s = self.items.get(token)
            if s is None:
                return None
            if time.time() - s.last_seen > self.ttl:
                del self.items[token]
                threading.Thread(target=s.mailbox.close, daemon=True).start()
                return None
            s.last_seen = time.time()
            return s

    def remove(self, token: str | None):
        with self.lock:
            s = self.items.pop(token or "", None)
        if s:
            s.mailbox.close()

    def sweep(self):
        now = time.time()
        with self.lock:
            expired = [t for t, s in self.items.items() if now - s.last_seen > self.ttl]
            dead = [self.items.pop(t) for t in expired]
            idle = [s for s in self.items.values() if now - s.mailbox.last_used > self.IMAP_IDLE]
        for s in dead + idle:
            s.mailbox.close()


class Throttle:
    """At most `tries` failed logins per client address in `window` seconds."""

    def __init__(self, tries: int = 5, window: float = 600):
        self.tries, self.window = tries, window
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def blocked(self, ip: str) -> float:
        """Seconds until the address may try again (0: may try now)."""
        now = time.time()
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < self.window]
            self.fails[ip] = recent
            return (recent[0] + self.window - now) if len(recent) >= self.tries else 0

    def failed(self, ip: str):
        with self.lock:
            self.fails.setdefault(ip, []).append(time.time())

    def succeeded(self, ip: str):
        with self.lock:
            self.fails.pop(ip, None)


# ---- HTTP -------------------------------------------------------------------

STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8"}
APP_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
           "connect-src 'self'; frame-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


def _static(name: str) -> bytes:
    return resources.files("thinmail").joinpath("static", name).read_bytes()


class App:
    def __init__(self, config: Config, mailbox_factory=Mailbox):
        self.config = config
        self.sessions = Sessions(config.session_hours)
        self.throttle = Throttle()
        self.mailbox_factory = mailbox_factory
        self.static = {}
        for name in ("style.css", "app.js", "icon.svg"):
            self.static[name] = _static(name)
        self.pages = {name: _static(name).decode() for name in ("login.html", "app.html")}

    def page(self, name: str, **values) -> bytes:
        text = self.pages[name]
        values.setdefault("title", self.config.title)
        values.setdefault("version", __version__)
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", html.escape(str(value)))
        return text.encode()

    def theme_css(self) -> bytes:
        a = self.config.accent
        r, g, b = (int(a[i:i + 2], 16) for i in (1, 3, 5))
        return (f":root {{ --accent: {a}; --accent-rgb: {r}, {g}, {b}; }}\n").encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "thinmail"
    sys_version = ""
    app: App            # set by make_server
    protocol_version = "HTTP/1.1"

    # -- plumbing --

    def log_message(self, fmt, *args):
        # Never log query strings (they hold nothing secret, but keep logs short).
        if os.environ.get("THINMAIL_ACCESS_LOG"):
            super().log_message(fmt, *args)

    @property
    def client_ip(self) -> str:
        return self.client_address[0]

    def token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            c = SimpleCookie(raw)
        except Exception:
            return None
        return c[COOKIE].value if COOKIE in c else None

    def session(self) -> Session | None:
        return self.app.sessions.get(self.token())

    def send(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8",
             headers: dict | None = None, csp: str | None = APP_CSP):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # same-origin, not no-referrer: with no-referrer browsers send
        # "Origin: null" on form POSTs, which the CSRF check would refuse.
        # Links out of mail carry rel=noreferrer instead.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        if csp == APP_CSP:
            self.send_header("X-Frame-Options", "DENY")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, data, status: int = 200):
        self.send(status, json.dumps(data).encode(), "application/json")

    def redirect(self, where: str, cookie: str | None = None):
        headers = {"Location": where}
        if cookie is not None:
            headers["Set-Cookie"] = cookie
        self.send(HTTPStatus.SEE_OTHER, b"", headers=headers)

    def cookie(self, value: str, max_age: int) -> str:
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
        if self.app.config.secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def same_origin(self) -> bool:
        """POSTs must come from a page of this site (CSRF guard, with SameSite)."""
        if self.headers.get("Sec-Fetch-Site") == "same-origin":
            return True
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            origin = self.headers.get("Referer")
        if not origin:
            return False
        host = self.headers.get("Host", "")
        return urlsplit(origin).netloc == host

    def body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise ValueError("request too large")
        return self.rfile.read(n) if n else b""

    # -- routing --

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        path = url.path
        try:
            if path == "/login":
                if self.session():
                    return self.redirect("/")
                return self.send(200, self.app.page("login.html", error="", user=""), "text/html; charset=utf-8")
            if path == "/theme.css":
                return self.send(200, self.app.theme_css(), "text/css; charset=utf-8",
                                 headers={"Cache-Control": "max-age=300"})
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                data = self.app.static.get(name)
                if data is None:
                    return self.send(404, b"not found")
                return self.send(200, data, STATIC_TYPES.get(Path(name).suffix, "application/octet-stream"))
            if path == "/healthz":
                return self.send(200, b"ok")

            s = self.session()
            if path == "/":
                if not s:
                    return self.redirect("/login")
                return self.send(200, self.app.page("app.html", user=s.user), "text/html; charset=utf-8")
            if not s:
                return self.json({"error": "login required"}, 401)
            if path == "/api/folders":
                return self.json({"folders": s.mailbox.folders()})
            if path == "/api/messages":
                folder = q.get("folder") or "*"
                if folder == "*":
                    msgs = s.mailbox.recent_merged(self.app.config.limit)
                else:
                    msgs = s.mailbox.recent(folder, self.app.config.limit)
                return self.json({"folder": folder, "messages": [m.summary() for m in msgs], "now": time.time()})
            m = re.fullmatch(r"/api/messages/(\d+)", path)
            if m:
                msg = s.mailbox.get(q.get("folder") or "INBOX", int(m.group(1)), mark_seen=True)
                if msg is None:
                    return self.json({"error": "no such message"}, 404)
                return self.json(msg.full())
            m = re.fullmatch(r"/api/messages/(\d+)/html", path)
            if m:
                msg = s.mailbox.get(q.get("folder") or "INBOX", int(m.group(1)), mark_seen=False)
                if msg is None or msg.html is None:
                    return self.send(404, b"no HTML part")
                img = "img-src data: https: http:" if q.get("images") == "1" else "img-src data:"
                csp = (f"default-src 'none'; style-src 'unsafe-inline'; {img}; font-src data:; "
                       "frame-ancestors 'self'; base-uri 'none'; form-action 'none'; "
                       "sandbox allow-popups allow-popups-to-escape-sandbox")
                return self.send(200, safe_html(msg.html).encode(), "text/html; charset=utf-8", csp=csp)
            return self.send(404, b"not found")
        except AuthError:
            # Password changed on the server: log out.
            self.app.sessions.remove(self.token())
            return self.json({"error": "login required"}, 401)
        except MailError as e:
            return self.json({"error": str(e)}, 502)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.same_origin():
            return self.send(403, b"cross-site request refused")
        try:
            body = self.body()
        except ValueError:
            return self.send(413, b"too large")

        if path == "/login":
            return self.login(body)
        if path == "/logout":
            self.app.sessions.remove(self.token())
            return self.redirect("/login", cookie=self.cookie("", 0))

        s = self.session()
        if not s:
            return self.json({"error": "login required"}, 401)
        m = re.fullmatch(r"/api/messages/(\d+)/delete", path)
        if m:
            try:
                data = json.loads(body or b"{}")
                s.mailbox.delete(str(data.get("folder") or "INBOX"), int(m.group(1)))
            except AuthError:
                self.app.sessions.remove(self.token())
                return self.json({"error": "login required"}, 401)
            except MailError as e:
                return self.json({"error": str(e)}, 502)
            except (ValueError, AttributeError):
                return self.json({"error": "bad request"}, 400)
            return self.json({"ok": True})
        return self.send(404, b"not found")

    def login(self, body: bytes):
        form = {k: v[-1] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        user = form.get("user", "").strip()
        password = form.get("password", "")
        page = lambda error, status: self.send(status, self.app.page("login.html", error=error, user=user),
                                               "text/html; charset=utf-8")
        wait = self.app.throttle.blocked(self.client_ip)
        if wait:
            return page(f"Too many failed logins. Try again in {int(wait // 60) + 1} min.", 429)
        if not user or not password:
            return page("Enter user name and password.", 400)
        login_user = user
        if self.app.config.default_domain and "@" not in user:
            login_user = f"{user}@{self.app.config.default_domain}"
        mailbox = self.app.mailbox_factory(self.app.config.imap, login_user, password)
        try:
            mailbox.login()
        except AuthError:
            self.app.throttle.failed(self.client_ip)
            return page("Wrong user name or password.", 401)
        except MailError as e:
            return page(f"Mail server unreachable: {e}", 502)
        self.app.throttle.succeeded(self.client_ip)
        token = self.app.sessions.add(Session(mailbox=mailbox, user=login_user))
        return self.redirect("/", cookie=self.cookie(token, int(self.app.config.session_hours * 3600)))


def make_server(config: Config, mailbox_factory=Mailbox) -> ThreadingHTTPServer:
    app = App(config, mailbox_factory)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), handler)
    server.daemon_threads = True
    server.app = app

    def sweeper():
        while True:
            time.sleep(60)
            app.sessions.sweep()
    threading.Thread(target=sweeper, daemon=True).start()
    return server
