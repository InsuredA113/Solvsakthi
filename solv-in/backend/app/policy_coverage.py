from datetime import date
from decimal import Decimal
import re
import pycountry
from pydantic import BaseModel, Field, field_validator, model_validator

ZERO = Decimal("0")


class PolicyCreate(BaseModel):
    policy_number: str = Field(min_length=2, max_length=80)
    insured_name: str = Field(min_length=2, max_length=120)
    claim_type: str
    country_code: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    effective_from: date
    effective_to: date
    per_occurrence_limit: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    aggregate_limit: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    aggregate_used: Decimal = Field(default=ZERO, ge=0, max_digits=18, decimal_places=6)
    deductible: Decimal = Field(default=ZERO, ge=0, max_digits=18, decimal_places=6)
    excluded_loss_categories: list[str] = Field(default_factory=list, max_length=100)
    covered_loss_categories: list[str] = Field(default_factory=list, max_length=100)
    endorsements: list[str] = Field(default_factory=list, max_length=100)
    policy_wording_reference: str = Field(min_length=3, max_length=500)
    notes: str = Field(default="", max_length=2000)

    @field_validator("policy_number", "insured_name", "policy_wording_reference")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("claim_type")
    @classmethod
    def valid_claim_type(cls, value: str) -> str:
        if value not in {"Property", "Casualty", "Both"}:
            raise ValueError("Claim type must be Property, Casualty or Both.")
        return value

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value) or pycountry.countries.get(alpha_2=value) is None:
            raise ValueError("Country must be an ISO 3166-1 alpha-2 code.")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value) or pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError("Currency must be an ISO 4217 code.")
        return value

    @model_validator(mode="after")
    def validate_policy_dates_and_limits(self):
        if self.effective_to < self.effective_from:
            raise ValueError("Policy end date must be on or after the start date.")
        if self.aggregate_limit is not None and self.aggregate_used > self.aggregate_limit:
            raise ValueError("Aggregate used cannot exceed the aggregate limit.")
        return self


class CoverageAssessmentRequest(BaseModel):
    loss_category: str = Field(min_length=2, max_length=100)
    claimed_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    notes: str = Field(default="", max_length=2000)

    @field_validator("loss_category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        return " ".join(value.strip().casefold().split())


def assess_policy(policy: dict | None, claim: dict, request: CoverageAssessmentRequest) -> dict:
    if not policy:
        return {
            "coverage_status": "Policy Not Found",
            "reason_codes": ["POLICY_NOT_FOUND"],
            "explanation": "No policy record matches the claim policy number. Verify the policy schedule and endorsements.",
            "indicative_limit": None,
            "indicative_deductible": None,
            "indicative_net_amount": None
        }

    reasons = []
    incident_date = date.fromisoformat(str(claim["incident_date"])[:10])
    start = date.fromisoformat(str(policy["effective_from"])[:10])
    end = date.fromisoformat(str(policy["effective_to"])[:10])

    if policy.get("claim_type") not in {claim.get("claim_type"), "Both"}:
        reasons.append("POLICY_CLAIM_TYPE_MISMATCH")
    if policy.get("country_code") != claim.get("country_code"):
        reasons.append("COUNTRY_MISMATCH")
    if policy.get("currency") != claim.get("currency"):
        reasons.append("CURRENCY_MISMATCH")
    if not (start <= incident_date <= end):
        reasons.append("OUTSIDE_POLICY_PERIOD")

    category = request.loss_category
    excluded = {" ".join(x.strip().casefold().split()) for x in policy.get("excluded_loss_categories", [])}
    covered = {" ".join(x.strip().casefold().split()) for x in policy.get("covered_loss_categories", [])}
    if category in excluded:
        reasons.append("EXPLICIT_EXCLUSION_MATCH")
    if covered and category not in covered:
        reasons.append("CATEGORY_NOT_LISTED_AS_COVERED")

    hard_fail = {"POLICY_CLAIM_TYPE_MISMATCH", "COUNTRY_MISMATCH", "CURRENCY_MISMATCH", "OUTSIDE_POLICY_PERIOD", "EXPLICIT_EXCLUSION_MATCH"}
    if any(code in hard_fail for code in reasons):
        status = "Potentially Excluded"
    elif "CATEGORY_NOT_LISTED_AS_COVERED" in reasons:
        status = "Needs Manual Review"
    else:
        status = "Potentially Covered"

    limit = Decimal(str(policy["per_occurrence_limit"]))
    if policy.get("aggregate_limit") is not None:
        aggregate_remaining = max(ZERO, Decimal(str(policy["aggregate_limit"])) - Decimal(str(policy.get("aggregate_used", "0"))))
        limit = min(limit, aggregate_remaining)

    indicative_net = None
    if request.claimed_amount is not None and status == "Potentially Covered":
        eligible_before_deductible = min(request.claimed_amount, limit)
        indicative_net = max(ZERO, eligible_before_deductible - Decimal(str(policy.get("deductible", "0"))))

    return {
        "coverage_status": status,
        "reason_codes": reasons or ["NO_CONFIGURED_CONFLICT_FOUND"],
        "explanation": "Rule-based preliminary screening only. Review the full policy wording, schedules, endorsements, exclusions, conditions, limits and applicable law before making any coverage decision.",
        "indicative_limit": str(limit),
        "indicative_deductible": str(policy.get("deductible", "0")),
        "indicative_net_amount": str(indicative_net) if indicative_net is not None else None
    }
