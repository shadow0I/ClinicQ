# ClinicQ — Queue Prediction & Crowd Management Engine

ClinicQ is a web-based clinic queue management prototype for small healthcare facilities. It helps clinic staff register patients and manage a daily token queue, while patients can check their own queue status and estimated waiting time from a lightweight web portal without installing a mobile app.

The current project is an MVP. This README separates functionality that is present in the code from features that are still planned.

## Current MVP features

### Staff workspace

- Staff sign-in using a clinic code, email address and password.
- A clinic dashboard with daily queue and consultation information.
- Patient registration with name, age, sex, mobile number and visit reason.
- Automatic daily token allocation.
- A private patient access code and QR code are generated for each registration.
- Queue actions for starting, completing, skipping and cancelling waiting tokens.
- A control to cancel all currently waiting tokens.
- Pause and resume controls for new consultations.
- A view of the day's completed consultations.

### Patient portal

- Patients can open their portal using the clinic QR code or enter their clinic code, token date, token number and private access code.
- The portal shows the patient's own token number and status, queue position, number of patients ahead and currently serving token.
- The portal shows an estimated waiting-time range when a forecast is available.
- The portal displays active clinic emergency alerts and public messages, including any stated delay.
- Patient updates are refreshed through Server-Sent Events (SSE) and periodic refreshes.

### Waiting-time prediction

- The prediction service uses completed consultation durations from the same clinic and doctor, together with the current queue state.
- A Monte Carlo simulation produces a waiting-time range using the 25th and 75th percentiles.
- Configured provisional consultation-duration bounds are used when there is not enough history.
- Active structured disruption delays are added to the forecast.
- Prediction snapshots are stored so forecasts can later be compared with observed waiting times.

A forecast is an estimate, not a guaranteed appointment time. Actual waiting time can change as consultations and clinic operations change.

### Emergency and clinic messages

- Staff can activate or update an emergency alert with an expected delay and a public message.
- Staff can publish or update a custom clinic message. A custom message can have zero additional delay.
- Staff can resolve an active emergency or remove an active custom message.
- Active alerts are shown in the staff queue timeline and in the patient portal.
- A disruption is associated with the consultation token that was active when the disruption was created, when such a token exists. The timeline uses that association to place the alert after the related token.

## Planned features — not implemented in the current MVP

The following are future improvements and should not be treated as working features in the current prototype:

- Automated phone-call reminders or IVR calls, including keypad responses.
- SMS reminders or SMS-based patient replies.
- A separate late-arrival queue and configurable automatic skip after a grace period.
- Self-service clinic registration and onboarding for new clinics.
- A complete multi-clinic administration and subscription workflow.
- Physical waiting-room crowd measurement or occupancy analytics.
- Web Push notifications and a screen wake-lock reminder.

The database and API use clinic identifiers to scope clinic records, but self-service onboarding for clinics is not currently available.

## Technology stack

- **Frontend:** React, Vite and CSS.
- **Backend:** Python, FastAPI and SQLAlchemy.
- **Database:** PostgreSQL, accessed through Psycopg.
- **Prediction:** NumPy and pandas, with a Monte Carlo simulation for the waiting-time range.
- **Patient updates:** Server-Sent Events (SSE).
- **QR generation:** `qrcode.react`.
- **Deployment used by the prototype:** Render for the frontend and backend services, with PostgreSQL hosted separately.

Tailwind CSS is not used by the current frontend.

## Project structure

```text
ClinicQ/
├── backend/
│   ├── app/
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── patient.py
│   │   │   └── staff.py
│   │   ├── db.py
│   │   ├── dependencies.py
│   │   ├── prediction.py
│   │   ├── prediction_service.py
│   │   ├── queue_recovery.py
│   │   ├── schemas.py
│   │   └── security.py
│   ├── schema.sql
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── App.css
│   │   ├── PatientPortal.jsx
│   │   └── PatientPortal.css
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## Run locally

### Requirements

- Python with support for the pinned packages in `backend/requirements.txt`.
- Node.js and npm.
- A PostgreSQL database.

### 1. Set up the database

Create an empty PostgreSQL database and a database user with permission to create and use the application's tables. Run `backend/schema.sql` against that empty database.

`backend/schema.sql` is the fresh-install schema. Do not run it over an existing database that already contains ClinicQ data. Use a reviewed, versioned migration for an existing database instead.

The schema creates the database tables; it does not provide a public default staff login. Before signing in, provision the initial clinic, doctor and staff account through a trusted one-time setup or seed process. Do not publish or reuse default credentials.

### 2. Configure the backend

In PowerShell, from the project root:

```powershell
cd backend
Copy-Item .env.example .env
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Edit `backend/.env` and set the connection details for your PostgreSQL database:

```env
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=clinicq_db
DB_USER=clinicq_app
DB_PASSWORD=your_database_password
DB_SSLMODE=require
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
COOKIE_SECURE=false
```

Use the SSL mode required by your PostgreSQL provider. For a hosted database such as Neon, use the provider's connection details and SSL requirement. Keep `.env` private; do not commit it or paste its values into public issues.

Start the backend from the `backend` directory:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API health endpoints are:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/health/db`
- `http://127.0.0.1:8000/docs` for the interactive API documentation.

### 3. Set up the frontend

Open a second terminal from the project root:

```powershell
cd frontend
npm ci
npm run dev
```

The Vite development server is configured to proxy `/api` requests to `http://127.0.0.1:8000`.

Open the staff portal at `http://127.0.0.1:5173` and the patient portal at `http://127.0.0.1:5173/patient`.

For a separate frontend and backend deployment, set `VITE_API_BASE_URL` to the backend's HTTPS base URL in the frontend deployment environment. Configure the backend's `ALLOWED_ORIGINS` with the exact frontend origin and set `COOKIE_SECURE=true` when serving over HTTPS.

## Useful development commands

From the project root:

```powershell
python -m compileall -q backend/app
```

From the `frontend` directory:

```powershell
npm run lint
npm run build
```

The repository currently does not include a comprehensive automated integration-test suite. Run the application locally and verify staff login, patient registration and access, queue actions, prediction updates, and disruption creation/update/resolution before deploying changes.

## Security and data handling

- Do not commit `.env`, `.env.local`, database passwords, session cookies or other credentials.
- Patient access codes and QR tokens are stored as hashes by the backend; the raw access details are shown at registration and should be given only to the intended patient.
- Staff and patient sessions use HTTP-only cookies.
- Patient-facing endpoints return the authenticated patient's queue information and public clinic messages, not other patients' private access credentials.
- Use synthetic demonstration records for development and demonstrations. Do not use real patient health information in an unapproved test or public environment.

## Current scope and limitations

The current staff queue view selects the first active doctor configured for the clinic. The prototype is focused on a single-clinic/single-active-doctor workflow. Clinic-scoped database records provide a foundation for future multi-clinic support, but clinic self-registration and a full multi-clinic management interface remain planned work.

## Project status

ClinicQ is an actively developed MVP. Features listed under **Current MVP features** are represented in the current application code; items under **Planned features** are not yet implemented end to end.
