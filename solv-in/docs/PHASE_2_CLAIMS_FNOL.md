# Phase 2 – Claims Management and FNOL

## Included
- MongoDB-backed Property and Casualty claim creation.
- Sequential claim IDs in the format CLM-YYYY-000001.
- Claims register with search by claim ID, customer or policy.
- Claim type and workflow status filters.
- Claim status update control.
- Dashboard counts sourced from MongoDB when connected.
- FNOL form with required-field validation and visible save errors.

## API
- GET /api/health
- GET /api/dashboard/summary
- GET /api/claims?search=&claim_type=&status=&limit=
- POST /api/claims
- PATCH /api/claims/{claim_id}/status

## Not included
Authentication, roles, tenant isolation, document uploads, policy validation, AI extraction, image damage assessment, financial estimation, email intake and audit history are future work.

Use fictional test data only. This development build is not approved for real customer claim information.
