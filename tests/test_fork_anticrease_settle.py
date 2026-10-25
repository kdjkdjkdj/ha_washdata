"""Fork 0.5.8.4: a settle level for the anti-crease confirm window.

The Tiny dryer cools down for 9-12 min with the drum turning non-stop at
150-230 W, then rests near standby with a single 155-280 W tumble every few
minutes. Both sit under its 400 W anti_wrinkle_max_power - same motor - so the
finalise fired a few minutes into the cool-down while the display still counted
down. ``anti_crease_settle_power_w`` holds the window until the first rest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.ha_washdata.const import ANTI_CREASE_SETTLE_WAIT_MAX_RATIO
from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
    STATE_ANTI_WRINKLE,
)

BASE = datetime(2026, 10, 25, 12, 3, 41, tzinfo=timezone.utc)
STEP = 6.0
EXPECTED = 3658.0           # Schranktrocken+ avg_duration
HEAT_END = 2400.0
COOL_END = 3000.0           # 10 min of non-stop cool-down drum
BURST_EVERY = 300.0


def _config(settle: float) -> CycleDetectorConfig:
    return CycleDetectorConfig(
        min_power=0.7,
        off_delay=180,
        device_type="dryer",
        stop_threshold_w=1.5,
        start_threshold_w=5.0,
        anti_wrinkle_enabled=True,
        anti_wrinkle_max_power=400.0,
        anti_wrinkle_max_duration=900.0,
        anti_crease_finalize_ratio=0.75,
        anti_crease_settle_power_w=settle,
        min_off_gap=300,
    )


def _dryer(t: float, cool_end: float = COOL_END) -> float:
    if t < HEAT_END:
        return 2050.0 if int(t) % 600 < 400 else 170.0
    if t < cool_end:
        return 165.0 + (t % 60)          # cool-down drum, never pauses
    # anti-crease: rest above the stop threshold, one short tumble per BURST_EVERY
    return 170.0 if (t - cool_end) % BURST_EVERY < 12 else 2.0


def _replay(settle: float, until: float, cool_end: float = COOL_END):
    ended: list[dict] = []
    states: list[str] = []
    det = CycleDetector(
        config=_config(settle),
        on_state_change=lambda _o, n: states.append(n),
        on_cycle_end=lambda d: ended.append(d),
    )
    t = 0.0
    while t <= until:
        det.process_reading(_dryer(t, cool_end), BASE + timedelta(seconds=t))
        det.update_match(("Schranktrocken+", 0.45, EXPECTED, None, False, False, False, False, 60.0))
        t += STEP
    return ended, states


def test_without_a_settle_level_the_finalise_fires_in_the_cool_down() -> None:
    ended, states = _replay(0.0, COOL_END)
    assert len(ended) == 1
    assert ended[0]["duration"] < COOL_END
    assert STATE_ANTI_WRINKLE in states


def test_a_settle_level_waits_out_the_cool_down() -> None:
    ended, _states = _replay(100.0, COOL_END)
    assert ended == []


def test_the_first_rest_after_the_cool_down_finalises_once() -> None:
    ended, states = _replay(100.0, COOL_END + 600)
    assert len(ended) == 1
    assert STATE_ANTI_WRINKLE in states


def test_a_settle_level_at_or_above_the_ceiling_changes_nothing() -> None:
    ended, _states = _replay(400.0, COOL_END)
    assert len(ended) == 1
    assert ended[0]["duration"] < COOL_END


def test_a_tumble_that_never_pauses_is_released_at_the_cap() -> None:
    cap = EXPECTED * ANTI_CREASE_SETTLE_WAIT_MAX_RATIO
    never = 10 * EXPECTED
    early, _s1 = _replay(100.0, cap - 300, cool_end=never)
    assert early == []
    late, _s2 = _replay(100.0, cap + 300, cool_end=never)
    assert len(late) == 1
