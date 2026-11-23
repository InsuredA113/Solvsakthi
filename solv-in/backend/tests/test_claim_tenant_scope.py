import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-at-least-32-characters-long")

import pytest
from fastapi import HTTPException

from app import main, auth, email_connector_routes


class FakeTenants:
    def __init__(self, rows):
        self.rows = rows

    def find_one(self, query, projection=None):
        return next(
            (row for row in self.rows if all(row.get(key) == value for key, value in query.items())),
            None,
        )


class FakeDatabase(dict):
    pass


def install_tenants(monkeypatch, rows):
    db = FakeDatabase(tenants=FakeTenants(rows))
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(auth, "get_database", lambda: db)


def test_claim_filter_limits_insurer_user_to_database_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    query = main._claim_filter(
        {"role": "Claims Manager", "tenant_id": "ten_alpha"},
        "CLM-2026-000001",
    )
    assert query == {"claim_id": "CLM-2026-000001", "tenant_id": "ten_alpha"}


def test_claim_filter_does_not_allow_tenantless_insurer_user(monkeypatch):
    install_tenants(monkeypatch, [])
    with pytest.raises(HTTPException) as error:
        main._claim_filter({"role": "Claims Manager"}, "CLM-2026-000001")
    assert error.value.status_code == 403


def test_claim_filter_rejects_inactive_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Suspended"}])
    with pytest.raises(HTTPException) as error:
        main._claim_filter({"role": "Claims Manager", "tenant_id": "ten_alpha"})
    assert error.value.status_code == 403


def test_global_super_admin_is_not_limited_to_one_tenant(monkeypatch):
    install_tenants(monkeypatch, [])
    query = main._claim_filter(
        {"role": "Super Administrator"},
        "CLM-2026-000001",
    )
    assert query == {"claim_id": "CLM-2026-000001"}


def test_scoped_query_preserves_filters_and_adds_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_beta", "status": "Active"}])
    query = main._scoped_query(
        {"role": "Claims Manager", "tenant_id": "ten_beta"},
        {"status": "Closed"},
    )
    assert query == {"status": "Closed", "tenant_id": "ten_beta"}

def test_claim_filter_for_second_tenant_cannot_match_first_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_bravo", "status": "Active"}])
    query = main._claim_filter(
        {"role": "Claims Manager", "tenant_id": "ten_bravo"},
        "CLM-2026-000001",
    )
    assert query["tenant_id"] == "ten_bravo"
    assert query["tenant_id"] != "ten_alpha"

def test_insurer_access_is_denied_until_explicitly_enabled(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    with pytest.raises(HTTPException) as error:
        auth.validate_tenant_user_access({
            "role": "Claims Manager", "tenant_id": "ten_alpha",
            "tenant_access_enabled": False,
        })
    assert error.value.status_code == 403


def test_insurer_access_requires_active_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Suspended"}])
    with pytest.raises(HTTPException) as error:
        auth.validate_tenant_user_access({
            "role": "Claims Manager", "tenant_id": "ten_alpha",
            "tenant_access_enabled": True,
        })
    assert error.value.status_code == 403


def test_enabled_insurer_user_with_active_tenant_passes_gate(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    user = {
        "role": "Claims Manager", "tenant_id": "ten_alpha",
        "tenant_access_enabled": True,
    }
    assert auth.validate_tenant_user_access(user) is user


def test_global_super_admin_does_not_require_tenant(monkeypatch):
    install_tenants(monkeypatch, [])
    user = {"role": "Super Administrator"}
    assert auth.validate_tenant_user_access(user) is user

def test_intake_filter_limits_insurer_to_own_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    query = main._intake_filter(
        {"role": "Claims Manager", "tenant_id": "ten_alpha"},
        "intake-123",
    )
    assert query == {"intake_id": "intake-123", "tenant_id": "ten_alpha"}


def test_super_admin_intake_filter_remains_global(monkeypatch):
    install_tenants(monkeypatch, [])
    query = main._intake_filter({"role": "Super Administrator"}, "intake-123")
    assert query == {"intake_id": "intake-123"}

def test_automated_email_intake_requires_configured_active_tenant(monkeypatch):
    db = FakeDatabase(tenants=FakeTenants([{"tenant_id": "ten_alpha", "status": "Active", "company_name": "Alpha"}]))
    monkeypatch.setattr(email_connector_routes, "setting", lambda name: "ten_alpha")
    tenant = email_connector_routes._configured_ingest_tenant(db)
    assert tenant["tenant_id"] == "ten_alpha"


def test_automated_email_intake_fails_closed_without_tenant_mapping(monkeypatch):
    from fastapi import HTTPException
    db = FakeDatabase(tenants=FakeTenants([]))
    monkeypatch.setattr(email_connector_routes, "setting", lambda name: "")
    with pytest.raises(HTTPException) as error:
        email_connector_routes._configured_ingest_tenant(db)
    assert error.value.status_code == 503


def test_automated_email_intake_rejects_inactive_tenant(monkeypatch):
    from fastapi import HTTPException
    db = FakeDatabase(tenants=FakeTenants([{"tenant_id": "ten_alpha", "status": "Suspended"}]))
    monkeypatch.setattr(email_connector_routes, "setting", lambda name: "ten_alpha")
    with pytest.raises(HTTPException) as error:
        email_connector_routes._configured_ingest_tenant(db)
    assert error.value.status_code == 503
