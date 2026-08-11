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
"""Guard against a trim window that would destroy the cycle it trims.

The panel's clock-time mode could hand the store a window one second wide at the
very end of a cycle (upstream #366). The store accepted it: the inclusive filter
kept exactly one sample, so the "empty window" guard never fired, and what it
wrote back was duration 0, energy 0, no signature, start_time moved onto that
sample and power_data replaced by it - with no undo. These tests pin the refusal
and the untouched record that has to come with it.
"""
import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_washdata.profile_store import ProfileStore

START = datetime(2026, 8, 9, 21, 0, 0, tzinfo=timezone.utc)
CADENCE = 10.0
SAMPLES = 919                      # 0 .. 9180 s, a 153 min dishwasher cycle
FULL = (SAMPLES - 1) * CADENCE


@pytest.fixture
def mock_hass():
    hass = MagicMock()

    async def mock_executor_job(func, *args, **kwargs):
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)

    hass.async_add_executor_job = AsyncMock(side_effect=mock_executor_job)
    hass.async_create_task = lambda coro, *a: asyncio.create_task(coro)
    return hass


@pytest.fixture
def store(mock_hass):
    with patch("custom_components.ha_washdata.profile_store.WashDataStore"):
        ps = ProfileStore(mock_hass, "test_entry_id")
        ps._store.async_load = AsyncMock(return_value=None)
        ps._store.async_save = AsyncMock()
        ps.async_save = AsyncMock()
        return ps


def _cycle():
    """A finished cycle: 11.4 W idle floor with two heating phases."""
    power_data = []
    for i in range(SAMPLES):
        offset = i * CADENCE
        heating = 300 <= offset <= 900 or 6000 <= offset <= 6600
        power_data.append([offset, 2050.0 if heating else 11.4])
    return {
        "id": "cyc-guard",
        "start_time": START.isoformat(),
        "end_time": (START + timedelta(seconds=FULL)).isoformat(),
        "duration": FULL,
        "energy_wh": 762.0,
        "power_data": power_data,
        "profile_name": None,
        "status": "completed",
    }


@pytest.fixture
def cycle(store):
    c = _cycle()
    store._data["past_cycles"] = [c]
    return c


@pytest.mark.asyncio
async def test_one_second_window_at_the_tail_is_refused(store, cycle):
    """The exact window the clock-time mode used to produce."""
    ok = await store.trim_cycle_power_data("cyc-guard", FULL - 1, FULL)

    assert ok is False
    assert len(cycle["power_data"]) == SAMPLES
    assert cycle["duration"] == FULL
    assert cycle["start_time"] == START.isoformat()
    assert "meta" not in cycle          # not even marked as edited


@pytest.mark.asyncio
async def test_window_on_a_single_sample_is_refused(store, cycle):
    """Start and end on the same offset - one sample is not a cycle."""
    ok = await store.trim_cycle_power_data("cyc-guard", 0.0, 0.0)

    assert ok is False
    assert len(cycle["power_data"]) == SAMPLES


@pytest.mark.asyncio
async def test_two_samples_are_still_allowed(store, cycle):
    """The guard draws the line at one sample, not at "suspiciously short"."""
    ok = await store.trim_cycle_power_data("cyc-guard", 0.0, CADENCE)

    assert ok is True
    assert len(cycle["power_data"]) == 2
    assert cycle["duration"] == CADENCE


@pytest.mark.asyncio
async def test_a_normal_trim_is_unaffected(store, cycle):
    """The tail trim this editor exists for."""
    ok = await store.trim_cycle_power_data("cyc-guard", 0.0, 8000.0)

    assert ok is True
    assert len(cycle["power_data"]) == 801
    assert cycle["duration"] == 8000.0
    assert cycle["signature"] is not None
    assert cycle["energy_wh"] > 0


@pytest.mark.asyncio
async def test_a_refused_trim_leaves_the_record_trimmable(store, cycle):
    """What made #366 unrecoverable: after the collapse every later window was
    zero seconds wide, because full_duration_s had become 0.0."""
    assert await store.trim_cycle_power_data("cyc-guard", FULL - 1, FULL) is False

    ok = await store.trim_cycle_power_data("cyc-guard", 0.0, 8000.0)

    assert ok is True
    assert cycle["duration"] == 8000.0
