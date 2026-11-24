import os
import logging
import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.prediction_service import generate_and_store_prediction
from app.queue_recovery import recover_stale_consultations
from app.disruption_recovery import expire_previous_day_disruptions
from app.db import SessionLocal, get_db
from app.schemas import PatientAccessRequest, PatientSelfRegistration
from app.security import (
    create_session_token,
    hash_session_token,
    hash_patient_access_code,
    hash_patient_qr_token,
    create_patient_access_code,
    create_patient_qr_token,
)

logger = logging.getLogger("clinicq.patient")

router = APIRouter(
    prefix="/api/patient",
    tags=["Patient Portal"]
)

PATIENT_COOKIE_NAME = "clinicq_patient_session"
PATIENT_SESSION_DURATION = timedelta(hours=12)


class PatientQRAccessRequest(BaseModel):
    qr_token: str

COOKIE_SECURE = (
    os.getenv("COOKIE_SECURE", "false").lower() == "true"
)
COOKIE_SAMESITE = "none" if COOKIE_SECURE else "lax"

def set_patient_cookie(response: Response, raw_token: str):
    response.set_cookie(
        key=PATIENT_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        max_age=int(PATIENT_SESSION_DURATION.total_seconds()),
        path="/",
    )

def get_patient_context(request: Request, db: Session):
    raw_token = request.cookies.get(PATIENT_COOKIE_NAME)

    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail="Patient authentication required."
        )

    token_hash = hash_session_token(raw_token)

    patient = db.execute(
        text("""
            SELECT
                ps.id AS session_id,
                ps.clinic_id,
                ps.token_id,

                t.doctor_id,
                t.token_date,
                t.token_number,
                t.status::text AS patient_status,
                t.joined_at,
                t.voice_reminder_consent,


                c.name AS clinic_name,
                c.clinic_code,
                c.queue_paused,

                d.display_name AS doctor_name

            FROM public.patient_sessions ps

            JOIN public.queue_tokens t
                ON t.id = ps.token_id
               AND t.clinic_id = ps.clinic_id

            JOIN public.clinics c
                ON c.id = ps.clinic_id

            JOIN public.doctors d
                ON d.id = t.doctor_id
               AND d.clinic_id = t.clinic_id

            WHERE ps.session_token_hash = :token_hash
              AND ps.revoked_at IS NULL
              AND ps.expires_at > NOW()
              AND c.is_active = TRUE

            LIMIT 1
        """),
        {"token_hash": token_hash}
    ).mappings().one_or_none()

    if patient is None:
        raise HTTPException(
            status_code=401,
            detail="Patient session expired or invalid."
        )

    return patient


@router.get("/clinics")
def list_public_clinics(db: Session = Depends(get_db)):
    """List active clinics that allow public patient registration."""
    rows = db.execute(
        text("""
            SELECT name, clinic_code
            FROM public.clinics
            WHERE is_active = TRUE
              AND public_registration_enabled = TRUE
            ORDER BY name, clinic_code
        """)
    ).mappings().all()
    return {"status": "success", "clinics": [dict(row) for row in rows]}


@router.get("/clinics/{clinic_code}/doctors")
def list_public_clinic_doctors(
    clinic_code: str,
    db: Session = Depends(get_db),
):
    """List active doctors for one publicly registered clinic."""
    normalised_code = clinic_code.strip().upper()
    clinic = db.execute(text("""
        SELECT id
        FROM public.clinics
        WHERE UPPER(clinic_code) = :clinic_code
          AND is_active = TRUE
          AND public_registration_enabled = TRUE
        LIMIT 1
    """), {"clinic_code": normalised_code}).mappings().one_or_none()
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic is unavailable for online registration.")

    doctors = db.execute(text("""
        SELECT id::text AS id, display_name
        FROM public.doctors
        WHERE clinic_id = :clinic_id
          AND is_active = TRUE
        ORDER BY created_at, id
        LIMIT 1
    """), {"clinic_id": clinic["id"]}).mappings().all()
    return {"status": "success", "doctors": [dict(row) for row in doctors]}


@router.post("/register", status_code=201)
def self_register_patient(
    payload: PatientSelfRegistration,
    response: Response,
    db: Session = Depends(get_db),
):
    """Create a queue token for the selected clinic and sign the patient in."""
    try:
        clinic = db.execute(text("""
            SELECT id, name, clinic_code, timezone
            FROM public.clinics
            WHERE UPPER(clinic_code) = :clinic_code
              AND is_active = TRUE
              AND public_registration_enabled = TRUE
            LIMIT 1
            FOR SHARE
        """), {"clinic_code": payload.clinic_code}).mappings().one_or_none()
        if clinic is None:
            raise HTTPException(status_code=404, detail="Clinic is unavailable for online registration.")

        doctor = db.execute(text("""
            SELECT id, display_name
            FROM public.doctors
            WHERE id = :doctor_id
              AND clinic_id = :clinic_id
              AND is_active = TRUE
              AND id = (
                  SELECT id
                  FROM public.doctors
                  WHERE clinic_id = :clinic_id AND is_active = TRUE
                  ORDER BY created_at, id
                  LIMIT 1
              )
            LIMIT 1
        """), {
            "doctor_id": payload.doctor_id,
            "clinic_id": clinic["id"],
        }).mappings().one_or_none()
        if doctor is None:
            raise HTTPException(status_code=409, detail="Selected doctor is not available at this clinic.")

        token_date = db.execute(text("""
            SELECT (NOW() AT TIME ZONE :timezone)::date
        """), {"timezone": clinic["timezone"]}).scalar_one()

        token_number = db.execute(text("""
            INSERT INTO public.clinic_day_counters (
                clinic_id, token_date, last_issued_number
            )
            VALUES (:clinic_id, :token_date, 1)
            ON CONFLICT (clinic_id, token_date)
            DO UPDATE SET last_issued_number =
                public.clinic_day_counters.last_issued_number + 1
            RETURNING last_issued_number
        """), {
            "clinic_id": clinic["id"],
            "token_date": token_date,
        }).scalar_one()

        access_code = create_patient_access_code()
        qr_token = create_patient_qr_token()
        token = db.execute(text("""
            INSERT INTO public.queue_tokens (
                clinic_id, doctor_id, token_date, token_number,
                access_code_hash, qr_access_hash,
                patient_name, age, sex, mobile_number, visit_reason,
                voice_reminder_consent, voice_reminder_consent_at
            )
            VALUES (
                :clinic_id, :doctor_id, :token_date, :token_number,
                :access_code_hash, :qr_access_hash,
                :patient_name, :age, :sex, :mobile_number, :visit_reason,
                :voice_reminder_consent,
                CASE WHEN :voice_reminder_consent THEN NOW() ELSE NULL END
            )
            RETURNING id, joined_at, status::text AS status
        """), {
            "clinic_id": clinic["id"],
            "doctor_id": doctor["id"],
            "token_date": token_date,
            "token_number": token_number,
            "access_code_hash": hash_patient_access_code(access_code),
            "qr_access_hash": hash_patient_qr_token(qr_token),
            "patient_name": payload.patient_name,
            "age": payload.age,
            "sex": payload.sex,
            "mobile_number": payload.mobile_number,
            "visit_reason": payload.visit_reason,
            "voice_reminder_consent": payload.voice_reminder_consent,
        }).mappings().one()

        db.execute(text("""
            INSERT INTO public.queue_events (
                clinic_id, token_id, event_type, event_time
            )
            VALUES (:clinic_id, :token_id, 'PATIENT_SELF_REGISTERED', NOW())
        """), {"clinic_id": clinic["id"], "token_id": token["id"]})

        raw_session_token = create_session_token()
        expires_at = datetime.now(timezone.utc) + PATIENT_SESSION_DURATION
        db.execute(text("""
            INSERT INTO public.patient_sessions (
                clinic_id, token_id, session_token_hash, expires_at
            )
            VALUES (:clinic_id, :token_id, :session_hash, :expires_at)
        """), {
            "clinic_id": clinic["id"],
            "token_id": token["id"],
            "session_hash": hash_session_token(raw_session_token),
            "expires_at": expires_at,
        })
        db.commit()
        set_patient_cookie(response, raw_session_token)

        return {
            "status": "success",
            "message": "You are registered. Save your access code for future sign-in.",
            "clinic_name": clinic["name"],
            "clinic_code": clinic["clinic_code"],
            "doctor_name": doctor["display_name"],
            "token_date": str(token_date),
            "token_number": token_number,
            "patient_status": token["status"],
            "joined_at": token["joined_at"],
            "patient_access_code": access_code,
            "qr_token": qr_token,
            "voice_reminder_consent": payload.voice_reminder_consent,
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Patient self-registration database error")
        raise HTTPException(status_code=500, detail="Unable to register with this clinic right now.")
    except Exception:
        db.rollback()
        logger.exception("Unexpected patient self-registration error")
        raise HTTPException(status_code=500, detail="Unable to complete patient registration.")


@router.post("/access")
def patient_access(
    payload: PatientAccessRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    # Hash the supplied access code before comparing it.
    access_code_hash = hash_patient_access_code(
        payload.access_code
    )

    try:
        # Verify clinic, date, token number and access code together.
        patient = db.execute(
            text("""
                SELECT
                    t.id AS token_id,
                    t.clinic_id,
                    t.doctor_id,
                    t.token_date,
                    t.token_number,
                    t.status::text AS patient_status,
                    t.joined_at,

                    c.name AS clinic_name,
                    c.clinic_code,

                    d.display_name AS doctor_name

                FROM public.queue_tokens t

                JOIN public.clinics c
                    ON c.id = t.clinic_id

                JOIN public.doctors d
                    ON d.id = t.doctor_id
                   AND d.clinic_id = t.clinic_id

                WHERE LOWER(c.clinic_code) = :clinic_code
                  AND t.token_date = :token_date
                  AND t.token_number = :token_number
                  AND t.access_code_hash = :access_code_hash
                  AND c.is_active = TRUE

                LIMIT 1
            """),
            {
                "clinic_code": payload.clinic_code.lower(),
                "token_date": payload.token_date,
                "token_number": payload.token_number,
                "access_code_hash": access_code_hash,
            }
        ).mappings().one_or_none()

        if patient is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid clinic code, token details or access code."
            )

        # Create a separate patient session.
        raw_session_token = create_session_token()
        session_hash = hash_session_token(raw_session_token)

        expires_at = (
            datetime.now(timezone.utc)
            + PATIENT_SESSION_DURATION
        )

        db.execute(
            text("""
                INSERT INTO public.patient_sessions (
                    clinic_id,
                    token_id,
                    session_token_hash,
                    expires_at
                )
                VALUES (
                    :clinic_id,
                    :token_id,
                    :session_hash,
                    :expires_at
                )
            """),
            {
                "clinic_id": patient["clinic_id"],
                "token_id": patient["token_id"],
                "session_hash": session_hash,
                "expires_at": expires_at,
            }
        )

        db.commit()

        # The raw session token is only sent in an HttpOnly cookie.
        set_patient_cookie(response, raw_session_token)

        return {
            "status": "success",
            "message": "Patient access verified successfully.",
            "patient": {
                "clinic_name": patient["clinic_name"],
                "clinic_code": patient["clinic_code"],
                "doctor_name": patient["doctor_name"],
                "token_date": str(patient["token_date"]),
                "token_number": patient["token_number"],
                "status": patient["patient_status"],
                "joined_at": patient["joined_at"],
            }
        }

    except HTTPException:
        db.rollback()
        raise

    except SQLAlchemyError:
        db.rollback()
        logger.exception("Patient access database error")

        raise HTTPException(
            status_code=500,
            detail="Unable to verify patient access."
        )

@router.post("/qr-access")
def patient_qr_access(
    payload: PatientQRAccessRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    qr_hash = hash_patient_qr_token(payload.qr_token)

    try:
        patient = db.execute(
            text("""
                SELECT t.id AS token_id, t.clinic_id
                FROM public.queue_tokens t
                JOIN public.clinics c ON c.id = t.clinic_id
                WHERE t.qr_access_hash = :qr_hash
                  AND c.is_active = TRUE
                  AND t.token_date =
                      (NOW() AT TIME ZONE c.timezone)::date
                LIMIT 1
            """),
            {"qr_hash": qr_hash},
        ).mappings().one_or_none()

        if patient is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired patient QR code.",
            )

        raw_session_token = create_session_token()
        session_hash = hash_session_token(raw_session_token)
        expires_at = (
            datetime.now(timezone.utc) + PATIENT_SESSION_DURATION
        )

        db.execute(
            text("""
                INSERT INTO public.patient_sessions (
                    clinic_id,
                    token_id,
                    session_token_hash,
                    expires_at
                )
                VALUES (
                    :clinic_id,
                    :token_id,
                    :session_hash,
                    :expires_at
                )
            """),
            {
                "clinic_id": patient["clinic_id"],
                "token_id": patient["token_id"],
                "session_hash": session_hash,
                "expires_at": expires_at,
            },
        )

        db.commit()
        set_patient_cookie(response, raw_session_token)

        return {"status": "success"}

    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to authenticate patient QR code.",
        )

@router.get("/me")
def get_patient_details(
    request: Request,
    db: Session = Depends(get_db),
):
    patient = get_patient_context(request, db)
    # Recover consultations left open from previous clinic days.
    recovered_count = recover_stale_consultations(
        db=db,
        clinic_id=patient["clinic_id"],
    )
    expired_disruption_count = expire_previous_day_disruptions(
        db=db,
        clinic_id=patient["clinic_id"],
    )

    if recovered_count > 0 or expired_disruption_count > 0:
        db.commit()

    if recovered_count > 0:
        # Refresh patient context after a stale consultation was recovered.
        patient = get_patient_context(request, db)

    waiting_ahead = 0
    queue_position = None

    # Count only patients who are still waiting ahead of this token.
    if patient["patient_status"] == "WAITING":
        waiting_ahead = db.execute(
            text("""
                SELECT COUNT(*)
                FROM public.queue_tokens

                WHERE clinic_id = :clinic_id
                  AND doctor_id = :doctor_id
                  AND token_date = :token_date
                  AND status = 'WAITING'
                  AND token_number < :token_number
            """),
            {
                "clinic_id": patient["clinic_id"],
                "doctor_id": patient["doctor_id"],
                "token_date": patient["token_date"],
                "token_number": patient["token_number"],
            }
        ).scalar_one()

        queue_position = waiting_ahead + 1

    # Identify the token currently being served, if any.
    current_consultation = db.execute(
        text("""
            SELECT t.token_number

            FROM public.consultations co

            JOIN public.queue_tokens t
                ON t.id = co.token_id
               AND t.clinic_id = co.clinic_id

            WHERE co.clinic_id = :clinic_id
              AND co.doctor_id = :doctor_id
              AND co.ended_at IS NULL
              AND t.token_date = :token_date

            LIMIT 1
        """),
        {
            "clinic_id": patient["clinic_id"],
            "doctor_id": patient["doctor_id"],
            "token_date": patient["token_date"],
        }
    ).scalar_one_or_none()


    # Prepare the default prediction response.
    prediction = {
        "estimated_wait": None,
        "estimated_wait_min_minutes": None,
        "estimated_wait_max_minutes": None,
        "prediction_status": "NOT_APPLICABLE",
        "prediction_label": "No waiting-time estimate applies.",
        "prediction_sample_count": 0,
        "prediction_generated_at": None,
        "suggested_return_minutes": None,
        "suggested_return_at": None,
    }

    if patient["patient_status"] == "WAITING":

        # Case 1: Queue is paused.
        if patient["queue_paused"]:
            prediction.update({
                "prediction_status": "QUEUE_PAUSED",
                "prediction_label": (
                    "The clinic queue is paused. "
                    "Please wait for an update from the clinic."
                ),
            })

        # Case 2: Nobody is ahead and no consultation is active.
        elif waiting_ahead == 0 and current_consultation is None:
            prediction.update({
                "prediction_status": "NEXT_UP",
                "prediction_label": (
                    "The clinic will call your token when "
                    "the doctor is ready. Please stay available."
                ),
            })

        # Case 3: Patients are ahead or a consultation is active.
        else:
            try:
                prediction = generate_and_store_prediction(
                    db=db,
                    patient=patient,
                    waiting_ahead=waiting_ahead,
                )

                db.commit()

            except Exception:
                db.rollback()

                logger.exception(
                    "Patient waiting-time prediction failed"
                )

                prediction.update({
                    "prediction_status": "TEMPORARILY_UNAVAILABLE",
                    "prediction_label": (
                        "Waiting-time estimate temporarily unavailable."
                    ),
                })

    elif patient["patient_status"] == "IN_CONSULTATION":
        prediction["prediction_status"] = "IN_PROGRESS"
        prediction["prediction_label"] = (
            "Your consultation has started."
        )

    elif patient["patient_status"] == "COMPLETED":
        prediction["prediction_label"] = (
            "Your consultation has been completed."
        )

    elif patient["patient_status"] in ("SKIPPED", "CANCELLED"):
        prediction["prediction_label"] = (
            "Your token is no longer in the active waiting queue."
        )

    # Fetch public active emergencies and custom messages.
    active_disruptions = db.execute(
        text("""
            SELECT
                qd.id::text AS id,
                qd.event_type,
                qd.expected_delay_minutes,
                qd.public_message,
                qd.started_at
            FROM public.queue_disruptions AS qd
            JOIN public.clinics AS c
              ON c.id = qd.clinic_id
            WHERE qd.clinic_id = :clinic_id
              AND qd.status = 'ACTIVE'
              AND (qd.started_at AT TIME ZONE c.timezone)::date =
                  (NOW() AT TIME ZONE c.timezone)::date
            ORDER BY qd.started_at DESC
        """),
        {
            "clinic_id": patient["clinic_id"]
        }
    ).mappings().all()

    return {
        "status": "success",
        "patient": {
            "clinic_name": patient["clinic_name"],
            "clinic_code": patient["clinic_code"],
            "doctor_name": patient["doctor_name"],
            "token_date": str(patient["token_date"]),
            "token_number": patient["token_number"],
            "patient_status": patient["patient_status"],
            "joined_at": patient["joined_at"],
            "voice_reminder_consent": bool(patient["voice_reminder_consent"]),
            "queue_paused": patient["queue_paused"],
            "waiting_ahead": waiting_ahead,
            "queue_position": queue_position,
            "current_serving_token_number": current_consultation,
            "active_disruptions": [
    dict(row) for row in active_disruptions
],
                        **prediction,
        }
    }


@router.post("/voice-reminders/opt-out")
def opt_out_of_voice_reminders(
    request: Request,
    db: Session = Depends(get_db),
):
    """Let an authenticated patient withdraw voice-reminder consent."""
    patient = get_patient_context(request, db)
    try:
        db.execute(text("""
            UPDATE public.queue_tokens
            SET voice_reminder_consent = FALSE,
                voice_reminder_opted_out_at = NOW(),
                updated_at = NOW()
            WHERE id = :token_id AND clinic_id = :clinic_id
        """), {
            "token_id": patient["token_id"],
            "clinic_id": patient["clinic_id"],
        })
        db.execute(text("""
            UPDATE public.ivr_reminders
            SET status = 'SKIPPED',
                last_error = 'Patient withdrew voice-reminder consent',
                updated_at = NOW()
            WHERE token_id = :token_id
              AND clinic_id = :clinic_id
              AND status = 'PENDING'
        """), {
            "token_id": patient["token_id"],
            "clinic_id": patient["clinic_id"],
        })
        db.commit()
        return {"status": "success", "voice_reminder_consent": False}
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Patient voice-reminder opt-out failed")
        raise HTTPException(status_code=500, detail="Unable to update reminder preferences right now.")


@router.post("/logout")
def patient_logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    raw_token = request.cookies.get(PATIENT_COOKIE_NAME)

    if raw_token:
        token_hash = hash_session_token(raw_token)

        try:
            db.execute(
                text("""
                    UPDATE public.patient_sessions

                    SET revoked_at = NOW()

                    WHERE session_token_hash = :token_hash
                      AND revoked_at IS NULL
                """),
                {"token_hash": token_hash}
            )

            db.commit()

        except SQLAlchemyError:
            db.rollback()
            logger.exception("Patient logout database error")

            raise HTTPException(
                status_code=500,
                detail="Unable to terminate patient session."
            )

    response.delete_cookie(
    key=PATIENT_COOKIE_NAME,
    path="/",
    httponly=True,
    secure=COOKIE_SECURE,
    samesite=COOKIE_SAMESITE,
)

    return {
        "status": "success",
        "message": "Patient logged out successfully."
    }

@router.get("/events")
async def patient_queue_events(
    request: Request,
    db: Session = Depends(get_db),
):
    # Authenticate the patient before opening the event stream.
    patient = get_patient_context(request, db)

    raw_token = request.cookies.get(PATIENT_COOKIE_NAME)
    token_hash = hash_session_token(raw_token)
    clinic_id = patient["clinic_id"]

    # Find the most recent event when the connection begins.
    # This prevents old queue events from being replayed.
    def get_initial_cursor():
        with SessionLocal() as session:
            latest = session.execute(
                text("""
                    SELECT id, event_time
                    FROM public.queue_events
                    WHERE clinic_id = :clinic_id
                    ORDER BY event_time DESC, id DESC
                    LIMIT 1
                """),
                {"clinic_id": clinic_id}
            ).mappings().one_or_none()

            if latest is not None:
                return latest["event_time"], str(latest["id"])

            database_time = session.execute(
                text("SELECT NOW()")
            ).scalar_one()

            return (
                database_time,
                "00000000-0000-0000-0000-000000000000"
            )

    last_event_time, last_event_id = await asyncio.to_thread(
        get_initial_cursor
    )

    async def event_generator():
        nonlocal last_event_time, last_event_id

        # Tell the browser to reconnect after temporary disconnections.
        yield "retry: 3000\n\n"

        yield (
            'event: connected\n'
            'data: {"status":"connected"}\n\n'
        )

        last_heartbeat = time.monotonic()

        while not await request.is_disconnected():

            def poll_database():
                with SessionLocal() as session:

                    # Stop streaming if the patient session is revoked,
                    # expires, or the clinic becomes inactive.
                    session_valid = session.execute(
                        text("""
                            SELECT 1

                            FROM public.patient_sessions ps

                            JOIN public.clinics c
                                ON c.id = ps.clinic_id

                            WHERE ps.session_token_hash = :token_hash
                              AND ps.clinic_id = :clinic_id
                              AND ps.revoked_at IS NULL
                              AND ps.expires_at > NOW()
                              AND c.is_active = TRUE

                            LIMIT 1
                        """),
                        {
                            "token_hash": token_hash,
                            "clinic_id": clinic_id,
                        }
                    ).scalar_one_or_none()

                    if session_valid is None:
                        return False, []

                    # Retrieve new events for this clinic only.
                    events = session.execute(
                        text("""
                            SELECT id, event_time

                            FROM public.queue_events

                            WHERE clinic_id = :clinic_id

                              AND (
                                  event_time > :last_event_time

                                  OR (
                                      event_time = :last_event_time
                                      AND id > CAST(
                                          :last_event_id AS UUID
                                      )
                                  )
                              )

                            ORDER BY event_time ASC, id ASC

                            LIMIT 100
                        """),
                        {
                            "clinic_id": clinic_id,
                            "last_event_time": last_event_time,
                            "last_event_id": last_event_id,
                        }
                    ).mappings().all()

                    return True, events

            try:
                session_valid, events = await asyncio.to_thread(
                    poll_database
                )

            except Exception:
                logger.exception(
                    "Patient SSE database polling failed"
                )

                # Keep the stream alive and retry on the next poll.
                await asyncio.sleep(2)
                continue

            if not session_valid:
                yield (
                    'event: session_expired\n'
                    'data: {"status":"expired"}\n\n'
                )
                break

            for event in events:
                last_event_time = event["event_time"]
                last_event_id = str(event["id"])

                # Send only a generic notification.
                # No patient codes, identities, or other patient
                # records are included in the event.
                yield (
                    'event: queue_update\n'
                    'data: {"updated":true}\n\n'
                )

            # Send a heartbeat every 15 seconds when idle.
            if time.monotonic() - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = time.monotonic()

            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )
