"""Fork 0.5.8.2: quiet evidence on the #288 prefix term.

A short programme that shares its start with a longer one keeps the prefix flag
set for its whole anti-crease tail, so the anti-crease finalize never engages. The
flag now drops a longer candidate once the trailing quiet below the anti-wrinkle
ceiling outlasts the longest quiet that programme ever resumed from mid-cycle.
Numbers from the Tiny washer: Kurz 29.7 min, AutomatikPlus 98 min whose longest
mid-cycle quiet below 50 W is 10.4 min.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.ha_washdata.const import (
    SMART_TERM_PREFIX_MIN_PAUSE_S,
    SMART_TERM_PREFIX_QUIET_MARGIN,
)
from custom_components.ha_washdata.profile_store import (
    ProfileStore,
    _match_prefix_ambiguity,
)

WIN = {"name": "Kurz", "profile_duration": 1782.0, "shape_score": 0.75, "score": 0.74}
LONGER = {"name": "AutomatikPlus", "profile_duration": 5880.0, "shape_score": 0.70, "score": 0.45}
LONGEST_QUIET = 624.0  # 10.4 min


def _quiet(name: str) -> float | None:
    return {"AutomatikPlus": LONGEST_QUIET}.get(name)


def test_without_quiet_input_the_guard_is_unchanged() -> None:
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0) is True
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, None, _quiet) is True


def test_a_quiet_the_longer_programme_also_shows_mid_cycle_keeps_the_guard() -> None:
    inside = LONGEST_QUIET * SMART_TERM_PREFIX_QUIET_MARGIN - 1.0
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, inside, _quiet) is True


def test_a_quiet_beyond_the_longer_programmes_longest_drops_it() -> None:
    beyond = LONGEST_QUIET * SMART_TERM_PREFIX_QUIET_MARGIN + 1.0
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, beyond, _quiet) is False


def test_no_traced_evidence_keeps_the_guard() -> None:
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, 5000.0, lambda _n: None) is True


def test_a_failing_statistic_keeps_the_guard() -> None:
    def boom(_name: str) -> float | None:
        raise RuntimeError("no opinion")

    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, 5000.0, boom) is True


def test_a_programme_never_quiet_mid_cycle_still_needs_the_minimum_pause() -> None:
    never = lambda _n: 0.0  # noqa: E731
    short = SMART_TERM_PREFIX_MIN_PAUSE_S - 1.0
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, short, never) is True
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, None, short + 2.0, never) is False


def test_pause_evidence_still_applies_first() -> None:
    # #424: a longer programme that never pauses below stop_threshold is dropped
    # regardless of the quiet evidence.
    assert _match_prefix_ambiguity([WIN, LONGER], 1782.0, lambda _n: False, None, None) is False


def _fake_store(cycles: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        iter_evidence_cycles=lambda: iter(cycles),
        _terminal_quiet_fingerprint=lambda _name: ("fp", len(cycles)),
    )


def test_longest_resumed_quiet_ignores_the_tail_and_the_pre_roll() -> None:
    # pre-roll standby 0-60 s, wash, a 300 s quiet mid-cycle, wash, then a long
    # open tail that never resumes - only the 300 s counts.
    trace = [
        [0.0, 2.0], [60.0, 2.0], [61.0, 200.0], [600.0, 150.0],
        [601.0, 10.0], [901.0, 180.0], [1500.0, 400.0], [1501.0, 20.0], [4000.0, 25.0],
    ]
    store = _fake_store([{"profile_name": "AutomatikPlus", "power_data": trace}])
    got = ProfileStore.profile_longest_resumed_quiet(store, "AutomatikPlus", 50.0)
    assert got == 300.0


def test_longest_resumed_quiet_without_evidence_is_none() -> None:
    store = _fake_store([{"profile_name": "Kurz", "power_data": [[0.0, 300.0], [10.0, 5.0]]}])
    assert ProfileStore.profile_longest_resumed_quiet(store, "AutomatikPlus", 50.0) is None
    assert ProfileStore.profile_longest_resumed_quiet(store, "Kurz", 0.0) is None
