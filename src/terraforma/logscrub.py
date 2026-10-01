"""Scrubbing secrets and personal details out of every log line.

Installed once by the app: every log record, from any library (uvicorn's
access log included), has email addresses, passwords, tokens and session
cookies replaced before it's written anywhere.
"""

import logging
import re

REDACTED = "[scrubbed]"

PATTERNS = [
    # email addresses
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), REDACTED),
    # key=value pairs for anything secret, in query strings, forms or text
    (re.compile(r"(?i)\b(password|passwd|token|secret|csrf[_-]?token|code|otp|session)=([^&\s\"']+)"), r"\1=" + REDACTED),
    # "key": "value" in JSON
    (re.compile(r'(?i)("(?:password|token|secret|csrf_token|code|otp)"\s*:\s*)"[^"]*"'), r'\1"' + REDACTED + '"'),
    # headers carrying credentials
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie|x-csrf-token)(\s*[:=]\s*)\S+"), r"\1\2" + REDACTED),
    # anything that looks like a long random token on its own
    (re.compile(r"\b[A-Za-z0-9_-]{32,}(?:\.[A-Za-z0-9_-]{8,})*\b"), REDACTED),
]


def scrub(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


_installed = False


def install() -> None:
    """Scrubs every log record from now on (safe to call more than once)."""
    global _installed
    if _installed:
        return
    make_record = logging.getLogRecordFactory()

    def scrubbed_record(*args, **kwargs):
        record = make_record(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:
            return record
        record.msg = scrub(message)
        record.args = None
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return record

    logging.setLogRecordFactory(scrubbed_record)
    _installed = True
