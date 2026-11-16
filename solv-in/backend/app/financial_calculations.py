from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)

class EstimateLineItem(BaseModel):
    description: str = Field(min_length=2, max_length=200)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    unit: str = Field(min_length=1, max_length=30)
    unit_rate: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    taxable: bool = True
    depreciation_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, decimal_places=4)

class TaxComponent(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    jurisdiction: str = Field(min_length=2, max_length=120)
    rate_percent: Decimal = Field(ge=0, le=100, max_digits=7, decimal_places=4)
    applies_to: Literal["repair_subtotal", "depreciated_repair_subtotal", "fees"] = "depreciated_repair_subtotal"
    authority_reference: str = Field(min_length=2, max_length=250)

class Charge(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    charge_type: Literal["fee", "other"] = "fee"
    taxable: bool = False
    authority_reference: str = Field(default="", max_length=250)

class InterestTerms(BaseModel):
    applicable: bool = False
    annual_rate_percent: Decimal | None = Field(default=None, ge=0, le=100, max_digits=7, decimal_places=4)
    principal: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    days: int | None = Field(default=None, ge=0, le=36500)
    basis: Literal["simple_actual_365"] = "simple_actual_365"
    authority_reference: str = Field(default="", max_length=250)

    @model_validator(mode="after")
    def require_terms_when_applicable(self):
        if self.applicable and (self.annual_rate_percent is None or self.principal is None or self.days is None or not self.authority_reference.strip()):
            raise ValueError("Applicable interest requires rate, principal, days and a policy/legal authority reference.")
        return self

class FinancialEstimateRequest(BaseModel):
    line_items: list[EstimateLineItem] = Field(min_length=1, max_length=100)
    taxes: list[TaxComponent] = Field(default_factory=list, max_length=10)
    charges: list[Charge] = Field(default_factory=list, max_length=30)
    deductible: Decimal = Field(default=ZERO, ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    state_or_jurisdiction: str = Field(min_length=2, max_length=120)
    interest: InterestTerms = Field(default_factory=InterestTerms)
    notes: str = Field(default="", max_length=2000)
    pricing_source: str = Field(min_length=3, max_length=250)
    pricing_effective_date: str = Field(min_length=10, max_length=10)

    @field_validator("currency")
    @classmethod
    def currency_upper(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_tax_and_interest(self):
        if self.interest.applicable and self.interest.authority_reference.strip() == "":
            raise ValueError("Interest must be supported by policy or legal terms.")
        return self

def calculate_estimate(payload: FinancialEstimateRequest) -> dict:
    detail = []
    gross = ZERO
    depreciation = ZERO
    taxable_gross = ZERO
    taxable_depreciated = ZERO
    for item in payload.line_items:
        gross_line = money(item.quantity * item.unit_rate)
        dep_line = money(gross_line * item.depreciation_rate / Decimal("100"))
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
    gross, depreciation = money(gross), money(depreciation)
    repair_net = money(gross - depreciation)
    fees = money(sum((c.amount for c in payload.charges), ZERO))
    taxable_fees = money(sum((c.amount for c in payload.charges if c.taxable), ZERO))
    tax_rows = []
    total_tax = ZERO
    for tax in payload.taxes:
        if tax.applies_to == "repair_subtotal":
            base = taxable_gross
        elif tax.applies_to == "fees":
            base = taxable_fees
        else:
            base = taxable_depreciated
        amount = money(base * tax.rate_percent / Decimal("100"))
        total_tax += amount
        tax_rows.append({"name": tax.name, "jurisdiction": tax.jurisdiction, "rate_percent": str(tax.rate_percent),
                         "taxable_base": str(money(base)), "amount": str(amount),
                         "authority_reference": tax.authority_reference})
    interest_amount = ZERO
    if payload.interest.applicable:
        interest_amount = money(payload.interest.principal * payload.interest.annual_rate_percent / Decimal("100")
                                * Decimal(payload.interest.days) / Decimal("365"))
    total_before_deductible = money(repair_net + fees + total_tax + interest_amount)
    net_preliminary = money(max(ZERO, total_before_deductible - payload.deductible))
    return {
        "currency": payload.currency, "state_or_jurisdiction": payload.state_or_jurisdiction,
        "line_items": detail, "gross_repair_cost": str(gross), "depreciation": str(depreciation),
        "net_repair_cost": str(repair_net), "charges": [c.model_dump(mode="json") for c in payload.charges],
        "total_fees_and_charges": str(fees), "taxes": tax_rows, "total_tax": str(money(total_tax)),
        "interest": {"applicable": payload.interest.applicable, "amount": str(interest_amount),
                     "annual_rate_percent": str(payload.interest.annual_rate_percent) if payload.interest.annual_rate_percent is not None else None,
                     "days": payload.interest.days, "basis": payload.interest.basis,
                     "authority_reference": payload.interest.authority_reference},
        "deductible": str(money(payload.deductible)), "total_before_deductible": str(total_before_deductible),
        "preliminary_net_estimate": str(net_preliminary), "pricing_source": payload.pricing_source,
        "pricing_effective_date": payload.pricing_effective_date, "notes": payload.notes,
        "calculation_method": "Python Decimal; half-up rounding to currency minor units; simple interest on actual days/365."
    }
