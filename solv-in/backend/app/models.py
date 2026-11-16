from datetime import date, datetime, timezone
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

ClaimType = Literal["Property", "Casualty"]
ClaimStatus = Literal[
    "FNOL Received", "Under Review", "Investigation", "Assessment",
    "Pending Approval", "Approved", "Closed"
]

class ClaimCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: str = Field(min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    policy_number: str = Field(min_length=2, max_length=80)
    claim_type: ClaimType
    incident_date: date
    incident_description: str = Field(min_length=5, max_length=5000)
    loss_location: str = Field(min_length=2, max_length=250)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=120)
    locality: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=30)
    estimated_loss: float | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("Currency must be a three-letter ISO 4217 code.")
        return value

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("Country must be a two-letter ISO 3166-1 alpha-2 code.")
        return value

