import mongomock
import io
from datetime import datetime, timezone
from bson import ObjectId
from gridfs.errors import NoFile
import pytest
from fastapi.testclient import TestClient

from app import main, adjuster_report_routes, comparison_comment_routes, claim_export_routes, email_connector_routes
from app.auth import get_current_user
from app import bootstrap



class _FakeGridOut:
    def __init__(self, row):
        self._id = row.get("_id")
        self.uploadDate = row.get("uploadDate")
        self.metadata = row.get("metadata") or {}
        self.filename = row.get("filename") or "evidence"
        self.length = row.get("length", 0)
        self._content = row.get("test_content", b"")

    def read(self):
        return self._content


class _FakeGridCursor(list):
    def sort(self, key, direction=1):
        super().sort(key=lambda row: getattr(row, key), reverse=(direction == -1))
        return self

    def limit(self, count):
        return _FakeGridCursor(self[:count])


class FakeGridFSBucket:
    """Small GridFSBucket test double; production continues to use MongoDB GridFS."""
    def __init__(self, db, bucket_name="fs"):
        self.files = db[f"{bucket_name}.files"]

    def upload_from_stream(self, filename, source, metadata=None):
        file_id = ObjectId()
        content = source.read() if hasattr(source, "read") else source
        self.files.insert_one({
            "_id": file_id, "filename": filename, "length": len(content),
            "metadata": metadata or {}, "uploadDate": datetime.now(timezone.utc),
            "test_content": content,
        })
        return file_id

    def find(self, query=None):
        return _FakeGridCursor([_FakeGridOut(row) for row in self.files.find(query or {})])

    def open_download_stream(self, file_id):
        row = self.files.find_one({"_id": file_id})
        if not row:
            raise NoFile(f"No file found with id {file_id}")
        return _FakeGridOut(row)

    def delete(self, file_id):
        result = self.files.delete_one({"_id": file_id})
        if result.deleted_count == 0:
            raise NoFile(f"No file found with id {file_id}")


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
    for module in (main, claim_export_routes, email_connector_routes):
        monkeypatch.setattr(module, "GridFSBucket", FakeGridFSBucket)
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




def company_admin(email="admin-a@example.test", organisation_id="org-a"):
    return {
        "email": email, "full_name": "Insurance Company Administrator",
        "role": "Insurance Company Administrator",
        "roles": ["Insurance Company Administrator"],
        "organisation_id": organisation_id, "is_active": True,
    }


def test_evidence_list_download_and_delete_validate_claim_and_organisation(tenant_api):
    client, db, sign_in_as = tenant_api
    bucket = FakeGridFSBucket(db, bucket_name="claim_documents")
    own_id = bucket.upload_from_stream(
        "own.pdf", b"%PDF-1.4 own evidence",
        metadata={"claim_id": "CLM-A-000001", "organisation_id": "org-a", "content_type": "application/pdf"},
    )
    foreign_id = bucket.upload_from_stream(
        "foreign.pdf", b"%PDF-1.4 foreign evidence",
        metadata={"claim_id": "CLM-A-000001", "organisation_id": "org-b", "content_type": "application/pdf"},
    )

    sign_in_as(manager())
    listed = client.get("/api/claims/CLM-A-000001/documents")
    own_download = client.get(f"/api/claims/CLM-A-000001/documents/{own_id}/download")
    foreign_download = client.get(f"/api/claims/CLM-A-000001/documents/{foreign_id}/download")

    assert listed.status_code == 200
    assert [item["file_id"] for item in listed.json()["items"]] == [str(own_id)]
    assert own_download.status_code == 200
    assert own_download.content == b"%PDF-1.4 own evidence"
    assert foreign_download.status_code == 404

    sign_in_as(company_admin())
    foreign_delete = client.delete(f"/api/claims/CLM-A-000001/documents/{foreign_id}")
    own_delete = client.delete(f"/api/claims/CLM-A-000001/documents/{own_id}")

    assert foreign_delete.status_code == 404
    assert own_delete.status_code == 200
    assert db["claim_documents.files"].count_documents({"_id": own_id}) == 0
    assert db["claim_documents.files"].count_documents({"_id": foreign_id}) == 1


def test_claim_export_excludes_child_records_from_another_organisation(tenant_api):
    client, db, sign_in_as = tenant_api
    db["damage_assessments"].insert_many([
        {"claim_id": "CLM-A-000001", "organisation_id": "org-a", "summary": "OWN-DAMAGE-RECORD"},
        {"claim_id": "CLM-A-000001", "organisation_id": "org-b", "summary": "FOREIGN-DAMAGE-RECORD"},
    ])
    sign_in_as(manager())
    response = client.get("/api/claims/CLM-A-000001/export?format=csv")

    assert response.status_code == 200
    assert "OWN-DAMAGE-RECORD" in response.text
    assert "FOREIGN-DAMAGE-RECORD" not in response.text


def test_dashboard_summary_is_limited_to_the_callers_organisation(tenant_api):
    client, _, sign_in_as = tenant_api
    sign_in_as(manager())
    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["total_claims"] == 2


def test_company_admin_cannot_change_roles_for_another_organisation_user(tenant_api):
    client, db, sign_in_as = tenant_api
    db["users"].insert_many([
        {"email": "target-a@example.test", "full_name": "Target A", "role": "Claims Manager", "roles": ["Claims Manager"], "organisation_id": "org-a", "is_active": True},
        {"email": "target-b@example.test", "full_name": "Target B", "role": "Claims Manager", "roles": ["Claims Manager"], "organisation_id": "org-b", "is_active": True},
    ])
    sign_in_as(company_admin())
    response = client.patch(
        "/api/users/target-b@example.test/roles",
        json={"roles": ["Claims Manager"], "custom_roles": []},
    )

    assert response.status_code == 404
    assert db["users"].find_one({"email": "target-b@example.test"})["roles"] == ["Claims Manager"]



def test_email_connector_rejects_provider_message_owned_by_another_organisation(tenant_api, monkeypatch):
    _, db, _ = tenant_api
    db["email_intake"].insert_one({
        "intake_id": "intake-org-b",
        "provider": "gmail",
        "provider_message_id": "provider-message-unique",
        "organisation_id": "org-b",
    })
    monkeypatch.setattr(
        email_connector_routes,
        "extract_email_intake",
        lambda subject, body: {
            "suggested_claim_type": "Out of Scope",
            "extracted_fields": {},
            "intake_status": "Needs Review",
            "missing_fields": [],
        },
    )

    with pytest.raises(Exception) as error:
        email_connector_routes._save_email(
            "gmail", "provider-message-unique", "sender@example.com",
            "Subject", "Body", None, organisation_id="org-a",
        )

    assert getattr(error.value, "status_code", None) == 409



def test_bootstrap_preserves_existing_super_admin_deactivation_and_password(tenant_api, monkeypatch):
    _, db, _ = tenant_api
    original_hash = "existing-password-hash"
    db["users"].insert_one({
        "email": "root@example.com",
        "full_name": "Existing Super Admin",
        "password_hash": original_hash,
        "role": "Claims Manager",
        "roles": ["Claims Manager"],
        "is_active": False,
    })
    monkeypatch.setattr(bootstrap, "get_database", lambda: db)
    monkeypatch.setattr(bootstrap, "database_is_available", lambda: True)
    monkeypatch.setattr(bootstrap, "INITIAL_ADMIN_EMAIL", "root@example.com")

    bootstrap.seed_initial_admin()

    updated = db["users"].find_one({"email": "root@example.com"})
    assert updated["is_active"] is False
    assert updated["password_hash"] == original_hash
    assert updated["roles"] == ["Super Administrator"]
    assert updated["is_global_admin"] is True



def test_manual_email_intake_rejects_cross_organisation_message_collision(tenant_api, monkeypatch):
    client, db, sign_in_as = tenant_api
    db["email_intake"].insert_one({
        "intake_id": "intake-org-b-manual",
        "provider": "gmail",
        "provider_message_id": "shared-provider-message",
        "organisation_id": "org-b",
    })
    monkeypatch.setattr(
        main,
        "extract_email_intake",
        lambda subject, body: {
            "intake_status": "Needs Review",
            "suggested_claim_type": None,
            "extracted_fields": {},
            "missing_fields": [],
        },
    )
    sign_in_as(manager())
    response = client.post(
        "/api/email-intake",
        json={
            "provider": "gmail",
            "provider_message_id": "shared-provider-message",
            "sender_email": "sender@example.com",
            "subject": "New claim",
            "body_text": "Please register a new claim.",
        },
    )

    assert response.status_code == 409
    assert "another organisation" in response.json()["detail"]
