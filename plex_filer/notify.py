"""One summary email per run. Never lets a mail problem affect filing."""
from __future__ import annotations

import logging
import os
import smtplib
import socket
from dataclasses import dataclass, field
from email.message import EmailMessage

from .config import Config

log = logging.getLogger("plex-filer")


@dataclass
class Notifier:
    cfg: Config
    filed: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ collection
    def add_filed(self, label: str) -> None:
        self.filed.append(label)

    def add_review(self, rel: str, reason: str) -> None:
        self.review.append(f"{rel}  ({reason})")

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    # ---------------------------------------------------------------- compose
    @property
    def has_content(self) -> bool:
        return bool(self.filed or self.review or self.errors)

    def build(self) -> tuple[str, str]:
        problems = len(self.review) + len(self.errors)
        if problems:
            subject = f"Plex: ATTENTION - {len(self.errors)} error(s), {len(self.review)} for review"
            if self.filed:
                subject += f", {len(self.filed)} filed"
        else:
            subject = f"Plex: {len(self.filed)} new item{'s' if len(self.filed) != 1 else ''} filed"

        parts: list[str] = []
        if self.errors:
            parts.append("ERRORS\n" + "\n".join(f"  ! {e}" for e in self.errors))
        if self.review:
            parts.append(f"NEEDS REVIEW (in {self.cfg.review_dir})\n"
                         + "\n".join(f"  ? {r}" for r in self.review))
        if self.filed:
            parts.append("NEW CONTENT\n" + "\n".join(f"  + {f}" for f in self.filed))
        parts.append(f"-- plex-filer on {socket.gethostname()}")
        return subject, "\n\n".join(parts)

    # ------------------------------------------------------------------ send
    def send(self, force_subject: str | None = None, force_body: str | None = None) -> bool:
        """Returns True if a mail was sent. Swallows and logs all failures."""
        cfg = self.cfg
        if not cfg.notify_enabled:
            return False
        if force_subject is None and not self.has_content:
            return False  # quiet runs stay quiet

        subject, body = (force_subject, force_body) if force_subject else self.build()
        user = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_PASSWORD")
        if not (user and password):
            log.warning("Email not configured (SMTP_USER / SMTP_PASSWORD not set); not sent")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.mail_from or user
        msg["To"] = cfg.mail_to or user
        msg.set_content(body)
        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=15) as s:
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            log.error("Could not send notification email: %s", e)
            return False
        log.info("Notification email sent: %s", subject)
        return True
