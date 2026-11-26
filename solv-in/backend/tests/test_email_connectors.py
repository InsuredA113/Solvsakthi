import base64
from datetime import timezone

from app import email_connector_routes
from app.email_connector_routes import DemoEmailRequest, demo_mailbox_tenant, ingest_demo_email

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



def test_demo_mailbox_maps_to_chat_insurance_tenant():
    assert demo_mailbox_tenant("abc@chatinsurance.com") == (
        "chat-insurance", "Chat Insurance"
    )


def test_demo_mailbox_maps_to_gpt_insurance_tenant():
    assert demo_mailbox_tenant("abc@gptinsurance.com") == (
        "gpt-insurance", "GPT Insurance"
    )


def test_demo_mailbox_mapping_normalizes_case_and_whitespace():
    assert demo_mailbox_tenant("  ABC@GPTINSURANCE.COM ") == (
        "gpt-insurance", "GPT Insurance"
    )


def test_demo_mailbox_mapping_rejects_unknown_addresses():
    assert demo_mailbox_tenant("attacker@chatinsurance.com") is None
    assert demo_mailbox_tenant("abc@unknown.example") is None
    assert demo_mailbox_tenant("") is None



def test_demo_intake_passes_chat_tenant_to_email_pipeline(monkeypatch):
    class FakeTenants:
        def find_one(self, query, projection):
            assert query["tenant_slug"] == "chat-insurance"
            assert query["company_name"] == "Chat Insurance"
            assert query["contact_email"] == "abc@chatinsurance.com"
            assert query["status"] == "Active"
            return {"tenant_id": "ten_chat_test"}

    class FakeDatabase:
        def __getitem__(self, name):
            assert name == "tenants"
            return FakeTenants()

    captured = {}
    monkeypatch.setattr(email_connector_routes, "database_is_available", lambda: True)
    monkeypatch.setattr(email_connector_routes, "get_database", lambda: FakeDatabase())

    def fake_save(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"duplicate": False, "intake_id": "demo-intake-1", "claim_id": "CLM-2026-000188"}

    monkeypatch.setattr(email_connector_routes, "_save_email", fake_save)
    payload = DemoEmailRequest(
        mailbox="abc@chatinsurance.com",
        sender_email="customer@example.test",
        subject="Sample water damage claim",
        body_text="Water damage occurred at the insured property.",
    )
    result = ingest_demo_email(payload, user={"role": "Super Administrator"})

    assert result["tenant_id"] == "ten_chat_test"
    assert captured["args"][0] == "demo"
    assert captured["args"][2] == "customer@example.test"
    assert captured["kwargs"]["tenant_id_override"] == "ten_chat_test"
    assert result["claim_id"] == "CLM-2026-000188"


def test_demo_intake_rejects_unknown_demo_mailbox():
    from fastapi import HTTPException
    import pytest

    payload = DemoEmailRequest(
        mailbox="not-a-demo-mailbox@example.test",
        sender_email="customer@example.test",
        body_text="This is a sufficiently long sample claim email.",
    )
    with pytest.raises(HTTPException) as exc:
        ingest_demo_email(payload, user={"role": "Super Administrator"})
    assert exc.value.status_code == 422
