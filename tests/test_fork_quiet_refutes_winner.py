"""Fork 0.5.8.3: the quiet evidence rules out the leading live candidate too.

0.5.8.2 only dropped a longer RUNNER-UP from the #288 prefix term. When the
longer programme itself leads the live match, its own quiet history was never
checked. Numbers from the Tiny washer: an AutomatikPlus run (activity over at
minute 77) led as 'Lang' (182 min, activity never over before minute 112, longest
resumed mid-cycle quiet below 50 W 2.1 min) through its tumble tail.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.ha_washdata.const import (
    SMART_TERM_PREFIX_QUIET_MARGIN,
    SMART_TERM_QUIET_REFUTE_END_RATIO,
    SMART_TERM_QUIET_REFUTE_MIN_S,
)
from custom_components.ha_washdata.profile_store import ProfileStore, _quiet_refutes

LANG = {"name": "Lang", "profile_duration": 10932.0, "score": 0.60}
LANG_QUIET = 126.0  # 2.1 min
LANG_END = 6738.0  # 112.3 min
QUIET = 600.0  # 10 min
ELAPSED = 4620.0 + QUIET  # activity stopped at minute 77


def _quiet(name: str) -> float | None:
    return {"Lang": LANG_QUIET}.get(name)


def _end(name: str) -> float | None:
    return {"Lang": LANG_END}.get(name)


def test_the_tail_of_a_shorter_programme_rules_the_leader_out() -> None:
    assert _quiet_refutes(LANG, ELAPSED, QUIET, _quiet, _end) is True


def test_no_quiet_or_a_quiet_below_the_floor_keeps_it() -> None:
    assert _quiet_refutes(LANG, ELAPSED, None, _quiet, _end) is False
    assert _quiet_refutes(LANG, ELAPSED, 0.0, _quiet, _end) is False
    short = SMART_TERM_QUIET_REFUTE_MIN_S - 1.0
    assert _quiet_refutes(LANG, 4620.0 + short, short, _quiet, _end) is False


def test_a_quiet_the_programme_also_shows_mid_cycle_keeps_it() -> None:
    long_quiet = lambda _n: 900.0  # noqa: E731 - 15 min mid-cycle once
    inside = 900.0 * SMART_TERM_PREFIX_QUIET_MARGIN
    assert _quiet_refutes(LANG, 4620.0 + inside, inside, long_quiet, _end) is False
    assert _quiet_refutes(LANG, 4621.0 + inside, inside + 1.0, long_quiet, _end) is True


def test_activity_stopping_where_the_programme_can_stop_keeps_it() -> None:
    at = LANG_END * SMART_TERM_QUIET_REFUTE_END_RATIO
    assert _quiet_refutes(LANG, at + QUIET, QUIET, _quiet, _end) is False
    assert _quiet_refutes(LANG, at - 1.0 + QUIET, QUIET, _quiet, _end) is True


def test_no_evidence_or_a_failing_statistic_keeps_it() -> None:
    none = lambda _n: None  # noqa: E731
    assert _quiet_refutes(LANG, ELAPSED, QUIET, none, _end) is False
    assert _quiet_refutes(LANG, ELAPSED, QUIET, _quiet, none) is False

    def boom(_name: str) -> float | None:
        raise RuntimeError("no opinion")

    assert _quiet_refutes(LANG, ELAPSED, QUIET, boom, _end) is False
    assert _quiet_refutes(LANG, ELAPSED, QUIET, _quiet, boom) is False


def _fake_store(cycles: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        iter_evidence_cycles=lambda: iter(cycles),
        _terminal_quiet_fingerprint=lambda _name: ("fp", len(cycles)),
    )


def test_earliest_activity_end_ignores_the_tumble_tail() -> None:
    # wash until 4620 s, then tumble pulses (25 W) to 5500 s; a second run ends at 6000 s.
    a = [[0.0, 2.0], [60.0, 2000.0], [4620.0, 360.0], [4650.0, 25.0], [5500.0, 1.4]]
    b = [[0.0, 300.0], [6000.0, 400.0], [6100.0, 1.0]]
    store = _fake_store([
        {"profile_name": "AutomatikPlus", "power_data": a},
        {"profile_name": "AutomatikPlus", "power_data": b},
        {"profile_name": "Kurz", "power_data": [[0.0, 300.0], [1200.0, 300.0]]},
    ])
    assert ProfileStore.profile_earliest_activity_end(store, "AutomatikPlus", 50.0) == 4620.0


def test_earliest_activity_end_without_evidence_is_none() -> None:
    store = _fake_store([{"profile_name": "Kurz", "power_data": [[0.0, 300.0], [10.0, 5.0]]}])
    assert ProfileStore.profile_earliest_activity_end(store, "AutomatikPlus", 50.0) is None
    assert ProfileStore.profile_earliest_activity_end(store, "Kurz", 0.0) is None
