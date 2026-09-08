from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agi.hourly_window import main, resolve_hourly_execution_window

JST = timezone(timedelta(hours=9))


def test_platform_scheduled_start_is_authoritative_for_early_fire() -> None:
    platform_start = datetime(2026, 8, 17, 11, 0, tzinfo=JST)
    window = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 10, 59, 30, tzinfo=JST),
        platform_scheduled_start=platform_start,
    )

    assert window.platform_scheduled_start == platform_start
    assert window.scheduled_start == platform_start
    assert window.scheduled_start_resolution == "platform_scheduled_start"
    assert window.soft_stop == datetime(2026, 8, 17, 11, 50, tzinfo=JST)
    assert window.hard_stop == datetime(2026, 8, 17, 11, 55, tzinfo=JST)
    assert window.to_dict()["schedule_metadata_available"] is True


def test_early_fire_without_metadata_rounds_to_next_hour() -> None:
    window = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 10, 59, 30, tzinfo=JST)
    )

    assert window.scheduled_start == datetime(2026, 8, 17, 11, 0, tzinfo=JST)
    assert window.scheduled_start_resolution == "inferred_nearest_boundary_early_fire"
    assert window.to_dict()["fired_before_scheduled_start"] is True
    assert window.to_dict()["execution_window_open_at_fire"] is True


def test_early_fire_threshold_is_inclusive() -> None:
    at_threshold = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 10, 58, 0, tzinfo=JST)
    )
    just_outside = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 10, 57, 59, 999999, tzinfo=JST)
    )

    assert at_threshold.scheduled_start == datetime(2026, 8, 17, 11, 0, tzinfo=JST)
    assert just_outside.scheduled_start == datetime(2026, 8, 17, 10, 0, tzinfo=JST)


def test_late_or_regular_fire_stays_in_current_hour() -> None:
    late = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 11, 1, 59, tzinfo=JST)
    )
    regular = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 11, 15, 0, tzinfo=JST)
    )

    assert late.scheduled_start == datetime(2026, 8, 17, 11, 0, tzinfo=JST)
    assert late.scheduled_start_resolution == "inferred_nearest_boundary_late_fire"
    assert regular.scheduled_start == datetime(2026, 8, 17, 11, 0, tzinfo=JST)
    assert regular.scheduled_start_resolution == "inferred_floor_fallback"


def test_utc_trigger_is_normalized_to_requested_timezone() -> None:
    window = resolve_hourly_execution_window(
        datetime(2026, 8, 17, 1, 59, 30, tzinfo=timezone.utc)
    )

    assert window.scheduler_fired_at == datetime(2026, 8, 17, 10, 59, 30, tzinfo=JST)
    assert window.scheduled_start == datetime(2026, 8, 17, 11, 0, tzinfo=JST)


def test_naive_or_non_boundary_platform_timestamps_fail_closed() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_hourly_execution_window(datetime(2026, 8, 17, 10, 59, 30))

    with pytest.raises(ValueError, match="exact hourly boundary"):
        resolve_hourly_execution_window(
            datetime(2026, 8, 17, 10, 59, 30, tzinfo=JST),
            platform_scheduled_start=datetime(2026, 8, 17, 11, 0, 1, tzinfo=JST),
        )


def test_invalid_boundary_tolerance_fails_closed() -> None:
    fired_at = datetime(2026, 8, 17, 10, 59, 30, tzinfo=JST)
    with pytest.raises(ValueError, match="between 0 and 30 minutes"):
        resolve_hourly_execution_window(
            fired_at,
            boundary_tolerance=timedelta(minutes=30),
        )


def test_cli_emits_machine_readable_window(capsys) -> None:
    assert main(["--fired-at", "2026-08-17T10:59:30+09:00"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["scheduler_fired_at"] == "2026-08-17T10:59:30+09:00"
    assert payload["platform_scheduled_start"] is None
    assert payload["scheduled_start"] == "2026-08-17T11:00:00+09:00"
    assert payload["soft_stop"] == "2026-08-17T11:50:00+09:00"
    assert payload["hard_stop"] == "2026-08-17T11:55:00+09:00"
    assert payload["seconds_from_scheduled_start"] == -30.0
