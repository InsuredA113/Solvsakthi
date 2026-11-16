import pytest
from pydantic import ValidationError
from app.policy_coverage import PolicyCreate, CoverageAssessmentRequest, assess_policy


def policy(**overrides):
    data = {
        "policy_number": "POL-100", "insured_name": "Example Insured",
        "claim_type": "Property", "country_code": "US", "currency": "USD",
        "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        "per_occurrence_limit": "100000", "aggregate_limit": "250000",
        "aggregate_used": "50000", "deductible": "1000",
        "excluded_loss_categories": ["earthquake"],
        "covered_loss_categories": ["fire", "water damage"],
        "endorsements": ["Endorsement A"], "policy_wording_reference": "Policy schedule section 1"
    }
    data.update(overrides)
    return PolicyCreate.model_validate(data).model_dump(mode="json")


def claim(**overrides):
    data = {
        "policy_number": "POL-100", "claim_type": "Property",
        "country_code": "US", "currency": "USD", "incident_date": "2026-06-15"
    }
    data.update(overrides)
    return data


def test_potentially_covered_calculates_indicative_limit_and_deductible():
    result = assess_policy(policy(), claim(), CoverageAssessmentRequest(loss_category="fire", claimed_amount="50000"))
    assert result["coverage_status"] == "Potentially Covered"
    assert result["indicative_limit"] == "100000"
    assert result["indicative_net_amount"] == "49000"


def test_explicit_exclusion_is_flagged_without_net_amount():
    result = assess_policy(policy(), claim(), CoverageAssessmentRequest(loss_category="earthquake", claimed_amount="50000"))
    assert result["coverage_status"] == "Potentially Excluded"
    assert result["indicative_net_amount"] is None


def test_policy_period_is_inclusive_at_start_and_end():
    p = policy()
    assert assess_policy(p, claim(incident_date="2026-01-01"), CoverageAssessmentRequest(loss_category="fire"))["coverage_status"] == "Potentially Covered"
    assert assess_policy(p, claim(incident_date="2027-01-01"), CoverageAssessmentRequest(loss_category="fire"))["coverage_status"] == "Potentially Excluded"


def test_aggregate_remaining_caps_indicative_limit():
    p = policy(per_occurrence_limit="200000", aggregate_limit="60000", aggregate_used="50000")
    result = assess_policy(p, claim(), CoverageAssessmentRequest(loss_category="fire", claimed_amount="30000"))
    assert result["indicative_limit"] == "10000"
    assert result["indicative_net_amount"] == "9000"


def test_unlisted_category_requires_manual_review_when_covered_categories_are_configured():
    result = assess_policy(policy(), claim(), CoverageAssessmentRequest(loss_category="theft", claimed_amount="5000"))
    assert result["coverage_status"] == "Needs Manual Review"
    assert result["indicative_net_amount"] is None


def test_policy_date_range_must_be_valid():
    with pytest.raises(ValidationError):
        policy(effective_from="2027-01-01", effective_to="2026-01-01")
