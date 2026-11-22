
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session


from app.prediction import (
    HISTORY_WINDOW_SIZE,
    MIN_VALID_DURATION_SECONDS,
    MAX_VALID_DURATION_SECONDS,
    ActiveConsultationOverrun,
    MODEL_VERSION,
    predict_wait_range,
)

def generate_and_store_prediction(
    db: Session,
    patient,
    waiting_ahead: int,
) -> dict:
    """
    Generate a patient-specific waiting-time forecast.

    Uses completed consultations from the same clinic and doctor.
    Saves forecasts in prediction_snapshots for later evaluation.
    """

    # A paused queue has no reliable service-time forecast.
    if patient["queue_paused"]:
        return {
            "estimated_wait": None,
            "estimated_wait_min_minutes": None,
            "estimated_wait_max_minutes": None,
            "prediction_status": "QUEUE_PAUSED",
            "prediction_label": "Queue paused — estimate temporarily unavailable.",
            "prediction_sample_count": 0,
            "suggested_return_minutes": None,
            "suggested_return_at": None,
            "model_version": "clinicq-monte-carlo-v1",
        }

    # 1. Retrieve this clinic's prediction settings.
    clinic = db.execute(
        text("""
            SELECT
                timezone,
                default_min_minutes,
                default_max_minutes,
                walk_back_buffer_minutes

            FROM public.clinics

            WHERE id = :clinic_id
              AND is_active = TRUE
        """),
        {"clinic_id": patient["clinic_id"]}
    ).mappings().one_or_none()

    if clinic is None:
        raise ValueError("Active clinic settings were not found.")

    # 2. Retrieve recent completed consultation durations.
    # Only use the same clinic and doctor.
    history = db.execute(
        text("""
            SELECT
                FLOOR(
                    EXTRACT(
                        EPOCH FROM (co.ended_at - co.started_at)
                    )
                )::INTEGER AS duration_seconds

            FROM public.consultations co

            WHERE co.clinic_id = :clinic_id
              AND co.doctor_id = :doctor_id
              AND co.ended_at IS NOT NULL
              AND co.outcome = 'COMPLETED'
              AND co.ended_at >= NOW() - INTERVAL '180 days'

              AND EXTRACT(
    EPOCH FROM (co.ended_at - co.started_at)
) BETWEEN :min_history_duration_seconds
  AND :max_history_duration_seconds
            ORDER BY co.ended_at DESC

            LIMIT :history_limit
        """),
        {
    "clinic_id": patient["clinic_id"],
    "doctor_id": patient["doctor_id"],
    "history_limit": HISTORY_WINDOW_SIZE,
    "min_history_duration_seconds": MIN_VALID_DURATION_SECONDS,
    "max_history_duration_seconds": MAX_VALID_DURATION_SECONDS,
}
    ).scalars().all()

    # 3. Find the active consultation's elapsed time.
    active_elapsed_seconds = db.execute(
        text("""
            SELECT
                GREATEST(
                    0,
                    FLOOR(
                        EXTRACT(EPOCH FROM (NOW() - started_at))
                    )
                )::INTEGER

            FROM public.consultations

            WHERE clinic_id = :clinic_id
              AND doctor_id = :doctor_id
              AND ended_at IS NULL

            ORDER BY started_at DESC

            LIMIT 1
        """),
        {
            "clinic_id": patient["clinic_id"],
            "doctor_id": patient["doctor_id"],
        }
    ).scalar_one_or_none()

    # 4. Sum active structured delays once.
    active_delay_minutes = db.execute(
        text("""
            SELECT COALESCE(
                SUM(qd.expected_delay_minutes),
                0
            )::INTEGER

            FROM public.queue_disruptions AS qd
            JOIN public.clinics AS c
              ON c.id = qd.clinic_id

            WHERE qd.clinic_id = :clinic_id
              AND qd.status = 'ACTIVE'
              AND (qd.started_at AT TIME ZONE c.timezone)::date =
                  (NOW() AT TIME ZONE c.timezone)::date
        """),
        {"clinic_id": patient["clinic_id"]}
    ).scalar_one()

        # 5. Run the statistical prediction.
    try:
        prediction = predict_wait_range(
            historical_durations_seconds=history,
            waiting_ahead=waiting_ahead,
            active_elapsed_seconds=active_elapsed_seconds,
            default_min_minutes=clinic["default_min_minutes"],
            default_max_minutes=clinic["default_max_minutes"],
            active_delay_minutes=active_delay_minutes,
            walk_back_buffer_minutes=clinic["walk_back_buffer_minutes"],
            simulation_runs=500,
            seed=int(str(patient["token_id"]).replace("-", "")[:8], 16),
        )

    except ActiveConsultationOverrun:
        return {
            "estimated_wait": None,
            "estimated_wait_min_minutes": None,
            "estimated_wait_max_minutes": None,
            "wait_p25_seconds": None,
            "wait_p75_seconds": None,
            "prediction_status": "DOCTOR_TAKING_LONGER",
            "prediction_label": (
                "The doctor is taking longer than usual. "
                "Please stay available."
            ),
            "prediction_sample_count": len(history),
            "prediction_generated_at": None,
            "suggested_return_minutes": None,
            "suggested_return_at": None,
            "model_version": MODEL_VERSION,
        }

    p25 = prediction["wait_p25_seconds"]
    p75 = prediction["wait_p75_seconds"]

    lower_minutes = math.floor(p25 / 60)
    upper_minutes = math.ceil(p75 / 60)

    if upper_minutes == 0:
        estimated_wait = "Ready when the doctor is available"
    else:
        estimated_wait = (
            f"{lower_minutes}–{upper_minutes} minutes"
        )

    # 6. Calculate the suggested return time.
    suggested_return_minutes = prediction[
        "suggested_return_minutes"
    ]

    clinic_timezone = ZoneInfo(clinic["timezone"])

    suggested_return_at = (
        datetime.now(clinic_timezone)
        + timedelta(minutes=suggested_return_minutes)
    )

    # 7. Check the previous snapshot to avoid excessive duplicates.

    # 7. Avoid creating duplicate snapshots on every refresh.
    latest_snapshot = db.execute(
        text("""
            SELECT
                generated_at,
                wait_p25_seconds,
                wait_p75_seconds,
                sample_count,
                data_status

            FROM public.prediction_snapshots

            WHERE clinic_id = :clinic_id
              AND token_id = :token_id

            ORDER BY generated_at DESC, id DESC

            LIMIT 1
        """),
        {
            "clinic_id": patient["clinic_id"],
            "token_id": patient["token_id"],
        }
    ).mappings().one_or_none()

    now_utc = datetime.now(timezone.utc)
    snapshot_generated_at = None

    should_create_snapshot = latest_snapshot is None

    if latest_snapshot is not None:
        materially_changed = (
            abs(
                latest_snapshot["wait_p25_seconds"] - p25
            ) >= 60
            or abs(
                latest_snapshot["wait_p75_seconds"] - p75
            ) >= 60
            or latest_snapshot["sample_count"]
                != prediction["sample_count"]
            or latest_snapshot["data_status"]
                != prediction["data_status"]
        )

        snapshot_is_stale = (
            latest_snapshot["generated_at"]
            <= now_utc - timedelta(seconds=120)
        )

        should_create_snapshot = (
            materially_changed or snapshot_is_stale
        )

    if should_create_snapshot:
        forecast_kind = (
            "INITIAL"
            if latest_snapshot is None
            else "UPDATED"
        )

        snapshot_generated_at = db.execute(
            text("""
                INSERT INTO public.prediction_snapshots (
                    clinic_id,
                    token_id,
                    wait_p25_seconds,
                    wait_p75_seconds,
                    sample_count,
                    model_version,
                    data_status,
                    forecast_kind
                )

                VALUES (
                    :clinic_id,
                    :token_id,
                    :p25,
                    :p75,
                    :sample_count,
                    :model_version,
                    :data_status,
                    :forecast_kind
                )

                RETURNING generated_at
            """),
            {
                "clinic_id": patient["clinic_id"],
                "token_id": patient["token_id"],
                "p25": p25,
                "p75": p75,
                "sample_count": prediction["sample_count"],
                "model_version": prediction["model_version"],
                "data_status": prediction["data_status"],
                "forecast_kind": forecast_kind,
            }
        ).scalar_one()

    else:
        snapshot_generated_at = latest_snapshot["generated_at"]


    # 8. Describe the maturity of the prediction.
    labels = {
        "PRELIMINARY": (
            "Preliminary — limited clinic history."
        ),
        "BUILDING_HISTORY": (
            "Building history — based on early clinic data."
        ),
        "ESTABLISHED": (
            "Based on established clinic consultation history."
        ),
    }

    return {
        "estimated_wait": estimated_wait,
        "estimated_wait_min_minutes": lower_minutes,
        "estimated_wait_max_minutes": upper_minutes,
        "wait_p25_seconds": p25,
        "wait_p75_seconds": p75,
        "prediction_status": prediction["data_status"],
        "prediction_label": labels[prediction["data_status"]],
        "prediction_sample_count": prediction["sample_count"],
        "prediction_generated_at": snapshot_generated_at,
        "suggested_return_minutes": suggested_return_minutes,
        "suggested_return_at": suggested_return_at,
        "model_version": prediction["model_version"],
    }
