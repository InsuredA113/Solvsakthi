"""Explicit, idempotent migration for assigning legacy claims to one insurer tenant.

Dry-run is the default. Run from solv-in/backend:
python -m scripts.migrate_legacy_claims --tenant-id <active-tenant-id>
Append --apply only after reviewing the dry-run count.
"""
import argparse
from datetime import datetime, timezone

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Existing active tenant ID to assign.")
    parser.add_argument("--apply", action="store_true", help="Write assignments; default is dry-run.")
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

        unassigned = {"$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}]}
        count = db["claims"].count_documents(unassigned)
        print(f"{'APPLY' if args.apply else 'DRY RUN'}: {count} legacy claim(s) have no tenant assignment.")
        print(f"Target tenant: {tenant['company_name']} ({tenant['tenant_id']})")
        if not args.apply:
            print("No records changed. Verify that every unassigned claim belongs to this insurer.")
            print("Re-run with --apply only after confirming the target tenant.")
            return 0

        result = db["claims"].update_many(
            unassigned,
            {"$set": {"tenant_id": tenant["tenant_id"], "tenant_migrated_at": datetime.now(timezone.utc).isoformat()}},
        )
        print(f"Matched {result.matched_count}; assigned tenant to {result.modified_count} claim(s).")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
