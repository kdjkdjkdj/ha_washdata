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
"""Curve decimation for the panel.

The inspector's curve is not decoration: the user reads a trim or split offset off
it, and that offset is then applied to the full-resolution stored data. A sample
the decimator drops is therefore a sample the user cannot aim at, in front of an
irreversible operation. These tests pin the two properties that follow from that.
"""
from __future__ import annotations

from custom_components.ha_washdata.ws_api import _downsample, _INSPECTOR_MAX_POINTS


def _shutdown_curve() -> list[list[float]]:
    """A dishwasher tail: standby plateau, wake-up pulse, one 0 W sample, standby.

    Mirrors the real 2026-08-06 curve, whose shutdown was a single 0.0 W reading
    between 11.7 W and 0.5 W.
    """
    pts: list[list[float]] = [[i * 10.0, 2.6] for i in range(300)]
    base = pts[-1][0]
    pts += [[base + 10.0, 12.8], [base + 20.0, 11.8], [base + 30.0, 11.7]]
    pts += [[base + 40.0, 0.0]]  # <- the self-shutdown, one sample wide
    pts += [[base + 50.0 + i * 10.0, 0.5] for i in range(60)]
    return pts


def test_short_series_is_returned_untouched() -> None:
    """At or below the budget nothing is dropped - only rounded."""
    pts = [[float(i), float(i % 7)] for i in range(50)]
    assert _downsample(pts, 240) == pts


def test_result_never_exceeds_the_budget() -> None:
    pts = [[float(i), float(i % 13)] for i in range(5000)]
    for budget in (10, 61, 240, 999):
        assert len(_downsample(pts, budget)) <= budget


def test_first_and_last_sample_always_survive() -> None:
    pts = [[float(i), float(i % 13)] for i in range(5000)]
    out = _downsample(pts, 100)
    assert out[0][0] == 0.0
    assert out[-1][0] == 4999.0


def test_time_order_is_preserved() -> None:
    pts = [[float(i), float((i * 37) % 100)] for i in range(3000)]
    out = _downsample(pts, 120)
    assert [p[0] for p in out] == sorted(p[0] for p in out)


def test_isolated_zero_survives_decimation() -> None:
    """The regression this decimator exists for.

    A single 0 W sample is the whole end-of-cycle signature. Striding dropped two
    of the four zeros in a real 376-sample curve; a curve without its zero makes a
    finished cycle look like one that never stopped, and there is nothing left for
    a trim to aim at.
    """
    pts = _shutdown_curve()
    zero_offset = next(o for o, w in pts if w == 0.0)

    out = _downsample(pts, 100)

    assert len(out) < len(pts), "curve should have been decimated for this to mean anything"
    assert [o for o, w in out if w == 0.0] == [zero_offset]


def test_isolated_spike_survives_decimation() -> None:
    """The same guarantee on the other side: a brief peak is not smoothed away."""
    pts = [[float(i), 100.0] for i in range(2000)]
    pts[1234] = [1234.0, 2400.0]

    out = _downsample(pts, 80)

    assert [1234.0, 2400.0] in out


def test_striding_would_have_lost_the_zero() -> None:
    """Guards the fix itself: the old algorithm fails the case above.

    Kept as an executable record of why the decimator is not plain striding, so a
    future simplification back to every-n-th-sample fails loudly here.
    """
    pts = _shutdown_curve()
    n, budget = len(pts), 100
    step = n / float(budget)
    strided = {pts[int(i * step)][0] for i in range(budget) if int(i * step) < n}
    strided.add(pts[-1][0])
    zero_offset = next(o for o, w in pts if w == 0.0)

    assert zero_offset not in strided


def test_inspector_budget_covers_real_cycles() -> None:
    """A real cycle must reach the trim view whole.

    Measured on a live install (2026-08-06): 151 stored cycles, the largest 1009
    samples. The inspector budget is set so none of those is decimated at all.
    """
    assert _INSPECTOR_MAX_POINTS >= 2000

    largest_real_cycle = [[float(i), float(i % 400)] for i in range(1009)]
    assert _downsample(largest_real_cycle, _INSPECTOR_MAX_POINTS) == largest_real_cycle


def test_empty_and_broken_input_do_not_raise() -> None:
    assert _downsample(None) == []
    assert _downsample([]) == []
    # A non-numeric watt value must not take the whole reply down; it sorts as 0.0.
    pts: list[list] = [[float(i), float(i % 5)] for i in range(500)]
    pts[250] = [250.0, "n/a"]
    out = _downsample(pts, 50)
    assert len(out) <= 50
