"""Tests for blip-tolerant graceful timeout (profile-independent end)."""

from datetime import datetime, timezone, timedelta

from custom_components.ha_washdata.cycle_detector import (
    CycleDetector,
    CycleDetectorConfig,
    STATE_ENDING,
    STATE_FINISHED,
    STATE_ANTI_WRINKLE,
    DEVICE_TYPE_DRYER,
    DEVICE_TYPE_WASHING_MACHINE,
)
from custom_components.ha_washdata.const import DEVICE_TYPE_DISHWASHER


def dt(s: float) -> datetime:
    return datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=s)


def test_config_has_blip_tolerant_defaults():
    cfg = CycleDetectorConfig(min_power=5.0, off_delay=60, device_type=DEVICE_TYPE_DRYER)
    assert cfg.crease_resume_threshold == 400.0
    assert cfg.unmatched_off_delay == 1800


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
        # Isolate the behavior under test: cycle_detector.py also runs a
        # pre-existing, unrelated "energy gate" on the fallback-timeout path
        # (integrates power over a trailing off_delay-sized window of ALL
        # ENDING readings; see "Cycle ending prevented by energy gate" in
        # cycle_detector.py). Left at its tiny 0.05 Wh default, periodic
        # 170 W crease blips would keep that *separate* gate shut regardless
        # of whether the timer-reset bug this task fixes is present, making
        # the test unable to distinguish fixed from broken. Raising the
        # threshold takes that unrelated gate out of the equation so this
        # test exercises only the accumulator-reset behavior in scope here.
        end_energy_threshold=1000.0,
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
    # Fail-capable check: with anti_wrinkle_enabled=False, the new graceful-end
    # branch (cycle_detector.py ~1138-1162) must never fire, so the cycle can
    # only progress via the pre-existing fallback-timeout/energy-gate path.
    # That path's energy gate (default end_energy_threshold=0.05 Wh) stays
    # shut because each 170 W blip injects far more energy than 0.05 Wh into
    # its trailing off_delay-sized window, so the cycle never reaches
    # STATE_FINISHED either -- it is stuck in STATE_ENDING for the whole
    # probe window. Empirically verified (see task-5-report.md): with this
    # same config except anti_wrinkle_enabled=True, the identical power
    # sequence reaches STATE_ANTI_WRINKLE well before the loop bound, so this
    # equality assertion WOULD fail if the new branch's anti_wrinkle_enabled
    # guard were bypassed -- i.e. it is genuinely fail-capable, unlike the
    # previous `!= STATE_ANTI_WRINKLE` assertion (which the pre-existing
    # _finish_cycle gate at cycle_detector.py ~1491-1497 satisfies on its own).
    assert det.state == STATE_ENDING


def test_dishwasher_untouched_by_anti_crease_classifier():
    """Unsupported device types must never reach STATE_ANTI_WRINKLE via the
    crease-blip classifier, even with anti_wrinkle_enabled=True (structural
    device_type gate in _is_anti_crease_blip, not merely default-off)."""
    cfg = CycleDetectorConfig(
        min_power=5.0,
        off_delay=1800,
        device_type=DEVICE_TYPE_DISHWASHER,
        anti_wrinkle_enabled=True,
        crease_resume_threshold=1000.0,
        unmatched_off_delay=900,
        stop_threshold_w=2.0,
        start_threshold_w=5.0,
        end_energy_threshold=1000.0,
    )
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
    assert det.state != STATE_ANTI_WRINKLE
