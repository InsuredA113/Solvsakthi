import mongomock
from bson import ObjectId
import pytest
from fastapi.testclient import TestClient

from app import main, adjuster_report_routes, comparison_comment_routes, claim_export_routes, email_connector_routes
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
    for module in (
        main, adjuster_report_routes, comparison_comment_routes,
        claim_export_routes, email_connector_routes,
    ):
        monkeypatch.setattr(module, "get_database", lambda: db)
        monkeypatch.setattr(module, "database_is_available", lambda: True)
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

def test_adjuster_can_read_and_review_only_assigned_claim_damage_assessments(tenant_api):
    client, db, sign_in_as = tenant_api
    adjuster_email = "adjuster-a@example.test"
    db["claims"].update_one(
        {"claim_id": "CLM-A-000001"},
        {"$set": {"assigned_adjuster_email": adjuster_email}},
    )
    assessment_id = ObjectId()
    db["damage_assessments"].insert_one({
        "_id": assessment_id,
        "claim_id": "CLM-A-000001",
        "organisation_id": "org-a",
        "status": "Pending Adjuster Review",
        "severity": "Moderate",
        "created_at": "2026-11-19T10:00:00+00:00",
    })
    sign_in_as({
        "email": adjuster_email, "full_name": "Property Adjuster",
        "role": "Property Adjuster", "roles": ["Property Adjuster"],
        "organisation_id": "org-a", "is_active": True,
    })

    listed = client.get("/api/claims/CLM-A-000001/damage-assessments")
    reviewed = client.patch(
        f"/api/damage-assessments/{assessment_id}/review",
        json={"status": "Reviewed", "notes": "Visual observations checked."},
    )

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "Reviewed"


def test_comparison_comment_create_and_read_are_claim_and_tenant_scoped(tenant_api):
    client, db, sign_in_as = tenant_api
    sign_in_as(manager())

    created = client.post(
        "/api/claims/CLM-A-000001/comparison-comments",
        json={
            "comment": "Please verify the estimate depreciation basis.",
            "field_key": "estimated_loss",
            "field_label": "Estimated Loss",
        },
    )

    assert created.status_code == 201
    assert created.json()["organisation_id"] == "org-a"
    db["comparison_comments"].insert_one({
        "claim_id": "CLM-A-000001",
        "organisation_id": "org-b",
        "comment": "This record belongs to another organisation.",
        "created_at": "2026-11-19T11:00:00+00:00",
    })
    listed = client.get("/api/claims/CLM-A-000001/comparison-comments")

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["comment"] == "Please verify the estimate depreciation basis."

