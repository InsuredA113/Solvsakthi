from collections import defaultdict
from decimal import Decimal, InvalidOperation


def is_in_scope_claim(claim: dict) -> bool:
    claim_type = claim.get("claim_type")
    return claim_type == "Property" or (
        claim_type == "Casualty" and claim.get("casualty_segment") == "Commercial"
    )


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def build_claims_analytics(claims: list[dict], estimates: list[dict] | None = None) -> dict:
    """Build currency-safe portfolio reporting for all Property and Commercial Casualty claims."""
    scoped = [claim for claim in claims if is_in_scope_claim(claim)]
    unclassified_casualty = [
        claim for claim in claims
        if claim.get("claim_type") == "Casualty" and claim.get("casualty_segment") != "Commercial"
    ]
    claim_by_id = {claim.get("claim_id"): claim for claim in scoped if claim.get("claim_id")}

    status_counts = defaultdict(int)
    country_counts = defaultdict(int)
    monthly_counts = defaultdict(int)
    line_counts = defaultdict(int)
    industry_counts = defaultdict(int)
    currency_rows = defaultdict(lambda: {"claim_count": 0, "reported_estimated_loss": Decimal("0")})
    for claim in scoped:
        status_counts[claim.get("status") or "Unknown"] += 1
        country_counts[claim.get("country_code") or "Unknown"] += 1
        if claim.get("claim_type") == "Property":
            line_counts["Property - " + (claim.get("property_occupancy") or "Unspecified")] += 1
        else:
            line_counts[claim.get("commercial_liability_type") or "Commercial Casualty - Unspecified"] += 1
            industry_counts[claim.get("industry") or "Unspecified"] += 1
        incident_date = str(claim.get("incident_date") or "")
        if len(incident_date) >= 7:
            monthly_counts[incident_date[:7]] += 1
        currency = claim.get("currency") or "Unknown"
        row = currency_rows[currency]
        row["claim_count"] += 1
        if claim.get("estimated_loss") is not None:
            row["reported_estimated_loss"] += _decimal(claim["estimated_loss"])

    latest_by_claim = {}
    for estimate in estimates or []:
        claim_id = estimate.get("claim_id")
        if claim_id not in claim_by_id:
            continue
        previous = latest_by_claim.get(claim_id)
        if previous is None or str(estimate.get("created_at") or "") > str(previous.get("created_at") or ""):
            latest_by_claim[claim_id] = estimate

    estimate_rows = defaultdict(lambda: {
        "claim_count": 0, "latest_preliminary_estimate_total": Decimal("0"),
        "pending_review_count": 0, "approved_estimate_count": 0
    })
    for claim_id, estimate in latest_by_claim.items():
        currency = estimate.get("currency") or claim_by_id[claim_id].get("currency") or "Unknown"
        row = estimate_rows[currency]
        row["claim_count"] += 1
        row["latest_preliminary_estimate_total"] += _decimal(estimate.get("preliminary_net_estimate"))
        review_status = estimate.get("review_status") or estimate.get("status") or ""
        if "Pending" in review_status:
            row["pending_review_count"] += 1
        if review_status == "Approved":
            row["approved_estimate_count"] += 1

    property_count = sum(1 for claim in scoped if claim.get("claim_type") == "Property")
    casualty_count = sum(1 for claim in scoped if claim.get("claim_type") == "Casualty")
    closed_count = sum(1 for claim in scoped if claim.get("status") == "Closed")

    return {
        "scope": "All Property claims and Commercial Casualty claims only",
        "summary": {
            "total_in_scope_claims": len(scoped),
            "property_claims": property_count,
            "commercial_casualty_claims": casualty_count,
            "open_claims": len(scoped) - closed_count,
            "closed_claims": closed_count,
            "unclassified_legacy_casualty_claims": len(unclassified_casualty)
        },
        "by_status": [
            {"status": key, "claim_count": value}
            for key, value in sorted(status_counts.items())
        ],
        "by_country": [
            {"country_code": key, "claim_count": value}
            for key, value in sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "by_claim_line": [
            {"claim_line": key, "claim_count": value}
            for key, value in sorted(line_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "by_industry": [
            {"industry": key, "claim_count": value}
            for key, value in sorted(industry_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "reported_exposure_by_currency": [
            {"currency": key, "claim_count": value["claim_count"],
             "reported_estimated_loss": str(value["reported_estimated_loss"])}
            for key, value in sorted(currency_rows.items())
        ],
        "latest_preliminary_estimates_by_currency": [
            {"currency": key, "claim_count": value["claim_count"],
             "latest_preliminary_estimate_total": str(value["latest_preliminary_estimate_total"]),
             "pending_review_count": value["pending_review_count"],
             "approved_estimate_count": value["approved_estimate_count"]}
            for key, value in sorted(estimate_rows.items())
        ],
        "by_month": [
            {"month": key, "claim_count": value}
            for key, value in sorted(monthly_counts.items())
        ]
    }
