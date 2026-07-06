"""Tests for the optional native energy meter (energy_sensor) feature."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.ha_washdata.const import CONF_ENERGY_SENSOR
from custom_components.ha_washdata.manager import WashDataManager

ENERGY_SENSOR = "sensor.test_energy"


def _build_manager(hass: HomeAssistant, entry: Any) -> WashDataManager:
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ):
        mgr = WashDataManager(hass, entry)
        mgr.profile_store.get_suggestions = MagicMock(return_value={})
        return mgr


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
