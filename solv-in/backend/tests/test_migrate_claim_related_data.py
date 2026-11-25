from scripts.migrate_claim_related_data import _build_claim_tenant_lookup


def test_claim_tenant_lookup_returns_unique_claim_ownership():
    lookup, ambiguous = _build_claim_tenant_lookup([
        {"claim_id": "CLM-1", "tenant_id": "ten_alpha"},
        {"claim_id": "CLM-2", "tenant_id": "ten_beta"},
    ])

    assert lookup == {"CLM-1": "ten_alpha", "CLM-2": "ten_beta"}
    assert ambiguous == set()


def test_claim_tenant_lookup_excludes_duplicate_claim_ids_even_when_tenant_matches():
    lookup, ambiguous = _build_claim_tenant_lookup([
        {"claim_id": "CLM-1", "tenant_id": "ten_alpha"},
        {"claim_id": "CLM-1", "tenant_id": "ten_alpha"},
        {"claim_id": "CLM-2", "tenant_id": "ten_beta"},
    ])

    assert lookup == {"CLM-2": "ten_beta"}
    assert ambiguous == {"CLM-1"}


def test_claim_tenant_lookup_marks_cross_tenant_duplicate_as_ambiguous():
    lookup, ambiguous = _build_claim_tenant_lookup([
        {"claim_id": "CLM-1", "tenant_id": "ten_alpha"},
        {"claim_id": "CLM-1", "tenant_id": "ten_beta"},
    ])

    assert "CLM-1" not in lookup
    assert ambiguous == {"CLM-1"}


def test_claim_tenant_lookup_keeps_claim_without_tenant_for_separate_skip_count():
    lookup, ambiguous = _build_claim_tenant_lookup([
        {"claim_id": "CLM-1", "tenant_id": None},
    ])

    assert lookup == {"CLM-1": None}
    assert ambiguous == set()
