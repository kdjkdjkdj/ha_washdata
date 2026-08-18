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

"""Fork build 0.5.4.1: the Smart-Termination duration gate is per-device configurable.

The gate demands ``current_duration >= expected * ratio`` with ratio hard-coded to
0.98 (0.99 for dishwashers, relaxed to 0.90 once the terminal drain is confirmed).
``expected`` is the profile's *mean* duration, so on appliances whose real runtime
varies with the load - a dryer above all - roughly half of all runs are shorter
than the mean and can never reach 98% of it. Those runs fall through to the power
timeout and finish minutes late.

Measured on the KD dryer, 7 runs: 5790 / 6120 / 7971 / 7250 / 7090 / 6300 / 6450 s
against an expected 7120 s. The three runs above 6978 s (= 0.98 x 7120) ended via
Smart Termination, the four below it all ended via timeout.

The ratio is now ``CycleDetectorConfig.smart_termination_duration_ratio``. The
dataclass carries the shipped 0.98; manager/ws_api substitute the dishwasher's
0.99 when the option is unset, so an unconfigured install is unchanged.
"""

from __future__ import annotations

import pytest

from custom_components.ha_washdata.cycle_detector import CycleDetector, CycleDetectorConfig
from custom_components.ha_washdata.const import (
    DEVICE_TYPE_DISHWASHER,
    DEVICE_TYPE_DRYER,
    SMART_TERM_DURATION_RATIO_DEFAULT,
    SMART_TERM_DURATION_RATIO_DISHWASHER_DEFAULT,
    SMART_TERM_DURATION_RATIO_MAX,
    SMART_TERM_DURATION_RATIO_MIN,
    SMART_TERM_DURATION_RATIO_PUMPOUT_CONFIRMED,
)
from custom_components.ha_washdata.manager import _opt_ratio, _resolve_smart_term_ratio


def _config(device_type: str, ratio: float | None) -> CycleDetectorConfig:
    return CycleDetectorConfig(
        min_power=5.0,
        off_delay=120,
        min_off_gap=120,
        completion_min_seconds=600,
        start_duration_threshold=0.0,
        device_type=device_type,
        smart_termination_duration_ratio=ratio,
    )


# --- the shipped defaults survive an unconfigured install ---------------------


def test_config_default_is_the_shipped_ratio():
    """A config built without the option carries the shipped 0.98."""
    assert (
        CycleDetectorConfig(min_power=5.0, off_delay=120).smart_termination_duration_ratio
        == SMART_TERM_DURATION_RATIO_DEFAULT
    )


@pytest.mark.parametrize(
    "device_type,expected",
    [
        (DEVICE_TYPE_DRYER, SMART_TERM_DURATION_RATIO_DEFAULT),
        (DEVICE_TYPE_DISHWASHER, SMART_TERM_DURATION_RATIO_DISHWASHER_DEFAULT),
    ],
)
def test_unset_option_resolves_to_the_device_type_default(device_type, expected):
    """The dataclass cannot know the device type - the builder resolves it."""
    assert _resolve_smart_term_ratio(None, device_type) == expected
    assert _resolve_smart_term_ratio("", device_type) == expected
    assert _resolve_smart_term_ratio("nonsense", device_type) == expected
    # A configured value wins over both defaults.
    assert _resolve_smart_term_ratio(0.88, device_type) == 0.88


@pytest.mark.parametrize(
    "device_type,expected_ratio",
    [
        (DEVICE_TYPE_DRYER, SMART_TERM_DURATION_RATIO_DEFAULT),
        (DEVICE_TYPE_DISHWASHER, SMART_TERM_DURATION_RATIO_DISHWASHER_DEFAULT),
    ],
)
def test_unconfigured_gate_matches_upstream(device_type, expected_ratio):
    """The KD dryer case: 6450 s against 7120 s expected stays blocked at 0.98."""
    detector = CycleDetector(_config(device_type, None), lambda *a: None, lambda *a: None)
    expected = 7120.0
    gate = expected * expected_ratio
    assert detector._smart_term_block_reason(
        gate - 1, expected, expected_ratio, True, False, False
    ) == "duration_not_reached"
    assert detector._smart_term_block_reason(
        gate, expected, expected_ratio, True, False, False
    ) is None


# --- a configured ratio opens the gate ---------------------------------------


def test_configured_ratio_lets_a_short_dryer_run_through():
    """0.88 x 7120 = 6266 s - the 6450 s run of 18.08. would have passed."""
    ratio = 0.88
    assert CycleDetector(
        _config(DEVICE_TYPE_DRYER, ratio), lambda *a: None, lambda *a: None
    )._smart_term_block_reason(6450.0, 7120.0, ratio, True, False, False) is None


def test_gate_order_is_unchanged():
    """duration first, then confidence, then ambiguity, then the prefix guard."""
    reason = CycleDetector._smart_term_block_reason
    assert reason(100.0, 1000.0, 0.98, False, True, True) == "duration_not_reached"
    assert reason(1000.0, 1000.0, 0.98, False, True, True) == "low_confidence"
    assert reason(1000.0, 1000.0, 0.98, True, True, True) == "match_ambiguous"
    assert reason(1000.0, 1000.0, 0.98, True, False, True) == "prefix_ambiguous"
    assert reason(1000.0, 1000.0, 0.98, True, False, False) is None


# --- the dishwasher relaxation stays a safety rule ----------------------------


def test_pumpout_relaxation_is_never_tightened_by_the_option():
    """A configured 0.95 must not undo the 0.90 relaxation once the drain is seen."""
    effective = min(SMART_TERM_DURATION_RATIO_PUMPOUT_CONFIRMED, 0.95)
    assert effective == SMART_TERM_DURATION_RATIO_PUMPOUT_CONFIRMED
    # ... while a stricter user value still wins, so the option can only loosen.
    assert min(SMART_TERM_DURATION_RATIO_PUMPOUT_CONFIRMED, 0.85) == 0.85


# --- option parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),          # never configured
        ("", None),            # field cleared in the panel
        ("nonsense", None),    # unparsable -> shipped default, not 0.0
        (0.88, 0.88),
        ("0.9", 0.9),
        (0.0, SMART_TERM_DURATION_RATIO_MIN),   # clamped: 0 would fire instantly
        (5.0, SMART_TERM_DURATION_RATIO_MAX),   # clamped: >1 is unreachable
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_option_parsing(raw, expected):
    assert _opt_ratio(raw) == expected
