# QA Platform

Web application testing platform — manual and automated QA for any website,
with multi-page crawling and real WCAG accessibility audits.

---

## Highlights

- **Automated full-site scan** — BFS crawler walks every reachable page,
  reports broken links, JS errors, missing alt text, SEO gaps, mobile issues,
  security headers, and WCAG violations (powered by axe-core).
- **Manual test runs with auto-trigger** — opens a real Chrome window for you
  to log in by hand (OTP / CAPTCHA / OAuth — anything). Once you leave the
  login page, the system takes over and crawls the authenticated app for you.
- **SPA-aware navigation** — uses link clicks and `history.pushState` (with
  `page.goto` fallback) so React/Vue auth context survives between pages.
  No more crawler getting bounced back to `/login` on every URL.
- **Manual login session capture** — Playwright `storage_state` plus
  `sessionStorage` replay handles SPAs that keep their auth flag outside
  cookies.
- **Async runner** — long crawls run on a thread pool and report live
  progress; the Flask request returns immediately with a `job_id`.

---

## Tech stack

**Backend** — Flask 3, Flask-JWT-Extended, Flask-MySQLdb, Flask-Limiter,
Playwright, axe-core, Pillow, reportlab.

**Frontend** — React 19, Vite, Tailwind CSS, Framer Motion, Three.js
(`@react-three/fiber`), Recharts, lucide-react, axios.

**Database** — MySQL 8 (utf8mb4).

**Tests** — pytest (31 tests covering health-score math, run helpers, session
capture, jobs, app smoke, and the manual-run state machine).

---

## Project structure

```
qa-platform/
├── backend/
│   ├── app.py                # Flask entry point
│   ├── config.py             # Loads .env
│   ├── logging_config.py     # Rotating file logs + UTF-8 console
│   ├── extensions.py         # Flask-Limiter
│   ├── init_db.py            # Creates schema
│   ├── migrate_db.py         # Lightweight migrations
│   ├── requirements.txt
│   ├── modules/
│   │   ├── auth/             # JWT login/register
│   │   ├── projects/         # CRUD + manual-login session capture
│   │   ├── testcases/        # CRUD
│   │   ├── runner/           # Automated + manual test execution
│   │   │   ├── routes.py     # /run, /run/async, /job, /manual/*
│   │   │   ├── manual_run.py # Headed-browser worker
│   │   │   ├── jobs.py       # Background job manager
│   │   │   └── axe.min.js    # Bundled WCAG engine
│   │   ├── bugs/             # Bug tracker
│   │   ├── reports/          # PDF/CSV exports
│   │   └── otp/              # In-run OTP prompts
│   └── tests/                # pytest suite
└── frontend/
    ├── src/
    │   ├── pages/            # Dashboard, Projects, TestCases, BugTracker,
    │   │                     # Reports, Login, Register
    │   ├── components/       # Sidebar, AmbientBackground, three/, ...
    │   └── api/              # axios wrappers per module
    ├── tailwind.config.js
    └── vite.config.js
```

---

## Setup

### Prerequisites

- Python 3.11+ (3.14 works)
- Node.js 20+
- MySQL 8 running locally (default port 3306)
- Real installed Chrome (recommended — crawler prefers it over bundled
  Chromium for lower automation-detection)

### 1. Database

```powershell
# Start MySQL, then:
cd backend
python init_db.py        # creates qa_platform database + tables
python migrate_db.py     # applies any later additive migrations
```

### 2. Backend

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium

# Configure secrets — never commit this file
copy .env.example .env
# Edit .env: DB_PASSWORD, JWT_SECRET_KEY (a long random string)

python app.py            # http://127.0.0.1:5000
```

### 3. Frontend

```powershell
cd frontend
npm install
npm run dev              # http://localhost:5173
```

Open the dev URL, register an account, and create your first project.

---

## Running a test

### Automated full-site scan

1. **Projects → New Project** → enter the site URL.
2. (Optional, for login-gated sites) **Open & Login** → log in in the popped
   browser → **I am logged in**. This captures cookies + `sessionStorage` to
   `backend/static/uploads/sessions/<project_id>.json`. **The token only
   lives ~15–30 min for sites like DigiELV**; capture and run back-to-back.
3. **Test Cases → New** → pick the project → mark it **Automated** → set
   *Max pages*.
4. **Run Full Site Test** — the modal shows live progress
   (`Testing 5/25 — /dashboard`). Done shows a per-page breakdown and an
   issue list labelled by page URL and severity.

### Manual run (with auto-crawl)

1. Make the test case **Manual**.
2. **Run Manual Test** → a real Chrome window opens at the project URL.
3. Log in by hand (any auth flow — OTP, CAPTCHA, OAuth, whatever).
4. Once you leave the login page and stay for ~5 seconds, the system auto-
   crawls the authenticated app from your current page.
5. **Mark Pass** / **Mark Fail** when you're done.

---

## Tests

```powershell
cd backend
.\venv\Scripts\Activate.ps1
pytest -q
```

Covers: health-score math, finding aggregation, session capture URL helpers,
JobManager state machine, app routing + rate limiting, manual-run state
machine end-to-end against a public page.

---

## Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in:

| Key | Purpose |
|---|---|
| `DB_HOST` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | MySQL connection |
| `JWT_SECRET_KEY` | Token signing key — generate a long random value |

---

## License

Private. Not for redistribution.
