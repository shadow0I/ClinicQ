from sqlalchemy import text
from sqlalchemy.orm import Session


def expire_previous_day_disruptions(db: Session, clinic_id) -> int:
    """Resolve active disruption records created on earlier clinic days.

    The comparison uses each clinic's configured timezone. Rows are retained
    for audit/history; only their active status is closed.
    """
    expired = db.execute(
        text("""
            UPDATE public.queue_disruptions AS qd
            SET
                status = 'RESOLVED',
                resolved_at = NOW()
            FROM public.clinics AS c
            WHERE qd.clinic_id = :clinic_id
              AND c.id = qd.clinic_id
              AND qd.status = 'ACTIVE'
              AND (qd.started_at AT TIME ZONE c.timezone)::date
                    < (NOW() AT TIME ZONE c.timezone)::date
            RETURNING
                qd.after_token_id,
                qd.event_type,
                qd.public_message
        """),
        {"clinic_id": clinic_id}
    ).mappings().all()

    for disruption in expired:
        event_type = (
            "CUSTOM_MESSAGE_RESOLVED"
            if disruption["event_type"] == "CUSTOM_MESSAGE"
            else "DELAY_RESOLVED"
        )

        db.execute(
            text("""
                INSERT INTO public.queue_events (
                    clinic_id,
                    token_id,
                    event_type,
                    staff_user_id,
                    public_message
                )
                VALUES (
                    :clinic_id,
                    :token_id,
                    :event_type,
                    NULL,
                    :public_message
                )
            """),
            {
                "clinic_id": clinic_id,
                "token_id": disruption["after_token_id"],
                "event_type": event_type,
                "public_message": disruption["public_message"],
            }
        )

    return len(expired)
