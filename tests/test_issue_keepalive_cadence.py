"""Regression test: synthetic watchdog keepalives must not poison the cadence.

Publish-on-change power sensors go silent at a flat low value. The watchdog
then injects 0 W keepalives every ``off_delay`` (~1800 s). Before the fix each
injection fed ``_update_cadence`` with the ~1800 s dt, ballooning ``_p95_dt`` and
hence ``_dynamic_end_threshold`` (= 3 * p95) to ~90 min, so a finished cycle hung
in PAUSED for ~2 h before ending.

The fix marks injected readings ``synthetic=True``: they must still advance the
below-threshold accumulator (so the silence is counted toward ending the cycle)
but must NOT feed the cadence statistic.
"""

from datetime import datetime, timedelta, timezone

from unittest.mock import Mock

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)


def _dt(seconds: float) -> datetime:
    return datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def _make_detector() -> CycleDetector:
    config = CycleDetectorConfig(
        min_power=2.0,
        off_delay=1800,
        device_type="dishwasher",
        stop_threshold_w=1.5,
        start_threshold_w=3.0,
    )
    callbacks = {"on_state_change": Mock(), "on_cycle_end": Mock()}
    return CycleDetector(config, callbacks["on_state_change"], callbacks["on_cycle_end"])


def test_synthetic_keepalives_do_not_poison_cadence():
    detector = _make_detector()

    # 1. Feed ~10 REAL readings at a normal ~30 s cadence in the active band.
    t = 0.0
    for i in range(10):
        detector.process_reading(2000.0, _dt(t))
        t += 30.0

    # After a run of 30 s samples, the cadence stat is small.
    assert detector._p95_dt < 100, detector._p95_dt
    end_thresh_before = detector._dynamic_end_threshold
    assert end_thresh_before < 300, end_thresh_before

    time_below_before = detector._time_below_threshold

    # 2. Simulate silence: the watchdog injects a 0 W keepalive at ~1800 s dt.
    # (One injection already accrues the full off_delay of silence; a second one
    #  drives the cycle into ENDING and would reset the accumulator, so we assert
    #  right after the first injection while the state is still PAUSED.)
    t += 1800.0
    detector.process_reading(0.0, _dt(t), synthetic=True)

    # Assert 1: the cadence stat was NOT poisoned by the 1800 s gap.
    assert detector._p95_dt < 100, detector._p95_dt
    assert detector._dynamic_end_threshold < 300, detector._dynamic_end_threshold

    # Assert 2: the below-threshold accumulator DID advance (silence is counted).
    assert detector._time_below_threshold >= 1800, detector._time_below_threshold
    assert detector._time_below_threshold > time_below_before


def test_non_synthetic_keepalives_balloon_cadence_contrast():
    """Contrast: without synthetic=True the same 1800 s gaps DO balloon the stat,
    proving that exactly the flag makes the difference."""
    detector = _make_detector()

    t = 0.0
    for i in range(10):
        detector.process_reading(2000.0, _dt(t))
        t += 30.0

    assert detector._p95_dt < 100

    # Feed the same 1800 s-spaced readings WITHOUT marking them synthetic.
    for _ in range(10):
        t += 1800.0
        detector.process_reading(0.0, _dt(t), synthetic=False)

    # The cadence stat is poisoned and the end threshold balloons.
    assert detector._p95_dt > 1000, detector._p95_dt
    assert detector._dynamic_end_threshold > 3000, detector._dynamic_end_threshold
