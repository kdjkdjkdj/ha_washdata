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

"""Fork build 0.5.4.2: the longer-look-alike guard is an actual prefix test.

The guard blocks Smart Termination while a much longer profile in the pool still
scores above SMART_TERM_LANDSCAPE_MIN_SHAPE, on the theory that the running trace
might be that program's opening stretch. It compared against the candidate's
*whole*, time-normalised curve - but DTW is scale free in time, so a short cycle
clears the bar against a much longer program whose overall silhouette is similar
even when their opening minutes look nothing alike. That is not the question the
guard is asking.

`compute_matches_worker` now also scores the candidate truncated to the elapsed
duration (`prefix_score`), on the same Stage-2 + Stage-3 scale. The guard prefers
it and falls back to the whole-curve score whenever it is absent - candidates
outside the DTW-refined top-N, DTW disabled, or the comparison switched back via
`prefix_guard_prefix_score` - so it is never *less* cautious than upstream.

Field evidence, all measured under upstream v0.5.4 on 18.08.2026:
  * Tiny washing machine, `Kurz` at 3926 s: duration and confidence gates open,
    `prefix_ambiguous` the sole blocker (AutomatikPlus, 2.13x longer)
  * KD washing machine, `Mix`: Baumwolle 60 at 1.94x, shape 0.402 against a 0.400 bar
  * KD dishwasher, `Kurz` at 2120 s: blocked the moment the duration gate opened
"""

from __future__ import annotations

from custom_components.ha_washdata.analysis import compute_matches_worker
from custom_components.ha_washdata.const import (
    SMART_TERM_LANDSCAPE_MIN_SHAPE,
    SMART_TERM_LANDSCAPE_RATIO,
)


def _cand(name: str, profile_duration: float, score: float, shape_score: float) -> dict:
    return {
        "name": name,
        "profile_duration": profile_duration,
        "score": score,
        "shape_score": shape_score,
    }


def _is_prefix_ambiguous(candidates, best_dur: float, use_prefix: bool = True) -> bool:
    """Mirror of ProfileStore's guard, including the comparison switch."""
    if best_dur <= 0:
        return False
    for c in candidates[1:]:
        if float(c.get("profile_duration") or 0) <= best_dur * SMART_TERM_LANDSCAPE_RATIO:
            continue
        prefix = c.get("prefix_score") if use_prefix else None
        used = float(
            prefix if prefix is not None
            else c.get("shape_score", c.get("score", 0))
        )
        if used >= SMART_TERM_LANDSCAPE_MIN_SHAPE:
            return True
    return False


# --- the guard asks about the PREFIX, not the whole curve ---------------------


def test_prefix_score_overrides_high_shape_score():
    """A high whole-curve shape score no longer blocks when the prefix disagrees."""
    candidates = [
        _cand("Kurz", 2140, 0.70, 0.66),
        {**_cand("Eco", 11750, 0.70, 0.29), "prefix_score": 0.18},
    ]
    assert _is_prefix_ambiguous(candidates, 2140.0) is False


def test_prefix_score_still_blocks_a_real_mid_cycle_prefix():
    """A trace that does look like the longer program's opening stretch keeps
    Smart Termination blocked - the protection this guard exists for."""
    candidates = [
        _cand("Quick", 2760, 0.70, 0.61),
        {**_cand("Normal", 5280, 0.70, 0.44), "prefix_score": 0.72},
    ]
    assert _is_prefix_ambiguous(candidates, 2760.0) is True


def test_missing_prefix_score_falls_back_to_previous_behaviour():
    """Candidates outside the DTW-refined top-N carry no prefix score; the guard
    must stay exactly as cautious as before for them."""
    candidates = [
        _cand("Quick", 2760, 0.70, 0.61),
        _cand("Normal", 5280, 0.70, 0.44),
    ]
    assert _is_prefix_ambiguous(candidates, 2760.0) is True


def test_switch_off_restores_whole_curve_behaviour():
    """With the option off, a present prefix score is ignored - the same
    candidate that the prefix test clears keeps blocking, as upstream does."""
    candidates = [
        _cand("Kurz", 2140, 0.70, 0.66),
        {**_cand("Eco", 11750, 0.70, 0.44), "prefix_score": 0.18},
    ]
    assert _is_prefix_ambiguous(candidates, 2140.0, use_prefix=True) is False
    assert _is_prefix_ambiguous(candidates, 2140.0, use_prefix=False) is True


# --- worker: where the number comes from --------------------------------------


def _heat_curve(n, blocks, base=40.0, peak=2000.0):
    """n-point curve at `base` watts with `blocks` = [(start, end), ...] at peak."""
    curve = [base] * n
    for start, end in blocks:
        for i in range(start, end):
            curve[i] = peak
    return curve


def test_worker_prefix_score_scores_the_truncated_curve():
    """The prefix score must come from the candidate's OPENING stretch, not its
    whole curve. Built so the two answers must differ: the trace holds a single
    heating block, the candidate opens with one and then adds two more later."""
    current = _heat_curve(200, [(60, 120)])
    longer = _heat_curve(200, [(20, 40), (90, 120), (150, 180)])

    snapshots = [
        {"name": "Short", "avg_duration": 2140.0, "sample_power": current},
        {"name": "Long", "avg_duration": 6420.0, "sample_power": longer},
    ]
    cands = compute_matches_worker(current, 2160.0, snapshots, {})
    by_name = {c["name"]: c for c in cands}

    assert "Long" in by_name, "the longer candidate must survive the duration filter"
    long_c = by_name["Long"]
    assert "prefix_score" in long_c, "a longer candidate must carry a prefix score"
    assert long_c["prefix_score"] != long_c["shape_score"], (
        "prefix and whole-curve score must not be the same number - if they are, "
        "the truncation had no effect and the guard learned nothing"
    )
    assert long_c["prefix_score"] > long_c["shape_score"], (
        f"opening-only score {long_c['prefix_score']:.3f} should beat whole-curve "
        f"{long_c['shape_score']:.3f} for a trace that matches only the opening"
    )


def test_worker_sets_no_prefix_score_for_a_candidate_not_longer():
    """No truncation is possible once the cycle has outlasted the candidate, so
    no prefix score is attached and the whole-curve path decides."""
    current = _heat_curve(200, [(30, 60), (95, 125), (155, 190)])
    snapshots = [{"name": "Kurz", "avg_duration": 2140.0, "sample_power": current}]
    cands = compute_matches_worker(current, 2160.0, snapshots, {})

    assert cands, "the matching candidate must survive"
    assert "prefix_score" not in cands[0]


def test_worker_skips_the_prefix_score_when_switched_off():
    """The extra alignment + DTW pass is not paid for when the option is off."""
    current = _heat_curve(200, [(60, 120)])
    longer = _heat_curve(200, [(20, 40), (90, 120), (150, 180)])
    snapshots = [
        {"name": "Short", "avg_duration": 2140.0, "sample_power": current},
        {"name": "Long", "avg_duration": 6420.0, "sample_power": longer},
    ]

    on = {c["name"]: c for c in compute_matches_worker(current, 2160.0, snapshots, {})}
    off = {
        c["name"]: c
        for c in compute_matches_worker(
            current, 2160.0, snapshots, {"prefix_guard_prefix_score": False}
        )
    }

    assert "prefix_score" in on["Long"]
    assert "prefix_score" not in off["Long"]
    # Everything else stays identical - the flag only adds a number, it never
    # changes the ranking itself.
    assert on["Long"]["score"] == off["Long"]["score"]
    assert on["Short"]["score"] == off["Short"]["score"]
