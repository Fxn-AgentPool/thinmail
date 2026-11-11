"""Find the one-time code (2FA/OTP/verification code) in a mail, if there is one.

Heuristic, tuned for the usual senders: a 4-8 digit number (or a short
letters+digits code like ``G-482913`` or ``K7Q-2MX``) close to a word such as
"code", "verification", "PIN" or "Bestätigungscode". Dates, times, amounts,
phone numbers, order numbers and anything inside a URL are skipped.
"""

from __future__ import annotations

import re

KEYWORDS = re.compile(
    r"\b(?:codes?|passcode|pass\s*code|verification|verify|verifizierung\w*|otp|one[- ]time|"
    r"pin|tan|2fa|two[- ]factor|mfa|security|sicherheit\w*|token|login|log[- ]in|sign[- ]in|"
    r"anmelde\w*|best[äa]tigung\w*|einmal\w*|authenticat\w*|authentifizier\w*|confirm\w*)\b"
    r"|\w*codes?\b|\w*kennwort\b",
    re.IGNORECASE,
)
# Words before a number that make it something else.
NOT_A_CODE = re.compile(
    r"(?:order|bestell\w*|invoice|rechnung\w*|ticket|case|kunden\w*|customer|account|konto|"
    r"tracking|sendung\w*|ref(?:erence)?|nr|no|number|nummer|id|tel|phone|telefon|fax|mobil|"
    r"plz|zip|street|str|iban|bic|blz|amount|betrag|eur|usd|chf|gbp|version|v)\W{0,3}$",
    re.IGNORECASE,
)

URL = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+\.\w+", re.IGNORECASE)
CANDIDATE = re.compile(
    r"(?<![\w.,:/#+\-])"
    r"("
    r"\d{3}[ -]\d{3}"                                  # 123 456 / 123-456
    r"|\d{4,8}"                                        # 482913
    r"|(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]{1,4}-[A-Z0-9]{3,8}"  # G-482913, K7Q-2MX
    r"|(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,8}"  # K7Q2MX
    r")"
    r"(?![\w,/#\-]|[.:]\d)",
)
YEAR = re.compile(r"(?:19|20)\d\d")


def _score(text: str, m: re.Match, subject_end: int) -> int:
    code = m.group(1)
    start, end = m.span(1)
    before = text[max(0, start - 80):start]
    after = text[end:end + 40]
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start:line_end if line_end >= 0 else len(text)]
    digits = code.isdigit()

    score = 0
    if digits:
        score += {4: 2, 5: 2, 6: 3, 7: 2, 8: 2}.get(len(code), 0)
    elif re.fullmatch(r"\d{3}[ -]\d{3}", code):
        score += 3
    else:
        # Letters and digits: likelier a code the more digits it has.
        score += 2 if sum(c.isdigit() for c in code) >= 4 else 1

    near = before[-60:]
    if KEYWORDS.search(near):
        score += 4
        # "Your code: 123456" / "code is 123456" right before it.
        if re.search(r"(?:code|pin|tan|otp|passcode|token)\w*\W{0,3}(?:is|lautet|ist|:)?\W{0,3}$", before, re.I):
            score += 2
    elif KEYWORDS.search(after):          # "123456 is your verification code"
        score += 4
    if line.strip() == code:              # alone on its line
        score += 2
    if start < subject_end:               # in the subject
        score += 2

    if NOT_A_CODE.search(before):
        score -= 6
    if digits and len(code) == 4 and YEAR.fullmatch(code):
        score -= 3
    if re.match(r"\s*(?:€|\$|eur|usd|%|kb|mb|gb|px|uhr|am|pm|h\b|min)", after, re.I):
        score -= 6
    if re.search(r"[€$£]\s*$", before):
        score -= 6
    if not digits and code.isalpha():
        score -= 10
    return score


def find_code(subject: str, text: str) -> str | None:
    """The most likely one-time code in a mail's subject and text, or None."""
    subject = subject or ""
    body = text or ""
    combined = subject + "\n\n" + body
    # Blank out URLs and mail addresses so numbers in them never match,
    # keeping offsets intact.
    combined = URL.sub(lambda m: " " * len(m.group(0)), combined)
    best: tuple[int, int, str] | None = None
    for i, m in enumerate(CANDIDATE.finditer(combined)):
        s = _score(combined, m, len(subject))
        if s >= 6 and (best is None or s > best[0]):
            best = (s, -i, m.group(1))
    return best[2] if best else None
