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
