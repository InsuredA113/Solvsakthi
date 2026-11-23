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
- Added tenant assignment to Super Administrator user provisioning. Insurer users must be assigned to an existing active tenant; global Super Administrators remain tenantless.
- Added the Angular Tenant Management screen and tenant selection in user provisioning. The user list now displays the assigned insurer.
- Added an explicit legacy-user migration utility. It is dry-run by default and requires an explicit tenant ID; `--apply` is required to write changes.
- Added an access activation gate: newly provisioned or migrated insurer users have `tenant_access_enabled: false` and cannot sign in until tenant data isolation is implemented and explicitly enabled in a later phase. Existing tenantless accounts and the global Super Administrator are not changed by this gate.
- Tenant status values are `Active`, `Inactive`, and `Suspended`.

Still outstanding:
- Applying tenant filters to claims and related operational APIs (Phase 3A-2 / Phase 3B).
- Automated endpoint-level tenant isolation tests and execution of the backend/frontend test suites.
- A controlled activation process for tenant users after tenant isolation has been fully tested.

Legacy user migration (run from `solv-in/backend`):
```bash
python -m scripts.migrate_legacy_users --tenant-id <active-tenant-id>
```
The command reports the eligible user count without changing data. After verifying the target tenant and count, append `--apply` to assign the tenant. Super Administrator accounts are excluded. Migrated users remain blocked from sign-in until tenant isolation is ready.

Security note: tenant registry and user assignment do not themselves isolate existing claims or operational data. Do not enable insurer user access or treat this branch as production-ready multi-tenant security until the follow-on isolation phases are implemented and verified.


## Phase 3A-2 – Core claims tenant isolation (implementation in progress)

Implemented on `feature/phase-3a-1-tenant-foundation`:
- Added database-backed tenant checks for insurer users. A non-global account must have a tenant assignment, explicit `tenant_access_enabled: true`, and an Active tenant before sign-in or authenticated requests are accepted.
- Added a shared claim-query scope in the FastAPI application. Insurer users' claim reads and writes are constrained by the tenant ID stored on their MongoDB user record.
- Applied tenant scope to claim listing, loss-cause options, claim details, manual FNOL creation, claim editing, field updates, status changes, deletion, adjuster assignment, dashboard summaries/analytics, and claims analytics/CSV export.
- Manual and reviewed email-to-claim creation stores the authenticated insurer tenant ID.
- Scoped claim export, comparison comments, and adjuster-report entry points through the parent claim's tenant ownership.
- Added tests for tenant-scoped query construction, cross-tenant filters, inactive/missing tenant rejection, and the insurer access activation gate.
- Added an explicit dry-run legacy claims migration utility: `python -m scripts.migrate_legacy_claims --tenant-id <active-tenant-id>`. Use `--apply` only after confirming every unassigned legacy claim belongs to that insurer.

Security and rollout constraints:
- Global Super Administrator remains global and can view claims across tenants.
- Insurer users cannot see legacy claims that have no `tenant_id`; assign those claims with the dry-run migration only when ownership is known. Never assign all legacy claims to one insurer if the data belongs to multiple companies.
- Insurer accounts remain blocked by `tenant_access_enabled: false`. Do not enable accounts yet.
- Email connector automation, email-intake record isolation, policy records, damage/financial/coverage child collections, GridFS evidence metadata, audit records, and any additional claim-related routers require a complete Phase 3B tenant-isolation review before insurer access is activated.
- The implementation has been committed to the feature branch only. Backend tests and Angular build have not been executed in a live project environment; CI status must be checked before merge.
