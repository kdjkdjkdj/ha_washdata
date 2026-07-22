# WashData - Home Assistant integration for appliance cycle monitoring via smart plugs.
# Copyright (C) 2026 Lukas Bandura
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fork feature #3: segmentation-based dishwasher drying-tail termination.

A dishwasher's passive drying tail draws a small steady power that can sit ABOVE
stop_threshold, so the detector reads it as "active" and ``_time_below_threshold``
never accumulates - the power quiet-release is blind to drying and the cycle
overshoots by up to the ~30-min end-spike wait. The phase segmenter classifies
the same low-power plateau as a terminal ``idle`` role, giving a reliable
"drying tail complete" signal.

``_drying_tail_finished`` is the pure predicate for that signal. It can only
*shorten* the overshoot: it requires we are at/past the expected program
duration, so it never fires early mid-cycle. The sustained-idle threshold adapts
to the matched program's learned drying length via an injected provider, with a
fixed quiet-release floor as the conservative default.
"""
from datetime import datetime, timedelta

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)

BASE = datetime(2026, 1, 1, 8, 0, 0)


def _make_detector(device_type="dishwasher", drying_provider=None):
    cfg = CycleDetectorConfig(
        min_power=0.8, off_delay=600, device_type=device_type,
        stop_threshold_w=1.0, start_threshold_w=1.5, min_off_gap=60,
    )
    return CycleDetector(
        cfg, on_state_change=lambda o, n: None, on_cycle_end=lambda i: None,
        drying_duration_provider=drying_provider,
    )


def _trace(heat_s=600, wash_s=1800, idle_s=1200, dt=30.0,
           heat_w=2000.0, wash_w=80.0, idle_w=3.0):
    """A dishwasher-shaped trace: heating -> wash -> passive drying idle."""
    pts, cur = [], 0.0
    for w, dur in ((heat_w, heat_s), (wash_w, wash_s), (idle_w, idle_s)):
        for _ in range(int(dur // dt)):
            pts.append((BASE + timedelta(seconds=cur), w))
            cur += dt
    return pts, cur


def _prime(det, readings, *, expected, matched="Eco", conf=0.8,
           ambiguous=False, prefix_ambiguous=False):
    det._power_readings = list(readings)
    det._current_cycle_start = readings[0][0]
    det._matched_profile = matched
    det._expected_duration = float(expected)
    det._last_match_confidence = conf
    det._match_ambiguous = ambiguous
    det._match_prefix_ambiguous = prefix_ambiguous


def test_drying_tail_finished_true_past_expected():
    det = _make_detector()
    readings, total = _trace()  # ~3600s, terminal idle 1200s
    _prime(det, readings, expected=3000.0)  # current 3600 > expected 3000
    assert det._drying_tail_finished(readings[-1][0]) is True


def test_drying_tail_finished_false_before_expected():
    # Safety floor: never fire before the expected program length.
    det = _make_detector()
    readings, total = _trace()
    _prime(det, readings, expected=9000.0)  # current 3600 < expected 9000
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_when_still_washing():
    # Terminal segment is wash, not the drying idle -> not finished.
    det = _make_detector()
    readings, total = _trace(idle_s=0)  # ends in wash
    _prime(det, readings, expected=1000.0)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_when_idle_too_short():
    # Terminal idle present but not yet sustained (< quiet-release) -> wait.
    det = _make_detector()
    readings, total = _trace(idle_s=120)  # only 2 min of drying
    _prime(det, readings, expected=1000.0)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_without_heating():
    # No heating role seen -> not a real dishwasher program tail.
    det = _make_detector()
    readings, total = _trace(heat_s=0)
    _prime(det, readings, expected=1000.0)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_on_ambiguous_match():
    det = _make_detector()
    readings, total = _trace()
    _prime(det, readings, expected=3000.0, ambiguous=True)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_on_low_confidence():
    det = _make_detector()
    readings, total = _trace()
    _prime(det, readings, expected=3000.0, conf=0.2)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_for_non_dishwasher():
    det = _make_detector(device_type="washing_machine")
    readings, total = _trace()
    _prime(det, readings, expected=3000.0)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_false_without_match():
    det = _make_detector()
    readings, total = _trace()
    _prime(det, readings, expected=3000.0, matched=None)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_waits_for_learned_drying_length():
    # Provider says this program dries for ~100 min; the observed 20-min tail is
    # not yet enough even though we are past the expected total -> keep waiting.
    det = _make_detector(drying_provider=lambda name: 6000.0)
    readings, total = _trace(idle_s=1200)  # 20 min of drying so far
    _prime(det, readings, expected=3000.0)
    assert det._drying_tail_finished(readings[-1][0]) is False


def test_drying_tail_finished_when_learned_drying_length_met():
    # Provider says this program dries for ~17 min (target ~15 min after the 0.9
    # tolerance); the observed 20-min tail clears it -> finished.
    det = _make_detector(drying_provider=lambda name: 1000.0)
    readings, total = _trace(idle_s=1200)
    _prime(det, readings, expected=3000.0)
    assert det._drying_tail_finished(readings[-1][0]) is True


def test_drying_tail_provider_error_falls_back_to_floor():
    # A throwing provider must not break detection; falls back to the quiet floor
    # (600 s), which the 20-min tail clears.
    def _boom(_name):
        raise RuntimeError("provider down")
    det = _make_detector(drying_provider=_boom)
    readings, total = _trace(idle_s=1200)
    _prime(det, readings, expected=3000.0)
    assert det._drying_tail_finished(readings[-1][0]) is True
