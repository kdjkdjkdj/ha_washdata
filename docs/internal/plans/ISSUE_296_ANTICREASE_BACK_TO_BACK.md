# Plan: Issue #296 follow-up - back-to-back anti-crease cycles merge

Status: IN PROGRESS (branch `fix/296-anticrease-back-to-back`, off `0.5.1`)
Owner: (maintainer)
Created: 2026-07-21

## 1. Problem

The #296 feature (anti-wrinkle enabled for `washing_machine`) shipped in 0.5.0.
TRON4R reports a regression on 0.5.1 with a real Miele "Knitterschutz" export: when
the door is not opened between two loads, WashData no longer detects the cycle end and
merges everything into one ~6 h "cycle" (wash -> anti-crease -> wash -> anti-crease).

Real export analysed: `cycle_data/tron4r/washing_machine/ha_washdata_export_01KWFX8C3HVEK7YK6F9N6KAVVS.json`,
cycle index 19 (206 min, back-to-back merge):

| Segment | Time | Signature |
|---|---|---|
| Wash #1 | 0-85 min | heating peaks ~2313 W, spin bursts to 868 W |
| Anti-crease tail #1 | 85-120 min | ~3.2 W baseline + tumble bursts, max 94 W, recurring ~every 34 s |
| Wash #2 | 120-175 min | heating peak 2279 W |
| Anti-crease tail #2 | 175-206 min | ~3.2 W baseline + bursts, max 259 W, until door opened |

User settings relevant here: `anti_wrinkle_enabled=true`, `anti_wrinkle_max_power=400`,
`anti_wrinkle_exit_power=0.8`, `off_delay=150`, `min_off_gap=480`, `stop_threshold_w=5`.

## 2. Root cause (verified by replaying cycle 19 through the real CycleDetector)

- **Unmatched replay -> 1 merged 203-min cycle** (reproduces the bug). Anti-crease bursts
  recur every ~34 s, well inside `off_delay` (150 s), so the cycle rarely reaches `ENDING`;
  when it does, a not-`past_expected` burst revives it to `RUNNING`. `STATE_ANTI_WRINKLE`
  (which handles the tail well) can only be entered AFTER a formal completion, which never
  comes -> a second wash started before the door opens is absorbed.
- **Replay with a confident match held -> 2 clean cycles** (`smart` termination, correct
  anti-wrinkle split at the 2279 W heating burst). The DOWNSTREAM anti-wrinkle machinery is
  sound. The merge happens because live re-matching on the growing burst tail degrades /
  drifts the match (lower confidence, ambiguity, or drift to a longer near-duplicate), which
  breaks the Smart-Termination gate (`is_confident_match and not _match_ambiguous`). Without
  smart termination the only exit is the power-based fallback (`max(off_delay, min_off_gap)`
  continuous quiet), which the periodic bursts defeat.
- **0.5.1's own #296 backstops do not cover this pattern.** `_is_standby_band_stuck` requires
  a FLAT plateau (`hi-lo <= 3% of peak`); `ENDING_HARD_FINALIZE` requires 600 s CONTINUOUS
  sub-threshold quiet. Baseline + bursts provides neither. Same failure mode the dishwasher
  path solves via `DISHWASHER_MATCH_FREEZE_QUIET_SECONDS`, but that freeze also keys on
  continuous quiet, which bursts defeat.
- **Secondary (TRON4R point #3), confirmed.** The anti-crease tail is included in cycle stats,
  so the suggestion engine derives poison: `stop_threshold_w=2.48`, `start_threshold_w=3.26`
  (both below the 3.2 W baseline -> ghost starts), `off_delay=3576 s`, `min_off_gap=3775 s`
  (35-min tail read as one intra-cycle pause). With 0.5.1's reconcile change
  (`min_off_gap >= off_delay`), applying these would make the cycle unable to end.

Replay harnesses used: `scratchpad/replay19.py` (unmatched) and `replay19_matched.py`
(injected confident match). To be turned into a slow pytest fixture (see Part 4).

## 2a. Refinement after implementation (important)

The initial plan framed the reliable discriminator as "no high-power reading in a
window".  Empirically that is WRONG on its own: a washer spends most of its cycle
below `anti_wrinkle_max_power` (only brief heating spikes exceed it), and clean
cycles have mid-wash sub-max_power gaps up to ~62 min.  The reliable discriminator
is being **past the matched expected duration** - every clean cycle's high-power
activity ends before its expected duration, while the anti-crease tail begins after
it (verified across all 19 export cycles).  So the finalize is gated on
`elapsed >= expected * ratio` FIRST; the low-power window is a secondary safety
(clear of the final spin).  This also means the fix is **matched-only** - a truly
unmatched anti-crease cycle is left to existing behaviour (acceptable: the user has
profiles, and an unmatched finalize on a washer is too risky given the 62-min
mid-wash gaps).

## 3. Fix (Approach A - chosen)

Asymmetric (finalize-only), opt-in via existing `anti_wrinkle_enabled`, device-gated to
washing machines (and by extension the existing `STANDBY_BAND_FINALIZE_DEVICE_TYPES`).

### Part 1 - Reliable finalize into anti-wrinkle for the burst tail
Add an "anti-crease regime" recognizer that runs while `RUNNING`/`PAUSED`/`ENDING`. Confirms
ALL of:
- (a) the cycle was genuinely energetic: `cycle_max_power` >> `anti_wrinkle_max_power`
      (a real wash happened, not a low-power appliance);
- (b) the recent sustained window contains NO reading above `anti_wrinkle_max_power`
      (only baseline + sub-max bursts, i.e. no heating / high spin);
- (c) if a match exists, elapsed >= `expected * ratio` (guards against finalizing mid-wash);
- (d) not user-paused.

On confirmation: freeze re-matching and `_finish_cycle(completed, SMART, keep_tail=True)`
-> `STATE_ANTI_WRINKLE`. The existing anti-wrinkle logic then absorbs the tail and splits
the next wash on its >`anti_wrinkle_max_power` heating burst. Robust because it does NOT
require continuous sub-`stop_threshold` quiet.

### Part 2 - Match-freeze on the burst tail
Generalize the dishwasher terminal-tail freeze (`_try_profile_match` guard) to washing
machines, keyed on the anti-crease regime (no >`anti_wrinkle_max_power` reading for a window)
rather than only continuous quiet. Preserves the good pre-tail `expected_duration`/label so
the existing `past_expected` Smart Termination can also finalize.

### Part 3 - Suggestion-engine tail exclusion (secondary) - INVESTIGATED & DECLINED

Investigated in depth (2026-07-21) and **declined** (maintainer decision: skip heuristic
changes).  The premise - that the anti-crease TAIL poisons the suggestions - is empirically
FALSE; tail-exclusion would be ineffective and potentially harmful.  Evidence (tron4r export,
via the real `suggestion_engine` functions):

- **Threshold poison** (`stop` suggested at 2.46 W, below the ~3 W standby -> ghost starts):
  the ~3.1-3.2 W floor is the machine's standby/electronics level, and it appears **mid-wash
  as often as in the tail** (the drum dips to it between pump strokes throughout the cycle).
  Per-cycle `min(active)` ≈ 3.1 W with or without the tail.  No clean percentile fix: p05/p10
  of active ≈ 3.3-3.4 W (still poisoned), p25 jumps to 16.5 W (-> stop 13 W, would cut genuine
  low phases).  Also fights the documented `stop < lowest-active` design intent.
- **off_delay poison** (~2000-3576 s): **19 of 20 of the biggest "pauses" are genuine mid-wash
  soak phases**, not tail artifacts (e.g. a real ~40-min low-power soak before the final spin).
  Trimming the tail wouldn't reduce them, and being aggressive here risks breaking the
  dishwasher drying-pause bridging that `find_intra_cycle_pauses` was tuned for.

So the poison is standby-bleed + genuine soaks, not the tail.  The proper fixes (robust
active-power floor; continuous-gap off_delay) are deeper, cross-device, tuned-heuristic changes
against documented intent - out of scope for this fix and requiring separate maintainer
calibration + full cross-device validation.  Mitigation instead comes for free from Parts 1+2:
cycles recorded AFTER the fix finalize at the expected duration with the tail routed into
anti-wrinkle (not baked into the recorded cycle), so new data is progressively cleaner; and the
suggestions were never auto-applied.

### Docs
Note that effective anti-wrinkle exit = `max(anti_wrinkle_exit_power, stop_threshold_w)`,
and that `stop_threshold_w` must sit below the anti-crease baseline for anti-wrinkle to persist.

## 4. Validation
- Vendor cycle 19 (already in `cycle_data/tron4r/...`) as a slow replay test fixture, in the
  style of `tests/test_issue_68_anti_wrinkle.py`: assert pre-fix merge (1 cycle) vs post-fix
  2 cycles + anti-wrinkle.
- Regression guard on clean cycles 0-18: the "energetic wash happened" + "no >max_power
  reading in window" gate protects real mid-cycle soak troughs (spins at 50-77 min hit 868 W
  > 400 W, keeping the regime "active").
- `./run_tests.sh` (fast) + `./run_tests.sh --slow`. Matcher untouched, so `dtw_ab_eval`
  top-1 is unaffected.
- Update `docs/internal/INTEGRATION_REFERENCE.md` §7 register + CHANGELOG entry.

## 5. Open maintainer decisions
1. New `TerminationReason.ANTI_CREASE` vs reuse `SMART` (must be in `ANTI_WRINKLE_ELIGIBLE_REASONS`).
   Current implementation choice: reuse `SMART` (no schema/migration churn); revisit if we want
   the reason visible in the UI/label.
2. Bundle Part 3 (suggestions) in this PR or split it out.
3. Reopen #296 vs a focused follow-up issue (currently labeled `done`).

## 6. Progress log
- 2026-07-21: root cause verified (replays), plan written, branch created.
- 2026-07-21: Parts 1+2 implemented + validated (TDD).
  - `const.py`: `ANTI_CREASE_FINALIZE_RATIO` (0.98), `ANTI_CREASE_CONFIRM_WINDOW_S` (180 s).
  - `cycle_detector.py`: `_anticrease_gate_open` / `_in_anticrease_freeze` / `_is_anticrease_tail`
    / `_maybe_finalize_anticrease_tail`; wired into RUNNING/PAUSED/ENDING; freeze at the
    `update_match` sink (covers the manager's direct-update path) + `_try_profile_match`.
  - Tests: `tests/test_issue_296_anticrease_back_to_back.py` (4 + regression guard).
    Real export replay: 6 h merge -> 2 clean ~86-min cycles.
  - Refinement 2a discovered during regression testing (matched-only, past-expected gate).
  - Docs: register item 54, module line count, CHANGELOG 0.5.2 entry.
  - Suites: fast 1269✓; slow 61 passed / 66 skipped / 0 failed (incl. the 5 anti-crease
    tests, `test_real_data_suggestions`, `test_smart_termination`, `test_verify_alignment` -
    no detection regressions).  Matcher untouched, so `dtw_ab_eval` top-1 is unaffected (the
    freeze changes WHEN matching runs, not HOW candidates are scored).
- 2026-07-21: Part 3 investigated + DECLINED (maintainer: skip heuristic changes). Empirically
  the tail is NOT the poison source (standby-bleed + genuine mid-wash soaks); see §3 Part 3.
  Cross-device retest: all 161 cycle_data cycles (104 dishwasher / 57 washer, anti-crease
  force-enabled everywhere) replayed through the real detector -> only the 2 intended tron4r
  splits (cycle 12 wash+tail, cycle 19 back-to-back), zero dishwasher/other-washer splits, zero
  exceptions.  Added a dishwasher-exclusion guard test (6 tests total in the file).
- TODO before PR: maintainer decisions in §5 (reason enum, reopen #296 vs follow-up).
