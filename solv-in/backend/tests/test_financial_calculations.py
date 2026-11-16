import pytest
from app.financial_calculations import FinancialEstimateRequest, calculate_estimate


def sample_payload(**overrides):
    data = {
        "line_items": [{
            "description": "Roof tile replacement", "quantity": "1", "unit": "job",
            "unit_rate": "1000", "taxable": True, "depreciation_rate": "10"
        }],
        "taxes": [{
            "name": "State repair tax", "jurisdiction": "Example State",
            "rate_percent": "8", "applies_to": "depreciated_repair_subtotal",
            "authority_reference": "Verified schedule reference"
        }],
        "charges": [{"name": "Inspection fee", "amount": "50", "charge_type": "fee", "taxable": False}],
        "deductible": "100", "currency": "INR", "state_or_jurisdiction": "Example State",
        "interest": {
            "applicable": True, "annual_rate_percent": "12", "principal": "5000",
            "days": 30, "basis": "simple_actual_365", "authority_reference": "Policy clause 4.2"
        },
        "pricing_source": "Approved repairer quotation Q-100",
        "pricing_effective_date": "2026-11-01"
    }
    data.update(overrides)
    return FinancialEstimateRequest.model_validate(data)


def test_decimal_calculation_includes_depreciation_tax_fee_interest_and_deductible():
    result = calculate_estimate(sample_payload())
    assert result["gross_repair_cost"] == "1000.00"
    assert result["depreciation"] == "100.00"
    assert result["net_repair_cost"] == "900.00"
    assert result["total_tax"] == "72.00"
    assert result["total_fees_and_charges"] == "50.00"
    assert result["interest"]["amount"] == "49.32"
    assert result["preliminary_net_estimate"] == "971.32"


def test_interest_is_zero_unless_explicitly_applicable():
    payload = sample_payload(interest={"applicable": False, "basis": "simple_actual_365"})
    result = calculate_estimate(payload)
    assert result["interest"]["amount"] == "0.00"


def test_tax_can_be_omitted_when_not_applicable():
    result = calculate_estimate(sample_payload(taxes=[]))
    assert result["total_tax"] == "0.00"


def test_applicable_interest_requires_contract_or_legal_reference():
    data = {
        "applicable": True, "annual_rate_percent": "12", "principal": "1000",
        "days": 30, "basis": "simple_actual_365", "authority_reference": ""
    }
    with pytest.raises(Exception):
        FinancialEstimateRequest.model_validate({
            **sample_payload().model_dump(mode="python"), "interest": data
        })
