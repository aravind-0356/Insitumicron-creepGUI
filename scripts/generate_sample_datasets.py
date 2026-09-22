"""
generate_sample_datasets.py
===========================
Generates realistic dummy test CSV datasets for testing the Insitumicron GUI
Analysis & Reporting engines:
1. creep_100hr_standard.csv       - Standard 100-hour unruptured creep test (steady-state rate ~0.018 %/hr)
2. creep_150hr_rupture.csv        - 3-stage creep test with tertiary acceleration and rupture at ~148 hr
3. creep_500hr_extended.csv       - Extended 500-hour long-duration creep test
4. tensile_mild_steel.csv         - Monotonic tensile test of Mild Steel (E ≈ 205 GPa, Yield ≈ 245 MPa, UTS ≈ 370 MPa)
5. tensile_aluminum_6061.csv      - Monotonic tensile test of Al 6061-T6 (E ≈ 70 GPa, 0.2% Yield ≈ 275 MPa, UTS ≈ 310 MPa)
"""

import os
import csv
import numpy as np

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def generate_creep_100hr_standard():
    path = os.path.join(OUTPUT_DIR, "creep_100hr_standard.csv")
    duration_hr = 100.0
    n_pts = 600
    gauge_length = 50.0  # mm
    area = 25.0         # mm² (20 MPa hold stress at 500 N)
    hold_load = 500.0   # N

    time_hr = np.linspace(0.0, duration_hr, n_pts)
    time_s = time_hr * 3600.0

    # Strain: primary (0.15% transient) + steady-state (0.018 %/hr)
    e_primary = 0.15 * (1.0 - np.exp(-time_hr / 6.0))
    e_secondary = 0.018 * time_hr
    strain_pct = e_primary + e_secondary
    disp_mm = (strain_pct / 100.0) * gauge_length

    # Load: constant with realistic noise (±0.25 N)
    rng = np.random.default_rng(101)
    load_n = hold_load + rng.normal(0.0, 0.25, size=n_pts)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for t, l, d in zip(time_s, load_n, disp_mm):
            writer.writerow([f"{t:.3f}", f"{l:.3f}", f"{d:.4f}"])

    print(f"[+] Created {path} ({n_pts} rows, 100.0 hr)")


def generate_creep_150hr_rupture():
    path = os.path.join(OUTPUT_DIR, "creep_150hr_rupture.csv")
    duration_hr = 150.0
    n_pts = 900
    gauge_length = 50.0
    hold_load = 650.0   # N

    time_hr = np.linspace(0.0, duration_hr, n_pts)
    time_s = time_hr * 3600.0

    # Primary stage
    e_primary = 0.20 * (1.0 - np.exp(-time_hr / 8.0))
    # Secondary stage (~0.022 %/hr)
    e_secondary = 0.022 * time_hr
    # Tertiary stage starting around 115 hr
    t_tertiary = 115.0
    dt_tert = np.clip(time_hr - t_tertiary, 0.0, None)
    e_tertiary = 0.0004 * (dt_tert ** 2.6)

    strain_pct = e_primary + e_secondary + e_tertiary
    disp_mm = (strain_pct / 100.0) * gauge_length

    # Load: drops abruptly at rupture (~148 hr)
    rng = np.random.default_rng(202)
    load_n = hold_load + rng.normal(0.0, 0.3, size=n_pts)
    
    # Rupture drop in last 8 points
    rupture_idx = n_pts - 8
    for i in range(rupture_idx, n_pts):
        load_n[i] = hold_load * max(0.0, (1.0 - (i - rupture_idx + 1) * 0.15))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for t, l, d in zip(time_s, load_n, disp_mm):
            writer.writerow([f"{t:.3f}", f"{l:.3f}", f"{d:.4f}"])

    print(f"[+] Created {path} ({n_pts} rows, Ruptured at ~148 hr)")


def generate_creep_500hr_extended():
    path = os.path.join(OUTPUT_DIR, "creep_500hr_extended.csv")
    duration_hr = 500.0
    n_pts = 1200
    gauge_length = 50.0
    hold_load = 400.0   # N

    time_hr = np.linspace(0.0, duration_hr, n_pts)
    time_s = time_hr * 3600.0

    # Low creep rate: 0.004 %/hr
    e_primary = 0.10 * (1.0 - np.exp(-time_hr / 15.0))
    e_secondary = 0.004 * time_hr
    strain_pct = e_primary + e_secondary
    disp_mm = (strain_pct / 100.0) * gauge_length

    rng = np.random.default_rng(303)
    load_n = hold_load + rng.normal(0.0, 0.2, size=n_pts)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for t, l, d in zip(time_s, load_n, disp_mm):
            writer.writerow([f"{t:.3f}", f"{l:.3f}", f"{d:.4f}"])

    print(f"[+] Created {path} ({n_pts} rows, 500.0 hr)")


def generate_tensile_mild_steel():
    path = os.path.join(OUTPUT_DIR, "tensile_mild_steel.csv")
    n_pts = 500
    gauge = 50.0    # mm
    area = 37.5     # mm² (12.5 mm x 3.0 mm)
    E = 205_000.0   # MPa
    yield_s = 245.0 # MPa
    uts_s = 370.0   # MPa
    break_s = 280.0 # MPa
    
    e_yield = yield_s / E
    e_uts = 0.18
    e_break = 0.24

    strain = np.linspace(0.0, e_break, n_pts)
    stress = np.zeros(n_pts)

    for i, e in enumerate(strain):
        if e <= e_yield:
            stress[i] = E * e
        elif e <= e_uts:
            # Ludwik power law hardening
            stress[i] = yield_s + (uts_s - yield_s) * ((e - e_yield) / (e_uts - e_yield)) ** 0.35
        else:
            # Necking post UTS
            stress[i] = uts_s - (uts_s - break_s) * ((e - e_uts) / (e_break - e_uts)) ** 1.5

    disp_mm = strain * gauge
    load_n = stress * area
    time_s = np.linspace(0.0, 120.0, n_pts)

    rng = np.random.default_rng(404)
    load_n += rng.normal(0.0, 1.5, size=n_pts)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for t, l, d in zip(time_s, load_n, disp_mm):
            writer.writerow([f"{t:.3f}", f"{l:.3f}", f"{d:.4f}"])

    print(f"[+] Created {path} (Mild Steel Tensile, {n_pts} rows)")


def generate_tensile_aluminum_6061():
    path = os.path.join(OUTPUT_DIR, "tensile_aluminum_6061.csv")
    n_pts = 500
    gauge = 50.0
    area = 30.0     # mm² (10.0 mm x 3.0 mm)
    E = 69_000.0    # MPa
    yield_02 = 275.0 # MPa
    uts_s = 310.0   # MPa
    break_s = 250.0 # MPa
    
    e_break = 0.13
    strain = np.linspace(0.0, e_break, n_pts)
    stress = np.zeros(n_pts)

    # Ramberg-Osgood approximation
    for i, e in enumerate(strain):
        if e < 0.004:
            stress[i] = E * e
        elif e < 0.09:
            stress[i] = 270.0 + 40.0 * ((e - 0.004) / 0.086) ** 0.4
        else:
            stress[i] = 310.0 - 60.0 * ((e - 0.09) / (e_break - 0.09)) ** 1.2

    disp_mm = strain * gauge
    load_n = stress * area
    time_s = np.linspace(0.0, 80.0, n_pts)

    rng = np.random.default_rng(505)
    load_n += rng.normal(0.0, 1.0, size=n_pts)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for t, l, d in zip(time_s, load_n, disp_mm):
            writer.writerow([f"{t:.3f}", f"{l:.3f}", f"{d:.4f}"])

    print(f"[+] Created {path} (Al 6061-T6 Tensile, {n_pts} rows)")


if __name__ == "__main__":
    print("=" * 60)
    print("GENERATING SAMPLE CSV DATASETS")
    print("=" * 60)
    generate_creep_100hr_standard()
    generate_creep_150hr_rupture()
    generate_creep_500hr_extended()
    generate_tensile_mild_steel()
    generate_tensile_aluminum_6061()
    print("=" * 60)
    print(f"All sample datasets saved to: {OUTPUT_DIR}")
    print("=" * 60)
