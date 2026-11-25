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


class FakeClaims:
    def __init__(self):
        self.queries = []

    def count_documents(self, query):
        self.queries.append(query)
        return 0


def test_dashboard_summary_counts_are_tenant_scoped_without_duplicate_filter(monkeypatch):
    claims = FakeClaims()
    db = FakeDatabase(
        tenants=FakeTenants([{"tenant_id": "ten_alpha", "status": "Active"}]),
        claims=claims,
    )
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)

    result = main.dashboard_summary({
        "role": "Claims Manager", "tenant_id": "ten_alpha",
    })

    assert result["total_claims"] == 0
    assert len(claims.queries) == 5
    for query in claims.queries:
        tenant_filters = [
            clause for clause in query["$and"]
            if clause == {"tenant_id": "ten_alpha"}
        ]
        assert len(tenant_filters) == 1

def test_insurer_cannot_override_tenant_with_header(monkeypatch):
    install_tenants(monkeypatch, [
        {"tenant_id": "ten_alpha", "status": "Active"},
        {"tenant_id": "ten_beta", "status": "Active"},
    ])
    with pytest.raises(HTTPException) as error:
        auth.get_current_tenant(
            {"role": "Claims Manager", "tenant_id": "ten_alpha"},
            tenant_header="ten_beta",
        )
    assert error.value.status_code == 403


def test_super_admin_may_select_only_an_active_tenant(monkeypatch):
    install_tenants(monkeypatch, [
        {"tenant_id": "ten_alpha", "status": "Active"},
        {"tenant_id": "ten_beta", "status": "Suspended"},
    ])
    selected = auth.get_current_tenant(
        {"role": "Super Administrator"},
        tenant_header="ten_alpha",
    )
    assert selected["tenant_id"] == "ten_alpha"
    with pytest.raises(HTTPException) as error:
        auth.get_current_tenant(
            {"role": "Super Administrator"},
            tenant_header="ten_beta",
        )
    assert error.value.status_code == 403


class FakeClaimCollection:
    def __init__(self, records):
        self.records = records
        self.queries = []

    def find_one(self, query, projection=None):
        self.queries.append(query)
        return next(
            (record for record in self.records
             if all(record.get(key) == value for key, value in query.items())),
            None,
        )


def test_claim_detail_endpoint_returns_not_found_for_other_tenant(monkeypatch):
    claims = FakeClaimCollection([{
        "claim_id": "CLM-2026-000099", "tenant_id": "ten_beta",
        "claim_type": "Property",
    }])
    db = FakeDatabase(
        tenants=FakeTenants([{"tenant_id": "ten_alpha", "status": "Active"}]),
        claims=claims,
    )
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)

    with pytest.raises(HTTPException) as error:
        main.get_claim_details("CLM-2026-000099", {
            "role": "Claims Manager", "tenant_id": "ten_alpha",
        })

    assert error.value.status_code == 404
    assert claims.queries == [{
        "claim_id": "CLM-2026-000099", "tenant_id": "ten_alpha",
    }]


def test_claim_export_rejects_other_tenant_before_loading_related_data(monkeypatch):
    from app import claim_export_routes

    claims = FakeClaimCollection([{
        "claim_id": "CLM-2026-000099", "tenant_id": "ten_beta",
        "claim_type": "Property",
    }])
    db = FakeDatabase(
        tenants=FakeTenants([{"tenant_id": "ten_alpha", "status": "Active"}]),
        claims=claims,
    )
    monkeypatch.setattr(claim_export_routes, "get_database", lambda: db)
    monkeypatch.setattr(claim_export_routes, "database_is_available", lambda: True)

    with pytest.raises(HTTPException) as error:
        claim_export_routes._load_claim_export("CLM-2026-000099", {
            "role": "Claims Manager", "tenant_id": "ten_alpha",
        })

    assert error.value.status_code == 404
    assert claims.queries == [{
        "claim_id": "CLM-2026-000099", "tenant_id": "ten_alpha",
    }]


def test_automated_intake_update_filter_requires_matching_tenant():
    query = email_connector_routes._intake_update_filter("intake-123", "ten_alpha")
    assert query == {"intake_id": "intake-123", "tenant_id": "ten_alpha"}
    assert query != {"intake_id": "intake-123", "tenant_id": "ten_beta"}


def test_super_admin_manual_claim_requires_active_tenant(monkeypatch):
    install_tenants(monkeypatch, [])
    with pytest.raises(HTTPException) as error:
        main._resolve_claim_tenant({"role": "Super Administrator"}, None)
    assert error.value.status_code == 422


def test_super_admin_manual_claim_resolves_selected_active_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    assert main._resolve_claim_tenant({"role": "Super Administrator"}, "ten_alpha") == "ten_alpha"


def test_super_admin_manual_claim_rejects_inactive_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Suspended"}])
    with pytest.raises(HTTPException) as error:
        main._resolve_claim_tenant({"role": "Super Administrator"}, "ten_alpha")
    assert error.value.status_code == 422


def test_insurer_cannot_request_claim_for_other_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    with pytest.raises(HTTPException) as error:
        main._resolve_claim_tenant(
            {"role": "Claims Manager", "tenant_id": "ten_alpha"}, "ten_beta"
        )
    assert error.value.status_code == 403
