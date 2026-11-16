# Solv-In

AI-assisted Property & Casualty (P&C) insurance claims platform.

## Current development stage

Phase 1 foundation and dashboard are in place. Phase 2 adds a working claims register and First Notice of Loss (FNOL) workflow.

### Stack
- Angular + TypeScript frontend
- Python + FastAPI backend
- MongoDB database
- Gemini integration planned for later phases

## Phase 2 functionality
- Create Property and Casualty claims through the FNOL form.
- Generate sequential claim IDs in the format CLM-YYYY-000001.
- Save claim records in MongoDB.
- Search claims by claim ID, customer or policy number.
- Filter claims by type and status.
- Update claim workflow status.
- Show dashboard counts from MongoDB when connected.

## Run locally

Prerequisites: Node.js LTS, npm, Python 3.11+, MongoDB Community Server.

### Backend (Windows PowerShell)
```powershell
cd solv-in/backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

API documentation: http://127.0.0.1:8000/docs

### Frontend (new terminal)
```powershell
cd solv-in/frontend
npm install
npm start
```

Open http://localhost:4200.

## Important safeguards
- Use fictional data only during development.
- AI assessments are preliminary and require authorised human review.
- No automatic claim approval, rejection or settlement.
- No final coverage, liability or fraud decisions.
- Do not invent missing claim facts or policy terms.
- Financial calculations must use deterministic Python Decimal logic and verified policy terms.
- Never commit .env files or API keys.
- This is a development build, not production-ready. Authentication, role-based access control, tenant isolation, secure document storage and audit logging remain future requirements.


## Phase 3 – Authentication and role permissions

- JWT bearer-token login with a short configurable token lifetime.
- Password hashing using Argon2 through pwdlib.
- Active-user lookup on every authenticated API request.
- Protected claim and dashboard APIs with role-based permissions.
- Super Administrator-only user creation and user listing until insurer tenant isolation is implemented.
- Angular sign-in/sign-out and session-scoped token storage.

### Configure the first administrator

1. Copy `.env.example` to `.env` in `solv-in/backend`.
2. Set `JWT_SECRET_KEY` to a randomly generated secret of at least 32 characters. For example, use Python: `py -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Set a unique `INITIAL_ADMIN_EMAIL` and a strong `INITIAL_ADMIN_PASSWORD` (at least 12 characters, with letters and numbers).
4. Start MongoDB, then start FastAPI. On first startup, the configured account is created as the Super Administrator if that email does not already exist.
5. Sign in through the Angular application with those credentials.

Do not commit `.env`, credentials, JWT secrets or real customer data.

### Current role boundaries

The API recognises: Super Administrator, Insurance Company Administrator, Claims Manager, Property Adjuster, Casualty Adjuster, Surveyor, Actuarial Analyst, Approver, Auditor and Customer.

The current implementation grants claim read access to operational roles, claim creation to administrators/managers/adjusters, and status updates to administrators/managers/adjusters/approvers. User provisioning is restricted to the global Super Administrator. Customer self-service, claim ownership scoping, insurer/tenant isolation, adjuster claim-type scoping, password reset, MFA, refresh-token rotation and audit logging are not implemented yet. Do not use this build for production or real customer information.
