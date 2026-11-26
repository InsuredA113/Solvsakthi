# Solv-In Copilot Instructions

## Project architecture
- Frontend: Angular 20 + TypeScript in `solv-in/frontend`.
- Backend: Python + FastAPI in `solv-in/backend`.
- Database: MongoDB via PyMongo. Preserve the existing MongoDB configuration and collections.
- Do not introduce Firebase, Firestore, Supabase, React, Node/Express, or another database unless the project owner explicitly changes the architecture.

## Safe development workflow
- Inspect the existing implementation before editing. Prefer small, focused changes and reuse established patterns.
- Preserve working functionality, existing API contracts, and the current Solv-In branding.
- Work only on the requested phase. Do not silently begin later roadmap phases.
- Add or update tests for backend and frontend changes. Run the relevant test suites and report exact results; never claim tests passed if they were not run.
- Do not change or merge into `main` without explicit project-owner approval.

## Insurance and AI safeguards
- Solv-In supports Property and Commercial Casualty insurance claims.
- AI findings are advisory and must retain evidence, confidence, limitations, and human review.
- Do not let an LLM perform authoritative financial arithmetic. Use deterministic Python calculations.
- Do not automatically make final coverage, liability, fraud, claim approval/rejection, or settlement decisions.

## Multi-tenant security
- Tenant identity and role must be read from the authenticated MongoDB user record, never trusted from request-body or browser-supplied role claims.
- A global Super Administrator is tenantless. Insurer users must belong to one active tenant.
- Only the global Super Administrator can create tenants or assign a tenant to a user.
- Ordinary users must never override their tenant using a header, URL, request body, or client-side state.
- Every tenant-owned collection and every read, write, export, analytics, document, and background-job path must be tenant-scoped before insurer user access is enabled.
- Do not enable `tenant_access_enabled` for insurer users until the applicable tenant-isolation phase is implemented and tested.
- Tenant migration scripts must be dry-run by default, require an explicit target tenant, avoid overwriting existing assignments, and never run automatically at application startup.
- Do not treat a tenant registry or tenant ID field alone as complete tenant isolation.
