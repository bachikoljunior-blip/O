from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Tokyo"
DEFAULT_BOUNDARY_TOLERANCE = timedelta(minutes=2)
SOFT_STOP_LEAD = timedelta(minutes=10)
HARD_STOP_LEAD = timedelta(minutes=5)


@dataclass(frozen=True)
class HourlyExecutionWindow:
    scheduler_fired_at: datetime
    platform_scheduled_start: datetime | None
    scheduled_start: datetime
    next_scheduled_start: datetime
    soft_stop: datetime
    hard_stop: datetime
    scheduled_start_resolution: str
    boundary_tolerance: timedelta

    def to_dict(self) -> dict[str, object]:
        return {
            "scheduler_fired_at": self.scheduler_fired_at.isoformat(),
            "platform_scheduled_start": (
                self.platform_scheduled_start.isoformat()
                if self.platform_scheduled_start is not None
                else None
            ),
            "schedule_metadata_available": self.platform_scheduled_start is not None,
            "scheduled_start": self.scheduled_start.isoformat(),
            "next_scheduled_start": self.next_scheduled_start.isoformat(),
            "soft_stop": self.soft_stop.isoformat(),
            "hard_stop": self.hard_stop.isoformat(),
            "scheduled_start_resolution": self.scheduled_start_resolution,
            "boundary_tolerance_seconds": self.boundary_tolerance.total_seconds(),
            "fired_before_scheduled_start": self.scheduler_fired_at < self.scheduled_start,
            "seconds_from_scheduled_start": (
                self.scheduler_fired_at - self.scheduled_start
            ).total_seconds(),
            "execution_window_open_at_fire": self.scheduler_fired_at <= self.hard_stop,
        }


def resolve_hourly_execution_window(
    scheduler_fired_at: datetime,
    *,
    platform_scheduled_start: datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    boundary_tolerance: timedelta = DEFAULT_BOUNDARY_TOLERANCE,
) -> HourlyExecutionWindow:
    """Resolve an hourly run window without misclassifying small early fires.

    Platform-provided scheduled metadata is authoritative. If it is unavailable,
    a trigger no more than ``boundary_tolerance`` before or after an hourly
    boundary is assigned to that nearest boundary. Other triggers fall back to
    the current hour. This makes a 10:59:30 trigger belong to the 11:00 run while
    preserving ordinary delayed starts such as 11:15 as the 11:00 run.
    """

    if scheduler_fired_at.tzinfo is None:
        raise ValueError("scheduler_fired_at must be timezone-aware")
    if boundary_tolerance < timedelta(0) or boundary_tolerance >= timedelta(minutes=30):
        raise ValueError("boundary_tolerance must be between 0 and 30 minutes")

    target_timezone = ZoneInfo(timezone)
    fired_at = scheduler_fired_at.astimezone(target_timezone)
    normalized_platform_start: datetime | None = None

    if platform_scheduled_start is not None:
        if platform_scheduled_start.tzinfo is None:
            raise ValueError("platform_scheduled_start must be timezone-aware")
        normalized_platform_start = platform_scheduled_start.astimezone(target_timezone)
        if (
            normalized_platform_start.minute != 0
            or normalized_platform_start.second != 0
            or normalized_platform_start.microsecond != 0
        ):
            raise ValueError("platform_scheduled_start must be an exact hourly boundary")
        scheduled_start = normalized_platform_start
        resolution = "platform_scheduled_start"
    else:
        floor_boundary = fired_at.replace(minute=0, second=0, microsecond=0)
        next_boundary = floor_boundary + timedelta(hours=1)
        since_floor = fired_at - floor_boundary
        until_next = next_boundary - fired_at

        if until_next <= boundary_tolerance:
            scheduled_start = next_boundary
            resolution = "inferred_nearest_boundary_early_fire"
        elif since_floor <= boundary_tolerance:
            scheduled_start = floor_boundary
            resolution = "inferred_nearest_boundary_late_fire"
        else:
            scheduled_start = floor_boundary
            resolution = "inferred_floor_fallback"

    next_scheduled_start = scheduled_start + timedelta(hours=1)
    return HourlyExecutionWindow(
        scheduler_fired_at=fired_at,
        platform_scheduled_start=normalized_platform_start,
        scheduled_start=scheduled_start,
        next_scheduled_start=next_scheduled_start,
        soft_stop=next_scheduled_start - SOFT_STOP_LEAD,
        hard_stop=next_scheduled_start - HARD_STOP_LEAD,
        scheduled_start_resolution=resolution,
        boundary_tolerance=boundary_tolerance,
    )


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset or Z")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the safe hourly AGI execution window."
    )
    parser.add_argument("--fired-at", required=True, type=_parse_datetime)
    parser.add_argument("--platform-scheduled-start", type=_parse_datetime)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--boundary-tolerance-seconds", type=int, default=120)
    args = parser.parse_args(argv)

    if not 0 <= args.boundary_tolerance_seconds < 1800:
        parser.error("--boundary-tolerance-seconds must be from 0 through 1799")

    window = resolve_hourly_execution_window(
        args.fired_at,
        platform_scheduled_start=args.platform_scheduled_start,
        timezone=args.timezone,
        boundary_tolerance=timedelta(seconds=args.boundary_tolerance_seconds),
    )
    print(json.dumps(window.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
