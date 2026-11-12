import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.queue_recovery import recover_stale_consultations
from app.db import get_db
from app.dependencies import get_current_staff
from app.schemas import StaffTokenCreate
from app.security import (
    create_patient_access_code,
    hash_patient_access_code,
    create_patient_qr_token,
    hash_patient_qr_token,
)

class EmergencyPayload(BaseModel):
    expected_delay_minutes: int = Field(ge=1, le=240)
    public_message: str = Field(
        default="An emergency has interrupted the queue. Waiting times have been updated.",
        min_length=1,
        max_length=240
    )


class CustomMessagePayload(BaseModel):
    public_message: str = Field(min_length=1, max_length=240)
    expected_delay_minutes: int = Field(default=0, ge=0, le=240)

router = APIRouter(
    prefix="/api/staff",
    tags=["Staff Queue"]
)


def verify_staff_role(staff):
    if staff["role"] not in ("OWNER", "RECEPTIONIST"):
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to manage this queue."
        )


@router.post(
    "/tokens",
    status_code=status.HTTP_201_CREATED
)
def create_patient_token(
    payload: StaffTokenCreate,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        # 1. Find the authenticated clinic.
        clinic = db.execute(
            text("""
                SELECT id, name, clinic_code, timezone
                FROM public.clinics
                WHERE id = :clinic_id
                  AND is_active = TRUE
            """),
            {"clinic_id": staff["clinic_id"]}
        ).mappings().one_or_none()

        if clinic is None:
            raise HTTPException(
                status_code=404,
                detail="Clinic not found."
            )

        # 2. Find an active doctor belonging to this clinic.
        if payload.doctor_id:
            doctor = db.execute(
                text("""
                    SELECT id, display_name
                    FROM public.doctors
                    WHERE id = :doctor_id
                      AND clinic_id = :clinic_id
                      AND is_active = TRUE
                """),
                {
                    "doctor_id": payload.doctor_id,
                    "clinic_id": clinic["id"]
                }
            ).mappings().one_or_none()
        else:
            doctor = db.execute(
                text("""
                    SELECT id, display_name
                    FROM public.doctors
                    WHERE clinic_id = :clinic_id
                      AND is_active = TRUE
                    ORDER BY created_at, id
                    LIMIT 1
                """),
                {"clinic_id": clinic["id"]}
            ).mappings().one_or_none()

        if doctor is None:
            raise HTTPException(
                status_code=409,
                detail="No active doctor is available."
            )

        # 3. Calculate the current date in clinic timezone.
        token_date = db.execute(
            text("""
                SELECT (NOW() AT TIME ZONE :timezone)::date
            """),
            {"timezone": clinic["timezone"]}
        ).scalar_one()

        # 4. Atomically allocate the next daily token number.
        token_number = db.execute(
            text("""
                INSERT INTO public.clinic_day_counters (
                    clinic_id,
                    token_date,
                    last_issued_number
                )
                VALUES (
                    :clinic_id,
                    :token_date,
                    1
                )
                ON CONFLICT (clinic_id, token_date)
                DO UPDATE SET
                    last_issued_number =
                    public.clinic_day_counters.last_issued_number + 1
                RETURNING last_issued_number
            """),
            {
                "clinic_id": clinic["id"],
                "token_date": token_date
            }
        ).scalar_one()

        # 5. Generate the private patient credential.
        access_code = create_patient_access_code()

        access_code_hash = hash_patient_access_code(
            access_code
        )
        qr_token = create_patient_qr_token()
        qr_access_hash = hash_patient_qr_token(qr_token)


        # 6. Create the queue token.
        token = db.execute(
            text("""
    INSERT INTO public.queue_tokens (
        clinic_id,
        doctor_id,
        token_date,
        token_number,
        access_code_hash,
        qr_access_hash,
        patient_name,
        age,
        sex,
        mobile_number,
        visit_reason
    )
    VALUES (
        :clinic_id,
        :doctor_id,
        :token_date,
        :token_number,
        :access_code_hash,
        :qr_access_hash,
        :patient_name,
        :age,
        :sex,
        :mobile_number,
        :visit_reason
    )
    RETURNING id, joined_at, status::text AS status
"""),
            {
                "clinic_id": clinic["id"],
                "doctor_id": doctor["id"],
                "token_date": token_date,
                "token_number": token_number,
                "access_code_hash": access_code_hash,
                "qr_access_hash": qr_access_hash,
                "patient_name": payload.patient_name,
                "age": payload.age,
                "sex": payload.sex,
                "mobile_number": payload.mobile_number,
                "visit_reason": payload.visit_reason,
            }
        ).mappings().one()

        # 7. Record the registration event.
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
                    'TOKEN_CREATED',
                    :staff_user_id
                )
            """),
            {
                "clinic_id": clinic["id"],
                "token_id": token["id"],
                "staff_user_id": staff["staff_id"]
            }
        )

        # Commit token, counter and audit event together.
        db.commit()

        # Return the raw access code only once.
        return {
            "status": "success",
            "message": "Patient token created successfully",
            "clinic_name": clinic["name"],
            "clinic_code": clinic["clinic_code"],
            "doctor_name": doctor["display_name"],
            "token_id": str(token["id"]),
            "token_number": token_number,
            "token_date": str(token_date),
            "patient_status": token["status"],
            "joined_at": token["joined_at"],
            "patient_access_code": access_code,
            "qr_token": qr_token,
            "important": "Save this access code. It cannot be retrieved later."
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to register patient. Check server logs."
        )

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unexpected registration error."
        )


@router.get("/queue")
def get_staff_queue(
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        # Clinic identity comes from authenticated staff.
        clinic = db.execute(
            text("""
                SELECT
                    id,
                    name,
                    clinic_code,
                    timezone,
                    queue_paused
                FROM public.clinics
                WHERE id = :clinic_id
                  AND is_active = TRUE
            """),
            {"clinic_id": staff["clinic_id"]}
        ).mappings().one_or_none()

        if clinic is None:
            raise HTTPException(
                status_code=404,
                detail="Clinic not found."
            )

        # Recover consultations left open from previous clinic days.
        recovered_count = recover_stale_consultations(
            db,
            clinic["id"],
            staff_user_id=staff["staff_id"]
        )

        if recovered_count > 0:
            db.commit()

        # Find the active doctor for the current MVP.
        doctor = db.execute(
            text("""
                SELECT id, display_name
                FROM public.doctors
                WHERE clinic_id = :clinic_id
                  AND is_active = TRUE
                ORDER BY created_at, id
                LIMIT 1
            """),
            {"clinic_id": clinic["id"]}
        ).mappings().one_or_none()

        if doctor is None:
            raise HTTPException(
                status_code=409,
                detail="No active doctor found."
            )

        # Determine today's date using clinic timezone.
        token_date = db.execute(
            text("""
                SELECT (NOW() AT TIME ZONE :timezone)::date
            """),
            {"timezone": clinic["timezone"]}
        ).scalar_one()

        # Fetch today's queue without exposing patient credentials.
        rows = db.execute(
            text("""
                SELECT
                    qt.id,
                    qt.token_number,
                    qt.patient_name,
                    qt.status::text AS status,
                    qt.joined_at,
                    c.started_at,
                    c.ended_at
                FROM public.queue_tokens qt
                LEFT JOIN public.consultations c
                    ON c.clinic_id = qt.clinic_id
                   AND c.token_id = qt.id
                WHERE qt.clinic_id = :clinic_id
                  AND qt.doctor_id = :doctor_id
                  AND qt.token_date = :token_date
                ORDER BY qt.token_number
            """),
            {
                "clinic_id": clinic["id"],
                "doctor_id": doctor["id"],
                "token_date": token_date
            }
        ).mappings().all()

        tokens = [
            {
                "token_id": str(row["id"]),
                "token_number": row["token_number"],
                "patient_name": row["patient_name"],
                "status": row["status"],
                "joined_at": row["joined_at"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"]
            }
            for row in rows
        ]

        # Fetch active emergencies and public clinic messages.
        active_disruptions = db.execute(
            text("""
                SELECT
                    id::text AS id,
                    after_token_id::text AS after_token_id,
                    event_type,
                    expected_delay_minutes,
                    public_message,
                    started_at
                FROM public.queue_disruptions
                WHERE clinic_id = :clinic_id
                  AND status = 'ACTIVE'
                ORDER BY started_at DESC
            """),
            {
                "clinic_id": clinic["id"]
            }
        ).mappings().all()

        current = next(
            (
                token for token in tokens
                if token["status"] == "IN_CONSULTATION"
            ),
            None
        )

        waiting_count = sum(
            1 for token in tokens
            if token["status"] == "WAITING"
        )

        return {
    "clinic_name": clinic["name"],
    "clinic_code": clinic["clinic_code"],
    "doctor_name": doctor["display_name"],
    "queue_date": str(token_date),
    "queue_paused": clinic["queue_paused"],
    "current_consultation": current,
    "waiting_count": waiting_count,
    "total_tokens_today": len(tokens),
    "tokens": tokens,
    "active_disruptions": [
        dict(row) for row in active_disruptions
    ]
}
    except HTTPException:
        raise

    except SQLAlchemyError:
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve clinic queue."
        )

# ==========================================
# QUEUE STATE MANAGEMENT
# ==========================================

def _get_clinic_for_update(db, clinic_id):
    """Lock the clinic row while changing queue controls."""

    return db.execute(
        text("""
            SELECT id, queue_paused
            FROM public.clinics
            WHERE id = :clinic_id
              AND is_active = TRUE
            FOR UPDATE
        """),
        {"clinic_id": clinic_id}
    ).mappings().one_or_none()


def _get_token_for_update(db, clinic_id, token_id):
    """Lock a token before changing its status."""

    return db.execute(
        text("""
            SELECT
                id,
                doctor_id,
                token_date,
                token_number,
                joined_at,
                status::text AS status

            FROM public.queue_tokens

            WHERE id = :token_id
              AND clinic_id = :clinic_id

            FOR UPDATE
        """),
        {
            "token_id": token_id,
            "clinic_id": clinic_id
        }
    ).mappings().one_or_none()

def _get_active_queue_token_id(db, clinic_id):
    """Return the token currently in consultation for this clinic."""

    return db.execute(
        text("""
            SELECT token_id
            FROM public.consultations
            WHERE clinic_id = :clinic_id
              AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
        """),
        {
            "clinic_id": clinic_id
        }
    ).scalar_one_or_none()

def _record_queue_event(
    db,
    clinic_id,
    staff_user_id,
    event_type,
    token_id=None,
    public_message=None
):
    """Save an auditable record of a queue operation."""

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
                :staff_user_id,
                :public_message
            )
        """),
        {
            "clinic_id": clinic_id,
            "token_id": token_id,
            "event_type": event_type,
            "staff_user_id": staff_user_id,
            "public_message": public_message
        }
    )

# ==========================================
# EMERGENCY AND CUSTOM MESSAGE MANAGEMENT
# ==========================================

@router.post("/disruptions/emergency")
def create_or_update_emergency(
    payload: EmergencyPayload,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        clinic = _get_clinic_for_update(
            db,
            staff["clinic_id"]
        )

        if clinic is None:
            raise HTTPException(
                status_code=404,
                detail="Clinic not found."
            )

        # Find the existing active emergency.
        existing = db.execute(
            text("""
                SELECT id
                FROM public.queue_disruptions
                WHERE clinic_id = :clinic_id
                  AND event_type = 'EMERGENCY'
                  AND status = 'ACTIVE'
                ORDER BY started_at DESC
                LIMIT 1
                FOR UPDATE
            """),
            {
                "clinic_id": staff["clinic_id"]
            }
        ).mappings().one_or_none()

        if existing:
            # Update the existing record.
            # The previous delay is replaced, not added.
            disruption = db.execute(
                text("""
                    UPDATE public.queue_disruptions
                    SET
                        expected_delay_minutes = :delay,
                        public_message = :message
                    WHERE id = :id
                      AND clinic_id = :clinic_id
                    RETURNING
                        id::text AS id,
                        after_token_id::text AS after_token_id,
                        event_type,
                        expected_delay_minutes,
                        public_message,
                        status,
                        started_at,
                        resolved_at
                """),
                {
                    "id": existing["id"],
                    "clinic_id": staff["clinic_id"],
                    "delay": payload.expected_delay_minutes,
                    "message": payload.public_message
                }
            ).mappings().one()

            event_type = "DELAY_UPDATED"
            response_message = "Emergency updated."

        else:
            # Capture the patient currently being served.
            after_token_id = _get_active_queue_token_id(
                db,
                staff["clinic_id"]
            )

            # Create a new emergency.
            disruption = db.execute(
                text("""
                    INSERT INTO public.queue_disruptions (
                        clinic_id,
                        after_token_id,
                        event_type,
                        expected_delay_minutes,
                        public_message,
                        status,
                        started_at,
                        created_by
                    )
                    VALUES (
                        :clinic_id,
                        :after_token_id,
                        'EMERGENCY',
                        :delay,
                        :message,
                        'ACTIVE',
                        NOW(),
                        :staff_id
                    )
                    RETURNING
                        id::text AS id,
                        after_token_id::text AS after_token_id,
                        event_type,
                        expected_delay_minutes,
                        public_message,
                        status,
                        started_at,
                        resolved_at
                """),
                {
                    "clinic_id": staff["clinic_id"],
                    "delay": payload.expected_delay_minutes,
                    "message": payload.public_message,
                    "staff_id": staff["staff_id"],
                    "after_token_id": after_token_id
                }
            ).mappings().one()

            event_type = "DELAY_CREATED"
            response_message = "Emergency activated."

        # Notify connected patient portals through queue events.
        _record_queue_event(
            db,
            staff["clinic_id"],
            staff["staff_id"],
            event_type,
            public_message=disruption["public_message"]
        )

        db.commit()

        return {
            "status": "success",
            "message": response_message,
            "disruption": dict(disruption)
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logging.exception("Unable to create or update emergency.")
        raise HTTPException(
            status_code=500,
            detail="Unable to update emergency."
        )


@router.post("/disruptions/message")
def create_or_update_custom_message(
    payload: CustomMessagePayload,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        clinic = _get_clinic_for_update(
            db,
            staff["clinic_id"]
        )

        if clinic is None:
            raise HTTPException(
                status_code=404,
                detail="Clinic not found."
            )

        # Keep one active custom message per clinic.
        existing = db.execute(
            text("""
                SELECT id
                FROM public.queue_disruptions
                WHERE clinic_id = :clinic_id
                  AND event_type = 'CUSTOM_MESSAGE'
                  AND status = 'ACTIVE'
                ORDER BY started_at DESC
                LIMIT 1
                FOR UPDATE
            """),
            {
                "clinic_id": staff["clinic_id"]
            }
        ).mappings().one_or_none()

        if existing:
            disruption = db.execute(
                text("""
                    UPDATE public.queue_disruptions
                    SET
                        expected_delay_minutes = :delay,
                        public_message = :message
                    WHERE id = :id
                      AND clinic_id = :clinic_id
                    RETURNING
                        id::text AS id,
                        after_token_id::text AS after_token_id,
                        event_type,
                        expected_delay_minutes,
                        public_message,
                        status,
                        started_at,
                        resolved_at
                """),
                {
                    "id": existing["id"],
                    "clinic_id": staff["clinic_id"],
                    "delay": payload.expected_delay_minutes,
                    "message": payload.public_message
                }
            ).mappings().one()

            event_type = "CUSTOM_MESSAGE_UPDATED"
            response_message = "Custom message updated."

        else:
            # Capture the patient currently being served.
            after_token_id = _get_active_queue_token_id(
                db,
                staff["clinic_id"]
            )
            disruption = db.execute(
                text("""
                    INSERT INTO public.queue_disruptions (
                        clinic_id,
                        after_token_id,
                        event_type,
                        expected_delay_minutes,
                        public_message,
                        status,
                        started_at,
                        created_by
                    )
                    VALUES (
                        :clinic_id,
                        :after_token_id,
                        'CUSTOM_MESSAGE',
                        :delay,
                        :message,
                        'ACTIVE',
                        NOW(),
                        :staff_id
                    )
                    RETURNING
                        id::text AS id,
                        after_token_id::text AS after_token_id,
                        event_type,
                        expected_delay_minutes,
                        public_message,
                        status,
                        started_at,
                        resolved_at
                """),
                {
                    "clinic_id": staff["clinic_id"],
                    "delay": payload.expected_delay_minutes,
                    "message": payload.public_message,
                    "staff_id": staff["staff_id"],
                    "after_token_id": after_token_id
                }
            ).mappings().one()

            event_type = "CUSTOM_MESSAGE_CREATED"
            response_message = "Custom message activated."

        _record_queue_event(
            db,
            staff["clinic_id"],
            staff["staff_id"],
            event_type,
            public_message=disruption["public_message"]
        )

        db.commit()

        return {
            "status": "success",
            "message": response_message,
            "disruption": dict(disruption)
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logging.exception("Unable to create or update custom message.")
        raise HTTPException(
            status_code=500,
            detail="Unable to update custom message."
        )


@router.post("/disruptions/{disruption_id}/resolve")
def resolve_disruption(
    disruption_id: UUID,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        clinic = _get_clinic_for_update(
            db,
            staff["clinic_id"]
        )

        if clinic is None:
            raise HTTPException(
                status_code=404,
                detail="Clinic not found."
            )

        disruption = db.execute(
            text("""
                SELECT
                    id,
                    event_type,
                    public_message
                FROM public.queue_disruptions
                WHERE id = :id
                  AND clinic_id = :clinic_id
                  AND status = 'ACTIVE'
                FOR UPDATE
            """),
            {
                "id": disruption_id,
                "clinic_id": staff["clinic_id"]
            }
        ).mappings().one_or_none()

        if disruption is None:
            raise HTTPException(
                status_code=404,
                detail="Active disruption not found."
            )

        db.execute(
            text("""
                UPDATE public.queue_disruptions
                SET
                    status = 'RESOLVED',
                    resolved_at = NOW()
                WHERE id = :id
                  AND clinic_id = :clinic_id
            """),
            {
                "id": disruption_id,
                "clinic_id": staff["clinic_id"]
            }
        )

        event_type = (
            "CUSTOM_MESSAGE_RESOLVED"
            if disruption["event_type"] == "CUSTOM_MESSAGE"
            else "DELAY_RESOLVED"
        )

        _record_queue_event(
            db,
            staff["clinic_id"],
            staff["staff_id"],
            event_type,
            public_message=disruption["public_message"]
        )

        db.commit()

        return {
            "status": "success",
            "message": "Disruption resolved."
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logging.exception("Unable to resolve disruption.")
        raise HTTPException(
            status_code=500,
            detail="Unable to resolve disruption."
        )


# ==========================================
# 1. START CONSULTATION
# ==========================================

@router.post("/tokens/{token_id}/start")
def start_consultation(
    token_id: UUID,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        # Lock clinic first. This also serializes simultaneous
        # start requests and queue pause/resume operations.
        clinic = _get_clinic_for_update(
            db,
            staff["clinic_id"]
        )

        if clinic is None:
            raise HTTPException(
                status_code=404,
                detail="Clinic not found."
            )

                # Recover consultations left open from previous clinic days.
        recovered_count = recover_stale_consultations(
            db=db,
            clinic_id=staff["clinic_id"],
            staff_user_id=staff["staff_id"],
        )

        if recovered_count > 0:
            # Persist recovery even if the new start request is rejected.
            db.commit()

            # Re-acquire the clinic lock before continuing.
            clinic = _get_clinic_for_update(
                db,
                staff["clinic_id"]
            )

            if clinic is None:
                raise HTTPException(
                    status_code=404,
                    detail="Clinic not found."
                )

        if clinic["queue_paused"]:
            raise HTTPException(
                status_code=409,
                detail="Queue is paused. Resume before starting."
            )

        token = _get_token_for_update(
            db,
            staff["clinic_id"],
            token_id
        )

        if token is None:
            raise HTTPException(
                status_code=404,
                detail="Token not found in your clinic."
            )

        if token["status"] != "WAITING":
            raise HTTPException(
                status_code=409,
                detail="Only waiting tokens can be started."
            )

        # Lock the doctor record.
        doctor = db.execute(
            text("""
                SELECT id
                FROM public.doctors
                WHERE id = :doctor_id
                  AND clinic_id = :clinic_id
                  AND is_active = TRUE
                FOR UPDATE
            """),
            {
                "doctor_id": token["doctor_id"],
                "clinic_id": staff["clinic_id"]
            }
        ).scalar_one_or_none()

        if doctor is None:
            raise HTTPException(
                status_code=404,
                detail="Active doctor not found."
            )

        # Do not allow another active consultation.
        active = db.execute(
            text("""
                SELECT id
                FROM public.consultations
                WHERE clinic_id = :clinic_id
                  AND doctor_id = :doctor_id
                  AND ended_at IS NULL
                LIMIT 1
            """),
            {
                "clinic_id": staff["clinic_id"],
                "doctor_id": token["doctor_id"]
            }
        ).scalar_one_or_none()

        if active is not None:
            raise HTTPException(
                status_code=409,
                detail="Another consultation is already active."
            )

        # Enforce queue order.
        earlier_waiting = db.execute(
            text("""
                SELECT token_number
                FROM public.queue_tokens
                WHERE clinic_id = :clinic_id
                  AND doctor_id = :doctor_id
                  AND token_date = :token_date
                  AND status = 'WAITING'
                  AND token_number < :token_number
                ORDER BY token_number
                LIMIT 1
            """),
            {
                "clinic_id": staff["clinic_id"],
                "doctor_id": token["doctor_id"],
                "token_date": token["token_date"],
                "token_number": token["token_number"]
            }
        ).scalar_one_or_none()

        if earlier_waiting is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Token {earlier_waiting} is still waiting. "
                    "Complete, skip or cancel earlier tokens first."
                )
            )

        # Record actual consultation start time.
        consultation = db.execute(
            text("""
                INSERT INTO public.consultations (
                    clinic_id,
                    token_id,
                    doctor_id,
                    started_at
                )
                VALUES (
                    :clinic_id,
                    :token_id,
                    :doctor_id,
                    NOW()
                )
                RETURNING started_at
            """),
            {
                "clinic_id": staff["clinic_id"],
                "token_id": token_id,
                "doctor_id": token["doctor_id"]
            }
        ).mappings().one()


        # Calculate the total time the patient waited since registration.
        actual_total_wait_seconds = max(
            0,
            int(
                (
                    consultation["started_at"]
                    - token["joined_at"]
                ).total_seconds()
            )
        )

        # Compare the latest forecast with the actual remaining
        # time from when that forecast was generated.
        prediction_evaluation = db.execute(
            text("""
                WITH latest_snapshot AS (
                    SELECT
                        id,

                        ROUND(
                            (
                                wait_p25_seconds
                                + wait_p75_seconds
                            ) / 2.0
                        )::INTEGER AS predicted_wait_seconds,

                        GREATEST(
                            0,
                            FLOOR(
                                EXTRACT(
                                    EPOCH FROM (
                                        :started_at - generated_at
                                    )
                                )
                            )::INTEGER
                        ) AS actual_remaining_wait_seconds

                    FROM public.prediction_snapshots

                    WHERE clinic_id = :clinic_id
                      AND token_id = :token_id
                      AND evaluated_at IS NULL
                      AND generated_at <= :started_at
                      AND generated_at >= :started_at - INTERVAL '5 minutes'
                      ORDER BY generated_at DESC, id DESC
                    LIMIT 1
                )

                UPDATE public.prediction_snapshots AS ps

                SET
                    actual_wait_seconds =
                        latest_snapshot.actual_remaining_wait_seconds,

                    absolute_error_seconds = ABS(
                        latest_snapshot.actual_remaining_wait_seconds
                        - latest_snapshot.predicted_wait_seconds
                    ),

                    evaluated_at = :started_at

                FROM latest_snapshot

                WHERE ps.id = latest_snapshot.id

                RETURNING
                    latest_snapshot.predicted_wait_seconds,
                    ps.actual_wait_seconds,
                    ps.absolute_error_seconds,
                    ps.evaluated_at
            """),
            {
                "clinic_id": staff["clinic_id"],
                "token_id": token_id,
                "started_at": consultation["started_at"],
            }
        ).mappings().one_or_none()



        # Change patient status.
        db.execute(
            text("""
                UPDATE public.queue_tokens
                SET
                    status = 'IN_CONSULTATION',
                    updated_at = NOW()
                WHERE id = :token_id
                  AND clinic_id = :clinic_id
                  AND status = 'WAITING'
            """),
            {
                "token_id": token_id,
                "clinic_id": staff["clinic_id"]
            }
        )

        _record_queue_event(
            db,
            staff["clinic_id"],
            staff["staff_id"],
            "CONSULTATION_STARTED",
            token_id
        )

        db.commit()


        return {
            "status": "success",
            "message": "Consultation started.",
            "token_number": token["token_number"],
            "patient_status": "IN_CONSULTATION",
            "started_at": consultation["started_at"],

            "actual_total_wait_seconds": actual_total_wait_seconds,

            "prediction_evaluated": (
                prediction_evaluation is not None
            ),

            "prediction_actual_remaining_wait_seconds": (
                prediction_evaluation["actual_wait_seconds"]
                if prediction_evaluation is not None
                else None
            ),

            "predicted_wait_seconds": (
                prediction_evaluation["predicted_wait_seconds"]
                if prediction_evaluation is not None
                else None
            ),

            "prediction_absolute_error_seconds": (
                prediction_evaluation["absolute_error_seconds"]
                if prediction_evaluation is not None
                else None
            )
        }


    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to start consultation."
        )


# ==========================================
# 2. COMPLETE CONSULTATION
# ==========================================

@router.post("/tokens/{token_id}/complete")
def complete_consultation(
    token_id: UUID,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        token = _get_token_for_update(
            db,
            staff["clinic_id"],
            token_id
        )

        if token is None:
            raise HTTPException(
                status_code=404,
                detail="Token not found in your clinic."
            )

        if token["status"] != "IN_CONSULTATION":
            raise HTTPException(
                status_code=409,
                detail="This token has no active consultation."
            )

        consultation = db.execute(
            text("""
               UPDATE public.consultations
SET
    ended_at = NOW(),
    outcome = 'COMPLETED'
WHERE clinic_id = :clinic_id
    AND token_id = :token_id
    AND ended_at IS NULL
                RETURNING
                    started_at,
                    ended_at,
                    FLOOR(
                        EXTRACT(EPOCH FROM (NOW() - started_at))
                    )::INTEGER AS duration_seconds
            """),
            {
                "clinic_id": staff["clinic_id"],
                "token_id": token_id
            }
        ).mappings().one_or_none()

        if consultation is None:
            raise HTTPException(
                status_code=409,
                detail="No active consultation record exists."
            )

        db.execute(
            text("""
                UPDATE public.queue_tokens
                SET
                    status = 'COMPLETED',
                    updated_at = NOW()
                WHERE id = :token_id
                  AND clinic_id = :clinic_id
            """),
            {
                "token_id": token_id,
                "clinic_id": staff["clinic_id"]
            }
        )

        _record_queue_event(
            db,
            staff["clinic_id"],
            staff["staff_id"],
            "CONSULTATION_COMPLETED",
            token_id
        )

        db.commit()

        return {
            "status": "success",
            "message": "Consultation completed.",
            "token_number": token["token_number"],
            "patient_status": "COMPLETED",
            "started_at": consultation["started_at"],
            "ended_at": consultation["ended_at"],
            "duration_seconds": consultation["duration_seconds"]
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to complete consultation."
        )


# ==========================================
# 3. SKIP OR CANCEL A WAITING TOKEN
# ==========================================

def _change_waiting_token_status(
    token_id,
    staff,
    db,
    new_status,
    event_type
):
    token = _get_token_for_update(
        db,
        staff["clinic_id"],
        token_id
    )

    if token is None:
        raise HTTPException(
            status_code=404,
            detail="Token not found in your clinic."
        )

    if token["status"] != "WAITING":
        raise HTTPException(
            status_code=409,
            detail="Only waiting tokens can be skipped or cancelled."
        )

    db.execute(
        text("""
            UPDATE public.queue_tokens
            SET
                status = CAST(:new_status AS public.token_status),
                updated_at = NOW()
            WHERE id = :token_id
              AND clinic_id = :clinic_id
        """),
        {
            "new_status": new_status,
            "token_id": token_id,
            "clinic_id": staff["clinic_id"]
        }
    )

    _record_queue_event(
        db,
        staff["clinic_id"],
        staff["staff_id"],
        event_type,
        token_id
    )

    db.commit()

    return {
        "status": "success",
        "token_number": token["token_number"],
        "patient_status": new_status,
        "message": f"Token {token['token_number']} marked {new_status}."
    }


@router.post("/tokens/{token_id}/skip")
def skip_token(
    token_id: UUID,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        return _change_waiting_token_status(
            token_id,
            staff,
            db,
            "SKIPPED",
            "TOKEN_SKIPPED"
        )

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logging.exception("Database error while skipping token")
        raise HTTPException(
            status_code=500,
            detail="Unable to skip token."
        )

@router.post("/tokens/{token_id}/cancel")
def cancel_token(
    token_id: UUID,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        return _change_waiting_token_status(
            token_id,
            staff,
            db,
            "CANCELLED",
            "TOKEN_CANCELLED"
        )

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logging.exception("Database error while cancelling token")
        raise HTTPException(
            status_code=500,
            detail="Unable to cancel token."
        )


# ==========================================
# 4. PAUSE AND RESUME QUEUE
# ==========================================

def _set_queue_pause_state(
    staff,
    db,
    requested_state,
    event_type
):
    clinic = _get_clinic_for_update(
        db,
        staff["clinic_id"]
    )

    if clinic is None:
        raise HTTPException(
            status_code=404,
            detail="Clinic not found."
        )

    # Make repeated pause/resume requests harmless.
    if clinic["queue_paused"] == requested_state:
        db.commit()

        return {
            "status": "success",
            "queue_paused": requested_state,
            "message": (
                "Queue is already paused."
                if requested_state
                else "Queue is already active."
            )
        }

    db.execute(
        text("""
            UPDATE public.clinics
            SET queue_paused = :requested_state
            WHERE id = :clinic_id
        """),
        {
            "requested_state": requested_state,
            "clinic_id": staff["clinic_id"]
        }
    )

    _record_queue_event(
        db,
        staff["clinic_id"],
        staff["staff_id"],
        event_type
    )

    db.commit()

    return {
        "status": "success",
        "queue_paused": requested_state,
        "message": (
            "Queue paused successfully."
            if requested_state
            else "Queue resumed successfully."
        )
    }


@router.post("/queue/pause")
def pause_queue(
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        return _set_queue_pause_state(
            staff,
            db,
            True,
            "QUEUE_PAUSED"
        )

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to pause queue."
        )


@router.post("/queue/resume")
def resume_queue(
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db)
):
    verify_staff_role(staff)

    try:
        return _set_queue_pause_state(
            staff,
            db,
            False,
            "QUEUE_RESUMED"
        )

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to resume queue."
        )
