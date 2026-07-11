# WashData — Improvement & Fix Plan

> Living checklist derived from the 2026-07-11 deep-dive audit (unfinished-features,
> frontend/UI, and two code bug-hunts). Check items off as they land. Evidence is cited
> as `file:line`. Severity: **P0** = release-blocking, **HIGH/MED/LOW** = bug severity.

## Status snapshot

- Branch `0.5.0` is **68 commits ahead of `main`, 0 behind**. Roadmap Groups **A–H merged**;
  **G2 (community library) deferred**. Fast suite: **1007 passed, 1 skipped**.
- Group A–H features sit in CHANGELOG **"Unreleased"** on top of the shipped `0.5.0` panel;
  `manifest.json` still `0.5.0` → needs a version bump before release.
- Working tree has a **large uncommitted Playground rewrite** in `ha-washdata-panel.js`
  (backend-complete, client-incomplete).
- Release gated by issue **#300** ("DO NOT SUBMIT ISSUES OR PRs UNTIL 0.5.0 RELEASE").

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
- [ ] **PG-2 (HIGH)** — Rewrite `playwright-tests/tests/playground.spec.ts` against the new
      single-canvas DOM. Run `--e2e` green.
- [ ] **PG-9 (LOW)** — Prune ~50 orphaned old Playground translation keys + update CHANGELOG.
      _DEFERRED until translation agents finish (they edit the same lang files)._
- [ ] **PG-10 (LOW)** — Backend replay skips Stage-5 group collapsing. `playground.py:143`.
      Apply Stage-5 or label as a simplification.

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
- [ ] Localize stray `Loading…` literals (`panel.js` ~2267,2326,5667). _Remaining (minor)._

Medium (remaining):
- [ ] Introduce a `--wd-*` token layer (radius/space/type/accent-tint) in `:host`; refactor
      raw `rgba()` tints, 15× `#fff`, and the 4 divergent stat font-sizes onto it.
- [ ] Make clickable div-cards real `<button>`s with keyboard support (`panel.js` ~2830, 2394).
- [ ] Distinct **error state** + retry for background fetches (`catch(()=>{})` → empty==error).
- [ ] Unify iconography on `<ha-icon>` MDI (card is the reference).

Larger (remaining):
- [ ] Full modal focus management (move-in + trap + restore) as a shared helper + `aria-labelledby`.
- [ ] ARIA tab-widget semantics (`role="tablist"/"tab"/"tabpanel"`, arrow-key nav).
- [ ] WCAG pass across the panel.

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
- [ ] Confirm `match_progress_top1` ≡ `duration_ratio_top1` duplication vs the lab column
      defs (`ml/feature_extraction.py:243,249`). _Deferred — needs the ml_washdata lab; note
      as a low-priority parity check._

## Phase 4 — Low-severity correctness + roadmap forward

- [x] **B4 (LOW)** — FIXED: resurrection path now converts offset-second readings to
      absolute ISO timestamps (base = cycle start) so `restore_state_snapshot` no longer
      drops the whole trace. `manager.py`.
- [x] **B6 (LOW)** — FIXED: `reset()` now clears `_verified_pause` (`cycle_detector.py`).
- [x] **B7 (LOW)** — FIXED: all 5 `c["id"]` lookups → `c.get("id")` (`learning.py`).
- [ ] **B8 (LOW)** — Warmup auto-label guard bypassable under inverted threshold config
      (`learning.py:426-484`). _Remaining — needs a degenerate config to trigger._
- [ ] Smells: `manager.py` misleading "snapshot" comment; ENDING energy gate missing
      `max_gap_s` (`cycle_detector.py:~1270`); non-monotonic linear-fallback progress.
      _Remaining (cosmetic/fragile-but-correct)._
- [ ] Optional storage **v9** bump to init additive keys (`lifetime_energy_wh`,
      `settings_changelog`, `maintenance_log`).

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
