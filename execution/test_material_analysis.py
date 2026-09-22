"""
test_material_analysis.py
==========================
Stand-alone test harness for material_analysis.py.

Generates three synthetic stress-strain datasets that mimic real materials
(mild steel, aluminium 6061-T6, polypropylene) and prints analysis results.

Run from the project root:
    python execution/test_material_analysis.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))   # ensure execution/ is on path

import numpy as np

from material_analysis import MaterialAnalysisEngine


# ---------------------------------------------------------------------------
# Synthetic test data generators
# ---------------------------------------------------------------------------

def make_steel_data(n_points: int = 500, noise_level: float = 0.5):
    """
    Mild Steel (S235) — sharp yield point, clear necking, fracture at ~25% strain.
      E     ≈ 210 000 MPa
      Yield ≈ 235 MPa  (0.2% offset)
      UTS   ≈ 360 MPa
    """
    rng = np.random.default_rng(42)

    gauge   = 50.0      # mm
    width   = 12.5      # mm
    thick   = 3.0       # mm
    area    = width * thick  # 37.5 mm2

    E        = 210_000.0   # MPa
    yield_s  = 235.0       # MPa
    uts_s    = 360.0       # MPa
    uts_e    = 0.20        # strain at UTS
    break_e  = 0.25        # fracture strain

    strain = np.linspace(0, break_e, n_points)

    plastic_base = np.clip(
        (strain - yield_s / E) / (uts_e - yield_s / E), 0, None
    )
    stress = np.where(
        strain < yield_s / E,
        E * strain,
        yield_s + (uts_s - yield_s) * plastic_base ** 0.3
    )

    # Softening / necking after UTS
    uts_idx = np.argmax(stress)
    for i in range(uts_idx, n_points):
        drop = (strain[i] - uts_e) / (break_e - uts_e)
        stress[i] = uts_s * (1 - 0.85 * drop ** 1.5)

    # Toe region (grip seating) at the very start
    stress[:15] *= np.linspace(0.3, 1.0, 15)

    # Add realistic noise
    stress += rng.normal(0, noise_level, n_points)
    stress = np.clip(stress, 0, None)

    displacement = strain * gauge
    load         = stress * area

    return load.tolist(), displacement.tolist(), area, gauge, "Mild Steel (S235)"


def make_aluminium_data(n_points: int = 400, noise_level: float = 0.3):
    """
    Aluminium 6061-T6 — no sharp yield, gradual curve, fracture ~12% strain.
      E     ≈ 69 000 MPa
      Yield ≈ 276 MPa  (0.2% offset)
      UTS   ≈ 310 MPa
    """
    rng = np.random.default_rng(7)

    gauge  = 50.0
    radius = 6.0            # circular cross-section
    area   = np.pi * radius ** 2  # ≈ 113.1 mm2

    E        = 69_000.0
    yield_s  = 276.0
    uts_s    = 310.0
    uts_e    = 0.10
    break_e  = 0.12

    strain = np.linspace(0, break_e, n_points)

    # Ramberg-Osgood approximation
    n = 25  # strain-hardening exponent
    stress = np.zeros(n_points)
    for i, e in enumerate(strain):
        # Solve σ/E + 0.002*(σ/yield_s)^n = e  (simplified Newton)
        s = e * E
        for _ in range(30):
            f  = s / E + 0.002 * (s / yield_s) ** n - e
            df = 1 / E + 0.002 * n * (s / yield_s) ** (n - 1) / yield_s
            s -= f / df
            if abs(f) < 1e-6:
                break
        stress[i] = max(0, min(s, uts_s * 1.05))

    # Fracture drop
    uts_idx = np.argmax(stress)
    for i in range(uts_idx, n_points):
        drop = (strain[i] - uts_e) / (break_e - uts_e + 1e-9)
        stress[i] = uts_s * (1 - 0.90 * drop ** 2)

    stress[:10] *= np.linspace(0.2, 1.0, 10)
    stress += rng.normal(0, noise_level, n_points)
    stress  = np.clip(stress, 0, None)

    displacement = strain * gauge
    load         = stress * area

    return load.tolist(), displacement.tolist(), area, gauge, "Aluminium 6061-T6"


def make_polymer_data(n_points: int = 600, noise_level: float = 0.1):
    """
    Polypropylene — low modulus, large plastic deformation, no clear fracture.
      E     ≈ 1 400 MPa
      Yield ≈ 30 MPa
      UTS   ≈ 35 MPa
    """
    rng = np.random.default_rng(13)

    gauge = 50.0
    width = 10.0
    thick = 4.0
    area  = width * thick  # 40 mm2

    E       = 1_400.0
    yield_s = 30.0
    uts_s   = 35.0
    uts_e   = 0.40
    break_e = 0.60

    strain = np.linspace(0, break_e, n_points)

    plastic_base2 = np.clip(
        (strain - yield_s / E) / (uts_e - yield_s / E + 1e-9), 0, None
    )
    stress = np.where(
        strain < yield_s / E,
        E * strain,
        yield_s + (uts_s - yield_s) * plastic_base2 ** 0.2
    )

    uts_idx = np.argmax(stress)
    for i in range(uts_idx, n_points):
        drop = (strain[i] - uts_e) / (break_e - uts_e + 1e-9)
        stress[i] = uts_s * (1 - 0.70 * drop)

    stress[:20] *= np.linspace(0.1, 1.0, 20)
    stress += rng.normal(0, noise_level, n_points)
    stress  = np.clip(stress, 0, None)

    displacement = strain * gauge
    load         = stress * area

    return load.tolist(), displacement.tolist(), area, gauge, "Polypropylene"


# ---------------------------------------------------------------------------
# Plot helper
# ---------------------------------------------------------------------------

def plot_result(ax, result, title):
    strain = np.array(result.strain)
    stress = np.array(result.stress)

    # Main curve
    ax.plot(strain, stress, color='#2c3e50', linewidth=2.0, label='Stress-Strain', zorder=3)

    # Elastic region highlight
    es = result.elastic_start_idx
    ee = result.elastic_end_idx
    ax.axvspan(strain[es], strain[ee], alpha=0.12, color='#2980b9', label='Elastic region')

    # Modulus line
    if result.modulus_line:
        ax.plot(result.modulus_line[0], result.modulus_line[1],
                '--', color='#2980b9', linewidth=1.5, label=f"E = {result.youngs_modulus:,.0f} MPa")

    # 0.2 % offset line
    if result.offset_line:
        ax.plot(result.offset_line[0], result.offset_line[1],
                ':', color='#8e44ad', linewidth=1.5, label='0.2 % offset')

    # Yield point
    if result.yield_stress is not None:
        ax.scatter([result.yield_strain], [result.yield_stress],
                   color='#8e44ad', zorder=5, s=80, label=f"Yield = {result.yield_stress:.1f} MPa")

    # UTS
    if result.uts_stress is not None:
        ax.scatter([result.uts_strain], [result.uts_stress],
                   color='#e74c3c', marker='^', zorder=5, s=100, label=f"UTS = {result.uts_stress:.1f} MPa")

    # Break
    if result.break_stress is not None:
        ax.scatter([result.break_strain], [result.break_stress],
                   color='#e67e22', marker='X', zorder=5, s=100, label=f"Break = {result.break_stress:.1f} MPa")

    ax.set_title(title, fontsize=12, fontweight='bold', pad=8)
    ax.set_xlabel("Strain (mm/mm)", fontsize=9)
    ax.set_ylabel("Stress (MPa)", fontsize=9)
    ax.legend(fontsize=7.5, loc='upper left')
    ax.grid(True, alpha=0.3)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)


def make_compression_data(n_points: int = 500, noise_level: float = 0.5):
    """
    Simulated Compression Test — purely negative load and displacement values.
    Should yield properties equal to Mild Steel in absolute magnitude.
    """
    load, disp, area, gauge, _ = make_steel_data(n_points, noise_level)
    # Invert to simulate a compression test
    load_neg = [-v for v in load]
    disp_neg = [-v for v in disp]
    return load_neg, disp_neg, area, gauge, "Simulated Compression"

# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_tests():
    engine = MaterialAnalysisEngine(
        toe_strain_threshold=0.001,
        elastic_window_size=20,
        min_r2=0.995,
        break_drop_fraction=0.40,
    )

    datasets = [
        make_steel_data(),
        make_aluminium_data(),
        make_polymer_data(),
        make_compression_data(),
    ]

    try:
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        
        fig = plt.figure(figsize=(24, 6))
        fig.suptitle("InsituMicron — Material Analysis Engine Test", fontsize=14, fontweight='bold')
        gs  = gridspec.GridSpec(1, 4, figure=fig, wspace=0.35)
        has_mpl = True
    except ImportError:
        has_mpl = False
        print("Matplotlib not found, skipping plots.")

    for col, (load, disp, area, gauge, name) in enumerate(datasets):
        print(f"\n{'='*55}")
        print(f"  Dataset: {name}")
        print(f"  Points : {len(load)}   Area: {area:.2f} mm2   Gauge: {gauge} mm")
        print(f"{'='*55}")

        result = engine.analyse(load, disp, area, gauge)
        print(result.summary())
        
        # Simple assertion for compression to verify absolute value handling worked
        if name == "Simulated Compression":
            assert result.youngs_modulus > 0, "Modulus should be positive!"
            assert result.yield_stress > 0, "Yield stress should be positive!"
            print("  -> Verification Passed: Compression data yielded positive absolute properties.")

        if has_mpl:
            ax = fig.add_subplot(gs[col])
            plot_result(ax, result, name)

    if has_mpl:
        plt.tight_layout()
        out_path = os.path.join(os.path.dirname(__file__), "test_output_stress_strain.png")
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        print(f"\n[OK] Plot saved -> {out_path}")
        plt.show()

if __name__ == "__main__":
    run_tests()
