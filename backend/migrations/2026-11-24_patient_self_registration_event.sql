ALTER TABLE public.queue_events
DROP CONSTRAINT IF EXISTS queue_events_event_type_check;

ALTER TABLE public.queue_events
ADD CONSTRAINT queue_events_event_type_check
CHECK (
    event_type = ANY (ARRAY[
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
        'CUSTOM_MESSAGE_RESOLVED',
        'PATIENT_SELF_REGISTERED'
    ]::text[])
);
