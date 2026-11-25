# Claim ID uniqueness safeguard

Solv-In generates Claim IDs from a yearly sequence. Related-data cleanup and
cross-reference operations rely on each Claim ID identifying one claim.

Before enforcing a MongoDB unique index, run the audit in the backend directory:

```bash
python -m scripts.ensure_claim_id_unique_index
```

The command is read-only and reports duplicate Claim ID values. Resolve any
duplicates using a reviewed data-reconciliation plan; the script does not
change or delete claims.

Only after the duplicate audit is clean, and after approving the database change,
run:

```bash
python -m scripts.ensure_claim_id_unique_index --create-index
```

This creates a partial unique index for string Claim IDs. It is not run at
application startup and does not automatically modify the database.
