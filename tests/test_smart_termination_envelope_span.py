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
"""The verified-pause release must be measured against the envelope's own span.

``mapped_time`` is a position on the envelope's time grid: the alignment worker
clamps the mapped index to the last grid slot and returns
``envelope_time_grid[idx]``, so the value can never exceed the envelope's
``target_duration``.  The release therefore has to be measured against that same
span.

Measuring it against ``avg_duration`` compares two differently-derived numbers:

* ``target_duration`` is the duration of the *median* member cycle
  (``compute_envelope_worker`` picks the cycle closest to the median duration),
* ``avg_duration`` is the outlier-trimmed *mean* of the member durations.

Whenever the mean runs longer than the median the release threshold
(``0.95 * avg_duration``) moves past the end of the grid and can never be
reached, so the cycle stays deferred until the max-deferral cap force-ends it.

The numbers used below are from a real profile in that state: a washing machine
whose "Kurz" program has two recorded runs of 1899 s and 2154 s.  The envelope
span is 1899 s (the median pick), the trimmed mean 2027 s, and 0.95 * 2027 =
1925.7 s -- 26.7 s beyond the end of a grid that stops at 1899 s.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ha_washdata.const import STATE_PAUSED
from custom_components.ha_washdata.manager import WashDataManager
from custom_components.ha_washdata.profile_store import MatchResult

PROFILE = "Kurz"

# The real profile that motivated the fix.
ENVELOPE_SPAN = 1899.0  # target_duration - duration of the median member cycle
AVG_DURATION = 2027.0  # outlier-trimmed mean of the member cycles


def _low_power_readings(count: int = 12, power: float = 1.0) -> list[tuple]:
    """Readings well below stop_threshold (machine sitting in its standby tail)."""
    now = dt_util.now()
    return [(now + timedelta(seconds=i * 30), power) for i in range(count)]


def _match_result() -> MatchResult:
    return MatchResult(
        best_profile=PROFILE,
        confidence=0.75,
        expected_duration=AVG_DURATION,
        matched_phase=None,
        candidates=[{"name": PROFILE, "score": 0.75}],
        is_ambiguous=False,
        ambiguity_margin=0.0,
    )


@pytest.fixture
def mock_entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "test_envelope_span"
    entry.title = "Test Washer"
    entry.options = {"power_sensor": "sensor.test_power"}
    entry.data = {}
    return entry


def _make_manager(hass: HomeAssistant, entry: Any, mapped_time: float,
                  envelope: dict | None) -> WashDataManager:
    """Manager sitting in a confirmed low-power pause with a given mapped_time."""
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    with (
        patch("custom_components.ha_washdata.manager.ProfileStore"),
        patch("custom_components.ha_washdata.manager.CycleDetector"),
    ):
        mgr = WashDataManager(hass, entry)
        mgr.profile_store.get_suggestions = MagicMock(return_value={})

        mgr.detector.matched_profile = PROFILE
        mgr.detector._verified_pause = True
        mgr.detector.state = STATE_PAUSED
        mgr.detector.config.stop_threshold_w = 5.0
        mgr.detector.get_elapsed_seconds = MagicMock(return_value=1800.0)
        mgr.detector.get_power_trace = MagicMock(return_value=[])
        mgr.detector.is_waiting_low_power = MagicMock(return_value=True)
        mgr.detector.set_verified_pause = MagicMock()
        mgr.detector.update_match = MagicMock()

        mgr.profile_store.async_match_profile = AsyncMock(return_value=_match_result())
        # Envelope CONFIRMS the low-power phase -> the release check is reached.
        mgr.profile_store.async_verify_alignment = AsyncMock(
            return_value=(True, mapped_time, 2.0)
        )
        mgr.profile_store.get_profile = MagicMock(
            return_value={"avg_duration": AVG_DURATION}
        )
        mgr.profile_store.get_envelope = MagicMock(return_value=envelope)

        mgr._current_program = PROFILE
        mgr._matched_profile_duration = AVG_DURATION
        mgr._notified_start = True

        mgr._update_remaining_only = MagicMock()
        mgr._check_live_progress_notification = MagicMock()
        mgr._notify_update = MagicMock()

        return mgr


def _last_verified_pause_call(mgr: WashDataManager) -> bool:
    assert mgr.detector.set_verified_pause.called, "set_verified_pause never called"
    return bool(mgr.detector.set_verified_pause.call_args[0][0])


@pytest.mark.asyncio
async def test_release_fires_against_envelope_span(
    hass: HomeAssistant, mock_entry: Any
) -> None:
    """Past 95% of the envelope span the pause lock is released.

    1850 s of a 1899 s span is 0.974 -- released.  Against ``avg_duration`` the
    same position is 1850/2027 = 0.913, which would NOT have released; this is
    exactly the case that used to hang until the max-deferral cap.
    """
    assert 1850.0 / ENVELOPE_SPAN > 0.95
    assert 1850.0 / AVG_DURATION < 0.95  # would not have released before the fix

    mgr = _make_manager(
        hass, mock_entry, mapped_time=1850.0,
        envelope={"target_duration": ENVELOPE_SPAN},
    )
    await mgr._async_do_perform_matching(_low_power_readings())

    assert _last_verified_pause_call(mgr) is False


@pytest.mark.asyncio
async def test_threshold_is_unreachable_against_avg_duration(
    hass: HomeAssistant, mock_entry: Any
) -> None:
    """Guard on the premise: the old threshold sat beyond the end of the grid.

    ``mapped_time`` is clamped to the last grid slot, so the best case is the
    full span -- and even that misses 0.95 * avg_duration.
    """
    assert ENVELOPE_SPAN / AVG_DURATION < 0.95

    # Even at the very end of the envelope the release must now fire.
    mgr = _make_manager(
        hass, mock_entry, mapped_time=ENVELOPE_SPAN,
        envelope={"target_duration": ENVELOPE_SPAN},
    )
    await mgr._async_do_perform_matching(_low_power_readings())

    assert _last_verified_pause_call(mgr) is False


@pytest.mark.asyncio
async def test_release_does_not_fire_early(
    hass: HomeAssistant, mock_entry: Any
) -> None:
    """Well inside the envelope the pause must be kept."""
    mapped = ENVELOPE_SPAN * 0.5
    assert mapped / ENVELOPE_SPAN < 0.95

    mgr = _make_manager(
        hass, mock_entry, mapped_time=mapped,
        envelope={"target_duration": ENVELOPE_SPAN},
    )
    await mgr._async_do_perform_matching(_low_power_readings())

    assert _last_verified_pause_call(mgr) is True


@pytest.mark.parametrize("envelope", [None, {}, {"target_duration": 0.0}])
@pytest.mark.asyncio
async def test_missing_span_keeps_pause_and_does_not_raise(
    hass: HomeAssistant, mock_entry: Any, envelope: dict | None
) -> None:
    """No usable span -> no release, no exception (same shape as the old guard)."""
    mgr = _make_manager(
        hass, mock_entry, mapped_time=ENVELOPE_SPAN, envelope=envelope,
    )
    await mgr._async_do_perform_matching(_low_power_readings())

    assert _last_verified_pause_call(mgr) is True
