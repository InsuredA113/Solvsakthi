import pytest
from pydantic import ValidationError
from app.models import ClaimCreate


def base_claim(**overrides):
    data = {
        "customer_name": "Example Insured", "email": "claims@example.com",
        "phone": "+1-555-0100", "policy_number": "POL-100",
        "claim_type": "Property", "incident_date": "2026-06-15",
        "incident_description": "Water damage was reported at the insured location.",
        "loss_location": "100 Main Street", "country_code": "US",
        "region": "California", "locality": "Los Angeles", "postal_code": "90001",
        "estimated_loss": 1000, "currency": "USD"
    }
    data.update(overrides)
    return ClaimCreate.model_validate(data)


def test_property_claims_remain_supported_without_casualty_fields():
    claim = base_claim(property_occupancy="Residential")
    assert claim.claim_type == "Property"
    assert claim.property_occupancy == "Residential"


def test_casualty_claim_requires_commercial_segment_and_business_details():
    with pytest.raises(ValidationError):
        base_claim(claim_type="Casualty")


def test_commercial_casualty_claim_is_accepted_with_business_details():
    claim = base_claim(
        claim_type="Casualty", casualty_segment="Commercial",
        business_name="Example Manufacturing Ltd", industry="Manufacturing",
        commercial_liability_type="Commercial General Liability"
    )
    assert claim.casualty_segment == "Commercial"
    assert claim.commercial_liability_type == "Commercial General Liability"


def test_personal_casualty_is_not_supported():
    with pytest.raises(ValidationError):
        base_claim(claim_type="Casualty", casualty_segment="Personal")
