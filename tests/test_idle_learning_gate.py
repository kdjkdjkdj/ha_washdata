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
"""Regression tests: idle readings must not feed the learning cadence stats.

``LearningManager.process_power_reading`` keeps a rolling window of the last 200
reading gaps and, every 300 s, derives the operational suggestions
(``watchdog_interval``, ``no_update_active_timeout``, ``off_delay``,
``profile_match_interval``) from its p95/median.

Those statistics are only meaningful for readings taken *while the appliance is
running*. A plug that keeps publishing between cycles - either because it emits
state reports on an unchanged value, or because its standby figure jitters - can
otherwise fill the whole window with idle cadence within a few hours. The
suggestions are then computed from standby traffic and drift far away from what
the appliance actually needs.

The detector must keep seeing every reading regardless: it is what detects the
next cycle start. So the gate belongs on the learning call, not on the reading
path.

Measured against v0.5.4 on two live installs (2026-08-18): with the gate absent
every appliance logged ``Applied 3-4 setting suggestion(s)`` on the five-minute
tick while standing idle.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ha_washdata.const import (
    DEVICE_TYPE_DRYER,
    STATE_ANTI_WRINKLE,
    STATE_ENDING,
    STATE_OFF,
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_STARTING,
)
from custom_components.ha_washdata.manager import WashDataManager


# ---------------------------------------------------------------------------
# Manager fixtures (mirrors tests/test_anti_wrinkle_silent_close.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "test_entry_idle_gate"
    entry.title = "Test Dryer Idle Gate"
    entry.options = {
        "power_sensor": "sensor.test_power",
        "device_type": DEVICE_TYPE_DRYER,
    }
    entry.data = {}
    return entry


@pytest.fixture
def manager(hass: HomeAssistant, mock_entry: Any) -> WashDataManager:
    hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ):
        mgr = WashDataManager(hass, mock_entry)
        mgr.profile_store.get_suggestions = MagicMock(return_value={})
        return mgr


def _make_power_event(new_power: float, old_power: float, ts: datetime) -> Any:
    new_state = types.SimpleNamespace(state=str(new_power), last_updated=ts)
    old_state = types.SimpleNamespace(state=str(old_power))
    return types.SimpleNamespace(data={"new_state": new_state, "old_state": old_state})


def _wire_manager(manager: WashDataManager, state: str) -> MagicMock:
    """Wire the manager so a single reading passes the sampling throttle."""
    detector = manager.detector
    detector.state = state
    detector.config = MagicMock()
    detector.config.min_power = 2.0
    detector.config.anti_wrinkle_exit_power = 0.8
    detector.config.stop_threshold_w = 2.0
    detector.current_cycle_start = dt_util.now() - timedelta(hours=1)
    detector.process_reading = MagicMock()
    manager.recorder = MagicMock()
    manager.recorder.is_recording = False
    manager.diag_buffer = MagicMock()
    manager.learning_manager = MagicMock()
    manager._notify_update = MagicMock()
    manager._update_estimates = MagicMock()
    manager._check_state_save = MagicMock()
    manager._sampling_interval = 30.0
    manager._current_power = 0.0
    # Well outside the throttle window, so the reading is accepted.
    manager._last_reading_time = dt_util.now() - timedelta(seconds=120)
    manager._last_real_reading_time = manager._last_reading_time
    return detector


@pytest.mark.parametrize("state", [STATE_OFF, STATE_ANTI_WRINKLE])
def test_idle_reading_reaches_detector_but_not_learning(
    hass: HomeAssistant, manager: WashDataManager, state: str
) -> None:
    """Between cycles the reading must still reach the detector - it is what
    spots the next cycle start - but must not enter the cadence statistics."""
    detector = _wire_manager(manager, state)

    manager._async_power_changed(_make_power_event(0.0, 0.0, dt_util.now()))

    detector.process_reading.assert_called_once()
    manager.learning_manager.process_power_reading.assert_not_called()


@pytest.mark.parametrize(
    "state", [STATE_RUNNING, STATE_PAUSED, STATE_ENDING, STATE_STARTING]
)
def test_running_reading_still_feeds_learning(
    hass: HomeAssistant, manager: WashDataManager, state: str
) -> None:
    """While a cycle is in progress the statistics are fed exactly as before."""
    detector = _wire_manager(manager, state)

    manager._async_power_changed(_make_power_event(75.0, 75.0, dt_util.now()))

    detector.process_reading.assert_called_once()
    manager.learning_manager.process_power_reading.assert_called_once()


def test_gap_handed_to_learning_is_the_previous_reading_time(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    """The gap argument must remain the *previous* reading time, not ``now`` -
    otherwise every sample would be zero and p95/median would collapse."""
    detector = _wire_manager(manager, STATE_RUNNING)
    previous = manager._last_reading_time

    manager._async_power_changed(_make_power_event(75.0, 75.0, dt_util.now()))

    assert manager.learning_manager.process_power_reading.call_args.args[2] == previous

