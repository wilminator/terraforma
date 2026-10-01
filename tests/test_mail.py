"""Mail: the development outbox, and which mailer the settings choose."""

import email

import pytest

from terraforma.app import default_mailer
from terraforma.mail import Mail, OutboxMailer, SmtpMailer
from terraforma.settings import MailSettings, Settings

pytestmark = pytest.mark.anyio


async def test_without_mail_settings_messages_land_in_the_outbox(tmp_path):
    mailer = default_mailer(Settings(session_secret="x" * 32, outbox_dir=tmp_path / "outbox"))
    assert isinstance(mailer, OutboxMailer)
    await mailer.send(Mail(to="mike@example.com", subject="Hello", body="A link: http://x/confirm-email?token=abc"))
    [saved] = list((tmp_path / "outbox").glob("*.eml"))
    message = email.message_from_bytes(saved.read_bytes())
    assert message["To"] == "mike@example.com" and message["Subject"] == "Hello"
    assert "token=abc" in message.get_payload()


def test_with_mail_settings_messages_go_by_smtp():
    mail = MailSettings(host="smtp.example.com", username="k", password="k", from_address="a@b.c")
    assert isinstance(default_mailer(Settings(session_secret="x" * 32, mail=mail)), SmtpMailer)
