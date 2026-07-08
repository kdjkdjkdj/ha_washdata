"""Tests for blip-tolerant graceful timeout (profile-independent end)."""

from datetime import datetime, timezone, timedelta

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
    STATE_ENDING,
    STATE_RUNNING,
    STATE_FINISHED,
    STATE_ANTI_WRINKLE,
    DEVICE_TYPE_DRYER,
    DEVICE_TYPE_WASHING_MACHINE,
)


def dt(s: float) -> datetime:
    return datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=s)


def test_config_has_blip_tolerant_defaults():
    cfg = CycleDetectorConfig(min_power=5.0, off_delay=60, device_type=DEVICE_TYPE_DRYER)
    assert cfg.crease_resume_threshold == 400.0
    assert cfg.unmatched_off_delay == 1800
