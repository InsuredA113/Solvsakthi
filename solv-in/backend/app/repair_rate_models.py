from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RepairRateCreate(BaseModel):
    country_code: str = Field(min_length=2, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    state: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=2, max_length=80)
    item_code: str = Field(min_length=2, max_length=60)
    item_name: str = Field(min_length=2, max_length=180)
    description: str = Field(default="", max_length=1000)
    unit: str = Field(min_length=1, max_length=30)
    unit_rate: Decimal = Field(gt=Decimal("0"), max_digits=18, decimal_places=4)
    effective_from: date
    effective_to: date | None = None
    source_name: str = Field(min_length=2, max_length=180)
    source_reference: str = Field(min_length=3, max_length=500)
    source_url: str | None = Field(default=None, max_length=1000)
    notes: str = Field(default="", max_length=2000)

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 2 or not value.isalpha():
            raise ValueError("Country must be an ISO alpha-2 code.")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be an ISO 4217 code.")
        return value

    @field_validator("state", "city", "category", "item_code", "item_name", "unit", "source_name", "source_reference")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("effective_to")
    @classmethod
    def validate_effective_range(cls, value, info):
        start = info.data.get("effective_from")
        if value and start and value < start:
            raise ValueError("Effective-to date cannot be earlier than effective-from date.")
        return value


class RepairRateStatusUpdate(BaseModel):
    status: Literal["Active", "Inactive"]
    verification_note: str = Field(default="", max_length=1000)
