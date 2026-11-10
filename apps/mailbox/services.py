"""Sending client emails from the agent's own mailbox, always CC'ing the Director's address, and reading
that same mailbox's real inbox and drafts over IMAP (stdlib imaplib/email - no extra dependency)."""

from __future__ import annotations

import imaplib
from email import message_from_bytes
from email.header import decode_header
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from .models import EmailTemplate, Mailbox, MailSettings, SentEmail

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
DRAFT_FOLDER_CANDIDATES = ["Drafts", "INBOX.Drafts", "[Gmail]/Drafts", "INBOX/Drafts"]


class MailError(Exception):
    pass


class _Blank(dict):
    def __missing__(self, key):
        return ""


def merge_fields(lead=None, user=None) -> dict:
    client = getattr(lead, "client", None)
    employee = getattr(user, "employee", None)
    full = getattr(client, "full_name", "") or ""
    return {
        "client_name": full, "client_first_name": full.split(" ")[0] if full else "",
        "agent_name": getattr(employee, "full_name", "") or (user.full_name if user else ""),
        "agent_phone": getattr(employee, "phone", "") or "",
        "destination": (lead.interest_label if lead is not None else "") or "your safari",
        "reference": getattr(lead, "reference", "") or "",
        "company": "Pocket of Paradise Travel Company",
    }


def render_template(template: EmailTemplate, lead=None, user=None) -> tuple[str, str]:
    fields = _Blank(merge_fields(lead, user))
    return template.subject.format_map(fields), template.body.format_map(fields)


def _connection(box: Mailbox):
    return get_connection(
        settings.AGENT_MAIL_BACKEND, host=box.smtp_host, port=box.smtp_port, username=box.username or box.email,
        password=box.password, use_ssl=box.security == Mailbox.Security.SSL, use_tls=box.security == Mailbox.Security.STARTTLS,
        timeout=20, fail_silently=False)


def mailbox_for(user) -> Mailbox:
    box = Mailbox.objects.filter(user=user).first()
    if box is None or not box.is_ready:
        raise MailError("Connect your email first (your name menu → My email).")
    return box


def send_test(box: Mailbox) -> None:
    """Sends a short email to the mailbox itself to prove the settings work."""
    try:
        EmailMessage("Pocket of Paradise · your email is connected", "This test was sent from the Pocket of Paradise system using your own mailbox.",
                     from_email=f"{box.display_name} <{box.email}>", to=[box.email], connection=_connection(box)).send()
    except Exception as exc:  # noqa: BLE001 - shown to the user
        box.last_error = str(exc)[:1000]
        box.save(update_fields=["last_error", "updated_at"])
        raise MailError(f"Couldn't send with these settings: {exc}") from exc
    box.verified_at, box.last_error = timezone.now(), ""
    box.save(update_fields=["verified_at", "last_error", "updated_at"])


def send_client_email(*, user, to: str, subject: str, body: str, lead=None, files=()) -> SentEmail:
    """Send from the user's own address. The Director's CC is added here, so nobody can leave it off."""
    from apps.leads.services import log_activity

    box = mailbox_for(user)
    if not to or not subject.strip() or not body.strip():
        raise MailError("Add the recipient, a subject and a message.")
    if sum(getattr(f, "size", 0) for f in files) > MAX_ATTACHMENT_BYTES:
        raise MailError("Attachments are over 10 MB in total.")
    cc = MailSettings.load().cc_list()
    msg = EmailMessage(subject.strip(), body, from_email=f"{box.display_name} <{box.email}>", to=[to], cc=cc,
                       bcc=[box.email] if box.bcc_self else [], reply_to=[box.email], connection=_connection(box))
    for f in files:
        msg.attach(f.name, f.read(), getattr(f, "content_type", None))
    record = SentEmail(enquiry=lead, sender=user, from_email=box.email, to=to, cc=", ".join(cc), subject=subject.strip(),
                       body=body, attachments=", ".join(f.name for f in files)[:500])
    try:
        msg.send()
    except Exception as exc:  # noqa: BLE001 - recorded and shown to the agent
        record.status, record.error = SentEmail.Status.FAILED, str(exc)[:2000]
        record.save()
        raise MailError(f"The email wasn't sent: {exc}") from exc
    record.save()
    if lead is not None:
        log_activity(lead, "email", f"Email to {to}: {record.subject}" + (f" (cc {record.cc})" if record.cc else ""), user)
    return record


# --------------------------------------------------------------------------
# Reading the mailbox (IMAP) - inbox and drafts
# --------------------------------------------------------------------------

def _imap_connection(box: Mailbox) -> imaplib.IMAP4:
    if box.imap_security == Mailbox.Security.STARTTLS:
        conn = imaplib.IMAP4(box.imap_host, box.imap_port)
        conn.starttls()
    else:
        conn = imaplib.IMAP4_SSL(box.imap_host, box.imap_port)
    conn.login(box.username or box.email, box.password)
    return conn


def test_imap(box: Mailbox) -> None:
    """Connects and opens the inbox read-only, to prove the settings work."""
    try:
        conn = _imap_connection(box)
        try:
            conn.select("INBOX", readonly=True)
        finally:
            conn.logout()
    except Exception as exc:  # noqa: BLE001 - shown to the user
        box.imap_last_error = str(exc)[:1000]
        box.save(update_fields=["imap_last_error", "updated_at"])
        raise MailError(f"Couldn't read the inbox with these settings: {exc}") from exc
    box.imap_verified_at, box.imap_last_error = timezone.now(), ""
    box.save(update_fields=["imap_verified_at", "imap_last_error", "updated_at"])


def _decode_header_value(value) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", errors="replace"))
            except (LookupError, ValueError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _message_body(msg) -> str:
    if msg.is_multipart():
        plain = html = ""
        for part in msg.walk():
            if part.get_filename():
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if part.get_content_type() == "text/plain" and not plain:
                plain = text
            elif part.get_content_type() == "text/html" and not html:
                html = text
        return plain or html
    payload = msg.get_payload(decode=True)
    if payload is None:
        return str(msg.get_payload())
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def _parse_message(raw: bytes, uid: str) -> dict:
    msg = message_from_bytes(raw)
    body = _message_body(msg).strip()
    try:
        when = parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        when = None
    return {
        "uid": uid, "subject": _decode_header_value(msg.get("Subject")) or "(no subject)",
        "from": _decode_header_value(msg.get("From")), "to": _decode_header_value(msg.get("To")),
        "date": when, "body": body[:8000], "snippet": " ".join(body.split())[:160],
    }


def _select_folder(conn: imaplib.IMAP4, candidates: list[str]) -> str | None:
    for name in candidates:
        try:
            status, _ = conn.select(f'"{name}"', readonly=True)
        except imaplib.IMAP4.error:
            continue
        if status == "OK":
            return name
    return None


def fetch_folder(box: Mailbox, folder: str, limit: int = 20) -> list[dict]:
    """`folder` is "inbox" or "drafts". Cached briefly so paging through the list doesn't re-hit the
    mail server on every click; a fresh fetch happens again after the cache expires."""
    cache_key = f"imap:{box.pk}:{folder}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    messages: list[dict] = []
    try:
        conn = _imap_connection(box)
        try:
            ready = _select_folder(conn, DRAFT_FOLDER_CANDIDATES) is not None if folder == "drafts" \
                else conn.select("INBOX", readonly=True)[0] == "OK"
            if ready:
                status, data = conn.search(None, "ALL")
                uids = data[0].split()[-limit:] if status == "OK" and data and data[0] else []
                for uid in reversed(uids):
                    status, msg_data = conn.fetch(uid, "(RFC822)")
                    if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                        continue
                    try:
                        messages.append(_parse_message(msg_data[0][1], uid.decode()))
                    except Exception:  # noqa: BLE001 - one unparsable message shouldn't break the whole list
                        continue
        finally:
            conn.logout()
    except Exception as exc:  # noqa: BLE001 - recorded and shown to the agent
        box.imap_last_error = str(exc)[:1000]
        box.save(update_fields=["imap_last_error", "updated_at"])
        raise MailError(f"Couldn't read {folder}: {exc}") from exc
    cache.set(cache_key, messages, timeout=120)
    return messages


DEFAULT_TEMPLATES = [
    ("Thank you for your enquiry", "Your safari enquiry {reference}",
     "Hello {client_first_name},\n\nThank you for choosing {company}. I'm {agent_name} and I'll be planning {destination} with you.\n\nCould you confirm your travel dates, the number of guests and your budget per person? Then I'll send a proposal.\n\nKind regards,\n{agent_name}\n{agent_phone}"),
    ("Sending your itinerary", "Your {destination} itinerary",
     "Hello {client_first_name},\n\nHere is the itinerary we discussed for {destination}. Have a look and tell me what you would like to change.\n\nKind regards,\n{agent_name}\n{agent_phone}"),
    ("Gentle follow-up", "Checking in about {destination}",
     "Hello {client_first_name},\n\nJust checking in on your {destination} plans. Happy to adjust the dates, the budget or the places we visit.\n\nKind regards,\n{agent_name}\n{agent_phone}"),
]


def ensure_templates():
    if not EmailTemplate.objects.exists():
        for i, (n, sub, body) in enumerate(DEFAULT_TEMPLATES):
            EmailTemplate.objects.create(name=n, subject=sub, body=body, sort=i)
