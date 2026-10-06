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


async def test_a_long_link_in_an_outbox_file_can_be_copied_as_it_is(tmp_path):
    link = "http://localhost:8000/confirm-email?token=" + "eyJhIjozLCJ0IjoxNzkxMjU1NTI0LCJkIjp7ImVtYWlsIjoi" * 3 + ".n383LrupjPZ_-sS3eGhuoX4Q1Dk"
    mailer = OutboxMailer(tmp_path)
    await mailer.send(Mail(to="mike@example.com", subject="Confirm", body=f"Open this:\n{link}\n"))
    [saved] = list(tmp_path.glob("*.eml"))
    assert link.encode() in saved.read_bytes(), "not wrapped, and no =3D for ="
    assert email.message_from_bytes(saved.read_bytes()).get_payload().strip().endswith(link)


async def test_mail_sent_by_smtp_is_still_encoded_for_the_wire():
    from terraforma.mail import _message

    long_line = "http://example.com/?token=" + "x" * 120
    assert "quoted-printable" in _message(Mail(to="a@b.c", subject="s", body=long_line), "n@x.y")["Content-Transfer-Encoding"]


def test_with_mail_settings_messages_go_by_smtp():
    mail = MailSettings(host="smtp.example.com", username="k", password="k", from_address="a@b.c")
    assert isinstance(default_mailer(Settings(session_secret="x" * 32, mail=mail)), SmtpMailer)
