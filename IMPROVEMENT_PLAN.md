# WashData — Improvement & Fix Plan

> Living checklist derived from the 2026-07-11 deep-dive audit (unfinished-features,
> frontend/UI, and two code bug-hunts). Check items off as they land. Evidence is cited
> as `file:line`. Severity: **P0** = release-blocking, **HIGH/MED/LOW** = bug severity.

## Status snapshot

- Branch `0.5.0` is **68 commits ahead of `main`, 0 behind**. Roadmap Groups **A–H merged**;
  **G2 (community library) deferred**. Fast suite: **1027 passed, 1 skipped**; E2E: **208/208**
  (all 22 playground spec failures resolved — 2026-07-13 pass).
- Version RESOLVED: stays **0.5.0** (no bump; `manifest.json` unchanged). STORAGE_VERSION bumped
  to **9** (additive-key init migration). CHANGELOG consolidated into `## 0.5.0`.
- Playground: **fully complete** — PG-1/2/3/4/5/6/7/8/9/10 all done. Stage-5 group collapsing
  in `playground.py`; E2E spec rewritten (208/208); 23 orphaned keys pruned.
- Phase 2 UI hardening: all medium/larger items done **except** `<ha-icon>` unification (deferred).
  ARIA tab-widget, button-cards, error states, `--wd-*` tokens all landed.
- Phase 4 correctness: ENDING energy gate fixed; storage v9 done. Only cosmetic smells remain.
- Release gated by issue **#300** ("DO NOT SUBMIT ISSUES OR PRs UNTIL 0.5.0 RELEASE").
- All work is uncommitted (working tree, branch `0.5.0`); nothing committed per the maintainer's request.

Verify gate after every phase:
```bash
./run_tests.sh                                                   # fast suite
node --check custom_components/ha_washdata/www/ha-washdata-panel.js
python3 -m compileall custom_components/ha_washdata -q
# UI-touching phases also: cd playwright-tests && npx playwright test
```

---

## Phase 0 — Pre-release blockers

- [x] **B1 (MED-HIGH)** — Back-to-back cycle race: `_async_process_cycle_end`'s reset tail
      clobbered a newly-started cycle and could reset it to Off mid-run. **FIXED**: capture
      `_ranking_snapshot_cycle_id` as a `cycle_token` in `_on_cycle_end`, thread it into
      `_async_process_cycle_end`, and skip the terminal-state reset + expiry-timer re-arm when
      the token has rolled over. Regression tests added
      (`test_cycle_end_reset_skipped_when_new_cycle_started` + control). _Residual (lesser):
      the learning/anomaly reads earlier in the tail still use instance state; noted as a
      Phase-4 follow-up, not the severe symptom._
- [x] **B2 (MED)** — Ghost/noise cycle branch missing `return`. **FIXED**: `return` after
      `_handle_noise_cycle(...)` (mirrors the tested dishwasher pump-out contract). Existing
      test strengthened to assert `_async_process_cycle_end` is not called.
- [x] **Playground fate decision** — _Decided: finish (Phase 1)._
- [x] **Version/changelog reconcile** — RESOLVED: user confirmed **it stays 0.5.0** (no bump;
      `manifest.json` unchanged). CHANGELOG's "Unreleased" wave consolidated into the single
      `## 0.5.0` entry (migration wave nested as "Earlier in 0.5.0"); this session's fixes added
      as a "🐛 Fixes & refinements" subsection. README/IMPLEMENTATION/ROADMAP Playground
      descriptions updated to the unified-canvas design.
- [x] **Housekeeping** — DONE: removed all 23 `.claude/worktrees/agent-*` worktrees + their
      branches (stale scratch on old base commits; committed group work already in 0.5.0).
      _(Left the ancient `stash@{0}` alone — not an "agent directory"; drop it separately if
      wanted.)_

## Phase 1 — Finish the Playground (uncommitted rewrite)

- [x] **PG-1 (HIGH)** — ~30 hardcoded strings routed through `_t()`; 62 keys added to
      `translations/panel/en.json` (incl. new `pg_desc` namespace). Bundle rebuilt.
- [x] **PG-7 (LOW)** — `lbl.auto_detect`, `lbl.replay_duration` added.
- [x] Translated the 62 new keys into all 34 languages via 6 grouped subagents (domain
      glossary, placeholder-preserved). Verdict-string separator normalized to a colon
      across en.json + 34 langs + JS fallbacks (no em-dash, per repo rule). Bundle rebuilt
      (35 langs, ~867 EN keys).
- [x] **PG-3 (MED)** — Envelope band fixed to read `{avg,min,max}` `[t,w]` pairs, normalized
      onto the cycle axis (shaded min..max band).
- [x] **PG-4 (MED)** — Removed fabricated candidate confidences; now shows only the real
      analyzed-profile score (`d.stage4.final_score`) or nothing.
- [x] **PG-5 (MED)** — Profile-group modal canvas renamed `wd-pgroup-canvas` (fixed the id
      collision + the wrong `_redrawCanvas` dispatch on Playground hover).
- [x] **PG-6 (LOW-MED)** — Sweep now merges dragged thresholds into the override (like sim).
- [x] **PG-8 (LOW)** — Sweep loop checks `_pgSimCancelled`; Cancel works.
- [x] **PG-2 (HIGH)** — Rewrote `playwright-tests/tests/playground.spec.ts` against the new
      single-canvas DOM. 13 new tests cover the real DOM (cycle/profile selectors, canvas,
      strip, param inputs, run-sim WS assertion, sweep controls, sweep chart); 3 passing WS-assertion
      and mobile-overflow tests kept as-is. E2E suite: **208/208** (was 186/208; 22 failures resolved).
- [x] **PG-9 (LOW)** — Pruned 23 orphaned old-design keys (e.g. `lbl.pg_ev_off/paused/start/match`,
      `msg.pg_tip_*`, `msg.pg_ab_intro`, `toast.pg_sim_failed`) across all 35 language files;
      `pg_desc.*` (dynamically referenced) and `lbl.pg_ev_running/ending` (used in strip/chart) kept.
      Bundle rebuilt (~935 EN keys).
- [x] **PG-10 (LOW)** — Implemented Stage-5 group collapsing in `playground.py`. `_build_match_snapshots`
      now calls `store._grouped_snapshots()` and returns a 4-tuple; `_simulate_one` resolves any
      `__group__*` winner via `store._stage5_pick_member()` before logging/ambiguity checks. Falls
      back silently when no cohesive groups exist (behaviour byte-identical to before).

## Phase 2 — UI hardening (accessibility + design tokens)

Quick wins:
- [x] Shared `:focus-visible` ring for tabs/buttons/chips/cards/links/selects.
- [x] `@media (prefers-reduced-motion: reduce)` guard around pulse/spin/toast motion.
- [x] `role="status"`/`role="alert"` + `aria-live` on toasts (error→assertive, else polite).
- [x] `role="dialog"` + `aria-modal` on all 5 modal wrappers. (`aria-labelledby` w/ title ids
      folded into the larger focus-management task below.)
- [x] `role="img"` + localized `aria-label` on all 9 canvases (8 aria keys added to en.json).
- [x] Fixed `${color}22` alpha-append bug (invalid CSS for `var()` colors) at the status badge
      **and** the profile-health banner border — both now use `color-mix()`.
- [x] Localize stray `Loading…` literals — DONE. Every loading string now routes through
      `_t()` (`msg.loading` / `msg.loading_settings` / `msg.ml_loading`); the last raw literal
      ("Loading diagnostics…" in the diagnostics stats pane) now uses `_t('msg.loading', …)`.

Medium:
- [x] Introduce a `--wd-*` token layer in `:host`. DONE — 14 tokens added (`--wd-radius-sm/md/lg`,
      `--wd-space-xs/sm/md/lg/xl`, `--wd-font-sm/xs`, `--wd-white`, `--wd-tint-xs/sm/md`); 27
      border-radius replacements + 14 `#fff`→`var(--wd-white)` replacements in CSS. Remaining
      `rgba()` tints and one-off values are a lower-priority follow-up.
- [x] Make clickable div-cards real `<button>`s with keyboard support. DONE — 5 attention cards
      (`goto-feedbacks`, `goto-conflicts`, `goto-suggestions`, `open-advanced`×2) and the profile
      card converted from `<div data-action>` to `<button type="button" data-action>`; CSS reset
      applied (`appearance:none; font:inherit; text-align:left; width:100%`). Info-only cards
      without `data-action` left as `<div>`.
- [x] Error state + retry for background WS fetches. DONE — `_fetchCycles`, `_fetchSuggestions`,
      `_fetchProfiles`, `_fetchProfileGroups` each set a `_*Error` flag in catch; the respective
      render methods (`_htmlHistory`, `_htmlSettings`, `_htmlProfiles`) prepend a `.wd-error-state`
      banner with a Retry button (`data-action="retry-*"`) handled in `_onAction`. Envelopes
      remain silent-fallback (low impact).
- [ ] Unify iconography on `<ha-icon>` MDI (card is the reference). _NOT-DONE — 0 `<ha-icon>` usages;
      icons are a mix of emoji (💡🤖🧺⚙️…), inline `<svg>`, and unicode glyphs. Deferred — most
      invasive remaining aesthetic item, lowest functional impact._

Larger:
- [x] Full modal focus management — DONE. Shared `_syncModalFocus(prevFocus)` helper: focus
      move-in on open, Tab/Shift-Tab TRAP + Escape-close in `_onKeydown` over `_focusableEls`,
      and focus RESTORE to the captured trigger (`_modalReturnFocus`) on close; every modal shell
      has `role="dialog" aria-modal="true" aria-labelledby` + a title id.
- [x] ARIA tab-widget semantics — DONE. `role="tablist"` on `.wd-tabs` container; `role="tab"`,
      `aria-selected`, roving `tabindex` on each `.wd-tab` button; `role="tabpanel"` +
      `aria-labelledby` on each `.wd-pane`. Arrow-key nav (Left/Right/Home/End) in `_onKeydown`
      wraps through tabs and fires `click()`+`focus()`.
- [ ] WCAG pass across the panel. _PARTIAL — focus-visible, modal focus mgmt, toast live regions,
      ARIA tab-widget, ~25 aria-labels + role=img, button-cards, `--wd-*` tokens all done; remaining
      gaps: `<ha-icon>` unification, `rgba()` contrast audit, no formal WCAG conformance test._

## Phase 3 — ML correctness

- [x] **B3 (MED)** — FIXED: `_quality_dataset` now uses each cycle's real
      `len(artifacts)` for `flag_count` (mirrors inference); the shared `X` also fixes the
      baseline-AUC blindness. `ml/training_task.py`.
- [x] **B5 (LOW-MED)** — FIXED: all five dataset builders emit a per-row `groups` array
      (source-cycle id); `_holdout_split`/`_regression_split` are group-aware via
      `_group_holdout_indices` so no cycle straddles the split. `live_match` groups by
      `cycle_id`. 3 regression tests added; dependent tests updated to the 4-tuple API.
- [x] Added `kind` guard to `resolve_scorer` (won't sigmoid a `standardized_linear` spec);
      fixed `resolve_regressor` docstring (now names `total_energy` too).
- [x] Confirm `match_progress_top1` ≡ `duration_ratio_top1` duplication vs the lab column defs
      — CONFIRMED parity. Both compute `float(min(progress, 2.0))` in the integration
      (`ml/feature_extraction.py:243,249`) AND the lab (`ml_washdata/wash_ml/live_matching.py:353,359`);
      the duplication is intentional (two independent feature slots the model weights separately)
      and byte-identical lab↔integration, so there is nothing to reconcile.

## Phase 4 — Low-severity correctness + roadmap forward

- [x] **B4 (LOW)** — FIXED: resurrection path now converts offset-second readings to
      absolute ISO timestamps (base = cycle start) so `restore_state_snapshot` no longer
      drops the whole trace. `manager.py`.
- [x] **B6 (LOW)** — FIXED: `reset()` now clears `_verified_pause` (`cycle_detector.py`).
- [x] **B7 (LOW)** — FIXED: all 5 `c["id"]` lookups → `c.get("id")` (`learning.py`).
- [x] **B8 (LOW)** — FIXED: warmup auto-label guard no longer bypassable. `_maybe_request_feedback`
      routes through `route_conf` (preserves the real match confidence, handles an inverted
      `learning_conf >= auto_label_conf` config) so a sub-warmup profile always requests manual
      confirmation and never silently skips. Regression tests: `tests/test_warmup_gate.py` (4).
- [x] Smells (actionable): ENDING energy gate now passes `max_gap_s=energy_gap_threshold_s(recent_ts)`
      to `integrate_wh` at `cycle_detector.py` (prevents energy inflation across a sensor outage
      in the ENDING window). `energy_gap_threshold_s` added to signal_processing import.
      Remaining cosmetic smells: `manager.py` "snapshot" comment; non-monotonic linear-fallback
      progress — both unchanged (correct, not worth detection-adjacent churn).
- [x] Storage **v9** bump — DONE. `STORAGE_VERSION` bumped to 9 (`const.py`). Migration step
      initialises `lifetime_energy_wh=0.0`, `settings_changelog=[]`, `maintenance_log=[]` via
      `setdefault` (idempotent). Two new regression tests in `test_migration_v032.py`.
- [ ] **Suggestion-scan snapshot (CR iter7 #4, DEFERRED)** — `learning._dispatch_scan_and_apply`
      offloads the suggestion generators to an executor thread where they read the store's
      **live** `get_profiles()` dict (cycle access is already `[-N:]`-sliced, so append-safe).
      A concurrent envelope rebuild on the loop could race the profiles read. Low severity
      (suggestion-path only — never touches live matching/detection; executor exceptions are
      caught and just skip that round). Proper fix is a deep-copied `snapshot_for_analysis()`
      threaded through every generator; deferred because that refactor has real GC-pressure
      implications in the 5-min scan hot path and warrants deliberate testing, not an
      autonomous review pass.

Roadmap forward (post-release, per open GitHub issues):
- [ ] #251 water consumption (WIP FR) — scope/design.
- [ ] #215 additional sensors to improve detection (WIP) — triage.
- [ ] #291 long tail at cycle end (needs-info) — reproduce vs terminal-drop.
- [ ] #297 notifications-not-sent — verify the `done`-labeled fix.
- [ ] G2 community profile library — resolve deferred design questions.

---

## Progress log

- 2026-07-11 — Plan created from audit. Starting Phase 0.
- 2026-07-11 — **Phase 0 code fixes landed**: B1 (cycle race) + B2 (ghost return) fixed with
  regression tests. Fast suite **1009 passed, 1 skipped**; panel smoke OK. Version bump +
  worktree cleanup left on HOLD (need user decision / destructive). Moving to Phase 1.
- 2026-07-11 — **Phases 1–4 major pass landed**. Playground: 5 logic bugs (PG-3/4/5/6/8),
  full `_t()` localization (62 keys, all 34 languages, bundle rebuilt). ML: B3 (flag_count
  skew) + B5 (group-aware splits) + `resolve_scorer` guard, with new tests. UI a11y quick-wins
  (focus-visible, reduced-motion, toast/modal roles, 9 canvas aria-labels, `${color}22`/`${col}22`
  color-mix fix). Low-sev: B4 (resurrection restore), B6 (`_verified_pause` reset), B7
  (`c.get("id")`). **Fast suite 1012 passed, 1 skipped; panel smoke OK; compileall OK.**
  Remaining: PG-2 (E2E spec rewrite), PG-9/PG-10, B8, smells, UI medium/large a11y (token
  layer, div-card buttons, error states, focus-trap, tab-widget), and the two HOLD items
  (version bump, worktree cleanup).
- 2026-07-11 — **Release hygiene pass**: user confirmed version stays **0.5.0**. CHANGELOG
  consolidated to a single 0.5.0 entry (+ session "Fixes & refinements"); README /
  IMPLEMENTATION / ROADMAP Playground docs updated to the unified-canvas design; **all 23
  stale agent worktrees + branches removed**. 8 canvas `aria-label` keys being translated
  into 34 languages (3 bg agents), then final bundle rebuild + verify.
- 2026-07-11 — **Release hygiene complete.** All 8 canvas `aria-label` keys translated into
  34 languages; also fixed a pre-existing machine-translation bug the new Playground strip
  surfaced — `lbl.power` meant "authority/force/political power" in 10 languages
  (cs/da/el/fi/hr/hu/is/ja/ko/nb), now the correct electrical term. Bundle rebuilt (35 langs,
  ~867 EN keys); `sync_translations.py` HA-layer no-op. **Final gate green: fast suite 1012
  passed / 1 skipped, panel smoke OK, compileall OK, all 35 panel JSON valid.** Version
  stays 0.5.0 (manifest unchanged). Nothing committed (working tree, branch 0.5.0).
- 2026-07-11/12 — **CodeRabbit review-hardening pass (implement→review iterated to a
  convergence point).** Ran repeated full-diff reviews (iter6 13 findings → iter7 12 →
  iter8 5 → iter9 22); stopped at iter9 once the review output became a *rotating* nitpick
  set (each applied fix adds new review surface) rather than converging to zero. Every
  critical/high/medium-value finding was applied; the residual is nitpicks, domain-wrong
  suggestions (e.g. one told us to move `power_sensor` out of `entry.options`, contradicting
  the documented options-first design), detection-adjacent risky tweaks, and design-deferred
  items. Regression gate is the test suite, not another CR pass. Deep review of the whole 0.5.0 branch vs `main` (authenticated CodeRabbit
  CLI + `coderabbit:code-reviewer` subagents); ~70 actionable findings applied across manager /
  profile_store / ws_api / config_flow / learning / suggestion_engine / analysis /
  signal_processing / ml (engine, trainer, training_task, matching_tuner) / panel.js, plus a
  full backend-localization sweep (Python now returns `*_key`+`*_params`, JS renders via `_t`).
  Highlights: stored-XSS `_esc`, quiet-hours notification-storm flag, config-flow options-first
  resolution, 32KB `match_ranking_top5` sanitize (both write sites), warmup guard (B8), ML
  in-sample/None-baseline promotion guards + operating-threshold calibration gate, matching-tuner
  no-leakage multi-split majority gate, ML provider throttle, RBAC admin-command tightening +
  `is_allowed_path` export/import guards, auto-pause dead-button fix, `binary_metrics` schema
  alignment. **Findings that would regress detection/matching were deliberately REJECTED**
  (time/gap-aware features + integrated-energy in the matcher — empirically worse in the
  `ml_washdata` lab A/B; generated model files needing lab regeneration). **Gate green: fast
  suite 1025/1 skipped, ML+parity 41, slow real-data replay 28/28 (matching top-1 unchanged),
  compileall OK.** Version stays 0.5.0; nothing committed.
- 2026-07-13 — **Completion pass: all remaining IMPROVEMENT_PLAN items (except deferred).** Parallel
  agents landed: PG-10 (Stage-5 group collapsing in `playground.py` — `_build_match_snapshots`
  returns 4-tuple, `_simulate_one` resolves `__group__*` winners via `_stage5_pick_member`);
  PG-2 (playground.spec.ts fully rewritten — 13 new tests cover real DOM; E2E **208/208**, all
  22 failures resolved); PG-9 (23 orphaned old-design keys pruned across 35 lang files, bundle
  ~935 EN keys); Phase 4 (ENDING energy gate now passes `max_gap_s`, storage v9 with 2 regression
  tests, `STORAGE_VERSION=9`); Phase 2 UI hardening (ARIA tab-widget: `role="tablist/tab/tabpanel"`,
  `aria-selected`, roving tabindex, arrow-key nav; 5 attention cards + profile card converted from
  `<div data-action>` to `<button type="button">`; error-state+retry banners for `_fetchCycles/
  Suggestions/Profiles/ProfileGroups`; 14 `--wd-*` CSS tokens + 27 radius + 14 `#fff` replacements).
  **Gate: fast suite 1027/1 skipped; E2E 208/208; compileall OK; node --check OK.**
  Remaining open: `<ha-icon>` unification (deferred, aesthetic), suggestion-scan snapshot race
  (deferred, GC-pressure), WCAG formal audit, post-release GitHub issues.
