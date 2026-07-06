# Anti-Wrinkle for Washing Machines — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Allow WashData's native anti-wrinkle handling (STATE_ANTI_WRINKLE) for `washing_machine` devices, so a completed cycle followed by periodic anti-crease drum bursts fires the finish notification at cycle end instead of after the door opens.

**Architecture:** Extend the two `device_type in (DRYER, WASHER_DRYER)` gates in `cycle_detector.py` to also include `WASHING_MACHINE`. No new config, no default changes, no device_type migration. The opt-in checkbox already exists in the UI. Invert the existing negative test and add an opt-out guard test.

**Tech Stack:** HA custom component (Python 3.12+), pytest.

**Spec:** `docs/superpowers/specs/2026-07-06-anti-wrinkle-washer-design.md`

## Global Constraints

- All code, comments, docstrings, UI strings, commit messages in **English**.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Branch `feature/anti-wrinkle-washer` in `D:\kdj_nas01\work\ClaudeProjekte\ha_washdata` (fork remote `origin`).
- Run tests with `.venv/Scripts/python -m pytest <path> -v` (NOT run_tests.sh). A git-excluded root `conftest.py` shim provides Windows socket support — do not touch/commit it.
- Only touch: `cycle_detector.py`, `tests/test_issue_68_anti_wrinkle.py`, `README.md`, `CHANGELOG.md`, `manifest.json`. Do NOT touch dishwasher logic, the ghost/pump-out logic, or the anti-wrinkle threshold constants.
- Both gate tuples must remain identical to each other.
- Known pre-existing baseline noise (NOT a finding): 1 lingering-timer teardown ERROR in tests/test_manager_event_payload_and_ghosts.py.

---

### Task 1: Open the gate + invert/extend tests + docs

**Files:**
- Modify: `custom_components/ha_washdata/cycle_detector.py` (two gate tuples, ~lines 626-628 and 1443-1444)
- Modify: `tests/test_issue_68_anti_wrinkle.py` (invert one test, add one)
- Modify: `README.md` (Features list), `CHANGELOG.md` (new section)

**Interfaces:**
- Consumes: `DEVICE_TYPE_WASHING_MACHINE` (already imported in cycle_detector.py line 27 and in the test file).
- Produces: `washing_machine` + `anti_wrinkle_enabled=True` → completed cycle transitions to `STATE_ANTI_WRINKLE`; `anti_wrinkle_enabled=False` → `STATE_FINISHED` (unchanged).

- [ ] **Step 1: Invert the existing test and add the opt-out test (TDD red)**

In `tests/test_issue_68_anti_wrinkle.py`, REPLACE the whole `test_anti_wrinkle_not_for_washing_machine` function (currently lines ~213-242) with these two functions:

```python
def test_anti_wrinkle_enabled_washing_machine(mock_callbacks: dict[str, Mock]) -> None:
    """Anti-wrinkle transition works for washing machines when enabled."""
    config = CycleDetectorConfig(
        min_power=5.0,
        off_delay=60,
        device_type=DEVICE_TYPE_WASHING_MACHINE,
        anti_wrinkle_enabled=True,
        anti_wrinkle_max_power=400.0,
        anti_wrinkle_max_duration=60.0,
        anti_wrinkle_exit_power=0.8,
    )

    detector = CycleDetector(
        config=config,
        on_state_change=mock_callbacks["on_state_change"],
        on_cycle_end=mock_callbacks["on_cycle_end"],
    )

    # Complete a cycle
    detector.process_reading(500.0, dt(0))
    detector.process_reading(500.0, dt(10))
    for t in range(10, 1500, 10):
        detector.process_reading(500.0, dt(t))

    detector.process_reading(1.0, dt(1501))
    detector.process_reading(1.0, dt(1540))
    flush_buffer(detector, 1540, num_readings=65)

    # Should transition to ANTI_WRINKLE (not FINISHED) for washing machines when enabled
    assert detector.state == STATE_ANTI_WRINKLE
    mock_callbacks["on_cycle_end"].assert_called_once()
    cycle_data = mock_callbacks["on_cycle_end"].call_args[0][0]
    assert cycle_data["status"] == "completed"


def test_anti_wrinkle_disabled_washing_machine_finishes(
    mock_callbacks: dict[str, Mock],
) -> None:
    """Without the opt-in flag, washing machines still finish normally (no anti-wrinkle)."""
    config = CycleDetectorConfig(
        min_power=5.0,
        off_delay=60,
        device_type=DEVICE_TYPE_WASHING_MACHINE,
        anti_wrinkle_enabled=False,
    )

    detector = CycleDetector(
        config=config,
        on_state_change=mock_callbacks["on_state_change"],
        on_cycle_end=mock_callbacks["on_cycle_end"],
    )

    # Complete a cycle
    detector.process_reading(500.0, dt(0))
    detector.process_reading(500.0, dt(10))
    for t in range(10, 1500, 10):
        detector.process_reading(500.0, dt(t))

    detector.process_reading(1.0, dt(1501))
    detector.process_reading(1.0, dt(1540))
    flush_buffer(detector, 1540, num_readings=65)

    # Opt-in flag off -> normal finish, no anti-wrinkle
    assert detector.state == STATE_FINISHED
```

- [ ] **Step 2: Run the new tests to confirm the positive one fails (red)**

Run: `.venv/Scripts/python -m pytest tests/test_issue_68_anti_wrinkle.py -v`
Expected: `test_anti_wrinkle_enabled_washing_machine` FAILS (asserts STATE_ANTI_WRINKLE but current code produces STATE_FINISHED); `test_anti_wrinkle_disabled_washing_machine_finishes` PASSES; all other anti-wrinkle tests still PASS.

- [ ] **Step 3: Open the two gates (green)**

In `custom_components/ha_washdata/cycle_detector.py`:

Gate 1 (~line 626-628), the `anti_wrinkle_active` assignment — change:
```python
        anti_wrinkle_active = (
            self._config.anti_wrinkle_enabled
            and self._config.device_type in (DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHER_DRYER)
```
to:
```python
        anti_wrinkle_active = (
            self._config.anti_wrinkle_enabled
            and self._config.device_type
            in (DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHER_DRYER, DEVICE_TYPE_WASHING_MACHINE)
```
(keep the closing `)` and the rest of the expression as-is).

Gate 2 (~line 1440-1445), the cycle-finish transition — change:
```python
            and self._config.anti_wrinkle_enabled
            and self._config.device_type in (DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHER_DRYER)
        ):
```
to:
```python
            and self._config.anti_wrinkle_enabled
            and self._config.device_type
            in (DEVICE_TYPE_DRYER, DEVICE_TYPE_WASHER_DRYER, DEVICE_TYPE_WASHING_MACHINE)
        ):
```

- [ ] **Step 4: Run the anti-wrinkle test file (green)**

Run: `.venv/Scripts/python -m pytest tests/test_issue_68_anti_wrinkle.py -v`
Expected: ALL pass, including both new tests.

- [ ] **Step 5: Regression — detector + manager suites**

Run: `.venv/Scripts/python -m pytest tests/test_cycle_detector.py tests/test_manager.py tests/test_device_types.py -v`
Expected: PASS (1 known pre-existing lingering-timer error may appear only in the manager event-payload file, which is NOT in this set — so expect fully clean here).

- [ ] **Step 6: Docs**

`README.md` — in the Features list, find the `**Anti-Wrinkle` bullet if present; if none exists, append a new bullet after the "Multi-Device Support" bullet (line ~21):
```markdown
- **Anti-Wrinkle Shield**: For dryers, washer-dryer combos, and washing machines, an optional shield keeps a completed cycle in an *Anti-Wrinkle* state during the periodic post-cycle anti-crease tumbles, so the finish notification fires when the program actually ends rather than after the drum stops or the door opens. Enable it per device under Advanced Settings.
```

`CHANGELOG.md` — add at the very top, above the `## 0.4.5.9 (fork)` section:
```markdown
## 0.4.5.10 (fork) - 2026-07-06

### ✨ Features
- **Anti-Wrinkle Shield for Washing Machines**: The anti-wrinkle handling previously limited to dryers and washer-dryer combos now also applies to `washing_machine` devices when enabled. Modern washers rotate the drum periodically after a program ends (anti-crease), which otherwise keeps resetting end-detection so the finish notification only arrives once the door is opened. With the shield enabled, the completed cycle enters the *Anti-Wrinkle* state and the finish notification fires at program end. Opt-in and unchanged for existing setups (default off); no default threshold changes.

<br>
```

- [ ] **Step 7: Commit**

```bash
git add custom_components/ha_washdata/cycle_detector.py tests/test_issue_68_anti_wrinkle.py README.md CHANGELOG.md
git commit -m "feat: enable anti-wrinkle shield for washing machines

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Full suite, version bump, fork release, deploy (controller-run)

**Files:** Modify `custom_components/ha_washdata/manifest.json:19`

- [ ] **Step 1: Full fast suite**

Run: `.venv/Scripts/python -m pytest tests/ -q --ignore=tests/test_verify_alignment.py --ignore=tests/test_mock_socket_synthesis.py --ignore=tests/repro`
Expected: 307+ passed (baseline 305 + 2 net new; one test was replaced by two), 3 skipped, 1 pre-existing error, no new failures.

- [ ] **Step 2: Version bump + commit**

`manifest.json` line 19: `"0.4.5.9"` → `"0.4.5.10"`.
```bash
git add custom_components/ha_washdata/manifest.json
git commit -m "chore: bump version to 0.4.5.10 for anti-wrinkle release

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push, merge, release**

```bash
git push -u origin feature/anti-wrinkle-washer
git checkout main && git merge --no-ff feature/anti-wrinkle-washer -m "Merge feature/anti-wrinkle-washer

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin main
gh release create v0.4.5.10 --repo kdjkdjkdj/ha_washdata --target main \
  --title "v0.4.5.10 (fork): anti-wrinkle shield for washing machines" \
  --notes "Enables the anti-wrinkle shield for washing_machine devices (opt-in). See CHANGELOG 0.4.5.10."
```
Verify `gh auth status` shows `kdjkdjkdj` active first.

- [ ] **Step 4: Deploy to Tiny + user action**

HACS update WashData to v0.4.5.10 on Tiny (`ha_manage_hacs` download), restart HA, confirm the 3 entries load. Then the user enables the Anti-Wrinkle Shield checkbox for the washing machine (UI). Live verification on the next wash cycle: state shows Anti-Wrinkle after program end, finish notification arrives at program end.
