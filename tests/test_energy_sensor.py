"""Tests for the optional native energy meter (energy_sensor) feature."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ha_washdata.const import (
    CONF_ENERGY_SENSOR,
    STATE_OFF,
    STATE_RUNNING,
)
from custom_components.ha_washdata.manager import WashDataManager

ENERGY_SENSOR = "sensor.test_energy"


def _build_manager(hass: HomeAssistant, entry: Any) -> WashDataManager:
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ):
        return WashDataManager(hass, entry)


@pytest.fixture
def mock_entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Test Washer"
    entry.options = {
        "power_sensor": "sensor.test_power",
        CONF_ENERGY_SENSOR: ENERGY_SENSOR,
    }
    entry.data = {}
    return entry


@pytest.fixture
def manager(hass: HomeAssistant, mock_entry: Any) -> WashDataManager:
    return _build_manager(hass, mock_entry)


async def test_read_energy_counter_normalizes_kwh(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "1.234", {"unit_of_measurement": "kWh"})
    assert manager._read_energy_counter_wh() == pytest.approx(1234.0)


async def test_read_energy_counter_wh_passthrough(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "250.5", {"unit_of_measurement": "Wh"})
    assert manager._read_energy_counter_wh() == pytest.approx(250.5)


async def test_read_energy_counter_normalizes_mwh(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "0.001", {"unit_of_measurement": "MWh"})
    assert manager._read_energy_counter_wh() == pytest.approx(1000.0)


async def test_read_energy_counter_unavailable_returns_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "unavailable", {"unit_of_measurement": "kWh"})
    assert manager._read_energy_counter_wh() is None


async def test_read_energy_counter_missing_unit_returns_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "1.0", {})
    assert manager._read_energy_counter_wh() is None


async def test_read_energy_counter_non_numeric_returns_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "abc", {"unit_of_measurement": "kWh"})
    assert manager._read_energy_counter_wh() is None


async def test_read_energy_counter_not_configured_returns_none(
    hass: HomeAssistant, mock_entry: Any
) -> None:
    mock_entry.options = {"power_sensor": "sensor.test_power"}
    mgr = _build_manager(hass, mock_entry)
    assert mgr.energy_sensor_entity_id is None
    assert mgr._read_energy_counter_wh() is None


async def test_new_cycle_start_snapshots_energy_counter(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    """Entering RUNNING from OFF must snapshot the configured energy meter."""
    hass.states.async_set(ENERGY_SENSOR, "353.527", {"unit_of_measurement": "kWh"})
    manager.detector.current_cycle_start = dt_util.now()
    manager._start_watchdog = MagicMock()
    manager._notify_update = MagicMock()

    manager._on_state_change(STATE_OFF, STATE_RUNNING)

    assert manager._energy_counter_start_wh == pytest.approx(353527.0)
    assert manager._energy_snapshot_entity_id == ENERGY_SENSOR


async def test_state_off_clears_energy_snapshot(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    """Transitioning back to OFF must drop any leftover meter snapshot."""
    manager._energy_counter_start_wh = 123.0
    manager._energy_snapshot_entity_id = ENERGY_SENSOR
    manager._notify_update = MagicMock()

    manager._on_state_change(STATE_RUNNING, STATE_OFF)

    assert manager._energy_counter_start_wh is None
    assert manager._energy_snapshot_entity_id is None


def _cycle_data() -> dict[str, Any]:
    # 100 W flat for 1 h -> integrated energy exactly 100.0 Wh
    return {
        "id": "cycle-1",
        "start_time": "2026-01-01T10:00:00+00:00",
        "duration": 5460,
        "max_power": 2000,
        "status": "completed",
        "power_data": [[0.0, 100.0], [3600.0, 100.0]],
    }


async def test_cycle_end_uses_meter_delta(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 353087.0
    hass.states.async_set(ENERGY_SENSOR, "353.527", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(440.0)
    assert cycle_data["energy_source"] == "meter"
    assert manager._energy_counter_start_wh is None


async def test_cycle_end_meter_swap_falls_back_to_integration(
    hass: HomeAssistant, manager: WashDataManager, mock_entry: Any
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 500.0
    manager._energy_snapshot_entity_id = ENERGY_SENSOR
    other_sensor = "sensor.other_energy"
    mock_entry.options = {**mock_entry.options, CONF_ENERGY_SENSOR: other_sensor}
    hass.states.async_set(other_sensor, "353.527", {"unit_of_measurement": "kWh"})

    cycle_data = _cycle_data()
    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"


async def test_cycle_end_meter_reset_falls_back_to_integration(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 500.0
    # 0.1 kWh = 100 Wh < 500 Wh start -> negative delta -> meter reset assumed
    hass.states.async_set(ENERGY_SENSOR, "0.1", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"


async def test_cycle_end_meter_unavailable_falls_back_to_integration(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 500.0
    hass.states.async_set(ENERGY_SENSOR, "unavailable", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"


async def test_cycle_end_without_start_snapshot_keeps_integration(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = None
    hass.states.async_set(ENERGY_SENSOR, "353.527", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"


async def test_check_state_save_includes_meter_snapshot(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager.detector.get_state_snapshot = MagicMock(return_value={})
    manager.profile_store.async_save_active_cycle = AsyncMock()
    manager._energy_counter_start_wh = 1234.5
    manager._last_state_save = None

    manager._check_state_save(dt_util.now())
    await hass.async_block_till_done()

    snapshot = manager.profile_store.async_save_active_cycle.call_args[0][0]
    assert snapshot["energy_counter_start_wh"] == 1234.5


async def test_restoration_restores_meter_snapshot(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    snapshot = {
        "state": STATE_RUNNING,
        "notified_start": True,
        "energy_counter_start_wh": 4321.0,
    }
    manager.profile_store.get_active_cycle = MagicMock(return_value=snapshot)
    manager.profile_store.get_last_active_save = MagicMock(return_value=dt_util.now())
    manager.profile_store.get_profiles = MagicMock(return_value={})
    manager.detector.restore_state_snapshot = MagicMock()
    manager.detector.state = STATE_RUNNING
    manager._start_watchdog = MagicMock()

    await manager._attempt_state_restoration()

    assert manager._energy_counter_start_wh == pytest.approx(4321.0)


async def test_restoration_without_meter_key_leaves_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    snapshot = {"state": STATE_RUNNING, "notified_start": False}
    manager.profile_store.get_active_cycle = MagicMock(return_value=snapshot)
    manager.profile_store.get_last_active_save = MagicMock(return_value=dt_util.now())
    manager.profile_store.get_profiles = MagicMock(return_value={})
    manager.detector.restore_state_snapshot = MagicMock()
    manager.detector.state = STATE_RUNNING
    manager._start_watchdog = MagicMock()

    await manager._attempt_state_restoration()

    assert manager._energy_counter_start_wh is None


def test_user_schema_accepts_optional_energy_sensor() -> None:
    from custom_components.ha_washdata.config_flow import STEP_USER_DATA_SCHEMA

    base = {
        "name": "Washer",
        "device_type": "washing_machine",
        "power_sensor": "sensor.p",
        "min_power": 2.0,
    }
    with_sensor = STEP_USER_DATA_SCHEMA({**base, "energy_sensor": "sensor.e"})
    assert with_sensor["energy_sensor"] == "sensor.e"

    without_sensor = STEP_USER_DATA_SCHEMA(dict(base))
    assert "energy_sensor" not in without_sensor
