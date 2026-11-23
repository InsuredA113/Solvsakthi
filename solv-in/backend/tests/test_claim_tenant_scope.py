import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-at-least-32-characters-long")

import pytest
from fastapi import HTTPException

from app import main


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
