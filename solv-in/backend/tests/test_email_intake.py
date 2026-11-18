import pytest
from pydantic import ValidationError
from app.email_intake import EmailIntakeCreate, extract_email_intake, extract_pdf_text


def test_property_email_is_classified_but_requires_human_review():
    result = extract_email_intake(
        "Water damage claim",
        "Property damage reported.\nInsured Name: Example Insured\nPolicy Number: P-100"
    )
    assert result["suggested_claim_type"] == "Property"
    assert result["intake_status"] == "Needs Review"
    assert "incident_date" in result["missing_fields"]


def test_commercial_casualty_is_detected_without_creating_a_claim():
    result = extract_email_intake(
        "Commercial General Liability incident",
        "A third-party injury occurred at our business premises."
    )
    assert result["suggested_claim_type"] == "Commercial Casualty"
    assert result["intake_status"] == "Needs Review"
    assert "business_name" in result["missing_fields"]


def test_personal_casualty_is_out_of_scope():
    result = extract_email_intake("Personal auto injury claim", "Personal auto incident.")
    assert result["suggested_claim_type"] == "Out of Scope"
    assert result["intake_status"] == "Out of Scope"


def test_ambiguous_email_is_not_guessed():
    result = extract_email_intake("Claim notification", "Please contact me about an incident.")
    assert result["suggested_claim_type"] == "Unclassified"


def test_label_extraction_is_conservative_and_normalized():
    result = extract_email_intake(
        "Water damage",
        "Property damage\nInsured Name: Example Insured\nPolicy Number: P-100"
    )
    assert result["extracted_fields"]["customer_name"] == "Example Insured"
    assert result["extracted_fields"]["policy_number"] == "P-100"


def test_email_requires_provider_message_id_and_valid_sender():
    with pytest.raises(ValidationError):
        EmailIntakeCreate(sender_email="bad", provider_message_id="", body_text="claim")


def test_pdf_extraction_rejects_non_pdf_and_oversized_content():
    assert extract_pdf_text(b"not a pdf") == ""
    assert extract_pdf_text(b"%PDF-" + (b"0" * (15 * 1024 * 1024 + 1))) == ""
