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
"""Per-appliance anti-crease finalize ratio (fork).

The finalize into STATE_ANTI_WRINKLE is gated on the cycle having reached
ANTI_CREASE_FINALIZE_RATIO of its expected duration - a fixed 0.98. On a
load-dependent dryer roughly half the runs never get there, so the path built
for exactly that tail can never engage. The ratio becomes a per-appliance
option; the default keeps the constant.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)
from custom_components.ha_washdata.const import (
    ANTI_CREASE_FINALIZE_RATIO,
    DEFAULT_ANTI_CREASE_FINALIZE_RATIO,
    DEVICE_TYPE_DRYER,
)


def dt(offset_seconds: float) -> datetime:
    return datetime(2026, 8, 24, 20, 0, 0, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds
    )


def _dryer(**overrides):
    cfg = CycleDetectorConfig(
        min_power=overrides.pop("min_power", 1.0),
        off_delay=overrides.pop("off_delay", 180),
        device_type=DEVICE_TYPE_DRYER,
        start_threshold_w=3.0,
        stop_threshold_w=1.5,
        anti_wrinkle_enabled=True,
        anti_wrinkle_max_power=overrides.pop("anti_wrinkle_max_power", 400.0),
        **overrides,
    )
    det = CycleDetector(config=cfg, on_state_change=Mock(), on_cycle_end=Mock())
    # A matched, energetic cycle that has run 80% of its expected duration -
    # the shape the gate is asked about once the heating has stopped.
    det._matched_profile = "Schranktrocken+"
    det._expected_duration = 5600.0
    det._last_match_confidence = 0.53
    det._match_ambiguous = False
    det._match_prefix_ambiguous_full_shape = False
    det._cycle_max_power = 2900.0
    det._current_cycle_start = dt(0)
    det._power_readings = [(dt(t), 160.0) for t in range(0, 4481, 60)]
    return det


def test_default_keeps_the_shipped_constant():
    assert DEFAULT_ANTI_CREASE_FINALIZE_RATIO == ANTI_CREASE_FINALIZE_RATIO == 0.98
    assert CycleDetectorConfig(
        min_power=1.0, off_delay=60
    ).anti_crease_finalize_ratio == pytest.approx(0.98)


def test_the_gate_stays_shut_at_the_default_for_a_short_run():
    """4480 s of 5600 s expected = 80%, below the shipped 0.98."""
    detector = _dryer()

    assert detector._anticrease_gate_open(dt(4480)) is False


def test_a_lowered_ratio_opens_the_gate_for_the_same_run():
    detector = _dryer(anti_crease_finalize_ratio=0.75)

    assert detector._anticrease_gate_open(dt(4480)) is True


def test_the_other_guards_still_apply_at_a_lowered_ratio():
    """Lowering the ratio must not bypass the confidence gate."""
    detector = _dryer(anti_crease_finalize_ratio=0.75)
    detector._last_match_confidence = 0.1

    assert detector._anticrease_gate_open(dt(4480)) is False


def test_an_unmatched_cycle_is_never_finalized_early():
    detector = _dryer(anti_crease_finalize_ratio=0.5)
    detector._matched_profile = None

    assert detector._anticrease_gate_open(dt(4480)) is False
