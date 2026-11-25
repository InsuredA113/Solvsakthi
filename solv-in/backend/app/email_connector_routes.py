"""HTTP endpoints for Gmail Pub/Sub and Microsoft Graph notifications."""
import base64
import hmac
import io
import logging
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime

from bson import ObjectId
from gridfs import GridFSBucket
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
import httpx

from .auth import require_roles
from .database import database_is_available, get_database
from .email_intake import EmailIntakeCreate, extract_email_intake, extract_pdf_text, ocr_pdf_text, extract_structured_claim_fields
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


def _intake_update_filter(intake_id: str, tenant_id: str) -> dict:
    """Keep automated intake mutations bound to the owning insurer."""
    return _intake_update_filter(intake_id, tenant_id)


def _configured_ingest_tenant(db) -> dict:
    """Resolve the single configured mailbox to an active insurer tenant."""
    tenant_id = setting("EMAIL_CONNECTOR_TENANT_ID").strip()
    if not tenant_id:
        raise HTTPException(
            status_code=503,
            detail="Email intake is paused until EMAIL_CONNECTOR_TENANT_ID is configured for an active insurer tenant.",
        )
    tenant = db["tenants"].find_one(
        {"tenant_id": tenant_id, "status": "Active"},
        {"_id": 0, "tenant_id": 1, "company_name": 1},
    )
    if not tenant:
        raise HTTPException(
            status_code=503,
            detail="The configured email intake tenant is missing or inactive. Email intake is paused.",
        )
    return tenant


def _suggestions_for_missing(missing_fields: list[str]) -> dict[str, str]:
    guidance = {
        "customer_name": "Ask for the insured person's full legal name or the business contact name.",
        "policy_number": "Request the policy number or policy schedule.",
        "incident_date": "Ask for the date the incident or loss occurred (YYYY-MM-DD preferred).",
        "incident_description": "Request a brief description of what happened and the reported damage or injury.",
        "loss_location": "Request the full incident address, including city and country.",
        "country_code": "Confirm the country where the loss occurred.",
        "currency": "Confirm the currency for any reported loss amount.",
        "business_name": "Request the affected business or insured legal entity name.",
        "industry": "Ask which industry or business activity is involved.",
        "commercial_liability_type": "Confirm the commercial liability line, such as Public Liability or Product Liability.",
        "claim_type": "Ask whether this is a Property or Commercial Casualty claim.",
    }
    return {field: guidance.get(field, "Please confirm this information with the sender.") for field in missing_fields}


def _create_provisional_claim(db, intake_id: str, tenant_id: str, sender_email: str, parsed: dict,
                              received_at: str) -> dict | None:
    classification = parsed.get("suggested_claim_type")
    if classification == "Out of Scope":
        return None
    fields = parsed.get("extracted_fields") or {}
    missing = list(parsed.get("missing_fields") or [])
    claim_type = "Property" if classification == "Property" else "Casualty" if classification == "Commercial Casualty" else "Unclassified"
    if claim_type == "Unclassified" and "claim_type" not in missing:
        missing.append("claim_type")
    if claim_type == "Casualty":
        for name in ("business_name", "industry", "commercial_liability_type"):
            if not fields.get(name) and name not in missing:
                missing.append(name)

    incident_date = fields.get("incident_date")
    if incident_date:
        try:
            from datetime import date
            incident_date = date.fromisoformat(str(incident_date)[:10]).isoformat()
        except ValueError:
            incident_date = None
            if "incident_date" not in missing:
                missing.append("incident_date")

    def clean(field: str):
        value = fields.get(field)
        return value.strip() if isinstance(value, str) and value.strip() else None

    loss_amount = None
    raw_amount = clean("estimated_loss")
    if raw_amount:
        try:
            loss_amount = float(raw_amount.replace(",", "").replace("INR", "").replace("USD", "").strip())
            if loss_amount < 0:
                loss_amount = None
        except ValueError:
            loss_amount = None

    now = datetime.now(timezone.utc).isoformat()
    sequence = db["counters"].find_one_and_update(
        {"_id": f"claim_id_{datetime.now(timezone.utc).year}"},
        {"$inc": {"sequence": 1}}, upsert=True, return_document=ReturnDocument.AFTER
    )
    claim_id = f"CLM-{datetime.now(timezone.utc).year}-{sequence['sequence']:06d}"
    claim = {
        "claim_id": claim_id,
        "tenant_id": tenant_id,
        "customer_name": clean("customer_name"),
        "loss_cause": clean("loss_cause"),
        "email": clean("email") or sender_email,
        "phone": clean("phone"),
        "policy_number": clean("policy_number"),
        "claim_type": claim_type,
        "property_occupancy": None,
        "casualty_segment": "Commercial" if claim_type == "Casualty" else None,
        "business_name": clean("business_name"),
        "industry": clean("industry"),
        "commercial_liability_type": clean("commercial_liability_type"),
        "incident_date": incident_date,
        "incident_description": clean("incident_description"),
        "loss_location": clean("loss_location"),
        "country_code": clean("country_code"),
        "region": None, "locality": None, "postal_code": None,
        "estimated_loss": loss_amount,
        "currency": clean("currency"),
        "status": "Under Review",
        "created_at": now, "updated_at": now,
        "created_by": "system:gmail-auto-intake" if parsed else "system:email-auto-intake",
        "created_by_name": "Email automation", "created_by_email": sender_email,
        "created_by_type": "system", "source": "Email FNOL", "source_mode": "email",
        "source_intake_id": intake_id,
        "auto_intake_status": "Provisional" if missing else "Created",
        "missing_fields": sorted(set(missing)),
        "suggested_information": _suggestions_for_missing(sorted(set(missing))),
        "classification_reason": parsed.get("classification_reason"),
        "source_received_at": received_at,
    }
    db["claims"].create_index("source_intake_id", unique=True, sparse=True)
    db["claims"].insert_one(claim)
    claim.pop("_id", None)
    return claim


def _save_email(provider: str, provider_message_id: str, sender_email: str,
                subject: str, body_text: str, received_at: datetime | None,
                attachments: list[dict] | None = None):
    if not database_is_available():
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.")
    db = get_database()
    tenant = _configured_ingest_tenant(db)
    tenant_id = tenant["tenant_id"]
    collection = db["email_intake"]
    collection.create_index([("provider", 1), ("provider_message_id", 1)], unique=True)
    existing = collection.find_one(
        {"tenant_id": tenant_id, "provider": provider, "provider_message_id": provider_message_id},
        {"_id": 0, "body_text": 0}
    )
    if existing:
        return {"duplicate": True, "intake_id": existing["intake_id"], "claim_id": existing.get("claim_id")}
    if not sender_email:
        return {"ignored": True, "reason": "missing_sender_address"}
    pdf_text_parts = []
    pdf_processing = []
    for attachment in attachments or []:
        if (attachment.get("content_type") or "").lower() == "application/pdf":
            pdf_content = attachment.get("content") or b""
            extracted = extract_pdf_text(pdf_content)
            extraction_method = "selectable_text" if extracted.strip() else "none"
            if not extracted.strip():
                extracted = ocr_pdf_text(pdf_content)
                if extracted.strip():
                    extraction_method = "gemini_vision_ocr"
            filename = (attachment.get("filename") or "attachment.pdf")[:180]
            pdf_processing.append({
                "filename": filename,
                "text_extracted": bool(extracted.strip()),
                "method": extraction_method,
                "status": ("Selectable text extracted" if extraction_method == "selectable_text" else "OCR text extracted" if extraction_method == "gemini_vision_ocr" else "Text unavailable; manual review required"),
            })
            if extracted:
                pdf_text_parts.append("\n[PDF ATTACHMENT: " + filename + "]\n" + extracted)
    intake_text = (body_text or "(No readable message body)") + "".join(pdf_text_parts)
    try:
        payload = EmailIntakeCreate(
            provider=provider, provider_message_id=provider_message_id,
            sender_email=sender_email, subject=subject or "",
            body_text=intake_text[:30000],
            received_at=received_at,
        )
    except ValidationError:
        return {"ignored": True, "reason": "sender_address_missing_or_invalid"}
    parsed = extract_email_intake(payload.subject, payload.body_text)
    document_text = "\n".join(pdf_text_parts)
    structured_fields = extract_structured_claim_fields(document_text) if document_text.strip() else {}
    # Keep explicit label-based values first; Gemini fills only fields the parser missed.
    extracted_fields = parsed.get("extracted_fields") or {}
    for field_name, field_value in structured_fields.items():
        if not extracted_fields.get(field_name) and field_value:
            extracted_fields[field_name] = field_value
    parsed["extracted_fields"] = extracted_fields
    parsed["document_ai_extraction"] = "completed" if structured_fields else (
        "no_document_text" if not document_text.strip() else "no_fields_returned"
    )
    required_fields = ["customer_name", "policy_number", "incident_date",
                       "incident_description", "loss_location", "country_code", "currency"]
    if parsed.get("suggested_claim_type") == "Commercial Casualty":
        required_fields += ["business_name", "industry", "commercial_liability_type"]
    if parsed.get("suggested_claim_type") == "Out of Scope":
        parsed["missing_fields"] = []
    else:
        parsed["missing_fields"] = [name for name in required_fields if not extracted_fields.get(name)]
    received = (payload.received_at or datetime.now(timezone.utc)).isoformat()
    intake_id = str(ObjectId())
    record = {
        "intake_id": intake_id, "tenant_id": tenant_id, "provider": provider,
        "provider_message_id": provider_message_id,
        "sender_email": str(payload.sender_email), "subject": payload.subject,
        "body_text": body_text or "(No readable message body)",
        "document_extracted_text": "".join(pdf_text_parts)[:30000],
        "pdf_processing": pdf_processing, "received_at": received,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": provider + "_connector", **parsed,
        "review_status": parsed["intake_status"], "review_history": [],
        "auto_creation_status": "Pending",
    }
    try:
        collection.insert_one(record)
    except DuplicateKeyError:
        existing = collection.find_one(
            {"tenant_id": tenant_id, "provider": provider, "provider_message_id": provider_message_id},
            {"_id": 0, "body_text": 0}
        )
        return {"duplicate": True, "intake_id": existing.get("intake_id") if existing else None,
                "claim_id": existing.get("claim_id") if existing else None}

    claim = None
    try:
        claim = _create_provisional_claim(db, intake_id, tenant_id, str(payload.sender_email), parsed, received)
        if claim:
            collection.update_one(_intake_update_filter(intake_id, tenant_id), {"$set": {
                "claim_id": claim["claim_id"], "review_status": "Converted",
                "conversion_state": "Completed", "auto_creation_status": claim["auto_intake_status"],
                "missing_fields": claim["missing_fields"],
                "suggested_information": claim["suggested_information"],
                "converted_at": datetime.now(timezone.utc).isoformat(),
            }})
        else:
            collection.update_one(_intake_update_filter(intake_id, tenant_id), {"$set": {
                "auto_creation_status": "Out of Scope",
                "auto_creation_note": "Personal-lines or unsupported claim indicators were detected; no in-scope claim record was created."
            }})
    except Exception:
        logger.exception("Automatic claim creation failed for email intake %s", intake_id)
        collection.update_one(_intake_update_filter(intake_id, tenant_id), {"$set": {"auto_creation_status": "Failed"}})
        raise

    saved_documents = []
    for attachment in attachments or []:
        try:
            filename = (attachment.get("filename") or "email-attachment").replace("\\", "/").split("/")[-1][:180]
            content_type = (attachment.get("content_type") or "").lower()
            content = attachment.get("content") or b""
            allowed = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
            valid = (
                (content_type == "application/pdf" and content.startswith(b"%PDF-")) or
                (content_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff")) or
                (content_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n")) or
                (content_type == "image/webp" and content[:4] == b"RIFF" and content[8:12] == b"WEBP")
            )
            if not claim or content_type not in allowed or not valid or not content or len(content) > 15 * 1024 * 1024:
                continue
            file_id = GridFSBucket(db, bucket_name="claim_documents").upload_from_stream(
                filename, io.BytesIO(content),
                metadata={"claim_id": claim["claim_id"], "tenant_id": tenant_id, "content_type": content_type,
                          "size": len(content), "uploaded_at": datetime.now(timezone.utc).isoformat(),
                          "uploaded_by": "system:email-attachment", "source": "email_attachment",
                          "source_intake_id": intake_id}
            )
            saved_documents.append({"file_id": str(file_id), "filename": filename})
        except Exception:
            logger.exception("Unable to store an email attachment for intake %s", intake_id)
    collection.update_one(_intake_update_filter(intake_id, tenant_id), {"$set": {
        "attachment_count": len(saved_documents), "attachments_saved": saved_documents
    }})
    return {"duplicate": False, "intake_id": intake_id,
            "claim_id": claim["claim_id"] if claim else None,
            "attachments_saved": len(saved_documents)}


def _gmail_attachment_parts(part: dict):
    for child in part.get("parts", []) or []:
        yield from _gmail_attachment_parts(child)
    body = part.get("body") or {}
    attachment_id = body.get("attachmentId")
    filename = part.get("filename") or ""
    if attachment_id and filename:
        yield {"attachment_id": attachment_id, "filename": filename,
               "content_type": (part.get("mimeType") or "").lower()}


def _fetch_gmail_attachments(message: dict, message_id: str, token: str) -> list[dict]:
    attachments = []
    for part in _gmail_attachment_parts(message.get("payload") or {}):
        if part["content_type"] not in {"application/pdf", "image/jpeg", "image/png", "image/webp"}:
            continue
        try:
            result = gmail_api("GET", f"messages/{message_id}/attachments/{part['attachment_id']}", token)
            data = result.get("data") or ""
            content = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            if content and len(content) <= 15 * 1024 * 1024:
                attachments.append({"filename": part["filename"], "content_type": part["content_type"], "content": content})
        except Exception:
            logger.exception("Unable to retrieve a supported Gmail attachment from message %s", message_id)
    return attachments


@router.get("/api/email-connectors/status")
def connector_status(user: dict = Depends(require_roles("Super Administrator"))):
    db = get_database()
    gmail_mailbox = setting("GMAIL_MAILBOX").lower()
    ms_mailbox = setting("MS365_MAILBOX").lower()
    tenant_mapping_id = setting("EMAIL_CONNECTOR_TENANT_ID")
    database_ready = database_is_available()
    gmail_state = db["email_connector_state"].find_one({"_id": "gmail:" + gmail_mailbox}, {"_id": 0}) if database_ready and gmail_mailbox else None
    ms_state = db["email_connector_state"].find_one({"_id": "microsoft365:" + ms_mailbox}, {"_id": 0}) if database_ready and ms_mailbox else None
    tenant_mapping_active = bool(
        database_ready and tenant_mapping_id and
        db["tenants"].find_one({"tenant_id": tenant_mapping_id, "status": "Active"}, {"_id": 1})
    )
    return {
        "gmail": {"configured": gmail_configured(), "watch_active": bool(gmail_state and gmail_state.get("watch_expiration")), "watch_expiration": gmail_state.get("watch_expiration") if gmail_state else None},
        "microsoft365": {"configured": microsoft_configured(), "subscription_active": bool(ms_state and ms_state.get("subscription_id")), "subscription_expiration": ms_state.get("subscription_expiration") if ms_state else None},
        "tenant_mapping_configured": tenant_mapping_active,
        "note": "Connector credentials and the single-mailbox tenant mapping are held in backend environment variables and are never returned."
    }


@router.post("/api/email-connectors/gmail/watch")
def start_gmail_watch(user: dict = Depends(require_roles("Super Administrator"))):
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
            _save_email("gmail", mailbox + ":" + message_id, sender, headers.get("subject", ""), gmail_body(message), received, _fetch_gmail_attachments(message, message_id, token))
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
def create_microsoft_subscription(user: dict = Depends(require_roles("Super Administrator"))):
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
