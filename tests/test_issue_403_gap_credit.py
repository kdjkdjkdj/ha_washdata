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
"""Issue #403 - the interval since the previous sample must not be credited at
the new sample's power.

With a change-only (send-on-delta) power sensor the appliance provably sat at
the previous low value during the interval, so booking a low->high step at the
high power lets one blip after an idle gap satisfy both start gates.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)
from custom_components.ha_washdata.const import (
    DEVICE_TYPE_DISHWASHER,
    STATE_OFF,
    STATE_RUNNING,
    STATE_STARTING,
)


def dt(offset_seconds: float) -> datetime:
    return datetime(2026, 8, 8, 7, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds
    )


def _make_detector(**overrides):
    cfg = CycleDetectorConfig(
        min_power=overrides.pop("min_power", 5.0),
        off_delay=overrides.pop("off_delay", 300),
        device_type=overrides.pop("device_type", DEVICE_TYPE_DISHWASHER),
        start_threshold_w=overrides.pop("start_threshold_w", 22.0),
        stop_threshold_w=overrides.pop("stop_threshold_w", 22.0),
        start_duration_threshold=overrides.pop("start_duration_threshold", 5.0),
        start_energy_threshold=overrides.pop("start_energy_threshold", 0.2),
        **overrides,
    )
    return CycleDetector(config=cfg, on_state_change=Mock(), on_cycle_end=Mock())


def _feed(detector, samples):
    for offset, power in samples:
        detector.process_reading(power, dt(offset))


def test_blip_after_idle_gap_does_not_commit_a_cycle():
    """The recorded trace from #403: 511 s of silence, then a 4 s blip."""
    detector = _make_detector()

    _feed(
        detector,
        [(0, 10.8), (511, 62.5), (515, 31.0), (523, 10.5)],
    )

    assert detector.state != STATE_RUNNING


def test_gap_is_not_credited_as_high_time():
    """The first high reading after a low one carries no observed high time."""
    detector = _make_detector()

    _feed(detector, [(0, 10.8), (511, 62.5)])

    assert detector.state == STATE_STARTING
    assert detector._time_above_threshold == 0.0


def test_off_to_starting_seed_energy_excludes_the_gap():
    """The OFF -> STARTING hand-off seeds energy from the guarded interval."""
    detector = _make_detector()

    _feed(detector, [(0, 10.8), (511, 62.5)])

    assert detector._energy_since_idle_wh == 0.0


def test_high_time_accrues_once_the_load_is_observed():
    """Evidence builds from consecutive high readings, not from the gap."""
    detector = _make_detector()

    _feed(detector, [(0, 10.8), (511, 62.5), (523, 62.5)])

    # 12 s between two high samples - that interval belongs to the high level,
    # and 62.5 W over it clears the 0.2 Wh energy gate as well.
    assert detector._time_above_threshold == 12.0
    assert detector.state == STATE_RUNNING


def test_dense_sampling_keeps_the_full_credit():
    """Parity pin: when the previous sample is already high nothing changes.

    Passes on the unpatched detector too - it guards against a fix that simply
    drops the credit for everyone.
    """
    detector = _make_detector()

    _feed(detector, [(0, 60.0), (2, 60.0), (4, 60.0)])

    assert detector._time_above_threshold == 4.0
    assert detector._energy_since_idle_wh == pytest.approx(
        60.0 * (4.0 / 3600.0), rel=1e-6
    )


def test_hysteresis_band_reading_earns_no_start_evidence():
    """A sample in [stop_threshold_w, start_threshold_w) is below the gate's own
    threshold, so the interval that follows it starts from zero."""
    detector = _make_detector(stop_threshold_w=10.0)

    _feed(detector, [(0, 2.0), (60, 15.0), (120, 62.5)])

    # 15 W sits inside the band: it is not "high" for the start gates, so the
    # 60 s interval after it must not count toward them either.
    assert detector.state == STATE_STARTING
    assert detector._time_above_threshold == 0.0


def test_manual_stop_lockout_records_the_observed_level():
    """The lockout hides a reading from the state machine but not from history."""
    detector = _make_detector()

    detector.process_reading(60.0, dt(0))
    detector._ignore_power_until_idle = True
    detector._lockout_high_seconds = 0.0

    detector.process_reading(55.0, dt(30))

    assert detector._last_power == 55.0
