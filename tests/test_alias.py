from email.message import EmailMessage

import pytest

from thinmail.mail import find_alias, parse

ME = ["me@example.org"]          # Stalwart's Delivered-To: the catch-all's target


@pytest.mark.parametrize("to,expected", [
    (["Discord@example.org"], "discord"),                 # catch-all, case folded
    (["microsoft@example.org"], "microsoft"),
    (["me+github@example.org"], "github"),                # plus addressing
    (["me@example.org"], None),                           # main address
    (["friend@other.example", "shop@example.org"], "shop"),
    (["undisclosed-recipients@unknown"], None),           # Bcc / newsletter
    ([], None),
    (["someone@other.example"], None),                    # not our domain
])
def test_find_alias(to, expected):
    assert find_alias(to, ME) == expected


def test_domains_from_login_and_config_without_delivered_to():
    assert find_alias(["discord@example.org"], [], login="me@example.org") == "discord"
    assert find_alias(["discord@example.org"], [], login="me") is None
    assert find_alias(["discord@example.org"], [], login="me", domains=("example.org",)) == "discord"
    assert find_alias(["x@alias.example"], ME, domains=("@Alias.Example",)) == "x"


def test_parse_collects_recipients():
    m = EmailMessage()
    m["From"], m["Subject"] = "Discord <noreply@discord.com>", "code"
    m["To"] = "Discord <Discord@example.org>"
    m["Cc"] = "other@else.example"
    m["Delivered-To"] = "me@example.org"
    m.set_content("Your code is 112233")
    msg = parse(1, m.as_bytes())
    assert msg.recipients == ["discord@example.org", "other@else.example"]
    assert msg.delivered == ["me@example.org"]
    assert find_alias(msg.recipients, msg.delivered) == "discord"


def test_undisclosed_recipients_header():
    m = EmailMessage()
    m["From"], m["Subject"], m["To"] = "a@b.example", "news", "undisclosed-recipients:;"
    m["Delivered-To"] = "me@example.org"
    m.set_content("x")
    msg = parse(1, m.as_bytes())
    assert find_alias(msg.recipients, msg.delivered) is None
