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
