"""Web layer against a fake mailbox: no mail server needed."""

import http.cookiejar
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

import pytest

from thinmail.mail import AuthError, ImapSettings, parse
from thinmail.web import Config, make_server


def sample(uid, subject, text, folder="INBOX"):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = "Bank <otp@bank.example>", subject, "Wed, 11 Nov 2026 16:00:00 +0000"
    m.set_content(text)
    msg = parse(uid, m.as_bytes())
    msg.folder = folder
    return msg


class FakeMailbox:
    PASSWORD = "secret"

    def __init__(self, settings, user, password):
        self.user, self.password = user, password
        self.msgs = {("INBOX", 1): sample(1, "Your code", "Your code is 271828."),
                     ("Junk", 7): sample(7, "Login", "Use 314159 to sign in.", "Junk")}
        self.last_used = 0

    def login(self):
        if self.password != self.PASSWORD:
            raise AuthError("wrong")

    def close(self):
        pass

    def folders(self):
        return [{"name": "INBOX", "role": "inbox"}, {"name": "Junk", "role": "junk"}]

    def recent(self, folder, limit):
        return [m for (f, _), m in self.msgs.items() if f == folder]

    def recent_merged(self, limit):
        return list(self.msgs.values())

    def get(self, folder, uid, mark_seen=True):
        m = self.msgs.get((folder, uid))
        if m and mark_seen:
            m.seen = True
        return m

    def delete(self, folder, uid):
        self.msgs.pop((folder, uid), None)


@pytest.fixture
def server():
    config = Config(imap=ImapSettings("unused"), listen_port=0, default_domain="example.org")
    srv = make_server(config, mailbox_factory=FakeMailbox)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def __call__(self, path, data=None, origin=True, headers=None):
        r = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        if origin:
            r.add_header("Origin", self.base)
        try:
            resp = self.op.open(r)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def login(self, user="me", password="secret", **kw):
        return self("/login", urllib.parse.urlencode({"user": user, "password": password}).encode(), **kw)


def test_login_flow_and_messages(server):
    srv, base = server
    c = Client(base)
    assert c("/api/messages")[0] == 401
    status, body = c.login()
    assert status == 200 and b'id="list"' in body
    # Bare user names get the default domain.
    session = next(iter(srv.app.sessions.items.values()))
    assert session.user == "me@example.org"

    status, body = c("/api/messages")
    data = json.loads(body)
    assert {m["code"] for m in data["messages"]} == {"271828", "314159"}
    assert {m["folder"] for m in data["messages"]} == {"INBOX", "Junk"}
    assert all("alias" in m for m in data["messages"])

    status, body = c("/api/messages/7?folder=Junk")
    assert status == 200 and json.loads(body)["seen"] is True
    assert c("/api/messages/99")[0] == 404


def test_post_needs_same_origin(server):
    _, base = server
    c = Client(base)
    assert c.login(origin=False)[0] == 403
    assert c("/login", b"user=me&password=secret", origin=False,
             headers={"Origin": "http://evil.example"})[0] == 403
    c.login()
    assert c("/api/messages/1/delete", b"{}", origin=False)[0] == 403
    assert c("/api/messages/1/delete", b'{"folder": "INBOX"}')[0] == 200


def test_login_throttle(server):
    _, base = server
    c = Client(base)
    for _ in range(5):
        assert c.login(password="nope")[0] == 401
    status, body = c.login()          # right password, but blocked now
    assert status == 429 and b"Too many failed logins" in body


def test_error_text_is_escaped(server):
    _, base = server
    c = Client(base)
    status, body = c.login(user='<script>x</script>', password="nope")
    assert b"<script>x" not in body and b"&lt;script&gt;" in body


def test_logout(server):
    _, base = server
    c = Client(base)
    c.login()
    assert c("/logout", b"")[0] == 200
    assert c("/api/messages")[0] == 401


def test_headers(server):
    _, base = server
    resp = urllib.request.urlopen(base + "/login")
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
