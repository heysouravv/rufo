from __future__ import annotations

import time
from dataclasses import dataclass

from google.cloud import monitoring_v3

_BILLABLE_INSTANCE_TIME_METRIC = "run.googleapis.com/container/billable_instance_time"


@dataclass(frozen=True)
class UsageWindow:
    billable_instance_seconds: float
    window_hours: float


def get_billable_instance_seconds(
    project_id: str, service_id: str, window_hours: float = 24 * 30
) -> UsageWindow:
    """Real, GCP-billed instance-time for a Cloud Run service over the trailing
    `window_hours` -- the same metric Google itself bills on
    (`billable_instance_time`, a DELTA metric in seconds), not our own
    estimate from measured request duration. Summing every point in a DELTA
    time series over the interval gives the total billed seconds.
    """
    client = monitoring_v3.MetricServiceClient()
    now = time.time()
    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(now)},
            "start_time": {"seconds": int(now - window_hours * 3600)},
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": (
                f'metric.type="{_BILLABLE_INSTANCE_TIME_METRIC}" '
                f'AND resource.labels.service_name="{service_id}"'
            ),
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )

    total_seconds = 0.0
    for series in results:
        for point in series.points:
            total_seconds += point.value.double_value

    return UsageWindow(billable_instance_seconds=total_seconds, window_hours=window_hours)
