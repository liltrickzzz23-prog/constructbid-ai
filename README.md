# ConstructBid AI — Government Contractor OS

An AI-powered bid scoring, proposal generation, and field reporting tool for construction companies pursuing government contracts.

**Built to be reusable** — not locked to any single company. Change the company profile and it works for any contractor.

---

## What's Inside

| Module | What It Does |
|--------|-------------|
| **Opportunity Scorer** | Paste a bid opportunity → get a fit score (0-100) and PURSUE / REVIEW / PASS recommendation |
| **Proposal Generator** | One-click draft sections: Executive Summary, Technical Approach, Past Performance, Staffing, Compliance |
| **Field Report Generator** | Type daily notes → get a formatted superintendent report |
| **Company Profile** | Edit your services, certs, NAICS, regions — scoring adjusts automatically |

---

## Step-by-Step Setup (Beginner)

### Prerequisites
You need **Python 3.10+** installed on your Mac.

Open **Terminal** (press `Cmd + Space`, type "Terminal", hit Enter).

Check if Python is installed:
```bash
python3 --version
```
If you see something like `Python 3.11.5`, you're good. If not, install it from [python.org](https://www.python.org/downloads/).

---

### Step 1: Download and open the project

Put the `constructbid-ai` folder somewhere on your Mac (like your Desktop or Documents).

Open Terminal and navigate to it:
```bash
cd ~/Desktop/constructbid-ai/backend
```
(Change the path if you put it somewhere else.)

---

### Step 2: Create a virtual environment

This keeps the project's packages separate from your system:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
You should see `(.venv)` at the start of your terminal line. That means it worked.

---

### Step 3: Install packages

```bash
pip install -r requirements.txt
```

---

### Step 4: Run the app

```bash
python -m uvicorn app.main:app --reload --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

---

### Step 5: Open it

Go to your browser and open:
```
http://127.0.0.1:8000
```

You'll see `{"app": "ConstructBid AI", "version": "1.0.0", "status": "running"}`.

The API is live. You can now use it with the React dashboard or test endpoints directly.

---

## API Endpoints

| Method | URL | What it does |
|--------|-----|-------------|
| GET | `/api/company/default` | Get company profile |
| PUT | `/api/company/default` | Update company profile |
| GET | `/api/opportunities/default` | List all opportunities with scores |
| POST | `/api/opportunities/default` | Add a new opportunity |
| GET | `/api/score/{opportunity_id}` | Score a specific opportunity |
| POST | `/api/proposal` | Generate a proposal section |
| POST | `/api/field-report` | Generate a field report |
| GET | `/api/projects/default` | List past projects |

---

## Testing the API (no frontend needed)

Open a new Terminal tab and try:

```bash
# Get all scored opportunities
curl http://127.0.0.1:8000/api/opportunities/default | python3 -m json.tool

# Add a new opportunity
curl -X POST http://127.0.0.1:8000/api/opportunities/default \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Roof Replacement – VA Medical Center",
    "agency": "Department of Veterans Affairs",
    "naics": "236220",
    "location": "VA",
    "due_date": "2026-06-15",
    "value": 1200000,
    "set_aside": "SDVOSB",
    "scope": "Complete roof replacement including tear-off, insulation, TPO membrane, flashing, and gutter replacement for a 30,000 SF medical facility."
  }' | python3 -m json.tool

# Generate a proposal section
curl -X POST http://127.0.0.1:8000/api/proposal \
  -H "Content-Type: application/json" \
  -d '{"section": "executive", "opportunity_id": "opp-1"}' | python3 -m json.tool

# Generate a field report
curl -X POST http://127.0.0.1:8000/api/field-report \
  -H "Content-Type: application/json" \
  -d '{"project_name": "Cemetery Expansion Phase II", "notes": "Poured footings for courts 7 and 8. Rebar inspection passed. Concrete arrived 2 hours late."}' | python3 -m json.tool
```

---

## "I Don't Have Past Proposals or Resumes"

That's fine. Start with what you know:

1. **Company name** — just your business name
2. **Services** — what you actually do (list them plainly)
3. **Certifications** — SDVOSB, 8a, HUBZone, OSHA, whatever you have
4. **NAICS codes** — look yours up at [naics.com](https://www.naics.com/search/)
5. **3-5 project summaries** — even if short. Name, client, dollar range, what you did
6. **Team summaries** — Name, role, years experience, key certs

Enter these in the Company Profile tab. The scoring and proposals will use them immediately.

---

## Making It Work for Other Companies

This is NOT locked to one company. To sell it:

1. Each company gets their own `company_id`
2. They fill in their own profile, projects, and certs
3. Scoring auto-adjusts to their capabilities
4. Proposals draft from their data

**Future upgrades for multi-company SaaS:**
- Add PostgreSQL instead of JSON files
- Add user login (Clerk or Supabase Auth)
- Add `tenant_id` to every record
- Add file uploads for proposals/resumes
- Add Stripe billing

---

## File Structure

```
constructbid-ai/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py          ← The entire backend
│   ├── data/                 ← JSON storage (auto-created)
│   ├── requirements.txt
│   └── .env.example
└── README.md
```

---

## Stopping the App

Press `Ctrl + C` in the terminal where it's running.

To deactivate the virtual environment:
```bash
deactivate
```

---

## Next Steps

1. ✅ Run locally and test with sample data
2. Replace sample company with a real company's info
3. Test scoring on 5 real SAM.gov opportunities
4. Add OpenAI API key for AI-powered drafting (future upgrade)
5. Deploy to a server for team access
6. Add login + multi-company support for SaaS
