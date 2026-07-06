# energy_sensor (Native Energy Meter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional per-device `energy_sensor` config to WashData; at cycle end the meter delta (end − start) replaces the trapezoid-integrated `cycle_data["energy_wh"]`, with the integration kept as fallback and for ghost-cycle filtering.

**Architecture:** Two counter snapshots in `WashDataManager` (cycle start / cycle end), normalized to Wh via `unit_of_measurement`. The start snapshot is persisted in the active-cycle snapshot so HA restarts mid-cycle don't lose it. Detection logic (`cycle_detector.py`) is untouched; ghost/pump-out filters keep using the integrated value. `cycle_data["energy_source"]` (`"meter"`/`"integration"`) marks the source.

**Tech Stack:** Home Assistant custom component (Python 3.12+), voluptuous config flow, pytest + pytest-homeassistant-custom-component.

**Spec:** `docs/superpowers/specs/2026-07-06-energy-sensor-design.md`

## Global Constraints

- All code, comments, docstrings, UI strings, and commit messages in **English** (upstream PR is the goal). Chat with the user is German.
- Every commit message ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Do NOT touch `cycle_detector.py`, the ghost-cycle/pump-out thresholds in `_on_cycle_end`, or the notification dispatch path.
- i18n: only `custom_components/ha_washdata/strings.json` and `translations/en.json`. Never edit the other 57 language files.
- Work on branch `feature/energy-sensor` in `D:\kdj_nas01\work\ClaudeProjekte\ha_washdata` (fork `kdjkdjkdj/ha_washdata`, remote `origin`; upstream remote `upstream`).
- `docs/superpowers/**` commits are fork-internal and will be dropped before the upstream PR.
- Windows: run tests with `.venv/Scripts/python.exe -m pytest ...` (Git Bash: `.venv/Scripts/python -m pytest ...`). Do not use `run_tests.sh` (expects Linux venv layout).
- Config key literal is `"energy_sensor"`; snapshot key literal is `"energy_counter_start_wh"`. Exact names matter — later tasks and live verification rely on them.

---

### Task 1: Test environment setup + baseline

**Files:**
- Create: `.venv/` (not committed; already gitignored via global ignores — verify `git status` stays clean)

**Interfaces:**
- Produces: working pytest environment; command shape `.venv/Scripts/python -m pytest tests/<file> -v` used by all later tasks.

- [ ] **Step 1: Create venv and install dependencies**

```bash
cd /d/kdj_nas01/work/ClaudeProjekte/ha_washdata
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install pytest pytest-asyncio pytest-homeassistant-custom-component numpy scipy
```

`pytest-homeassistant-custom-component` pins a matching `homeassistant` version and the pytest plugins the suite needs (`pytest.ini` sets `asyncio_mode = auto` and default marker filter `-m "not slow and not benchmark"`).

- [ ] **Step 2: Baseline run of an existing manager test file**

Run: `.venv/Scripts/python -m pytest tests/test_manager_event_payload_and_ghosts.py -v`
Expected: all tests PASS. If an `ImportError` names a missing package, `pip install` exactly that package into the venv and rerun. Do not proceed until this file is green — it exercises the same fixtures our new tests use.

- [ ] **Step 3: Verify git stays clean**

Run: `git status --porcelain`
Expected: empty output (if `.venv/` shows up, add a `.venv/` line to `.git/info/exclude` — NOT to the repo's `.gitignore`).

---

### Task 2: `CONF_ENERGY_SENSOR` + meter reading helper in the manager

**Files:**
- Modify: `custom_components/ha_washdata/const.py:6` (after `CONF_POWER_SENSOR`)
- Modify: `custom_components/ha_washdata/manager.py` (import at line 24, `__init__` near line 312, new property + helper near the `cycle_start_time` property at ~line 4254)
- Test: `tests/test_energy_sensor.py` (create)

**Interfaces:**
- Consumes: existing manager test fixture pattern from `tests/test_manager_event_payload_and_ghosts.py`.
- Produces:
  - `CONF_ENERGY_SENSOR: str = "energy_sensor"` in `const.py`
  - `WashDataManager.energy_sensor_entity_id -> str | None` (property, reads live from `self.config_entry` so `async_reload_config`'s entry swap picks up changes without extra code)
  - `WashDataManager._read_energy_counter_wh() -> float | None`
  - `WashDataManager._energy_counter_start_wh: float | None` (instance attribute, initialized `None`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_energy_sensor.py`:

```python
"""Tests for the optional native energy meter (energy_sensor) feature."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.ha_washdata.const import CONF_ENERGY_SENSOR
from custom_components.ha_washdata.manager import WashDataManager

ENERGY_SENSOR = "sensor.test_energy"


def _build_manager(hass: HomeAssistant, entry: Any) -> WashDataManager:
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    with patch("custom_components.ha_washdata.manager.ProfileStore"), patch(
        "custom_components.ha_washdata.manager.CycleDetector"
    ):
        mgr = WashDataManager(hass, entry)
        mgr.profile_store.get_suggestions = MagicMock(return_value={})
        return mgr


@pytest.fixture
def mock_entry() -> Any:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.title = "Test Washer"
    entry.options = {
        "power_sensor": "sensor.test_power",
        CONF_ENERGY_SENSOR: ENERGY_SENSOR,
    }
    entry.data = {}
    return entry


@pytest.fixture
def manager(hass: HomeAssistant, mock_entry: Any) -> WashDataManager:
    return _build_manager(hass, mock_entry)


async def test_read_energy_counter_normalizes_kwh(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "1.234", {"unit_of_measurement": "kWh"})
    assert manager._read_energy_counter_wh() == pytest.approx(1234.0)


async def test_read_energy_counter_wh_passthrough(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "250.5", {"unit_of_measurement": "Wh"})
    assert manager._read_energy_counter_wh() == pytest.approx(250.5)


async def test_read_energy_counter_normalizes_mwh(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "0.001", {"unit_of_measurement": "MWh"})
    assert manager._read_energy_counter_wh() == pytest.approx(1000.0)


async def test_read_energy_counter_unavailable_returns_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "unavailable", {"unit_of_measurement": "kWh"})
    assert manager._read_energy_counter_wh() is None


async def test_read_energy_counter_missing_unit_returns_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "1.0", {})
    assert manager._read_energy_counter_wh() is None


async def test_read_energy_counter_non_numeric_returns_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    hass.states.async_set(ENERGY_SENSOR, "abc", {"unit_of_measurement": "kWh"})
    assert manager._read_energy_counter_wh() is None


async def test_read_energy_counter_not_configured_returns_none(
    hass: HomeAssistant, mock_entry: Any
) -> None:
    mock_entry.options = {"power_sensor": "sensor.test_power"}
    mgr = _build_manager(hass, mock_entry)
    assert mgr.energy_sensor_entity_id is None
    assert mgr._read_energy_counter_wh() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v`
Expected: FAIL — `ImportError: cannot import name 'CONF_ENERGY_SENSOR'`.

- [ ] **Step 3: Implement**

`const.py` — insert directly after line 6 (`CONF_POWER_SENSOR = "power_sensor"`):

```python
CONF_ENERGY_SENSOR = "energy_sensor"  # Optional cumulative energy meter entity
```

`manager.py` line 24 — extend the existing import:

```python
from homeassistant.const import STATE_UNAVAILABLE, STATE_HOME, UnitOfEnergy
```

`manager.py` — add `CONF_ENERGY_SENSOR` to the `from .const import (...)` block (alphabetically near `CONF_DTW_BANDWIDTH`/`CONF_DEVICE_TYPE`; exact position irrelevant, must compile).

`manager.py` `__init__` — directly after line 312 (`self._cycle_start_time: datetime | None = None`):

```python
        # Native energy meter snapshot taken at cycle start (Wh); None = no
        # snapshot (feature unconfigured, meter unreadable, or no active cycle).
        self._energy_counter_start_wh: float | None = None
```

`manager.py` — add next to the `cycle_start_time` property (~line 4254):

```python
    # Map of supported cumulative-energy units to their Wh factor.
    _ENERGY_UNIT_TO_WH = {
        UnitOfEnergy.WATT_HOUR: 1.0,
        UnitOfEnergy.KILO_WATT_HOUR: 1000.0,
        UnitOfEnergy.MEGA_WATT_HOUR: 1_000_000.0,
    }

    @property
    def energy_sensor_entity_id(self) -> str | None:
        """Return the configured cumulative energy meter entity, if any."""
        return self.config_entry.options.get(
            CONF_ENERGY_SENSOR, self.config_entry.data.get(CONF_ENERGY_SENSOR)
        )

    def _read_energy_counter_wh(self) -> float | None:
        """Read the configured energy meter, normalized to Wh.

        Returns None whenever the value cannot be trusted (unconfigured,
        unavailable, non-numeric, or unsupported/missing unit) so callers
        fall back to the integrated power curve.
        """
        entity_id = self.energy_sensor_entity_id
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._logger.debug("Energy sensor %s is unavailable", entity_id)
            return None
        unit = state.attributes.get("unit_of_measurement")
        factor = self._ENERGY_UNIT_TO_WH.get(unit)
        if factor is None:
            self._logger.debug(
                "Energy sensor %s has unsupported unit %r", entity_id, unit
            )
            return None
        try:
            return float(state.state) * factor
        except (TypeError, ValueError):
            self._logger.debug(
                "Energy sensor %s has non-numeric state %r", entity_id, state.state
            )
            return None
```

Note: `STATE_UNKNOWN` is already imported from `.const` (manager.py line 200) and equals `"unknown"` — do not re-import it from `homeassistant.const`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_washdata/const.py custom_components/ha_washdata/manager.py tests/test_energy_sensor.py
git commit -m "feat: add energy_sensor config key and meter reading helper

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Cycle-end override — meter delta replaces integrated energy

**Files:**
- Modify: `custom_components/ha_washdata/manager.py` (`_on_cycle_end` ~lines 2673–2738; new helper `_compute_meter_energy_wh` next to `_read_energy_counter_wh`)
- Test: `tests/test_energy_sensor.py` (extend)

**Interfaces:**
- Consumes: `_read_energy_counter_wh()`, `_energy_counter_start_wh` (Task 2).
- Produces:
  - `WashDataManager._compute_meter_energy_wh(start_wh: float | None) -> float | None`
  - `cycle_data["energy_source"]: str` — `"meter"` or `"integration"`, set on every stored cycle
  - `_energy_counter_start_wh` is consumed (reset to `None`) on every `_on_cycle_end` entry, including ghost paths.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_energy_sensor.py`:

```python
def _cycle_data() -> dict[str, Any]:
    # 100 W flat for 1 h -> integrated energy exactly 100.0 Wh
    return {
        "id": "cycle-1",
        "start_time": "2026-01-01T10:00:00+00:00",
        "duration": 5460,
        "max_power": 2000,
        "status": "completed",
        "power_data": [[0.0, 100.0], [3600.0, 100.0]],
    }


async def test_cycle_end_uses_meter_delta(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 353087.0
    hass.states.async_set(ENERGY_SENSOR, "353.527", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(440.0)
    assert cycle_data["energy_source"] == "meter"
    assert manager._energy_counter_start_wh is None


async def test_cycle_end_meter_reset_falls_back_to_integration(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 500.0
    # 0.1 kWh = 100 Wh < 500 Wh start -> negative delta -> meter reset assumed
    hass.states.async_set(ENERGY_SENSOR, "0.1", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"


async def test_cycle_end_meter_unavailable_falls_back_to_integration(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = 500.0
    hass.states.async_set(ENERGY_SENSOR, "unavailable", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"


async def test_cycle_end_without_start_snapshot_keeps_integration(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager._async_process_cycle_end = AsyncMock()
    manager._energy_counter_start_wh = None
    hass.states.async_set(ENERGY_SENSOR, "353.527", {"unit_of_measurement": "kWh"})
    cycle_data = _cycle_data()

    manager._on_cycle_end(cycle_data)
    await hass.async_block_till_done()

    assert cycle_data["energy_wh"] == pytest.approx(100.0)
    assert cycle_data["energy_source"] == "integration"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v -k cycle_end`
Expected: 4 FAIL — `KeyError: 'energy_source'` (and 440.0 vs 100.0 on the first).

- [ ] **Step 3: Implement**

`manager.py`, `_on_cycle_end` — directly after line 2676 (`max_power = cycle_data.get("max_power", 0)`), insert:

```python
        # Consume the meter start snapshot up front so every exit path
        # (including ghost/noise cycles) clears it for the next cycle.
        meter_start_wh = self._energy_counter_start_wh
        self._energy_counter_start_wh = None
```

Replace line 2735 (`cycle_data["energy_wh"] = round(cycle_energy_wh, 3)`) and its comment with:

```python
        # Store energy for notification and persistence. The integrated value
        # (calculated above) also feeds the ghost checks; when a native energy
        # meter is configured and plausible, its delta wins for the stored value.
        cycle_data["energy_wh"] = round(cycle_energy_wh, 3)
        cycle_data["energy_source"] = "integration"
        meter_wh = self._compute_meter_energy_wh(meter_start_wh)
        if meter_wh is not None:
            cycle_data["energy_wh"] = round(meter_wh, 3)
            cycle_data["energy_source"] = "meter"
```

Add helper directly after `_read_energy_counter_wh` (from Task 2):

```python
    def _compute_meter_energy_wh(self, start_wh: float | None) -> float | None:
        """Return cycle energy from the native meter delta, or None to fall back."""
        if start_wh is None:
            return None
        end_wh = self._read_energy_counter_wh()
        if end_wh is None:
            self._logger.debug(
                "Energy meter unreadable at cycle end; using integrated energy"
            )
            return None
        delta_wh = end_wh - start_wh
        if delta_wh < 0:
            self._logger.info(
                "Energy meter went backwards during cycle (%.3f -> %.3f Wh); "
                "meter reset assumed, using integrated energy",
                start_wh,
                end_wh,
            )
            return None
        return delta_wh
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v`
Expected: 11 PASS.

- [ ] **Step 5: Regression check on neighboring behavior**

Run: `.venv/Scripts/python -m pytest tests/test_manager_event_payload_and_ghosts.py tests/test_manager.py -v`
Expected: PASS (ghost detection still uses the integrated value; event payload unchanged).

- [ ] **Step 6: Commit**

```bash
git add custom_components/ha_washdata/manager.py tests/test_energy_sensor.py
git commit -m "feat: prefer native energy meter delta for cycle energy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Start snapshot + persistence across HA restarts

**Files:**
- Modify: `custom_components/ha_washdata/manager.py` (new-cycle block ~line 2609; snapshot builders at ~1813–1821 and ~2129–2137; restore block after ~line 1254 in `_attempt_state_restoration`)
- Test: `tests/test_energy_sensor.py` (extend)

**Interfaces:**
- Consumes: `_read_energy_counter_wh()` (Task 2).
- Produces: snapshot dict key `"energy_counter_start_wh"` (float | None) written by both snapshot-save sites and restored by `_attempt_state_restoration`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_energy_sensor.py` (extend the imports at the top of the file: add `from homeassistant.util import dt as dt_util` and `from custom_components.ha_washdata.const import STATE_RUNNING`):

```python
async def test_check_state_save_includes_meter_snapshot(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    manager.detector.get_state_snapshot = MagicMock(return_value={})
    manager.profile_store.async_save_active_cycle = AsyncMock()
    manager._energy_counter_start_wh = 1234.5
    manager._last_state_save = None

    manager._check_state_save(dt_util.now())
    await hass.async_block_till_done()

    snapshot = manager.profile_store.async_save_active_cycle.call_args[0][0]
    assert snapshot["energy_counter_start_wh"] == 1234.5


async def test_restoration_restores_meter_snapshot(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    snapshot = {
        "state": STATE_RUNNING,
        "notified_start": True,
        "energy_counter_start_wh": 4321.0,
    }
    manager.profile_store.get_active_cycle = MagicMock(return_value=snapshot)
    manager.profile_store.get_last_active_save = MagicMock(return_value=dt_util.now())
    manager.profile_store.get_profiles = MagicMock(return_value={})
    manager.detector.restore_state_snapshot = MagicMock()
    manager.detector.state = STATE_RUNNING

    await manager._attempt_state_restoration()

    assert manager._energy_counter_start_wh == pytest.approx(4321.0)
    assert manager._notified_start is True


async def test_restoration_without_meter_key_leaves_none(
    hass: HomeAssistant, manager: WashDataManager
) -> None:
    snapshot = {"state": STATE_RUNNING, "notified_start": False}
    manager.profile_store.get_active_cycle = MagicMock(return_value=snapshot)
    manager.profile_store.get_last_active_save = MagicMock(return_value=dt_util.now())
    manager.profile_store.get_profiles = MagicMock(return_value={})
    manager.detector.restore_state_snapshot = MagicMock()
    manager.detector.state = STATE_RUNNING

    await manager._attempt_state_restoration()

    assert manager._energy_counter_start_wh is None
```

Note: `_attempt_state_restoration` has a broad `except Exception` around the restore branch — if the first two assertions fail unexpectedly, check test output for a logged restore error before assuming the feature code is wrong.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v -k "snapshot or restoration"`
Expected: `test_check_state_save_includes_meter_snapshot` FAIL with `KeyError: 'energy_counter_start_wh'`; `test_restoration_restores_meter_snapshot` FAIL on the `4321.0` assertion (attribute stays `None`). `test_restoration_without_meter_key_leaves_none` may already pass — that is fine.

- [ ] **Step 3: Implement**

`manager.py` new-cycle block — directly after line 2609 (`self._cycle_start_time = self.detector.current_cycle_start or dt_util.now()`):

```python
                self._energy_counter_start_wh = self._read_energy_counter_wh()
```

Both snapshot builders — after `snapshot["total_user_paused_seconds"] = self._total_user_paused_seconds` (site 1: ~line 1821 in the shutdown save; site 2: ~line 2137 in `_check_state_save`), add the same line at both sites:

```python
            snapshot["energy_counter_start_wh"] = self._energy_counter_start_wh
```

(Indentation differs per site — match the surrounding `snapshot[...]` lines.)

Restore — in `_attempt_state_restoration`, directly after the `self._total_user_paused_seconds = float(...)` block (~line 1254), inside the same `if self.detector.state in (...)` branch:

```python
                    _meter_raw = active_snapshot_to_restore.get(
                        "energy_counter_start_wh"
                    )
                    self._energy_counter_start_wh = (
                        float(_meter_raw)
                        if isinstance(_meter_raw, (int, float))
                        else None
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v`
Expected: 14 PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/ha_washdata/manager.py tests/test_energy_sensor.py
git commit -m "feat: snapshot energy meter at cycle start and persist across restarts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Config flow — setup step + settings step + clear handling

**Files:**
- Modify: `custom_components/ha_washdata/config_flow.py` (import block; `STEP_USER_DATA_SCHEMA` ~line 222; `async_step_settings` schema ~line 560 and submit handler ~line 495)
- Test: `tests/test_energy_sensor.py` (extend)

**Interfaces:**
- Consumes: `CONF_ENERGY_SENSOR` (Task 2).
- Produces: `energy_sensor` storable via initial setup and editable/clearable in the basic settings step; cleared selector saves as `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_energy_sensor.py`:

```python
def test_user_schema_accepts_optional_energy_sensor() -> None:
    from custom_components.ha_washdata.config_flow import STEP_USER_DATA_SCHEMA

    base = {
        "name": "Washer",
        "device_type": "washing_machine",
        "power_sensor": "sensor.p",
        "min_power": 2.0,
    }
    with_sensor = STEP_USER_DATA_SCHEMA({**base, "energy_sensor": "sensor.e"})
    assert with_sensor["energy_sensor"] == "sensor.e"

    without_sensor = STEP_USER_DATA_SCHEMA(dict(base))
    assert "energy_sensor" not in without_sensor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py::test_user_schema_accepts_optional_energy_sensor -v`
Expected: FAIL — voluptuous rejects the extra `energy_sensor` key (`extra keys not allowed`).

- [ ] **Step 3: Implement**

`config_flow.py` — add `CONF_ENERGY_SENSOR` to the `from .const import (...)` block (near `CONF_POWER_SENSOR`, line 27).

`STEP_USER_DATA_SCHEMA` — insert after the `CONF_POWER_SENSOR` entry (lines 222–224):

```python
        vol.Optional(CONF_ENERGY_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="energy"),
        ),
```

`async_step_settings` schema (~line 560) — insert after the `CONF_POWER_SENSOR` entry (lines 560–563):

```python
            vol.Optional(
                CONF_ENERGY_SENSOR,
                description={"suggested_value": get_val(CONF_ENERGY_SENSOR, None)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
```

`async_step_settings` submit handler — directly after line 495 (`if user_input is not None:`), before the `CONF_SHOW_ADVANCED` branch:

```python
            # A cleared EntitySelector omits its key entirely; normalize to None
            # so the merge below cannot resurrect the previously stored value.
            user_input[CONF_ENERGY_SENSOR] = user_input.get(CONF_ENERGY_SENSOR) or None
```

(This step's form always shows the field, so a missing key means the user cleared it — mirrors the `CONF_DOOR_SENSOR_ENTITY` normalization pattern at line 1028, adapted for a step where the field is always rendered.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_energy_sensor.py -v`
Expected: 15 PASS.

- [ ] **Step 5: Config-flow regression check**

Run: `.venv/Scripts/python -m pytest tests/test_integration_flow.py tests/test_migration.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/ha_washdata/config_flow.py tests/test_energy_sensor.py
git commit -m "feat: expose energy_sensor in setup and settings flows

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Strings, translations, README, CHANGELOG

**Files:**
- Modify: `custom_components/ha_washdata/strings.json` (config.step.user + options.step.settings)
- Modify: `custom_components/ha_washdata/translations/en.json` (same two spots — file mirrors strings.json)
- Modify: `README.md`
- Modify: `CHANGELOG.md` (new top section)

**Interfaces:**
- Consumes: field key `energy_sensor` (Task 5).
- Produces: user-visible labels/descriptions; release notes used by Task 7's release.

- [ ] **Step 1: strings.json — user step**

In `config.step.user.data`, after `"power_sensor": "Power Sensor",` add:

```json
  "energy_sensor": "Energy Sensor (optional)",
```

In `config.step.user.data_description`, after the `power_sensor` entry add:

```json
  "energy_sensor": "Optional: a cumulative energy sensor (Wh/kWh) from the same smart plug. When set, cycle energy is taken from this meter's start/end delta instead of integrating the power curve — much more accurate on plugs that report power only on change.",
```

- [ ] **Step 2: strings.json — settings step**

In `options.step.settings.data`, after `"power_sensor": "Power Sensor Entity",` add:

```json
  "energy_sensor": "Energy Sensor Entity (optional)",
```

If `options.step.settings` has a `data_description` object, add the same description text there under `"energy_sensor"`; if it has none, create the `data_description` object with just this key, following the structure used by `config.step.user`.

- [ ] **Step 3: translations/en.json — mirror both changes**

Apply the exact same four additions at the same JSON paths in `custom_components/ha_washdata/translations/en.json`.

Validate both files: `.venv/Scripts/python -c "import json; json.load(open('custom_components/ha_washdata/strings.json', encoding='utf-8')); json.load(open('custom_components/ha_washdata/translations/en.json', encoding='utf-8')); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: README**

Find the section describing configuration/setup (search for `Power Sensor` or `power_sensor` in `README.md`) and add this paragraph after the power-sensor description (as its own subsection if the README uses subsections there):

```markdown
### Energy Sensor (optional)

If your smart plug also exposes a cumulative energy counter (`Wh`/`kWh`, e.g.
Shelly `..._energy` or Tasmota `..._total`), you can select it as the optional
**Energy Sensor**. WashData then computes each cycle's energy from the meter's
start/end delta instead of integrating the power curve. Plugs report power
on-change, so integration systematically misses peaks between reports —
real-world deviations of ~20 % are common; the on-device meter does not have
this problem. The integrated value remains the automatic fallback whenever the
meter is unavailable or resets mid-cycle, and each stored cycle records its
source in `energy_source` (`meter` or `integration`). Note: cycles from the
manual recorder and cycles trimmed afterwards keep trace-based energy, because
trimming changes the time window the meter delta was captured for.
```

- [ ] **Step 5: CHANGELOG**

Add at the very top of `CHANGELOG.md` (above the current latest section):

```markdown
## 0.4.5.9 (fork) - 2026-07-06

### ✨ Features
- **Optional Native Energy Meter Source**: A new optional **Energy Sensor** config (initial setup and Settings) accepts a cumulative energy entity (`Wh`/`kWh`/`MWh`). When set, `energy_wh` for a finished cycle is computed from the meter's start/end delta instead of integrating the report-on-change power curve, which systematically underestimates spiky loads (measured ~23 % low on a Shelly-metered washing machine). The integrated value remains the fallback (meter unconfigured, unavailable, non-numeric, unsupported unit, or counter reset mid-cycle) and continues to feed ghost-cycle/pump-out detection unchanged. Each stored cycle carries `energy_source` (`meter`/`integration`). The start snapshot is persisted with the active cycle, surviving Home Assistant restarts.

<br>
```

- [ ] **Step 6: Commit**

```bash
git add custom_components/ha_washdata/strings.json custom_components/ha_washdata/translations/en.json README.md CHANGELOG.md
git commit -m "docs: document optional energy_sensor source

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full suite, version bump, fork release

**Files:**
- Modify: `custom_components/ha_washdata/manifest.json:19` (`"version"`)

**Interfaces:**
- Consumes: everything above.
- Produces: fork `main` with the feature; GitHub release `v0.4.5.9` that HACS can install (must sort above upstream's `v0.4.5.2`).

- [ ] **Step 1: Run the full fast suite**

Run: `.venv/Scripts/python -m pytest tests/ -v --tb=short`
Expected: PASS (same result set as the pre-change baseline; `pytest.ini` already excludes slow/benchmark). If a failure also occurs on a clean checkout of `upstream/main`, note it and move on; a failure only on our branch must be fixed before proceeding.

- [ ] **Step 2: Bump manifest version**

`manifest.json` line 19: `"version": "0.4.5.1"` → `"version": "0.4.5.9"`.
(Upstream's latest release is `v0.4.5.2`; `0.4.5.9` sorts above it and below a future `0.4.6`, so HACS treats the fork as newer until upstream ships 0.4.6.)

```bash
git add custom_components/ha_washdata/manifest.json
git commit -m "chore: bump version to 0.4.5.9 for fork release

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push branch, merge to fork main, tag, release**

```bash
git push -u origin feature/energy-sensor
git checkout main
git merge --no-ff feature/energy-sensor -m "Merge feature/energy-sensor: optional native energy meter source

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push origin main
gh release create v0.4.5.9 --repo kdjkdjkdj/ha_washdata --target main \
  --title "v0.4.5.9 (fork): optional native energy meter source" \
  --notes "Fork release for testing. See CHANGELOG section 0.4.5.9. Upstream: 3dg1luk43/ha_washdata."
```

Verify active gh account is `kdjkdjkdj` first: `gh auth status`.
Expected: release visible at https://github.com/kdjkdjkdj/ha_washdata/releases/tag/v0.4.5.9

---

### Task 8: Deploy to HA-Tiny and verify live (manual/operational)

**Files:** none (operational task on HA-Tiny)

**Interfaces:**
- Consumes: fork release `v0.4.5.9` (Task 7).
- Produces: fork running on Tiny, `energy_sensor` configured on the washing machine, verification checklist for the next real cycle.

- [ ] **Step 1: Add fork as HACS custom repository on Tiny**

In HA-Tiny UI (user action, or via `HA-MCP-Tiny` `ha_manage_hacs` if it supports custom repositories): HACS → Custom repositories → `https://github.com/kdjkdjkdj/ha_washdata`, type Integration. Then update/redownload WashData → version `v0.4.5.9`. Restart HA. Config entries survive (same domain `ha_washdata`).

- [ ] **Step 2: Identify the washing machine plug's native energy entity**

Via `HA-MCP-Tiny` `ha_search` for the washing-machine Shelly plug's energy entity (cumulative kWh, `state_class: total_increasing` — reference value 2026-07-05 was ~353.5 kWh). Record the entity_id.

- [ ] **Step 3: Configure energy_sensor on the washing machine only**

WashData → Waschmaschine → Configure → Settings → set **Energy Sensor Entity** to the entity from Step 2 (UI action — WashData options are not writable via MCP). Trockner and Spülmaschine stay unset for now; HA-KD stays on upstream.

- [ ] **Step 4: Verification checklist for the next real cycle**

After the next washing-machine cycle on Tiny, check via `HA-MCP-Tiny`:
1. `ha_get_integration(entry_id="01KG5Q60PQP1DF3XNNV9Z7K6DH", include_diagnostics=True, diagnostics_data_path="data.store_export.data.past_cycles", diagnostics_data_limit=1, ...)` → newest cycle has `energy_source: "meter"`.
2. `energy_wh` matches the Shelly counter delta over the cycle window (compare against `ha_get_history` of the energy entity) within a few percent.
3. Notification arrived with the plausible (higher) kWh value.
4. No `ha_washdata` errors in `ha_get_logs(source="error_log", search="washdata")`.

If all four pass for 2–3 cycles: set `energy_sensor` on Trockner + Spülmaschine (Tiny). KD migration and the upstream PR are separate follow-ups (PR text must be approved in chat first; drop `docs/superpowers/**` commits and rebase onto `upstream/main`).
