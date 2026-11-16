# Solv-In

AI-assisted Property & Casualty (P&C) insurance claims platform.

## Initial stack
- Angular + TypeScript frontend
- Python + FastAPI backend
- MongoDB database
- Gemini API integration planned for later phases

This starter contains Phase 1 foundation and a Phase 2 dashboard UI. Dashboard figures and recent claims shown in the UI are clearly labelled demo data. Claims API operations use MongoDB and do not silently pretend that data was saved when MongoDB is unavailable.

## Prerequisites
- Node.js LTS and npm
- Python 3.11+
- MongoDB Community Server running locally
- Git

## 1. Configure MongoDB
Start MongoDB Community Server. The default local connection is:
`mongodb://localhost:27017`

## 2. Run the backend (Windows PowerShell)
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```
API docs: http://127.0.0.1:8000/docs

If PowerShell blocks activation, run:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

## 3. Run the frontend (new terminal)
```powershell
cd frontend
npm install
npm start
```
Open http://localhost:4200

## 4. GitHub
From the repository root:
```powershell
git add .
git commit -m "Create Solv-In foundation and dashboard"
git push
```

## Important
- Never commit `.env` files or API keys.
- This is a development starter, not a production-ready claims system.
- Do not use real customer, policy, medical, financial or claim documents in development.
- AI outputs must remain preliminary and human-reviewed.
- No automatic claim approval, rejection, settlement, liability, coverage or fraud determination.
- The actuarial calculation module should use deterministic Python Decimal calculations and actual policy wording, not LLM arithmetic.
