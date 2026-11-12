
-- ClinicQ fresh-install schema.
-- Do not run this file against an existing database; use versioned migrations
-- for existing deployments so data and current constraints are preserved.

BEGIN;

-- 1. CLINICS
CREATE TABLE public.clinics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    clinic_code TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    default_min_minutes INTEGER NOT NULL DEFAULT 5,
    default_max_minutes INTEGER NOT NULL DEFAULT 20,
    walk_back_buffer_minutes INTEGER NOT NULL DEFAULT 5,
    queue_paused BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (default_min_minutes > 0),
    CHECK (default_max_minutes >= default_min_minutes),
    CHECK (walk_back_buffer_minutes >= 0),
    CHECK (length(clinic_code) BETWEEN 4 AND 20)
);

CREATE UNIQUE INDEX uq_clinics_code
ON public.clinics (LOWER(clinic_code));


-- 2. DOCTORS
CREATE TABLE public.doctors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES public.clinics(id),
    display_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (clinic_id, id)
);


-- 3. STAFF USERS
CREATE TABLE public.staff_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES public.clinics(id),
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL
        CHECK (role IN ('OWNER', 'RECEPTIONIST')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (clinic_id, id)
);

CREATE UNIQUE INDEX uq_staff_email_per_clinic
ON public.staff_users (clinic_id, LOWER(email));


-- 4. STAFF SESSIONS
CREATE TABLE public.staff_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    staff_user_id UUID NOT NULL,
    session_token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,

    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at),

    FOREIGN KEY (clinic_id, staff_user_id)
        REFERENCES public.staff_users(clinic_id, id)
);


-- 5. TOKEN STATUS
CREATE TYPE public.token_status AS ENUM (
    'WAITING',
    'IN_CONSULTATION',
    'COMPLETED',
    'SKIPPED',
    'CANCELLED'
);


-- 6. DAILY TOKEN COUNTERS
CREATE TABLE public.clinic_day_counters (
    clinic_id UUID NOT NULL REFERENCES public.clinics(id),
    token_date DATE NOT NULL,
    last_issued_number INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (clinic_id, token_date),
    CHECK (last_issued_number >= 0)
);


-- 7. QUEUE TOKENS
CREATE TABLE public.queue_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    doctor_id UUID NOT NULL,
    token_date DATE NOT NULL,
    token_number INTEGER NOT NULL,
    access_code_hash TEXT NOT NULL,
    qr_access_hash TEXT NOT NULL UNIQUE,
    patient_name TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age BETWEEN 0 AND 120),
    sex TEXT NOT NULL CHECK (
        sex IN ('Male', 'Female', 'Other', 'Prefer not to say')
    ),
    mobile_number TEXT NOT NULL CHECK (
        mobile_number ~ '^[6-9][0-9]{9}$'
    ),
    visit_reason TEXT NOT NULL CHECK (
        visit_reason IN (
            'General check-up',
            'Follow-up check-up',
            'Other'
        )
    ),
    status public.token_status NOT NULL DEFAULT 'WAITING',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (token_number > 0),

    UNIQUE (clinic_id, token_date, token_number),
    UNIQUE (clinic_id, id),

    FOREIGN KEY (clinic_id, doctor_id)
        REFERENCES public.doctors(clinic_id, id)
);

CREATE INDEX idx_tokens_active_queue
ON public.queue_tokens (
    clinic_id,
    doctor_id,
    token_date,
    status,
    token_number
);


-- 8. PATIENT SESSIONS
CREATE TABLE public.patient_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    token_id UUID NOT NULL,
    session_token_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,

    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at),

    FOREIGN KEY (clinic_id, token_id)
        REFERENCES public.queue_tokens(clinic_id, id)
);

CREATE INDEX idx_patient_sessions_token
ON public.patient_sessions (clinic_id, token_id);


-- 9. CONSULTATIONS
CREATE TABLE public.consultations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    token_id UUID NOT NULL,
    doctor_id UUID NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    outcome TEXT CHECK (
        outcome IS NULL OR outcome IN ('COMPLETED', 'INTERRUPTED')
    ),

    CHECK (ended_at IS NULL OR ended_at >= started_at),

    UNIQUE (token_id),

    FOREIGN KEY (clinic_id, token_id)
        REFERENCES public.queue_tokens(clinic_id, id),

    FOREIGN KEY (clinic_id, doctor_id)
        REFERENCES public.doctors(clinic_id, id)
);

-- One doctor cannot have two active consultations.
CREATE UNIQUE INDEX uq_active_consultation_per_doctor
ON public.consultations (clinic_id, doctor_id)
WHERE ended_at IS NULL;

CREATE INDEX idx_consultation_history
ON public.consultations (clinic_id, doctor_id, ended_at);


-- 10. QUEUE EVENTS
CREATE TABLE public.queue_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES public.clinics(id),
    token_id UUID,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'TOKEN_CREATED',
            'CONSULTATION_STARTED',
            'CONSULTATION_COMPLETED',
            'TOKEN_SKIPPED',
            'TOKEN_CANCELLED',
            'CONSULTATION_INTERRUPTED',
            'QUEUE_PAUSED',
            'QUEUE_RESUMED',
            'DELAY_CREATED',
            'DELAY_UPDATED',
            'DELAY_RESOLVED',
            'CUSTOM_MESSAGE_CREATED',
            'CUSTOM_MESSAGE_UPDATED',
            'CUSTOM_MESSAGE_RESOLVED'
        )
    ),
    public_message VARCHAR(240),
    staff_user_id UUID,
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (clinic_id, token_id)
        REFERENCES public.queue_tokens(clinic_id, id),

    FOREIGN KEY (clinic_id, staff_user_id)
        REFERENCES public.staff_users(clinic_id, id)
);

CREATE INDEX idx_queue_events_clinic_time
ON public.queue_events (clinic_id, event_time);


-- 11. QUEUE DISRUPTIONS
CREATE TABLE public.queue_disruptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL REFERENCES public.clinics(id),
    after_token_id UUID,
    FOREIGN KEY (clinic_id, after_token_id)
    REFERENCES public.queue_tokens(clinic_id, id),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('EMERGENCY', 'DELAY', 'CUSTOM_MESSAGE')
    ),
    expected_delay_minutes INTEGER NOT NULL
        CHECK (expected_delay_minutes BETWEEN 0 AND 240),
    public_message VARCHAR(240) NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'RESOLVED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    created_by UUID,

    CHECK (
        (status = 'ACTIVE' AND resolved_at IS NULL)
        OR
        (status = 'RESOLVED' AND resolved_at IS NOT NULL)
    ),

    FOREIGN KEY (clinic_id, created_by)
        REFERENCES public.staff_users(clinic_id, id)
);

CREATE INDEX idx_active_disruptions
ON public.queue_disruptions (clinic_id, status);



-- 12. WAITING ROOM PRESENCE
CREATE TABLE public.presence_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    token_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'ENTERED_WAITING_ROOM',
            'LEFT_WAITING_ROOM',
            'RETURNED_TO_WAITING_ROOM'
        )
    ),
    staff_user_id UUID,
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (clinic_id, token_id)
        REFERENCES public.queue_tokens(clinic_id, id),

    FOREIGN KEY (clinic_id, staff_user_id)
        REFERENCES public.staff_users(clinic_id, id)
);

CREATE INDEX idx_presence_token_time
ON public.presence_events (clinic_id, token_id, event_time);


-- 13. PREDICTION SNAPSHOTS
CREATE TABLE public.prediction_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    token_id UUID NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    wait_p25_seconds INTEGER NOT NULL,
    wait_p75_seconds INTEGER NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    model_version TEXT NOT NULL,
    data_status TEXT NOT NULL CHECK (
        data_status IN (
            'PRELIMINARY',
            'BUILDING_HISTORY',
            'ESTABLISHED'
        )
    ),
    forecast_kind TEXT NOT NULL DEFAULT 'UPDATED'
        CHECK (forecast_kind IN ('INITIAL', 'UPDATED')),
    actual_wait_seconds INTEGER,
    absolute_error_seconds INTEGER,
    evaluated_at TIMESTAMPTZ,

    CHECK (wait_p25_seconds >= 0),
    CHECK (wait_p75_seconds >= wait_p25_seconds),
    CHECK (sample_count >= 0),
    CHECK (actual_wait_seconds IS NULL OR actual_wait_seconds >= 0),
    CHECK (absolute_error_seconds IS NULL OR absolute_error_seconds >= 0),

    FOREIGN KEY (clinic_id, token_id)
        REFERENCES public.queue_tokens(clinic_id, id)
);

CREATE INDEX idx_prediction_token_time
ON public.prediction_snapshots (clinic_id, token_id, generated_at);


-- SECURITY AND APPLICATION PERMISSIONS

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO clinicq_app;

GRANT USAGE ON TYPE public.token_status TO clinicq_app;

GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public
TO clinicq_app;

ALTER DEFAULT PRIVILEGES
FOR ROLE neondb_owner
IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE
ON TABLES TO clinicq_app;

COMMIT;
