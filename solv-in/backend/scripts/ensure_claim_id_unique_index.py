"""Audit Claim ID duplicates and optionally create a safe unique index.

Run from solv-in/backend:
    python -m scripts.ensure_claim_id_unique_index
    python -m scripts.ensure_claim_id_unique_index --create-index

The default mode is read-only. The index is only created when no duplicate
string Claim IDs exist. This script never edits or deletes claim records.
"""
import argparse
from app.database import get_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--create-index",
        action="store_true",
        help="Create the partial unique Claim ID index after a duplicate audit.",
    )
    args = parser.parse_args()

    claims = get_database()["claims"]
    duplicates = list(claims.aggregate([
        {"$match": {"claim_id": {"$type": "string"}}},
        {"$group": {"_id": "$claim_id", "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
    ]))

    if duplicates:
        print(f"Found {len(duplicates)} duplicate Claim ID value(s).")
        for row in duplicates[:100]:
            print(f"  {row['_id']}: {row['count']} records")
        if len(duplicates) > 100:
            print(f"  ... and {len(duplicates) - 100} more")
        print("No index created. Review and reconcile duplicates manually.")
        return 1

    print("Duplicate audit passed: no duplicate string Claim IDs found.")
    if not args.create_index:
        print("Read-only mode. Re-run with --create-index to enforce uniqueness.")
        return 0

    name = claims.create_index(
        [("claim_id", 1)],
        unique=True,
        name="claim_id_unique",
        partialFilterExpression={"claim_id": {"$type": "string"}},
    )
    print(f"Created unique partial index: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
