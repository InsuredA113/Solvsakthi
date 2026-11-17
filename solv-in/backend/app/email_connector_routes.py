"""HTTP endpoints for Gmail Pub/Sub and Microsoft Graph notifications."""
import hmac
import logging
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError
import httpx

from .auth import require_roles
from .database import database_is_available, get_database
from .email_intake import EmailIntakeCreate, extract_email_intake
from .email_connectors import (
    setting, gmail_configured, microsoft_configured, gmail_access_token,
    gmail_api, gmail_pubsub_identity, parse_gmail_notification, gmail_headers,
    gmail_body, microsoft_access_token, graph_api, microsoft_body,
    parse_datetime, microsoft_subscription_payload,
)

router = APIRouter()
logger = logging.getLogger(__name__)
READ_ROLES = (
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor"
)
ADMIN_ROLES = ("Super Administrator", "Insurance Company Administrator")


def _log_connector_failure(provider: str, stage: str, exc: Exception) -> None:
    """Log actionable provider errors without logging OAuth credentials or tokens."""
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        details = {}
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    details = {
                        key: error.get(key)
                        for key in ("code", "message", "status", "errors")
                        if error.get(key) is not None
                    }
                else:
                    details = {
                        key: payload.get(key)
                        for key in ("error", "error_description", "message", "status")
                        if payload.get(key) is not None
                    }
        except (ValueError, TypeError):
            details = {"response": "Provider returned a non-JSON error response."}
        logger.error(
            "%s connector failed during %s: HTTP %s; provider_error=%s",
            provider, stage, response.status_code, details,
        )
    elif isinstance(exc, httpx.RequestError):
        logger.error(
            "%s connector failed during %s: network error %s: %s",
            provider, stage, type(exc).__name__, str(exc)[:500],
        )
    else:
        logger.exception(
            "%s connector failed during %s: %s",
            provider, stage, type(exc).__name__,
        )


def _save_email(provider: str, provider_message_id: str, sender_email: str,
                subject: str, body_text: str, received_at: datetime | None):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    collection = get_database()["email_intake"]
    collection.create_index([("provider", 1), ("provider_message_id", 1)], unique=True)
    existing = collection.find_one(
        {"provider": provider, "provider_message_id": provider_message_id},
        {"_id": 0, "body_text": 0}
    )
    if existing:
        return {"duplicate": True, "intake_id": existing["intake_id"]}
    if not sender_email:
        return {"ignored": True, "reason": "missing_sender_address"}
    try:
        payload = EmailIntakeCreate(
            provider=provider, provider_message_id=provider_message_id,
            sender_email=sender_email, subject=subject or "",
            body_text=body_text or "(No readable message body)",
            received_at=received_at,
        )
    except ValidationError:
        return {"ignored": True, "reason": "sender_address_missing_or_invalid"}
    parsed = extract_email_intake(payload.subject, payload.body_text)
    received = (payload.received_at or datetime.now(timezone.utc)).isoformat()
    record = {
        "intake_id": str(ObjectId()), "provider": provider,
        "provider_message_id": provider_message_id,
        "sender_email": str(payload.sender_email), "subject": payload.subject,
        "body_text": payload.body_text, "received_at": received,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": provider + "_connector", **parsed,
        "received_at": received, "review_status": parsed["intake_status"],
        "review_history": [],
    }
    try:
        collection.insert_one(record)
    except DuplicateKeyError:
        existing = collection.find_one(
            {"provider": provider, "provider_message_id": provider_message_id},
            {"_id": 0, "body_text": 0}
        )
        return {"duplicate": True, "intake_id": existing["intake_id"]}
    return {"duplicate": False, "intake_id": record["intake_id"]}


@router.get("/api/email-connectors/status")
def connector_status(user: dict = Depends(require_roles(*READ_ROLES))):
    db = get_database()
    gmail_mailbox = setting("GMAIL_MAILBOX").lower()
    ms_mailbox = setting("MS365_MAILBOX").lower()
    gmail_state = db["email_connector_state"].find_one({"_id": "gmail:" + gmail_mailbox}, {"_id": 0}) if database_is_available() and gmail_mailbox else None
    ms_state = db["email_connector_state"].find_one({"_id": "microsoft365:" + ms_mailbox}, {"_id": 0}) if database_is_available() and ms_mailbox else None
    return {
        "gmail": {"configured": gmail_configured(), "watch_active": bool(gmail_state and gmail_state.get("watch_expiration")), "watch_expiration": gmail_state.get("watch_expiration") if gmail_state else None},
        "microsoft365": {"configured": microsoft_configured(), "subscription_active": bool(ms_state and ms_state.get("subscription_id")), "subscription_expiration": ms_state.get("subscription_expiration") if ms_state else None},
        "note": "Connector credentials are held in backend environment variables and are never returned."
    }


@router.post("/api/email-connectors/gmail/watch")
def start_gmail_watch(user: dict = Depends(require_roles(*ADMIN_ROLES))):
    if not gmail_configured():
        raise HTTPException(status_code=409, detail="Configure all GMAIL environment variables before starting Gmail watch.")
    stage = "OAuth token refresh"
    try:
        token = gmail_access_token()
        stage = "Gmail users.watch request (check Gmail API access and Pub/Sub topic IAM)"
        result = gmail_api("POST", "watch", token, json_body={
            "topicName": setting("GMAIL_PUBSUB_TOPIC"),
            "labelIds": ["INBOX"], "labelFilterBehavior": "include",
        })
        stage = "saving watch state to MongoDB"
        mailbox = setting("GMAIL_MAILBOX").lower()
        get_database()["email_connector_state"].update_one(
            {"_id": "gmail:" + mailbox},
            {"$set": {"provider": "gmail", "mailbox": mailbox,
                      "last_history_id": str(result["historyId"]),
                      "watch_expiration": datetime.fromtimestamp(int(result["expiration"]) / 1000, timezone.utc).isoformat(),
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return {"provider": "gmail", "watch_expiration": result.get("expiration"), "history_cursor_initialized": True}
    except Exception as exc:
        _log_connector_failure("Gmail", stage, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Gmail watch failed during {stage}. Check the backend application logs for the provider error.",
        ) from exc


@router.post("/api/email-connectors/gmail/pubsub")
def gmail_pubsub(payload: dict, authorization: str | None = Header(default=None)):
    try:
        gmail_pubsub_identity(authorization)
        mailbox, notified_history_id = parse_gmail_notification(payload)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Unauthorised or invalid Gmail Pub/Sub notification.") from exc
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB unavailable; Pub/Sub should retry.")
    states = get_database()["email_connector_state"]
    state = states.find_one({"_id": "gmail:" + mailbox})
    if not state or not state.get("last_history_id"):
        raise HTTPException(status_code=409, detail="Gmail watch cursor missing. Start Gmail watch from connector settings.")
    try:
        token = gmail_access_token()
        cursor, page_token, ids = str(state["last_history_id"]), None, set()
        latest_history_id = cursor
        while True:
            params = {"startHistoryId": cursor, "historyTypes": "messageAdded", "labelId": "INBOX", "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            page = gmail_api("GET", "history", token, params=params)
            latest_history_id = str(page.get("historyId", latest_history_id))
            for item in page.get("history", []):
                for added in item.get("messagesAdded", []):
                    message = added.get("message", {})
                    labels = message.get("labelIds", [])
                    if not labels or "INBOX" in labels:
                        ids.add(message.get("id"))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        for message_id in sorted(x for x in ids if x):
            message = gmail_api("GET", "messages/" + message_id, token, params={"format": "full"})
            headers = gmail_headers(message)
            sender = parseaddr(headers.get("from", ""))[1]
            received = None
            if headers.get("date"):
                try:
                    received = parsedate_to_datetime(headers["date"])
                    if received.tzinfo is None:
                        received = received.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    pass
            _save_email("gmail", mailbox + ":" + message_id, sender, headers.get("subject", ""), gmail_body(message), received)
        states.update_one({"_id": "gmail:" + mailbox}, {"$set": {
            "last_history_id": latest_history_id,
            "last_notification_history_id": str(notified_history_id),
            "last_successful_sync": datetime.now(timezone.utc).isoformat()
        }})
        return {"status": "processed", "messages_seen": len(ids), "cursor_updated": latest_history_id}
    except HTTPException:
        raise
    except Exception as exc:
        _log_connector_failure("Gmail", "Pub/Sub history sync", exc)
        raise HTTPException(status_code=502, detail="Gmail history sync failed; Pub/Sub may retry this notification.") from exc


@router.post("/api/email-connectors/microsoft365/subscribe")
def create_microsoft_subscription(user: dict = Depends(require_roles(*ADMIN_ROLES))):
    if not microsoft_configured():
        raise HTTPException(status_code=409, detail="Configure all MS365 environment variables before subscribing.")
    try:
        token = microsoft_access_token()
        subscription = microsoft_subscription_payload()
        mailbox = setting("MS365_MAILBOX").lower()
        states = get_database()["email_connector_state"]
        previous = states.find_one({"_id": "microsoft365:" + mailbox}) or {}
        result = None
        if previous.get("subscription_id"):
            try:
                result = graph_api("PATCH", "subscriptions/" + previous["subscription_id"], token,
                                   json_body={"expirationDateTime": subscription["expirationDateTime"]})
            except httpx.HTTPStatusError as renewal_error:
                if renewal_error.response.status_code not in (404, 410):
                    raise
        if result is None:
            result = graph_api("POST", "subscriptions", token, json_body=subscription)
        states.update_one(
            {"_id": "microsoft365:" + mailbox},
            {"$set": {"provider": "microsoft365", "mailbox": mailbox,
                      "subscription_id": result.get("id"),
                      "subscription_expiration": result.get("expirationDateTime"),
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        return {"provider": "microsoft365", "subscription_id": result.get("id"), "expiration": result.get("expirationDateTime")}
    except Exception as exc:
        _log_connector_failure("Microsoft 365", "Graph subscription request", exc)
        raise HTTPException(status_code=502, detail="Unable to create Graph subscription. Check Entra permissions, admin consent and public HTTPS notification URL.") from exc


@router.get("/api/email-connectors/microsoft365/notifications")
def graph_validation(validationToken: str | None = Query(default=None)):
    if not validationToken:
        raise HTTPException(status_code=400, detail="Microsoft Graph validation token is required.")
    return Response(content=validationToken, media_type="text/plain")


@router.post("/api/email-connectors/microsoft365/notifications")
def graph_notifications(payload: dict | None = None, validationToken: str | None = Query(default=None)):
    if validationToken:
        return Response(content=validationToken, media_type="text/plain")
    notifications = (payload or {}).get("value", [])
    if not notifications:
        return {"status": "accepted", "processed": 0}
    if not microsoft_configured() or not database_is_available():
        raise HTTPException(status_code=503, detail="Microsoft connector or MongoDB unavailable; Graph should retry.")
    mailbox = setting("MS365_MAILBOX").lower()
    state = get_database()["email_connector_state"].find_one({"_id": "microsoft365:" + mailbox})
    if not state:
        raise HTTPException(status_code=409, detail="No active Microsoft Graph subscription is registered.")
    for item in notifications:
        if not hmac.compare_digest(str(item.get("clientState", "")), setting("MS365_CLIENT_STATE")):
            raise HTTPException(status_code=401, detail="Microsoft Graph clientState is invalid.")
        if item.get("subscriptionId") != state.get("subscription_id"):
            raise HTTPException(status_code=401, detail="Microsoft Graph subscription ID is not recognised.")
    try:
        token, processed = microsoft_access_token(), 0
        for item in notifications:
            message_id = (item.get("resourceData") or {}).get("id")
            if not message_id:
                continue
            message = graph_api(
                "GET", "users/" + setting("MS365_MAILBOX") + "/messages/" + message_id, token,
                params={"$select": "id,subject,from,receivedDateTime,body,bodyPreview"}
            )
            sender = (((message.get("from") or {}).get("emailAddress") or {}).get("address") or "")
            _save_email("microsoft365", mailbox + ":" + message.get("id", message_id), sender,
                        message.get("subject", ""), microsoft_body(message), parse_datetime(message.get("receivedDateTime")))
            processed += 1
        get_database()["email_connector_state"].update_one(
            {"_id": "microsoft365:" + mailbox},
            {"$set": {"last_successful_notification": datetime.now(timezone.utc).isoformat()}}
        )
        return {"status": "processed", "messages_seen": processed}
    except HTTPException:
        raise
    except Exception as exc:
        _log_connector_failure("Microsoft 365", "Graph message retrieval", exc)
        raise HTTPException(status_code=502, detail="Microsoft Graph message retrieval failed; Graph may retry this notification.") from exc
