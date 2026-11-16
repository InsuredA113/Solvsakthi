from app.claims_analytics import build_claims_analytics, is_in_scope_claim


def test_scope_includes_all_property_and_commercial_casualty_only():
    assert is_in_scope_claim({"claim_type": "Property"})
    assert is_in_scope_claim({"claim_type": "Casualty", "casualty_segment": "Commercial"})
    assert not is_in_scope_claim({"claim_type": "Casualty"})
    assert not is_in_scope_claim({"claim_type": "Casualty", "casualty_segment": "Personal"})


def test_analytics_excludes_unclassified_casualty_from_portfolio_totals():
    result = build_claims_analytics([
        {"claim_id": "P1", "claim_type": "Property", "status": "Open", "country_code": "IN", "currency": "INR", "estimated_loss": 1000},
        {"claim_id": "C1", "claim_type": "Casualty", "casualty_segment": "Commercial", "status": "Closed", "country_code": "US", "currency": "USD", "estimated_loss": 5000},
        {"claim_id": "OLD1", "claim_type": "Casualty", "status": "Open", "country_code": "GB", "currency": "GBP", "estimated_loss": 900}
    ])
    assert result["summary"] == {
        "total_in_scope_claims": 2,
        "property_claims": 1,
        "commercial_casualty_claims": 1,
        "open_claims": 1,
        "closed_claims": 1,
        "unclassified_legacy_casualty_claims": 1
    }


def test_exposure_is_grouped_by_currency_without_cross_currency_addition():
    result = build_claims_analytics([
        {"claim_id": "P1", "claim_type": "Property", "currency": "INR", "estimated_loss": "1000.50"},
        {"claim_id": "P2", "claim_type": "Property", "currency": "USD", "estimated_loss": "100"}
    ])
    rows = {row["currency"]: row for row in result["reported_exposure_by_currency"]}
    assert rows["INR"]["reported_estimated_loss"] == "1000.50"
    assert rows["USD"]["reported_estimated_loss"] == "100"


def test_only_latest_estimate_per_in_scope_claim_is_aggregated():
    claims = [
        {"claim_id": "P1", "claim_type": "Property", "currency": "USD"},
        {"claim_id": "OLD1", "claim_type": "Casualty", "currency": "USD"}
    ]
    estimates = [
        {"claim_id": "P1", "currency": "USD", "preliminary_net_estimate": "100", "created_at": "2026-01-01", "status": "Preliminary - Pending Review"},
        {"claim_id": "P1", "currency": "USD", "preliminary_net_estimate": "250", "created_at": "2026-02-01", "status": "Approved"},
        {"claim_id": "OLD1", "currency": "USD", "preliminary_net_estimate": "900", "created_at": "2026-03-01", "status": "Approved"}
    ]
    result = build_claims_analytics(claims, estimates)
    assert result["latest_preliminary_estimates_by_currency"] == [{
        "currency": "USD", "claim_count": 1,
        "latest_preliminary_estimate_total": "250",
        "pending_review_count": 0, "approved_estimate_count": 1
    }]


def test_status_country_and_month_breakdowns_are_returned():
    result = build_claims_analytics([
        {"claim_id": "P1", "claim_type": "Property", "status": "FNOL Received", "country_code": "IN", "incident_date": "2026-03-10"},
        {"claim_id": "P2", "claim_type": "Property", "status": "Closed", "country_code": "IN", "incident_date": "2026-03-12"},
        {"claim_id": "C1", "claim_type": "Casualty", "casualty_segment": "Commercial", "status": "Investigation", "country_code": "US", "incident_date": "2026-04-01"}
    ])
    assert {x["status"]: x["claim_count"] for x in result["by_status"]} == {
        "Closed": 1, "FNOL Received": 1, "Investigation": 1
    }
    assert {x["country_code"]: x["claim_count"] for x in result["by_country"]} == {"IN": 2, "US": 1}
    assert result["by_month"] == [{"month": "2026-03", "claim_count": 2}, {"month": "2026-04", "claim_count": 1}]
