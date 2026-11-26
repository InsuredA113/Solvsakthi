"""Migrate email intake idempotency from global to tenant-scoped uniqueness.

Dry-run is the default. Run from solv-in/backend:
python -m scripts.migrate_email_intake_unique_index
Add --apply only after reviewing the audit. This script does not assign tenants
or alter intake records; tenantless legacy records are reported for review.
"""
import argparse

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI

OLD_KEYS = [("provider", 1), ("provider_message_id", 1)]
NEW_KEYS = [("tenant_id", 1), ("provider", 1), ("provider_message_id", 1)]
NEW_NAME = "tenant_provider_message_id_unique"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Create tenant index and drop old global index.")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        collection = client[MONGODB_DATABASE]["email_intake"]
        missing_tenant = collection.count_documents({
            "$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}, {"tenant_id": ""}]
        })
        duplicates = list(collection.aggregate([
            {"$match": {"tenant_id": {"$type": "string"}, "provider": {"$type": "string"},
                       "provider_message_id": {"$type": "string"}}},
            {"$group": {"_id": {"tenant_id": "$tenant_id", "provider": "$provider",
                               "provider_message_id": "$provider_message_id"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
            {"$limit": 20},
        ]))
        indexes = collection.index_information()
        old_names = [
            name for name, spec in indexes.items()
            if spec.get("unique") and spec.get("key") == OLD_KEYS
        ]
        print("APPLY" if args.apply else "DRY RUN")
        print(f"Tenantless legacy intake records requiring review: {missing_tenant}")
        print(f"Duplicate tenant/provider/message groups: {len(duplicates)}")
        for duplicate in duplicates:
            print(f"  {duplicate['_id']} count={duplicate['count']}")
        print(f"Existing global unique index(es): {old_names or 'none'}")
        print(f"Tenant-scoped index present: {NEW_NAME in indexes}")
        if duplicates:
            print("ABORT: resolve duplicate groups before applying.")
            return 2
        if not args.apply:
            print("No indexes changed. Re-run with --apply after reviewing the audit.")
            return 0

        collection.create_index(
            NEW_KEYS, name=NEW_NAME, unique=True,
            partialFilterExpression={"tenant_id": {"$type": "string"}},
        )
        for name in old_names:
            collection.drop_index(name)
        print("Tenant-scoped unique index created; old global unique index dropped.")
        if missing_tenant:
            print("WARNING: tenantless records remain excluded from the new index and need manual ownership review.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
