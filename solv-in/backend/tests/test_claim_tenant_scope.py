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
    # Five status/summary metrics plus the unclassified legacy casualty count.
    assert len(claims.queries) == 6
    for query in claims.queries:
        # Tenant scope may be merged into the query directly or nested in $and.
        tenant_filters = int(query.get("tenant_id") == "ten_alpha") + sum(
            clause == {"tenant_id": "ten_alpha"}
            for clause in query.get("$and", [])
        )
        assert tenant_filters == 1

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


class FakeUsers:
    def __init__(self):
        self.queries = []

    def find_one(self, query, projection=None):
        self.queries.append(query)
        return None


def test_super_admin_adjuster_lookup_is_constrained_to_claim_tenant(monkeypatch):
    claims = FakeClaimCollection([{
        "claim_id": "CLM-2026-000101",
        "tenant_id": "ten_alpha",
        "claim_type": "Property",
    }])
    users = FakeUsers()
    db = FakeDatabase(
        tenants=FakeTenants([{"tenant_id": "ten_alpha", "status": "Active"}]),
        claims=claims,
        users=users,
    )
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)

    with pytest.raises(HTTPException) as error:
        main.assign_claim_adjuster(
            "CLM-2026-000101",
            main.AdjusterAssignment(adjuster_email="adjuster@beta.example"),
            {"role": "Super Administrator", "email": "admin@solvin.example"},
        )

    assert error.value.status_code == 422
    assert users.queries == [{
        "email": "adjuster@beta.example",
        "is_active": True,
        "tenant_access_enabled": True,
        "tenant_id": "ten_alpha",
    }]


@pytest.mark.parametrize("status", ["Approved", "Closed"])
def test_adjuster_cannot_approve_or_close_claim(status):
    with pytest.raises(HTTPException) as error:
        main._enforce_claim_status_permission({"role": "Property Adjuster"}, status)
    assert error.value.status_code == 403


@pytest.mark.parametrize("role", [
    "Super Administrator", "Insurance Company Administrator", "Claims Manager", "Approver"
])
@pytest.mark.parametrize("status", ["Approved", "Closed"])
def test_authorised_claim_approver_can_set_final_status(role, status):
    main._enforce_claim_status_permission({"role": role}, status)


def test_adjuster_can_set_nonfinal_claim_status():
    main._enforce_claim_status_permission({"role": "Property Adjuster"}, "Assessment")


def test_financial_estimate_analytics_filter_is_tenant_scoped():
    query = main._financial_estimate_match(
        ["CLM-2026-000001", "CLM-2026-000002"], "ten_alpha"
    )
    assert query == {
        "claim_id": {"$in": ["CLM-2026-000001", "CLM-2026-000002"]},
        "tenant_id": "ten_alpha",
    }


def test_super_admin_financial_estimate_analytics_remains_global():
    query = main._financial_estimate_match(["CLM-2026-000001"], None)
    assert query == {"claim_id": {"$in": ["CLM-2026-000001"]}}


class DeleteCaptureCollection:
    def __init__(self, parent=None):
        self.parent = parent
        self.delete_queries = []
        self.deleted = []

    def find_one(self, query, projection=None):
        if self.parent and query.get("claim_id") == self.parent["claim_id"] and query.get("tenant_id") == self.parent["tenant_id"]:
            return self.parent
        return None

    def delete_many(self, query):
        self.delete_queries.append(query)

    def delete_one(self, query):
        self.deleted.append(query)


class DeleteCaptureBucket:
    def __init__(self):
        self.find_query = None
        self.deleted = []

    def find(self, query):
        self.find_query = query
        return []

    def delete(self, file_id):
        self.deleted.append(file_id)


def test_claim_delete_scopes_every_child_record_to_parent_tenant(monkeypatch):
    claim = {"claim_id": "CLM-2026-000099", "tenant_id": "ten_alpha"}
    claims = DeleteCaptureCollection(parent=claim)
    collections = {
        name: DeleteCaptureCollection()
        for name in (
            "financial_estimates", "damage_assessments", "coverage_assessments",
            "comparison_comments", "adjuster_reports", "claim_export_audit", "email_intake",
        )
    }
    tenants = FakeTenants([{"tenant_id": "ten_alpha", "status": "Active"}])
    db = FakeDatabase(tenants=tenants, claims=claims, **collections)
    bucket = DeleteCaptureBucket()
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)
    monkeypatch.setattr(main, "GridFSBucket", lambda *args, **kwargs: bucket)

    result = main.delete_claim(
        "CLM-2026-000099",
        {"role": "Claims Manager", "tenant_id": "ten_alpha"},
    )

    assert result == {"deleted": True, "claim_id": "CLM-2026-000099"}
    assert bucket.find_query == {
        "metadata.claim_id": "CLM-2026-000099",
        "metadata.tenant_id": "ten_alpha",
    }
    for collection in collections.values():
        assert collection.delete_queries == [{
            "claim_id": "CLM-2026-000099",
            "tenant_id": "ten_alpha",
        }]
    assert claims.deleted == [{
        "claim_id": "CLM-2026-000099",
        "tenant_id": "ten_alpha",
    }]


def test_claim_delete_refuses_tenantless_legacy_claim(monkeypatch):
    claim = {"claim_id": "CLM-2026-000099", "tenant_id": None}
    claims = DeleteCaptureCollection(parent=claim)
    db = FakeDatabase(
        tenants=FakeTenants([]),
        claims=claims,
    )
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)

    with pytest.raises(HTTPException) as error:
        main.delete_claim(
            "CLM-2026-000099",
            {"role": "Super Administrator"},
        )

    assert error.value.status_code == 409
    assert claims.deleted == []


def test_super_admin_manual_intake_requires_active_selected_tenant(monkeypatch):
    install_tenants(monkeypatch, [{"tenant_id": "ten_alpha", "status": "Active"}])
    with pytest.raises(HTTPException) as error:
        main._resolve_claim_tenant({"role": "Super Administrator"}, None)
    assert error.value.status_code == 422
    assert main._resolve_claim_tenant(
        {"role": "Super Administrator"}, "ten_alpha"
    ) == "ten_alpha"


def test_insurer_manual_intake_cannot_choose_another_tenant(monkeypatch):
    install_tenants(monkeypatch, [
        {"tenant_id": "ten_alpha", "status": "Active"},
        {"tenant_id": "ten_beta", "status": "Active"},
    ])
    user = {"role": "Claims Manager", "tenant_id": "ten_alpha"}
    assert main._resolve_claim_tenant(user, None) == "ten_alpha"
    with pytest.raises(HTTPException) as error:
        main._resolve_claim_tenant(user, "ten_beta")
    assert error.value.status_code == 403


def test_manual_email_intake_model_accepts_tenant_assignment():
    from app.email_intake import EmailIntakeCreate

    payload = EmailIntakeCreate(
        tenant_id="ten_alpha", provider="manual",
        provider_message_id="sample-1", sender_email="notice@example.com",
        subject="Sample FNOL", body_text="Loss details",
    )
    assert payload.tenant_id == "ten_alpha"
