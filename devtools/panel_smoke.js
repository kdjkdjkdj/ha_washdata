#!/usr/bin/env node
// WashData - Home Assistant integration for appliance cycle monitoring via smart plugs.
// Copyright (C) 2026 Lukas Bandura
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.
/*
 * Panel render smoke test. Stubs a minimal DOM, loads the panel module (catches
 * top-level TDZ/init errors), instantiates the element, seeds representative
 * state, and calls every tab renderer + the modals so template ReferenceErrors
 * surface without a browser. Exit non-zero on any failure.
 *
 *   node devtools/panel_smoke.js
 */
'use strict';
const path = require('path');
const { pathToFileURL } = require('url');

function fakeEl() {
  const el = {
    style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
    children: [], attributes: {},
    appendChild() {}, setAttribute() {}, removeAttribute() {}, addEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getElementById() { return null; }, focus() {}, setSelectionRange() {},
    getContext() { return null; }, getBoundingClientRect() { return { width: 300, height: 150 }; },
    set innerHTML(_v) {}, get innerHTML() { return ''; },
  };
  return el;
}

global.HTMLElement = class { attachShadow() { return fakeEl(); } addEventListener() {} };
global.customElements = { _cls: null, get() { return null; }, define(_n, c) { this._cls = c; } };
global.document = {
  createElement: () => fakeEl(), createElementNS: () => fakeEl(),
  head: fakeEl(), body: fakeEl(), addEventListener() {},
};
global.window = { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {}, location: {} };
// Node >= 21 defines a getter-only global "navigator", so a plain assignment
// throws. defineProperty works on both old and new runtimes.
Object.defineProperty(globalThis, 'navigator', {
  value: { language: 'en' }, configurable: true, writable: true,
});

let failures = 0;
function check(label, fn) {
  try { fn(); console.log('  ok   ' + label); }
  catch (e) { failures++; console.log('  FAIL ' + label + ' -> ' + e.message); }
}

// Load the panel as an ES module (matching production, where HA imports it via
// import()), so import.meta.url resolves. Wrap the rest in an async IIFE.
(async () => {
// 1. Module load (top-level init / TDZ).
let Panel;
try {
  await import(pathToFileURL(path.resolve(__dirname, '../custom_components/ha_washdata/www/ha-washdata-panel.js')).href);
  Panel = global.customElements._cls;
  if (!Panel) throw new Error('customElements.define was not called');
  console.log('module load: ok');
} catch (e) {
  console.log('module load: FAIL -> ' + e.message);
  process.exit(1);
}

// 2. Instantiate + seed representative state.
const el = new Panel();
el.shadowRoot = fakeEl();
el._container = fakeEl();
el._hass = { states: { 'sensor.p': { state: '5' } } };
el._constants = { stateColors: {}, deviceTypes: [['washing_machine', 'Washing Machine']], mlLabEnabled: true, mlSuggestionsEnabled: true, mlTrainingAvailable: true };
el._panelCfg = { is_admin: true, panel: {}, prefs: {}, rbac: { enabled: false }, users: [] };
el._devices = [{ entry_id: 'e', title: 'Washer', detector_state: 'running', options: { device_type: 'washing_machine' }, current_power_w: 500, cycle_progress_pct: 40, time_remaining_s: 600, suggestions_count: 1, feedback_count: 1 }];
el._selIdx = 0;
el._profiles = [{ name: 'Cotton 60', avg_duration: 1000, avg_energy: 0.5, cycle_count: 4 }, { name: 'Cotton 40', avg_duration: 1010, avg_energy: 0.4, cycle_count: 3 }, { name: 'Quick', avg_duration: 300, avg_energy: 0.1, cycle_count: 2 }];
el._profileGroups = { groups: [{ name: 'Cotton 2:47', members: ['Cotton 60', 'Cotton 40'], cohesion: 0.93, cohesive: true }, { name: 'Loose', members: ['Cotton 60', 'Quick'], cohesion: 0.4, cohesive: false }], suggestions: [{ members: ['Cotton 60', 'Cotton 40'], existing_group: null }], min_cohesion: 0.85 };
el._opts = { device_type: 'washing_machine', min_power: 2, off_delay: 180, abrupt_drop_watts: 500, notify_actions: [{ action: 'X', title: 'Y' }], notify_people: ['person.a'], notify_start_services: ['notify.phone'] };
el._cycles = [{ id: 'c1', profile_name: 'Cotton 60', status: 'completed', duration: 1000, start_time: new Date().toISOString(), energy_wh: 500, match_confidence: 0.8 }, { id: 'c2', profile_name: null, status: 'force_stopped', duration: 200, start_time: new Date().toISOString() }];
el._suggestions = [{ key: 'off_delay', suggested: 120, current: 180, reason: 'test' }];
el._mlSettings = { off_delay: { ml_value: 130, ml_reason: 'ml' } };
el._mlById = {};
el._mlTrainingStatus = { available: true, enabled: true, running: false, last_trained: new Date().toISOString(), cycle_count: 35, min_cycles: 30, hour: 2,
  on_device_models: {
    end: { trained_at: new Date().toISOString(), cycle_count: 40, kind: 'standardized_logistic', label: 'Cycle-end detection', blurb: "Knowing when a cycle has truly finished", auc: 0.91, metric: 'AUC 0.91 on held-out data', trend: 'improving' },
    remaining_time: { trained_at: new Date().toISOString(), cycle_count: 40, kind: 'standardized_linear', label: 'Time-remaining estimate', blurb: 'Predicting how long is left', model_mae: 0.02, naive_mae: 0.12, metric: 'error 0.020 vs 0.120 baseline', trend: 'declining' },
  },
  matching: { defaults: { corr_weight: 0.45, duration_weight: 0.22, energy_weight: 0.22, dtw_ensemble_w: 0.7 }, tuned: { config: { corr_weight: 0.5, duration_weight: 0.15, energy_weight: 0.15, dtw_ensemble_w: 0.85 }, trained_at: new Date().toISOString(), cycle_count: 40, baseline_test_top1: 0.7, tuned_test_top1: 0.8 }, active: 'tuned' } };
el._powerData = { live: [], raw: [], cycle_active: true };
el._diag = { total_cycles: 10, total_profiles: 3, debug_traces_count: 0, file_size_kb: 12.3 };
el._logs = [];
el._feedbacks = [{ cycle_id: 'c2', detected_profile: 'Cotton 60', confidence: 0.5 }];

// 3. Exercise the renderers.
check('_htmlHeader', () => el._htmlHeader());
check('_htmlDeviceBar', () => el._htmlDeviceBar());
check('_htmlBody (all panes)', () => el._htmlBody());
check('_htmlStatus', () => el._htmlStatus());
check('_htmlHistory', () => el._htmlHistory());
check('_htmlProfiles', () => el._htmlProfiles());
check('_htmlSettings', () => el._htmlSettings());
check('_htmlSettings (search)', () => { el._settingsSearch = 'threshold'; const h = el._htmlSettings(); el._settingsSearch = ''; return h; });
check('_htmlAutomations', () => { el._deviceAutomations = [{ id: 'abc', name: 'Notify on finish', enabled: true }, { id: 'def', name: 'Off automation', enabled: false }]; return el._htmlAutomations(); });
check('_htmlAutomations (legacy actions)', () => { const o = el._opts; el._opts = { ...o, notify_actions: [{ service: 'notify.phone', data: { message: 'done' } }] }; const h = el._htmlAutomations(); el._opts = o; return h; });
check('_htmlSettings (notifications section)', () => { const s = el._settingsSec; el._settingsSec = 'notifications'; const h = el._htmlSettings(); el._settingsSec = s; return h; });
check('_htmlPanel (advanced)', () => el._htmlPanel());
check('_htmlDiagnostics', () => el._htmlDiagnostics());
check('_htmlLogDrawer', () => { const o = el._logOpen; el._logOpen = true; const h = el._htmlLogDrawer(); el._logOpen = o; return h; });
check('_htmlMlTab', () => el._htmlMlTab());
check('_htmlMlStatusSection', () => el._htmlMlStatusSection(el._mlTrainingStatus, 'entry-1'));
check('_htmlMlLearnedSection', () => el._htmlMlLearnedSection(el._mlTrainingStatus));
check('_htmlMlLearnedSection (empty)', () => { const s = el._mlTrainingStatus; el._mlTrainingStatus = { ...s, on_device_models: {} }; const h = el._htmlMlLearnedSection(el._mlTrainingStatus); el._mlTrainingStatus = s; return h; });
check('_htmlMatchingTuningCard', () => el._htmlMatchingTuningCard());
check('_htmlMatchingTuningCard (default)', () => { const s = el._mlTrainingStatus; el._mlTrainingStatus = { ...s, matching: { ...s.matching, tuned: null, active: 'default' } }; const h = el._htmlMatchingTuningCard(); el._mlTrainingStatus = s; return h; });
// Playground (unified workbench + drawer, with and without backend data)
check('_htmlPlayground (workbench, empty)', () => { el._pgAnalysisTab = 'history'; return el._htmlPlayground(); });
check('_htmlPlayground (workbench, with detail)', () => {
  el._pgAnalysisTab = 'history';
  el._pgCycleId = 'c1';
  el._pgPowerPts = [{ t: 0, w: 5 }, { t: 500, w: 900 }, { t: 1000, w: 3 }];
  el._pgDetail = {
    cycle_id: 'c1', label: 'Cotton 60', duration_s: 1000,
    series: [
      { t: 0, power: 5, energy_wh: 0, state: 'starting', progress: null, remaining_s: null, phase: null, confidence: null, matched_profile: null },
      { t: 300, power: 900, energy_wh: 80, state: 'running', progress: 30, remaining_s: 700, phase: 'Wash', confidence: 0.72, matched_profile: 'Cotton 60', projected_energy_wh: 500, projected_cost: 3 },
      { t: 1000, power: 3, energy_wh: 500, state: 'ending', progress: 96, remaining_s: 40, phase: 'Spin', confidence: 0.8, matched_profile: 'Cotton 60', projected_energy_wh: 505, projected_cost: 3 },
    ],
    events: [
      { t: 30, type: 'detected', detail: 'cycle detected', severity: 'info' },
      { t: 200, type: 'match_commit', detail: 'Cotton 60 (0.7)', severity: 'info' },
      { t: 900, type: 'notify_pre_complete', detail: 'almost done', severity: 'info' },
      { t: 1000, type: 'finished', detail: 'reason=smart', severity: 'info' },
    ],
    alerts: [{ code: 'overrun', severity: 'warn', detail: 'Ran 110% of typical.' }],
    outcome: { detected: true, detected_count: 1, termination_reason: 'smart', status: 'completed', final_duration_s: 1000, matched_profile: 'Cotton 60', match_correct: true, overrun_ratio: 1.1, projected_energy_wh: 505, projected_cost: 3 },
  };
  const h = el._htmlPlayground(); el._pgUpdateStripAt(500); el._pgUpdateStripAt(null); return h;
});
check('_htmlPlayground (drawer: history)', () => {
  el._pgAnalysisTab = 'history';
  el._pgHistory = {
    rows: [
      { cycle_id: 'c1', label: 'Cotton 60', detected: true, detected_count: 1, matched_profile: 'Cotton 60', match_correct: true, confidence: 0.8, termination_reason: 'smart', duration_s: 1000, expected_s: 1000, overrun_ratio: 1.0, alerts: [] },
      { cycle_id: 'c2', label: 'Cotton 40', detected: true, detected_count: 1, matched_profile: 'Cotton 60', match_correct: false, confidence: 0.5, termination_reason: 'timeout', duration_s: 1400, expected_s: 1010, overrun_ratio: 1.38, alerts: ['overrun'] },
    ],
    summary: { cycles: 2, detected: 2, labelled: 2, match_correct: 1, match_wrong: 1, unmatched: 0, false_end: 0 },
    baseline_rows: [
      { cycle_id: 'c1', label: 'Cotton 60', match_correct: true, termination_reason: 'smart', duration_s: 1000 },
      { cycle_id: 'c2', label: 'Cotton 40', match_correct: false, termination_reason: 'timeout', duration_s: 1400 },
    ],
    baseline_summary: { cycles: 2, detected: 2, labelled: 2, match_correct: 1 },
    diff: { newly_correct: [], regressed: [], end_timing_changed: ['c2'] },
  };
  return el._htmlPlayground();
});
check('_htmlPlayground (drawer: sweep 1D)', () => {
  el._pgAnalysisTab = 'sweep';
  el._pgSweepNew = { param: 'off_delay', objective: 'match_accuracy', current_value: 180, best_value: 120, best_metric: 0.9,
    points: [{ value: 120, metric: 0.9, summary: {} }, { value: 180, metric: 0.8, summary: {} }, { value: 240, metric: 0.7, summary: {} }] };
  return el._htmlPlayground();
});
el._pgAnalysisTab = 'history';

check('_buildHtml', () => el._buildHtml());

// Modals
check('modal: profile-group (new)', () => { el._modal = { type: 'profile-group', orig: null, name: '', members: [] }; return el._htmlModal(); });
check('modal: profile-group (edit)', () => { el._modal = { type: 'profile-group', orig: 'Cotton 2:47', name: 'Cotton 2:47', members: ['Cotton 60', 'Cotton 40'] }; return el._htmlModal(); });
check('modal: cycle-detail review', () => { el._modal = { type: 'cycle-detail', mode: 'review', loaded: true, cycleId: 'c1', curve: { samples: [[0, 1], [10, 2]], full_duration_s: 1000, duration: 1000, profile_name: 'Cotton 60', start_time: new Date().toISOString(), artifacts: [{ type: 'pause', start_s: 300, end_s: 420, detail: 'Power dropped to near zero for ~120s then resumed — likely the door was opened.', severity: 0.4 }] }, ml: null }; return el._htmlModal(); });
check('modal: cycle-detail inspect (draw w/ artifacts)', () => { el._modal = { type: 'cycle-detail', mode: 'view', loaded: true, cycleId: 'c1', curve: { samples: [[0, 900], [300, 950], [360, 3], [420, 940], [1000, 900]], full_duration_s: 1000, profile_name: 'Cotton 60', start_time: new Date().toISOString(), artifacts: [{ type: 'pause', start_s: 300, end_s: 420, detail: 'door opened', severity: 0.4 }] }, ml: null }; const h = el._htmlModal(); el._drawCycleEditor(); return h; });
check('modal: cycle-detail inspect (with restart gap)', () => { const now = new Date(); el._modal = { type: 'cycle-detail', mode: 'view', loaded: true, cycleId: 'c2', curve: { samples: [[0, 900], [200, 950], [600, 920], [1000, 10]], full_duration_s: 1000, profile_name: 'Cotton 60', start_time: new Date(now - 1000000).toISOString(), restart_gaps: [{ start_ts: new Date(now - 600000).toISOString(), end_ts: new Date(now - 400000).toISOString(), gap_seconds: 200, profile: 'Cotton 60', match_confidence: 0.78 }] }, ml: null }; const h = el._htmlModal(); el._drawCycleEditor(); return h; });
// Trim boundary precision. The offsets below are the real tail of a dishwasher
// cycle recorded 2026-08-06: the machine's self-shutdown is the single 0.0 W
// sample at 3132.0 s, and the panel used to round the boundary to whole seconds
// with no snapping, so a drag ending at 3131.6 s - or a typed 3132 against a
// sample at 3132.3 - silently cut the very edge the trim was aiming at. The trim
// call is irreversible, so this is pinned.
const _trimCurve = () => ({
  type: 'cycle-detail', mode: 'trim', loaded: true, cycleId: 'c1', ml: null,
  curve: {
    samples: [[0, 6.6], [3122.3, 11.7], [3132.0, 0.0], [3132.3, 0.0], [3152.0, 0.5], [3352.3, 0.5]],
    full_duration_s: 3352.3, sample_count: 6, decimated: false,
    start_time: new Date().toISOString(),
  },
  trim: { start: 0, end: 3352.3 },
});

check('trim: a dragged boundary snaps onto a real sample', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3131.6;              // where a pointer drag lands
  el._snapTrimBounds();
  if (el._modal.trim.end !== 3132.0) throw new Error('expected 3132.0, got ' + el._modal.trim.end);
});

check('trim: a whole-second entry snaps to the nearest sample, not below it', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3132;                // what the old rounded input produced
  el._snapTrimBounds();
  if (el._modal.trim.end !== 3132.0) throw new Error('expected 3132.0, got ' + el._modal.trim.end);
});

check('trim: the readout names the sample that survives', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3132.0;
  const out = el._trimReadout();
  if (!/3132\.0 s/.test(out)) throw new Error('boundary offset missing: ' + out);
  if (!/0\.0 W/.test(out)) throw new Error('boundary wattage missing: ' + out);
  if (!/3 \/ 6/.test(out)) throw new Error('kept/total count missing: ' + out);
});

check('trim: offsets are formatted to a tenth of a second', () => {
  el._modal = _trimCurve();
  if (el._fmtTrimOffset(3132.29) !== '3132.3') throw new Error(el._fmtTrimOffset(3132.29));
  if (el._fmtTrimOffset(3132) !== '3132.0') throw new Error(el._fmtTrimOffset(3132));
});

// The spinner arrows and the up/down keys move the boundary by one SAMPLE.
// Regression guard: they used to move by 0.1 s, which always landed inside the
// snap radius of the sample already selected, so the value bounced straight back
// and the control looked dead (reported from the live panel, 2026-08-06).
check('trim: stepping up moves to the next sample', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3132.0;
  el._stepTrimBySample('end', 1);
  if (el._modal.trim.end !== 3132.3) throw new Error('expected 3132.3, got ' + el._modal.trim.end);
});

check('trim: stepping down moves to the previous sample', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3132.0;
  el._stepTrimBySample('end', -1);
  if (el._modal.trim.end !== 3122.3) throw new Error('expected 3122.3, got ' + el._modal.trim.end);
});

check('trim: a step never leaves the boundary where it was', () => {
  el._modal = _trimCurve();
  const before = el._modal.trim.end = 3132.0;
  el._stepTrimBySample('end', 1);
  if (el._modal.trim.end === before) throw new Error('stepper did nothing - the 0.1s bounce is back');
});

// Clock-time input. The mode reads the entered time against the cycle's own date,
// so a cycle running past midnight has to be shifted by a day - but the shift used
// to fire for ANY time earlier than the start, and the clamp then pinned the START
// handle to the last sample. What survived was a one-second window, the store kept
// a single sample, and the cycle was gone with no way back (upstream #366).
// Local-time constructor on purpose: the mode compares wall-clock against the
// cycle's date, so the fixture must not carry a fixed UTC offset.
const _clockCurve = (h, min, full) => {
  const start = new Date(2026, 7, 9, h, min, 0, 0);
  return {
    type: 'cycle-detail', mode: 'trim', loaded: true, cycleId: 'c1', ml: null, timeMode: 'clock',
    curve: {
      samples: [[0, 11.4], [full, 11.4]], full_duration_s: full, sample_count: 2,
      decimated: false, start_time: start.toISOString(),
    },
    trim: { start: 0, end: full },
  };
};
const _clockOff = (mod, clock) => { el._modal = mod; return el._clockToOffset(clock); };

check('trim/clock: the exact start maps to offset 0', () => {
  const got = _clockOff(_clockCurve(21, 0, 9180), '21:00:00');
  if (got !== 0) throw new Error('expected 0, got ' + got);
});

check('trim/clock: a start entered before the cycle keeps the whole cycle', () => {
  const got = _clockOff(_clockCurve(21, 0, 9180), '20:44:00');
  if (got !== 0) throw new Error('expected 0, got ' + got + ' - the day shift is back');
});

check('trim/clock: two seconds before the start is still the front of the cycle', () => {
  const got = _clockOff(_clockCurve(21, 0, 9180), '20:59:58');
  if (got !== 0) throw new Error('expected 0, got ' + got);
});

check('trim/clock: a cycle running past midnight still shifts by a day', () => {
  const got = _clockOff(_clockCurve(23, 30, 7200), '00:30:00');
  if (got !== 3600) throw new Error('expected 3600, got ' + got);
});

check('trim/clock: before the start of a past-midnight cycle is the front, not the tail', () => {
  const got = _clockOff(_clockCurve(23, 30, 7200), '23:00:00');
  if (got !== 0) throw new Error('expected 0, got ' + got);
});

check('trim/clock: a time past the end clamps to the end', () => {
  const got = _clockOff(_clockCurve(21, 0, 9180), '23:50:00');
  if (got !== 9180) throw new Error('expected 9180, got ' + got);
});

check('trim/clock: an early start no longer collapses the window', () => {
  const mod = _clockCurve(21, 0, 9180);
  el._modal = mod;
  mod.trim.start = Math.max(0, Math.min(el._clockToOffset('20:44:00'), mod.trim.end - 1));
  mod.trim.end = Math.min(mod.curve.full_duration_s, Math.max(el._clockToOffset('23:50:00'), mod.trim.start + 1));
  const width = mod.trim.end - mod.trim.start;
  if (width !== 9180) throw new Error('window is ' + width + 's, expected the full 9180s');
});

check('trim: stepping from an off-sample value snaps first', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3131.6;            // where a pointer drag lands
  el._stepTrimBySample('end', -1);
  if (el._modal.trim.end !== 3122.3) throw new Error('expected 3122.3, got ' + el._modal.trim.end);
});

check('trim: a step cannot run past the end of the curve', () => {
  el._modal = _trimCurve();
  el._modal.trim.end = 3352.3;            // last sample
  el._stepTrimBySample('end', 1);
  if (el._modal.trim.end !== 3352.3) throw new Error('ran past the last sample: ' + el._modal.trim.end);
});

check('trim: a step cannot push start past end', () => {
  el._modal = _trimCurve();
  el._modal.trim.start = 3122.3;
  el._modal.trim.end = 3132.0;
  el._stepTrimBySample('start', 1);       // 3132.0 would collide with end
  if (el._modal.trim.start !== 3122.3) throw new Error('start crossed end: ' + el._modal.trim.start);
});

check('modal: cycle-detail trim', () => { el._modal = _trimCurve(); return el._htmlModal(); });

check('modal: profile-panel stats', () => { el._modal = { type: 'profile-panel', name: 'Cotton 60', tab: 'stats', loaded: true, stats: el._profiles[0], env: {} }; return el._htmlModal(); });
check('modal: compare-cycles (html + draw)', () => {
  el._cycles = [
    { id: 'c1', start_time: new Date().toISOString(), duration: 1000, profile_name: 'Cotton 60' },
    { id: 'c2', start_time: new Date().toISOString(), duration: 1200, profile_name: 'Cotton 40' },
  ];
  el._modal = {
    type: 'compare-cycles', ids: ['c1', 'c2'], hidden: new Set(['c2']), overlays: ['Cotton 60'], loaded: true,
    cycles: { c1: { samples: [[0, 900], [500, 800], [1000, 5]], full_duration_s: 1000 }, c2: { samples: [[0, 950], [1200, 3]], full_duration_s: 1200 } },
  };
  const h = el._htmlModal(); el._drawCompareCanvas(); return h;
});
check('modal: store-share-device (tree)', () => {
  el._shareableCycles = [
    { id: 'g1', start_time: new Date().toISOString(), duration: 3600, profile_name: 'Cotton 60', source: 'recorder' },
    { id: 'g2', start_time: new Date().toISOString(), duration: 1800, profile_name: 'Eco 40', source: 'recorder' },
  ];
  el._sharePhasePrograms = ['Cotton 60'];
  el._modal = { type: 'store-share-device', selected: new Set(['g1']), includePhases: new Set(['Cotton 60']), includeSettings: true };
  return el._htmlModal();
});
check('modal: store-share-device (empty)', () => { el._shareableCycles = []; el._sharePhasePrograms = []; el._modal = { type: 'store-share-device', selected: new Set(), includePhases: new Set(), includeSettings: false }; return el._htmlModal(); });
el._modal = null;

console.log(failures ? `\nSMOKE FAILED (${failures})` : '\nSMOKE OK');
process.exit(failures ? 1 : 0);
})();
