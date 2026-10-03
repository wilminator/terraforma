"""Logs never carry passwords, tokens, cookies or email addresses."""

import logging

import pytest

from terraforma import logscrub


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ("login for mike@example.com failed", "mike@example.com"),
        ('POST /api/confirm-email?token=eyJhIjoxLCJ0IjoxNzAw.abcdefgh HTTP/1.1" 200', "eyJhIjoxLCJ0IjoxNzAw"),
        ('body {"username": "Mike", "password": "correct horse battery"}', "correct horse battery"),
        ("cookie: terraforma_session=abc.def.ghi", "abc.def.ghi"),
        ("X-CSRF-Token: Zm9vYmFyYmF6cXV4", "Zm9vYmFyYmF6cXV4"),
        ("Authorization: Bearer s3cret-shop-key", "s3cret-shop-key"),
        ("password=hunter2&next=/", "hunter2"),
        ("issued A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
    ],
)
def test_secrets_are_scrubbed(line, secret):
    scrubbed = logscrub.scrub(line)
    assert secret not in scrubbed
    assert logscrub.REDACTED in scrubbed


def test_ordinary_lines_are_left_alone():
    line = 'GET /api/fights/7 HTTP/1.1" 200 OK'
    assert logscrub.scrub(line) == line


def test_every_logger_is_scrubbed_once_installed(caplog):
    logscrub.install()
    logscrub.install()  # twice is harmless
    with caplog.at_level(logging.INFO):
        logging.getLogger("uvicorn.access").info('%s - "GET /reset-password?token=%s HTTP/1.1" 200', "1.2.3.4", "secretsecretsecretsecretsecret12")
        logging.getLogger("anything").warning("mail to %s bounced", "mike@example.com")
    text = caplog.text
    assert "secretsecretsecretsecretsecret12" not in text
    assert "mike@example.com" not in text
    assert "1.2.3.4" in text, "the rest of the line is kept"
