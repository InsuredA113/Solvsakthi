from scripts.tenant_migration_filters import unassigned_tenant_filter


def test_unassigned_tenant_filter_matches_missing_null_and_blank_values():
    assert unassigned_tenant_filter() == {
        "$or": [
            {"tenant_id": {"$exists": False}},
            {"tenant_id": None},
            {"tenant_id": ""},
        ]
    }
