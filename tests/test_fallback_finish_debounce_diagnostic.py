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
"""A fallback finish must say how far the Smart-Termination debounce got.

Smart Termination is gated by four conditions.  Three of them - match
confidence, ambiguous match, prefix-ambiguous match - already log *why* they
blocked.  The fourth, the confirmation window ``_time_in_state >=
smart_debounce``, is silent.

That asymmetry is not academic.  Measured on a real dishwasher cycle
(2026-08-05, KD): the cycle entered ENDING at 10:50:00 and the fallback path
finished it at 10:52:00, so the window stood at 120 s of the required 300 s.
No blocker line was emitted - correctly, none of the three gates was closed -
and from the log alone a healthy match that simply ran out of runway is
indistinguishable from a Smart Termination that never became due.

One line per cycle, emitted only when the fast path did not take it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)
from custom_components.ha_washdata.const import (
    DISHWASHER_SMART_TERMINATION_DEBOUNCE_SECONDS,
    STATE_ENDING,
    TerminationReason,
)

_BASE = datetime(2026, 8, 5, 5, 31, 41, tzinfo=timezone.utc)


def _ts(offset_s: float) -> datetime:
    return _BASE + timedelta(seconds=offset_s)


def _dishwasher_detector() -> CycleDetector:
    cfg = CycleDetectorConfig(
        min_power=1.3,
        off_delay=300,
        stop_threshold_w=1.5,
        start_threshold_w=3.0,
        device_type="dishwasher",
        min_off_gap=300,
        completion_min_seconds=900,
        end_energy_threshold=0.05,
        start_energy_threshold=0.2,
        start_duration_threshold=5.0,
    )
    return CycleDetector(
        config=cfg,
        on_state_change=lambda old, new: None,
        on_cycle_end=lambda d: None,
    )


def _armed_detector() -> CycleDetector:
    """A dishwasher sitting in ENDING with a healthy match, 120 s into the window."""
    det = _dishwasher_detector()
    det._current_cycle_start = _ts(0)
    det._last_active_time = _ts(11680)
    det._power_readings = [(_ts(0), 2000.0), (_ts(11600), 2.6), (_ts(11680), 3.8)]
    det._matched_profile = "Eco (Standard)"
    det._expected_duration = 11600.0
    det._last_match_confidence = 0.869
    det._state = STATE_ENDING
    det._time_in_state = 120.0
    det._logger = MagicMock()
    return det


def _debounce_lines(det: CycleDetector) -> list[str]:
    return [
        call.args[0]
        for call in det._logger.debug.call_args_list
        if call.args and "debounce" in str(call.args[0])
    ]


def test_fallback_finish_reports_the_debounce_shortfall() -> None:
    """The silent gate must become visible when the fallback path wins."""
    det = _armed_detector()

    det._finish_cycle(
        _ts(11800),
        status="completed",
        termination_reason=TerminationReason.TIMEOUT,
        keep_tail=False,
    )

    lines = _debounce_lines(det)
    assert lines, (
        "a fallback finish with a healthy match must state how far the "
        "smart-termination debounce got; otherwise it is indistinguishable "
        "from 'never became due'"
    )
    call = next(
        c
        for c in det._logger.debug.call_args_list
        if c.args and "debounce" in str(c.args[0])
    )
    assert 120.0 in call.args, f"reached time must be reported: {call.args!r}"
    assert (
        DISHWASHER_SMART_TERMINATION_DEBOUNCE_SECONDS in call.args
    ), f"required window must be reported: {call.args!r}"


def test_smart_finish_stays_quiet() -> None:
    """When the fast path did fire there is nothing to explain."""
    det = _armed_detector()

    det._finish_cycle(
        _ts(11800),
        status="completed",
        termination_reason=TerminationReason.SMART,
        keep_tail=True,
    )

    assert not _debounce_lines(det)


def test_unmatched_cycle_stays_quiet() -> None:
    """Without a matched profile Smart Termination was never an option."""
    det = _armed_detector()
    det._matched_profile = None
    det._expected_duration = 0.0

    det._finish_cycle(
        _ts(11800),
        status="completed",
        termination_reason=TerminationReason.TIMEOUT,
        keep_tail=False,
    )

    assert not _debounce_lines(det)


@pytest.mark.parametrize(
    ("device_type", "min_off_gap", "expected"),
    [
        ("dishwasher", 300, DISHWASHER_SMART_TERMINATION_DEBOUNCE_SECONDS),
        ("washing_machine", 300, 180.0),
        ("washing_machine", 1200, 600.0),
        ("dryer", 300, 120.0),
    ],
)
def test_debounce_helper_matches_the_documented_windows(
    device_type: str, min_off_gap: int, expected: float
) -> None:
    """The diagnostic must report the same number the gate uses - one formula."""
    cfg = CycleDetectorConfig(
        min_power=1.3,
        off_delay=300,
        stop_threshold_w=1.5,
        start_threshold_w=3.0,
        device_type=device_type,
        min_off_gap=min_off_gap,
    )
    det = CycleDetector(
        config=cfg,
        on_state_change=lambda old, new: None,
        on_cycle_end=lambda d: None,
    )

    assert det._smart_termination_debounce_seconds() == expected
