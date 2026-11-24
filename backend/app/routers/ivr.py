import hmac
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_staff
from app.ivr_service import (
    provider_configuration,
    public_provider_status,
    record_ivr_response,
    update_exotel_callback,
)

logger = logging.getLogger("clinicq.ivr.routes")
router = APIRouter(prefix="/api", tags=["IVR Reminders"])


class ReminderSettingsPayload(BaseModel):
    enabled: bool


def _require_owner(staff: dict) -> None:
    if staff.get("role") != "OWNER":
        raise HTTPException(status_code=403, detail="Only a clinic owner can change IVR settings.")


def _verify_webhook_token(token: str | None) -> None:
    expected = provider_configuration()["webhook_secret"]
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid IVR webhook token.")


@router.get("/staff/ivr/reminders")
def list_ivr_reminders(
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT r.id::text AS id,
               t.token_number,
               r.status,
               r.patient_response,
               r.attempt_count,
               (COALESCE(r.provider_call_sid, '') LIKE 'mock:%') AS is_simulation,
               r.last_error,
               r.created_at,
               r.dispatched_at,
               r.completed_at
        FROM public.ivr_reminders r
        JOIN public.queue_tokens t
          ON t.id = r.token_id AND t.clinic_id = r.clinic_id
        WHERE r.clinic_id = :clinic_id
        ORDER BY r.created_at DESC
        LIMIT 40
    """), {"clinic_id": staff["clinic_id"]}).mappings().all()
    return {
        "status": "success",
        "provider": public_provider_status(),
        "reminders": [dict(row) for row in rows],
    }


@router.get("/staff/ivr/settings")
def get_ivr_settings(
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    enabled = db.execute(text("""
        SELECT voice_reminders_enabled
        FROM public.clinics WHERE id = :clinic_id
    """), {"clinic_id": staff["clinic_id"]}).scalar_one_or_none()
    if enabled is None:
        raise HTTPException(status_code=404, detail="Clinic not found.")
    return {
        "status": "success",
        "enabled": bool(enabled),
        "provider": public_provider_status(),
        "can_manage": staff.get("role") == "OWNER",
    }


@router.post("/staff/ivr/settings")
def update_ivr_settings(
    payload: ReminderSettingsPayload,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    _require_owner(staff)
    provider = public_provider_status()
    if payload.enabled and not provider["configured"]:
        raise HTTPException(
            status_code=409,
            detail="IVR provider is not configured. Add the required server environment variables first.",
        )
    db.execute(text("""
        UPDATE public.clinics
        SET voice_reminders_enabled = :enabled
        WHERE id = :clinic_id
    """), {"enabled": payload.enabled, "clinic_id": staff["clinic_id"]})
    db.commit()
    return {"status": "success", "enabled": payload.enabled, "provider": provider}


@router.post("/staff/ivr/reminders/{reminder_id}/retry")
def retry_ivr_reminder(
    reminder_id: UUID,
    staff=Depends(get_current_staff),
    db: Session = Depends(get_db),
):
    provider = public_provider_status()
    if not provider["configured"]:
        raise HTTPException(status_code=409, detail="IVR provider is not configured.")
    result = db.execute(text("""
        UPDATE public.ivr_reminders r
        SET status = 'PENDING',
            scheduled_at = NOW(),
            completed_at = NULL,
            dispatched_at = NULL,
            provider_call_sid = NULL,
            last_error = NULL,
            updated_at = NOW()
        FROM public.queue_tokens t, public.clinics c
        WHERE r.id = :reminder_id
          AND r.clinic_id = :clinic_id
          AND t.id = r.token_id
          AND t.clinic_id = r.clinic_id
          AND c.id = r.clinic_id
          AND r.status = 'FAILED'
          AND r.attempt_count < 3
          AND t.status = 'WAITING'
          AND t.voice_reminder_consent = TRUE
          AND c.voice_reminders_enabled = TRUE
          AND c.queue_paused = FALSE
        RETURNING r.id
    """), {"reminder_id": str(reminder_id), "clinic_id": staff["clinic_id"]}).first()
    if not result:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This reminder cannot be retried. Check its status, consent and remaining attempts.",
        )
    db.commit()
    return {"status": "success", "message": "Reminder queued for retry."}


@router.post("/ivr/webhooks/exotel")
async def exotel_status_webhook(
    request: Request,
    token: str | None = Query(default=None),
):
    _verify_webhook_token(token)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid callback payload.")
    updated = update_exotel_callback(payload)
    return {"status": "ok", "updated": updated}


@router.post("/ivr/webhooks/response")
async def exotel_dtmf_webhook(
    request: Request,
    token: str | None = Query(default=None),
):
    """Accept a DTMF response from a configured Exotel Gather/Passthru flow."""
    _verify_webhook_token(token)
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            payload = {}

    if not isinstance(payload, dict):
        payload = {}
    custom_field = (
        payload.get("custom_field")
        or payload.get("CustomField")
        or payload.get("customField")
        or request.query_params.get("custom_field")
    )
    call_sid = (
        payload.get("CallSid")
        or payload.get("call_sid")
        or payload.get("Sid")
        or request.query_params.get("CallSid")
    )
    digits = (
        payload.get("digits")
        or payload.get("DigitsPressed")
        or payload.get("Digits")
        or request.query_params.get("DigitsPressed")
        or request.query_params.get("choice")
    )
    reminder_id = None
    if custom_field:
        try:
            reminder_id = str(UUID(str(custom_field)))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid reminder reference.")
    if not digits:
        raise HTTPException(status_code=400, detail="IVR response was not provided.")
    if not reminder_id and not call_sid:
        raise HTTPException(status_code=400, detail="Call or reminder reference was not provided.")
    updated = record_ivr_response(str(digits), reminder_id=reminder_id, call_sid=str(call_sid) if call_sid else None)
    if not updated:
        raise HTTPException(status_code=404, detail="Reminder not found.")

    spoken_response = {
        "1": "Thank you. Your confirmation has been recorded. Goodbye.",
        "2": "Your request for a call from reception has been recorded. Goodbye.",
    }.get(str(digits).strip(), "Your response has been recorded. Goodbye.")
    exoml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response><Say>' + spoken_response + '</Say><Hangup/></Response>'
    )
    return Response(content=exoml, media_type="text/xml")
