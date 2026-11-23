# Phase 3 – Authentication and Role-Based Access

## Implemented
- JSON email/password sign-in endpoint.
- JWT access tokens with configurable expiry.
- Argon2 password hashing via pwdlib.
- Database-backed active-user validation on authenticated requests.
- Protected claims and dashboard endpoints.
- Role permissions for claim read, create and status-update actions.
- Super Administrator user provisioning and user listing.
- Optional first-run Super Administrator bootstrap from backend environment variables.
- Angular sign-in/sign-out and a Users administration screen.

## Initial setup
1. Copy backend/.env.example to backend/.env.
2. Set JWT_SECRET_KEY to a random secret of at least 32 characters.
3. Set INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD to credentials you create and keep private.
4. Ensure MongoDB is running before starting FastAPI for the first time.
5. Install backend requirements and start the API.
6. Start the Angular application and sign in using the initial administrator credentials.

## Role permissions currently enforced
- Read claims/dashboard: Super Administrator, Insurance Company Administrator, Claims Manager, Property Adjuster, Casualty Adjuster, Surveyor, Actuarial Analyst, Approver, Auditor.
- Create claims: Super Administrator, Insurance Company Administrator, Claims Manager, Property Adjuster, Casualty Adjuster.
- Update claim status: Super Administrator, Insurance Company Administrator, Claims Manager, Property Adjuster, Casualty Adjuster, Approver.
- Create/list users: Super Administrator only.

## Important limitations
This is a development implementation, not a production identity and access management system. Insurer tenant isolation, claim-level ownership, property/casualty claim-type scoping, Customer self-service, MFA, password reset/change, refresh-token rotation, account lockout/rate limiting, comprehensive audit logging, and security testing are not yet implemented. The Insurance Company Administrator role is recognised for claim API permissions, but user administration remains Super Administrator-only until tenant boundaries exist. Do not use real customer or policyholder data.

## Phase 3A-1 – Tenant registry foundation (in progress)

Implemented on branch `feature/phase-3a-1-tenant-foundation`:
- Added a MongoDB `tenants` collection registry with unique indexes for `tenant_id` and `tenant_slug`.
- Added Super Administrator-only endpoints: `GET /api/tenants`, `POST /api/tenants`, `GET /api/tenants/{tenant_id}`, and `PATCH /api/tenants/{tenant_id}/status`.
- Added reusable database-backed tenant authorization dependencies. A global Super Administrator can select an active tenant using `X-Tenant-ID`; non-super-admin users cannot override their database tenant association.
- Tenant status values are `Active`, `Inactive`, and `Suspended`.

Not yet complete:
- Assigning tenant IDs when provisioning insurer users and displaying tenant assignment in the Angular administration screen.
- A safe, explicit legacy-user migration utility.
- Applying tenant filters to claims and related operational APIs (Phase 3A-2 / Phase 3B).
- Automated tenant isolation tests and execution of the backend/frontend test suites.

Security note: tenant registry creation does not itself isolate existing claims or operational data. Do not treat this branch as production-ready multi-tenant security until the follow-on isolation phases are implemented and verified. Existing login and claims behavior have intentionally not been changed in this first registry slice.
