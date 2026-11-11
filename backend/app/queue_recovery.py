from sqlalchemy import text
from sqlalchemy.orm import Session


def recover_stale_consultations(
    db: Session,
    clinic_id,
    staff_user_id=None,
) -> int:
    """
    Close unfinished consultations belonging to previous clinic days.
    Mark their tokens as CANCELLED and record an interruption event.
    """

    stale_consultations = db.execute(
        text("""
            SELECT
                co.id AS consultation_id,
                co.token_id
            FROM public.consultations co
            JOIN public.queue_tokens qt
                ON qt.clinic_id = co.clinic_id
                AND qt.id = co.token_id
            JOIN public.clinics c
                ON c.id = co.clinic_id
            WHERE co.clinic_id = :clinic_id
                AND co.ended_at IS NULL
                AND qt.token_date <
                    (NOW() AT TIME ZONE c.timezone)::date
            FOR UPDATE OF co, qt
        """),
        {"clinic_id": clinic_id},
    ).mappings().all()

    recovered_count = 0

    for consultation in stale_consultations:
        result = db.execute(
            text("""
                UPDATE public.consultations
                SET
                    ended_at = NOW(),
                    outcome = 'INTERRUPTED'
                WHERE id = :consultation_id
                    AND ended_at IS NULL
                RETURNING token_id
            """),
            {
                "consultation_id": consultation["consultation_id"],
            },
        ).first()

        if result is None:
            continue

        db.execute(
            text("""
                UPDATE public.queue_tokens
                SET status = 'CANCELLED'
                WHERE clinic_id = :clinic_id
                    AND id = :token_id
                    AND status = 'IN_CONSULTATION'
            """),
            {
                "clinic_id": clinic_id,
                "token_id": consultation["token_id"],
            },
        )

        db.execute(
            text("""
                INSERT INTO public.queue_events (
                    clinic_id,
                    token_id,
                    event_type,
                    staff_user_id
                )
                VALUES (
                    :clinic_id,
                    :token_id,
                    'CONSULTATION_INTERRUPTED',
                    :staff_user_id
                )
            """),
            {
                "clinic_id": clinic_id,
                "token_id": consultation["token_id"],
                "staff_user_id": staff_user_id,
            },
        )

        recovered_count += 1

    return recovered_count