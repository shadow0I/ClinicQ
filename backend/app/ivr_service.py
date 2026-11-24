"""Clinic-scoped, opt-in IVR reminder scheduling and Exotel dispatch."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import text

from app.db import SessionLocal

logger = logging.getLogger("clinicq.ivr")


def setting(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def provider_configuration() -> dict:
    provider = setting("IVR_PROVIDER", "exotel").lower()
    required = {
        "EXOTEL_API_KEY": setting("EXOTEL_API_KEY"),
        "EXOTEL_API_TOKEN": setting("EXOTEL_API_TOKEN"),
        "EXOTEL_ACCOUNT_SID": setting("EXOTEL_ACCOUNT_SID"),
        "EXOTEL_CALLER_ID": setting("EXOTEL_CALLER_ID"),
        "EXOTEL_IVR_URL": setting("EXOTEL_IVR_URL"),
        "PUBLIC_API_BASE_URL": setting("PUBLIC_API_BASE_URL").rstrip("/"),
        "IVR_WEBHOOK_SECRET": setting("IVR_WEBHOOK_SECRET"),
    }
    missing = [] if provider == "mock" else [key for key, value in required.items() if not value]
    if provider not in {"mock", "exotel"}:
        missing = ["IVR_PROVIDER (must be mock or exotel)"]
    if provider == "exotel":
        if required["PUBLIC_API_BASE_URL"] and not required["PUBLIC_API_BASE_URL"].startswith("https://"):
            missing.append("PUBLIC_API_BASE_URL (must use HTTPS)")
        if required["IVR_WEBHOOK_SECRET"] and len(required["IVR_WEBHOOK_SECRET"]) < 32:
            missing.append("IVR_WEBHOOK_SECRET (use at least 32 characters)")
    api_host = setting("EXOTEL_API_HOST", "api.in.exotel.com")
    if provider == "exotel" and api_host not in {"api.in.exotel.com", "api.exotel.com"}:
        missing.append("EXOTEL_API_HOST (must be api.in.exotel.com or api.exotel.com)")
    configured = provider == "mock" or (provider == "exotel" and not missing)
    return {
        "provider": provider,
        "configured": configured,
        "missing": missing,
        "api_key": required["EXOTEL_API_KEY"],
        "api_token": required["EXOTEL_API_TOKEN"],
        "account_sid": required["EXOTEL_ACCOUNT_SID"],
        "caller_id": required["EXOTEL_CALLER_ID"],
        "ivr_url": required["EXOTEL_IVR_URL"],
        "public_api_base_url": required["PUBLIC_API_BASE_URL"],
        "webhook_secret": required["IVR_WEBHOOK_SECRET"],
        "api_host": api_host,
    }


def public_provider_status() -> dict:
    config = provider_configuration()
    return {
        "provider": config["provider"],
        "configured": config["configured"],
        "missing": config["missing"],
        "mode": "simulation" if config["provider"] == "mock" else "live",
    }


def normalise_indian_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return f"+{digits}"
    raise ValueError("A valid Indian mobile number is required for a voice reminder.")


def _callback_url(config: dict) -> str:
    return (
        f"{config['public_api_base_url']}/api/ivr/webhooks/exotel"
        f"?token={quote(config['webhook_secret'], safe='')}"
    )


def _start_exotel_call(config: dict, destination: str, reminder_id: str) -> str:
    if config["provider"] == "mock":
        return f"mock:{uuid4()}"

    if config["api_host"] not in {"api.in.exotel.com", "api.exotel.com"}:
        raise RuntimeError("EXOTEL_API_HOST must be api.in.exotel.com or api.exotel.com.")

    url = (
        f"https://{config['api_host']}/v1/Accounts/"
        f"{config['account_sid']}/Calls/connect"
    )
    fields = {
        # Voice V1 Connect Flow calls From, then runs the configured App Bazaar IVR.
        "From": destination,
        "CallerId": config["caller_id"],
        "Url": config["ivr_url"],
        "CallType": "trans",
        "TimeLimit": "90",
        "TimeOut": "25",
        "StatusCallback": _callback_url(config),
        # Exotel's Connect-with-Flow endpoint rejects StatusCallbackEvents
        # when a Flow Url is supplied. The default callback is terminal-only.
        "StatusCallbackContentType": "application/json",
        # Opaque identifier only: no patient name, number or health data.
        "CustomField": reminder_id,
    }
    credentials = base64.b64encode(
        f"{config['api_key']}:{config['api_token']}".encode("utf-8")
    ).decode("ascii")
    request = Request(
        url,
        data=urlencode(fields).encode("utf-8"),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(131072).decode("utf-8", errors="replace")
            payload = json.loads(body) if body else {}
    except HTTPError as exc:
        # Do not log credentials or full response bodies that could contain PII.
        raise RuntimeError(f"Exotel call request failed with HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Unable to reach Exotel ({type(exc).__name__}).") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Exotel returned an unreadable response.") from exc

    call = payload.get("Call") or payload.get("call") or payload
    call_sid = call.get("Sid") or call.get("sid") or call.get("CallSid") or call.get("call_sid")
    if not call_sid:
        raise RuntimeError("Exotel did not return a call identifier.")
    return str(call_sid)


def enqueue_eligible_reminders(db) -> int:
    """Create one idempotent reminder when a consenting patient has 1–3 waiting ahead."""
    result = db.execute(text("""
        WITH ranked_waiting AS (
            SELECT t.clinic_id, t.id AS token_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY t.clinic_id, t.doctor_id, t.token_date
                       ORDER BY t.token_number
                   ) - 1 AS waiting_ahead
            FROM public.queue_tokens t
            JOIN public.clinics c ON c.id = t.clinic_id
            WHERE t.status = 'WAITING'
              AND c.is_active = TRUE
              AND c.voice_reminders_enabled = TRUE
              AND c.queue_paused = FALSE
              AND t.token_date = (NOW() AT TIME ZONE c.timezone)::date
        )
        INSERT INTO public.ivr_reminders (
            clinic_id, token_id, reminder_type, status,
            scheduled_at, created_at, updated_at
        )
        SELECT rw.clinic_id, rw.token_id, 'THREE_AHEAD', 'PENDING', NOW(), NOW(), NOW()
        FROM ranked_waiting rw
        JOIN public.queue_tokens t
          ON t.id = rw.token_id AND t.clinic_id = rw.clinic_id
        WHERE rw.waiting_ahead BETWEEN 1 AND 3
          AND t.voice_reminder_consent = TRUE
          AND t.mobile_number IS NOT NULL
          AND t.mobile_number ~ '^[6-9][0-9]{9}$'
        ON CONFLICT (clinic_id, token_id, reminder_type) DO NOTHING
    """))
    inserted = max(result.rowcount or 0, 0)
    db.commit()
    return inserted


def _set_status(db, reminder_id: str, status: str, **fields) -> None:
    allowed = {
        "provider_call_sid", "last_error", "dispatched_at", "completed_at",
        "patient_response", "scheduled_at",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    updates.update({"status": status, "updated_at": datetime.now(timezone.utc), "reminder_id": reminder_id})
    assignments = [f"{key} = :{key}" for key in updates if key != "reminder_id"]
    db.execute(text(
        "UPDATE public.ivr_reminders SET " + ", ".join(assignments) + " WHERE id = :reminder_id"
    ), updates)
    db.commit()


def dispatch_pending_reminders(limit: int = 5) -> int:
    """Claim reminders safely across workers and place at most one automatic call."""
    config = provider_configuration()
    if not config["configured"]:
        # Don't queue reminders in an environment that cannot deliver or simulate them.
        return 0

    with SessionLocal() as db:
        enqueue_eligible_reminders(db)
        claimed = db.execute(text("""
            SELECT r.id::text AS reminder_id,
                   t.mobile_number,
                   t.status::text AS token_status,
                   t.voice_reminder_consent,
                   c.voice_reminders_enabled,
                   c.queue_paused
            FROM public.ivr_reminders r
            JOIN public.queue_tokens t
              ON t.id = r.token_id AND t.clinic_id = r.clinic_id
            JOIN public.clinics c ON c.id = r.clinic_id
            WHERE r.status = 'PENDING'
              AND r.scheduled_at <= NOW()
              AND r.attempt_count < 3
            ORDER BY r.scheduled_at, r.created_at
            LIMIT :limit
            FOR UPDATE OF r SKIP LOCKED
        """), {"limit": limit}).mappings().all()

        ready = []
        for row in claimed:
            rid = row["reminder_id"]
            if row["token_status"] != "WAITING" or not row["voice_reminder_consent"] or not row["voice_reminders_enabled"]:
                db.execute(text("UPDATE public.ivr_reminders SET status='SKIPPED', updated_at=NOW() WHERE id=:id"), {"id": rid})
            elif row["queue_paused"]:
                db.execute(text("""
                    UPDATE public.ivr_reminders
                    SET scheduled_at=NOW() + INTERVAL '2 minutes', updated_at=NOW()
                    WHERE id=:id
                """), {"id": rid})
            else:
                db.execute(text("""
                    UPDATE public.ivr_reminders
                    SET status='DISPATCHING', attempt_count=attempt_count+1, updated_at=NOW()
                    WHERE id=:id
                """), {"id": rid})
                ready.append({"reminder_id": rid, "mobile_number": row["mobile_number"]})
        db.commit()

    sent = 0
    for item in ready:
        rid = item["reminder_id"]
        try:
            with SessionLocal() as db:
                eligible = db.execute(text("""
                    SELECT t.mobile_number
                    FROM public.ivr_reminders r
                    JOIN public.queue_tokens t
                      ON t.id=r.token_id AND t.clinic_id=r.clinic_id
                    JOIN public.clinics c ON c.id=r.clinic_id
                    WHERE r.id=:id AND r.status='DISPATCHING'
                      AND t.status='WAITING'
                      AND t.voice_reminder_consent=TRUE
                      AND c.voice_reminders_enabled=TRUE
                      AND c.queue_paused=FALSE
                """), {"id": rid}).scalar_one_or_none()
                if eligible is None:
                    _set_status(db, rid, "SKIPPED")
                    continue
                destination_number = eligible
            destination = normalise_indian_mobile(destination_number)
            call_sid = _start_exotel_call(config, destination, rid)
            with SessionLocal() as db:
                if call_sid.startswith("mock:"):
                    _set_status(
                        db, rid, "COMPLETED", provider_call_sid=call_sid,
                        dispatched_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                    )
                else:
                    _set_status(
                        db, rid, "SUBMITTED", provider_call_sid=call_sid,
                        dispatched_at=datetime.now(timezone.utc), last_error=None,
                    )
            sent += 1
        except Exception as exc:
            logger.warning("IVR reminder dispatch failed for %s: %s", rid, type(exc).__name__)
            with SessionLocal() as db:
                _set_status(
                    db, rid, "FAILED", last_error=str(exc)[:250],
                    completed_at=datetime.now(timezone.utc),
                )
    return sent


def update_exotel_callback(payload: dict) -> bool:
    """Apply an Exotel call status callback idempotently."""
    call = payload.get("Call") if isinstance(payload.get("Call"), dict) else payload
    call_sid = call.get("CallSid") or call.get("Sid") or call.get("sid")
    status_value = str(call.get("Status") or call.get("status") or "").lower().replace("_", "-")
    custom_field = call.get("CustomField") or call.get("custom_field")
    if not call_sid and not custom_field:
        return False

    status_map = {
        "queued": "SUBMITTED",
        "in-progress": "IN_PROGRESS",
        "completed": "COMPLETED",
        "failed": "FAILED",
        "busy": "FAILED",
        "no-answer": "FAILED",
        "canceled": "FAILED",
        "cancelled": "FAILED",
    }
    mapped = status_map.get(status_value)
    if not mapped:
        return False

    with SessionLocal() as db:
        row = db.execute(text("""
            SELECT id::text AS id FROM public.ivr_reminders
            WHERE (CAST(:sid AS TEXT) IS NOT NULL AND provider_call_sid=CAST(:sid AS TEXT))
               OR (CAST(:custom AS TEXT) IS NOT NULL AND id::text=CAST(:custom AS TEXT))
            LIMIT 1
        """), {"sid": call_sid, "custom": custom_field}).mappings().one_or_none()
        if row is None:
            return False
        terminal = mapped in {"COMPLETED", "FAILED"}
        db.execute(text("""
            UPDATE public.ivr_reminders
            SET status=:status,
                completed_at=CASE WHEN :terminal THEN NOW() ELSE completed_at END,
                updated_at=NOW()
            WHERE id=:id
        """), {"status": mapped, "terminal": terminal, "id": row["id"]})
        db.commit()
    return True


def record_ivr_response(digits: str, reminder_id: str | None = None, call_sid: str | None = None) -> bool:
    """Map a keypad response to its reminder using its opaque reminder ID or Exotel CallSid."""
    if not reminder_id and not call_sid:
        return False
    response_value = {"1": "CONFIRMED", "2": "CALLBACK_REQUESTED"}.get(str(digits).strip(), "OTHER")
    with SessionLocal() as db:
        result = db.execute(text("""
            UPDATE public.ivr_reminders
            SET patient_response=:response, updated_at=NOW()
            WHERE status NOT IN ('FAILED','SKIPPED')
              AND patient_response IS NULL
              AND (
                (CAST(:reminder_id AS TEXT) IS NOT NULL AND id::text=CAST(:reminder_id AS TEXT))
                OR (CAST(:call_sid AS TEXT) IS NOT NULL AND provider_call_sid=CAST(:call_sid AS TEXT))
              )
            RETURNING id
        """), {
            "response": response_value,
            "reminder_id": reminder_id,
            "call_sid": call_sid,
        }).first()
        if not result:
            db.rollback()
            return False
        db.commit()
    return True


async def ivr_worker_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(dispatch_pending_reminders)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("IVR reminder worker cycle failed")
        await asyncio.sleep(15)
