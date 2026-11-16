"""Email-to-FNOL intake parsing helpers.

This module deliberately performs conservative, label-based extraction only.
It never treats an email as proof of coverage, liability, fraud or settlement.
"""
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class EmailIntakeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider: str = Field(default="manual", min_length=1, max_length=40)
    provider_message_id: str = Field(min_length=1, max_length=250)
    sender_email: EmailStr
    subject: str = Field(default="", max_length=500)
    body_text: str = Field(min_length=1, max_length=30000)
    received_at: datetime | None = None

    @field_validator("provider_message_id")
    @classmethod
    def clean_message_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provider message ID is required for idempotent intake.")
        return value.strip()


class EmailIntakeReview(BaseModel):
    status: Literal["Reviewed", "Rejected"]
    notes: str = Field(default="", max_length=2000)
    confirmed_claim_type: Literal["Property", "Commercial Casualty"] | None = None


LABELS = {
    "customer_name": ("insured name", "customer name", "contact name"),
    "policy_number": ("policy number", "policy no", "policy #"),
    "incident_date": ("incident date", "date of loss", "loss date"),
    "incident_description": ("incident description", "description of loss", "what happened"),
    "loss_location": ("loss location", "incident location", "property address", "location of loss"),
    "country_code": ("country code",),
    "currency": ("currency",),
    "business_name": ("business name", "insured business"),
    "industry": ("industry", "business sector"),
    "commercial_liability_type": ("liability line", "coverage line", "claim line"),
    "email": ("contact email", "insured email"),
    "phone": ("contact phone", "phone number"),
    "estimated_loss": ("estimated loss", "claimed amount", "loss amount"),
}


def _label_value(body: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(
            rf"(?im)^\\s*{re.escape(label)}\\s*:\\s*(.*?)\\s*$",
            body,
        )
        if match and match.group(1).strip():
            return match.group(1).strip()[:500]
    return None


def extract_email_intake(subject: str, body_text: str) -> dict:
    text = f"{subject}\n{body_text}".lower()
    fields = {key: _label_value(body_text, labels) for key, labels in LABELS.items()}
    commercial_casualty_signals = (
        "commercial general liability", "professional liability", "product liability",
        "employers liability", "public liability", "commercial casualty",
        "business liability", "third-party injury", "third party injury",
    )
    personal_casualty_signals = ("personal injury claim", "personal auto", "private motor", "personal liability")
    property_signals = ("property damage", "building damage", "water damage", "fire damage", "storm damage", "theft at")
    if any(term in text for term in personal_casualty_signals):
        classification = "Out of Scope"
        reason = "The message appears to concern personal-lines casualty, which Solv-In does not support."
    elif any(term in text for term in commercial_casualty_signals):
        classification = "Commercial Casualty"
        reason = "Commercial liability indicators were found; an authorised reviewer must confirm the claim line."
    elif any(term in text for term in property_signals):
        classification = "Property"
        reason = "Property-loss indicators were found; an authorised reviewer must confirm the claim type."
    else:
        classification = "Unclassified"
        reason = "The email does not contain enough clear information to classify its claim type."
    required = ["customer_name", "policy_number", "incident_date", "incident_description",
                "loss_location", "country_code", "currency"]
    if classification == "Commercial Casualty":
        required += ["business_name", "industry", "commercial_liability_type"]
    missing = [name for name in required if not fields.get(name)]
    if classification == "Out of Scope":
        missing = []
    return {
        "suggested_claim_type": classification,
        "classification_reason": reason,
        "extracted_fields": fields,
        "missing_fields": missing,
        "intake_status": "Out of Scope" if classification == "Out of Scope" else "Needs Review",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
