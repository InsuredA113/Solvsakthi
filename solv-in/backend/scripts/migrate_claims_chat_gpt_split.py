"""Deterministically assign legacy claims to the two demo insurer tenants.

Dry-run is the default. From solv-in/backend:
python -m scripts.migrate_claims_chat_gpt_split --chat-tenant-id <id> --gpt-tenant-id <id>
Add --apply only after reviewing the planned Claim ID ranges and counts.

The first 93 claims in ascending Claim ID order go to Chat Insurance; the
remaining claims go to GPT Insurance. The expected total defaults to 185 and
must be explicitly changed if the verified inventory differs.
"""
import argparse
from datetime import datetime, timezone

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI
from scripts.tenant_migration_filters import unassigned_tenant_filter


CHAT_INSURANCE_COUNT = 93
EXPECTED_CLAIM_COUNT = 185


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-tenant-id", required=True, help="Existing active Chat Insurance tenant ID.")
    parser.add_argument("--gpt-tenant-id", required=True, help="Existing active GPT Insurance tenant ID.")
    parser.add_argument(
        "--expected-count",
        type=int,
        default=EXPECTED_CLAIM_COUNT,
        help="Expected total claims; defaults to the verified inventory of 185.",
    )
    parser.add_argument("--apply", action="store_true", help="Write assignments; default is dry-run.")
    args = parser.parse_args()

    if args.chat_tenant_id == args.gpt_tenant_id:
        parser.error("Chat Insurance and GPT Insurance must use different tenant IDs.")
    if args.expected_count <= CHAT_INSURANCE_COUNT:
        parser.error(f"Expected claim count must be greater than {CHAT_INSURANCE_COUNT}.")

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        db = client[MONGODB_DATABASE]
        tenants = db["tenants"]
        chat_tenant = tenants.find_one(
            {"tenant_id": args.chat_tenant_id, "status": "Active"},
            {"_id": 0, "tenant_id": 1, "company_name": 1},
        )
        gpt_tenant = tenants.find_one(
            {"tenant_id": args.gpt_tenant_id, "status": "Active"},
            {"_id": 0, "tenant_id": 1, "company_name": 1},
        )
        if not chat_tenant or not gpt_tenant:
            parser.error("Both target tenants must already exist and be Active.")

        claims = list(db["claims"].find(
            {},
            {"_id": 1, "claim_id": 1, "tenant_id": 1},
        ))
        if len(claims) != args.expected_count:
            parser.error(
                f"Expected exactly {args.expected_count} claims, found {len(claims)}. "
                "Stop and review the inventory; do not apply this split."
            )

        claims.sort(key=lambda row: (str(row.get("claim_id") or ""), str(row["_id"])))
        claim_ids = [row.get("claim_id") for row in claims]
        if any(not isinstance(value, str) or not value.strip() for value in claim_ids):
            parser.error("At least one claim has a missing/invalid Claim ID.")
        if len(set(claim_ids)) != len(claim_ids):
            parser.error("Duplicate Claim IDs found; no migration performed.")

        assignments = []
        for index, claim in enumerate(claims):
            tenant_id = args.chat_tenant_id if index < CHAT_INSURANCE_COUNT else args.gpt_tenant_id
            assignments.append((claim, tenant_id))

        conflicts = [
            (claim.get("claim_id"), claim.get("tenant_id"), target)
            for claim, target in assignments
            if claim.get("tenant_id") and claim.get("tenant_id") != target
        ]
        if conflicts:
            parser.error(
                f"{len(conflicts)} claims already have a different tenant assignment. "
                "No records changed; inspect these conflicts manually."
            )

        chat_claims = assignments[:CHAT_INSURANCE_COUNT]
        gpt_claims = assignments[CHAT_INSURANCE_COUNT:]
        print("APPLY MODE" if args.apply else "DRY-RUN MODE (no records will be changed)")
        print(f"Chat Insurance: {len(chat_claims)} claims; Claim IDs {chat_claims[0][0]['claim_id']} .. {chat_claims[-1][0]['claim_id']}")
        print(f"GPT Insurance: {len(gpt_claims)} claims; Claim IDs {gpt_claims[0][0]['claim_id']} .. {gpt_claims[-1][0]['claim_id']}")
        print("Ordering: ascending Claim ID, with MongoDB _id as a deterministic tie-breaker.")
        print("Review the exact ranges and confirm tenant IDs before applying.")

        if not args.apply:
            print("Dry run complete. No records changed.")
            return 0

        updated = 0
        already_assigned = 0
        for claim, tenant_id in assignments:
            if claim.get("tenant_id") == tenant_id:
                already_assigned += 1
                continue
            result = db["claims"].update_one(
                {"_id": claim["_id"], **unassigned_tenant_filter()},
                {"$set": {
                    "tenant_id": tenant_id,
                    "tenant_migrated_at": datetime.now(timezone.utc).isoformat(),
                    "tenant_migration_rule": "chat_first_93_then_gpt_remaining_by_claim_id",
                }},
            )
            if result.modified_count != 1:
                raise RuntimeError(
                    f"Claim {claim['claim_id']} changed during migration; stop and review. "
                    f"Successfully assigned so far: {updated}."
                )
            updated += 1

        print(f"Migration finished: assigned {updated}; already correctly assigned {already_assigned}.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
