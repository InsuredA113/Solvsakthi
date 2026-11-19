import mongomock
import pytest
from fastapi.testclient import TestClient

from app import main
from app.auth import get_current_user


@pytest.fixture
def tenant_api(monkeypatch):
    db = mongomock.MongoClient().get_database("solvin_test")
    db["organisations"].insert_many([
        {"organisation_id": "org-a", "status": "active"},
        {"organisation_id": "org-b", "status": "active"},
    ])
    db["claims"].insert_many([
        {
            "claim_id": "CLM-A-000001", "organisation_id": "org-a",
            "claim_type": "Property", "customer_name": "A Policyholder",
            "email": "customer-a@example.test", "status": "FNOL Received",
        },
        {
            "claim_id": "CLM-A-000002", "organisation_id": "org-a",
            "claim_type": "Property", "customer_name": "Unverified Contact",
            "email": "customer-a@example.test", "status": "FNOL Received",
        },
        {
            "claim_id": "CLM-B-000001", "organisation_id": "org-b",
            "claim_type": "Property", "customer_name": "B Policyholder",
            "email": "customer-a@example.test", "status": "FNOL Received",
        },
    ])
    db["users"].insert_many([
        {
            "full_name": "Customer A", "email": "customer-a@example.test",
            "role": "Customer / Policyholder", "roles": ["Customer / Policyholder"],
            "organisation_id": "org-a", "is_active": True,
        },
        {
            "full_name": "Surveyor A", "email": "surveyor-a@example.test",
            "role": "Insurance Surveyor", "roles": ["Insurance Surveyor"],
            "organisation_id": "org-a", "is_active": True,
        },
        {
            "full_name": "Surveyor B", "email": "surveyor-b@example.test",
            "role": "Insurance Surveyor", "roles": ["Insurance Surveyor"],
            "organisation_id": "org-b", "is_active": True,
        },
    ])
    monkeypatch.setattr(main, "get_database", lambda: db)
    monkeypatch.setattr(main, "database_is_available", lambda: True)
    client = TestClient(main.app)

    def sign_in_as(user):
        main.app.dependency_overrides[get_current_user] = lambda: user

    yield client, db, sign_in_as
    main.app.dependency_overrides.clear()


def manager(email="manager-a@example.test", organisation_id="org-a"):
    return {
        "email": email, "full_name": "Claims Manager",
        "role": "Claims Manager", "roles": ["Claims Manager"],
        "organisation_id": organisation_id, "is_active": True,
    }


def test_claim_id_cannot_read_another_organisation_claim(tenant_api):
    client, _, sign_in_as = tenant_api
    sign_in_as(manager())

    own_claim = client.get("/api/claims/CLM-A-000001")
    other_claim = client.get("/api/claims/CLM-B-000001")

    assert own_claim.status_code == 200
    assert own_claim.json()["organisation_id"] == "org-a"
    assert other_claim.status_code == 404


def test_surveyor_assignment_requires_same_organisation_and_records_history(tenant_api):
    client, db, sign_in_as = tenant_api
    sign_in_as(manager())

    assigned = client.patch(
        "/api/claims/CLM-A-000001/assign-surveyor",
        json={"surveyor_email": "surveyor-a@example.test"},
    )
    cross_tenant = client.patch(
        "/api/claims/CLM-A-000001/assign-surveyor",
        json={"surveyor_email": "surveyor-b@example.test"},
    )

    assert assigned.status_code == 200
    assert assigned.json()["assigned_surveyor_email"] == "surveyor-a@example.test"
    assert len(assigned.json()["surveyor_assignment_history"]) == 1
    assert cross_tenant.status_code == 422
    assert db["claims"].find_one({"claim_id": "CLM-A-000001"})["assigned_surveyor_email"] == "surveyor-a@example.test"


def test_customer_sees_only_staff_verified_claims_in_own_organisation(tenant_api):
    client, db, sign_in_as = tenant_api
    sign_in_as(manager())

    linked = client.post(
        "/api/claims/CLM-A-000001/customer-link",
        json={
            "customer_email": "customer-a@example.test",
            "verification_method": "Policyholder document review",
            "verification_reference": "VERIFY-CASE-1001",
            "identity_verified": True,
        },
    )
    assert linked.status_code == 200
    assert linked.json()["customer_identity_verified"] is True

    sign_in_as({
        "email": "customer-a@example.test", "full_name": "Customer A",
        "role": "Customer / Policyholder", "roles": ["Customer / Policyholder"],
        "organisation_id": "org-a", "is_active": True,
    })
    result = client.get("/api/customer/claims")

    assert result.status_code == 200
    returned_ids = {claim["claim_id"] for claim in result.json()["items"]}
    assert returned_ids == {"CLM-A-000001"}
    assert "customer_identity_verification_reference" not in result.json()["items"][0]
    own_detail = client.get("/api/customer/claims/CLM-A-000001")
    assert own_detail.status_code == 200
    assert "customer_identity_verification_reference" not in own_detail.json()
    assert client.get("/api/customer/claims/CLM-B-000001").status_code == 404
    assert db["claims"].find_one({"claim_id": "CLM-A-000001"})["customer_identity_verification_reference"] == "VERIFY-CASE-1001"
