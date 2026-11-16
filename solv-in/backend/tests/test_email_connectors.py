import base64
from datetime import timezone

from app.email_connectors import (
    gmail_body, html_to_text, microsoft_body, parse_datetime,
    parse_gmail_notification, microsoft_subscription_payload,
)


def test_html_email_body_is_converted_to_readable_text():
    result = html_to_text("<p>Water damage</p><script>ignore()</script><p>Kitchen ceiling</p>")
    assert "Water damage" in result
    assert "Kitchen ceiling" in result
    assert "ignore()" not in result


def test_gmail_plain_text_body_is_decoded():
    encoded = base64.urlsafe_b64encode(b"Property damage\nPolicy Number: P-1").decode().rstrip("=")
    message = {"payload": {"mimeType": "text/plain", "body": {"data": encoded}}}
    assert "Policy Number: P-1" in gmail_body(message)


def test_gmail_html_body_is_decoded_when_plain_text_is_absent():
    encoded = base64.urlsafe_b64encode(b"<p>Fire damage</p>").decode().rstrip("=")
    message = {"payload": {"mimeType": "text/html", "body": {"data": encoded}}}
    assert "Fire damage" in gmail_body(message)


def test_microsoft_html_body_is_converted():
    message = {"body": {"contentType": "html", "content": "<p>Storm damage</p>"}}
    assert "Storm damage" in microsoft_body(message)


def test_parse_datetime_normalizes_zulu_timestamp():
    result = parse_datetime("2026-11-16T10:00:00Z")
    assert result is not None
    assert result.tzinfo == timezone.utc


def test_gmail_notification_is_scoped_to_configured_mailbox(monkeypatch):
    monkeypatch.setenv("GMAIL_MAILBOX", "claims@example.com")
    data = base64.urlsafe_b64encode(b'{"emailAddress":"claims@example.com","historyId":"1234"}').decode().rstrip('=')
    mailbox, history_id = parse_gmail_notification({"message": {"data": data}})
    assert mailbox == "claims@example.com"
    assert history_id == 1234


def test_microsoft_subscription_uses_public_notification_url(monkeypatch):
    monkeypatch.setenv("MS365_MAILBOX", "claims@example.com")
    monkeypatch.setenv("MS365_NOTIFICATION_URL", "https://claims.example.com/api/email-connectors/microsoft365/notifications")
    monkeypatch.setenv("MS365_CLIENT_STATE", "a-long-random-client-state-value")
    payload = microsoft_subscription_payload()
    assert payload["notificationUrl"].startswith("https://")
    assert payload["resource"] == "users/claims@example.com/mailFolders('Inbox')/messages"
    assert "lifecycleNotificationUrl" not in payload
