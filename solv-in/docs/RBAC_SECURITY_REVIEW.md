# Solv-In RBAC Security Review and Organisation-Scoping Plan

Review target: `feature/solvin-rbac-foundation` (PR #7), based on the repository state reviewed on 2026-11-19.

## Approved starting decision — 2026-11-19

The user confirmed that Solv-In currently has no business/customer/claim data and approved **Option 1: create a default organisation**.

- Initial organisation display name: `Solv-In Default Organisation`.
- Create one organisation record in MongoDB Atlas with an immutable opaque `organisation_id`, stable key `solv-in-default`, active status, and creation/update audit metadata.
- Associate ordinary users created for the initial Solv-In workspace with this organisation.
- Associate all newly created claims and claim-linked records with the organisation of the authenticated non-global user.
- Keep Super Administrator as an explicitly global role. Global access must be explicit and must not be inferred from a missing `organisation_id`.
- Future insurance companies must receive separate organisation records and isolated users, claims, roles, configuration, and audit records.
- Since the user reports no existing business data, the initial rollout should create the default organisation before creating ordinary users or claims. Do not fabricate user, policy, or claim records.
- This decision does **not** authorize merging PR #7, changing production data, or deploying. Implement and test on the feature branch first.

## Executive assessment

The RBAC branch establishes a useful permission catalogue, multiple predefined roles per user, custom roles, legacy-role normalization, API permission dependencies, and Angular permission-aware navigation. The current CI result is positive for Python compilation/tests and Angular production build.

**The branch is not production-ready for multi-organisation access.** Permission checks answer whether a user may perform an action, but most database queries do not yet answer which organisation's records that user may access. A user with a valid permission can therefore potentially read or modify another organisation's claims or related records if they know an identifier.

Keep PR #7 as draft and do not merge or deploy until the critical controls below are implemented and tested.

## Findings from code review

### Critical: no persisted organisation boundary

- Current user records and claim creation do not persist a canonical `organisation_id`.
- Claims and related records are generally queried by `claim_id`, policy number, or collection-wide filters.
- User listing, custom-role listing, role assignment, and role audit queries are global.
- Email intake and automatically created provisional claims do not carry an organisation boundary.
- The application has no central helper that scopes a request to an organisation and no consistent claim-visibility check.

### Critical: claim-level visibility is not enforced

- Claim details, documents, financial estimates, damage assessments, coverage assessments, adjuster reports, comparison comments, and exports rely on a permission check plus a claim identifier.
- Adjuster assignment is stored on the claim, but access to claims is not consistently restricted to the assigned adjuster.
- Customer / Policyholder has `own_claims.*` permission identifiers, but there is no verified claim-owner identity relationship and no endpoint enforcement for that ownership.
- A permission check alone is insufficient to prevent IDOR (insecure direct object reference) access.

### High: administrative data needs tenant boundaries

- Custom roles are stored in a global collection with a globally unique `name_key`; custom role permissions are copied into user documents.
- User role updates locate a target user globally by email.
- Role audit history is global.
- Company administrators must be limited to their own organisation and to permissions within their own grant ceiling. Super Administrator remains the only global administrative role.

### High: non-claim business data also needs scope

Policies, tax rules, repair rates, email connector state/intake, analytics, and other configuration data need an explicit classification:
- organisation-owned data (must include `organisation_id`),
- globally shared reference data (read-only or governed by Super Administrator),
- user-owned or claim-owned data (must inherit the parent scope).

Do not assume every collection should be tenant-owned; define this per collection before changing queries.

### Positive compatibility checks

- The current `auth.py` branch retains the existing `pwdlib.PasswordHash.recommended()` password hashing and verification implementation.
- The current `user_models.py` change is additive to the existing login and user-create models; it retains the original fields while adding multi-role support and legacy role labels.
- The existing legacy `role` field is still supported while `roles` is introduced.
- The initial bootstrap Super Administrator seed currently creates a user without an organisation identifier; this must be addressed in the tenant migration design.

## Target access model

### Organisation model

Create an `organisations` collection. Each organisation should have:
- immutable `organisation_id` (UUID or generated opaque ID),
- display name and stable key,
- active/suspended status,
- created/updated timestamps and actor audit fields,
- optional organisation profile/configuration fields.

Do not use a user-supplied organisation name as an authorization key.

### User model

Every non-global user should carry:
- `organisation_id`,
- `roles` (multiple predefined roles),
- `custom_role_ids` (references to organisation-scoped custom roles),
- legacy `role` during migration,
- active/deactivated state and timestamps.

Super Administrator is global and may access organisations through explicit administrative workflows. Do not make every ordinary user global by leaving `organisation_id` empty.

### Claim model

Every claim should carry:
- `organisation_id`,
- `created_by` and a normalized `claimant_email`/verified owner identity where appropriate,
- `assigned_adjuster_email` and assignment history,
- optional explicit claim participants/claim-level grants for surveyors or external reviewers.

All claim-linked records (documents, damage assessments, financial estimates, coverage assessments, adjuster reports, comments, exports, email intake links) must inherit and validate the same organisation and parent claim scope.

### Authorization decision order

1. Authenticate the user and reload current active account state from MongoDB.
2. Resolve global Super Administrator status explicitly.
3. For non-global users, require a valid active `organisation_id`.
4. Check organisation membership and account status.
5. Check the required permission.
6. For claim operations, load the claim using both `claim_id` and the authorised organisation scope.
7. Apply role-specific claim visibility:
   - Company Administrator / Claims Manager: claims in their organisation, subject to permission.
   - Property / Casualty Adjuster: claims assigned to them, unless separately granted broader organisation visibility.
   - Surveyor / external reviewer: only explicitly assigned/participating claims.
   - Customer / Policyholder: only claims linked to their verified identity.
   - Auditor / Actuarial / Finance / Reinsurance / Compliance: organisation-scoped records according to their permissions and approved business rules.
8. Record sensitive access and mutation events where audit requirements apply.

A global Super Administrator bypass must be explicit and narrowly implemented; do not implement it as a generic empty-filter fallback.

## Implementation sequence

1. **Complete review and inventory**: map every API route and MongoDB collection to organisation-owned, claim-owned, global-reference, or system data.
2. **Default organisation bootstrap**: create or safely retrieve `Solv-In Default Organisation` before seeding ordinary users; use a unique stable key and immutable ID.
3. **Add shared scope helpers**: organisation resolution, claim access, ownership/assignment checks, and safe 404 behavior for out-of-scope records.
4. **Scope all endpoints and related collections**: including email intake, GridFS metadata, exports, analytics, policies, tax rules, repair rates, custom roles, user management, and audit logs.
5. **Update Angular**: show organisation context and only permitted records/actions; treat the frontend as usability, never as the security boundary.
6. **Test**: two-organisation isolation, cross-tenant ID guessing, assigned/unassigned adjuster, surveyor grant, customer ownership, deactivated user, role escalation, custom-role ceiling, global administrator workflows, and legacy migration.
7. **Migration dry run and operational review**: backup, dry-run report, rollback plan, production migration approval.
8. **Final gate**: security review, CI, PR review, explicit user approval, then merge and deploy.

## Non-negotiable release gates

- No cross-organisation read, write, export, document download, or analytics leakage.
- No claim access based only on possession of a Claim ID.
- No user can grant permissions or roles above their authority.
- No account without a valid organisation can access tenant data (except explicitly authorised global administration).
- Role changes and deactivation take effect on the next request; stale JWT role claims must not be trusted as the source of current permissions.
- Migration is reversible and preserves existing password hashes.
- Production remains unchanged until explicit approval.


## Organisation-isolation implementation status (2026-11-19)

Implemented on `feature/solvin-rbac-foundation`:

- Added reusable organisation and claim-scope helpers in `app/organisations.py`.
- Backend startup creates or retrieves the default organisation before seeding the configured administrator.
- The configured bootstrap administrator is explicitly marked `is_global_admin=true`; a Super Administrator role label alone is not a global-access bypass.
- Authentication rejects non-global users without an active organisation membership.
- New users and manually created claims receive an organisation ID; email-created claims inherit the organisation from their intake record.
- Claim reads and mutations in the main API now include the authenticated organisation scope, including claim details, edits, status changes, deletion, evidence, damage assessments, estimates and coverage assessments.
- Claim register, dashboard metrics, dashboard analytics, claims analytics and CSV reporting apply organisation filters.
- Policies, tax rules, repair rates, custom roles, user administration and role audit records are organisation-scoped for tenant administrators.
- Adjuster reports, comparison comments and claim exports verify the parent claim's organisation; export audit records and related policy reads are tied to that claim.
- Email connector state is associated with an organisation; webhook ingestion requires an organisation-bound connector state and stamps generated intake/claims with that organisation.
- Added unit tests for organisation filtering, explicit global-admin bypass, assigned-adjuster visibility, Claims Manager organisation-wide visibility and verified customer-link filtering.

## Remaining release blockers

This work is a foundation, not yet a production-ready multi-tenant security certification:

1. Review and test every MongoDB query and every child-record / GridFS operation, including all connector retry and duplicate paths.
2. Adjuster assigned-claim visibility is now implemented in claim lookups, registers and analytics; newly created claims by adjusters are assigned to the creator. Still implement and test the surveyor assignment workflow and customer/policyholder own-claim APIs using an explicitly verified customer-to-claim link (do not infer ownership from an unverified contact email).
3. Decide and implement multi-organisation mailbox configuration; current email connector settings are environment-configured per provider/mailbox and bind that mailbox to one organisation.
4. Add two-organisation integration tests for read, write, update, delete, export, download, analytics, role administration and IDOR attempts.
5. Review legacy-record migration. The user has stated the current database has no business/claim data, but any deployed database must still be inspected and backed up before migration.
6. Run Python compileall, backend pytest and Angular production build against the latest branch head; confirm CI results before merge.
7. Keep PR #7 in draft. No merge, production database migration, production deployment or live account changes are authorised by this implementation.

Do not remove these blockers until evidence from tests and review is recorded.
