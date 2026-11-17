from datetime import date, datetime, timezone
from typing import Literal
import re
import pycountry
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ClaimType = Literal["Property", "Casualty"]
CommercialLiabilityType = Literal[
    "Commercial General Liability", "Professional Liability", "Product Liability",
    "Employers Liability", "Public Liability", "Other Commercial Liability"
]
PropertyOccupancy = Literal["Residential", "Commercial", "Industrial", "Mixed Use", "Other"]
ClaimStatus = Literal[
    "FNOL Received", "Under Review", "Investigation", "Assessment",
    "Pending Approval", "Approved", "Closed"
]

class ClaimCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: str = Field(min_length=2, max_length=120)
    loss_cause: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    policy_number: str = Field(min_length=2, max_length=80)
    claim_type: ClaimType
    property_occupancy: PropertyOccupancy | None = None
    casualty_segment: Literal["Commercial"] | None = None
    business_name: str | None = Field(default=None, min_length=2, max_length=160)
    industry: str | None = Field(default=None, min_length=2, max_length=120)
    commercial_liability_type: CommercialLiabilityType | None = None
    incident_date: date
    incident_description: str = Field(min_length=5, max_length=5000)
    loss_location: str = Field(min_length=2, max_length=250)
    country_code: str = Field(min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=120)
    locality: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=30)
    estimated_loss: float | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def enforce_product_scope(self):
        if self.claim_type == "Casualty":
            if self.casualty_segment != "Commercial":
                raise ValueError("Solv-In supports commercial casualty claims only.")
            if not self.business_name or not self.industry or not self.commercial_liability_type:
                raise ValueError("Commercial casualty claims require the business name, industry and liability type.")
            if self.property_occupancy is not None:
                raise ValueError("Property occupancy is only valid for Property claims.")
        elif self.casualty_segment or self.business_name or self.industry or self.commercial_liability_type:
            raise ValueError("Commercial casualty fields are only valid for Casualty claims.")
        return self

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value) or pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError("Currency must be a three-letter ISO 4217 code.")
        return value

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Country code is required.")
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value) or pycountry.countries.get(alpha_2=value) is None:
            raise ValueError("Country must be a two-letter ISO 3166-1 alpha-2 code.")
        return value



class ClaimStatusUpdate(BaseModel):
    """Validated status transition payload for an existing claim."""
    status: ClaimStatus
