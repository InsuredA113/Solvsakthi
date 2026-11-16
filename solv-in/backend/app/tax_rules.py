from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
import re
import pycountry
from pydantic import BaseModel, Field, field_validator, model_validator

class TaxRuleCreate(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    admin_area: str = Field(default="", max_length=120)
    region: str = Field(default="", max_length=120)
    locality: str = Field(default="", max_length=120)
    tax_name: str = Field(min_length=2, max_length=100)
    tax_type: str = Field(min_length=2, max_length=50)
    rate_percent: Decimal = Field(ge=Decimal("0"), le=Decimal("100"), max_digits=7, decimal_places=4)
    applies_to: Literal["repair_subtotal", "depreciated_repair_subtotal", "fees", "repair_and_fees"] = "depreciated_repair_subtotal"
    compound_on_prior_taxes: bool = False
    tax_point_type: str = Field(default="Tax point date", min_length=2, max_length=80)
    effective_from: date
    effective_to: date | None = None
    authority_name: str = Field(min_length=2, max_length=160)
    authority_reference: str = Field(min_length=3, max_length=500)
    source_url: str | None = Field(default=None, max_length=1000)
    notes: str = Field(default="", max_length=2000)

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value):
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value) or pycountry.countries.get(alpha_2=value) is None:
            raise ValueError("Use a valid ISO 3166-1 alpha-2 country code.")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value):
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value) or pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError("Use a valid ISO 4217 currency code.")
        return value

    @model_validator(mode="after")
    def date_range_valid(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("Effective-to date cannot be earlier than effective-from date.")
        return self

class TaxRuleStatusUpdate(BaseModel):
    status: Literal["Active", "Inactive"]
    verification_note: str = Field(default="", max_length=1000)

def rule_is_applicable(rule: dict, *, country_code: str, currency: str, admin_area: str,
                       region: str, locality: str, tax_point_date: date) -> bool:
    if rule.get("status") != "Active":
        return False
    if rule.get("country_code") != country_code.upper():
        return False
    if rule.get("currency") != currency.upper():
        return False
    if rule.get("admin_area") and rule["admin_area"].casefold() != admin_area.casefold():
        return False
    if rule.get("region") and rule["region"].casefold() != region.casefold():
        return False
    if rule.get("locality") and rule["locality"].casefold() != locality.casefold():
        return False
    start = date.fromisoformat(str(rule["effective_from"])[:10])
    end = date.fromisoformat(str(rule["effective_to"])[:10]) if rule.get("effective_to") else None
    return start <= tax_point_date and (end is None or tax_point_date <= end)

def rule_specificity(rule: dict) -> int:
    return sum(bool(rule.get(field)) for field in ("admin_area", "region", "locality"))


def select_applicable_tax_rules(rules: list[dict], *, country_code: str, currency: str,
                                admin_area: str, region: str, locality: str,
                                tax_point_date: date) -> tuple[list[dict], list[str]]:
    applicable = [rule for rule in rules if rule_is_applicable(
        rule, country_code=country_code, currency=currency, admin_area=admin_area,
        region=region, locality=locality, tax_point_date=tax_point_date
    )]
    applicable.sort(key=rule_specificity, reverse=True)
    selected, ambiguous = [], []
    for name in sorted({str(rule.get("tax_name", "")).casefold() for rule in applicable}):
        group = [rule for rule in applicable if str(rule.get("tax_name", "")).casefold() == name]
        top_specificity = rule_specificity(group[0])
        best = [rule for rule in group if rule_specificity(rule) == top_specificity]
        if len(best) > 1:
            ambiguous.append(str(group[0].get("tax_name", name)))
        else:
            selected.append(best[0])
    return selected, ambiguous
