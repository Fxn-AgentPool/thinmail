"""End-to-end smoke test against a running thinmail (see CLAUDE.md, "Integration test").

    python tests/http_smoke.py http://127.0.0.1:18088 USER PASSWORD
"""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

B, USER, PW = sys.argv[1:4]
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def req(path, data=None, origin=True, ctype=None):
    r = urllib.request.Request(B + path, data=data)
    if origin:
        r.add_header("Origin", B)
    if ctype:
        r.add_header("Content-Type", ctype)
    try:
        resp = op.open(r)
        return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        check.failed = True


check.failed = False
form = lambda **k: urllib.parse.urlencode(k).encode()

s, h, b = req("/")
check("unauthenticated / redirects to login", s == 200 and b"Sign in" in b, s)
s, h, b = req("/api/messages")
check("unauthenticated API is 401", s == 401, s)
s, h, b = req("/login", form(user=USER, password="wrong"))
check("wrong password rejected", s == 401 and b"Wrong user name" in b, s)
s, h, b = req("/login", form(user=USER, password=PW), origin=False)
check("login without Origin refused", s == 403, s)
s, h, b = req("/login", form(user=USER, password=PW))
check("login", s == 200 and any(c.name == "thinmail" for c in jar), s)
cookie = next(c for c in jar if c.name == "thinmail")
check("cookie HttpOnly + SameSite", cookie.has_nonstandard_attr("HttpOnly") and cookie.get_nonstandard_attr("SameSite") == "Strict")

s, h, b = req("/api/folders")
folders = json.loads(b)["folders"]
check("folders", s == 200 and any(f["role"] == "inbox" for f in folders), [f["name"] for f in folders])

s, h, b = req("/api/messages")
msgs = json.loads(b)["messages"]
for m in msgs:
    print(f'     {m["folder"]:<10} uid={m["uid"]:<3} seen={m["seen"]!s:<5} code={m["code"]!s:<9} link={m["link"]} | {m["subject"]}')
by = {m["subject"]: m for m in msgs}
expect = {
    "[GitHub] Please verify your device": "482917",
    "Your Discord login code": "731 882",
    "Ihr Bestätigungscode": "551209",
    "G-604311 is your Google verification code": "G-604311",
    "Order 123456 shipped": None,
    "Confirm your email address": None,
}
for subj, code in expect.items():
    check(f"code for {subj!r}", subj in by and by[subj]["code"] == code, by.get(subj, {}).get("code"))
check("magic link found", by.get("Confirm your email address", {}).get("link") == "https://slack.com/magic/abc123")
check("newest first", [m["date"] for m in msgs] == sorted([m["date"] for m in msgs], reverse=True))

d = by["Your Discord login code"]
path = f'/api/messages/{d["uid"]}?folder={urllib.parse.quote(d["folder"])}'
s, h, b = req(path)
full = json.loads(b)
check("open HTML-only mail", s == 200 and full["hasHtml"] and "731 882" in full["text"], s)
s, h, b = req(f'/api/messages/{d["uid"]}/html?folder={urllib.parse.quote(d["folder"])}')
csp = h.get("Content-Security-Policy", "")
check("HTML view sandboxed, no remote images", s == 200 and "sandbox" in csp and "img-src data:;" in csp and b"<base target" in b, csp)
s, h, b = req(f'/api/messages/{d["uid"]}/html?folder={urllib.parse.quote(d["folder"])}&images=1')
check("HTML view with images", "https:" in h.get("Content-Security-Policy", ""))
s, h, b = req("/api/messages")
check("opened mail is marked seen", any(m["uid"] == d["uid"] and m["folder"] == d["folder"] and m["seen"] for m in json.loads(b)["messages"]))

o = by["Order 123456 shipped"]
s, h, b = req(f'/api/messages/{o["uid"]}/delete', json.dumps({"folder": o["folder"]}).encode(), origin=False, ctype="application/json")
check("delete without Origin refused", s == 403, s)
s, h, b = req(f'/api/messages/{o["uid"]}/delete', json.dumps({"folder": o["folder"]}).encode(), ctype="application/json")
check("delete", s == 200, b)
s, h, b = req("/api/messages")
check("deleted mail gone from list", all((m["folder"], m["uid"]) != (o["folder"], o["uid"]) for m in json.loads(b)["messages"]))
trash = next((f["name"] for f in folders if f["role"] == "trash"), None)
if trash:
    s, h, b = req("/api/messages?folder=" + urllib.parse.quote(trash))
    check("deleted mail is in Trash", any(m["subject"] == o["subject"] for m in json.loads(b)["messages"]), trash)

s, h, b = req("/static/app.js")
check("static served", s == 200 and h["Content-Type"].startswith("text/javascript"))
s, h, b = req("/static/../web.py")
check("no path traversal", s == 404, s)
s, h, b = req("/logout", b"")
check("logout", s == 200 and b"Sign in" in b, s)
s, h, b = req("/api/messages")
check("API after logout is 401", s == 401, s)
sys.exit(1 if check.failed else 0)
