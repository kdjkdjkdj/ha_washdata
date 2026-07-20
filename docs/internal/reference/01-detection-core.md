# WashData Detection Core — Engineering Reference

Scope: the four detection-core modules of the WashData HA integration
(`custom_components/ha_washdata/`). Read in full and cross-referenced against
`const.py`, `manager.py`, `profile_store.py`, and `playground.py`.

- `cycle_detector.py` — the OFF→STARTING→RUNNING↔PAUSED→ENDING state machine (1968 lines)
- `signal_processing.py` — resampling + `integrate_wh` energy math (251 lines)
- `features.py` — compact cycle signature (116 lines)
- `time_utils.py` — power_data format detection / offset conversion (291 lines)

> **CLAUDE.md discrepancy (important).** Both CLAUDE.md and the assessment scope
> describe `signal_processing.py` as containing *"Resampling, filtering, DTW
> implementation."* **Neither DTW nor any filtering/smoothing lives here.**
> - DTW is entirely in `analysis.py` (`compute_dtw_lite` @ analysis.py:159,
>   `_dtw_component_score` @ :245, `compute_dtw_path` @ :397). The DTW *variants*
>   (`scaled`/`ddtw`/`ensemble`/`legacy`) documented in CLAUDE.md's matching
>   section are implemented in `analysis.py`, not here.
> - There is no filter/smoothing function in `signal_processing.py`. The only
>   "smoothing" in the detection path is a display-only moving-average buffer
>   (`_ma_buffer`) inside `cycle_detector.py` that the state machine does **not**
>   consume (it works on raw power + time accumulators).
>
> A doc-writer should correct the module blurb: `signal_processing.py` =
> *resampling + energy integration primitives* only.

---

## 1. `cycle_detector.py` — the cycle state machine

### 1.1 File purpose

The single-device power state machine. Consumes `(power, timestamp)` readings one
at a time from `manager.async_handle_power_change`, accumulates dt-aware
time/energy statistics, drives a state machine, invokes the async profile matcher
periodically, and calls back the manager on state change and cycle end. It is
**pure** in the sense that it never touches HA directly — everything comes in via
config + callbacks, and the same class is instantiated headless by the Playground
(`playground.py`) so what-if replays are byte-identical to live detection.

Two providers are injected optionally by the manager (both gated on the
`CONF_ENABLE_ML_MODELS` opt-in): the **ML end-guard** (`end_confidence_provider`)
and the **terminal-drop detector** (`terminal_drop_provider`). Both are
strictly one-directional and never break detection when absent.

### 1.2 States (from `const.py`)

Imported state constants: `STATE_OFF`, `STATE_DELAY_WAIT`, `STATE_STARTING`,
`STATE_RUNNING`, `STATE_PAUSED`, `STATE_ENDING`, `STATE_FINISHED`,
`STATE_ANTI_WRINKLE`, `STATE_INTERRUPTED`, `STATE_FORCE_STOPPED`, `STATE_UNKNOWN`.

Canonical happy path: `OFF → STARTING → RUNNING ↔ PAUSED → ENDING → OFF`
(via a terminal state `FINISHED`/`INTERRUPTED`/`FORCE_STOPPED`/`ANTI_WRINKLE`).
Two side branches: **delayed-start** (`OFF → DELAY_WAIT → STARTING`) and
**anti-wrinkle** (dryers: terminal `ANTI_WRINKLE`, which can re-enter `STARTING`).

> **Terminal-state expiry is NOT owned by the detector.** The comment at
> cycle_detector.py:895-902 documents that `FINISHED/INTERRUPTED/FORCE_STOPPED →
> OFF` is owned solely by the manager (`WashDataManager._handle_state_expiry`,
> with the opt-in power-based Off of issue #284). The old detector 30-min
> auto-expire was removed. `ANTI_WRINKLE → OFF` is the one exception the detector
> still owns (its own idle/timeout logic).

### 1.3 Module-level items

- **Load-time invariants** (cycle_detector.py:64, :85): raises `ValueError` if
  `DISHWASHER_END_SPIKE_WAIT_SECONDS <= 0` or `DISHWASHER_END_SPIKE_MIN_PROGRESS`
  is not a fraction in (0,1). Deliberately runtime checks (not `assert`, which
  `python -O` strips), because two code paths must release the dishwasher end
  wait at the same instant.
- **Detector-internal ML-guard policy** (kept out of `const.py` on purpose,
  cycle_detector.py:77-84):
  - `ML_END_GUARD_MIN_CONFIDENCE = 0.5` — P(true end) below this ⇒ treat as a
    likely pause (defer).
  - `ML_END_GUARD_MAX_DEFER_SECONDS = 1800.0` — cap on the extra wait the guard
    may add (30 min).
  - `ML_PROVIDER_THROTTLE_SECONDS = 30.0` — recompute cap for both providers
    (data-clock seconds). Safe to cache because the guard only ever *defers* and
    terminal-drop only ever *shortens*, so a stale value ≤ this window is tolerable.
- `STOP_LOCKOUT_RELEASE_SECONDS = 180.0` (cycle_detector.py:98) — issue #267
  safety net: releases the manual-stop lockout when power stays high longer than
  any plausible spin-down (back-to-back load).
- `trim_zero_readings(readings, threshold=0.5, trim_start, trim_end)`
  (cycle_detector.py:146) — trims leading/trailing near-zero samples. All-zero
  input returns `readings[:1]`. Only caller is `_finish_cycle` (passes
  `threshold=stop_threshold_w`, `trim_end=not keep_tail`).

### 1.4 `CycleDetectorConfig` (dataclass, cycle_detector.py:101)

Detection tunables carried on the detector. Notable fields + defaults:

| field | default | meaning |
|---|---|---|
| `min_power` / `off_delay` | (required) | base sensitivity + off-delay seconds |
| `device_type` | `"washing_machine"` | branches dishwasher/dryer/washer-dryer logic |
| `smoothing_window` | 5 | display MA buffer size (not used by logic) |
| `interrupted_min_seconds` | 150 | below → status `interrupted` |
| `completion_min_seconds` | 600 | below → status `interrupted` |
| `abrupt_drop_watts/_ratio/_high_load_factor` | 500/0.6/5.0 | (abrupt-drop fields; `_abrupt_drop` is set but never assigned True in current code — see gotcha) |
| `start_duration_threshold` | 5.0 | seconds of sustained high power to confirm start |
| `start_energy_threshold` | 0.005 Wh | energy gate to confirm STARTING→RUNNING |
| `end_energy_threshold` | 0.05 Wh | max energy in the look-back window to allow end |
| `end_repeat_count` | 1 | (carried, not used in this file) |
| `min_off_gap` | 60 | min quiet gap to separate cycles |
| `start_threshold_w` / `stop_threshold_w` | 2.0 / 2.0 | hysteresis thresholds |
| `min_duration_ratio` | 0.8 | deferred-finish ratio |
| `power_off_threshold_w` / `power_off_delay` | 0.0 / 30.0 | issue #284 power-based Off — **carried for the manager to read live; the detector never acts on them** |
| `match_interval` | 300 | seconds between periodic matches |
| `profile_duration_tolerance` | 0.25 | ±25% completion window |
| `anti_wrinkle_enabled/max_power/max_duration/exit_power` | False/400/60/0.8 | dryer anti-wrinkle |
| `delay_detect_enabled/delay_confirm_seconds/delay_timeout_seconds` | False/60/28800 | delayed-start band |

### 1.5 Key instance state

- Data: `_power_readings: list[(datetime, raw_power)]`, `_current_cycle_start`,
  `_last_active_time`, `_cycle_max_power`.
- Accumulators (dt-aware): `_energy_since_idle_wh`, `_time_above_threshold`,
  `_time_below_threshold`, `_last_process_time`, `_time_in_state`.
- Cadence: `_recent_dts` (last 20 dt), `_p95_dt` (drives dynamic thresholds).
- Match trackers: `_matched_profile`, `_expected_duration`,
  `_last_match_confidence`, `_match_ambiguous`, `_match_prefix_ambiguous`,
  `_end_spike_seen`, `_end_spike_duration`, `_verified_pause`.
- Lockout: `_ignore_power_until_idle`, `_lockout_high_seconds`.
- Provider throttle caches: `_ml_end_cache`, `_terminal_drop_cache`
  (both `(last_reading_ts, expected_duration, cycle_start, result)`),
  `_ml_defer_start_duration`.
- Anti-wrinkle: `_anti_wrinkle_candidate_start/_peak/_start_power`,
  `_anti_wrinkle_idle_time`, `_anti_wrinkle_idle_timeout=120.0`.
- Delayed start: `_delay_band_start/_seconds/_peak`,
  `_delay_wait_true_off_seconds`, `_delay_wait_high_start/_high_power`,
  `_preserve_delay_band_on_off`.

### 1.6 Dynamic (cadence-adaptive) thresholds

- `_dynamic_pause_threshold` (property, :333) = `max(15.0, 3.0 * _p95_dt)` —
  seconds of low power before RUNNING→PAUSED. "T_pause ≥ 3× p95 update interval".
- `_dynamic_end_threshold` (property, :340) = `max(3*_p95_dt,
  _dynamic_pause_threshold + 15.0)` — PAUSED→ENDING gate; always ≥15s above pause.
- `_update_cadence(dt)` (:348) — appends dt (ignoring dt≤0.1s), keeps last 20,
  computes `_p95_dt = np.percentile(recent, 95)` once ≥5 samples (else
  `max(dt,1.0)`). This is the *only* NumPy call on the hot reading path.

### 1.7 `process_reading(power, timestamp)` — the hot path (:641)

The per-reading entry point. Order of operations:

1. **dt computation.** `dt = (timestamp - _last_process_time)`. **Negative dt is
   dropped** (updates `_last_process_time` and returns) — protects against
   out-of-order sensor events.
2. **Manual-stop lockout** (:661). If `_ignore_power_until_idle`:
   - power < `start_threshold_w` → clear lockout (normal path).
   - else accumulate `_lockout_high_seconds += dt`; while `< STOP_LOCKOUT_RELEASE_SECONDS`
     (180s) swallow the reading (return). Past that, release the lockout and fall
     through to start a genuinely new back-to-back load (issue #267).
3. `_update_cadence(dt)`; set `_last_process_time`.
4. Push into display MA buffer (`_ma_buffer`, capped at `smoothing_window`).
5. **Hysteresis + accumulators.** Threshold is `start_threshold_w` in
   `{OFF, DELAY_WAIT, STARTING, UNKNOWN}`, else `stop_threshold_w`. `is_high =
   power >= threshold`.
   - high: `_time_above_threshold += dt`, reset `_time_below_threshold`,
     `_energy_since_idle_wh += power * dt/3600` (simple rectangle, not trapezoid —
     see gotcha), set `_last_active_time`.
   - low: `_time_below_threshold += dt`, reset `_time_above_threshold`.
   - `_time_in_state += dt`; `_last_power = power`.
6. Dispatch by state (below).

> **Gotcha — inline energy vs `integrate_wh`.** The running accumulator at :710
> uses `power * dt/3600` (left-rectangle), *not* the shared trapezoidal
> `integrate_wh`. Only the ENDING energy-gate look-back (:1370) and the final
> persisted cycle energy (in `manager`/`profile_store`) use `integrate_wh`. The
> accumulator is only used as the STARTING→RUNNING confirmation gate, so the
> approximation is acceptable, but it is a deliberate divergence from the
> "single shared implementation" rule that applies to *persisted* energy.

### 1.8 State-machine transitions (the core algorithm)

**OFF / FINISHED / INTERRUPTED / FORCE_STOPPED / ANTI_WRINKLE block (:732).**

- *Anti-wrinkle re-arm* (:740, dryers/washers/washer-dryers only). While in
  `ANTI_WRINKLE`, a high reading starts/extends a candidate burst
  (`_anti_wrinkle_candidate_*`). If the candidate peak or current power exceeds
  `anti_wrinkle_max_power` **or** the candidate duration exceeds
  `anti_wrinkle_max_duration`, it transitions to `STARTING`, seeding
  `_power_readings` with the candidate window (preserving ramp-up samples) and
  back-computing `_energy_since_idle_wh` over the interval.
- *Anti-wrinkle idle/exit* (:789). Below `max(anti_wrinkle_exit_power,
  stop_threshold_w)` accumulates `_anti_wrinkle_idle_time`; when it reaches
  `max(_dynamic_end_threshold, _anti_wrinkle_idle_timeout=120)` → OFF. A
  low-power gap invalidates any burst candidate. A 2-hour hard safety timeout
  (`> 7200s` in state) forces OFF. `return`s after handling.
- *Delayed-start band* (:838, only from OFF, `delay_detect_enabled`,
  `stop_threshold_w < start_threshold_w`). Tracks anchored time while power sits
  in `[stop_threshold_w, start_threshold_w)`; once `_delay_band_seconds >=
  delay_confirm_seconds` → `DELAY_WAIT`. Power below `stop_threshold_w` resets
  the band; power above `start_threshold_w` falls through to normal start.
- *Normal start* (:886). `is_high` and not just-started-from-anti-wrinkle →
  `STARTING`, seed `_current_cycle_start=timestamp`, `_power_readings=[(t,p)]`,
  `_cycle_max_power=p`. `_preserve_delay_band_on_off` remembers whether a band
  candidate was in flight so a false-start peak doesn't lose accumulated band time.

**DELAY_WAIT (:904).** Waits for a genuine start vs. a spurious spike.
- power ≥ `start_threshold_w`: anchor first high reading; only commit to STARTING
  once the high streak spans `start_duration_threshold` **real** seconds
  (measured between two consecutive high readings, not dt to the previous low
  reading — prevents a single isolated spike from tripping on a long sample
  interval). Seeds the cycle from the anchor.
- power < `start_threshold_w`: clear the high anchor. If power < `stop_threshold_w`
  accumulate `_delay_wait_true_off_seconds`; ≥30s → OFF (delayed start cancelled).
  Safety: `>= delay_timeout_seconds` (8h) → OFF.

**STARTING (:978).** Append reading, update peak.
- Confirm: `_time_above_threshold >= start_duration_threshold` **and**
  `_energy_since_idle_wh >= start_energy_threshold` → RUNNING. (Dual gate: time +
  energy, so a brief blip doesn't confirm.)
- Abort: `not is_high and _time_below_threshold > 1.0` (1s grace) → OFF (false
  start) — **unless `_verified_pause`** (user pause, issue #306), which holds.

**RUNNING (:1005).** Append, update peak.
- `_time_below_threshold >= _dynamic_pause_threshold` → force a match
  (`_try_profile_match(force=True)`) then → PAUSED.
- Periodic `_try_profile_match(timestamp)` (rate-limited by `match_interval`).
- 8h hard safety (`> 28800s`) → `_finish_cycle(status="force_stopped")`.

**PAUSED (:1025).** Append.
- `is_high` → RUNNING (resume).
- else periodic match; `_time_below_threshold >= _dynamic_end_threshold` → ENDING.

**ENDING (:1039).** The most complex state. Two arms:

*High reading arm (:1042)* — decide whether a power burst is a terminal end-spike,
a mid-cycle resume, or noise:
- Compute `current_duration`. **End-spike arming gate (issue #43):** set
  `_end_spike_seen=True` + `_end_spike_duration` only if `_expected_duration <= 0`
  (unmatched — legacy "any spike counts") **or** `current_duration >=
  expected * DISHWASHER_END_SPIKE_MIN_PROGRESS (0.85)`. A mid-cycle
  wash→dry drain wind-down at ~50% must not arm smart termination, else the cycle
  finishes before the real pump-out and the pump-out reads as a ghost cycle.
- Sanity: if `_expected_duration` invalid (≤0 or >6h) and `current_duration >
  10800s` (3h), fall back to `effective_expected = current_duration*0.99`.
- `past_expected = current_duration >= effective_expected*0.98`.
- `long_ending_tail = _time_in_state >= 120s`; `terminal_spike = long_ending_tail`.
  Dishwashers additionally: `near_expected = current_duration >= expected*0.90`,
  `terminal_spike = near_expected or long_ending_tail`.
- If `terminal_spike` → stay in ENDING (`return`). Else if `past_expected` → stay
  (spike recorded, no resume). Else → RUNNING (genuine mid-cycle activity).

*Low reading arm (:1131)* — periodic match, then two termination mechanisms:

1. **Smart Termination (:1138)** — only with a matched profile.
   - `smart_ratio`: dishwasher `0.90` if a ≥90%-progress pump-out spike was seen
     else `0.99`; washing-machine/washer-dryer/other `0.98`.
   - `is_confident_match = _last_match_confidence >= 0.4`.
   - Fires only if `current_duration >= expected*smart_ratio` **and**
     `is_confident_match` **and** `not _match_ambiguous` **and**
     `not _match_prefix_ambiguous`. (The two ambiguity gates prevent cutting a
     long program short when a near-duplicate/prefix profile matched.)
   - `smart_debounce` (time required in ENDING): dishwasher = fixed
     `DISHWASHER_SMART_TERMINATION_DEBOUNCE_SECONDS (300)` — **deliberately NOT
     derived from off_delay** (the old `max(300, off_delay*0.25)` coupling starved
     dishwasher termination — see const.py:597-612 and MEMORY
     `project_offdelay_smart_debounce_coupling`); washer/washer-dryer =
     `max(180, min_off_gap*0.5)`; other = 120.
   - **Dishwasher pump-out wait (:1211):** if `_time_in_state >= smart_debounce`
     but no end-spike seen and not `past_wait_period`, keep waiting.
     `past_wait_period = current_duration >= expected + DISHWASHER_END_SPIKE_WAIT_SECONDS
     (1800)` **OR** (`current_duration >= expected` **and** `_time_below_threshold
     >= DISHWASHER_END_SPIKE_QUIET_RELEASE_SECONDS (600)`). Takes the *sooner* of
     the two — can only shorten. The quiet-release arm closes cycles whose pump-out
     landed before the drop into ENDING; the `>= expected` gate keeps a long
     passive-drying phase deferred.
   - On fire: `_finish_cycle(status="completed", reason=SMART, keep_tail=True)`.

2. **Fallback timeout (:1286).** `effective_off_delay = max(off_delay, min_off_gap)`.
   `gate_window = off_delay`. Dishwasher unmatched + end-spike-seen caps
   `effective_off_delay` (and `gate_window`) at 1800s (:1300).
   - **Terminal-drop fast finalize (:1317)** — opt-in shorten-only. Fires when
     `_terminal_drop_provider is not None` **and** `not _verified_pause` **and**
     `effective_off_delay > TERMINAL_DROP_OFF_DELAY_SECONDS (90)` **and**
     `_time_below_threshold >= 90` **and** `_is_terminal_drop()`. Then
     `_finish_cycle(status="interrupted", reason=TERMINAL_DROP, keep_tail=False)`.
     The energy/defer gates are *bypassed* — the sustained sub-threshold span
     already proves the appliance is off and the anomaly check ruled out a pause.
   - Normal path: once `_time_below_threshold >= effective_off_delay`, build the
     `recent_window` (readings within `gate_window` of now). If empty → check
     `_should_defer_finish`, else finish (dishwasher `keep_tail=True`). If
     non-empty, compute `recent_e = integrate_wh(recent_ts, recent_p,
     max_gap_s=energy_gap_threshold_s(recent_ts))`. If `recent_e <=
     end_energy_threshold` → defer-check then finish, else **energy gate blocks
     the end** (logs "prevented by energy gate").

### 1.9 `_transition_to(new_state, timestamp)` (:1389)

No-op if already in `new_state`. Sets `_state`, `_state_enter_time`, resets
`_time_in_state=0`, sets `_sub_state = new_state.capitalize()` (overridden for
`DELAY_WAIT`→"Waiting to Start", `ANTI_WRINKLE`→"Anti-Wrinkle"). Per-state resets:
clears `_ml_defer_start_duration` whenever leaving ENDING; on OFF resets energy +
delay trackers (honoring `_preserve_delay_band_on_off`); on ENDING resets
`_end_spike_seen/_duration`; on DELAY_WAIT/RUNNING resets the band. Finally calls
`_on_state_change(old, new)` (the manager callback).

### 1.10 `_should_defer_finish(duration) -> bool` (:1523)

The deferral policy, evaluated whenever the fallback timeout is about to finish.
Ordered checks:
1. `_verified_pause` → defer (user/envelope pause).
2. Dishwasher `duration < DISHWASHER_MIN_CYCLE_DURATION_S (1800)` → defer (floor,
   even without a matched profile).
3. No matched profile or `_expected_duration <= 0` → do **not** defer (False).
4. Safety: `duration > expected + DEFAULT_MAX_DEFERRAL_SECONDS (14400 = 4h)` →
   allow finish (never defer forever).
5. **ML end-guard (:1565)** — only when `_end_confidence_provider` wired **and**
   `_last_match_confidence >= DEFAULT_DEFER_FINISH_CONFIDENCE (0.55)`. If
   `_ml_end_confidence() < ML_END_GUARD_MIN_CONFIDENCE (0.5)`, defer — but only
   while `(duration - _ml_defer_start_duration) < ML_END_GUARD_MAX_DEFER_SECONDS
   (1800)`. If the model is confident it's the end, clear `_ml_defer_start_duration`
   (stop ML-deferring). Asymmetric: can only delay, bounded so a wrong model can't
   hang the cycle.
6. Dishwasher passive-drying protection (:1594): matched, `duration <
   expected*0.85` → defer (confidence gate bypassed — confidence may be low here).
7. Dishwasher pump-out wait (:1631): matched, not end-spike-seen, `duration <
   expected + 1800`, and not `quiet_released` (`duration >= expected` and quiet ≥
   600s) → defer. Mirrors the STATE_ENDING pump-out wait so both release together.
8. Generic min-duration ratio: if `_last_match_confidence <
   DEFAULT_DEFER_FINISH_CONFIDENCE (0.55)` → do not defer. Else if `duration <
   expected * min_duration_ratio` → defer; else allow.

### 1.11 ML/anomaly hooks

- `_ml_end_confidence() -> float | None` (:1452). Builds offset-second points from
  `_power_readings`, throttles via `_ml_end_cache` (scoped to cycle_start +
  expected_duration, `< ML_PROVIDER_THROTTLE_SECONDS` on data-clock). Returns
  `None` on no provider/no start/exception (ML must never break detection).
- `_is_terminal_drop() -> bool` (:1488). Mirror of the above with
  `_terminal_drop_cache`; returns `False` on any absence/exception. The manager's
  `_terminal_drop_provider` delegates to `profile_store.is_terminal_drop(...)`
  (pure decision, profile_store.py:494): requires clearly-ON (peak ≥
  `TERMINAL_DROP_MIN_PEAK_RATIO*stop_threshold`), familiar (peak within historical
  `peak_range` widened by `TERMINAL_DROP_PEAK_FAMILIAR_TOL=0.4`), and anomalous
  (trailing sub-threshold span began at offset `< earliest_quiet *
  TERMINAL_DROP_EARLINESS_RATIO(0.8)`). Baselines need
  `TERMINAL_DROP_MIN_CLEAN_CYCLES(3)` completed cycles.

### 1.12 `_finish_cycle(...)` (:1696)

The single cycle-completion path.
- `end_time = timestamp if keep_tail else (_last_active_time or timestamp)`.
- No `_current_cycle_start` → `reset()` and bail.
- `duration = end_time - start`. **Status downgrade to `interrupted`** if
  `duration < interrupted_min_seconds (150)`, or `< completion_min_seconds (600)`,
  or (`_abrupt_drop` and `duration < interrupted_min_seconds+90`).
- `trim_zero_readings(threshold=stop_threshold_w, trim_end=not keep_tail)`; append
  a synthetic `(end_time, last_p)` sample if the trace ends before `end_time`
  (covers drying phases with no sensor updates).
- Builds `cycle_data` with **offset-format** `power_data`
  (`[round(t.timestamp()-start_ts,1), p]`) — this is the canonical storage format
  `time_utils` expects. Calls `_on_cycle_end(cycle_data)` (manager).
- Chooses the terminal state: `INTERRUPTED`/`FORCE_STOPPED`, else `ANTI_WRINKLE`
  if `completed` + reason in `ANTI_WRINKLE_ELIGIBLE_REASONS ({TIMEOUT, SMART})` +
  anti-wrinkle enabled + dryer-family, else `FINISHED`. Then `reset(target)`.

### 1.13 Other public methods

- `update_match(result)` (:461) — processes the matcher tuple. Accepts 4/5/6/7
  element tuples: `(name, confidence, expected_duration, phase, [is_mismatch],
  [is_ambiguous], [is_prefix_ambiguous])`. Sanitizes confidence (non-finite→0) and
  expected_duration (via `_sanitize_expected_duration`). A confident mismatch
  clears `_matched_profile`; a match with a sanitized-invalid expected_duration is
  **ignored** (both cleared) so Smart Termination can't fire on the `>= 0`
  always-true comparison. Sets `_match_ambiguous`/`_match_prefix_ambiguous`.
- `_sanitize_expected_duration(raw, source)` (:417) — coerces to finite float in
  `(0, 6h]` else `0.0` sentinel. Distinct DEBUG markers (`<= 0`, `> 6h`) are part
  of issue #197's regression contract (tests assert on them).
- `_try_profile_match(timestamp, force)` (:362) — invokes `_profile_matcher`
  (rate-limited by `match_interval` unless forced). Has the **dishwasher
  terminal-tail match freeze** (:382): in ENDING, dishwasher, matched, and quiet
  ≥ `DISHWASHER_MATCH_FREEZE_QUIET_SECONDS (300)` → skip re-matching (re-matching
  on the growing idle tail inflates duration and drifts the label to a longer
  near-duplicate). Note the live wrapper returns `None` (async offload) and calls
  `update_match` later; only the manual-program path returns a synchronous tuple.
- `set_verified_pause(bool)` (:569), `reset(target_state=OFF)` (:573).
- `force_end(ts)` (:1787) → `force_stopped`/`FORCE_STOPPED`, clears lockout.
- `user_stop()` (:1798) → `completed`/`USER`, `keep_tail=True`, **arms the
  manual-stop lockout** and anchors `_last_process_time=now` (issue #267 — so the
  pre-stop gap isn't counted against the lockout release window).
- `get_power_trace()`, `get_state_snapshot()` (:1822), `get_elapsed_seconds()`,
  `is_waiting_low_power()`, `restore_state_snapshot(snapshot)` (:1865).
  `restore_state_snapshot` re-sanitizes `expected_duration` (dropping
  `matched_profile` if invalid), fixes naive timestamps to local tz (legacy data),
  and drops non-finite/malformed readings.

### 1.14 Detection-core gotchas / subtleties

- `_abrupt_drop` is initialized False, reset to False on start, read in
  `_finish_cycle`'s interrupted logic — but **never set to True anywhere** in the
  current file. The `abrupt_drop_*` config fields are effectively dead in the
  detector (dead code / vestigial).
- `power_off_threshold_w` / `power_off_delay` live on the config but are acted on
  by the **manager** (issue #284), not the detector.
- The `max_reasonable = 21600.0` (6h) in the ENDING high-arm (:1081) duplicates
  `_SANITIZE_MAX_EXPECTED_DURATION` (also 6h) as a local literal.
- `_verified_pause` is dual-owned (envelope auto-pause vs. user pause) — see MEMORY
  `project_verified_pause_dual_ownership`; it is cleared on `reset()` (B6 fix) so a
  stale True can't leak into the next cycle, and it is **not** in the state
  snapshot (manager re-derives it on restore).
- Playground drives this exact class headless (`playground.py` `SimRunner`), so
  any behavior change here changes what-if replays too.

---

## 2. `signal_processing.py` — resampling + energy integration

NumPy-only. Constraints stated in the docstring: dt-aware, **segment-based
resampling (no interpolation across gaps)**.

### 2.1 `Segment` (dataclass, :30)
`timestamps` (uniform grid, seconds), `power` (interpolated W), `mask` (bool;
True=valid). Reserved for future multi-channel use.

### 2.2 `energy_gap_threshold_s(timestamps) -> float` (:47)
Data-driven outage-gap threshold = `clip(10 × median(positive intervals), 60,
3600)`. `< 2` samples → `3600.0`. Single source used by both persistence paths and
`features.compute_signature`. Segments longer than this are treated as sensor
outages and excluded from energy sums without penalizing legitimately slow sampling.

### 2.3 `integrate_wh(timestamps, power, *, max_gap_s=None) -> float` (:64)
Trapezoidal energy in Wh. `< 2` samples → 0.0. `dt_hours = diff(ts)/3600`,
`avg_power = (p[:-1]+p[1:])*0.5`, energy = `Σ avg_power * dt_hours`.
When `max_gap_s` is set, a boolean mask keeps only segments with `0 < dt_hours <=
max_gap_s/3600` — this is the explicit gap handling for sensor outages and
non-positive/duplicate timestamps. `None` (default) integrates every segment
(original behavior). **This is the shared, single-source energy implementation**
mandated by CLAUDE.md; callers: `cycle_detector` (:1370 ENDING gate),
`features.compute_signature`, `manager._on_cycle_end`, `profile_store.add_cycle`
(:3219), envelope avg-energy (:3964), `phase_segmenter` (:260).

### 2.4 `resample_uniform(timestamps, power, dt_s=5.0, gap_s=60.0) -> List[Segment]` (:100)
Gap-aware uniform resampler. Splits at `diff > gap_s`
(`break_indices = where(diffs > gap_s)[0]+1`), builds `[start,end)` chunks, and for
each chunk with ≥2 points and span `>= dt_s`, builds `target_ts = arange(t0,
t_end+0.001, dt_s)` and `np.interp` power onto it. **Never interpolates across a
gap** — each gap becomes a separate Segment. Returns `[]` for `< 2` input points.

### 2.5 `resample_to_n(power: list[float], n) -> list[float]` (:165)
Timestamp-free resample to exactly `n` evenly-spaced points via `np.interp` over a
normalized `[0,1]` axis (assumes uniform original spacing). Edge cases documented
and intentional: input already length `n` → returned unchanged; `n < 1` or empty
input → `[]` (a "missing" marker, **not fabricated zeros**); single sample → `n`
copies of that value. Returns native Python floats (JSON-friendly). Callers:
`profile_store` cluster/shape comparisons (`CLUSTER_RESAMPLE_N`).

### 2.6 `resample_adaptive(timestamps, power, min_dt=5.0, gap_s=300.0) -> (segments, used_dt)` (:203)
Picks `target_dt = max(min_dt, median_interval)` ("never resample finer than the
sensor", never finer than 5s), widens `gap_s = max(gap_s, target_dt*1.5, 1e-3)`,
then delegates to `resample_uniform`. Guards against non-positive step/gap. `< 2`
inputs → `([], min_dt)`. Callers: `profile_store` envelope building (:4494).

---

## 3. `features.py` — compact cycle signature

NumPy-only. Purpose: a compact per-cycle summary used for fast-reject/matching and
persisted with cycles (Stage-1 of the matching pipeline consumes these).

### 3.1 `CycleSignature` (dataclass, :31)
Fields: `duration`, `total_energy`, `max_power`, `event_density`
(**deprecated/reserved — always 0.0**; the event detector was removed, field kept
for stored-signature back-compat), `time_to_first_high`, `high_phase_ratio`,
`p05/p25/p50/p75/p95` (power quantiles).

### 3.2 `compute_signature(timestamps, power) -> CycleSignature` (:51)
- Empty power → all-zero signature.
- `duration = ts[-1]-ts[0]`.
- `total_energy = integrate_wh(ts, power, max_gap_s=energy_gap_threshold_s(ts))` —
  the shared energy path (gap-aware).
- Quantiles via `np.percentile(power, [5,25,50,75,95])`.
- **High-phase heuristic:** `thresh_high = max(800.0, 0.8*max_power)`.
  `time_to_first_high` = seconds to the first sample above `thresh_high` (else
  `duration`). `high_phase_ratio` = high-time / duration, where high-time sums
  `capped_dt[high_mask[:-1]]` and `capped_dt = where(dt > energy_gap_threshold_s,
  0, dt)` — the **same gap-capping as the energy integrator**, so a long outage
  after a high sample isn't miscounted as high-phase time. `mask[i]` aligns to
  interval `i` (uses `high_mask[:-1]`).
- All fields cast to `float`. The 800W floor is tuned to heater-class appliances;
  low-power devices will rely on the `0.8*max` arm.

Callers: `profile_store` (add_cycle :3208, reference/repair paths, matching setup).

---

## 4. `time_utils.py` — power_data format normalization

Purpose: the single place that recognizes and converts the three in-flight
`power_data` formats. Canonical storage form is `[[offset_seconds, power], ...]`
(offset is a float relative to the cycle's `start_time`). Consumers
(`progress.py`, `suggestion_engine.py`, `manager.py`, `profile_store.py`) never
guess a format — they route through these helpers.

### 4.1 `detect_power_data_format(power_data)` (:46)
Returns `"empty" | "unknown" | "datetime" | "iso" | "offset" | "unix_timestamp"`.
Inspects the first sample with a non-None timestamp. Numeric timestamps `> 1e8`
(≈3+ years of seconds) are classified `unix_timestamp` (absolute epoch) vs.
`offset` (relative) — a subtle but important disambiguation.

### 4.2 `power_data_to_offsets(power_data, start_time_iso=None) -> list[[offset, power]]` (:76)
Normalizes any format to canonical offsets. Per-format:
- `unix_timestamp`: subtract the parsed `start_time_iso` (or first reading as
  anchor), `max(0.0, offset)`.
- `offset`: pass-through, coerced to `[float, float]`.
- `datetime`: subtract `start_time_iso` timestamp (or first sample).
- `iso`: requires parseable `start_time_iso`; otherwise falls back to the first
  reading as zero reference with a WARNING, and clamps negative offsets to 0 (DEBUG).
All arms swallow malformed samples (`continue`). Returns `[]` on failure/unknown.

### 4.3 `power_data_offsets_to_datetimes(power_data, start_time_iso) -> [(datetime, power)]` (:209)
Inverse of the offset conversion: `datetime.fromtimestamp(start_ts + offset,
tz=start_dt.tzinfo)`. Preserves the start timestamp's tzinfo. Returns `[]` if
`start_time_iso` unparseable. This is how stored offset traces are rehydrated into
the `(datetime, power)` form the detector/matcher operate on.

### 4.4 `migrate_power_data_to_offsets(cycle) -> bool` (:242)
In-place, idempotent migration of a single cycle dict's `power_data` to offset
form. No-ops on `offset`/`empty`; skips `unknown` with a warning. For `iso`
requires a parseable `start_time`. Returns True iff the cycle was modified.

### 4.5 Notes
- All datetime handling uses `homeassistant.util.dt` (tz-aware), consistent with
  the CLAUDE.md rule. Naive timestamps are only ever fixed defensively (in the
  detector's `restore_state_snapshot`, not here).
- `PowerPoint`/`PowerData` type aliases document the tolerant list/tuple shapes.

---

## 5. Cross-module map (who calls what)

- **`cycle_detector.CycleDetector`** is constructed by `manager.py` (:783) with:
  `_on_state_change`, `_on_cycle_end`, `profile_matcher=profile_matcher_wrapper`
  (async-offload; returns `None` and later calls `update_match`), and the two
  opt-in providers `_ml_end_confidence` / `_terminal_drop_provider`. Also
  constructed headless by `playground.py` (SimRunner) and `ws_api.py`
  (Playground base config).
- **`update_match` tuple** originates from `manager`'s combined-matching path,
  which reads `MatchResult.is_ambiguous` / `.is_prefix_ambiguous` /
  `.is_confident_mismatch` (manager.py ~1275). The 6th/7th tuple elements gate
  Smart Termination.
- **`integrate_wh` / `energy_gap_threshold_s`** are the shared energy primitives:
  cycle_detector (ENDING gate), features, manager (`_on_cycle_end`),
  profile_store (add_cycle, envelope), phase_segmenter.
- **`compute_signature`** is used only by `profile_store` (Stage-1 fast reject +
  reference/repair).
- **`resample_uniform`/`resample_adaptive`/`resample_to_n`** are used by
  `profile_store` (envelopes, cluster shape comparison) and internally.
- **`time_utils`** helpers are used by `progress`, `suggestion_engine`, `manager`,
  `profile_store` to rehydrate/normalize traces.
- **DTW lives in `analysis.py`** (not signal_processing) and is orchestrated by
  `profile_store` Stage-3; `playground.dtw_debug_payload` / `ws_get_dtw_debug`
  expose it in the panel.

---

## 6. Most important discrepancies vs CLAUDE.md

1. **`signal_processing.py` does NOT contain DTW or filtering** (CLAUDE.md and the
   scope both claim it does). DTW = `analysis.py`; there is no filtering function
   anywhere in signal_processing.py. The only smoothing is the display-only
   `_ma_buffer` in the detector.
2. **The running energy accumulator uses left-rectangle `power*dt/3600`, not the
   shared `integrate_wh`** — a deliberate divergence, acceptable because it only
   gates STARTING→RUNNING, but worth documenting given the "single shared energy
   implementation" rule (which does hold for *persisted* energy).
3. **`_abrupt_drop` is dead** — read in `_finish_cycle` but never set True; the
   `abrupt_drop_*` config fields are effectively vestigial in the detector.
4. CLAUDE.md's ML end-guard blurb cites `ML_END_GUARD_MAX_DEFER_SECONDS` and
   `DEFAULT_DEFER_FINISH_CONFIDENCE` correctly, but note the guard's
   `ML_END_GUARD_MIN_CONFIDENCE (0.5)` and throttle live *in cycle_detector.py*,
   not const.py.
