"""Sending mail.

With a ``[mail]`` section in the settings, mail goes out over SMTP (for
Cloudflare: port 465, TLS from the first byte). Without one, each message
is written to the ``outbox/`` folder as a .eml file instead, for
development. Tests use MemoryMailer.
"""

from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

import aiosmtplib

from . import wallclock
from .settings import MailSettings


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    async def send(self, mail: Mail) -> None: ...


def _message(mail: Mail, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = mail.to
    message["Subject"] = mail.subject
    message.set_content(mail.body)
    return message


class SmtpMailer:
    def __init__(self, settings: MailSettings):
        self.settings = settings

    async def send(self, mail: Mail) -> None:
        await aiosmtplib.send(
            _message(mail, self.settings.from_address),
            hostname=self.settings.host,
            port=self.settings.port,
            username=self.settings.username,
            password=self.settings.password,
            use_tls=self.settings.implicit_tls,
            start_tls=not self.settings.implicit_tls,
            timeout=30,
        )


class OutboxMailer:
    """Development: each message becomes a file in $folder."""

    def __init__(self, folder: Path, sender: str = "TerraForma <noreply@localhost>"):
        self.folder = folder
        self.sender = sender

    async def send(self, mail: Mail) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{wallclock.now():%Y%m%d-%H%M%S-%f}.eml"
        (self.folder / name).write_bytes(bytes(_message(mail, self.sender)))


@dataclass
class MemoryMailer:
    """Tests: keeps what was sent."""

    sent: list[Mail] = field(default_factory=list)

    async def send(self, mail: Mail) -> None:
        self.sent.append(mail)

    def last_to(self, address: str) -> Mail:
        for mail in reversed(self.sent):
            if mail.to == address:
                return mail
        raise LookupError(f"no mail to {address}")
