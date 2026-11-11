import pytest

from thinmail.codes import find_code

CASES = [
    # (subject, body, expected)
    ("Your GitHub launch code", "Here is your GitHub launch code: 48291731\n\nIf you didn't...", "48291731"),
    ("Sign in to Discord", "Your verification code is 482913. It expires in 10 minutes.", "482913"),
    ("482913 is your Instagram code", "Someone tried to log in.", "482913"),
    ("Ihr Bestätigungscode", "Hallo,\n\nIhr Bestätigungscode lautet:\n\n  731 882\n\nGültig bis 14:05 Uhr.", "731 882"),
    ("Google verification", "G-482913 is your Google verification code.", "G-482913"),
    ("Steam Guard", "Login Code\n\nK7Q2M\n\nThis is the code you need.", "K7Q2M"),
    ("Your one-time passcode", "Use 0042 to sign in.", "0042"),
    ("Security code", "Your Microsoft account security code is 6 4 ... no wait: 553201", "553201"),
    ("Order 123456 shipped", "Your order #123456 is on its way. Tracking 8823991.", None),
    ("Invoice", "Invoice no. 20261103, amount 129.00 EUR, due 2026-11-20.", None),
    ("Meeting", "See you at 10:30 on 11.11.2026, room 4012? Call +49 151 2345678.", None),
    ("Newsletter", "Read more at https://example.com/a/482913/verify?code=123456", None),
    ("Hi", "Just text, from 2026 with 3000 users.", None),
    ("Your code", "Your code: ABCDEF (letters only should not count)", None),
]


@pytest.mark.parametrize("subject,body,expected", CASES)
def test_find_code(subject, body, expected):
    assert find_code(subject, body) == expected


def test_prefers_code_near_keyword():
    body = "Account 99881234\n\nYour verification code is 551177.\nCustomer ID 7766554"
    assert find_code("Verify", body) == "551177"


def test_empty():
    assert find_code("", "") is None
    assert find_code(None, None) is None
