from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal[
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Surveyor", "Actuarial Analyst",
    "Approver", "Auditor", "Customer"
]

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    role: Role
    tenant_id: str | None = Field(default=None, min_length=8, max_length=80)


class UserTenantAccessUpdate(BaseModel):
    tenant_access_enabled: bool
