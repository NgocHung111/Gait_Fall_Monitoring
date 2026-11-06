from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel


class GaitMetrics(BaseModel):
    step_intervals_ms: list[float]
    cadence_steps_per_minute: float | None
    mean_step_interval_ms: float | None
    variability_cv_percent: float | None


def analyze_gait(
    *,
    step_timestamps_ms: Sequence[int] | None = None,
    step_intervals_ms: Sequence[int] | None = None,
) -> GaitMetrics:
    if step_timestamps_ms is not None and step_intervals_ms is not None:
        raise ValueError("Provide timestamps or intervals, not both.")

    if step_timestamps_ms is not None:
        timestamps = np.asarray(step_timestamps_ms, dtype=np.int64)
        if np.any(timestamps < 0):
            raise ValueError("Step timestamps must be non-negative.")
        if timestamps.size > 1:
            intervals = np.diff(timestamps).astype(np.float64)
        else:
            intervals = np.asarray([], dtype=np.float64)
    elif step_intervals_ms is not None:
        intervals = np.asarray(step_intervals_ms, dtype=np.float64)
    else:
        intervals = np.asarray([], dtype=np.float64)

    if np.any(intervals <= 0):
        raise ValueError("Step intervals must be positive and timestamps increasing.")

    if intervals.size == 0:
        return GaitMetrics(
            step_intervals_ms=[],
            cadence_steps_per_minute=None,
            mean_step_interval_ms=None,
            variability_cv_percent=None,
        )

    mean_interval = float(np.mean(intervals))
    variability = (
        float(np.std(intervals, ddof=0) / mean_interval * 100)
        if intervals.size > 1
        else None
    )
    return GaitMetrics(
        step_intervals_ms=intervals.tolist(),
        cadence_steps_per_minute=60000.0 / mean_interval,
        mean_step_interval_ms=mean_interval,
        variability_cv_percent=variability,
    )
