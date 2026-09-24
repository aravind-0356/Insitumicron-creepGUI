"""
test_creep_analysis.py
=======================
Unit tests and validation suite for execution/creep_analysis.py.
"""

import sys
import os
import math
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from creep_analysis import CreepAnalysisEngine, CreepAnalysisResult


def make_synthetic_creep_data(
    duration_hours: float = 100.0,
    n_points: int = 500,
    steady_rate_pct_hr: float = 0.015,
    hold_load_n: float = 500.0,
    with_rupture: bool = False
):
    """
    Generate synthetic classical 3-stage creep curve:
    Stage 1: Primary (decelerating strain rate)
    Stage 2: Secondary (constant linear steady-state creep rate)
    Stage 3: Tertiary (accelerating strain rate ending in rupture if specified)
    """
    time_hr = np.linspace(0.0, duration_hours, n_points)
    time_s = time_hr * 3600.0

    gauge_length = 50.0  # mm
    area = 25.0         # mm²

    # Strain components
    # 1. Instantaneous elastic + primary: 0.2% + 0.3% * (1 - exp(-t / 5))
    e_primary = 0.2 + 0.3 * (1.0 - np.exp(-time_hr / 8.0))
    # 2. Secondary linear: steady_rate_pct_hr * t
    e_secondary = steady_rate_pct_hr * time_hr
    # 3. Tertiary acceleration
    if with_rupture:
        t_tertiary = duration_hours * 0.75
        e_tertiary = np.where(time_hr > t_tertiary, 0.002 * np.clip(time_hr - t_tertiary, 0, None)**2.5, 0.0)
    else:
        e_tertiary = np.zeros_like(time_hr)

    strain_pct = e_primary + e_secondary + e_tertiary
    disp_mm = (strain_pct / 100.0) * gauge_length

    # Load with small random noise
    rng = np.random.default_rng(42)
    load_n = hold_load_n + rng.normal(0.0, 0.3, size=n_points)
    if with_rupture:
        # Load drops at the final 3 points
        load_n[-3:] = [hold_load_n * 0.5, hold_load_n * 0.2, 0.0]

    return time_s.tolist(), load_n.tolist(), disp_mm.tolist(), area, gauge_length


def test_standard_creep():
    print("[1/4] Testing standard 100-hour unruptured creep curve...")
    time_s, load_n, disp_mm, area, gauge = make_synthetic_creep_data(
        duration_hours=100.0, n_points=400, steady_rate_pct_hr=0.02, with_rupture=False
    )
    engine = CreepAnalysisEngine()
    res = engine.analyse(time_s, load_n, disp_mm, area, gauge)

    assert res.mean_stress_mpa > 19.5 and res.mean_stress_mpa < 20.5
    assert res.steady_state_rate_pct_per_hr > 0.015 and res.steady_state_rate_pct_per_hr < 0.025
    assert res.steady_state_r2 > 0.98, f"Expected high R², got {res.steady_state_r2}"
    assert not res.rupture_detected
    assert res.fit_line is not None
    print(f"  -> PASS (Rate: {res.steady_state_rate_pct_per_hr:.4f} %/hr, R²: {res.steady_state_r2:.4f})")


def test_rupture_detection():
    print("[2/4] Testing creep curve with tertiary acceleration & rupture...")
    time_s, load_n, disp_mm, area, gauge = make_synthetic_creep_data(
        duration_hours=150.0, n_points=500, steady_rate_pct_hr=0.01, with_rupture=True
    )
    engine = CreepAnalysisEngine()
    res = engine.analyse(time_s, load_n, disp_mm, area, gauge)

    assert res.rupture_detected, "Rupture should be detected on load drop"
    assert res.rupture_time_hr is not None
    assert res.rupture_time_hr > 140.0
    print(f"  -> PASS (Rupture at {res.rupture_time_hr:.1f} hr)")


def test_manual_override():
    print("[3/4] Testing manual steady-state window override...")
    time_s, load_n, disp_mm, area, gauge = make_synthetic_creep_data(
        duration_hours=80.0, n_points=300, steady_rate_pct_hr=0.015
    )
    engine = CreepAnalysisEngine()
    # Explicitly set steady-state to 25.0 hr -> 60.0 hr
    res = engine.analyse(time_s, load_n, disp_mm, area, gauge, manual_steady_state_range=(25.0, 60.0))

    assert abs(res.steady_state_start_hr - 25.0) < 1.0
    assert abs(res.steady_state_end_hr - 60.0) < 1.0
    assert res.steady_state_rate_pct_per_hr > 0.01 and res.steady_state_rate_pct_per_hr < 0.02
    print("  -> PASS")


def test_short_noisy_data():
    print("[4/5] Testing robustness with small/noisy dataset...")
    time_s = [i * 60.0 for i in range(12)]
    load_n = [500.0 + (i % 2) for i in range(12)]
    disp_mm = [0.1 + i * 0.005 for i in range(12)]

    engine = CreepAnalysisEngine()
    res = engine.analyse(time_s, load_n, disp_mm, area_mm2=20.0, gauge_length_mm=40.0)
    assert res.steady_state_rate_pct_per_hr >= 0.0
    assert not math.isnan(res.steady_state_r2)
    print("  -> PASS")


def test_negative_displacement_and_seating():
    print("[5/5] Testing negative displacement progression & seating noise (Y >= 0 guarantee)...")
    # Simulate a test that progresses in the negative direction (e.g. compression or inverted stroke)
    # with an initial negative seating jitter: 0.0 -> -0.005 -> -0.05 -> -0.2 -> -1.0 mm
    n_pts = 300
    time_hr = np.linspace(0.0, 100.0, n_pts)
    time_s = time_hr * 3600.0
    
    # Negative displacement curve (-0.015 %/hr rate)
    steady_rate = 0.015
    gauge = 50.0
    disp_mm = -((steady_rate * time_hr + 0.1 * (1.0 - np.exp(-time_hr / 5.0))) / 100.0) * gauge
    # Add a tiny seating dip at index 1
    disp_mm[1] = -0.002
    load_n = [500.0] * n_pts

    engine = CreepAnalysisEngine()
    res = engine.analyse(time_s.tolist(), load_n, disp_mm.tolist(), area_mm2=25.0, gauge_length_mm=gauge)

    # 1. Guarantee every single point is Y >= 0
    assert all(s >= 0.0 for s in res.strain_pct), f"Found negative strain: {min(res.strain_pct)}"
    assert res.strain_pct[0] == 0.0, f"Expected start at 0.0, got {res.strain_pct[0]}"
    
    # 2. Guarantee steady-state rate is positive and accurately calculated
    assert res.steady_state_rate_pct_per_hr > 0.012 and res.steady_state_rate_pct_per_hr < 0.018, \
        f"Rate should be ~0.015, got {res.steady_state_rate_pct_per_hr}"
    assert res.steady_state_r2 > 0.98, f"Expected high R², got {res.steady_state_r2}"
    
    # 3. Warning was recorded
    assert any("negative direction" in w.lower() for w in res.warnings)
    print(f"  -> PASS (Inverted Rate: {res.steady_state_rate_pct_per_hr:.4f} %/hr, Min Y: {min(res.strain_pct):.4f}%)")


def run_all():
    print("=" * 60)
    print("RUNNING CREEP ANALYSIS ENGINE TEST SUITE")
    print("=" * 60)
    test_standard_creep()
    test_rupture_detection()
    test_manual_override()
    test_short_noisy_data()
    test_negative_displacement_and_seating()
    print("=" * 60)
    print("ALL CREEP ANALYSIS ENGINE TESTS PASSED [5/5]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
