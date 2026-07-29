#!/usr/bin/env python3
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
"""verify_alignment.py - does the envelope alignment behave, on real data?

Runs the integration's *own* ``ProfileStore.async_verify_alignment`` against the
envelopes and recorded cycles in a diagnostic export - no Home Assistant
instance, no live appliance.  Nothing is re-implemented: the DTW, the envelope
and the traces are the real ones, so a result here is a statement about the
shipped code, not about a model of it.

``mapped_time``, the value that decides whether a verified pause is released, is
a *position on the envelope's time grid*.  Two properties have to hold for the
release to work, and each is checked per profile:

  1. REACHABLE - the release threshold must lie inside the envelope.  The worker
     clamps the mapped index to the last grid slot, so ``mapped_time`` can never
     exceed the envelope's ``target_duration``.  If the threshold is derived
     from a longer number, it sits past the end of the grid and no amount of
     waiting will reach it.

  2. TRACKS TIME - a trace covering x % of the envelope must map to roughly
     x % along it.  Verified by truncating a real recorded cycle at several
     fractions of its length and comparing where each lands.  Uses only data
     that actually happened.

  3. ADVANCES - during a standby tail the meter goes quiet and the only readings
     are the watchdog's keepalives.  The mapped position must keep pace with the
     wall clock across those, otherwise the release is not reached before the
     max-deferral cap force-ends the cycle.  Checked by appending keepalives at
     the cadence the entry's own settings produce.

Usage:
    python3 devtools/verify_alignment.py <path/to/diagnostics.json>
    python3 devtools/verify_alignment.py            # interactive file prompt

Run from the repository root with the venv activated:
    source .venv/bin/activate
    python3 devtools/verify_alignment.py export.json

Exit code 0 if every check passed, 1 if any failed, 2 if nothing could be
checked (no envelope, or no cycle carrying a power trace).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

try:
    from custom_components.ha_washdata.const import (
        CONF_MIN_POWER,
        CONF_OFF_DELAY,
        CONF_STOP_THRESHOLD_W,
        CONF_WATCHDOG_INTERVAL,
        DEFAULT_MAX_DEFERRAL_SECONDS,
        DEFAULT_MIN_POWER,
        DEFAULT_OFF_DELAY,
        DEFAULT_WATCHDOG_INTERVAL,
    )
    from custom_components.ha_washdata.profile_store import (
        ProfileStore,
        decompress_power_data,
    )
except ModuleNotFoundError as exc:
    print(
        f"\n[ERROR] Could not import required modules: {exc}\n"
        "Make sure you run this script from the repository root with the venv activated:\n"
        "  source .venv/bin/activate\n"
        "  python3 devtools/verify_alignment.py <export.json>\n",
        file=sys.stderr,
    )
    sys.exit(1)

# The release condition in manager.py: verified_pause is lifted once the mapped
# position passes this fraction of the reference duration.
RELEASE_RATIO = 0.95

# Check 2: mean absolute deviation between "where the trace maps" and "how far
# along it actually is", as a fraction of the envelope span.
TRACK_TOLERANCE = 0.15
TRACK_FRACTIONS = (0.3, 0.5, 0.7, 0.8, 0.9, 1.0)
TRACK_MIN_POINTS = 3

# Check 3: mapped seconds gained per wall-clock second across a synthetic tail.
# 1.0 is perfect; the failure this catches is an order of magnitude below that.
ADVANCE_MIN = 0.5
ADVANCE_TAIL_MINUTES = 60

_USE_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(t: str) -> str: return _c(t, "32")
def red(t: str) -> str: return _c(t, "31")
def yellow(t: str) -> str: return _c(t, "33")
def bold(t: str) -> str: return _c(t, "1")
def dim(t: str) -> str: return _c(t, "2")


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _verdict(status: str) -> str:
    if status == PASS:
        return green("PASS")
    if status == FAIL:
        return red("FAIL")
    return yellow("SKIP")


# ---------------------------------------------------------------------------
# Export handling - same tolerance as analyze_diag.py
# ---------------------------------------------------------------------------

def extract_store(data: dict[str, Any]) -> dict[str, Any]:
    """Return the store data dict regardless of export format."""
    store_export = data.get("data", {}).get("store_export", {})
    if isinstance(store_export, dict) and "data" in store_export:
        return store_export["data"]
    return data.get("data", {}).get("store_data", {})


def extract_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Merge entry.data + entry.options into a flat settings dict."""
    entry = data.get("data", {}).get("entry", {})
    merged: dict[str, Any] = {}
    entry_present = isinstance(entry, dict) and (entry.get("data") or entry.get("options"))
    if isinstance(entry, dict):
        merged.update(entry.get("data", {}) or {})
        merged.update(entry.get("options", {}) or {})
    if not entry_present:
        store_export = data.get("data", {}).get("store_export", {})
        if isinstance(store_export, dict):
            merged.update(store_export.get("entry_data", {}) or {})
            merged.update(store_export.get("entry_options", {}) or {})
    return merged


def build_store(store_data: dict[str, Any]) -> ProfileStore:
    """A ProfileStore holding the exported data, with persistence patched out."""
    hass = MagicMock()

    async def run_now(func, *args, **kwargs):
        return func(*args, **kwargs)

    hass.async_add_executor_job = AsyncMock(side_effect=run_now)
    with patch("custom_components.ha_washdata.profile_store.WashDataStore"):
        store = ProfileStore(hass, "verify_alignment")
        store._store.async_load = AsyncMock(return_value=None)
        store._store.async_save = AsyncMock()
    store._data = store_data
    return store


def keepalive_cadence(settings: dict[str, Any]) -> tuple[float, str]:
    """Interval at which the watchdog injects 0 W keepalives during a low-power tail.

    The injection fires once ``off_delay`` of silence has passed, but is only
    evaluated on watchdog ticks - so the effective cadence is ``off_delay``
    rounded up to the next whole tick.
    """
    off_delay = float(settings.get(CONF_OFF_DELAY) or DEFAULT_OFF_DELAY)
    watchdog = float(settings.get(CONF_WATCHDOG_INTERVAL) or DEFAULT_WATCHDOG_INTERVAL)
    if watchdog <= 0:
        return off_delay, f"off_delay {off_delay:.0f}s"
    cadence = math.ceil(off_delay / watchdog) * watchdog
    return cadence, f"off_delay {off_delay:.0f}s / watchdog {watchdog:.0f}s"


def stop_threshold(settings: dict[str, Any]) -> float:
    """The power below which the detector considers the appliance idle.

    Mirrors the manager: an explicit ``stop_threshold_w`` wins, otherwise it is
    derived as 60 % of ``min_power``.
    """
    explicit = settings.get(CONF_STOP_THRESHOLD_W)
    if explicit is not None and float(explicit) > 0:
        return float(explicit)
    min_power = float(settings.get(CONF_MIN_POWER) or DEFAULT_MIN_POWER)
    return min_power * 0.6 if min_power > 0 else 2.0


def cut_at_tail(trace: list[list[float]], threshold: float) -> list[list[float]] | None:
    """Trace up to the last reading above ``threshold`` - where the tail begins.

    The advance check has to start where the appliance falls quiet: from there on
    the meter stops reporting and only watchdog keepalives arrive.  Feeding the
    *whole* recorded cycle instead would already sit at the end of the envelope
    and the check would measure nothing.
    """
    last_active = None
    for i, (_, power) in enumerate(trace):
        if power > threshold:
            last_active = i
    if last_active is None or last_active < 10:
        return None
    return trace[: last_active + 1]


def best_trace(cycles: list[dict[str, Any]], profile: str) -> list[list[float]] | None:
    """Longest recorded trace labelled with this profile, as [[offset, watt], ...]."""
    best: list[list[float]] | None = None
    for cycle in cycles:
        if cycle.get("profile_name") != profile:
            continue
        pairs = decompress_power_data(cycle)
        if len(pairs) < 10:
            continue
        trace = [[float(t), float(p)] for t, p in pairs]
        if best is None or trace[-1][0] > best[-1][0]:
            best = trace
    return best


# ---------------------------------------------------------------------------
# The three checks
# ---------------------------------------------------------------------------

def check_reachable(span: float, avg_duration: float) -> tuple[str, str]:
    if span <= 0 or avg_duration <= 0:
        return SKIP, "no envelope span or no average duration"
    threshold = RELEASE_RATIO * avg_duration
    ceiling = span / avg_duration
    detail = (
        f"threshold {threshold:.0f}s vs envelope span {span:.0f}s "
        f"(ceiling {ceiling:.4f})"
    )
    if threshold > span:
        return FAIL, detail + " - threshold lies past the end of the grid"
    return PASS, detail


async def check_tracks_time(
    store: ProfileStore, profile: str, span: float, trace: list[list[float]]
) -> tuple[str, str]:
    total = trace[-1][0] - trace[0][0]
    if total <= 0:
        return SKIP, "trace has no elapsed time"

    deviations: list[float] = []
    samples: list[str] = []
    skipped = 0
    for frac in TRACK_FRACTIONS:
        cut = trace[0][0] + total * frac
        part = [p for p in trace if p[0] <= cut]
        if len(part) < 3:
            continue
        _, mapped, power = await store.async_verify_alignment(profile, part)
        if mapped <= 0.0 and power >= 9999.0:
            skipped += 1  # worker could not align this slice at all
            continue
        got = mapped / span
        expected = min(1.0, (cut - trace[0][0]) / span)
        deviations.append(abs(got - expected))
        samples.append(f"{frac:.0%}:{got:.2f}/{expected:.2f}")

    if len(deviations) < TRACK_MIN_POINTS:
        return SKIP, f"only {len(deviations)} slice(s) could be aligned"

    mad = sum(deviations) / len(deviations)
    detail = f"mean deviation {mad:.3f} over {len(deviations)} slices  " + dim(
        "[" + " ".join(samples) + "]"
    )
    if skipped:
        detail += dim(f"  ({skipped} slice(s) unalignable)")
    return (PASS if mad <= TRACK_TOLERANCE else FAIL), detail


async def check_advances(
    store: ProfileStore, profile: str, span: float, trace: list[list[float]],
    cadence: float, cadence_src: str, threshold: float,
) -> tuple[str, str]:
    if cadence <= 0:
        return SKIP, "no usable keepalive cadence"

    head = cut_at_tail(trace, threshold)
    if head is None:
        return SKIP, f"no active section above the stop threshold ({threshold:.1f} W)"
    trace = head

    _, before, _ = await store.async_verify_alignment(profile, trace)
    tail_s = ADVANCE_TAIL_MINUTES * 60.0
    steps = int(tail_s / cadence)
    if steps < 1:
        return SKIP, f"keepalive cadence {cadence:.0f}s longer than the probe window"

    # Nothing left to advance towards: the active part of the cycle already
    # covers the envelope, so the release condition is met before the tail even
    # begins.  That is the healthy outcome, not a stalled measurement.
    if before >= span - 1e-6:
        return PASS, (
            f"active part already maps to the end of the envelope ({before:.0f}s) "
            "- release condition met before the tail begins"
        )

    t0 = trace[-1][0]
    extended = trace + [[t0 + cadence * i, 0.0] for i in range(1, steps + 1)]
    _, after, _ = await store.async_verify_alignment(profile, extended)

    wall = cadence * steps
    factor = (after - before) / wall if wall > 0 else 0.0
    detail = (
        f"{after - before:.0f}s mapped per {wall / 60:.0f} min of keepalives "
        f"= {factor:.3f}x wall clock  " + dim(f"[cadence {cadence:.0f}s from {cadence_src}]")
    )

    if factor >= ADVANCE_MIN:
        return PASS, detail

    # Quantify the consequence: how long until the threshold, against the cap.
    remaining = max(0.0, RELEASE_RATIO * span - after)
    if factor > 0:
        hours = remaining / factor / 3600.0
        cap_h = (DEFAULT_MAX_DEFERRAL_SECONDS + 1800) / 3600.0
        detail += red(f"\n{'':38}-> release in ~{hours:.1f} h, deferral cap at {cap_h:.1f} h")
    else:
        detail += red(f"\n{'':38}-> position does not advance at all")
    return FAIL, detail


# ---------------------------------------------------------------------------

async def run(export_path: str) -> int:
    with open(export_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    store_data = extract_store(raw)
    settings = extract_settings(raw)
    envelopes = store_data.get("envelopes") or {}
    profiles = store_data.get("profiles") or {}
    cycles = store_data.get("past_cycles") or []

    if not envelopes:
        print(red("\nNo envelopes in this export - nothing to verify.\n"))
        return 2

    store = build_store(store_data)
    cadence, cadence_src = keepalive_cadence(settings)
    threshold = stop_threshold(settings)
    device = settings.get("name") or ""
    if not device or "REDACTED" in str(device):
        device = settings.get("device_type") or "device"

    print(f"\n{bold('Envelope alignment verification')}  {dim('·')}  {device}")
    print(dim(f"  {os.path.basename(export_path)}  ·  {len(envelopes)} envelope(s)  ·  "
              f"{len(cycles)} recorded cycle(s)"))

    failures = 0
    checked = 0

    for name in sorted(envelopes):
        env = envelopes[name] or {}
        span = float(env.get("target_duration") or 0.0)
        grid_points = len(env.get("time_grid") or env.get("avg") or [])
        avg_duration = float((profiles.get(name) or {}).get("avg_duration") or 0.0)
        step = span / (grid_points - 1) if grid_points > 1 else 0.0

        print(f"\n{bold(name)}")
        print(dim(f"  envelope {grid_points} slots x {step:.1f}s, span {span:.0f}s"
                  f"  ·  avg_duration {avg_duration:.0f}s"))

        results: list[tuple[str, str, str]] = []

        status, detail = check_reachable(span, avg_duration)
        results.append(("reachable", status, detail))

        trace = best_trace(cycles, name)
        if trace is None:
            results.append(("tracks time", SKIP, "no recorded cycle with a power trace"))
            results.append(("advances", SKIP, "no recorded cycle with a power trace"))
        elif span <= 0:
            results.append(("tracks time", SKIP, "no envelope span"))
            results.append(("advances", SKIP, "no envelope span"))
        else:
            results.append(("tracks time", *await check_tracks_time(store, name, span, trace)))
            results.append(("advances", *await check_advances(
                store, name, span, trace, cadence, cadence_src, threshold)))

        for label, status, detail in results:
            print(f"  {_verdict(status)}  {label:<12} {detail}")
            if status == FAIL:
                failures += 1
            if status != SKIP:
                checked += 1

    print()
    if checked == 0:
        print(yellow("Nothing could be checked - no envelope had a usable trace.\n"))
        return 2
    if failures:
        print(red(f"{failures} check(s) failed."), "\n")
        return 1
    print(green("All checks passed."), "\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify the envelope alignment against a WashData diagnostic export.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("export", nargs="?", metavar="EXPORT_JSON",
                        help="Path to the diagnostic export JSON file.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colour output.")
    args = parser.parse_args()

    global _USE_COLOR
    if args.no_color:
        _USE_COLOR = False

    export_path = args.export
    if not export_path:
        print("Enter the path to the diagnostic export JSON file:")
        export_path = input("  > ").strip().strip("'\"")

    if not export_path or not os.path.isfile(export_path):
        print(f"[ERROR] File not found: {export_path!r}", file=sys.stderr)
        sys.exit(1)

    sys.exit(asyncio.run(run(export_path)))


if __name__ == "__main__":
    main()
