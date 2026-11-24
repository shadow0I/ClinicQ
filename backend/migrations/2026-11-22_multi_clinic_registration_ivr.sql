-- ClinicQ: patient self-registration and opt-in voice reminders.
-- Apply once to an existing ClinicQ database. This migration is additive.

ALTER TABLE public.clinics
    ADD COLUMN IF NOT EXISTS public_registration_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE public.clinics
    ADD COLUMN IF NOT EXISTS voice_reminders_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.queue_tokens
    ADD COLUMN IF NOT EXISTS voice_reminder_consent BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.queue_tokens
    ADD COLUMN IF NOT EXISTS voice_reminder_consent_at TIMESTAMPTZ;

ALTER TABLE public.queue_tokens
    ADD COLUMN IF NOT EXISTS voice_reminder_opted_out_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS public.ivr_reminders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id UUID NOT NULL,
    token_id UUID NOT NULL,
    reminder_type TEXT NOT NULL DEFAULT 'THREE_AHEAD'
        CHECK (reminder_type IN ('THREE_AHEAD')),
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN (
            'PENDING', 'DISPATCHING', 'SUBMITTED', 'IN_PROGRESS',
            'COMPLETED', 'FAILED', 'SKIPPED'
        )),
    provider_call_sid TEXT UNIQUE,
    patient_response TEXT
        CHECK (patient_response IS NULL OR patient_response IN (
            'CONFIRMED', 'CALLBACK_REQUESTED', 'OTHER'
        )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error TEXT,
    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_ivr_reminder_token
        FOREIGN KEY (clinic_id, token_id)
        REFERENCES public.queue_tokens (clinic_id, id),
    CONSTRAINT uq_ivr_reminder_per_token
        UNIQUE (clinic_id, token_id, reminder_type)
);

CREATE INDEX IF NOT EXISTS idx_ivr_reminders_due
    ON public.ivr_reminders (status, scheduled_at)
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_ivr_reminders_clinic_created
    ON public.ivr_reminders (clinic_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_tokens_voice_reminder_candidates
    ON public.queue_tokens (clinic_id, doctor_id, token_date, token_number)
    WHERE status = 'WAITING' AND voice_reminder_consent = TRUE;
