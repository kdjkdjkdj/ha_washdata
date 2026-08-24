# WashData - Home Assistant integration for appliance cycle monitoring via smart plugs.
# Copyright (C) 2026 Lukas Bandura
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
"""Curve pre-roll (fork): carry the ramp an aborted start probe observed.

A cycle's curve begins at the OFF -> STARTING transition that committed, so an
appliance that feels its way in over several probes loses everything the
aborted ones saw.  `curve_preroll_seconds` carries those readings forward;
0 (the default) keeps the previous behaviour exactly.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)
from custom_components.ha_washdata.const import (
    DEFAULT_CURVE_PREROLL_SECONDS,
    DEVICE_TYPE_DISHWASHER,
    STATE_OFF,
    STATE_STARTING,
)


def dt(offset_seconds: float) -> datetime:
    return datetime(2026, 8, 20, 9, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds
    )


def _make_detector(**overrides):
    cfg = CycleDetectorConfig(
        min_power=overrides.pop("min_power", 1.0),
        off_delay=overrides.pop("off_delay", 300),
        device_type=overrides.pop("device_type", DEVICE_TYPE_DISHWASHER),
        start_threshold_w=overrides.pop("start_threshold_w", 3.0),
        stop_threshold_w=overrides.pop("stop_threshold_w", 1.0),
        start_duration_threshold=overrides.pop("start_duration_threshold", 5.0),
        start_energy_threshold=overrides.pop("start_energy_threshold", 0.2),
        **overrides,
    )
    return CycleDetector(config=cfg, on_state_change=Mock(), on_cycle_end=Mock())


def _feed(detector, samples):
    for offset, power in samples:
        detector.process_reading(power, dt(offset))


# A dishwasher feeling its way in: a probe at t=20 that aborts, then the real
# start at t=140.  Mirrors the measured shape at both dishwashers.
PROBE_THEN_START = [
    (0, 0.8),
    (10, 0.9),
    (20, 13.0),   # first probe - aborts
    (30, 0.9),
    (60, 0.8),
    (120, 6.8),   # second probe - aborts
    (130, 0.9),
    (140, 84.0),  # the start that commits
    (150, 90.0),
]


def test_default_is_off_and_keeps_the_committing_probe_as_the_start():
    assert DEFAULT_CURVE_PREROLL_SECONDS == 0.0

    detector = _make_detector()
    _feed(detector, PROBE_THEN_START)

    assert detector.current_cycle_start == dt(140)
    assert detector._power_readings[0][0] == dt(140)


def test_preroll_backdates_the_start_to_the_first_probe():
    detector = _make_detector(curve_preroll_seconds=300.0)
    _feed(detector, PROBE_THEN_START)

    assert detector.current_cycle_start == dt(20)
    assert detector._power_readings[0] == (dt(20), 13.0)
    # the standby samples between the probes come along - trim_zero_readings
    # drops them at finalize time, they must not become the anchor
    assert [p for _, p in detector._power_readings][:3] == [13.0, 0.9, 0.8]


def test_window_bounds_how_far_back_the_anchor_may_sit():
    """A 60 s window reaches the second probe, not the first."""
    detector = _make_detector(curve_preroll_seconds=60.0)
    _feed(detector, PROBE_THEN_START)

    assert detector.current_cycle_start == dt(120)
    assert detector._power_readings[0] == (dt(120), 6.8)


def test_standby_alone_never_anchors_a_cycle():
    """Without a probe above the start threshold nothing is carried."""
    detector = _make_detector(curve_preroll_seconds=300.0)
    _feed(detector, [(0, 0.8), (60, 0.9), (120, 0.8), (140, 84.0)])

    assert detector.current_cycle_start == dt(140)
    assert detector._power_readings[0] == (dt(140), 84.0)


def test_previous_cycle_tail_is_not_carried_into_the_next_start():
    detector = _make_detector(curve_preroll_seconds=1800.0)
    detector._preroll_buffer = [(dt(-600), 900.0)]
    detector._finish_cycle(dt(-500), status="completed")

    assert detector._preroll_buffer == []


def test_buffer_stays_empty_while_the_feature_is_off():
    detector = _make_detector()
    _feed(detector, PROBE_THEN_START)

    assert detector._preroll_buffer == []


def test_carried_peak_is_reflected_in_the_cycle_maximum():
    detector = _make_detector(curve_preroll_seconds=300.0)
    _feed(detector, [(0, 0.8), (20, 130.0), (80, 0.9), (140, 84.0)])

    assert detector._cycle_max_power == pytest.approx(130.0)


def test_an_isolated_blip_behind_a_quiet_gap_does_not_anchor():
    """The dryer case: a crease-guard tumble minutes before the next load.

    Measured shape - one reading well above the threshold, then two minutes of
    standby, then the real start. Carrying it would back-date the cycle into
    unrelated activity, so the chain breaks at the quiet stretch.
    """
    detector = _make_detector(curve_preroll_seconds=300.0)
    _feed(detector, [(0, 1.4), (20, 167.6), (80, 1.4), (140, 1.4), (200, 84.0)])

    assert detector.current_cycle_start == dt(200)
    assert detector._power_readings[0] == (dt(200), 84.0)


def test_a_continuous_approach_survives_the_chain_break():
    """Same span, but the probes keep touching the threshold - this is a ramp."""
    detector = _make_detector(curve_preroll_seconds=300.0)
    _feed(detector, [(0, 1.4), (20, 13.0), (80, 6.8), (140, 5.7), (200, 84.0)])

    assert detector.current_cycle_start == dt(20)
