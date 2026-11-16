from decimal import Decimal, ROUND_HALF_UP
import re
import pycountry
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

ZERO = Decimal("0")
CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")

def money(value: Decimal, currency: str) -> Decimal:
    # Babel/CLDR supplies the standard minor-unit precision for ISO 4217 currencies:
    # e.g. JPY has 0 decimals while KWD has 3. No hard-coded India-only assumption.
    from babel.numbers import get_currency_precision
    precision = get_currency_precision(currency)
    quantum = Decimal("1").scaleb(-precision)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)

class EstimateLineItem(BaseModel):
    description: str = Field(min_length=2, max_length=200)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    unit: str = Field(min_length=1, max_length=30)
    unit_rate: Decimal = Field(ge=0, max_digits=16, decimal_places=6)
    taxable: bool = True
    depreciation_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, decimal_places=4)

class TaxComponent(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    tax_type: str = Field(default="Other", min_length=2, max_length=50)
    jurisdiction: str = Field(min_length=2, max_length=160)
    rate_percent: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    applies_to: Literal["repair_subtotal", "depreciated_repair_subtotal", "fees", "repair_and_fees"] = "depreciated_repair_subtotal"
    compound_on_prior_taxes: bool = False
    authority_reference: str = Field(min_length=2, max_length=300)
    effective_from: str | None = Field(default=None, min_length=10, max_length=10)
    effective_to: str | None = Field(default=None, min_length=10, max_length=10)

class Charge(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    amount: Decimal = Field(ge=0, max_digits=16, decimal_places=6)
    charge_type: Literal["fee", "other", "assessment", "mitigation", "salvage", "legal", "adjusting"] = "fee"
    taxable: bool = False
    authority_reference: str = Field(default="", max_length=300)

class InterestTerms(BaseModel):
    applicable: bool = False
    annual_rate_percent: Decimal | None = Field(default=None, ge=0, le=100, max_digits=7, decimal_places=4)
    principal: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=6)
    days: int | None = Field(default=None, ge=0, le=36500)
    basis: Literal["simple_actual_365", "simple_actual_360", "simple_actual_366", "simple_30_360"] = "simple_actual_365"
    authority_reference: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def require_terms_when_applicable(self):
        if self.applicable and (self.annual_rate_percent is None or self.principal is None or self.days is None or not self.authority_reference.strip()):
            raise ValueError("Applicable interest requires rate, principal, days and a policy/legal authority reference.")
        return self

class FinancialEstimateRequest(BaseModel):
    line_items: list[EstimateLineItem] = Field(min_length=1, max_length=100)
    taxes: list[TaxComponent] = Field(default_factory=list, max_length=20)
    tax_mode: Literal["automatic", "manual"] = "automatic"
    confirm_automatic_tax_application: bool = False
    charges: list[Charge] = Field(default_factory=list, max_length=50)
    deductible: Decimal = Field(default=ZERO, ge=0, max_digits=16, decimal_places=6)
    currency: str = Field(min_length=3, max_length=3)
    country_code: str = Field(min_length=2, max_length=2)
    state_or_jurisdiction: str = Field(min_length=2, max_length=160)
    region: str = Field(default="", max_length=120)
    locality: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=30)
    loss_date: str = Field(min_length=10, max_length=10)
    tax_point_date: str = Field(min_length=10, max_length=10)
    interest: InterestTerms = Field(default_factory=InterestTerms)
    notes: str = Field(default="", max_length=2000)
    pricing_source: str = Field(min_length=3, max_length=250)
    pricing_effective_date: str = Field(min_length=10, max_length=10)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str) -> str:
        value = value.upper()
        if not CURRENCY_CODE.fullmatch(value) or pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError("Currency must be a three-letter ISO 4217 code.")
        return value

    @field_validator("country_code")
    @classmethod
    def country_upper(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value) or pycountry.countries.get(alpha_2=value) is None:
            raise ValueError("Country must be a two-letter ISO 3166-1 alpha-2 code.")
        return value

    @model_validator(mode="after")
    def validate_dates_and_tax_rules(self):
        from datetime import date
        try:
            loss_date = date.fromisoformat(self.loss_date)
            date.fromisoformat(self.pricing_effective_date)
            tax_point_date = date.fromisoformat(self.tax_point_date)
        except ValueError as exc:
            raise ValueError("Loss date, tax point date and pricing effective date must use YYYY-MM-DD.") from exc
        for tax in self.taxes:
            if tax.effective_from and date.fromisoformat(tax.effective_from) > tax_point_date:
                raise ValueError(f"Tax rule '{tax.name}' is not effective on the tax point date.")
            if tax.effective_to and date.fromisoformat(tax.effective_to) < tax_point_date:
                raise ValueError(f"Tax rule '{tax.name}' expired before the tax point date.")
        return self

def calculate_estimate(payload: FinancialEstimateRequest) -> dict:
    currency = payload.currency
    detail = []
    gross = ZERO
    depreciation = ZERO
    taxable_gross = ZERO
    taxable_depreciated = ZERO
    for item in payload.line_items:
        gross_line = money(item.quantity * item.unit_rate, currency)
        dep_line = money(gross_line * item.depreciation_rate / Decimal("100"), currency)
        net_line = gross_line - dep_line
        gross += gross_line
        depreciation += dep_line
        if item.taxable:
            taxable_gross += gross_line
            taxable_depreciated += net_line
        detail.append({
            "description": item.description, "quantity": str(item.quantity), "unit": item.unit,
            "unit_rate": str(item.unit_rate), "gross_amount": str(gross_line),
            "depreciation_rate_percent": str(item.depreciation_rate),
            "depreciation_amount": str(dep_line), "net_amount": str(net_line), "taxable": item.taxable
        })
    gross, depreciation = money(gross, currency), money(depreciation, currency)
    repair_net = money(gross - depreciation, currency)
    fees = money(sum((c.amount for c in payload.charges), ZERO), currency)
    taxable_fees = money(sum((c.amount for c in payload.charges if c.taxable), ZERO), currency)
    tax_rows = []
    total_tax = ZERO
    for tax in payload.taxes:
        if tax.applies_to == "repair_subtotal":
            base = taxable_gross
        elif tax.applies_to == "fees":
            base = taxable_fees
        elif tax.applies_to == "repair_and_fees":
            base = taxable_depreciated + taxable_fees
        else:
            base = taxable_depreciated
        if tax.compound_on_prior_taxes:
            base += total_tax
        base = money(base, currency)
        amount = money(base * tax.rate_percent / Decimal("100"), currency)
        total_tax += amount
        tax_rows.append({
            "name": tax.name, "tax_type": tax.tax_type, "jurisdiction": tax.jurisdiction,
            "rate_percent": str(tax.rate_percent), "taxable_base": str(base),
            "compound_on_prior_taxes": tax.compound_on_prior_taxes, "amount": str(amount),
            "authority_reference": tax.authority_reference,
            "effective_from": tax.effective_from, "effective_to": tax.effective_to
        })
    total_tax = money(total_tax, currency)
    interest_amount = money(ZERO, currency)
    if payload.interest.applicable:
        denominator = {
            "simple_actual_365": Decimal("365"),
            "simple_actual_360": Decimal("360"),
            "simple_actual_366": Decimal("366"),
            "simple_30_360": Decimal("360")
        }[payload.interest.basis]
        interest_amount = money(
            payload.interest.principal * payload.interest.annual_rate_percent / Decimal("100")
            * Decimal(payload.interest.days) / denominator, currency
        )
    total_before_deductible = money(repair_net + fees + total_tax + interest_amount, currency)
    deductible = money(payload.deductible, currency)
    net_preliminary = money(max(ZERO, total_before_deductible - deductible), currency)
    return {
        "currency": currency, "country_code": payload.country_code,
        "state_or_jurisdiction": payload.state_or_jurisdiction, "region": payload.region,
        "locality": payload.locality, "postal_code": payload.postal_code, "loss_date": payload.loss_date, "tax_point_date": payload.tax_point_date,
        "currency_minor_units": __import__("babel.numbers", fromlist=["get_currency_precision"]).get_currency_precision(currency),
        "line_items": detail, "gross_repair_cost": str(gross), "depreciation": str(depreciation),
        "net_repair_cost": str(repair_net), "charges": [c.model_dump(mode="json") for c in payload.charges],
        "total_fees_and_charges": str(fees), "taxes": tax_rows, "total_tax": str(total_tax),
        "interest": {"applicable": payload.interest.applicable, "amount": str(interest_amount),
                     "annual_rate_percent": str(payload.interest.annual_rate_percent) if payload.interest.annual_rate_percent is not None else None,
                     "days": payload.interest.days, "basis": payload.interest.basis,
                     "authority_reference": payload.interest.authority_reference},
        "deductible": str(deductible), "total_before_deductible": str(total_before_deductible),
        "preliminary_net_estimate": str(net_preliminary), "pricing_source": payload.pricing_source,
        "pricing_effective_date": payload.pricing_effective_date, "notes": payload.notes,
        "calculation_method": "Python Decimal; currency-specific ISO 4217 minor units via CLDR; half-up rounding; explicitly selected tax bases and interest day-count basis."
    }
