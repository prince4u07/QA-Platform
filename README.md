# QA Platform

QA Platform is a full-stack application for managing software quality workflows. It brings projects, test cases, automated test runs, bug tracking, reports, AI assistance, and administration into one interface.

## Features

- Project and test-case management
- Manual and automated test execution
- Browser-based test evidence and screenshots
- Bug tracking with AI-assisted analysis
- Test reports and PDF generation
- JWT authentication and admin controls
- AI assistant powered by Anthropic Claude

## Tech stack

- **Frontend:** React 19, Vite, Tailwind CSS, Axios, Recharts, Three.js
- **Backend:** Python, Flask, Flask-JWT-Extended
- **Database:** MySQL
- **Testing:** Pytest, Vitest, Testing Library, Playwright

## Prerequisites

Install these before starting:

- Python 3
- Node.js and npm
- MySQL Server

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

Edit `backend/.env` and provide your MySQL credentials, JWT secret, admin email, and Anthropic API key:

```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your-password
DB_NAME=qa_platform
JWT_SECRET_KEY=your-long-random-secret
ANTHROPIC_API_KEY=your-anthropic-api-key
ADMIN_EMAIL=admin@example.com
```

Make sure MySQL is running, then initialize the database and start Flask:

```powershell
python init_db.py
python app.py
```

The backend runs at `http://127.0.0.1:5000`.

## Frontend setup

Open another terminal in the project root:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

## Tests and checks

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

## Project structure

```text
qa-platform/
|-- backend/            Flask API, database schema, and backend tests
|   |-- modules/        Auth, projects, test cases, runner, bugs, reports, AI, and admin
|   `-- static/         Uploaded files and generated test evidence
|-- frontend/           React application and frontend tests
|   `-- src/
|       |-- api/        API client modules
|       |-- components/ Shared interface components
|       `-- pages/      Application pages
`-- docs/               Project documentation
```

## Security

Never commit `.env` files, API keys, database passwords, JWT secrets, browser sessions, or generated test evidence. The included `.gitignore` excludes these files.

