import unittest

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
        self.assertIn("Super Administrator", get_effective_permissions({"role": "Super Administrator"}))


if __name__ == "__main__":
    unittest.main()
