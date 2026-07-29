# Development Tools

Note: Some examples use "washer" in topic/entity names, but the same tooling applies to other predictable-cycle appliances (e.g., dryers and dishwashers).

**See [../TESTING.md](../TESTING.md) for comprehensive documentation.**

All testing and mock socket documentation has been consolidated into [../TESTING.md](../TESTING.md):

- Mock socket reference & parameters
- Fault injection scenarios
- Testing procedures
- Debugging guide

## Quick Start

```bash
cd /root/ha_washdata/devtools
pip install paho-mqtt
python3 mqtt_mock_socket.py --speedup 720
```

In another terminal:
```bash
mosquitto_pub -t homeassistant/mock_washer_power/cmd -m 'LONG'
```

See [../TESTING.md#mock-socket-reference](../TESTING.md#mock-socket-reference) for full documentation.

---

## Diagnostic Analyser (`analyze_diag.py`)

Analyses a WashData diagnostic export (JSON) and compares the device's
**current settings** against **optimal settings derived from its own cycle
history**.  Uses the same heuristics as the in-HA suggestion engine but runs
fully offline - no Home Assistant required.

### Usage

```bash
# From the repository root with the venv activated:
source .venv/bin/activate

# Pass the export file as an argument
python3 devtools/analyze_diag.py path/to/diagnostics_export.json

# Or let it prompt you interactively
python3 devtools/analyze_diag.py

# Plain text output (no ANSI colours - good for CI or piping)
python3 devtools/analyze_diag.py --no-color export.json
```

### What it produces

| Section | Parameters analysed |
|---------|--------------------|
| **Power Thresholds** | `stop_threshold_w`, `start_threshold_w`, `running_dead_zone` |
| **Energy Gates** | `end_energy_threshold`, `start_energy_threshold` |
| **Timing & Operational** | `watchdog_interval`, `no_update_active_timeout`, `off_delay`, `min_off_gap`, `profile_match_interval` |
| **Matching & Learning** | `duration_tolerance`, `profile_duration_tolerance`, `min/max_duration_ratio` |

Each row shows the **current value**, **suggested value**, a **% change arrow**, and a one-line **rationale**.  A summary at the end lists how many parameters can be improved and where to apply them in the HA UI.

The report also surfaces any **suggestions already computed by live HA operation** (stored in `manager_state.suggestions` inside the export) alongside the offline analysis - useful for cross-checking.

A **Cycle History** table at the bottom lists every detected programme with its average duration, standard deviation, and coefficient of variation so you can immediately see which programmes are consistently recognised vs. which are noisy.

### How to get a diagnostic export

1. In Home Assistant go to **Settings → Devices & Services → WashData**.
2. Click the three-dot menu on the device card and choose **Download Diagnostics**.
3. Pass the downloaded `.json` file to `analyze_diag.py`.

---

## Alignment Verifier (`verify_alignment.py`)

Answers one question about a diagnostic export: **does the envelope alignment
behave?**  Runs the integration's *own* `ProfileStore.async_verify_alignment`
against the exported envelopes and recorded cycles - the DTW, the envelope and
the traces are the real ones, so a result is a statement about the shipped code
rather than about a model of it.

`mapped_time` - the value that decides whether a verified pause is released - is
a *position on the envelope's time grid*.  Three properties are checked per
profile:

| Check | Question | Fails when |
|---|---|---|
| **reachable** | Does the release threshold lie inside the envelope? | The threshold is derived from a number longer than the envelope span, so it sits past the last grid slot and can never be reached. |
| **tracks time** | Does a trace covering x % of the envelope map to x % along it? | Truncating a real recorded cycle at several fractions shows the mapping does not follow elapsed time. |
| **advances** | Does the position keep pace with the wall clock across a quiet tail? | Watchdog keepalives advance the position far slower than real time, so the release is not reached before the max-deferral cap force-ends the cycle. |

### Usage

```bash
source .venv/bin/activate
python3 devtools/verify_alignment.py <export.json>
```

Same input as `analyze_diag.py` - see *How to get a diagnostic export* above.
Exit code `0` if every check passed, `1` if any failed, `2` if nothing could be
checked (no envelope, or no recorded cycle carrying a power trace).

Profiles without an envelope, without an average duration, or without a labelled
cycle are reported as `SKIP` with the reason, so a partially-populated export
still produces a useful report.
