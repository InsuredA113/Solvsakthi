"""Explicit, idempotent migration for assigning legacy insurer users to one tenant.

Dry-run is the default. Pass --apply only after reviewing the dry-run count.
Run from solv-in/backend: python -m scripts.migrate_legacy_users --tenant-id <id>
"""
import argparse

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Existing active tenant ID to assign.")
    parser.add_argument("--apply", action="store_true", help="Apply the migration; without this flag only report the count.")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[MONGODB_DATABASE]
        tenant = db["tenants"].find_one(
            {"tenant_id": args.tenant_id, "status": "Active"},
            {"_id": 0, "tenant_id": 1, "company_name": 1},
        )
        if not tenant:
            parser.error("The target tenant does not exist or is not active.")

        legacy_filter = {
            "role": {"$ne": "Super Administrator"},
            "$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}],
        }
        count = db["users"].count_documents(legacy_filter)
        mode = "APPLY" if args.apply else "DRY RUN"
        print(f"{mode}: {count} legacy non-super-admin user(s) eligible for tenant assignment.")
        print(f"Target tenant: {tenant['company_name']} ({tenant['tenant_id']})")
        if not args.apply:
            print("No records changed. Re-run with --apply only after verifying the target tenant.")
            return 0

        result = db["users"].update_many(
            legacy_filter,
            {"$set": {"tenant_id": tenant["tenant_id"]}},
        )
        print(f"Matched {result.matched_count}; assigned tenant to {result.modified_count} user(s).")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
