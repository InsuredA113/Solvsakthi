"""Backfill tenant IDs on claim-linked records from their authoritative parent claims.

Dry-run is the default. Run from solv-in/backend:
python -m scripts.migrate_claim_related_data
Append --apply only after reviewing every collection's planned updates and skips.
"""
import argparse
from datetime import datetime, timezone

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI

CLAIM_LINKED_COLLECTIONS = (
    "damage_assessments",
    "financial_estimates",
    "coverage_assessments",
    "comparison_comments",
    "adjuster_reports",
    "claim_export_audit",
    "email_intake",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write assignments; default is dry-run.")
    args = parser.parse_args()

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[MONGODB_DATABASE]
        totals = {"would_update": 0, "updated": 0, "already_assigned": 0, "skipped_no_parent": 0,
                  "skipped_parent_without_tenant": 0, "skipped_conflict": 0}
        print("APPLY MODE" if args.apply else "DRY-RUN MODE (no records will be changed)")
        for collection_name in CLAIM_LINKED_COLLECTIONS:
            collection = db[collection_name]
            counts = {"would_update": 0, "updated": 0, "already_assigned": 0,
                      "skipped_no_parent": 0, "skipped_parent_without_tenant": 0, "skipped_conflict": 0}
            cursor = collection.find(
                {"claim_id": {"$exists": True, "$nin": [None, ""]}},
                {"_id": 1, "claim_id": 1, "tenant_id": 1},
            )
            for row in cursor:
                parent = db["claims"].find_one(
                    {"claim_id": row["claim_id"]},
                    {"_id": 0, "tenant_id": 1},
                )
                if not parent:
                    counts["skipped_no_parent"] += 1
                    continue
                parent_tenant = parent.get("tenant_id")
                if not parent_tenant:
                    counts["skipped_parent_without_tenant"] += 1
                    continue
                existing_tenant = row.get("tenant_id")
                if existing_tenant:
                    if existing_tenant == parent_tenant:
                        counts["already_assigned"] += 1
                    else:
                        counts["skipped_conflict"] += 1
                    continue
                counts["would_update"] += 1
                if args.apply:
                    result = collection.update_one(
                        {"_id": row["_id"], "$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}]},
                        {"$set": {"tenant_id": parent_tenant, "tenant_migrated_at": datetime.now(timezone.utc).isoformat()}},
                    )
                    counts["updated"] += result.modified_count
            totals = {key: totals[key] + counts[key] for key in totals}
            print(f"{collection_name}: {counts}")

        policy_counts = {"would_update": 0, "updated": 0, "already_assigned": 0, "skipped_unreferenced": 0, "skipped_ambiguous": 0, "skipped_conflict": 0}
        for policy in db["policies"].find({}, {"_id": 1, "policy_number": 1, "tenant_id": 1}):
            tenant_ids = db["claims"].distinct(
                "tenant_id",
                {"policy_number": policy.get("policy_number"), "tenant_id": {"$exists": True, "$nin": [None, ""]}},
            )
            existing_tenant = policy.get("tenant_id")
            if existing_tenant:
                if not tenant_ids or set(tenant_ids) == {existing_tenant}:
                    policy_counts["already_assigned"] += 1
                else:
                    policy_counts["skipped_conflict"] += 1
                continue
            if not tenant_ids:
                policy_counts["skipped_unreferenced"] += 1
                continue
            if len(tenant_ids) != 1:
                policy_counts["skipped_ambiguous"] += 1
                continue
            policy_counts["would_update"] += 1
            if args.apply:
                result = db["policies"].update_one(
                    {"_id": policy["_id"], "$or": [{"tenant_id": {"$exists": False}}, {"tenant_id": None}]},
                    {"$set": {"tenant_id": tenant_ids[0], "tenant_migrated_at": datetime.now(timezone.utc).isoformat()}},
                )
                policy_counts["updated"] += result.modified_count
        print(f"policies: {policy_counts}")
        unlinked_intake_count = db["email_intake"].count_documents({
            "$or": [{"claim_id": {"$exists": False}}, {"claim_id": None}, {"claim_id": ""}]
        })
        print(f"email_intake without a linked claim (manual ownership review required): {unlinked_intake_count}")

        files = db["claim_documents.files"]
        file_counts = {"would_update": 0, "updated": 0, "already_assigned": 0,
                       "skipped_no_parent": 0, "skipped_parent_without_tenant": 0, "skipped_conflict": 0}
        for row in files.find({"metadata.claim_id": {"$exists": True}} , {"_id": 1, "metadata": 1}):
            metadata = row.get("metadata") or {}
            parent = db["claims"].find_one({"claim_id": metadata.get("claim_id")}, {"_id": 0, "tenant_id": 1})
            if not parent:
                file_counts["skipped_no_parent"] += 1
                continue
            parent_tenant = parent.get("tenant_id")
            if not parent_tenant:
                file_counts["skipped_parent_without_tenant"] += 1
                continue
            existing_tenant = metadata.get("tenant_id")
            if existing_tenant:
                if existing_tenant == parent_tenant:
                    file_counts["already_assigned"] += 1
                else:
                    file_counts["skipped_conflict"] += 1
                continue
            file_counts["would_update"] += 1
            if args.apply:
                result = files.update_one(
                    {"_id": row["_id"], "$or": [{"metadata.tenant_id": {"$exists": False}}, {"metadata.tenant_id": None}]},
                    {"$set": {"metadata.tenant_id": parent_tenant, "metadata.tenant_migrated_at": datetime.now(timezone.utc).isoformat()}},
                )
                file_counts["updated"] += result.modified_count
        print(f"claim_documents.files: {file_counts}")

        print(f"TOTALS: {totals}")
        if not args.apply:
            print("No records changed. Claims without a tenant, policies referenced by multiple tenants, missing parent claims, and conflicting tenant IDs were intentionally skipped.")
            print("Review the counts before re-running with --apply.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
