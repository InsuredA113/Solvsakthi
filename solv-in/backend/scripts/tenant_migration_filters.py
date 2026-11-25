"""Shared filters for safe legacy tenant migration."""

def unassigned_tenant_filter() -> dict:
    """Match records whose tenant assignment is absent, null, or blank."""
    return {
        "$or": [
            {"tenant_id": {"$exists": False}},
            {"tenant_id": None},
            {"tenant_id": ""},
        ]
    }
