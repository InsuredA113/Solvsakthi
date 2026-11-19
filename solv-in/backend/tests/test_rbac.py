import unittest

from fastapi import HTTPException
from app.auth import require_permissions
from app.organisations import organisation_filter, scoped_claim_filter
from app.rbac import (
    PREDEFINED_ROLES,
    get_effective_permissions,
    get_user_roles,
    normalize_role,
    user_has_any_role,
)


class RoleCompatibilityTests(unittest.TestCase):
    def test_legacy_role_aliases_are_normalized(self):
        self.assertEqual(normalize_role("Approver"), "Claims Approver")
        self.assertEqual(normalize_role("Surveyor"), "Insurance Surveyor")
        self.assertEqual(normalize_role("Customer"), "Customer / Policyholder")

    def test_legacy_single_role_user_is_supported(self):
        user = {"role": "Approver"}
        self.assertEqual(get_user_roles(user), ["Claims Approver"])
        self.assertTrue(user_has_any_role(user, ("Claims Approver",)))

    def test_empty_roles_array_falls_back_to_legacy_role(self):
        self.assertEqual(get_user_roles({"roles": [], "role": "Auditor"}), ["Auditor"])

    def test_multiple_predefined_roles_are_combined(self):
        user = {"roles": ["Property Adjuster", "Actuarial Analyst"]}
        permissions = get_effective_permissions(user)
        self.assertIn("damage.analyse", permissions)
        self.assertIn("finance.view", permissions)
        self.assertTrue(user_has_any_role(user, ("Actuarial Analyst",)))

    def test_custom_role_permissions_are_filtered_to_registry(self):
        user = {"roles": ["Auditor"], "custom_roles": [
            {"name": "Extra", "permissions": ["claims.export", "root.takeover"]}
        ]}
        permissions = get_effective_permissions(user)
        self.assertIn("claims.export", permissions)
        self.assertNotIn("root.takeover", permissions)

    def test_super_administrator_is_predefined_once(self):
        self.assertEqual(list(PREDEFINED_ROLES).count("Super Administrator"), 1)
        self.assertIn("claims.view", get_effective_permissions({"role": "Super Administrator"}))
        self.assertIn("roles.manage", get_effective_permissions({"role": "Super Administrator"}))

    def test_claims_manager_has_claim_and_financial_approval_permissions(self):
        permissions = get_effective_permissions({"roles": ["Claims Manager"]})
        self.assertIn("claims.approve", permissions)
        self.assertIn("finance.approve", permissions)
        self.assertIn("finance.edit", permissions)

    def test_adjuster_can_create_estimates_but_not_approve_financials(self):
        permissions = get_effective_permissions({"roles": ["Property Adjuster"]})
        self.assertIn("finance.edit", permissions)
        self.assertNotIn("finance.approve", permissions)

    def test_custom_role_can_add_only_registered_permissions(self):
        permissions = get_effective_permissions({"roles": ["Auditor"], "custom_roles": [
            {"name": "Reviewer", "permissions": ["claims.approve", "not.a.permission"]}
        ]})
        self.assertIn("claims.approve", permissions)
        self.assertNotIn("not.a.permission", permissions)

    def test_permission_dependency_allows_granted_permission(self):
        user = {"roles": ["Claims Manager"]}
        self.assertIs(require_permissions("claims.view")(user=user), user)

    def test_permission_dependency_rejects_missing_permission(self):
        with self.assertRaises(HTTPException) as error:
            require_permissions("claims.delete")(user={"roles": ["Auditor"]})
        self.assertEqual(error.exception.status_code, 403)

    def test_permission_dependency_rejects_unknown_permission_keys(self):
        with self.assertRaises(ValueError):
            require_permissions("system.takeover")


class OrganisationScopeTests(unittest.TestCase):
    def test_regular_user_is_scoped_to_exact_organisation(self):
        user = {"email": "adjuster@example.test", "organisation_id": "org-a"}
        self.assertEqual(organisation_filter(user), {"organisation_id": "org-a"})

    def test_only_explicit_global_admin_bypasses_organisation_filter(self):
        self.assertEqual(
            organisation_filter({"role": "Super Administrator"}),
            {"organisation_id": None},
        )
        self.assertEqual(
            organisation_filter({"is_global_admin": True}),
            {},
        )

    def test_unassigned_non_global_user_is_denied(self):
        with self.assertRaises(HTTPException) as error:
            organisation_filter({"roles": ["Claims Manager"]})
        self.assertEqual(error.exception.status_code, 403)

    def test_claim_filter_always_includes_tenant_boundary(self):
        self.assertEqual(
            scoped_claim_filter("CLM-2026-000001", {"organisation_id": "org-a"}),
            {"$and": [
                {"claim_id": "CLM-2026-000001"},
                {"organisation_id": "org-a"},
            ]},
        )


if __name__ == "__main__":
    unittest.main()
