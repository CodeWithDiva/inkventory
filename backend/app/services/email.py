"""Outgoing email. EMAIL_BACKEND=console logs emails (development); smtp sends them.

Works with any SMTP provider (Brevo, Resend, Amazon SES, Gmail app passwords, ...).
"""

import html
import logging
import re
import smtplib
import threading
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger("app.email")

# Last messages sent with the console backend (handy in tests and local development).
outbox: list[dict] = []


def _layout(title: str, body_html: str, button: tuple[str, str] | None = None) -> str:
    settings = get_settings()
    button_html = ""
    if button:
        label, url = button
        button_html = (
            f'<p><a href="{html.escape(url)}" style="background:#4f46e5;color:#fff;padding:10px 18px;'
            f'border-radius:8px;text-decoration:none;display:inline-block">{html.escape(label)}</a></p>'
        )
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;color:#1a1d23">'
        f"<h2>{html.escape(title)}</h2>{body_html}{button_html}"
        f'<p style="color:#6b7280;font-size:12px">{html.escape(settings.app_name)}</p></div>'
    )


def send_email(to: str, subject: str, title: str, body_html: str, button: tuple[str, str] | None = None) -> None:
    """Send in the background so requests never wait on (or fail because of) the mail server."""
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to
    message["Subject"] = subject
    plain_body = re.sub(r"<[^>]+>", "", re.sub(r"</p>|<br\s*/?>", "\n", body_html))
    text = f"{title}\n\n{html.unescape(plain_body).strip()}\n"
    if button:
        text += f"\n{button[0]}: {button[1]}"
    message.set_content(text)
    message.add_alternative(_layout(title, body_html, button), subtype="html")

    if settings.email_backend != "smtp":
        outbox.append({"to": to, "subject": subject, "body": text})
        del outbox[:-50]
        log.info("EMAIL (console backend) to=%s subject=%s\n%s", to, subject, text)
        return

    def deliver() -> None:
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        except Exception:
            log.exception("Failed to send email to %s (%s)", to, subject)

    threading.Thread(target=deliver, daemon=True).start()


# ---------- Templates ----------
def password_reset(to: str, name: str, link: str) -> None:
    send_email(
        to, "Reset your password", "Reset your password",
        f"<p>Hi {html.escape(name)},</p><p>We received a request to reset your password. "
        "This link is valid for 1 hour. If you did not ask for this, you can ignore this email.</p>",
        ("Choose a new password", link),
    )


def welcome(to: str, name: str, org: str) -> None:
    settings = get_settings()
    send_email(
        to, f"Welcome to {settings.app_name}", f"Welcome, {name}!",
        f"<p>Your workspace <b>{html.escape(org)}</b> is ready. Upload a product CSV to generate "
        "SEO-optimized descriptions in minutes.</p>",
        ("Open your workspace", settings.app_url),
    )


def team_invite(to: str, name: str, org: str, invited_by: str) -> None:
    settings = get_settings()
    send_email(
        to, f"You were added to {org}", f"You've been added to {org}",
        f"<p>Hi {html.escape(name)},</p><p>{html.escape(invited_by)} added you to <b>{html.escape(org)}</b> "
        f"on {html.escape(settings.app_name)}. Sign in with this email address and the temporary password "
        "they shared with you, then change it under Settings.</p>",
        ("Sign in", f"{settings.app_url}/login"),
    )


def upgrade_requested(to: str, org: str, name: str, requester_email: str, plan: str, message: str) -> None:
    settings = get_settings()
    note = f"<p>Message: {html.escape(message)}</p>" if message.strip() else ""
    send_email(
        to, f"Upgrade request: {org} → {plan}", "New upgrade request",
        f"<p><b>{html.escape(org)}</b> wants the <b>{html.escape(plan)}</b> plan.</p>"
        f"<p>Requested by {html.escape(name)} ({html.escape(requester_email)}).</p>{note}"
        "<p>Send them payment details; once paid, activate the plan in Platform admin.</p>",
        ("Open Platform admin", f"{settings.app_url}/admin"),
    )


def job_finished(to: str, name: str, catalog: str, catalog_id: int, completed: int, failed: int, status: str) -> None:
    settings = get_settings()
    outcome = f"{completed} descriptions are ready for review"
    if failed:
        outcome += f"; {failed} failed (see the job history for details)"
    send_email(
        to, f"Descriptions ready: {catalog}", f"Generation {status}",
        f"<p>Hi {html.escape(name)},</p><p>Your job for <b>{html.escape(catalog)}</b> has {status}. "
        f"{html.escape(outcome)}.</p>",
        ("Review descriptions", f"{settings.app_url}/catalogs/{catalog_id}"),
    )
