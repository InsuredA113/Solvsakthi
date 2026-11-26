"""Deterministically assign legacy claims to the two demo insurer tenants.

Dry-run is the default. From solv-in/backend:
python -m scripts.migrate_claims_chat_gpt_split --chat-tenant-id <id> --gpt-tenant-id <id>

The first 93 claims in ascending Claim ID order go to Chat Insurance; the
remaining claims go to GPT Insurance. Applying requires an exact inventory,
the exact expected tenant company names, and MongoDB transaction support.
All writes run in one transaction so a failure rolls back the entire split.
"""
import argparse
from datetime import datetime, timezone

from pymongo import MongoClient

from app.config import MONGODB_DATABASE, MONGODB_URI
from scripts.tenant_migration_filters import unassigned_tenant_filter


CHAT_COMPANY_NAME = "Chat Insurance"
GPT_COMPANY_NAME = "GPT Insurance"
CHAT_INSURANCE_COUNT = 93
EXPECTED_CLAIM_COUNT = 185
MIGRATION_RULE = "chat_first_93_then_gpt_remaining_by_claim_id"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-tenant-id", required=True, help="Active Chat Insurance tenant ID.")
    parser.add_argument("--gpt-tenant-id", required=True, help="Active GPT Insurance tenant ID.")
    parser.add_argument(
        "--expected-count",
        type=int,
        default=EXPECTED_CLAIM_COUNT,
        help="Expected total claims; defaults to 185.",
    )
    parser.add_argument("--apply", action="store_true", help="Write assignments; default is dry-run.")
    args = parser.parse_args()

    if args.chat_tenant_id.strip() != args.chat_tenant_id or args.gpt_tenant_id.strip() != args.gpt_tenant_id:
        parser.error("Tenant IDs must not contain leading or trailing whitespace.")
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
        if chat_tenant.get("company_name") != CHAT_COMPANY_NAME:
            parser.error(f"Chat tenant ID must belong to exactly '{CHAT_COMPANY_NAME}'.")
        if gpt_tenant.get("company_name") != GPT_COMPANY_NAME:
            parser.error(f"GPT tenant ID must belong to exactly '{GPT_COMPANY_NAME}'.")

        claims = list(db["claims"].find({}, {"_id": 1, "claim_id": 1, "tenant_id": 1}))
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

        assignments = [
            (claim, args.chat_tenant_id if index < CHAT_INSURANCE_COUNT else args.gpt_tenant_id)
            for index, claim in enumerate(claims)
        ]
        conflicts = [
            (claim.get("claim_id"), claim.get("tenant_id"), target)
            for claim, target in assignments
            if claim.get("tenant_id") not in (None, "") and claim.get("tenant_id") != target
        ]
        if conflicts:
            parser.error(
                f"{len(conflicts)} claims already have a different tenant assignment. "
                "No records changed; inspect these conflicts manually."
            )

        chat_claims = assignments[:CHAT_INSURANCE_COUNT]
        gpt_claims = assignments[CHAT_INSURANCE_COUNT:]
        print("APPLY MODE" if args.apply else "DRY-RUN MODE (no records will be changed)")
        print(f"Chat Insurance ({CHAT_COMPANY_NAME}): {len(chat_claims)} claims; "
              f"{chat_claims[0][0]['claim_id']} .. {chat_claims[-1][0]['claim_id']}")
        print(f"GPT Insurance ({GPT_COMPANY_NAME}): {len(gpt_claims)} claims; "
              f"{gpt_claims[0][0]['claim_id']} .. {gpt_claims[-1][0]['claim_id']}")
        print("Ordering: ascending Claim ID, with MongoDB _id as a deterministic tie-breaker.")
        print("Review the exact ranges and tenant IDs before applying.")

        if not args.apply:
            print("Dry run complete. No records changed.")
            return 0

        # MongoDB transactions require a replica set or sharded cluster.
        # Atlas supports this; standalone MongoDB safely fails without partial writes.
        migrated_at = datetime.now(timezone.utc).isoformat()
        updated = 0
        already_assigned = 0
        with client.start_session() as session:
            with session.start_transaction():
                # Revalidate tenant identity/status inside the transaction.
                for tenant_id, expected_name in (
                    (args.chat_tenant_id, CHAT_COMPANY_NAME),
                    (args.gpt_tenant_id, GPT_COMPANY_NAME),
                ):
                    current = tenants.find_one(
                        {"tenant_id": tenant_id, "status": "Active", "company_name": expected_name},
                        {"_id": 1},
                        session=session,
                    )
                    if not current:
                        raise RuntimeError(
                            f"Tenant {tenant_id} is no longer the active {expected_name}; transaction aborted."
                        )

                current_claims = list(db["claims"].find(
                    {}, {"_id": 1, "claim_id": 1, "tenant_id": 1}, session=session
                ))
                if len(current_claims) != args.expected_count:
                    raise RuntimeError(
                        f"Claim inventory changed during migration (now {len(current_claims)}); transaction aborted."
                    )
                current_by_id = {str(row["_id"]): row for row in current_claims}
                if len(current_by_id) != args.expected_count:
                    raise RuntimeError("Claim record identity check failed; transaction aborted.")

                for original, tenant_id in assignments:
                    claim = current_by_id.get(str(original["_id"]))
                    if not claim or claim.get("claim_id") != original["claim_id"]:
                        raise RuntimeError("A claim changed during migration; transaction aborted.")
                    if claim.get("tenant_id") == tenant_id:
                        already_assigned += 1
                        continue
                    if claim.get("tenant_id") not in (None, ""):
                        raise RuntimeError(
                            f"Claim {claim['claim_id']} has a conflicting tenant; transaction aborted."
                        )
                    result = db["claims"].update_one(
                        {"_id": claim["_id"], **unassigned_tenant_filter()},
                        {"$set": {
                            "tenant_id": tenant_id,
                            "tenant_migrated_at": migrated_at,
                            "tenant_migration_rule": MIGRATION_RULE,
                        }},
                        session=session,
                    )
                    if result.modified_count != 1:
                        raise RuntimeError(
                            f"Claim {claim['claim_id']} changed during migration; transaction aborted."
                        )
                    updated += 1

        print(f"Migration committed: assigned {updated}; already correctly assigned {already_assigned}.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
