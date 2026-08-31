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
"""Power refutation for the Smart-Termination prefix guard.

The #364 guard blocks on "this trace could be the beginning of a longer
programme", and once it fires it has no way back. On an appliance with one short
and one long profile that holds EVERY short run to the fallback timeout - measured
1.7-3.7 min per run on two machines at one site.

But the claim the guard makes is refutable. A blocking candidate asserts we are
mid-run inside IT, and its own envelope says what it draws at that offset. When
the live trailing mean sits a multiple below the QUIETEST level that candidate
has ever shown there, it is not the programme running, and the guard may open.

The min curve is the load-bearing choice, and it is not interchangeable with the
average: on a washer with real soak pauses, 6 of 20 genuine long runs sit under
half their own profile average at that offset (down to 0.05x) - a mean-based test
would have cut them in half. Every such pause is already inside the min curve.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import Mock

from custom_components.ha_washdata.const import SMART_TERM_PREFIX_REFUTE_FACTOR
from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
)
from custom_components.ha_washdata.profile_store import (
    ProfileStore,
    _match_prefix_ambiguity,
)

BASE = datetime(2026, 8, 31, 8, 0, 0)


def _detector(expected: float = 4961.0) -> CycleDetector:
    det = CycleDetector(CycleDetectorConfig(min_power=5.0, off_delay=120), Mock(), Mock())
    det._expected_duration = expected
    return det


def _feed(det: CycleDetector, watts: float, *, points: int = 12, step: int = 20) -> datetime:
    """A flat trailing window at ``watts``, ending at the returned timestamp."""
    det._power_readings = [
        (BASE + timedelta(seconds=i * step), watts) for i in range(points)
    ]
    det._p95_dt = float(step)
    return det._power_readings[-1][0]


def _store(envelope: dict | None) -> ProfileStore:
    """A stand-in exposing only what ``profile_prefix_floor`` reads."""
    fake = Mock(spec=["get_envelope"])
    fake.get_envelope.return_value = envelope
    return fake


# ── the blocker's identity travels with the verdict ────────────────────────────

def test_prefix_term_names_the_candidate_that_set_the_verdict():
    """Refuting a candidate requires knowing WHICH one blocked."""
    cands = [
        {"name": "Kurz", "profile_duration": 2140.0, "score": 0.78, "shape_score": 0.60},
        {
            "name": "Eco",
            "profile_duration": 11900.0,
            "score": 0.38,
            "shape_score": 0.30,
            "prefix_score": 0.90,
        },
    ]
    full_shape_hit, prefix_fit_hit, blocker = _match_prefix_ambiguity(cands, 2140.0)
    assert (full_shape_hit, prefix_fit_hit) == (False, True)
    assert blocker == "Eco"


def test_the_prefix_term_wins_the_naming_race_over_the_legacy_term():
    """Both terms can fire on different candidates; the prefix one is the specific
    verdict, and the only one whose floor is meaningful at this offset."""
    cands = [
        {"name": "Kurz", "profile_duration": 2000.0, "score": 0.70, "shape_score": 0.50},
        # legacy #288 hit: much longer, good FULL-envelope shape, no prefix score
        {"name": "Lang-A", "profile_duration": 9000.0, "score": 0.60, "shape_score": 0.60},
        # prefix hit: the candidate we can actually refute
        {
            "name": "Lang-B",
            "profile_duration": 5000.0,
            "score": 0.40,
            "shape_score": 0.35,
            "prefix_score": 0.80,
        },
    ]
    full_shape_hit, prefix_fit_hit, blocker = _match_prefix_ambiguity(cands, 2000.0)
    assert (full_shape_hit, prefix_fit_hit) == (True, True)
    assert blocker == "Lang-B"


def test_no_verdict_means_no_blocker_name():
    cands = [
        {"name": "Kurz", "profile_duration": 2000.0, "score": 0.80, "shape_score": 0.75},
        {"name": "Auch kurz", "profile_duration": 2050.0, "score": 0.40, "shape_score": 0.30},
    ]
    assert _match_prefix_ambiguity(cands, 2000.0) == (False, False, None)
    assert _match_prefix_ambiguity([], 2000.0) == (False, False, None)


# ── the floor the candidate itself defines ─────────────────────────────────────

def test_floor_is_the_minimum_over_the_window_not_the_value_at_the_point():
    """Phase alignment between run and template is not exact, so both sides are
    windowed - and the window takes the LOWEST admissible level, never an average."""
    env = {"min": [[0.0, 50.0], [100.0, 40.0], [200.0, 12.0], [300.0, 45.0]]}
    floor = ProfileStore.profile_prefix_floor(_store(env), "Eco", 150.0, window_s=60.0)
    assert floor == 12.0


def test_floor_reads_the_legacy_flat_min_format_via_time_grid():
    env = {"min": [50.0, 40.0, 12.0, 45.0], "time_grid": [0.0, 100.0, 200.0, 300.0]}
    assert ProfileStore.profile_prefix_floor(_store(env), "Eco", 200.0, window_s=10.0) == 12.0


def test_floor_reconstructs_the_grid_from_target_duration():
    env = {"min": [50.0, 40.0, 12.0, 45.0], "target_duration": 300.0}
    assert ProfileStore.profile_prefix_floor(_store(env), "Eco", 200.0, window_s=10.0) == 12.0


def test_floor_has_no_opinion_without_a_usable_curve():
    """Every missing input is 'no opinion' - the guard then stays exactly as it was."""
    assert ProfileStore.profile_prefix_floor(_store(None), "Eco", 100.0) is None
    assert ProfileStore.profile_prefix_floor(_store({}), "Eco", 100.0) is None
    assert ProfileStore.profile_prefix_floor(_store({"min": []}), "Eco", 100.0) is None
    # no grid and no target duration: the legacy format cannot be placed in time
    assert ProfileStore.profile_prefix_floor(_store({"min": [1.0, 2.0]}), "Eco", 100.0) is None
    # window falls outside the template
    env = {"min": [[0.0, 50.0], [100.0, 40.0]]}
    assert ProfileStore.profile_prefix_floor(_store(env), "Eco", 9000.0, window_s=10.0) is None


def test_a_zero_floor_is_no_opinion_rather_than_an_always_true_refutation():
    """A candidate that itself drops to 0 W here cannot be refuted on power -
    and must not become a floor that every reading undercuts."""
    env = {"min": [[0.0, 50.0], [100.0, 0.0], [200.0, 40.0]]}
    assert ProfileStore.profile_prefix_floor(_store(env), "Eco", 100.0, window_s=10.0) is None


def test_floor_never_raises_on_a_malformed_curve():
    env = {"min": [["not-a-number", 5.0]]}
    assert ProfileStore.profile_prefix_floor(_store(env), "Eco", 100.0) is None


# ── the refutation itself ──────────────────────────────────────────────────────

def test_refuted_when_the_trace_sits_a_multiple_below_the_candidate_floor():
    """The measured KD dishwasher case: 0.65 W against an Eco floor of 27.6 W."""
    det = _detector()
    det._prefix_floor_w = 27.6
    ts = _feed(det, 0.65)
    assert det._prefix_refuted(ts) is True


def test_not_refuted_just_below_the_floor():
    """A genuine long run dipping slightly under its own minimum must NOT open the
    guard - the measured worst case sat at 27.1 W against a 27.6 W floor."""
    det = _detector()
    det._prefix_floor_w = 27.6
    ts = _feed(det, 27.1)
    assert det._prefix_refuted(ts) is False


def test_the_refutation_boundary_is_the_configured_factor():
    det = _detector()
    det._prefix_floor_w = 30.0
    just_inside = 30.0 / SMART_TERM_PREFIX_REFUTE_FACTOR * 0.99
    just_outside = 30.0 / SMART_TERM_PREFIX_REFUTE_FACTOR * 1.01
    assert det._prefix_refuted(_feed(det, just_inside)) is True
    assert det._prefix_refuted(_feed(det, just_outside)) is False


def test_refutation_fails_closed_on_every_missing_input():
    """Unlike the power-plausibility guard, absent data must keep the guard
    BLOCKING: opening on no evidence would split cycles."""
    det = _detector()
    ts = _feed(det, 0.5)
    det._prefix_floor_w = None
    assert det._prefix_refuted(ts) is False
    det._prefix_floor_w = 0.0
    assert det._prefix_refuted(ts) is False
    # too few readings to judge
    det._prefix_floor_w = 27.6
    det._power_readings = [(BASE, 0.5)]
    assert det._prefix_refuted(BASE) is False


# ── the trailing window is walked once per reading, not once per guard ─────────

def test_a_pre_computed_mean_is_used_instead_of_walking_the_window_again():
    """Both guards mean the same window at the same timestamp, so the ENDING path
    takes it once and hands it over. A guard that recomputed would undo that."""
    det = _detector()
    det._prefix_floor_w = 27.6
    det._matched_tail_power = 3.0
    det._trailing_mean_power = Mock(side_effect=AssertionError("window walked again"))

    assert det._prefix_refuted(BASE, 0.65) is True
    assert det._smart_term_power_plausible(BASE, 0.65) is True
    det._trailing_mean_power.assert_not_called()


def test_omitting_the_mean_still_takes_it_locally():
    """Every other caller - the anti-crease path, the tests, the Playground - keeps
    working unchanged."""
    det = _detector()
    det._prefix_floor_w = 27.6
    ts = _feed(det, 0.65)
    assert det._prefix_refuted(ts) is True

    det2 = _detector()
    det2._matched_tail_power = 3.0
    ts2 = _feed(det2, 0.65)
    assert det2._smart_term_power_plausible(ts2) is True


def test_a_pre_computed_none_is_honoured_and_not_mistaken_for_absence():
    """None is the mean's own 'too few samples' verdict and must not trigger a
    recompute - that is why the default is a sentinel rather than None."""
    det = _detector()
    det._prefix_floor_w = 27.6
    det._matched_tail_power = 3.0
    det._trailing_mean_power = Mock(side_effect=AssertionError("window walked again"))

    assert det._prefix_refuted(BASE, None) is False
    assert det._smart_term_power_plausible(BASE, None) is True
    det._trailing_mean_power.assert_not_called()


def test_neither_guard_armed_means_the_window_is_never_walked():
    """Each guard returns on its own reference level before touching the window,
    so the common mid-wash case must stay free of the scan."""
    det = _detector()
    det._prefix_floor_w = None
    det._matched_tail_power = None
    det._trailing_mean_power = Mock(side_effect=AssertionError("window walked"))

    assert det._smart_term_power_plausible(BASE) is True
    assert det._prefix_refuted(BASE) is False
    det._trailing_mean_power.assert_not_called()


# ── wiring: the floor reaches the detector, and clears ─────────────────────────

def test_update_match_reads_the_floor_from_element_10():
    det = _detector()
    det.update_match(("Kurz", 0.8, 2140.0, None, False, False, True, False, 3.0, 27.6))
    assert det._prefix_floor_w == 27.6


def test_a_shorter_tuple_clears_the_floor_rather_than_keeping_it():
    """Same contract as element 9: a stale floor would judge the live trace
    against a previous match's blocker."""
    det = _detector()
    det._prefix_floor_w = 27.6
    det.update_match(("Kurz", 0.8, 2140.0, None, False, False, True))
    assert det._prefix_floor_w is None


def test_reset_clears_the_floor():
    det = _detector()
    det._prefix_floor_w = 27.6
    det.reset()
    assert det._prefix_floor_w is None


def test_the_floor_survives_a_snapshot_round_trip():
    det = _detector()
    det._prefix_floor_w = 27.6
    snapshot = det.get_state_snapshot()
    restored = _detector()
    restored.restore_state_snapshot(snapshot)
    assert restored._prefix_floor_w == 27.6


# ── the gate and its diagnostic ────────────────────────────────────────────────

def test_block_reason_still_reports_prefix_ambiguous_without_a_refutation():
    assert (
        CycleDetector._smart_term_block_reason(
            5000.0, 4961.0, 0.98, True, False, True, True, False
        )
        == "prefix_ambiguous"
    )


def test_block_reason_drops_prefix_ambiguous_once_refuted():
    """With the blocker disproved the gate passes, so there is no reason to report."""
    assert (
        CycleDetector._smart_term_block_reason(
            5000.0, 4961.0, 0.98, True, False, True, True, True
        )
        is None
    )


def test_a_refutation_does_not_override_the_other_gates():
    """It releases the prefix verdict only - never confidence, ambiguity or the
    duration gate."""
    assert (
        CycleDetector._smart_term_block_reason(
            100.0, 4961.0, 0.98, True, False, True, True, True
        )
        == "duration_not_reached"
    )
    assert (
        CycleDetector._smart_term_block_reason(
            5000.0, 4961.0, 0.98, False, False, True, True, True
        )
        == "low_confidence"
    )
    assert (
        CycleDetector._smart_term_block_reason(
            5000.0, 4961.0, 0.98, True, True, True, True, True
        )
        == "match_ambiguous"
    )
    assert (
        CycleDetector._smart_term_block_reason(
            5000.0, 4961.0, 0.98, True, False, True, False, True
        )
        == "still_active"
    )


def test_the_refutation_defaults_off_for_older_callers():
    """The parameter is optional, so a caller that predates it keeps blocking."""
    assert (
        CycleDetector._smart_term_block_reason(5000.0, 4961.0, 0.98, True, False, True)
        == "prefix_ambiguous"
    )


# ── the corpus cases this was calibrated on ────────────────────────────────────

def test_the_measured_corpus_separates_cleanly_at_the_chosen_factor():
    """Both groups from the 77-cycle calibration, as the detector sees them.

    Short runs that the guard held for nothing, against genuine long runs passing
    through the same offset - including the washer soak dips that rule out
    comparing with the profile AVERAGE.
    """
    # (floor, trailing mean) - all 7 short runs the guard releases at this factor
    releases = [
        (27.6, 0.00),   # KD dishwasher 18.08.
        (27.6, 0.00),   # KD dishwasher 24.08.
        (27.6, 0.65),   # KD dishwasher 28.08.
        (2.2, 0.54),    # Tiny dishwasher 12.08.
        (2.2, 0.27),    # Tiny dishwasher 17.08.
        (2.2, 0.47),    # Tiny dishwasher 21.08.
        (2.2, 0.44),    # Tiny dishwasher 30.08.
    ]
    # The genuine long runs that come closest to the opening threshold, which is
    # where a too-eager factor would start splitting cycles. The tightest sits at
    # 2.95x, and the washer entries are the soak dips that rule out the average.
    holds = [
        (27.6, 27.12), (27.6, 27.36), (27.6, 28.70),  # KD dishwasher Eco
        (1.2, 1.77), (1.2, 1.82), (1.2, 1.94),        # Tiny washer soak dips
        (1.2, 1.98), (1.2, 2.22),
        (2.3, 84.54), (2.3, 115.98),                  # KD washer Baumwolle 60
    ]
    det = _detector()
    for floor, watts in releases:
        det._prefix_floor_w = floor
        assert det._prefix_refuted(_feed(det, watts)) is True, (floor, watts)
    for floor, watts in holds:
        det._prefix_floor_w = floor
        assert det._prefix_refuted(_feed(det, watts)) is False, (floor, watts)
