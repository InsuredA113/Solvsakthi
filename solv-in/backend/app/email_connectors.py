"""Gmail and Microsoft 365 email connector helpers for Solv-In.

Secrets are read only from backend environment variables. No mailbox tokens or
client secrets are returned to API clients or written to logs.
"""
import base64
import html
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from html.parser import HTMLParser
from typing import Any

import httpx


def setting(name: str) -> str:
    return os.getenv(name, "").strip()


def gmail_configured() -> bool:
    required = all(setting(key) for key in (
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
        "GMAIL_MAILBOX", "GMAIL_PUBSUB_TOPIC", "GMAIL_PUBSUB_AUDIENCE",
        "GMAIL_PUBSUB_SERVICE_ACCOUNT",
    ))
    return required and setting("GMAIL_PUBSUB_AUDIENCE").startswith("https://") and setting("GMAIL_PUBSUB_TOPIC").startswith("projects/")


def microsoft_configured() -> bool:
    required = all(setting(key) for key in (
        "MS365_TENANT_ID", "MS365_CLIENT_ID", "MS365_CLIENT_SECRET",
        "MS365_MAILBOX", "MS365_NOTIFICATION_URL", "MS365_CLIENT_STATE",
    ))
    return required and setting("MS365_NOTIFICATION_URL").startswith("https://") and len(setting("MS365_CLIENT_STATE")) >= 32


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, TypeError):
        return value


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip_depth += 1
        elif tag in ("p", "div", "br", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip_depth:
            self.skip_depth -= 1
        elif tag in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub("\n{3,}", "\n\n", html.unescape("".join(parser.parts))).strip()


def _gmail_part_text(part: dict[str, Any]) -> tuple[str, str]:
    plain, rich = [], []
    mime = part.get("mimeType", "")
    data = (part.get("body") or {}).get("data")
    if data:
        try:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if mime == "text/plain" else rich if mime == "text/html" else []).append(decoded)
        except (ValueError, UnicodeError):
            pass
    for child in part.get("parts", []) or []:
        child_plain, child_rich = _gmail_part_text(child)
        if child_plain:
            plain.append(child_plain)
        if child_rich:
            rich.append(child_rich)
    return "\n".join(plain), "\n".join(rich)


def gmail_body(message: dict[str, Any]) -> str:
    plain, rich = _gmail_part_text(message.get("payload") or {})
    body = plain or html_to_text(rich)
    if not body:
        body = (message.get("snippet") or "").strip()
    return body[:30000]


def gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    result = {}
    for item in (message.get("payload") or {}).get("headers", []):
        name = str(item.get("name", "")).lower()
        if name in ("from", "subject", "date", "message-id"):
            result[name] = _decode_header(item.get("value"))
    return result


def microsoft_body(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    content = body.get("content", "") or message.get("bodyPreview", "")
    if body.get("contentType", "").lower() == "html":
        content = html_to_text(content)
    return content[:30000]


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _post_form(url: str, data: dict[str, str]) -> dict:
    response = httpx.post(url, data=data, timeout=20)
    response.raise_for_status()
    return response.json()


def gmail_access_token() -> str:
    if not gmail_configured():
        raise RuntimeError("Gmail connector environment settings are incomplete.")
    token = _post_form("https://oauth2.googleapis.com/token", {
        "client_id": setting("GMAIL_CLIENT_ID"),
        "client_secret": setting("GMAIL_CLIENT_SECRET"),
        "refresh_token": setting("GMAIL_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    })
    return token["access_token"]


def gmail_api(method: str, path: str, token: str, params=None, json_body=None) -> dict:
    response = httpx.request(
        method, "https://gmail.googleapis.com/gmail/v1/users/me/" + path.lstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        params=params, json=json_body, timeout=30,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def microsoft_access_token() -> str:
    if not microsoft_configured():
        raise RuntimeError("Microsoft 365 connector environment settings are incomplete.")
    tenant = setting("MS365_TENANT_ID")
    token = _post_form(f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", {
        "client_id": setting("MS365_CLIENT_ID"),
        "client_secret": setting("MS365_CLIENT_SECRET"),
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    })
    return token["access_token"]


def graph_api(method: str, path: str, token: str, json_body=None, params=None) -> dict:
    response = httpx.request(
        method, "https://graph.microsoft.com/v1.0/" + path.lstrip("/"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=json_body, params=params, timeout=30,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def gmail_pubsub_identity(authorization: str | None) -> dict:
    audience = setting("GMAIL_PUBSUB_AUDIENCE")
    expected_email = setting("GMAIL_PUBSUB_SERVICE_ACCOUNT").lower()
    if not audience or not expected_email or not authorization or not authorization.lower().startswith("bearer "):
        raise ValueError("Missing Google Pub/Sub push identity.")
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token
    claims = id_token.verify_oauth2_token(authorization.split(" ", 1)[1], Request(), audience)
    if claims.get("email", "").lower() != expected_email or claims.get("email_verified") is not True:
        raise ValueError("Google Pub/Sub push identity is not authorised.")
    return claims


def parse_gmail_notification(payload: dict) -> tuple[str, int]:
    message = payload.get("message") or {}
    data = message.get("data")
    if not data:
        raise ValueError("Pub/Sub message data is missing.")
    decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
    import json
    notification = json.loads(decoded)
    mailbox = str(notification.get("emailAddress", "")).lower()
    if mailbox != setting("GMAIL_MAILBOX").lower():
        raise ValueError("Notification mailbox does not match the configured Gmail mailbox.")
    return mailbox, int(notification["historyId"])


def microsoft_subscription_payload() -> dict:
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(days=2)
    return {
        "changeType": "created",
        "notificationUrl": setting("MS365_NOTIFICATION_URL"),
        "resource": f"users/{setting('MS365_MAILBOX')}/mailFolders('Inbox')/messages",
        "expirationDateTime": expiry.isoformat().replace("+00:00", "Z"),
        "clientState": setting("MS365_CLIENT_STATE"),
    }
