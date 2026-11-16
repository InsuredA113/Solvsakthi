import pytest
from pydantic import ValidationError
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
        "deductible": "100", "currency": "INR", "country_code": "IN", "state_or_jurisdiction": "Example State",
        "region": "Example Region", "locality": "Example City", "postal_code": "000000",
        "loss_date": "2026-10-20", "tax_point_date": "2026-10-20",
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
    with pytest.raises(ValidationError):
        FinancialEstimateRequest.model_validate({
            **sample_payload().model_dump(mode="python"), "interest": data
        })



def test_currency_minor_units_follow_iso_currency_conventions():
    jpy = sample_payload(currency="JPY", taxes=[], charges=[], deductible="0", interest={"applicable": False, "basis": "simple_actual_365"},
                         line_items=[{"description": "Repair", "quantity": "1", "unit": "job", "unit_rate": "1200.5", "taxable": False, "depreciation_rate": "0"}])
    jpy_result = calculate_estimate(jpy)
    assert jpy_result["currency_minor_units"] == 0
    assert jpy_result["gross_repair_cost"] == "1201"

    kwd = sample_payload(currency="KWD", taxes=[], charges=[], deductible="0", interest={"applicable": False, "basis": "simple_actual_365"},
                         line_items=[{"description": "Repair", "quantity": "1", "unit": "job", "unit_rate": "1.2345", "taxable": False, "depreciation_rate": "0"}])
    kwd_result = calculate_estimate(kwd)
    assert kwd_result["currency_minor_units"] == 3
    assert kwd_result["gross_repair_cost"] == "1.235"


def test_tax_components_can_compound_when_configured():
    payload = sample_payload(taxes=[
        {"name": "Primary tax", "tax_type": "VAT", "jurisdiction": "Country", "rate_percent": "10",
         "applies_to": "depreciated_repair_subtotal", "compound_on_prior_taxes": False,
         "authority_reference": "Verified source"},
        {"name": "Secondary tax", "tax_type": "Local surcharge", "jurisdiction": "Region", "rate_percent": "5",
         "applies_to": "depreciated_repair_subtotal", "compound_on_prior_taxes": True,
         "authority_reference": "Verified source"}
    ])
    result = calculate_estimate(payload)
    assert result["taxes"][0]["amount"] == "90.00"
    assert result["taxes"][1]["taxable_base"] == "990.00"
    assert result["taxes"][1]["amount"] == "49.50"


def test_tax_rule_effective_dates_are_checked_against_tax_point_date():
    with pytest.raises(ValidationError):
        sample_payload(taxes=[{
            "name": "Future tax", "jurisdiction": "Country", "rate_percent": "5",
            "authority_reference": "Verified source", "effective_from": "2027-01-01"
        }])


def test_automatic_tax_mode_is_the_default():
    payload = sample_payload()
    assert payload.tax_mode == "automatic"

def test_manual_tax_mode_requires_explicit_selection():
    payload = sample_payload(tax_mode="manual")
    assert payload.tax_mode == "manual"
