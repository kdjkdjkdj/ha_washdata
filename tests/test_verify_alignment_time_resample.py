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
"""``async_verify_alignment`` must read elapsed time, not sample count.

The envelope is built from time-resampled curves - ``compute_envelope_worker``
interpolates every member cycle onto a uniform grid - but the live trace used to
be handed to the DTW as raw sample values with the timestamps dropped.  The
mapped index is therefore driven by the *number* of samples; the worker's
no-path fallback states it outright (``offset + len(curr) - 1``).

That is invisible while readings arrive at the cadence the envelope was built
at, and decisive once they do not: in a standby tail the meter goes quiet and
the only readings are the watchdog's 0 W keepalives, one per ``off_delay``.
Each advanced the mapped position by a single grid step regardless of how much
wall-clock time it stood for.

The invariant these tests pin down: **two traces covering the same elapsed time
must map to the same place, however many samples they happen to contain.**
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.ha_washdata.profile_store import ProfileStore

PROFILE = "Test Program"
GRID_STEP = 10.0
GRID_POINTS = 121  # 0 .. 1200 s
SPAN = GRID_STEP * (GRID_POINTS - 1)


def _envelope_curve(t: float) -> float:
    """A heating hump followed by a long low-power tail - the shape that matters.

    The tail is what a standby-quiet cycle aligns against, and it is deliberately
    flat: that is precisely where sample-count-driven mapping goes wrong, because
    there is no structure left for the DTW to grip.
    """
    if t < 200.0:
        return 5.0
    if t < 500.0:
        return 1800.0
    return 3.0


@pytest.fixture
def mock_hass() -> MagicMock:
    hass = MagicMock()
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *a: f(*a))
    return hass


@pytest.fixture
def store(mock_hass: MagicMock) -> ProfileStore:
    with patch("custom_components.ha_washdata.profile_store.WashDataStore"):
        ps = ProfileStore(mock_hass, "test_entry")
        ps._store.async_load = AsyncMock(return_value=None)
        ps._store.async_save = AsyncMock()

    grid = [i * GRID_STEP for i in range(GRID_POINTS)]
    ps._data["envelopes"] = {
        PROFILE: {
            "avg": [[t, _envelope_curve(t)] for t in grid],
            "time_grid": grid,
            "target_duration": SPAN,
            "cycle_count": 5,
        }
    }
    return ps


def _trace(until_s: float, step_s: float) -> list[list[float]]:
    """Trace of the same programme sampled at ``step_s``, ending at ``until_s``."""
    n = int(until_s / step_s)
    return [[i * step_s, _envelope_curve(i * step_s)] for i in range(n + 1)]


@pytest.mark.asyncio
async def test_same_elapsed_time_maps_to_same_place(store: ProfileStore) -> None:
    """A sparse trace and a dense one covering 900 s must agree.

    Before the fix the sparse trace mapped far short of the dense one purely
    because it carried fewer samples.
    """
    dense = _trace(900.0, GRID_STEP)          # 91 samples, one per grid slot
    sparse = _trace(900.0, 300.0)             # 4 samples over the same 900 s

    _, mapped_dense, _ = await store.async_verify_alignment(PROFILE, dense)
    _, mapped_sparse, _ = await store.async_verify_alignment(PROFILE, sparse)

    assert mapped_dense > 0.0, "dense trace produced no mapping at all"
    # Within one tenth of the span of each other - the point is that the sample
    # count no longer decides, not that the DTW is exact.
    assert math.isclose(mapped_sparse, mapped_dense, abs_tol=SPAN * 0.1), (
        f"sparse mapped to {mapped_sparse:.0f}s but dense to {mapped_dense:.0f}s"
    )


@pytest.mark.asyncio
async def test_sparse_tail_reaches_the_end_of_the_envelope(store: ProfileStore) -> None:
    """A cycle quiet to the very end must map near the end, on sparse readings.

    This is the standby-tail case: real samples up to 500 s, then only watchdog
    keepalives every 300 s.  It used to sit far short of the end no matter how
    long it waited, which is what kept a verified pause alive until the
    max-deferral cap.
    """
    trace = _trace(500.0, GRID_STEP)
    t = 500.0
    while t < SPAN:
        t += 300.0
        trace.append([t, 0.0])

    _, mapped, _ = await store.async_verify_alignment(PROFILE, trace)

    assert mapped >= SPAN * 0.9, (
        f"mapped only to {mapped:.0f}s of a {SPAN:.0f}s envelope"
    )


@pytest.mark.asyncio
async def test_partial_cycle_still_maps_short(store: ProfileStore) -> None:
    """Resampling must not push every cycle to the end.

    Guards the obvious failure mode of the fix: if elapsed time were ignored in
    the other direction, a half-finished cycle would look complete and the
    release would open for every running cycle.
    """
    trace = _trace(SPAN * 0.4, GRID_STEP)

    _, mapped, _ = await store.async_verify_alignment(PROFILE, trace)

    assert mapped < SPAN * 0.75, (
        f"a 40% cycle mapped to {mapped:.0f}s of {SPAN:.0f}s"
    )


@pytest.mark.parametrize(
    "trace",
    [
        [],                                   # nothing at all
        [[0.0, 3.0]],                         # single sample - no span
        [[10.0, 3.0], [10.0, 3.0]],           # zero elapsed time
    ],
)
@pytest.mark.asyncio
async def test_degenerate_traces_do_not_raise(
    store: ProfileStore, trace: list[list[float]]
) -> None:
    """Too little to resample -> fall through to the raw values, never raise."""
    is_confirmed, mapped, power = await store.async_verify_alignment(PROFILE, trace)

    assert isinstance(is_confirmed, bool)
    assert mapped >= 0.0
    assert power >= 0.0


@pytest.mark.asyncio
async def test_missing_envelope_is_unchanged(store: ProfileStore) -> None:
    """No envelope -> the documented not-confirmed tuple, as before."""
    is_confirmed, mapped, power = await store.async_verify_alignment(
        "no such profile", _trace(900.0, GRID_STEP)
    )

    assert is_confirmed is False
    assert mapped == 0.0
    assert power == 9999.0
