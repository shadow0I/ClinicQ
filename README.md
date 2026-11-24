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

- Patients can discover active clinics that have enabled online registration, select a clinic and its active doctor, and self-register for the current queue.
- Self-registration creates a clinic-scoped token, private access code, QR token and patient session. The access code is shown once and must be saved for future sign-in.
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

### Automated voice reminders / IVR

- Patients can explicitly opt in to an automated voice reminder during self-registration. Staff registration has the same opt-in field; it must remain unchecked unless the patient has agreed. The consent timestamp is recorded, and the patient can withdraw consent from their active patient portal. Pending reminders are cancelled on withdrawal; an outbound call that has already started cannot be recalled.
- A background worker considers an opted-in waiting patient eligible when there are one to three waiting tokens ahead in the same clinic, doctor and clinic-local queue date.
- Reminders are clinic-scoped and idempotent: the database allows one automatic `THREE_AHEAD` reminder per token. A reminder is skipped if the token is no longer waiting, consent is absent, the clinic disables calling, or the queue is paused.
- The staff workspace has a Call reminders page with clinic-level enable/disable, provider readiness, recent call state, keypad response and limited manual retries.
- The Exotel adapter uses the India API endpoint by default, an App Bazaar IVR flow URL, an ExoPhone caller ID and an authenticated server-side status callback. No recording is requested.
- Set `IVR_PROVIDER=mock` to simulate a completed call locally. Mock mode never contacts a phone network. Live calling is disabled until all Exotel and public webhook settings are configured.

### Emergency and clinic messages

- Staff can activate or update an emergency alert with an expected delay and a public message.
- Staff can publish or update a custom clinic message. A custom message can have zero additional delay.
- Staff can resolve an active emergency or remove an active custom message.
- Active alerts are shown in the staff queue timeline and in the patient portal.
- A disruption is associated with the consultation token that was active when the disruption was created, when such a token exists. The timeline uses that association to place the alert after the related token.

## Planned features — not implemented in the current MVP

The following remain future improvements and should not be treated as working features:

- SMS reminders or SMS-based patient replies.
- A separate late-arrival queue and configurable automatic skip after a grace period.
- Self-service onboarding for brand-new clinics and a full subscription/billing workflow.
- Physical waiting-room crowd measurement or occupancy analytics.
- Web Push notifications and a screen wake-lock reminder.

Patient self-registration is for existing active clinics. It does not create a new clinic account or replace staff onboarding.

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
│   │   │   ├── ivr.py
│   │   │   ├── patient.py
│   │   │   └── staff.py
│   │   ├── db.py
│   │   ├── dependencies.py
│   │   ├── ivr_service.py
│   │   ├── main.py
│   │   ├── prediction.py
│   │   ├── prediction_service.py
│   │   ├── queue_recovery.py
│   │   ├── schemas.py
│   │   └── security.py
│   ├── migrations/
│   │   └── 2026-11-22_multi_clinic_registration_ivr.sql
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html
│   ├── src/
│   │   ├── App.jsx
│   │   ├── App.css
│   │   ├── PatientPortal.jsx
│   │   └── PatientPortal.css
│   ├── package.json
│   └── vite.config.js
└── README.md
```

## Enable the new features in an existing installation

1. Back up the PostgreSQL database before applying any schema change.
2. Apply `backend/migrations/2026-11-22_multi_clinic_registration_ivr.sql` once to the existing ClinicQ database. It uses additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements and creates the IVR reminder table and indexes. Do not run the fresh-install schema over an existing database.
3. Deploy the updated backend and frontend together. The patient self-registration option appears inside `/patient`; the staff Call reminders tab appears after staff login.
4. Existing tokens default to `voice_reminder_consent = FALSE`. A previous patient registration is never treated as consent.
5. Clinics are publicly discoverable only while active and `public_registration_enabled = TRUE`. This migration enables that flag by default for existing clinics; change it to `FALSE` for any clinic that should not accept public registrations.

### IVR provider configuration

For local UI/worker testing, set `IVR_PROVIDER=mock`. This simulates a successful reminder and never places a real call. A clinic owner can then enable calls from Staff → Call reminders and verify the activity history.

For live Exotel calling, use `IVR_PROVIDER=exotel` and configure these backend environment variables (never put them in Vite or the frontend):

- `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_ACCOUNT_SID`
- `EXOTEL_CALLER_ID` — the ExoPhone used as the caller ID
- `EXOTEL_IVR_URL` — the App Bazaar flow URL for the reminder greeting and keypad menu
- `EXOTEL_API_HOST=api.in.exotel.com` for an India-based account (use `api.exotel.com` only if the account is provisioned in Singapore)
- `PUBLIC_API_BASE_URL` — the public HTTPS base URL of the FastAPI service
- `IVR_WEBHOOK_SECRET` — a long random secret used to authenticate webhook requests

In Exotel, create an App Bazaar flow with a clear ClinicQ reminder greeting and a one-digit DTMF Gather step. Recommended options: press 1 to confirm that the reminder was received; press 2 to request a call from reception. Configure the Gather action URL as `https://<your-api-host>/api/ivr/webhooks/response?token=<IVR_WEBHOOK_SECRET>` and verify that Exotel posts `CallSid` and `DigitsPressed`; ClinicQ matches the call ID to its reminder record, stores the first keypad response and returns a short ExoML spoken response followed by hang-up. The endpoint also accepts an opaque `CustomField` reminder ID when your flow passes it through. The call-status callback URL supplied by ClinicQ is `/api/ivr/webhooks/exotel` with the shared secret in its query string. The Connect-with-Flow request deliberately omits `StatusCallbackEvents`: Exotel documents that event selection as unsupported when a Flow `Url` is supplied, while the default callback reports the terminal status. Test the complete IVR flow using a staff/test phone before enabling it for patients.

Exotel's Voice V1 Connect Flow uses HTTP Basic authentication and the India host `api.in.exotel.com`; its `From`, `CallerId` and `Url` parameters start the outbound call and run the App Bazaar flow. Status callbacks can include the call ID, status and original `CustomField`. See the [Connect Voice AI with Flow documentation](https://developer.exotel.com/docs/agentstream/connect-voice-ai-flow) and [call status callback reference](https://developer.exotel.com/docs/voice-v1/api-reference/call-details).

Before live calling, complete Exotel account/ExoPhone onboarding and obtain the originating access provider's required A2P/auto-dialer declaration for this use case and the exact caller-ID CLI. TRAI's 18 September 2026 summary says entities using A2P calls must pre-declare that use and the CLIs they will use; it also identifies designated commercial-call series including 1600xx/1601xx. Ask Exotel and the originating access provider which registered service/transactional category and CLI series apply to a clinic queue reminder before enabling real calls. Do not assume the API parameter `CallType=trans` by itself establishes regulatory classification. ClinicQ never requests call recording. Do not use this reminder feature for promotional calls.

The background worker polls every 15 seconds. A live call is attempted only when all provider credentials, caller ID, IVR flow URL, public HTTPS base URL and webhook secret are present, and the clinic owner has enabled voice reminders. The worker sends only an Indian mobile number in E.164 form and an opaque reminder ID; it does not send patient names or visit reasons to Exotel.

## Run locally

### Requirements

- Python with support for the pinned packages in `backend/requirements.txt`.
- Node.js and npm.
- A PostgreSQL database.

### 1. Set up the database

This implementation ZIP is an additive update for the existing ClinicQ database, not a fresh-install database bundle. It does not include the original base schema. Keep your existing ClinicQ PostgreSQL database and take a backup before applying the new migration. If you need a fresh installation, first restore the project's original base schema and trusted clinic/staff provisioning process; do not attempt to create a new installation using only the feature migration in this ZIP.

The migration creates only the additional registration and IVR fields/tables. Existing staff accounts, clinics, doctors and queue data must already exist. Do not publish or reuse default credentials.

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
npm install
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

The current staff queue view selects the first active doctor configured for the selected clinic. Patient self-registration supports multiple existing active clinics, while each staff login remains scoped to one clinic. A cross-clinic owner/admin dashboard and multi-doctor queue operations are not implemented.

## Project status

ClinicQ is an actively developed MVP. Features listed under **Current MVP features** are represented in the current application code; items under **Planned features** are not yet implemented end to end.
