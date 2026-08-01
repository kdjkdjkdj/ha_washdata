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
"""The manager's profile-matcher wrapper must honour the detector's offload contract.

``CycleDetector._try_profile_match`` treats a falsy matcher return value as
"no synchronous result - the matcher will call update_match() later".  The
wrapper offloads to an async task, so it must return exactly that.  Returning a
placeholder tuple instead is truthy, so the detector feeds ``(None, 0.0, 0.0,
None)`` into ``update_match()``, which resets ``_last_match_confidence`` to 0.0
while ``_matched_profile`` / ``_expected_duration`` stay set - Smart Termination
then reports "due ... but blocked: confidence 0.00 < 0.4" and the cycle falls
back to the power timeout.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)
from custom_components.ha_washdata.manager import WashDataManager


def dt(offset_seconds: int) -> datetime:
    return datetime(2026, 7, 31, 19, 10, 0, tzinfo=timezone.utc) + timedelta(
        seconds=offset_seconds
    )


@pytest.fixture
def mock_entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Spuelmaschine"
    entry.options = {"power_sensor": "sensor.test_power"}
    entry.data = {}
    return entry


@pytest.fixture
def wrapper_and_manager(hass: HomeAssistant, mock_entry: Any) -> tuple[Any, Any]:
    """Return the wrapper the manager hands to the detector, plus the manager."""
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ) as detector_cls:
        mgr = WashDataManager(hass, mock_entry)

    wrapper = detector_cls.call_args.kwargs["profile_matcher"]
    # Keep the offload from creating a real coroutine/task in the test.
    mgr._async_perform_combined_matching = MagicMock()
    mgr._spawn_tracked = MagicMock()
    return wrapper, mgr


def test_async_offload_returns_no_synchronous_result(
    wrapper_and_manager: tuple[Any, Any],
) -> None:
    """The offload path signals 'nothing synchronous' - and still offloads."""
    wrapper, mgr = wrapper_and_manager

    result = wrapper([(dt(0), 100.0), (dt(10), 120.0)])

    assert not result, (
        "matcher must return a falsy value on async offload; a placeholder "
        f"tuple is truthy and reaches update_match(): {result!r}"
    )
    assert mgr._spawn_tracked.called, "the async match must still be scheduled"


def test_empty_readings_return_no_synchronous_result(
    wrapper_and_manager: tuple[Any, Any],
) -> None:
    """Without readings there is nothing to match - and nothing to publish."""
    wrapper, mgr = wrapper_and_manager

    result = wrapper([])

    assert not result, f"empty readings must not yield a match tuple: {result!r}"
    assert not mgr._spawn_tracked.called


def test_manual_override_still_delivers_a_synchronous_match(
    wrapper_and_manager: tuple[Any, Any],
) -> None:
    """The manual-program path is synchronous and must keep returning its match."""
    wrapper, mgr = wrapper_and_manager
    mgr._manual_program_active = True
    mgr._current_program = "Eco (Standard)"
    mgr._matched_profile_duration = 11660.0
    mgr.profile_store.check_phase_match = MagicMock(return_value="Drying")

    result = wrapper([(dt(0), 100.0), (dt(10), 120.0)])

    assert result is not None
    assert result[0] == "Eco (Standard)"
    assert result[1] == 1.0
    assert result[2] == 11660.0


def test_offload_does_not_wipe_the_live_match_confidence(
    wrapper_and_manager: tuple[Any, Any],
) -> None:
    """A running cycle keeps its match while the async matcher is in flight.

    This is the field failure: with a truthy placeholder the confidence drops to
    0.0 on every match trigger, so Smart Termination stays blocked behind its
    0.4 gate even though the manager holds a confident match.
    """
    wrapper, _mgr = wrapper_and_manager
    detector = CycleDetector(
        config=CycleDetectorConfig(
            min_power=5.0,
            off_delay=60,
            interrupted_min_seconds=150,
            completion_min_seconds=600,
            start_duration_threshold=0.0,
        ),
        on_state_change=MagicMock(),
        on_cycle_end=MagicMock(),
        profile_matcher=wrapper,
        device_name="Spuelmaschine",
    )
    detector.update_match(
        ("Eco (Standard)", 0.788, 11660.0, None, False, False, False)
    )
    assert detector._last_match_confidence == pytest.approx(0.788)
    detector._power_readings = [(dt(0), 100.0), (dt(10), 120.0)]

    detector._try_profile_match(dt(20), force=True)

    assert detector._last_match_confidence == pytest.approx(0.788), (
        "the in-flight async match wiped the live confidence"
    )
    assert detector._matched_profile == "Eco (Standard)"
    assert detector._expected_duration == pytest.approx(11660.0)
