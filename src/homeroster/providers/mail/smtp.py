"""SMTP mailer (works with Gmail app passwords, Fastmail, Proton Bridge, a self-hosted relay...).

Options (``mail:`` section): host, port (465 = implicit TLS, 587 = STARTTLS), username, password,
from_name, from_address (default: username), admin_email (alerts; default: from_address).
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from ..base import Health, ServiceError
from ..registry import register_mail
from .base import InlineImage, Mailer

# The tests replace this with a function that captures messages instead of sending them.
SENDER: Callable[[EmailMessage], None] | None = None


@register_mail("smtp")
class SmtpMailer(Mailer):
    label = "E-mail (SMTP)"

    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.host = str(options.get("host") or "")
        self.port = int(options.get("port") or 465)
        self.username = str(options.get("username") or "")
        self.password = str(options.get("password") or "").replace(" ", "")
        self.from_address = str(options.get("from_address") or self.username)
        self.from_name = str(options.get("from_name") or "HomeRoster")

    @property
    def admin_address(self) -> str:
        return str(self.options.get("admin_email") or self.from_address)

    def configured(self) -> bool:
        return bool(self.host and self.from_address and (self.password or not self.username))

    def _connect(self) -> smtplib.SMTP:
        context = ssl.create_default_context()
        if self.port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=20)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
            server.starttls(context=context)
        if self.username:
            server.login(self.username, self.password)
        return server

    def _send_sync(self, message: EmailMessage) -> None:
        if SENDER is not None:
            SENDER(message)
            return
        with self._connect() as server:
            server.send_message(message)

    def build(self, to: str, subject: str, html: str, text: str,
              images: list[InlineImage] | tuple[InlineImage, ...] = ()) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((self.from_name, self.from_address))
        message["To"] = to
        message["Message-ID"] = make_msgid(domain=self.from_address.rsplit("@", 1)[-1] or None)
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        html_part = message.get_payload()[-1]
        for image in images:
            html_part.add_related(image.data, maintype="image", subtype=image.subtype, cid=f"<{image.cid}>",
                                  filename=f"{image.cid}.{image.subtype}", disposition="inline")
        return message

    async def send(self, to: str, subject: str, html: str, text: str,
                   images: list[InlineImage] | tuple[InlineImage, ...] = ()) -> None:
        if not self.configured():
            raise ServiceError(self.label, "errors.not_configured")
        message = self.build(to, subject, html, text, images)
        try:
            await asyncio.to_thread(self._send_sync, message)
        except smtplib.SMTPAuthenticationError as e:
            raise ServiceError(self.label, "errors.smtp_auth") from e
        except (smtplib.SMTPException, OSError) as e:
            raise ServiceError(self.label, "errors.smtp_send", kind=e.__class__.__name__) from e

    async def health(self) -> Health:
        if not self.configured():
            return Health.from_error(ServiceError(self.label, "errors.not_configured"))
        if SENDER is not None:
            return Health(ok=True, message="", version=self.host)
        try:
            await asyncio.to_thread(lambda: self._connect().quit())
            return Health(ok=True, message="", version=self.host)
        except smtplib.SMTPAuthenticationError:
            return Health.from_error(ServiceError(self.label, "errors.smtp_auth"))
        except (smtplib.SMTPException, OSError) as e:
            return Health.from_error(ServiceError(self.label, "errors.smtp_send", kind=e.__class__.__name__))
