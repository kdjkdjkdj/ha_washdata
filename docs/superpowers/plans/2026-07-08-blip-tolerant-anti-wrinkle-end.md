# Blip-Tolerant Graceful Timeout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an unmatched cycle whose device has anti-wrinkle enabled finish gracefully via the existing timeout→`STATE_ANTI_WRINKLE` path — instead of the watchdog `force_stop` — by not letting periodic anti-crease power blips reset the end timer.

**Architecture:** Augment (not replace) the `STATE_ENDING` logic in `cycle_detector.py`. A new classifier `_is_anti_crease_blip(power)` decides whether an `is_high` reading in the ENDING tail of an unmatched, anti-wrinkle-enabled cycle is a crease-guard blip; if so, the `time_below_threshold` reset (Z.608) is skipped and the reading is booked as idle. A device-tuned `unmatched_off_delay` makes the graceful timeout fire promptly. Two new config parameters, wired through the existing options→`CycleDetectorConfig` path.

**Tech Stack:** Python 3.x, Home Assistant custom component, pytest. No new dependencies.

## Global Constraints

- Only change behavior when ALL hold: `anti_wrinkle_enabled=True`, `_expected_duration <= 0` (unmatched), `_state == STATE_ENDING` with `_time_in_state >= 120.0`, `power < crease_resume_threshold`, and `_cycle_max_power` above a real-program floor. Matched cycles and anti-wrinkle-disabled devices are byte-for-byte unchanged.
- No new detector state; reuse `_finish_cycle(termination_reason="timeout")` → `STATE_ANTI_WRINKLE` (cycle_detector.py ~Z.1441–1448).
- No changes to existing default thresholds.
- Keep the two new params consistent everywhere: `crease_resume_threshold: float`, `unmatched_off_delay: int`.
- Windows test runs need the git-excluded root `conftest.py` (pytest-socket vs win32 asyncio). Test command: `.venv/Scripts/python -m pytest tests/ -q --ignore=tests/test_verify_alignment.py --ignore=tests/test_mock_socket_synthesis.py --ignore=tests/repro`.
- Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `custom_components/ha_washdata/const.py` — new CONF keys + defaults (incl. per-device dicts).
- `custom_components/ha_washdata/cycle_detector.py` — new `CycleDetectorConfig` fields; `_is_anti_crease_blip`; guarded reset; `unmatched_off_delay` in the timeout gate.
- `custom_components/ha_washdata/manager.py` — read the two options into `CycleDetectorConfig`.
- `custom_components/ha_washdata/config_flow.py` — expose the two options in the advanced-settings schema.
- `tests/test_blip_tolerant_end.py` — new test module for this feature.

---

### Task 1: Config parameters (const + dataclass + manager wiring)

**Files:**
- Modify: `custom_components/ha_washdata/const.py`
- Modify: `custom_components/ha_washdata/cycle_detector.py:85-96` (dataclass fields)
- Modify: `custom_components/ha_washdata/manager.py:489-558` (config construction)
- Test: `tests/test_blip_tolerant_end.py`

**Interfaces:**
- Produces: `CycleDetectorConfig` gains `crease_resume_threshold: float = 400.0` and `unmatched_off_delay: int = 1800`. const exports `CONF_CREASE_RESUME_THRESHOLD`, `CONF_UNMATCHED_OFF_DELAY`, `DEFAULT_CREASE_RESUME_THRESHOLD`, `DEFAULT_CREASE_RESUME_THRESHOLD_BY_DEVICE`, `DEFAULT_UNMATCHED_OFF_DELAY`, `DEFAULT_UNMATCHED_OFF_DELAY_BY_DEVICE`, `WM_SPIN_SEEN_W`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_blip_tolerant_end.py`:

```python
"""Tests for blip-tolerant graceful timeout (profile-independent end)."""

from datetime import datetime, timezone, timedelta

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
    STATE_ENDING,
    STATE_RUNNING,
    STATE_FINISHED,
    STATE_ANTI_WRINKLE,
    DEVICE_TYPE_DRYER,
    DEVICE_TYPE_WASHING_MACHINE,
)


def dt(s: float) -> datetime:
    return datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=s)


def test_config_has_blip_tolerant_defaults():
    cfg = CycleDetectorConfig(min_power=5.0, off_delay=60, device_type=DEVICE_TYPE_DRYER)
    assert cfg.crease_resume_threshold == 400.0
    assert cfg.unmatched_off_delay == 1800
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py::test_config_has_blip_tolerant_defaults -v`
Expected: FAIL — `TypeError` / `AttributeError` (fields don't exist).

- [ ] **Step 3: Add dataclass fields**

In `cycle_detector.py`, after line 88 (`anti_wrinkle_exit_power`), add:

```python
    crease_resume_threshold: float = 400.0
    unmatched_off_delay: int = 1800
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py::test_config_has_blip_tolerant_defaults -v`
Expected: PASS.

- [ ] **Step 5: Add const keys + defaults**

In `const.py`, after the anti-wrinkle CONF block (~Z.73) add:

```python
CONF_CREASE_RESUME_THRESHOLD = "crease_resume_threshold"  # W: below this in ENDING tail = anti-crease blip
CONF_UNMATCHED_OFF_DELAY = "unmatched_off_delay"  # s: graceful end delay for unmatched anti-wrinkle cycles
```

After the anti-wrinkle DEFAULT block (~Z.186) add:

```python
DEFAULT_CREASE_RESUME_THRESHOLD = 400.0  # W
DEFAULT_CREASE_RESUME_THRESHOLD_BY_DEVICE = {
    "dryer": 1000.0,
    "washer_dryer": 1000.0,
    "washing_machine": 250.0,
}
DEFAULT_UNMATCHED_OFF_DELAY = 1800  # s
DEFAULT_UNMATCHED_OFF_DELAY_BY_DEVICE = {
    "dryer": 900,
    "washer_dryer": 900,
    "washing_machine": 2400,
}
WM_SPIN_SEEN_W = 250.0  # washing machine: a spin-magnitude peak must have occurred before blip-tolerant finish
```

- [ ] **Step 6: Wire options → config in manager.py**

In `manager.py`, add imports next to the anti-wrinkle imports (Z.85-88 / Z.126-129):

```python
    CONF_CREASE_RESUME_THRESHOLD,
    CONF_UNMATCHED_OFF_DELAY,
```
```python
    DEFAULT_CREASE_RESUME_THRESHOLD,
    DEFAULT_CREASE_RESUME_THRESHOLD_BY_DEVICE,
    DEFAULT_UNMATCHED_OFF_DELAY,
    DEFAULT_UNMATCHED_OFF_DELAY_BY_DEVICE,
```

In the `CycleDetectorConfig(...)` call (after the `anti_wrinkle_exit_power=...` argument, ~Z.558), add:

```python
            crease_resume_threshold=float(
                config_entry.options.get(
                    CONF_CREASE_RESUME_THRESHOLD,
                    DEFAULT_CREASE_RESUME_THRESHOLD_BY_DEVICE.get(
                        self.device_type, DEFAULT_CREASE_RESUME_THRESHOLD
                    ),
                )
            ),
            unmatched_off_delay=int(
                config_entry.options.get(
                    CONF_UNMATCHED_OFF_DELAY,
                    DEFAULT_UNMATCHED_OFF_DELAY_BY_DEVICE.get(
                        self.device_type, DEFAULT_UNMATCHED_OFF_DELAY
                    ),
                )
            ),
```

- [ ] **Step 7: Run full suite (no regressions from plumbing)**

Run: `.venv/Scripts/python -m pytest tests/ -q --ignore=tests/test_verify_alignment.py --ignore=tests/test_mock_socket_synthesis.py --ignore=tests/repro`
Expected: baseline pass count unchanged (1 known pre-existing lingering-timer error is acceptable).

- [ ] **Step 8: Commit**

```bash
git add custom_components/ha_washdata/const.py custom_components/ha_washdata/cycle_detector.py custom_components/ha_washdata/manager.py tests/test_blip_tolerant_end.py
git commit -m "feat: add crease_resume_threshold + unmatched_off_delay config (plumbing)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Anti-crease blip classifier + guarded reset (dryer path)

**Files:**
- Modify: `custom_components/ha_washdata/cycle_detector.py:604-620` (guarded reset) and add helper method
- Test: `tests/test_blip_tolerant_end.py`

**Interfaces:**
- Consumes: `crease_resume_threshold`, `unmatched_off_delay`, `anti_wrinkle_enabled` from Task 1.
- Produces: method `CycleDetector._is_anti_crease_blip(self, power: float) -> bool`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_blip_tolerant_end.py`. This drives a dryer to ENDING, then sends periodic ~170 W blips; with the fix the cycle must reach `STATE_ANTI_WRINKLE`, not hang/force_stop:

```python
def _dryer_cfg() -> CycleDetectorConfig:
    return CycleDetectorConfig(
        min_power=5.0,
        off_delay=1800,
        device_type=DEVICE_TYPE_DRYER,
        anti_wrinkle_enabled=True,
        crease_resume_threshold=1000.0,
        unmatched_off_delay=900,
        stop_threshold_w=2.0,
        start_threshold_w=5.0,
    )


def test_dryer_crease_blips_reach_anti_wrinkle():
    det = CycleDetector(config=_dryer_cfg(), on_state_change=lambda *a: None, on_cycle_end=lambda *a: None)
    # Real drying: high power for ~40 min (unmatched -> no profile)
    det.process_reading(2000.0, dt(0))
    for t in range(10, 2400, 10):
        det.process_reading(2000.0, dt(t))
    # Power drops -> PAUSED/ENDING
    for t in range(2400, 2600, 10):
        det.process_reading(1.0, dt(t))
    assert det.state in (STATE_ENDING, STATE_ANTI_WRINKLE, STATE_FINISHED)
    # Crease-guard tail: 170 W blip every ~3 min, near-zero between, for ~30 min
    t = 2600
    while t < 4600 and det.state == STATE_ENDING:
        det.process_reading(170.0, dt(t)); t += 6          # brief blip
        det.process_reading(170.0, dt(t)); t += 6
        for _ in range(28):                                # ~2.8 min near-zero
            det.process_reading(1.0, dt(t)); t += 6
    assert det.state == STATE_ANTI_WRINKLE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py::test_dryer_crease_blips_reach_anti_wrinkle -v`
Expected: FAIL — state stays `STATE_ENDING` (blips keep resetting `time_below_threshold`).

- [ ] **Step 3: Add the classifier method**

In `cycle_detector.py`, add a method on `CycleDetector` (near the other helpers):

```python
    def _is_anti_crease_blip(self, power: float) -> bool:
        """True if this high reading is a post-program crease-guard blip that
        must NOT reset the end timer (unmatched, anti-wrinkle-enabled cycles only)."""
        if not self._config.anti_wrinkle_enabled:
            return False
        if self._state != STATE_ENDING or self._time_in_state < 120.0:
            return False
        if self._expected_duration > 0:
            return False
        # Anti-ghost: a real program (heating/spin) must have occurred.
        if self._cycle_max_power < self._config.crease_resume_threshold:
            return False
        # A sustained/high rise is a genuine resumption, not a blip.
        return power < self._config.crease_resume_threshold
```

- [ ] **Step 4: Guard the reset**

In `cycle_detector.py` replace the `is_high` branch reset (Z.606-617) so anti-crease blips are booked as idle instead of resetting the timer:

```python
        if is_high and not self._is_anti_crease_blip(power):
            self._time_above_threshold += dt
            self._time_below_threshold = 0.0
            step_wh = power * (dt / 3600.0)
            self._energy_since_idle_wh += step_wh
            self._last_active_time = timestamp
        else:
            self._time_below_threshold += dt
            self._time_above_threshold = 0.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py::test_dryer_crease_blips_reach_anti_wrinkle -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/ha_washdata/cycle_detector.py tests/test_blip_tolerant_end.py
git commit -m "feat: anti-crease blip classifier keeps end timer running (dryer)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: profile-independent graceful end (dryer), bypassing the energy gate

**Context (why this replaces a simple off_delay tweak):** The classifier from Task 2 stops anti-crease blips from resetting `_time_below_threshold`, but a SECOND barrier remains — the ENDING fallback energy gate (~Z.1160-1177) integrates **raw power** over a trailing `gate_window` (= `off_delay`). A 170 W blip every few minutes keeps that window's energy above `end_energy_threshold`, so completion stays blocked → watchdog `force_stop` in production. Confirmed by the Task 2 reviewer. Fix: add an explicit graceful-finish path that keys on **time since the last REAL activity** — which `_last_active_time` already tracks correctly, because Task 2's guarded reset does NOT update `_last_active_time` for blips. This bypasses the raw-power energy gate. Scoped to `dryer`/`washer_dryer` here (Task 4 extends to WM).

**Files:**
- Modify: `custom_components/ha_washdata/cycle_detector.py` (ENDING fallback section, ~Z.1114, before the `effective_off_delay`/energy-gate logic)
- Test: `tests/test_blip_tolerant_end.py`

**Interfaces:**
- Consumes: `unmatched_off_delay`, `anti_wrinkle_enabled`, `crease_resume_threshold` (Task 1); the `_last_active_time` semantics established by Task 2 (only real, non-blip activity advances it). `DEVICE_TYPE_DRYER`/`DEVICE_TYPE_WASHER_DRYER` are already imported in this module.
- Produces: an early graceful-finish branch in ENDING; Task 4 will widen its device gate to include `DEVICE_TYPE_WASHING_MACHINE`.

- [ ] **Step 1: Write the failing test**

This uses REALISTIC config (default `end_energy_threshold`, i.e. NOT the 1000.0 isolation value from Task 2) with the periodic-blip crease tail, and asserts the dryer reaches `STATE_ANTI_WRINKLE` — proving the energy-gate bypass. Add to `tests/test_blip_tolerant_end.py`:

```python
def _dryer_cfg_realistic() -> CycleDetectorConfig:
    return CycleDetectorConfig(
        min_power=5.0, off_delay=1800, device_type=DEVICE_TYPE_DRYER,
        anti_wrinkle_enabled=True, crease_resume_threshold=1000.0,
        unmatched_off_delay=900, stop_threshold_w=2.0, start_threshold_w=5.0,
    )  # note: default end_energy_threshold (0.05) — the energy gate is in play

def test_dryer_finishes_despite_energy_gate():
    det = CycleDetector(config=_dryer_cfg_realistic(), on_state_change=lambda *a: None, on_cycle_end=lambda *a: None)
    det.process_reading(2000.0, dt(0))
    for t in range(10, 2400, 10):
        det.process_reading(2000.0, dt(t))            # ~40 min real drying, unmatched
    for t in range(2400, 2600, 10):
        det.process_reading(1.0, dt(t))               # power drops -> ENDING
    # Crease tail: 170 W blip every ~3 min for a long time; last REAL activity was t~2390
    t = 2600
    while t < 6000 and det.state == STATE_ENDING:
        det.process_reading(170.0, dt(t)); t += 6
        for _ in range(28):
            det.process_reading(1.0, dt(t)); t += 6
    assert det.state == STATE_ANTI_WRINKLE
    # finished within ~unmatched_off_delay of the last blip's real-activity reset semantics
```

Note: because a 170 W blip DOES advance `_last_active_time` only if it is NOT classified as a blip, verify the classifier treats the 170 W (< crease_resume_threshold 1000) reading as a blip so `_last_active_time` stays at the pre-tail value. If the blips are correctly classified, `since_real` grows monotonically and the finish fires ~`unmatched_off_delay` after drying stopped.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py::test_dryer_finishes_despite_energy_gate -v`
Expected: FAIL — cycle stays `STATE_ENDING` (energy gate keeps it open; no early-finish path yet).

- [ ] **Step 3: Add the early graceful-finish branch**

In `cycle_detector.py`, in the ENDING handler's `else` branch (the non-`is_high` path that runs the fallback-timeout logic), BEFORE the existing `effective_off_delay`/energy-gate code (~Z.1114), insert:

```python
                # --- PROFILE-INDEPENDENT GRACEFUL END (unmatched anti-wrinkle) ---
                # No matched profile => no smart-termination; and the crease-guard
                # blips keep the raw-power energy gate shut. Finish on time since
                # the last *real* activity (blips excluded via _last_active_time,
                # see _is_anti_crease_blip). termination_reason "timeout" routes to
                # STATE_ANTI_WRINKLE. Dryer/washer_dryer only here; Task 4 adds WM.
                if (
                    self._expected_duration <= 0
                    and self._config.anti_wrinkle_enabled
                    and self._config.device_type
                    in (DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHER_DRYER)
                    and self._cycle_max_power >= self._config.crease_resume_threshold
                    and self._last_active_time is not None
                    and (timestamp - self._last_active_time).total_seconds()
                    >= self._config.unmatched_off_delay
                ):
                    self._logger.debug(
                        "Unmatched anti-wrinkle graceful end: %.0fs since last real activity",
                        (timestamp - self._last_active_time).total_seconds(),
                    )
                    self._finish_cycle(
                        timestamp,
                        status="completed",
                        termination_reason="timeout",
                        keep_tail=False,
                    )
                    return
```

If `_last_active_time` is not populated on the code path reached here, read how the neighboring timeout branch obtains the last-activity timestamp and use the same source; do NOT invent a new field.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py::test_dryer_finishes_despite_energy_gate -v`
Expected: PASS. Also run `tests/test_blip_tolerant_end.py` in full to confirm Task 2's test still passes.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_washdata/cycle_detector.py tests/test_blip_tolerant_end.py
git commit -m "feat: profile-independent graceful end for unmatched dryer anti-wrinkle cycles

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Extend the graceful end to the washing machine (conservative long window)

**Decision (supersedes the spec's spin-seen guard):** distinguishing a WM spin from heating in the power curve proved intractable (both are high-power). Instead, the WM gets the SAME graceful-end branch as the dryer, with conservatism coming entirely from its long `unmatched_off_delay` (2400 s = 40 min of no real activity). A WM soak/pause between phases is effectively never 40 min, so a false early finish is not a realistic risk. The now-unused `WM_SPIN_SEEN_W` constant is removed.

**Files:**
- Modify: `custom_components/ha_washdata/cycle_detector.py` (device-gate tuple in the Task-3 graceful-end branch)
- Modify: `custom_components/ha_washdata/const.py` (remove `WM_SPIN_SEEN_W`)
- Test: `tests/test_blip_tolerant_end.py`

**Interfaces:**
- Consumes: the graceful-end branch added in Task 3; `unmatched_off_delay`, `crease_resume_threshold`, `anti_wrinkle_enabled` (Task 1).

- [ ] **Step 1: Write the failing tests**

A WM (unmatched, anti-wrinkle) that has run a real program and then sits quiet past `unmatched_off_delay` must reach ANTI_WRINKLE; one whose quiet period is SHORTER than `unmatched_off_delay` must NOT finish early. Append to `tests/test_blip_tolerant_end.py`:

```python
def _wm_cfg() -> CycleDetectorConfig:
    return CycleDetectorConfig(
        min_power=5.0, off_delay=3600, device_type=DEVICE_TYPE_WASHING_MACHINE,
        anti_wrinkle_enabled=True, crease_resume_threshold=250.0, unmatched_off_delay=2400,
        stop_threshold_w=2.0, start_threshold_w=5.0,
    )

def test_wm_unmatched_finishes_after_long_quiet():
    det = CycleDetector(config=_wm_cfg(), on_state_change=lambda *a: None, on_cycle_end=lambda *a: None)
    det.process_reading(2000.0, dt(0))                 # heating (real program, unmatched)
    for t in range(10, 1500, 10):
        det.process_reading(60.0, dt(t))               # wash tumbling
    for t in range(1500, 1800, 10):
        det.process_reading(400.0, dt(t))              # final spin -> last real activity ~t=1790
    for t in range(1800, 2000, 10):
        det.process_reading(1.0, dt(t))                # power drops -> ENDING
    # Crease tail (12-30 W blips) well past unmatched_off_delay (2400 s)
    t = 2000
    while t < 1790 + 2400 + 600 and det.state == STATE_ENDING:
        det.process_reading(20.0, dt(t)); t += 6
        for _ in range(20):
            det.process_reading(1.0, dt(t)); t += 6
    assert det.state == STATE_ANTI_WRINKLE

def test_wm_short_quiet_not_finished_early():
    det = CycleDetector(config=_wm_cfg(), on_state_change=lambda *a: None, on_cycle_end=lambda *a: None)
    det.process_reading(2000.0, dt(0))
    for t in range(10, 1500, 10):
        det.process_reading(60.0, dt(t))
    for t in range(1500, 1800, 10):
        det.process_reading(400.0, dt(t))              # spin, last real activity ~t=1790
    # Quiet for only ~10 min (< unmatched_off_delay 2400 s)
    t = 1800
    while t < 1790 + 600:
        det.process_reading(1.0, dt(t)); t += 10
    assert det.state != STATE_ANTI_WRINKLE              # must NOT finish early
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py -k wm -v`
Expected: `test_wm_unmatched_finishes_after_long_quiet` FAILS (WM excluded from the dryer-only device gate → never reaches ANTI_WRINKLE via the graceful-end branch).

- [ ] **Step 3: Widen the device gate + remove unused constant**

In `cycle_detector.py`, in the Task-3 graceful-end branch, add `DEVICE_TYPE_WASHING_MACHINE` to the device tuple:

```python
                    and self._config.device_type
                    in (
                        DEVICE_TYPE_DRYER,
                        DEVICE_TYPE_WASHER_DRYER,
                        DEVICE_TYPE_WASHING_MACHINE,
                    )
```

In `const.py`, remove the `WM_SPIN_SEEN_W = 250.0` line. First confirm it is not imported anywhere: run `grep -rn WM_SPIN_SEEN_W custom_components tests` — it must appear only at its definition (Task 1 left it unused). If any import references it, remove those too.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py -k wm -v`
Expected: both PASS. Then the full module: `.venv/Scripts/python -m pytest tests/test_blip_tolerant_end.py -v` (all prior tests still green).

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_washdata/cycle_detector.py custom_components/ha_washdata/const.py tests/test_blip_tolerant_end.py
git commit -m "feat: extend graceful end to washing machine (conservative long window)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Config-flow UI, regression sweep, docs

**Files:**
- Modify: `custom_components/ha_washdata/config_flow.py:74-148, 1410-1446`
- Modify: `README` / `CHANGELOG`
- Test: full suite

**Interfaces:**
- Consumes: const keys from Task 1.

- [ ] **Step 1: Add the two options to the advanced-settings schema**

In `config_flow.py`, add imports (next to the anti-wrinkle CONF/DEFAULT imports at Z.74-77 / 145-148):

```python
    CONF_CREASE_RESUME_THRESHOLD,
    CONF_UNMATCHED_OFF_DELAY,
```
```python
    DEFAULT_CREASE_RESUME_THRESHOLD,
    DEFAULT_UNMATCHED_OFF_DELAY,
```

In the advanced-settings schema dict (near the anti-wrinkle fields ~Z.1444), add two optional numeric fields mirroring `CONF_ANTI_WRINKLE_MAX_POWER`:

```python
            vol.Optional(
                CONF_CREASE_RESUME_THRESHOLD,
                default=get_val(CONF_CREASE_RESUME_THRESHOLD, DEFAULT_CREASE_RESUME_THRESHOLD),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_UNMATCHED_OFF_DELAY,
                default=get_val(CONF_UNMATCHED_OFF_DELAY, DEFAULT_UNMATCHED_OFF_DELAY),
            ): vol.Coerce(int),
```

(Match the exact `get_val`/selector idiom used by the neighboring anti-wrinkle fields; if they use a number selector, use the same.)

- [ ] **Step 2: Add opt-out + matched-regression tests**

Append to `tests/test_blip_tolerant_end.py`:

```python
def test_disabled_device_resets_as_before():
    cfg = CycleDetectorConfig(min_power=5.0, off_delay=1800, device_type=DEVICE_TYPE_DRYER,
                              anti_wrinkle_enabled=False, crease_resume_threshold=1000.0)
    det = CycleDetector(config=cfg, on_state_change=lambda *a: None, on_cycle_end=lambda *a: None)
    det.process_reading(2000.0, dt(0))
    for t in range(10, 2400, 10):
        det.process_reading(2000.0, dt(t))
    for t in range(2400, 2600, 10):
        det.process_reading(1.0, dt(t))
    t = 2600
    while t < 4600 and det.state == STATE_ENDING:
        det.process_reading(170.0, dt(t)); t += 6
        for _ in range(28):
            det.process_reading(1.0, dt(t)); t += 6
    # anti-wrinkle disabled -> blip resets timer -> does NOT reach ANTI_WRINKLE via this path
    assert det.state != STATE_ANTI_WRINKLE
```

- [ ] **Step 3: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q --ignore=tests/test_verify_alignment.py --ignore=tests/test_mock_socket_synthesis.py --ignore=tests/repro`
Expected: all green except the 1 known pre-existing lingering-timer error. Confirm `tests/test_issue_68_anti_wrinkle.py` and profile-matching tests are unaffected.

- [ ] **Step 4: Docs**

- README: one line under the anti-wrinkle section — unmatched anti-wrinkle cycles (esp. dryers) now end via graceful timeout instead of `force_stop`.
- CHANGELOG: feature entry in upstream style, next fork version.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_washdata/config_flow.py tests/test_blip_tolerant_end.py README* CHANGELOG*
git commit -m "feat: expose blip-tolerant options in config flow + docs + regression tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Live validation (post-merge):** next unattended dryer run on Tiny should end `timeout`/`anti_wrinkle` (not `force_stop`), notification near the real drying end. Compare against the 2026-07-07 02:53 force_stop baseline.
- **WM path:** if Task 4's spin-seen separation proves fiddly, ship dryer-only (keep the WM defaults but gate the WM branch behind the spin-seen flag returning `False` until `_spin_seen` tracking lands) — matches the "Trockner zuerst" decision.
- **Before upstream PR:** drop `docs/superpowers/**` commits from the branch, rebase onto `upstream/main`, update `.superpowers/sdd/progress.md`.
