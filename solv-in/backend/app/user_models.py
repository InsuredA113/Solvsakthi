from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

Role = Literal[
    "Super Administrator", "Insurance Company Administrator", "Claims Manager",
    "Property Adjuster", "Casualty Adjuster", "Claims Approver", "Insurance Surveyor",
    "Actuarial Analyst", "Auditor", "Underwriting Specialist", "Customer / Policyholder",
    "Compliance Officer", "Finance Officer", "Reinsurance Analyst",
    # Legacy labels accepted for existing clients during migration.
    "Approver", "Surveyor", "Customer"
]

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    # Keep role for older Angular builds; roles enables multi-role accounts.
    role: Role | None = None
    roles: list[Role] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def require_at_least_one_role(self):
        if not self.roles and not self.role:
            raise ValueError("At least one role must be selected.")
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("Duplicate roles are not allowed.")
        return self
