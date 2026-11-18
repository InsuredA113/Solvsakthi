"""Email-to-FNOL intake parsing helpers.

This module deliberately performs conservative, label-based extraction only.
It never treats an email as proof of coverage, liability, fraud or settlement.
"""
import re
import io
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pypdf import PdfReader
from google import genai
from google.genai import types
from .config import GEMINI_API_KEY, GEMINI_MODEL


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
    "loss_cause": ("loss cause", "cause of loss", "estimated loss cause"),
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
            rf"(?im)^\s*{re.escape(label)}\s*:\s*(.*?)\s*$",
            body,
        )
        if match and match.group(1).strip():
            return match.group(1).strip()[:500]
    return None


def extract_pdf_text(content: bytes, max_pages: int = 40, max_chars: int = 30000) -> str:
    """Extract selectable text from a bounded PDF; scanned PDFs need OCR/manual review."""
    if not content or not content.startswith(b"%PDF-") or len(content) > 15 * 1024 * 1024:
        return ""
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            return ""
        chunks = []
        total = 0
        for page in reader.pages[:max_pages]:
            page_text = page.extract_text() or ""
            if page_text:
                chunks.append(page_text)
                total += len(page_text)
            if total >= max_chars:
                break
        return "\n".join(chunks)[:max_chars]
    except Exception:
        return ""

def ocr_pdf_text(content: bytes, max_pages: int = 10, max_chars: int = 20000) -> str:
    """OCR scanned PDF pages with Gemini Vision; returns only visibly printed text."""
    if not GEMINI_API_KEY or not content or not content.startswith(b"%PDF-") or len(content) > 15 * 1024 * 1024:
        return ""
    try:
        import fitz
        document = fitz.open(stream=content, filetype="pdf")
        client = genai.Client(api_key=GEMINI_API_KEY)
        extracted = []
        for page_number in range(min(len(document), max_pages)):
            page = document.load_page(page_number)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_bytes(data=pixmap.tobytes("png"), mime_type="image/png"),
                    "Transcribe all legible text on this document page exactly as shown. Preserve labels, values, dates, addresses and amounts. Do not infer, correct, summarize, or add information. If no readable text exists, return an empty string.",
                ],
            )
            page_text = (getattr(response, "text", None) or "").strip()
            if page_text:
                extracted.append("[OCR PAGE " + str(page_number + 1) + "]\\n" + page_text)
            if sum(len(item) for item in extracted) >= max_chars:
                break
        document.close()
        return "\\n\\n".join(extracted)[:max_chars]
    except Exception:
        return ""


def extract_structured_claim_fields(source_text: str) -> dict:
    """Use Gemini to map document text into known FNOL fields; unknowns stay null."""
    allowed_fields = tuple(LABELS.keys())
    if not GEMINI_API_KEY or not source_text or not source_text.strip():
        return {}
    prompt = (
        "Extract factual insurance first-notice-of-loss information from the source text. "
        "Return one JSON object with exactly these keys: " + ", ".join(allowed_fields) + ". "
        "Values must be strings or null. Do not infer missing facts, do not invent values, "
        "do not decide coverage, liability, fraud, settlement, or claim approval. "
        "Preserve names, policy identifiers, addresses, phone numbers and amounts exactly. "
        "For incident_date, use YYYY-MM-DD only when the date is unambiguous; otherwise return "
        "the original date text. For country_code use ISO 3166-1 alpha-2 only when explicitly "
        "supported by the document. For currency use an explicit currency code when present. "
        "For estimated_loss return the numeric amount as text without currency symbols. "
        "Source text:\\n" + source_text[:30000]
    )
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw = (getattr(response, "text", None) or "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        result = {}
        for key in allowed_fields:
            value = data.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                result[key] = str(value).strip()[:500]
        return result
    except Exception:
        return {}


def extract_email_intake(subject: str, body_text: str) -> dict:
    text = f"{subject}\n{body_text}".lower()
    fields = {key: _label_value(body_text, labels) for key, labels in LABELS.items()}

    # Apply conservative India defaults only when the reported loss location
    # clearly identifies India. Never infer a country from the sender mailbox.
    location = (fields.get("loss_location") or "").casefold()
    indian_location_terms = (
        "india", "tamil nadu", "coimbatore", "chennai", "bengaluru",
        "bangalore", "mumbai", "pune", "hyderabad", "delhi",
        "new delhi", "kolkata", "ahmedabad", "jaipur", "kochi",
        "thiruvananthapuram", "madurai", "tiruchirappalli", "salem",
        "erode", "tiruppur", "vellore", "mysuru", "mysore",
    )
    if any(re.search(rf"\b{re.escape(term)}\b", location) for term in indian_location_terms):
        fields["country_code"] = (fields.get("country_code") or "IN").upper()
        fields["currency"] = (fields.get("currency") or "INR").upper()
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
