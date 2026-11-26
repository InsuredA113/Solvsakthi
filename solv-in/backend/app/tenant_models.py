from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

TenantStatus = Literal["Active", "Inactive", "Suspended"]

class TenantCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    company_name: str = Field(min_length=2, max_length=160)
    tenant_slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    contact_email: EmailStr

    @field_validator("tenant_slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return value.lower() if isinstance(value, str) else value

class TenantStatusUpdate(BaseModel):
    status: TenantStatus
