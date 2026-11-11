"""Reading mail: IMAP access (one connection per web session) and MIME parsing.

Messages are parsed once and cached by (folder, UIDVALIDITY, UID): a message
with a given UID never changes, so a refresh only fetches flags plus the
bodies of messages it hasn't seen yet.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from .codes import find_code

MAX_FETCH = 2 * 1024 * 1024        # bytes of a message fetched at most
TIMEOUT = 15


class MailError(Exception):
    pass


class AuthError(MailError):
    pass


# ---- parsing ----------------------------------------------------------------

BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table",
         "section", "article", "header", "footer", "blockquote", "hr", "ul", "ol", "center"}
SKIP = {"script", "style", "head", "title", "noscript", "template", "svg"}
ACTION = re.compile(
    r"verify|verification|confirm|activate|sign[ -]?in|log[ -]?in|magic|reset|"
    r"best[äa]tig|anmeld|aktivier|zur[üu]cksetzen|einloggen", re.I)


class _HtmlText(HTMLParser):
    """HTML to plain text, keeping the links (href and their text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.skip = 0
        self.href: str | None = None
        self.link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag in BLOCK:
            self.out.append("\n")
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.link_text = []
        if tag == "td":
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK:
            self.out.append("\n")
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join("".join(self.link_text).split())))
            self.href = None

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(data)
        if self.href is not None:
            self.link_text.append(data)

    def text(self) -> str:
        raw = "".join(self.out).replace("\xa0", " ")
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> tuple[str, list[tuple[str, str]]]:
    p = _HtmlText()
    try:
        p.feed(html)
        p.close()
    except Exception:  # broken HTML: whatever was parsed so far
        pass
    return p.text(), p.links


def action_link(links: list[tuple[str, str]], text: str) -> str | None:
    """The mail's 'Verify' / 'Sign in' / 'Confirm' link, if it has one."""
    for href, label in links:
        if href and href.lower().startswith(("https://", "http://")) and ACTION.search(label):
            return href
    for href, label in links:
        if href and href.lower().startswith(("https://", "http://")) and ACTION.search(href) and len(label) < 60:
            return href
    # Text-only mails: a URL on a line after a matching word.
    m = re.search(r"(?:" + ACTION.pattern + r")[^\n]{0,80}\n?\s*(https?://\S+)", text, re.I)
    return m.group(1) if m else None


def _decode(part) -> str:
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        if not isinstance(payload, bytes):
            return str(payload)
        try:
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
        except LookupError:            # unknown charset name
            return payload.decode("utf-8", "replace")


@dataclass
class Message:
    uid: int
    subject: str
    sender: str           # display name, or address
    address: str
    date: float           # unix time
    text: str
    html: str | None
    code: str | None
    link: str | None
    seen: bool = False
    flags: set = field(default_factory=set)
    folder: str = "INBOX"
    recipients: list = field(default_factory=list)   # To + Cc addresses, lower case
    delivered: list = field(default_factory=list)    # Delivered-To / X-Original-To (the mailbox's own address)
    alias: str | None = None                          # set by Mailbox, see find_alias

    def summary(self) -> dict:
        return {
            "uid": self.uid, "folder": self.folder, "subject": self.subject, "from": self.sender, "address": self.address,
            "date": self.date, "seen": self.seen, "code": self.code, "link": self.link, "alias": self.alias,
            "preview": " ".join(self.text.split())[:160],
        }

    def full(self) -> dict:
        return {**self.summary(), "text": self.text, "hasHtml": self.html is not None}


def parse(uid: int, raw: bytes, internal_date: float | None = None) -> Message:
    """Parse a message; never raises (one broken mail must not break the list)."""
    try:
        return _parse(uid, raw, internal_date)
    except Exception as e:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else ""
        return Message(uid=uid, subject="(unreadable message)", sender="", address="",
                       date=internal_date or 0.0, text=f"[thinmail could not parse this mail: {e}]\n\n{text}",
                       html=None, code=None, link=None)


def _parse(uid: int, raw: bytes, internal_date: float | None) -> Message:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("subject", "") or "").strip()
    try:
        name, addr = (getaddresses([str(msg.get("from", ""))]) or [("", "")])[0]
    except Exception:
        name, addr = "", str(msg.get("from", ""))
    def addresses(*headers):
        out = []
        for h in headers:
            try:
                values = [str(v) for v in msg.get_all(h, [])]
                out += [a.strip().lower() for _, a in getaddresses(values) if "@" in a]
            except Exception:
                pass
        return out
    try:
        date = parsedate_to_datetime(str(msg["date"])).timestamp()
    except Exception:
        date = internal_date or 0.0

    plain = html = None
    try:
        body = msg.get_body(preferencelist=("plain",))
        plain = _decode(body) if body else None
        body = msg.get_body(preferencelist=("html",))
        html = _decode(body) if body else None
    except Exception:
        pass
    if plain is None and html is None and not msg.is_multipart():
        plain = _decode(msg)

    links: list[tuple[str, str]] = []
    if html is not None:
        html_text, links = html_to_text(html)
    text = (plain or "").strip()
    if not text and html is not None:
        text = html_text
    if html is not None and not plain:
        code_source = html_text
    else:
        code_source = text

    return Message(
        uid=uid, subject=subject, sender=name or addr, address=addr, date=date,
        text=text, html=html, code=find_code(subject, code_source),
        link=action_link(links, text),
        recipients=addresses("to", "cc"), delivered=addresses("delivered-to", "x-original-to"),
    )


def find_alias(recipients: list[str], delivered: list[str], login: str = "",
               domains: tuple[str, ...] | list[str] = ()) -> str | None:
    """Which of your addresses a mail was sent to, as a short name for sorting.

    With a catch-all (discord@example.org) it's the local part, "discord";
    with plus addressing (me+discord@example.org) it's the tag, "discord".
    Mail to your main address, or with no address of yours in To/Cc (Bcc,
    newsletters), gives None. Stalwart's Delivered-To names the catch-all's
    target, i.e. your own address, so it tells which addresses and domains are
    yours; the original recipient only survives in To/Cc.
    """
    own = {a.lower() for a in delivered}
    if "@" in login:
        own.add(login.lower())
    domains = {d.lower().lstrip("@") for d in domains} | {a.rpartition("@")[2] for a in own}
    own_locals = {a.rpartition("@")[0] for a in own}
    for addr in recipients:
        local, _, domain = addr.lower().rpartition("@")
        if domain not in domains or not local:
            continue
        base, plus, tag = local.partition("+")
        if plus and tag and base in own_locals:
            return tag
        if addr.lower() in own:
            continue
        return local
    return None


def safe_html(html: str) -> str:
    """The mail's HTML for the sandboxed viewer: scripts can't run there anyway
    (iframe sandbox + CSP), this only makes links open in a new tab."""
    head = '<base target="_blank"><meta name="referrer" content="no-referrer">'
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + head, html, count=1, flags=re.I)
    return head + html


# ---- IMAP -------------------------------------------------------------------

@dataclass
class ImapSettings:
    host: str
    port: int = 993
    security: str = "ssl"          # ssl | starttls | plain
    verify: bool = True
    cafile: str | None = None
    alias_domains: tuple = ()      # your domains, for find_alias (more are learned from Delivered-To)

    def context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=self.cafile)
        if not self.verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


class Mailbox:
    """One user's IMAP connection, reconnecting when it drops. Thread-safe."""

    def __init__(self, settings: ImapSettings, user: str, password: str):
        self.settings = settings
        self.user = user
        self.password = password
        self.lock = threading.Lock()
        self.conn: imaplib.IMAP4 | None = None
        self.selected: str | None = None
        self.uidvalidity: dict[str, int] = {}
        self.cache: dict[tuple[str, int, int], Message] = {}
        self.last_used = time.time()
        self._folders: tuple[float, list[dict]] | None = None

    def _parse(self, uid: int, raw: bytes, idate: float | None) -> Message:
        msg = parse(uid, raw, idate)
        msg.alias = find_alias(msg.recipients, msg.delivered, self.user, self.settings.alias_domains)
        return msg

    # connection

    def _connect(self) -> imaplib.IMAP4:
        s = self.settings
        try:
            if s.security == "ssl":
                conn = imaplib.IMAP4_SSL(s.host, s.port, ssl_context=s.context(), timeout=TIMEOUT)
            else:
                conn = imaplib.IMAP4(s.host, s.port, timeout=TIMEOUT)
                if s.security == "starttls":
                    conn.starttls(ssl_context=s.context())
        except (OSError, ssl.SSLError, imaplib.IMAP4.error) as e:
            raise MailError(f"cannot reach the mail server ({e})") from e
        try:
            conn.login(self.user, self.password)
        except imaplib.IMAP4.error as e:
            try:
                conn.logout()
            except Exception:
                pass
            raise AuthError("wrong user name or password") from e
        return conn

    def login(self) -> None:
        """Check the credentials (and keep the connection)."""
        with self.lock:
            self._close()
            self.conn = self._connect()

    def _close(self):
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
        self.conn = None
        self.selected = None

    def close(self):
        with self.lock:
            self._close()

    def _run(self, fn):
        """fn(conn) with a live connection; one retry after a dropped one."""
        self.last_used = time.time()
        for attempt in (1, 2):
            if self.conn is None:
                self.conn = self._connect()
            try:
                return fn(self.conn)
            except (imaplib.IMAP4.abort, OSError, socket.timeout, ssl.SSLError) as e:
                self._close()
                if attempt == 2:
                    raise MailError(f"mail server connection lost ({e})") from e

    def _select(self, conn: imaplib.IMAP4, folder: str, readonly: bool = False) -> int:
        typ, data = conn.select(_quote(folder), readonly=readonly)
        if typ != "OK":
            raise MailError(f"no folder {folder!r}")
        self.selected = folder
        typ, data = conn.response("UIDVALIDITY")
        validity = int(data[0]) if data and data[0] else 0
        if self.uidvalidity.get(folder) not in (None, validity):
            self.cache = {k: v for k, v in self.cache.items() if k[0] != folder}
        self.uidvalidity[folder] = validity
        return validity

    # queries

    def folders(self, max_age: float = 300) -> list[dict]:
        if self._folders and time.time() - self._folders[0] < max_age:
            return self._folders[1]

        def run(conn):
            typ, data = conn.list()
            result = []
            for line in data or []:
                if not isinstance(line, bytes):
                    continue
                m = LIST_RE.match(line)
                if not m:
                    continue
                flags = m.group("flags").decode(errors="replace").lower()
                if "\\noselect" in flags:
                    continue
                name = m.group("name").decode("utf-8", "replace")
                if name.startswith('"') and name.endswith('"'):
                    name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                role = next((r for r in ("inbox", "junk", "trash", "sent", "drafts", "archive")
                             if f"\\{r}" in flags), "inbox" if name.upper() == "INBOX" else None)
                result.append({"name": name, "role": role})
            order = {"inbox": 0, "junk": 1}
            result.sort(key=lambda f: (order.get(f["role"], 2), f["name"].lower()))
            return result
        with self.lock:
            result = self._run(run)
        self._folders = (time.time(), result)
        return result

    def recent(self, folder: str = "INBOX", limit: int = 40) -> list[Message]:
        def run(conn):
            validity = self._select(conn, folder, readonly=True)
            typ, data = conn.uid("SEARCH", None, "ALL")
            uids = [int(u) for u in (data[0] or b"").split()] if typ == "OK" else []
            uids = sorted(uids)[-limit:]
            if not uids:
                return []
            flags = self._flags(conn, uids)
            missing = [u for u in uids if (folder, validity, u) not in self.cache]
            for uid, raw, idate in self._bodies(conn, missing):
                msg = self._parse(uid, raw, idate)
                msg.folder = folder
                self.cache[(folder, validity, uid)] = msg
            out = []
            for u in reversed(uids):
                msg = self.cache.get((folder, validity, u))
                if msg is None:
                    continue
                msg.flags = flags.get(u, set())
                msg.seen = "\\Seen" in msg.flags
                out.append(msg)
            self._trim(folder, validity, uids)
            return out
        with self.lock:
            return self._run(run)

    def recent_merged(self, limit: int = 40) -> list[Message]:
        """Inbox and Junk together, newest first: codes often land in Junk."""
        names = [f["name"] for f in self.folders() if f["role"] in ("inbox", "junk")] or ["INBOX"]
        msgs = [m for name in names for m in self.recent(name, limit)]
        msgs.sort(key=lambda m: m.date, reverse=True)
        return msgs[:limit]

    def get(self, folder: str, uid: int, mark_seen: bool = True) -> Message | None:
        def run(conn):
            validity = self._select(conn, folder, readonly=not mark_seen)
            key = (folder, validity, uid)
            if key not in self.cache:
                for u, raw, idate in self._bodies(conn, [uid]):
                    msg = self._parse(u, raw, idate)
                    msg.folder = folder
                    self.cache[(folder, validity, u)] = msg
            msg = self.cache.get(key)
            if msg is not None and mark_seen and not msg.seen:
                conn.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Seen)")
                msg.seen = True
            return msg
        with self.lock:
            return self._run(run)

    def delete(self, folder: str, uid: int) -> None:
        """Move to the Trash folder; delete for good when already there or there is none."""
        trash = next((f["name"] for f in self.folders() if f["role"] == "trash"), None)

        def run(conn):
            self._select(conn, folder)
            if trash and trash != folder:
                typ, data = conn.uid("MOVE", str(uid), _quote(trash))
                if typ == "OK":
                    return
                typ, data = conn.uid("COPY", str(uid), _quote(trash))
                if typ != "OK":
                    raise MailError(f"could not move to {trash}")
            conn.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Deleted)")
            typ, data = conn.uid("EXPUNGE", str(uid))
            if typ != "OK":
                conn.expunge()
        with self.lock:
            self._run(run)

    # helpers

    @staticmethod
    def _flags(conn, uids: list[int]) -> dict[int, set]:
        typ, data = conn.uid("FETCH", ",".join(map(str, uids)), "(UID FLAGS)")
        result = {}
        for item in data or []:
            line = item[0] if isinstance(item, tuple) else item
            if not isinstance(line, bytes):
                continue
            m = re.search(rb"UID (\d+)", line)
            f = re.search(rb"FLAGS \(([^)]*)\)", line)
            if m:
                result[int(m.group(1))] = set(f.group(1).decode().split()) if f else set()
        return result

    @staticmethod
    def _bodies(conn, uids: list[int]):
        for i in range(0, len(uids), 20):
            chunk = uids[i:i + 20]
            typ, data = conn.uid("FETCH", ",".join(map(str, chunk)),
                                 f"(UID INTERNALDATE BODY.PEEK[]<0.{MAX_FETCH}>)")
            if typ != "OK":
                continue
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                head, raw = item[0], item[1]
                m = re.search(rb"UID (\d+)", head)
                if not m:
                    continue
                idate = None
                d = re.search(rb'INTERNALDATE "([^"]+)"', head)
                if d:
                    try:
                        idate = time.mktime(imaplib.Internaldate2tuple(b'INTERNALDATE "' + d.group(1) + b'"'))
                    except Exception:
                        pass
                yield int(m.group(1)), raw, idate

    def _trim(self, folder: str, validity: int, keep: list[int]):
        """Forget cached messages of this folder that dropped out of the window."""
        if len(self.cache) < 200:
            return
        keep_set = set(keep)
        self.cache = {k: v for k, v in self.cache.items() if k[0] != folder or k[2] in keep_set}

