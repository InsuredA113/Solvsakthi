import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-at-least-32-characters-long")

import pytest
from fastapi import HTTPException

from app import adjuster_report_routes, comparison_comment_routes


class FakeTenants:
    def find_one(self, query, projection=None):
        if query == {"tenant_id": "ten_alpha", "status": "Active"}:
            return {"tenant_id": "ten_alpha"}
        return None


class FakeClaims:
    def __init__(self, record):
        self.record = record
        self.queries = []

    def find_one(self, query, projection=None):
        self.queries.append(query)
        if all(self.record.get(key) == value for key, value in query.items()):
            return self.record
        return None


class ForbiddenCollection:
    def find(self, *args, **kwargs):
        raise AssertionError("Related records must not be queried before tenant claim authorization.")


class FakeDatabase(dict):
    pass


def make_database(claim):
    claims = FakeClaims(claim)
    db = FakeDatabase(
        tenants=FakeTenants(),
        claims=claims,
        adjuster_reports=ForbiddenCollection(),
        comparison_comments=ForbiddenCollection(),
    )
    return db, claims


def test_adjuster_report_endpoint_rejects_cross_tenant_claim_before_child_query(monkeypatch):
    db, claims = make_database({
        "claim_id": "CLM-2026-000099",
        "tenant_id": "ten_beta",
        "claim_type": "Property",
    })
    monkeypatch.setattr(adjuster_report_routes, "get_database", lambda: db)
    monkeypatch.setattr(adjuster_report_routes, "database_is_available", lambda: True)

    with pytest.raises(HTTPException) as error:
        adjuster_report_routes.list_adjuster_reports(
            "CLM-2026-000099",
            {"role": "Claims Manager", "tenant_id": "ten_alpha"},
        )

    assert error.value.status_code == 404
    assert claims.queries == [{
        "claim_id": "CLM-2026-000099",
        "tenant_id": "ten_alpha",
    }]


def test_comparison_comment_endpoint_rejects_cross_tenant_claim_before_child_query(monkeypatch):
    db, claims = make_database({
        "claim_id": "CLM-2026-000099",
        "tenant_id": "ten_beta",
        "claim_type": "Property",
    })
    monkeypatch.setattr(comparison_comment_routes, "get_database", lambda: db)
    monkeypatch.setattr(comparison_comment_routes, "database_is_available", lambda: True)

    with pytest.raises(HTTPException) as error:
        comparison_comment_routes.list_comparison_comments(
            "CLM-2026-000099",
            {"role": "Claims Manager", "tenant_id": "ten_alpha"},
        )

    assert error.value.status_code == 404
    assert claims.queries == [{
        "claim_id": "CLM-2026-000099",
        "tenant_id": "ten_alpha",
    }]
