
import os
import math
from typing import Sequence

import numpy as np


MODEL_VERSION = "clinicq-monte-carlo-v2"

SIMULATION_RUNS = 500

MIN_ACTIVE_HISTORY_SAMPLES = int(
    os.getenv("PREDICTION_MIN_ACTIVE_HISTORY_SAMPLES", "5")
)

MIN_HISTORY_SAMPLES = int(
    os.getenv("PREDICTION_MIN_HISTORY_SAMPLES", "5")
)


class ActiveConsultationOverrun(Exception):
    """Raised when the active consultation reaches its configured maximum."""

ESTABLISHED_HISTORY_SAMPLES = int(
    os.getenv("PREDICTION_ESTABLISHED_HISTORY_SAMPLES", "20")
)

HISTORY_WINDOW_SIZE = int(
    os.getenv("PREDICTION_HISTORY_LIMIT", "50")
)

MIN_VALID_DURATION_SECONDS = int(
    os.getenv("PREDICTION_MIN_DURATION_SECONDS", "120")
)

MAX_VALID_DURATION_SECONDS = int(
    os.getenv("PREDICTION_MAX_DURATION_SECONDS", "1500")
)


def predict_wait_range(
    historical_durations_seconds: Sequence[float],
    waiting_ahead: int,
    active_elapsed_seconds: float | None,
    default_min_minutes: int,
    default_max_minutes: int,
    active_delay_minutes: int = 0,
    walk_back_buffer_minutes: int = 5,
    simulation_runs: int = SIMULATION_RUNS,
    seed: int | None = None,
) -> dict:
    """
    Estimate a patient's waiting time using Monte Carlo simulation.

    Historical durations are taken from the same clinic and doctor.

    If history is insufficient, use the clinic's configured
    provisional minimum and maximum consultation durations.

    The active structured delay is added once to each simulation.

    The walk-back buffer is used only for the suggested return time.
    """

    # Validate input values.
    if waiting_ahead < 0:
        raise ValueError("waiting_ahead cannot be negative")

    if default_min_minutes <= 0:
        raise ValueError("default_min_minutes must be positive")

    if default_max_minutes < default_min_minutes:
        raise ValueError(
            "default_max_minutes cannot be lower than default_min_minutes"
        )

    if active_delay_minutes < 0:
        raise ValueError("active_delay_minutes cannot be negative")

    if walk_back_buffer_minutes < 0:
        raise ValueError(
            "walk_back_buffer_minutes cannot be negative"
        )

    if simulation_runs < 100:
        raise ValueError(
            "Use at least 100 simulation runs"
        )

    if (
        active_elapsed_seconds is not None
        and active_elapsed_seconds < 0
    ):
        raise ValueError(
            "active_elapsed_seconds cannot be negative"
        )

    if MIN_HISTORY_SAMPLES < 1:
        raise ValueError(
            "PREDICTION_MIN_HISTORY_SAMPLES must be at least 1"
        )

    if MIN_ACTIVE_HISTORY_SAMPLES < 1:
        raise ValueError(
        "PREDICTION_MIN_ACTIVE_HISTORY_SAMPLES must be at least 1"
       )

    if ESTABLISHED_HISTORY_SAMPLES <= MIN_HISTORY_SAMPLES:
        raise ValueError(
            "Established-history threshold must exceed minimum history"
        )

    # Keep only plausible, positive consultation durations.
    valid_history = [
        float(duration)
        for duration in historical_durations_seconds
        if math.isfinite(float(duration))
        and MIN_VALID_DURATION_SECONDS
        <= float(duration)
        <= MAX_VALID_DURATION_SECONDS
    ]

    # Apply a configurable recent-history window.
    valid_history = valid_history[:HISTORY_WINDOW_SIZE]

    history_count = len(valid_history)

    # Decide which data source to use.
    if history_count < MIN_HISTORY_SAMPLES:
        data_status = "PRELIMINARY"
        duration_source = "CLINIC_DEFAULTS"

    elif history_count < ESTABLISHED_HISTORY_SAMPLES:
        data_status = "BUILDING_HISTORY"
        duration_source = "CLINIC_HISTORY"

    else:
        data_status = "ESTABLISHED"
        duration_source = "CLINIC_HISTORY"

    rng = np.random.default_rng(seed)

    # Determine the number of consultations contributing to the wait.
    number_of_waiting_consultations = waiting_ahead

    has_active_consultation = (
        active_elapsed_seconds is not None
    )

    def sample_durations(number_of_samples: int) -> np.ndarray:
        if number_of_samples == 0:
            return np.zeros(simulation_runs)

        if duration_source == "CLINIC_HISTORY":
            return rng.choice(
                valid_history,
                size=number_of_samples,
                replace=True,
            )

        return rng.uniform(
            low=default_min_minutes * 60,
            high=default_max_minutes * 60,
            size=number_of_samples,
        )


    # Start with the remaining time in the active consultation.
    simulated_waits = np.zeros(simulation_runs)
    active_duration_source = None

    if has_active_consultation:
        elapsed_seconds = float(active_elapsed_seconds)

        provisional_min_seconds = default_min_minutes * 60
        provisional_max_seconds = default_max_minutes * 60

        # Stop numerical prediction when the configured maximum is reached.
        if elapsed_seconds >= provisional_max_seconds:
            raise ActiveConsultationOverrun(
                "Active consultation reached its configured maximum."
            )

        # Historical consultations must have lasted longer than
        # the current consultation's elapsed time.
        eligible_active_history = [
            duration
            for duration in valid_history
            if duration > elapsed_seconds
        ]

        # Use historical data only when enough qualifying records exist.
        if len(eligible_active_history) >= MIN_ACTIVE_HISTORY_SAMPLES:
            active_duration_source = "CLINIC_HISTORY"

            active_total_duration = rng.choice(
                eligible_active_history,
                size=simulation_runs,
                replace=True,
            )

        else:
            # Fall back to the clinic's provisional duration settings.
            # Only sample possible total durations beyond elapsed time.
            active_duration_source = "CLINIC_DEFAULTS"

            active_lower_seconds = max(
                provisional_min_seconds,
                elapsed_seconds,
            )

            active_total_duration = rng.uniform(
                low=active_lower_seconds,
                high=provisional_max_seconds,
                size=simulation_runs,
            )

        # Convert possible total durations into remaining durations.
        active_remaining = (
            active_total_duration - elapsed_seconds
        )

        simulated_waits += active_remaining


    # Add one sampled consultation duration for each patient ahead.
    if number_of_waiting_consultations > 0:
        waiting_durations = sample_durations(
            simulation_runs * number_of_waiting_consultations
        ).reshape(
            simulation_runs,
            number_of_waiting_consultations,
        )

        simulated_waits += waiting_durations.sum(axis=1)

    # Add every active structured delay once to the total wait.
    simulated_waits += active_delay_minutes * 60

    # Calculate the middle 50% range of simulated outcomes.
    p25, p75 = np.percentile(
        simulated_waits,
        [25, 75],
    )

    wait_p25_seconds = max(0, math.ceil(float(p25)))
    wait_p75_seconds = max(
        wait_p25_seconds,
        math.ceil(float(p75)),
    )

    # Suggested return time uses the lower estimate,
    # minus the clinic's walk-back buffer.
    estimated_lower_minutes = math.floor(
        wait_p25_seconds / 60
    )

    suggested_return_minutes = max(
        0,
        estimated_lower_minutes - walk_back_buffer_minutes,
    )

    return {
        "wait_p25_seconds": wait_p25_seconds,
        "wait_p75_seconds": wait_p75_seconds,
        "sample_count": history_count,
        "data_status": data_status,
        "duration_source":duration_source,
        "active_duration_source": active_duration_source,
        "model_version": MODEL_VERSION,
        "simulation_runs": simulation_runs,
        "suggested_return_minutes": suggested_return_minutes,
        "walk_back_buffer_minutes": walk_back_buffer_minutes,
    }
