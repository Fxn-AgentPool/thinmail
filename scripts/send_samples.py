#!/usr/bin/env python3
"""Deliver sample 2FA mails over SMTP (for trying thinmail against a test server).

    scripts/send_samples.py HOST:PORT RECIPIENT [--only N] [--aliases]

--aliases sends each sample to <service>@<recipient's domain> (e.g. github@test.lan),
as if you had signed up there with a catch-all address.
"""
import argparse
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SAMPLES = [
    ("GitHub <noreply@github.com>", "[GitHub] Please verify your device",
     "Hey there!\n\nA sign in attempt requires further verification.\n\nVerification code: 482917\n\nIf you did not attempt to sign in, change your password.", None),
    ("Discord <noreply@discord.com>", "Your Discord login code", None,
     "<html><head><style>p{font-family:sans-serif}</style></head><body><p>Here is your code:</p>"
     "<h1 style='letter-spacing:4px'>731 882</h1><p>It expires in 10 minutes.</p>"
     "<img src='https://tracker.example.com/open.gif'></body></html>"),
    ("Sparkasse <service@sparkasse.example>", "Ihr Bestätigungscode",
     "Guten Tag,\n\nIhr Bestätigungscode lautet: 55 12 09? Nein: 551209\n\nGültig bis 14:05 Uhr.\nKundennummer 88812345", None),
    ("Slack <no-reply@slack.com>", "Confirm your email address",
     "Click the link below to sign in.\nhttps://slack.com/magic/abc123",
     "<p>Click below to sign in.</p><p><a href='https://slack.com/magic/abc123'>Sign in to Slack</a></p>"),
    ("Shop <orders@shop.example>", "Order 123456 shipped",
     "Your order #123456 is on its way. Total 49.99 EUR. Track: https://shop.example/t/998877", None),
    ("Google <no-reply@accounts.google.com>", "G-604311 is your Google verification code",
     "G-604311 is your Google verification code.", None),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("server")
    p.add_argument("rcpt")
    p.add_argument("--only", type=int)
    p.add_argument("--aliases", action="store_true")
    a = p.parse_args()
    host, _, port = a.server.rpartition(":")
    with smtplib.SMTP(host, int(port), timeout=20) as s:
        s.ehlo("sender.example")
        for i, (frm, subject, text, html) in enumerate(SAMPLES):
            if a.only is not None and i != a.only:
                continue
            rcpt = a.rcpt
            if a.aliases:
                service = frm.split("<")[0].strip().split()[0]          # "GitHub", "Discord", ...
                rcpt = f"{service}@{a.rcpt.rpartition('@')[2]}"
            m = EmailMessage()
            m["From"], m["To"], m["Subject"] = frm, rcpt, subject
            m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid("thinmail-test")
            if text is not None:
                m.set_content(text)
                if html is not None:
                    m.add_alternative(html, subtype="html")
            else:
                m.set_content(html, subtype="html")
            s.send_message(m, to_addrs=[rcpt])
            print("sent", subject, "->", rcpt)


if __name__ == "__main__":
    main()
