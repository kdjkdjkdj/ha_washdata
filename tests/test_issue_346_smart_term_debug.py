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

"""Issue #346: make the silent Smart-Termination decisions traceable.

Two DEBUG lines are added at the two decision points (no behaviour change). The
detector-side reason is derived by a pure helper so the (otherwise buried) gate
logic is unit-testable: it returns why the fast end-path did NOT fire, or None
when the gate would pass or no expected duration is known yet.
"""

from custom_components.ha_washdata.cycle_detector import CycleDetector


def test_gate_passes_returns_none():
    # duration reached, confident, not ambiguous, not prefix-ambiguous
    assert CycleDetector._smart_term_block_reason(
        current_duration=1000.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=False,
    ) is None


def test_no_expected_duration_is_suppressed():
    assert CycleDetector._smart_term_block_reason(
        current_duration=500.0, expected=0.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=False,
    ) is None


def test_duration_not_reached():
    assert CycleDetector._smart_term_block_reason(
        current_duration=100.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=False,
    ) == "duration_not_reached"


def test_low_confidence():
    assert CycleDetector._smart_term_block_reason(
        current_duration=1000.0, expected=1000.0, smart_ratio=0.98,
        is_confident=False, ambiguous=False, prefix_ambiguous=False,
    ) == "low_confidence"


def test_match_ambiguous_takes_priority_over_prefix():
    assert CycleDetector._smart_term_block_reason(
        current_duration=1000.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=True, prefix_ambiguous=True,
    ) == "match_ambiguous"


def test_prefix_ambiguous():
    assert CycleDetector._smart_term_block_reason(
        current_duration=1000.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=True,
    ) == "prefix_ambiguous"


def test_reset_clears_the_block_reason_throttle():
    """A new cycle must be able to emit its first diagnostic.

    The DEBUG line is throttled to fire only when the reason CHANGES. Carrying
    the previous cycle's reason across ``reset()`` swallowed the new cycle's very
    first "Smart Termination not applied" line whenever it happened to be blocked
    for the same reason - the common case, since the same appliance tends to hit
    the same gate.
    """
    from unittest.mock import Mock

    from custom_components.ha_washdata.cycle_detector import CycleDetectorConfig

    det = CycleDetector(CycleDetectorConfig(min_power=5.0, off_delay=120), Mock(), Mock())
    det._last_smart_term_block_reasons = ("duration_not_reached",)

    det.reset()

    assert det._last_smart_term_block_reasons is None


def test_anti_wrinkle_reset_also_clears_the_block_reason_throttle():
    """The ANTI_WRINKLE reset path is a cycle boundary too.

    That branch deliberately preserves the below-threshold tallies; the
    diagnostic throttle is not one of them and must still clear.
    """
    from unittest.mock import Mock

    from custom_components.ha_washdata.const import STATE_ANTI_WRINKLE
    from custom_components.ha_washdata.cycle_detector import CycleDetectorConfig

    det = CycleDetector(CycleDetectorConfig(min_power=5.0, off_delay=120), Mock(), Mock())
    det._last_smart_term_block_reasons = ("low_confidence",)

    det.reset(STATE_ANTI_WRINKLE)

    assert det._last_smart_term_block_reasons is None


# ── every reason at once, not just the foremost one ────────────────────────────
#
# The gate is a conjunction, so several conditions routinely block together. The
# single-valued diagnostic stops at the first, which means fixing it only reveals
# the next one on the FOLLOWING run - and on an appliance that runs twice a week
# each of those rounds costs a real cycle.


def test_reasons_reports_every_blocking_condition_together():
    """The measured Tiny washer case: the duration gate and the prefix guard held
    at the same time, but only the former was ever named."""
    assert CycleDetector._smart_term_block_reasons(
        current_duration=1823.0, expected=2838.0, smart_ratio=0.85,
        is_confident=True, ambiguous=False, prefix_ambiguous=True,
    ) == ("duration_not_reached", "prefix_ambiguous")


def test_reasons_keeps_gate_order_so_the_first_entry_is_the_old_headline():
    reasons = CycleDetector._smart_term_block_reasons(
        current_duration=100.0, expected=1000.0, smart_ratio=0.98,
        is_confident=False, ambiguous=True, prefix_ambiguous=True,
        power_plausible=False,
    )
    assert reasons == (
        "duration_not_reached",
        "low_confidence",
        "match_ambiguous",
        "prefix_ambiguous",
        "still_active",
    )
    assert CycleDetector._smart_term_block_reason(
        current_duration=100.0, expected=1000.0, smart_ratio=0.98,
        is_confident=False, ambiguous=True, prefix_ambiguous=True,
        power_plausible=False,
    ) == reasons[0]


def test_reasons_is_empty_when_the_gate_would_pass():
    assert CycleDetector._smart_term_block_reasons(
        current_duration=1000.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=False,
    ) == ()


def test_reasons_is_empty_without_an_expected_duration():
    assert CycleDetector._smart_term_block_reasons(
        current_duration=500.0, expected=0.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=False,
    ) == ()


def test_a_refuted_prefix_guard_drops_out_while_the_others_remain():
    """The point of collecting them: one guard opening is visible even though
    another still holds, without waiting for another cycle to find out."""
    common = dict(
        current_duration=100.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=True,
    )
    assert CycleDetector._smart_term_block_reasons(**common) == (
        "duration_not_reached",
        "prefix_ambiguous",
    )
    assert CycleDetector._smart_term_block_reasons(**common, prefix_refuted=True) == (
        "duration_not_reached",
    )


def test_the_single_valued_view_still_returns_none_when_nothing_blocks():
    assert CycleDetector._smart_term_block_reason(
        current_duration=1000.0, expected=1000.0, smart_ratio=0.98,
        is_confident=True, ambiguous=False, prefix_ambiguous=False,
    ) is None
