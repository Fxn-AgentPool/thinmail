from email.message import EmailMessage

from thinmail.mail import action_link, html_to_text, parse, safe_html


def build(subject, text=None, html=None):
    m = EmailMessage()
    m["From"] = '"Service Team" <team@service.example>'
    m["Subject"] = subject
    m["Date"] = "Wed, 11 Nov 2026 16:00:00 +0100"
    if text is not None:
        m.set_content(text)
        if html is not None:
            m.add_alternative(html, subtype="html")
    else:
        m.set_content(html, subtype="html")
    return m.as_bytes()


def test_plain():
    msg = parse(3, build("Your code", "Your verification code is 123456."))
    assert (msg.uid, msg.sender, msg.address) == (3, "Service Team", "team@service.example")
    assert msg.code == "123456" and msg.html is None
    assert msg.date == 1794409200.0


def test_html_only_code_and_text():
    html = "<html><head><style>.x{}</style></head><body><p>Your code</p><h1>904 112</h1><script>bad()</script></body></html>"
    msg = parse(1, build("Login", html=html))
    assert msg.code == "904 112"
    assert "bad()" not in msg.text and ".x{}" not in msg.text
    assert msg.html is not None


def test_link_from_html_anchor():
    msg = parse(1, build("Welcome", "Open the app.", "<a href='https://ex.example/x?t=1'>Confirm email</a>"))
    assert msg.link == "https://ex.example/x?t=1"


def test_no_link_for_plain_newsletter():
    msg = parse(1, build("News", "Read our blog at https://blog.example/post"))
    assert msg.link is None


def test_link_in_plain_text_after_keyword():
    assert action_link([], "To sign in, open\nhttps://ex.example/magic/abc") == "https://ex.example/magic/abc"


def test_html_to_text_links():
    text, links = html_to_text("<p>Hi&nbsp;there</p><a href='https://a.example'>Verify</a>")
    assert text == "Hi there\nVerify"
    assert links == [("https://a.example", "Verify")]


def test_broken_mail_does_not_raise():
    msg = parse(9, b"Subject: =?bogus?Q?x?=\r\nContent-Type: text/plain; charset=nonsense\r\n\r\ncode 4471")
    assert msg.uid == 9


def test_safe_html_adds_base():
    assert safe_html("<html><head></head><body>x</body></html>").startswith('<html><head><base target="_blank">')
    assert safe_html("<p>x</p>").startswith('<base target="_blank">')
