# QA Platform

QA Platform is a full-stack application for managing software quality workflows. It brings projects, test cases, automated test runs, bug tracking, reports, AI assistance, and administration into one interface.

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Backend setup](#backend-setup)
- [Frontend setup](#frontend-setup)
- [Configuration](#configuration)
- [Application pages](#application-pages)
- [API overview](#api-overview)
- [Database](#database)
- [Testing and quality checks](#testing-and-quality-checks)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Security](#security)

## Features

- Project and test-case management
- Manual and automated test execution
- Browser-based test evidence and screenshots
- Bug tracking with AI-assisted analysis
- Test reports and PDF generation
- JWT authentication and admin controls
- AI assistant powered by Google Gemini

Automated audits also generate safe invalid values for supported form controls
(empty required fields, invalid email/URL/phone values, patterns, and numeric or
length limits). The runner restores the original values and never submits
destructive actions automatically. It also verifies important pages exposed by
the site, including privacy, terms, security, contact, and support links, and
reports unavailable or empty destinations.

## How it works

1. A user registers and signs in to receive a JWT access token.
2. The user creates a project for the application they want to test.
3. Test cases are created manually or suggested by the AI module.
4. Tests can be completed manually or executed through the Playwright-based runner.
5. Automated runs crawl pages, collect evidence, and store run results.
6. Detected issues can be converted into tracked bugs and analyzed by AI.
7. Dashboard and report views summarize project health, pass rates, trends, and recent runs.

During an automated run, the Playwright runner crawls the configured pages,
audits forms without making destructive submissions, checks security headers and
mixed content, and verifies important-page links. Findings include the page or
field involved, severity, evidence, and a suggested fix.

Projects that require authentication can save a browser session after an interactive login. Session data may contain cookies and local storage, so it must be treated as sensitive runtime data.

## Tech stack

- **Frontend:** React 19, Vite, Tailwind CSS, Axios, Recharts, Three.js
- **Backend:** Python, Flask, Flask-JWT-Extended
- **Database:** MySQL
- **Testing:** Pytest, Vitest, Testing Library, Playwright

## Architecture

```text
Browser
   |
   | React pages and Axios requests
   v
Vite frontend (localhost:5173)
   |
   | JSON over /api/*
   v
Flask API (127.0.0.1:5000)
   |              |                |
   |              |                `-- Google Gemini API
   |              `-- Playwright browser runner
   `-- MySQL database
```

The Flask application registers each feature as a blueprint under `/api`. JWT authentication protects user-specific operations. The runner can execute work asynchronously through an in-process job manager with two workers. Generated screenshots and uploads are served from the backend's `static` directory.

## Prerequisites

Install these before starting:

- Python 3
- Node.js and npm
- MySQL Server

## Quick start

After installing the prerequisites, use two terminals.

Terminal 1:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
# Edit .env before continuing.
python init_db.py
python app.py
```

Terminal 2:

```powershell
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173`.

## Backend setup

Open a terminal in the project root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

Edit `backend/.env` and provide your MySQL credentials, JWT secret, admin email, and Gemini API key:

```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your-password
DB_NAME=qa_platform
JWT_SECRET_KEY=your-long-random-secret
GEMINI_API_KEY=your-gemini-api-key
ADMIN_EMAIL=admin@example.com
```

Make sure MySQL is running, then initialize the database and start Flask:

```powershell
python init_db.py
python app.py
```

The backend runs at `http://127.0.0.1:5000`.

### Initial database versus an existing database

- Run `python init_db.py` for a new installation. It creates the configured database and missing tables without deleting existing data.
- Run `python migrate_db.py` when updating the schema of an installation that already contains data.
- Keep `backend/schema.sql` as the source of truth for fresh database creation.

## Frontend setup

Open another terminal in the project root:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

The frontend currently sends API requests to `http://127.0.0.1:5000/api`. Flask allows development requests from ports `5173`, `5174`, and `5175` on both `localhost` and `127.0.0.1`.

## Configuration

Configuration is loaded from `backend/.env` through `python-dotenv`.

| Variable | Required | Purpose | Example |
| --- | --- | --- | --- |
| `DB_HOST` | Yes | MySQL server host | `localhost` |
| `DB_USER` | Yes | MySQL username | `root` |
| `DB_PASSWORD` | Yes | MySQL password | `change-me` |
| `DB_NAME` | Yes | Database name | `qa_platform` |
| `JWT_SECRET_KEY` | Yes | Signs authentication tokens | A long random value |
| `GEMINI_API_KEY` | For AI features | Authenticates Gemini requests | `your-gemini-api-key` |
| `ADMIN_EMAIL` | For admin access | Registration email assigned admin rights | `admin@example.com` |
| `FLASK_DEBUG` | Optional | Development debug setting | `0` |

Generate a JWT secret with Python:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The email in `ADMIN_EMAIL` is matched when an account registers. The backend does not accept an administrator role from the public registration request.

## Application pages

| Route | Purpose |
| --- | --- |
| `/login` | User authentication |
| `/register` | Account registration |
| `/dashboard` | QA activity and project overview |
| `/projects` | Project creation, editing, and authenticated browser sessions |
| `/testcases` | Test-case management and manual or automated execution |
| `/bugs` | Bug tracking, evidence, status updates, and AI analysis |
| `/ai-assistant` | AI chat and testing assistance |
| `/reports` | Health trends, pass rates, summaries, and PDF export |
| `/admin` | User and system administration |

## API overview

The base URL for local development is `http://127.0.0.1:5000/api`.

### Automation issue intelligence

Automated findings are converted into readable issue records with reproduction
steps, expected versus actual behavior, evidence, impact score, confidence,
stable fingerprints, occurrence counts, root-cause hints, and suggested fixes.
Repeated findings are grouped instead of creating duplicate bugs, and recent
reports show new versus fixed issues between runs.

Automated workflows support executable assertions, not only clicks:

```text
Expect text Welcome
Expect element Submit to be visible
Expect 1 elements matching .result
Expect Submit attribute disabled to equal false
Expect local storage token to exist
Expect url contains /dashboard
```

Recent runs also record inconsistent Pass/Fail outcomes as a possible flaky
test when at least four comparable runs alternate between outcomes. Manual
runs keep step-level notes, screenshots, expected-result decisions, and
tester-reported issues so human observations and machine evidence stay linked.

After pulling a version that adds issue intelligence fields to an existing
database, run the additive migration:

```powershell
cd backend
.\.venv\Scripts\python.exe migrate_db.py
```

Authenticated requests send the JWT in the `Authorization` header:

```http
Authorization: Bearer <access-token>
```

| Prefix | Main responsibilities |
| --- | --- |
| `/api/auth` | Registration, login, profile, profile picture, and login history |
| `/api/projects` | Project CRUD, statistics, and saved browser login sessions |
| `/api/testcases` | Test-case CRUD, status changes, and statistics |
| `/api/runner` | Automated runs, asynchronous jobs, manual runs, crawl status, and evidence |
| `/api/otp` | OTP status, submission, and cancellation during test runs |
| `/api/bugs` | Bug CRUD, statistics, status changes, and bug creation from test runs |
| `/api/reports` | Summaries, trends, bug breakdowns, pass rates, recent runs, and PDF export |
| `/api/ai` | Bug analysis, test suggestions, fix suggestions, chat, and health checks |
| `/api/admin` | Platform overview and user administration |

Example AI service health request:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/ai/health
```

## Database

The MySQL schema contains these tables:

| Table | Purpose |
| --- | --- |
| `users` | Accounts, roles, profile data, and active status |
| `login_history` | Authentication activity |
| `projects` | Applications and testing targets |
| `test_cases` | Manual and automated test definitions |
| `test_runs` | Execution state, results, and run metadata |
| `crawled_pages` | Pages and evidence discovered during runs |
| `otp_sessions` | OTP interactions required during execution |
| `ai_suggestions` | Generated AI recommendations |
| `bugs` | Tracked defects, evidence, severity, and status |

The configured database uses the `utf8mb4` character set. Uploaded files are limited to 200 MB by the Flask configuration.

## Testing and quality checks

Run backend tests:

```powershell
cd backend
pytest
```

Run frontend tests, linting, and a production build:

```powershell
cd frontend
npm test
npm run lint
npm run build
```

### What the checks cover

- `pytest` runs backend tests configured by `backend/pytest.ini`.
- `npm test` runs Vitest once using the `jsdom` environment.
- `npm run test:watch` keeps Vitest running during frontend development.
- `npm run lint` checks frontend JavaScript and React code with ESLint.
- `npm run build` verifies that Vite can create a production frontend bundle.
- Playwright provides the real browser used by automated QA runs.

Run the complete frontend check sequence before submitting a frontend change:

```powershell
cd frontend
npm test
npm run lint
npm run build
```

## Project structure

```text
qa-platform/
|-- backend/            Flask API, database schema, and backend tests
|   |-- app.py          Flask application and blueprint registration
|   |-- config.py       Environment-backed application configuration
|   |-- init_db.py      Idempotent database initialization
|   |-- migrate_db.py   Existing database migrations
|   |-- schema.sql      Fresh-install MySQL schema
|   |-- modules/        Auth, projects, test cases, runner, OTP, bugs, reports, AI, and admin
|   |-- tests/          Backend test suite
|   `-- static/         Uploaded files and generated test evidence
|-- frontend/           React application and frontend tests
|   `-- src/
|       |-- api/        API client modules
|       |-- components/ Shared interface components
|       `-- pages/      Application pages
`-- docs/               Project documentation
```

## Troubleshooting

### Database connection error

Confirm that MySQL is running and that `DB_HOST`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` in `backend/.env` are correct. The API returns HTTP `503` when it cannot reach MySQL.

### PowerShell blocks virtual-environment activation

Use this command for the current terminal, then activate the environment again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Playwright cannot launch a browser

Install the required browser binary from the activated backend environment:

```powershell
playwright install chromium
```

### Frontend cannot reach the API

- Confirm Flask is running on `http://127.0.0.1:5000`.
- Confirm Vite is running on an allowed development port: `5173`, `5174`, or `5175`.
- Check the browser developer console and the Flask terminal for the actual error.

### Port already in use

Vite automatically tries another available port. If the Flask port is occupied, stop the application using port `5000` or update both the backend port and frontend API URLs together.

### AI features are unavailable

Confirm that `GEMINI_API_KEY` is present in `backend/.env`, restart Flask, and request `/api/ai/health` again.

## Security

Never commit `.env` files, API keys, database passwords, JWT secrets, browser sessions, or generated test evidence. The included `.gitignore` excludes these files.

Additional development security notes:

- Use a unique, randomly generated JWT secret outside local development.
- Do not expose the Flask development server directly to the internet.
- Restrict CORS to trusted production origins before deployment.
- Replace development debug settings before deployment.
- Treat uploaded evidence, screenshots, cookies, and saved browser sessions as private data.
- Review authentication token lifetime and revocation requirements before production use.
