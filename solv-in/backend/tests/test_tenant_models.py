import pytest
from pydantic import ValidationError

from app.tenant_models import TenantCreate, TenantStatusUpdate
from app.user_models import UserCreate


def test_tenant_slug_is_normalized_to_lowercase():
    tenant = TenantCreate(
        company_name="Example Insurance",
        tenant_slug="Example-Insurance",
        contact_email="admin@example.com",
    )
    assert tenant.tenant_slug == "example-insurance"


def test_tenant_slug_rejects_spaces_and_underscores():
    with pytest.raises(ValidationError):
        TenantCreate(
            company_name="Example Insurance",
            tenant_slug="example_insurance",
            contact_email="admin@example.com",
        )


def test_tenant_status_accepts_only_supported_values():
    assert TenantStatusUpdate(status="Suspended").status == "Suspended"
    with pytest.raises(ValidationError):
        TenantStatusUpdate(status="Deleted")


def test_user_model_accepts_optional_tenant_id():
    user = UserCreate(
        full_name="Example User",
        email="user@example.com",
        password="SecurePassword123",
        role="Claims Manager",
        tenant_id="ten_12345678",
    )
    assert user.tenant_id == "ten_12345678"


def test_global_super_admin_can_be_created_without_tenant_id():
    user = UserCreate(
        full_name="Global Admin",
        email="global@example.com",
        password="SecurePassword123",
        role="Super Administrator",
    )
    assert user.tenant_id is None
