# WashData ML Subsystem — Exhaustive Technical Reference

Scope: `custom_components/ha_washdata/ml/` (engine, feature extraction, trainer,
training orchestration, matching tuner, three embedded models, manifest/contracts/
parity), plus every gated runtime consumer wired in `manager.py` /
`cycle_detector.py` / `progress.py`, all ML constants, promotion gates, the parity
test contract, and the revert paths. All paths are absolute-relative to
`/root/ha_washdata/custom_components/ha_washdata/` unless noted.

Design invariants (verified across all files):
- **NumPy-only** everywhere. No sklearn/torch/scipy at runtime. All training +
  scoring is pure NumPy.
- **Never raises into live inference.** Every scorer/regressor/consumer wraps in
  try/except and returns a neutral value (`0.0`, `None`, `nan`, or `False`) on any
  error, so ML can never break detection/matching/ETA.
- **Opt-in + gated.** Runtime consumers all check `ml_models_enabled(options)`
  (per-device `enable_ml_models`, default off). Training is behind
  `ENABLE_ML_TRAINING`. Shadow/suggestion paths (`ws_api._compute_ml_comparison`,
  `MLSuggestionEngine`) go through `resolve_scorer` directly and are NOT gated on
  `enable_ml_models`.
- **On-device never touches shipped baselines.** Embedded `*_model.py` blobs are
  regenerated offline only. On-device training writes JSON specs into the profile
  store key `ml_model_versions`; the matcher override into `matching_config`.

---

## 1. Engine — the single inference bridge (`ml/engine.py`, 216 lines)

`engine.py:46` `CONF_ENABLE_ML_MODELS = "enable_ml_models"`.

`ml_models_enabled(options)` (`engine.py:56`): returns `bool(options.get("enable_ml_models", False))`; `None`/empty → False.

`_MODEL_MODULES` (`engine.py:49`): capability → embedded module name (classifiers only):
- `"quality"` → `hybrid_curve_quality_model`
- `"live_match"` → `live_match_commit_model`
- `"end"` → `cycle_end_detector_model`

### `resolve_scorer(capability, store)` → `(score_fn, source)` (`engine.py:63`)
Returns a **classifier** scorer mapping `feats → P∈[0,1]`, and `source ∈ {"on_device","baseline"}`, or `(None, None)`.
1. If `store` has `ml_model_versions[capability].spec` that is a dict with
   `kind != "standardized_linear"` → wraps `trainer.score_spec` as `_on_device_score`
   (source `"on_device"`). The `kind != standardized_linear` guard (`engine.py:118`)
   prevents a regression spec ever being sigmoid-squashed. On call-time error the
   on-device scorer falls back to the embedded baseline, then to `0.0` (`engine.py:132-138`).
2. Otherwise `_baseline()` lazily imports the embedded module and wraps
   `module.score()`; on error logs + returns neutral `0.0` (`engine.py:92-104`).
- Unknown capability → `(None, None)` (`engine.py:81`).

### `resolve_regressor(capability, store)` → `(predict_fn, source)` (`engine.py:150`)
Regression twin for `"remaining_time"` / `"total_energy"`. **No shipped baseline** —
returns `(None, None)` unless the store holds a spec with `kind == "standardized_linear"`
(`engine.py:167`), which it wraps as `trainer.predict_value_spec`. On call-time error
returns `float("nan")` (isfinite-guarded consumers treat the capability as inert)
(`engine.py:182`). So both regressors are byte-identical-to-baseline inert until
on-device training promotes one.

### `available_models()` (`engine.py:196`)
Parses `promoted_manifest.json` once (module-cached); returns `[]` if missing/invalid.
Used only for provenance display.

---

## 2. Embedded baseline models (three `*_model.py`)

All three are `standardized_logistic` (mean/std scaler + weight vector + bias +
decision threshold), stored as a gzip+base64 JSON blob decoded lazily by `_load()`
(cached in `_MODEL_CACHE`). Each exposes `MODEL_NAME`, `MODEL_TARGET`, `MODEL_KIND`,
`TARGET_UNITS`, `THRESHOLD`, `FEATURE_COLUMNS`, `MODEL_METRICS`, `score()`, `predict()`.

**Scoring math (byte-identical in all three, `*_model.py:114-124`):**
```
vector = [features.get(col) or 0.0 for col in FEATURE_COLUMNS]   # missing → 0.0
scaled = (vector - center) / scale
logit  = float(scaled @ coef + bias)
logit  = max(-60.0, min(60.0, logit))          # overflow clamp
return 1.0 / (1.0 + np.exp(-logit))            # sigmoid
```
`predict()` = `score() >= THRESHOLD`.

| model | MODEL_NAME | target | THRESHOLD | #cols | file |
|---|---|---|---|---|---|
| cycle_end_detector | `cycle_end_detector` | `cycle_truly_ended` | **0.6** | 8 | `cycle_end_detector_model.py` |
| hybrid_curve_quality | `hybrid_curve_quality` | `problem_cycle` | **0.19** | 31 | `hybrid_curve_quality_model.py` |
| live_match_commit | `live_match_commit` | `match_top1_correct` | **0.371786** | 8 | `live_match_commit_model.py` |

Provenance / determinism (from module docstrings + `MODEL_METRICS`):
- end: det-check `max_abs_score_diff=7.994e-09` over 1168 rows; owner_holdout
  balanced_accuracy 0.857, precision 0.695, recall 0.839; `premature_stop_rate 0.125`.
- quality: `6.8331e-08` over 373 rows; owner_holdout bal_acc 0.900, precision 0.986,
  recall 0.826; (`synthetic_all` row is a degenerate all-positive sanity block).
- live_match: `1.6467e-08` over 2392 rows; owner_holdout bal_acc 0.742, precision
  0.866, recall 0.810.

`promoted_manifest.json`: `generated_at 2026-07-01`, all three from `git_commit 605a862`,
`source: ml_washdata/output/promoted`. **No regression model is shipped** (manifest
lists only the 3 classifiers).

---

## 3. Feature extraction (`ml/feature_extraction.py`, 807 lines)

Pure NumPy, no HA dependency. Each extractor produces exactly the model's
`FEATURE_COLUMNS`. The test suite asserts each list equals the embedded model's
`FEATURE_COLUMNS` so they cannot drift.

### 3.1 End detector — `END_FEATURE_COLUMNS` (8) (`feature_extraction.py:51`)
`elapsed_fraction, energy_fraction, energy_remaining_expected, power_before_ratio,
drop_ratio, peak_seen_ratio, low_run_s_log, elapsed_log`.
- `latest_end_event_features(points, expectation, min_low_run_s=45.0)`
  (`feature_extraction.py:143`): finds the most recent contiguous low-power run
  (`low_threshold = max(5.0, 0.02*peak)`), requires run ≥ `MIN_LOW_RUN_S` (45s) else
  returns `None`. Computes ratios vs profile median duration/energy/peak, `log1p`
  transforms. `None` = "no qualifying end event yet, keep existing behavior."
- `cumulative_energy_wh(points)` (`feature_extraction.py:67`): trapezoidal Wh with
  gap zeroing via `signal_processing.energy_gap_threshold_s` (matches stored
  `energy_wh`).
- `profile_expectation(cycles_points)` / `profile_expectations(cycles)`
  (`feature_extraction.py:87/111`): median duration/energy/peak. The dict version
  reads stored scalar fields; missing energy/peak default to **500.0**.

### 3.2 Live-match commit — `LIVE_MATCH_FEATURE_COLUMNS` (8) (`feature_extraction.py:204`)
`match_progress_top1, top1_distance, margin, distance_ratio, candidate_count_log,
prefix_active_fraction, duration_ratio_top1, elapsed_log`.
- `live_match_features(points, elapsed_s, top1_distance, top2_distance,
  top1_median_duration_s, candidate_count)` (`feature_extraction.py:216`): `top2`
  defaults to `top1+1.0` when absent (→ margin 1.0, distance_ratio behaviour);
  `prefix_active_fraction` = fraction of prefix above `max(1.0, 0.05*peak)`.

### 3.3 Progress / energy regressors — `PROGRESS_FEATURE_COLUMNS` (7) (`feature_extraction.py:282`)
`elapsed_over_expected, energy_over_expected, mean_power_over_peak,
recent_power_over_peak, tail_slope_norm, active_fraction, elapsed_log`.
- `elapsed_over_expected` is deliberately **column 0** so the naive baseline
  (elapsed/expected clamped to [0,1]) is trivially recoverable at gate time.
- `progress_features(points, expectation)` (`feature_extraction.py:293`): returns
  `None` if `< 4` clean points. Shared by both `remaining_time` and `total_energy`
  at training AND inference (columns can't drift). `tail_slope_norm` is the OLS
  slope of the last quarter normalized by peak (declining tail = near-end signal).

### 3.4 Hybrid quality — `QUALITY_FEATURE_COLUMNS` (31) (`feature_extraction.py:354`)
18 profile/context + 12 shape descriptors + `has_trace`. Context group:
`duration_log_ratio, energy_log_ratio, peak_log_ratio, profile_distance,
label_margin_positive, max_gap_ratio, low_power_gap_ratio, false_end_energy_ratio,
sample_density_log, peak_density_log, local_spike_score, local_spike_rate,
local_noise_score, leading_idle_ratio, trailing_idle_ratio,
trimmed_duration_log_ratio, flag_pressure, shape_fit_penalty`. Shape group
(`_SHAPE_COLUMNS`, `feature_extraction.py:395`): `shape_active_fraction,
shape_early_energy_fraction, shape_late_energy_fraction, shape_mid_trough_depth,
shape_peak_density, shape_max_step_drop, shape_max_step_rise, shape_active_cv,
shape_autocorr_lag1, shape_derivative_sign_changes, shape_plateau_ratio,
shape_tail_slope`. Plus `has_trace`.
- `quality_features(...)` (`feature_extraction.py:411`): trace resampled to
  `_QUALITY_TRACE_LENGTH=128` (`feature_extraction.py:391`), idle threshold 2.0W,
  stop threshold 2.0W. If `< 4` clean points → `_no_trace_quality_features`
  (all zero shape, `has_trace=0.0`).
- Deterministic helpers ported from the lab: `_trace_noise_features`,
  `_trace_shape_descriptors`, `_resample_to_length`, `_shape_peak_density`,
  `_autocorr_lag1`, `_plateau_ratio`, `_longest_low_power_gap_s`,
  `_false_end_energy_wh`, `_power_peak_count_arr` (`feature_extraction.py:566-807`).

---

## 4. Trainer (`ml/trainer.py`, 440 lines)

`PROMOTION_SCHEMA = "washdata.promoted_model/1"` (`trainer.py:38`) — matches the lab
so a trained spec is interchangeable with shipped bundles.

### 4.1 Logistic classifier training
- `fit_logistic(matrix, labels, l2=0.01, learning_rate=0.2, iterations=4000)`
  (`trainer.py:46`): mean/std standardisation (`scale<=1e-8 → 1.0`), inverse-frequency
  class weights, fixed-step GD. Raises `ValueError` if `< 2` classes present.
  Returns `{center, scale, coef, bias}`.
- `binary_metrics(labels, scores, threshold)` (`trainer.py:99`): confusion-matrix
  metrics; key names mirror shipped `MODEL_METRICS` (`problem_recall`,
  `positive_rate`, `balanced_accuracy`, …).
- `auc(labels, scores)` (`trainer.py:133`): rank-based Mann-Whitney U with tie-rank
  averaging (`_assign_tie_ranks`); 0.5 if a class is absent.
- `select_threshold(labels, scores, default=0.5)` (`trainer.py:173`): picks the
  candidate maximising balanced accuracy; ties break toward `default`.
- `build_spec(...)` (`trainer.py:215`): assembles a `standardized_logistic` spec
  (rounds to 8 dp; `output_center=0.0`, `output_scale=1.0`, `source="on_device"`).
- `score_matrix_spec` / `score_spec` (`trainer.py:253/265`): `sigmoid(((x-center)/scale)@coef + bias)`.

**Byte-identical-to-embedded caveat (`trainer.py:265-283`):** for a *complete*
feature mapping, `score_spec` == embedded `score()`. They differ only on the
missing-key fallback: `score_spec` fills a missing feature with the training
`center` (standardises to 0.0 = neutral), whereas embedded `score()` fills raw
`0.0` (potential 8+ SD corruption). Extractors always populate every column, so this
path is not exercised and not reachable in practice.

### 4.2 Ridge regressor training (`standardized_linear`)
- `fit_ridge(matrix, labels, alpha=1.0)` (`trainer.py:299`): standardises features AND
  target; solves closed-form normal equations `(ZᵀZ + αI) w = Zᵀ y_std` (falls back to
  `lstsq` on singular). Standardised intercept is 0. Raises `ValueError` if the target
  is (near-)constant. Returns `{center, scale, coef, bias=0.0, y_center, y_scale}`.
- `regression_metrics(labels, predictions)` (`trainer.py:354`): MAE / RMSE / R².
- `predict_matrix_spec` / `predict_value_spec` (`trainer.py:374/388`):
  `y = (((x-center)/scale)@coef + bias) * output_scale + output_center` — un-standardises
  via the spec's `output_center`/`output_scale`. **No sigmoid.** Missing keys filled with
  center (neutral), same as classifier.
- `build_regression_spec(...)` (`trainer.py:404`): `kind="standardized_linear"`,
  `threshold=0.0` (retained only for uniform shape), `output_center=y_center`,
  `output_scale=y_scale`.

---

## 5. On-device training orchestration (`ml/training_task.py`, 814 lines)

Gated behind `ENABLE_ML_TRAINING`; entry point `async_run_training(hass, manager)`
(`training_task.py:754`) offloads the pure `train_from_cycles(...)` (`training_task.py:682`)
to an executor and persists winners via `store.set_ml_model_version(cap, record)`.

`_CAPABILITIES` (`training_task.py:61`): classifier cap → (embedded module, target label):
`end→cycle_truly_ended`, `quality→problem_cycle`, `live_match→match_top1_correct`.
`_REGRESSION_CAPABILITIES` (`training_task.py:80`): `remaining_time→(progress_fraction,
fraction)`, `total_energy→(energy_fraction, fraction)`.
`_OPERATING_THRESHOLD` (`training_task.py:71`): the FIXED live cutoff each classifier
is judged at by the calibration gate — `end→DEFAULT_DEFER_FINISH_CONFIDENCE (0.55)`,
`quality→ML_QUALITY_SUSPICIOUS_THRESHOLD (0.65)`, `live_match→ML_MATCH_COMMIT_THRESHOLD (0.85)`.

### Label derivation (no manual labelling required to start)
- **end** — `_end_dataset` (`training_task.py:109`): from trace geometry. Positive =
  each completed clean cycle's final end event; negatives = earlier low-power runs
  (≥30s) that *resumed*. `active_thr = max(stop_thr, 0.02*peak)`.
- **quality** — `_quality_dataset` (`training_task.py:177`) uses ALL cycles (not
  clean-filtered) so mis-detects are positives. `_quality_label` (`training_task.py:158`):
  `ml_review.golden`/`good`/`golden` → 0; `bad`/`unusable` → 1; status
  `force_stopped`/`interrupted` → 1; `completed` → 0; else `None` (skip). `flag_count`
  uses real `artifacts` length so `flag_pressure` isn't train-time-constant.
- **live_match** — `_live_match_dataset` (`training_task.py:225`): from
  match-ranking-history snapshots; label `1.0` iff snapshot `top1_profile ==
  confirmed_label` (back-filled at cycle end). Snapshots without a confirmed label skipped.
- **remaining_time** — `_progress_dataset` (`training_task.py:278`): each clean
  completed cycle cut at `_PROGRESS_CUT_FRACTIONS = (0.15,0.30,0.45,0.60,0.75,0.90)`
  (`training_task.py:86`); target = `prefix_elapsed / total`. Requires total > 60s.
- **total_energy** — `_energy_dataset` (`training_task.py:325`): same cuts/features;
  target = `energy_so_far / total_energy` (learns non-linear energy accumulation).

### Group-aware holdout (prevents same-cycle leakage, "B5")
Every dataset returns a per-row `groups` array (source-cycle index / cycle_id).
`_group_holdout_indices` (`training_task.py:379`) assigns whole groups to train/test.
`_holdout_split` (`training_task.py:512`) retries up to 8 seeds to keep both classes on
each side; `_regression_split` (`training_task.py:404`) is the class-free twin. Both
fall back to **in-sample** eval when too few groups — and an in-sample eval **never
promotes** (`in_sample` flag, `training_task.py:453/590`).

### Promotion gates
**Classifiers** — `_train_capability` (`training_task.py:571`). Data floor:
`n >= _MIN_ROWS (40)` AND `n_pos >= ML_TRAINING_MIN_POSITIVES (20)` AND `n_neg >= 5`.
Threshold seeded from `select_threshold` (default = embedded `THRESHOLD` via
`_baseline_threshold`). Promote iff ALL:
```
new_auc >= (baseline_auc - ML_TRAINING_AUC_MARGIN)      # 0.02  (rank quality)
AND calib_ok:  trained_op_bacc >= base_op_bacc - ML_TRAINING_BACC_MARGIN   # 0.02
               (balanced accuracy AT _OPERATING_THRESHOLD[cap])
AND not in_sample
```
`baseline_auc` is the embedded model's AUC on the same test rows; if the baseline
fails to load/score, promotion is refused (won't promote against a fabricated 0.5 bar,
`training_task.py:615-624`). The AUC margin is intentional slack allowing personalisation
to win at a tiny AUC cost; the calibration gate stops a differently-calibrated model
silently shifting decision rates at the fixed live cutoff.

**Regressors** — `_train_regression_capability` (`training_task.py:429`). Data floor:
`n >= ML_TRAINING_MIN_REGRESSION_ROWS (30)`. Naive baseline = `elapsed_over_expected`
(col 0) clamped to [0,1] — i.e. the current profile-duration projection. Promote iff:
```
model_mae <= naive_mae * (1.0 - ML_TRAINING_REGRESSION_MARGIN)   # 0.05 (5% lower MAE)
AND not in_sample
```
`cycle_count` in every record reports **distinct source cycles**
(`np.unique(groups).size`), not rows.

---

## 6. Matching tuner (`ml/matching_tuner.py`, 261 lines)

`tune_matching_config(cycles, min_cycles=25, min_targets=12, margin=0.03, seed=0)`
(`matching_tuner.py:157`): NumPy-only, executor-safe leave-one-out tuning of the
matcher's **bounded scoring weights** over the device's own labelled cycles. It can
never change structural behaviour — only shape/level/energy emphasis.

`OVERRIDE_KEYS` (`matching_tuner.py:129`): `corr_weight, duration_weight,
energy_weight, dtw_ensemble_w` (all in [0,1]).
`_BASE_CFG` (`matching_tuner.py:124`): `{min_duration_ratio: 0.10, max_duration_ratio: 1.5}`.
`_grid()` (`matching_tuner.py:132`): `cw∈{0.40,0.45,0.50,0.60} × dur_w∈{0.15,0.22} ×
en_w∈{0.15,0.22} × ew∈{0.55,0.70,0.85}` = **48** configs (duration/energy on
independent axes → asymmetric configs allowed).

Methodology (no selection/gating leakage): partition targets ONCE into search + holdout
pools; **select** best grid config by leave-one-out top-1 on the SEARCH pool
(`_top1` via `analysis.compute_matches_worker`, `matching_tuner.py:105`); **gate** the
fixed candidate on the HOLDOUT pool — must beat defaults by `margin` on a MAJORITY
(`min_wins=4` of `n_splits=5` reshuffled subsamples) AND on the holdout mean. Only then
`promoted=True` with `config` = override. Never raises for data reasons.

Wiring: `manager._tune_matching_config` (`manager.py:2675`) offloads it, and on promotion
persists a record to store key `matching_config` via `set_matching_config`
(`profile_store.py:1173`). `ProfileStore._matching_overrides()` (`profile_store.py:1183`)
merges only the 4 whitelisted keys (each clamped [0,1]) into the live matcher config
(`profile_store.py:4645, 4771`). Runs on every training pass, independent of model promotion.

---

## 7. The five gated runtime consumers

All read `ml_models_enabled(self.config_entry.options)` first and no-op when off.
Asymmetry guarantees are the safety spine of the subsystem.

### C1 — ML end-detection guard (anti-premature-stop; can only DEFER)
- Provider: `manager._ml_end_confidence(points, expected_duration)` (`manager.py:3644`)
  → `resolve_scorer("end")` + `latest_end_event_features`. Returns `P(this low-power
  event is the true end)` or `None`.
- Consumer: `cycle_detector._should_defer_finish` (`cycle_detector.py:1559-1582`).
  Gate: only when `self._last_match_confidence >= DEFAULT_DEFER_FINISH_CONFIDENCE
  (0.55)`. Defers when `confidence < ML_END_GUARD_MIN_CONFIDENCE (0.5)`, bounded by
  `ML_END_GUARD_MAX_DEFER_SECONDS (1800.0s / 30min)` from `_ml_defer_start_duration`.
  Once the model agrees (≥0.5) it stops deferring (`cycle_detector.py:1580-1582`).
- **Asymmetry:** can only *add* a bounded extra wait — never ends early. A wrong
  model can delay, never hang (hard cap + `DEFAULT_MAX_DEFERRAL_SECONDS` above it).
- Constants live in `cycle_detector.py:77-78` (NOT const.py): `ML_END_GUARD_MIN_CONFIDENCE
  = 0.5`, `ML_END_GUARD_MAX_DEFER_SECONDS = 1800.0`.

### C2 — ML early match commit (can only SPEED first commit)
- `manager._async_do_perform_matching` (`manager.py:1020-1078`): builds
  `live_match_features`, calls `resolve_scorer("live_match")` →
  `ml_commit_score`. `ml_early_commit = ml_commit_score >= ML_MATCH_COMMIT_THRESHOLD
  (0.85) and confidence >= 0.30` (`manager.py:1058`).
- Effect: in Case-1 "initial match from detecting…" (`manager.py:1074`) it substitutes
  for the persistence counter, committing the first match without waiting the
  persistence window. Cannot switch away a committed profile or end a cycle.
- The ranking snapshot at `manager.py:1044-1056` is recorded **unconditionally**
  (not ML-gated) so live_match training data accumulates before the user opts in.

### C3 — ML quality gate (can only downgrade auto-label → feedback request)
- `manager._compute_cycle_quality_score(cycle_data)` (`manager.py:3845`): at cycle end,
  `resolve_scorer("quality")` + `quality_features` (profile medians from stored
  cycles; match-confidence proxies when unavailable). Stores
  `cycle_data["ml_quality_score"]`. Offloaded to executor only when ML enabled
  (`manager.py:4198-4203`), BEFORE `async_add_cycle` so reference stats stay clean.
- Consumer: `learning._maybe_request_feedback` — when `score >=
  ML_QUALITY_SUSPICIOUS_THRESHOLD (0.65)`, a high-confidence auto-label is downgraded
  to a feedback request. Also a *second* trigger: `envelope_conformance < 0.40`.
- **Asymmetry:** only converts a confident auto-label into a confirm-request; never
  auto-relabels or discards.

### C4 — ML remaining-time (and energy) regressor blend (bounded 50% blend)
- `manager._ml_progress_percent(trace, program)` (`manager.py:3795`) →
  `progress.ml_progress_percent` (`progress.py:112`) → `resolve_regressor("remaining_time")`
  + `progress_features`; returns a completion fraction × 100, clamped to ≤ 99.
- Blended in `progress.compute_progress` at `ML_PROGRESS_BLEND_WEIGHT (0.5)`:
  phase branch `progress = (1-w)*phase + w*ml_pct` (`progress.py:478-480`); linear
  fallback (`progress.py:531-534`) — both BEFORE the EMA smoothing/monotonicity guards,
  so a bad model can never wholly override the phase estimator. Called at `manager.py:5694`.
- Sibling: `progress.ml_energy_total` (`progress.py:167`) → `resolve_regressor("total_energy")`
  → projects total kWh (floors fraction at 0.05, never below energy-so-far). Feeds the
  running cycle's `projected_energy_kwh` attribute.
- **Asymmetry / inertness:** both regressors have no shipped baseline → `resolve_regressor`
  returns `(None, None)` until on-device promotion → byte-identical behaviour to before.

### C5 — Terminal-drop fast finalize (pure statistics; can only SHORTEN the wait)
- Provider: `manager._terminal_drop_provider(points, expected_duration)`
  (`manager.py:3700`) → `profile_store.is_terminal_drop(...)` (`profile_store.py:494`),
  gated on two learned baselines from `manager._terminal_drop_baseline` (`manager.py:3742`;
  cached, refreshed off-loop via `terminal_drop_baseline` executor job):
  (a) `earliest_sustained_quiet_offset` (`profile_store.py:399`) — earliest offset any
  **completed** cycle legitimately went quiet (strict min = conservative; interrupted
  cycles excluded so they can't poison it); (b) `device_active_peak_range`
  (`profile_store.py:450`) — min/max peak across completed cycles.
- `is_terminal_drop` fires only if all hold: **clearly ON** (`peak >=
  TERMINAL_DROP_MIN_PEAK_RATIO(5.0) * stop_thr`), **familiar** (peak within historical
  range widened by `TERMINAL_DROP_PEAK_FAMILIAR_TOL(0.4)` — else possible new program →
  defer), **anomalous** (`drop_start < earliest_quiet * TERMINAL_DROP_EARLINESS_RATIO(0.8)`).
  Needs `TERMINAL_DROP_MIN_CLEAN_CYCLES(3)` completed cycles + `TERMINAL_DROP_MIN_QUIET_SPAN_S(60)`.
- Consumer: `cycle_detector._is_terminal_drop` → STATE_ENDING finalizes at
  `TERMINAL_DROP_OFF_DELAY_SECONDS(90)` instead of the full soak-bridging `min_off_gap`
  (up to 8min washers / 1h dishwashers); stamps `TerminationReason.TERMINAL_DROP`.
- **No trained model** — it is a per-device anomaly heuristic (like
  `compute_profile_health`). **Asymmetric, opposite of C1:** can only shorten the wait,
  only for anomalously-early drops on a familiar cycle.

---

## 8. Full ML constant inventory (with values)

### const.py — live decision thresholds
- `DEFAULT_DEFER_FINISH_CONFIDENCE = 0.55` (`const.py:267`) — match-confidence gate for C1 + calib operating threshold for `end`.
- `ML_MATCH_COMMIT_THRESHOLD = 0.85` (`const.py:272`) — C2 commit gate + `live_match` operating threshold.
- `ML_QUALITY_SUSPICIOUS_THRESHOLD = 0.65` (`const.py:277`) — C3 gate + `quality` operating threshold.
- `MATCH_RANKING_HISTORY_MAX = 500` (`const.py:283`) — retained live_match snapshots (~6–12 months).

### const.py — terminal drop (C5)
- `TERMINAL_DROP_OFF_DELAY_SECONDS = 90` (`const.py:331`)
- `TERMINAL_DROP_MIN_CLEAN_CYCLES = 3` (`const.py:332`)
- `TERMINAL_DROP_MIN_QUIET_SPAN_S = 60` (`const.py:333`)
- `TERMINAL_DROP_EARLINESS_RATIO = 0.8` (`const.py:334`)
- `TERMINAL_DROP_MIN_PEAK_RATIO = 5.0` (`const.py:335`)
- `TERMINAL_DROP_PEAK_FAMILIAR_TOL = 0.4` (`const.py:343`)

### const.py — feature flags + training (Stage 4)
- `SHOW_ML_LAB = True` (`const.py:743`), `ENABLE_ML_SUGGESTIONS = True` (`const.py:744`), `ENABLE_ML_TRAINING = True` (`const.py:745`).
- `CONF_ML_TRAINING_ENABLED = "ml_training_enabled"`; `DEFAULT_ML_TRAINING_ENABLED = False` (`const.py:806/811`).
- `CONF_ML_TRAINING_HOUR`; `DEFAULT_ML_TRAINING_HOUR = 2` (02:00 local) (`const.py:807/812`).
- `CONF_ML_TRAINING_MIN_CYCLES`; `DEFAULT_ML_TRAINING_MIN_CYCLES = 30` (`const.py:808/813`).
- `CONF_ML_TRAINING_INTERVAL_DAYS`; `DEFAULT_ML_TRAINING_INTERVAL_DAYS = 7` (`const.py:809/814`).
- `ML_TRAINING_AUC_MARGIN = 0.02` (`const.py:819`) — classifier rank-quality gate slack.
- `ML_TRAINING_BACC_MARGIN = 0.02` (`const.py:824`) — classifier calibration gate slack.
- `ML_TRAINING_MIN_POSITIVES = 20` (`const.py:825`).
- `ML_TRAINING_HISTORY_MAX = 30` (`const.py:831`) — per-capability held-out score history depth.
- `ML_TRAINING_REGRESSION_MARGIN = 0.05` (`const.py:838`) — regressor MAE-vs-naive gate (5% lower).
- `ML_TRAINING_MIN_REGRESSION_ROWS = 30` (`const.py:839`).
- `ML_PROGRESS_BLEND_WEIGHT = 0.5` (`const.py:844`) — C4 blend weight.
- `SERVICE_TRIGGER_ML_TRAINING = "trigger_ml_training"` (`const.py:847`).
- `EVENT_ML_TRAINING_COMPLETE = "ha_washdata_ml_training_complete"` (`const.py:848`).

### cycle_detector.py — C1 (NOT in const.py)
- `ML_END_GUARD_MIN_CONFIDENCE = 0.5` (`cycle_detector.py:77`)
- `ML_END_GUARD_MAX_DEFER_SECONDS = 1800.0` (`cycle_detector.py:78`)

### engine.py / training_task.py — internal
- `CONF_ENABLE_ML_MODELS = "enable_ml_models"` (`engine.py:46`).
- Trainer defaults: `fit_logistic` `l2=0.01, learning_rate=0.2, iterations=4000`; `fit_ridge` `alpha=1.0`.
- `_MIN_ROWS = 40` (`training_task.py:89`); `_ACTIVE_FLOOR_RATIO = 0.02` (`training_task.py:88`);
  `_PROGRESS_CUT_FRACTIONS = (0.15,0.30,0.45,0.60,0.75,0.90)` (`training_task.py:86`).
- Feature-extraction thresholds: `MIN_LOW_RUN_S = 45.0`, `_QUALITY_TRACE_LENGTH = 128`,
  `_IDLE_THRESHOLD_W = 2.0`, `_STOP_THRESHOLD_W = 2.0`.
- Matching tuner: `min_cycles=25, min_targets=12, margin=0.03, n_splits=5, min_wins=4, _RESAMPLE_L=150`.

### const.py — matcher weights tunable on-device (context for matching_tuner)
`MATCH_CORR_WEIGHT=0.45, MATCH_DURATION_WEIGHT=0.22, MATCH_ENERGY_WEIGHT=0.22,
MATCH_DTW_ENSEMBLE_W=0.7` (`const.py:396,456,457,427`).

---

## 9. All ML capabilities (classifier vs regressor)

| capability | type | predicts | target label | #feature cols | promotion gate | embedded THRESHOLD / baseline |
|---|---|---|---|---|---|---|
| `end` | classifier | P(low-power event is the true cycle end vs a pause) | `cycle_truly_ended` | 8 (`END_FEATURE_COLUMNS`) | AUC ≥ baseline−0.02 AND bacc@0.55 ≥ base−0.02 AND held-out; n≥40, pos≥20, neg≥5 | shipped, THRESHOLD 0.6 |
| `quality` | classifier | P(finished cycle is a problem / mis-detect) | `problem_cycle` | 31 (`QUALITY_FEATURE_COLUMNS`) | AUC ≥ baseline−0.02 AND bacc@0.65 ≥ base−0.02 AND held-out; n≥40, pos≥20, neg≥5 | shipped, THRESHOLD 0.19 |
| `live_match` | classifier | P(top-1 live program match is correct) | `match_top1_correct` | 8 (`LIVE_MATCH_FEATURE_COLUMNS`) | AUC ≥ baseline−0.02 AND bacc@0.85 ≥ base−0.02 AND held-out; n≥40, pos≥20, neg≥5 | shipped, THRESHOLD 0.371786 |
| `remaining_time` | regressor | cycle completion fraction (elapsed/total) | `progress_fraction` | 7 (`PROGRESS_FEATURE_COLUMNS`) | held-out MAE ≤ naive_mae×(1−0.05); n≥30 rows | **none** (inert until promoted) |
| `total_energy` | regressor | energy completion fraction (energy_so_far/total) | `energy_fraction` | 7 (`PROGRESS_FEATURE_COLUMNS`) | held-out MAE ≤ naive_mae×(1−0.05); n≥30 rows | **none** (inert until promoted) |

Naive baseline for both regressors = `elapsed_over_expected` (feature col 0, clamped [0,1]).

---

## 10. Parity / drift test contract

`tests/test_ml_models.py`:
- `test_embedded_model_matches_lab_parity_fixtures` (`:88`): for each of the 3 model
  modules, asserts `module.score(case.features) == pytest.approx(case.expected_score,
  abs=1e-5)` over the 8 cases in `<name>_parity.json`. This is the cross-repo
  guarantee that the base64-embedded copy reproduces the lab bit-for-bit.
- `test_model_module_loads_and_scores` (`:52`): score ∈ [0,1], order-independent,
  `predict()==(score>=THRESHOLD)`.
- `test_available_models_manifest` (`:72`): manifest lists the 3 models with `kind ∈
  {standardized_logistic, standardized_linear}` and `metrics`.
- `test_parity_fixtures_present_for_all_manifest_models` (`:108`): every manifest
  model ships a `_parity.json` + `_feature_contract.json`.
- `test_unknown_capability_returns_none` (`:82`).

`tests/test_ml_feature_extraction.py`:
- `test_{end,live_match,quality}_columns_match_embedded_model` (`:72/139/225`):
  asserts each `*_FEATURE_COLUMNS` list equals the embedded model's `FEATURE_COLUMNS`
  (the anti-drift gate). Plus geometry + feeds-embedded-model tests.

Each parity fixture (`*_parity.json`) has 8 cases; `_feature_contract.json` documents
`{feature, group, runtime_source}` per column. Other coverage present:
`test_ml_early_commit`, `test_ml_end_guard`, `test_ml_quality_gate`,
`test_ml_cycle_quality_score`, `test_ml_remaining_time`, `test_ml_energy_projection`,
`test_ml_progress_gate`, `test_ml_training`, `test_ml_training_history`,
`test_ml_model_versions_store`, `test_matching_tuner`, `test_matching_tuner_wiring`,
`test_matching_config_store`, `test_ml_last_training_run`, `test_ml_suggestions`.

---

## 11. Persistence + revert paths

Profile-store keys (`homeassistant.helpers.storage.Store`):
- `ml_model_versions` — promoted classifier/regressor specs, `{capability: {spec,
  trained_at, cycle_count, metrics, new_auc/baseline_auc | model_mae/naive_mae}}`.
  `set_ml_model_version` (`profile_store.py:1084`) / `clear_ml_model_versions`
  (`profile_store.py:1090`).
- `ml_last_training_run` (ISO) — advances every run regardless of promotion
  (`profile_store.py:1105`).
- `ml_training_history` — per-capability held-out score series (AUC higher-better /
  MAE lower-better), capped `ML_TRAINING_HISTORY_MAX (30)` (`append_ml_training_history`,
  `profile_store.py:1120`). Feeds the panel fit-trend badge.
- `matching_config` — tuned matcher weight override + metadata; `set/clear_matching_config`
  (`profile_store.py:1173/1178`); `_matching_overrides()` (`profile_store.py:1183`).

Revert (WS commands in `ws_api.py`):
- `ha_washdata/revert_ml_models` → `ws_revert_ml_models` (`ws_api.py:4635`) →
  `clear_ml_model_versions()` → all `resolve_scorer` fall back to baseline; regressors
  become inert. Panel "Reset to built-in models".
- `ha_washdata/revert_matching_config` → `ws_revert_matching_config` (`ws_api.py:4608`) →
  `clear_matching_config()` → matcher reverts to shipped `MATCH_*` defaults.
- `ha_washdata/set_ml_review` (`ws_api.py:4673`) → `set_cycle_review` — write-back that
  turns shadow review into a strong `quality` training label.

Orchestration: `manager.async_run_ml_training(force)` (`manager.py:2552`) — gates:
not mid-cycle, `>= CONF_ML_TRAINING_MIN_CYCLES`, `>= interval_days` since last (all
bypassed by `force`); single-flight `_ml_training_running`. Runs `async_run_training`,
then `_tune_matching_config` (always), records `set_ml_last_training_run` +
`append_ml_training_history`, fires `EVENT_ML_TRAINING_COMPLETE`, and if anything was
promoted recomputes cycle health.

---

## 12. Code-vs-CLAUDE.md / doc discrepancies

1. **`ml/README.md:14-17` is STALE (contradicts reality + CLAUDE.md).** It states for
   `CONF_ENABLE_ML_MODELS`: *"(No runtime consumer wires this yet — the live ML paths
   below run under their own flags.)"* In fact FIVE runtime consumers gate on
   `ml_models_enabled(options)` (C1–C5, §7). CLAUDE.md correctly documents all five.
   README should be updated.

2. **C1 end-guard operating-threshold mismatch (subtle).**
   `training_task._OPERATING_THRESHOLD["end"] = DEFAULT_DEFER_FINISH_CONFIDENCE (0.55)`
   is the *calibration gate's* assumed live cutoff, but the actual live defer trigger
   in `cycle_detector._should_defer_finish` is `ML_END_GUARD_MIN_CONFIDENCE (0.5)`
   (0.55 is only the *match-confidence* precondition, not the ML end-score cutoff). So
   the calibration gate evaluates balanced accuracy at 0.55 while the model is actually
   applied at 0.5 — a 0.05 mismatch. Not a bug per se, but the "FIXED probability cutoff
   each live consumer applies" comment (`training_task.py:70`) is imprecise for `end`.

3. **CLAUDE.md lists "five gated runtime consumers"; there is effectively a sixth ML
   inference path** — the `total_energy` regressor via `progress.ml_energy_total`
   (projected-energy attribute). It is correctly folded under the remaining-time
   regressor family in CLAUDE.md's consumer #4 description ("ML remaining-time
   regressor"), but the `total_energy` head/blend for projected energy is not called out
   as its own consumer. Minor.

4. **CLAUDE.md end-guard doc** says "gated on `DEFAULT_DEFER_FINISH_CONFIDENCE`" and
   "bounded by `ML_END_GUARD_MAX_DEFER_SECONDS`" — both correct — but never names
   `ML_END_GUARD_MIN_CONFIDENCE (0.5)`, the value that actually decides pause-vs-end.
   Doc completeness gap, not an error.

5. **README §"What ships here"** says each embedded module exposes `MODEL_METRICS` and
   `THRESHOLD` — verified correct. No discrepancy; noted for completeness.

Everything else in CLAUDE.md's ML section (scoring math byte-identity, promotion
disciplines, asymmetry guarantees, revert commands, `resolve_scorer`/`resolve_regressor`
as the single bridge, `matching_tuner` tuning the four bounded weights, regressors
having no shipped baseline) matches the code as read.
