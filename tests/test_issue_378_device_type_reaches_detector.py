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

"""Issue #378: the configured device_type has to reach CycleDetectorConfig.

The detector branches on ``self._config.device_type`` in 16 places - the dishwasher
end-spike wait, ``keep_tail``, the standby-plateau finalizer, the terminal-tail match
freeze.  The field defaults to ``washing_machine``, so a construction that forgets to
pass it makes every appliance run the washing-machine paths, silently: the manager's
own ``self.device_type`` stays correct, so phase naming and the ghost-cycle suppressor
keep working and nothing looks wrong from the outside.

Both write sites are covered: construction and the in-place update in
``async_reload_config`` (missing there, changing the type in the UI would not fix a
running manager either).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_washdata.const import (
    CONF_DEVICE_TYPE,
    CONF_MIN_POWER,
    CONF_POWER_SENSOR,
    DEVICE_TYPE_DISHWASHER,
    DEVICE_TYPE_DRYER,
    DEVICE_TYPE_WASHING_MACHINE,
    STATE_OFF,
)
from custom_components.ha_washdata.cycle_detector import CycleDetectorConfig
from custom_components.ha_washdata.manager import WashDataManager


@pytest.fixture
def mock_hass() -> Any:
    hass = MagicMock()
    hass.data = {}
    hass.services.async_call = AsyncMock()
    hass.bus.async_fire = MagicMock()
    hass.async_create_task = MagicMock(
        side_effect=lambda coro: getattr(coro, "close", lambda: None)()
    )
    hass.config_entries.async_get_entry = MagicMock()
    return hass


def _entry(device_type: str) -> Any:
    entry = MagicMock()
    entry.entry_id = "test_entry_378"
    entry.title = "Test Appliance 378"
    entry.options = {
        CONF_POWER_SENSOR: "sensor.test_power",
        CONF_MIN_POWER: 2.0,
        CONF_DEVICE_TYPE: device_type,
    }
    entry.data = {}
    return entry


def _wire_store(mgr: WashDataManager) -> None:
    """Minimal profile-store wiring, as in test_manager.py's reload tests."""
    mgr.profile_store.get_suggestions = MagicMock(return_value={})
    mgr.profile_store.get_duration_ratio_limits = MagicMock(return_value=(0.7, 1.3))
    mgr.profile_store.set_duration_ratio_limits = MagicMock()
    mgr.profile_store.get_active_cycle = MagicMock(return_value={"manual_program": False})
    mgr.profile_store.get_past_cycles = MagicMock(return_value=[])
    mgr.profile_store.get_last_active_save = MagicMock(return_value=None)
    mgr.profile_store.async_clear_active_cycle = AsyncMock()
    mgr._setup_maintenance_scheduler = AsyncMock()


def _config_from_call(mock_detector: MagicMock) -> CycleDetectorConfig:
    """Pull the CycleDetectorConfig out of the CycleDetector(...) call.

    Found by type rather than by position so the assertion survives a signature
    change in the detector constructor.
    """
    args, kwargs = mock_detector.call_args
    for candidate in (*args, *kwargs.values()):
        if isinstance(candidate, CycleDetectorConfig):
            return candidate
    raise AssertionError("CycleDetector was not called with a CycleDetectorConfig")


@pytest.mark.parametrize(
    "device_type",
    [DEVICE_TYPE_DISHWASHER, DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHING_MACHINE],
)
def test_construction_passes_device_type(mock_hass: Any, device_type: str) -> None:
    """The detector must be built with the entry's device type, not the default."""
    entry = _entry(device_type)
    mock_hass.config_entries.async_get_entry.return_value = entry
    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ) as mock_detector:
        mgr = WashDataManager(mock_hass, entry)
        _wire_store(mgr)

    assert mgr.device_type == device_type
    assert _config_from_call(mock_detector).device_type == device_type


async def test_reload_updates_device_type_in_place(mock_hass: Any) -> None:
    """Changing the appliance type in the UI must reach the running detector."""
    entry = _entry(DEVICE_TYPE_WASHING_MACHINE)
    mock_hass.config_entries.async_get_entry.return_value = entry
    mock_state = MagicMock()
    mock_state.state = "10.5"
    mock_hass.states.get = MagicMock(return_value=mock_state)

    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ), patch("custom_components.ha_washdata.manager.async_track_state_change_event"):
        mgr = WashDataManager(mock_hass, entry)
        _wire_store(mgr)

        # The detector itself is a mock; give it a real config so the in-place
        # update is observable instead of being swallowed by the mock.
        mgr.detector.config = CycleDetectorConfig(
            min_power=2.0, off_delay=180, device_type=DEVICE_TYPE_WASHING_MACHINE
        )
        mgr.detector.state = STATE_OFF

        await mgr.async_reload_config(_entry(DEVICE_TYPE_DISHWASHER))

    assert mgr.device_type == DEVICE_TYPE_DISHWASHER
    assert mgr.detector.config.device_type == DEVICE_TYPE_DISHWASHER
