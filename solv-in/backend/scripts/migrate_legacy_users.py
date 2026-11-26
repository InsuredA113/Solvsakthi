"""Safely assign legacy insurer users from an explicit email-to-tenant mapping.

Dry-run is the default. The JSON mapping must be an object whose keys are user
emails and values are existing active tenant IDs. Example:
{"adjuster@example.com": "ten_abc", "manager@example.com": "ten_xyz"}

Run from solv-in/backend:
python -m scripts.migrate_legacy_users --mapping-file user_tenant_map.json
Add --apply only after reviewing the dry-run output.
"""
import argparse
import json
from pathlib import Path

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI
from scripts.tenant_migration_filters import unassigned_tenant_filter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping-file", required=True, help="JSON object mapping user email to tenant_id.")
    parser.add_argument("--apply", action="store_true", help="Apply assignments; without this flag only report.")
    args = parser.parse_args()

    try:
        mapping = json.loads(Path(args.mapping_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"Unable to read a valid mapping file: {exc}")
    if not isinstance(mapping, dict) or not mapping:
        parser.error("Mapping file must be a non-empty JSON object of email: tenant_id pairs.")
    normalized = {}
    for email, tenant_id in mapping.items():
        if not isinstance(email, str) or not email.strip() or not isinstance(tenant_id, str) or not tenant_id.strip():
            parser.error("Every mapping must contain a non-empty email and tenant ID.")
        key = email.strip().lower()
        if key in normalized:
            parser.error(f"Duplicate user email in mapping: {key}")
        normalized[key] = tenant_id.strip()

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[MONGODB_DATABASE]
        tenant_ids = sorted(set(normalized.values()))
        tenants = {
            row["tenant_id"]: row
            for row in db["tenants"].find(
                {"tenant_id": {"$in": tenant_ids}, "status": "Active"},
                {"_id": 0, "tenant_id": 1, "company_name": 1},
            )
        }
        missing = set(tenant_ids) - set(tenants)
        if missing:
            parser.error("Inactive or missing tenant ID(s): " + ", ".join(sorted(missing)))

        matched = []
        unresolved = []
        for email, tenant_id in normalized.items():
            user = db["users"].find_one(
                {"email": {"$regex": "^" + __import__("re").escape(email) + "$", "$options": "i"},
                 "role": {"$ne": "Super Administrator"}, **unassigned_tenant_filter()},
                {"_id": 0, "email": 1, "full_name": 1, "role": 1},
            )
            if user:
                matched.append((user, tenant_id))
            else:
                unresolved.append(email)

        print("DRY RUN" if not args.apply else "APPLY")
        print(f"Explicit mappings: {len(normalized)}; eligible users: {len(matched)}; unresolved/already assigned: {len(unresolved)}")
        for user, tenant_id in matched:
            print(f"  {user.get('email')} | {user.get('role')} | {tenants[tenant_id].get('company_name')} ({tenant_id})")
        if unresolved:
            print("Not assigned (not found, Super Administrator, or already tenant-assigned): " + ", ".join(unresolved))
        if not args.apply:
            print("No records changed. Re-run with --apply only after verifying every listed assignment.")
            return 0

        for user, tenant_id in matched:
            result = db["users"].update_one(
                {"email": user["email"], "role": {"$ne": "Super Administrator"}, **unassigned_tenant_filter()},
                {"$set": {"tenant_id": tenant_id, "tenant_access_enabled": False}},
            )
            if result.modified_count != 1:
                raise RuntimeError(f"Assignment changed concurrently for {user.get('email')}; stop and reconcile.")
        print(f"Assigned {len(matched)} user(s). Tenant access remains disabled.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
