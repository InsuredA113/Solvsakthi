import os

os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-at-least-32-characters-long")

import pytest
from fastapi import HTTPException

from app import main


class FakeUsers:
    def __init__(self, user):
        self.user = user
        self.update_query = None
        self.update_values = None

    def find_one(self, query, projection=None):
        return self.user if self.user and self.user.get("email") == query.get("email") else None

    def find_one_and_update(self, query, update, **kwargs):
        self.update_query = query
        self.update_values = update["$set"]
        if not self.user or self.user.get("email") != query.get("email"):
            return None
        if self.user.get("role") == "Super Administrator" or self.user.get("tenant_id") != query.get("tenant_id"):
            return None
        self.user.update(update["$set"])
        return self.user


class FakeTenants:
    def __init__(self, active=True):
        self.active = active
        self.last_query = None

    def find_one(self, query, projection=None):
        self.last_query = query
        if self.active and query == {"tenant_id": "ten_alpha", "status": "Active"}:
            return {"_id": "tenant"}
        return None


class FakeDatabase(dict):
    pass


def setup_access_test(monkeypatch, user, tenant_active=True):
    users = FakeUsers(user)
    tenants = FakeTenants(tenant_active)
    db = FakeDatabase(users=users, tenants=tenants)
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)
    return users, tenants


def test_super_admin_can_enable_assigned_user_on_active_tenant(monkeypatch):
    users, tenants = setup_access_test(monkeypatch, {
        "email": "adjuster@example.com",
        "role": "Claims Manager",
        "tenant_id": "ten_alpha",
        "tenant_access_enabled": False,
    })
    result = main.update_user_tenant_access(
        "ADJUSTER@example.com",
        main.UserTenantAccessUpdate(tenant_access_enabled=True),
        {"email": "super@example.com", "role": "Super Administrator"},
    )
    assert result["tenant_access_enabled"] is True
    assert result["tenant_access_updated_by"] == "super@example.com"
    assert users.update_query["tenant_id"] == "ten_alpha"
    assert tenants.last_query["status"] == "Active"


def test_cannot_enable_user_when_tenant_is_inactive(monkeypatch):
    setup_access_test(monkeypatch, {
        "email": "adjuster@example.com",
        "role": "Claims Manager",
        "tenant_id": "ten_alpha",
        "tenant_access_enabled": False,
    }, tenant_active=False)
    with pytest.raises(HTTPException) as error:
        main.update_user_tenant_access(
            "adjuster@example.com",
            main.UserTenantAccessUpdate(tenant_access_enabled=True),
            {"email": "super@example.com", "role": "Super Administrator"},
        )
    assert error.value.status_code == 422


def test_cannot_manage_global_super_admin_as_tenant_user(monkeypatch):
    setup_access_test(monkeypatch, {
        "email": "global@example.com",
        "role": "Super Administrator",
        "tenant_id": None,
    })
    with pytest.raises(HTTPException) as error:
        main.update_user_tenant_access(
            "global@example.com",
            main.UserTenantAccessUpdate(tenant_access_enabled=True),
            {"email": "super@example.com", "role": "Super Administrator"},
        )
    assert error.value.status_code == 422


def test_only_super_admin_can_change_tenant_access(monkeypatch):
    setup_access_test(monkeypatch, {
        "email": "adjuster@example.com",
        "role": "Claims Manager",
        "tenant_id": "ten_alpha",
        "tenant_access_enabled": False,
    })
    with pytest.raises(HTTPException) as error:
        main.update_user_tenant_access(
            "adjuster@example.com",
            main.UserTenantAccessUpdate(tenant_access_enabled=True),
            {"email": "manager@example.com", "role": "Claims Manager"},
        )
    assert error.value.status_code == 403
