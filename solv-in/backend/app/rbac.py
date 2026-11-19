"""Solv-In role catalogue and compatibility helpers.

This module keeps legacy single-role accounts working while enabling multiple
roles per user. Permission checks remain server-side; UI visibility is not
an authorization boundary.
"""
from __future__ import annotations

# Canonical role names. Super Administrator already exists and is not duplicated.
PREDEFINED_ROLES = (
    "Super Administrator",
    "Insurance Company Administrator",
    "Claims Manager",
    "Property Adjuster",
    "Casualty Adjuster",
    "Claims Approver",
    "Insurance Surveyor",
    "Actuarial Analyst",
    "Auditor",
    "Underwriting Specialist",
    "Customer / Policyholder",
    "Compliance Officer",
    "Finance Officer",
    "Reinsurance Analyst",
)

# Legacy names are accepted during migration and normalized on read.
ROLE_ALIASES = {
    "Approver": "Claims Approver",
    "Surveyor": "Insurance Surveyor",
    "Customer": "Customer / Policyholder",
}

# Permission identifiers are stable API-level keys, not display labels.
PERMISSIONS = (
    "dashboard.view",
    "claims.view", "claims.create", "claims.edit", "claims.delete", "claims.assign",
    "claims.approve", "claims.export",
    "evidence.view", "evidence.upload", "evidence.delete",
    "damage.view", "damage.analyse", "damage.review",
    "coverage.view", "coverage.review",
    "finance.view", "finance.edit", "finance.approve",
    "reports.view", "reports.compare", "reports.export", "reports.comment",
    "analytics.view",
    "users.view", "users.create", "users.edit", "users.deactivate",
    "roles.view", "roles.manage",
    "audit.view",
    "underwriting.view", "underwriting.review",
    "reinsurance.view", "reinsurance.review",
    "compliance.view", "compliance.manage",
    "own_claims.view", "own_claims.create",
)

ROLE_PERMISSIONS = {
    "Super Administrator": frozenset(PERMISSIONS),
    "Insurance Company Administrator": frozenset({
        "dashboard.view", "claims.view", "claims.create", "claims.edit", "claims.assign",
        "claims.export", "evidence.view", "evidence.upload", "damage.view", "damage.analyse",
        "damage.review", "coverage.view", "coverage.review", "finance.view", "reports.view",
        "reports.compare", "reports.export", "reports.comment", "analytics.view",
        "users.view", "users.create", "users.edit", "users.deactivate", "roles.view",
        "roles.manage", "audit.view", "underwriting.view", "reinsurance.view",
        "compliance.view",
    }),
    "Claims Manager": frozenset({
        "dashboard.view", "claims.view", "claims.create", "claims.edit", "claims.assign", "claims.approve",
        "claims.export", "evidence.view", "evidence.upload", "damage.view", "damage.analyse", "damage.review", "finance.edit", "finance.approve",
        "coverage.view", "coverage.review", "reports.view", "reports.compare",
        "reports.export", "reports.comment", "analytics.view",
    }),
    "Property Adjuster": frozenset({
        "dashboard.view", "claims.view", "claims.edit", "evidence.view", "evidence.upload", "finance.edit",
        "damage.view", "damage.analyse", "damage.review", "coverage.view", "reports.view",
        "reports.compare", "reports.comment",
    }),
    "Casualty Adjuster": frozenset({
        "dashboard.view", "claims.view", "claims.edit", "evidence.view", "evidence.upload", "finance.edit",
        "damage.view", "damage.analyse", "damage.review", "coverage.view", "reports.view", "reports.compare", "reports.comment",
    }),
    "Claims Approver": frozenset({
        "dashboard.view", "claims.view", "claims.approve", "claims.export", "damage.view",
        "damage.review", "coverage.view", "coverage.review", "finance.view",
        "finance.approve", "reports.view", "reports.compare", "reports.comment",
    }),
    "Insurance Surveyor": frozenset({
        "claims.view", "evidence.view", "evidence.upload", "damage.view", "damage.analyse",
        "damage.review", "reports.view", "reports.comment",
    }),
    "Actuarial Analyst": frozenset({
        "dashboard.view", "claims.view", "damage.view", "damage.analyse", "coverage.view", "finance.view", "finance.edit",
        "reports.view", "reports.compare", "reports.export", "analytics.view",
    }),
    "Auditor": frozenset({
        "dashboard.view", "claims.view", "evidence.view", "damage.view", "coverage.view",
        "finance.view", "reports.view", "reports.compare", "analytics.view", "audit.view",
    }),
    "Underwriting Specialist": frozenset({
        "claims.view", "coverage.view", "underwriting.view", "underwriting.review",
        "reports.view",
    }),
    "Customer / Policyholder": frozenset({
        "own_claims.view", "own_claims.create", "evidence.view", "evidence.upload",
    }),
    "Compliance Officer": frozenset({
        "dashboard.view", "claims.view", "reports.view", "analytics.view",
        "compliance.view", "compliance.manage", "audit.view",
    }),
    "Finance Officer": frozenset({
        "dashboard.view", "claims.view", "finance.view", "finance.edit",
        "reports.view", "analytics.view",
    }),
    "Reinsurance Analyst": frozenset({
        "claims.view", "finance.view", "reports.view", "reinsurance.view",
        "reinsurance.review", "analytics.view",
    }),
}


def normalize_role(role: str | None) -> str | None:
    if not role:
        return None
    cleaned = role.strip()
    return ROLE_ALIASES.get(cleaned, cleaned)


def get_user_roles(user: dict) -> list[str]:
    raw_roles = user.get("roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        raw_roles = [user.get("role")]
    normalized = []
    for role in raw_roles:
        value = normalize_role(role)
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def user_has_any_role(user: dict, allowed_roles: tuple[str, ...]) -> bool:
    allowed = {normalize_role(role) for role in allowed_roles}
    return bool(set(get_user_roles(user)) & allowed)


def get_effective_permissions(user: dict) -> set[str]:
    """Union permissions from all assigned predefined roles.

    Custom-role permissions are included from the user's embedded custom_roles
    entries only after the role has been granted by an administrator.
    """
    effective: set[str] = set()
    for role in get_user_roles(user):
        effective.update(ROLE_PERMISSIONS.get(role, ()))
    for custom_role in user.get("custom_roles", []):
        if isinstance(custom_role, dict):
            effective.update(p for p in custom_role.get("permissions", []) if p in PERMISSIONS)
    return effective
