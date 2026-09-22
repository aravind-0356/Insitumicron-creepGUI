"""
material_analysis.py
====================
Pure-math engine for Stress-Strain analysis of Uniaxial tensile test data.
No GUI, no Qt, no hardware dependencies — safe to unit-test standalone.

Inputs  : Load array (N), Displacement array (mm), Cross-section Area (mm²),
          Gauge Length (mm)
Outputs : AnalysisResult dataclass containing all key material properties.

Algorithm summary
-----------------
1. Convert raw Load/Displacement → Stress (MPa) / Strain (mm/mm).
2. Detect and strip the initial "toe" region (grip seating noise).
3. Find the linear elastic region using a rolling-window R² scan.
4. Compute Young's Modulus via linear regression over that window.
5. Compute Yield Strength via the 0.2 % offset method.
6. Find UTS (peak stress) and Break point (load-drop threshold).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class AnalysisResult:
    """All key material properties derived from one tensile test."""

    # Raw converted arrays
    stress: List[float] = field(default_factory=list)   # MPa
    strain: List[float] = field(default_factory=list)   # mm/mm (dimensionless)

    # Elastic region used for modulus fit (indices into stress/strain)
    elastic_start_idx: int = 0
    elastic_end_idx: int = 0

    # Key properties
    youngs_modulus: Optional[float] = None      # MPa
    yield_stress: Optional[float] = None        # MPa  (0.2 % offset)
    yield_strain: Optional[float] = None        # mm/mm

    uts_stress: Optional[float] = None          # MPa
    uts_strain: Optional[float] = None          # mm/mm

    break_stress: Optional[float] = None        # MPa
    break_strain: Optional[float] = None        # mm/mm

    # Fit line for plotting: two (strain, stress) points
    modulus_line: Optional[Tuple[List[float], List[float]]] = None
    # Offset line for plotting (0.2 % offset of the modulus line)
    offset_line: Optional[Tuple[List[float], List[float]]] = None

    # Warnings or notes from the engine
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["=== Material Analysis Results ==="]
        if self.youngs_modulus is not None:
            lines.append(f"  Young's Modulus  : {self.youngs_modulus:>10.1f} MPa")
        if self.yield_stress is not None:
            lines.append(f"  Yield Strength   : {self.yield_stress:>10.2f} MPa  "
                         f"(e = {self.yield_strain:.4f})")
        if self.uts_stress is not None:
            lines.append(f"  UTS              : {self.uts_stress:>10.2f} MPa  "
                         f"(e = {self.uts_strain:.4f})")
        if self.break_stress is not None:
            lines.append(f"  Break            : {self.break_stress:>10.2f} MPa  "
                         f"(e = {self.break_strain:.4f})")
        if self.warnings:
            lines.append("\n  Warnings:")
            for w in self.warnings:
                lines.append(f"    [!] {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class MaterialAnalysisEngine:
    """
    Stateless analysis engine.  Call ``analyse()`` with raw test vectors.

    Parameters
    ----------
    toe_strain_threshold : float
        Strain below which data is considered "toe" (grip seating) and excluded
        from modulus fitting.  Default 0.001 (0.1 %).
    elastic_window_size : int
        Number of consecutive points used in the rolling R² scan to locate the
        linear elastic region.
    min_r2 : float
        Minimum R² required to consider a window "linear".
    break_drop_fraction : float
        Fraction of UTS drop that defines the fracture point (default 0.40 = 40 %).
    """

    def __init__(
        self,
        toe_strain_threshold: float = 0.001,
        elastic_window_size: int = 20,
        min_r2: float = 0.995,
        break_drop_fraction: float = 0.40,
    ):
        self.toe_strain_threshold = toe_strain_threshold
        self.elastic_window_size = elastic_window_size
        self.min_r2 = min_r2
        self.break_drop_fraction = break_drop_fraction

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyse(
        self,
        load: List[float],
        displacement: List[float],
        area_mm2: float,
        gauge_length_mm: float,
        manual_elastic_range: Optional[Tuple[float, float]] = None,
    ) -> AnalysisResult:
        """
        Run the full analysis pipeline.

        Parameters
        ----------
        load            : Force values in Newtons.
        displacement    : Displacement values in mm (already zeroed to gauge start).
        area_mm2        : Cross-sectional area in mm².
        gauge_length_mm : Original gauge length in mm.
        manual_elastic_range : Optional (strain_start, strain_end) override.
                               If provided, bypasses automatic detection.

        Returns
        -------
        AnalysisResult
        """
        result = AnalysisResult()

        # ---- 0. Basic validation ----------------------------------------
        if len(load) != len(displacement):
            result.warnings.append("Load and displacement arrays have different lengths — truncating.")
            n = min(len(load), len(displacement))
            load = load[:n]
            displacement = displacement[:n]

        if len(load) < 10:
            result.warnings.append("Fewer than 10 data points — results unreliable.")

        if area_mm2 <= 0:
            result.warnings.append("Area is zero or negative — stress calculation invalid.")
            return result

        if gauge_length_mm <= 0:
            result.warnings.append("Gauge length is zero or negative — strain calculation invalid.")
            return result

        # ---- 1. Pre-process and Convert to Stress / Strain --------------
        load_arr = np.array(load, dtype=float)
        disp_arr = np.array(displacement, dtype=float)
        
        # Detect if this is a compressive test (peak negative load > peak positive load)
        if len(load_arr) > 0 and abs(np.min(load_arr)) > abs(np.max(load_arr)):
            load_arr = -load_arr
            disp_arr = -disp_arr
            result.warnings.append("Data appears to be compressive. It has been converted to absolute values for analysis and plotting.")

        stress = load_arr / area_mm2          # MPa
        strain = disp_arr / gauge_length_mm  # dimensionless

        result.stress = stress.tolist()
        result.strain = strain.tolist()

        # ---- 2. UTS -----------------------------------------------------
        uts_idx = int(np.argmax(stress))
        result.uts_stress = float(stress[uts_idx])
        result.uts_strain = float(strain[uts_idx])

        # ---- 3. Break point (two-stage hybrid) --------------------------
        #
        # Stage 1 — Sudden-fracture detection (brittle / semi-brittle materials)
        # -----------------------------------------------------------------------
        # After UTS, find the largest single point-to-point stress drop.
        # If that drop exceeds SUDDEN_MIN_FRACTION of UTS (e.g. 10%), it is a
        # sudden collapse (cliff-edge fracture).  The break point is reported as
        # the data point JUST BEFORE that cliff — the last moment the specimen
        # was intact and holding load.
        #
        # Stage 2 — Gradual-necking fallback (ductile materials)
        # -----------------------------------------------------------------------
        # If no sudden collapse is detected, fall back to the original threshold:
        # first point where stress has dropped by break_drop_fraction from UTS.

        SUDDEN_MIN_FRACTION = 0.10   # single step must be > 10% of UTS to be a fracture

        post_uts_stress = stress[uts_idx:]
        break_idx = None

        if len(post_uts_stress) > 2:
            # Point-to-point drops (positive value = stress is falling)
            drops = np.diff(post_uts_stress) * -1.0
            drops = np.clip(drops, 0, None)        # ignore any upward noise

            collapse_local_idx = int(np.argmax(drops))
            collapse_drop      = drops[collapse_local_idx]
            min_abs            = result.uts_stress * SUDDEN_MIN_FRACTION

            if collapse_drop >= min_abs:
                # Sudden fracture detected — break point is the last intact sample
                break_idx = uts_idx + collapse_local_idx

        # Stage 2 fallback — percentage-drop method (ductile / gradual necking)
        if break_idx is None:
            break_threshold = result.uts_stress * (1.0 - self.break_drop_fraction)
            for i in range(uts_idx, len(stress)):
                if stress[i] <= break_threshold:
                    break_idx = i
                    break

        if break_idx is not None:
            result.break_stress = float(stress[break_idx])
            result.break_strain = float(strain[break_idx])
        else:
            result.warnings.append(
                "Break point not detected — stress never dropped by "
                f"{self.break_drop_fraction*100:.0f}% from UTS within test range."
            )

        # ---- 4. Locate elastic region -----------------------------------
        if manual_elastic_range is not None:
            e_start, e_end = manual_elastic_range
            mask = (strain >= e_start) & (strain <= e_end)
            elastic_indices = np.where(mask)[0]
            if len(elastic_indices) < 5:
                result.warnings.append(
                    "Manual elastic range contains fewer than 5 points — auto-detection used instead."
                )
                elastic_indices = self._auto_detect_elastic(strain, stress)
        else:
            elastic_indices = self._auto_detect_elastic(strain, stress)

        if len(elastic_indices) < 3:
            result.warnings.append("Could not detect a reliable linear elastic region.")
            return result

        result.elastic_start_idx = int(elastic_indices[0])
        result.elastic_end_idx   = int(elastic_indices[-1])

        e_strain = strain[elastic_indices]
        e_stress = stress[elastic_indices]

        # ---- 5. Young's Modulus via linear regression -------------------
        slope, intercept, r_value, _, _ = stats.linregress(e_strain, e_stress)
        if r_value ** 2 < 0.98:
            result.warnings.append(
                f"Elastic region R² = {r_value**2:.4f} — modulus fit may be unreliable."
            )
        result.youngs_modulus = float(slope)   # MPa/mm/mm = MPa

        # Build modulus line for plotting — extend modestly past elastic region
        # Use 2x elastic strain range so it's visible but doesn't blow the Y scale
        elastic_strain_range = float(strain[result.elastic_end_idx]) - float(strain[result.elastic_start_idx])
        plot_end_strain = float(strain[result.elastic_end_idx]) + elastic_strain_range
        plot_strain = [float(strain[result.elastic_start_idx]), plot_end_strain]
        plot_stress = [slope * s + intercept for s in plot_strain]
        result.modulus_line = (plot_strain, plot_stress)

        # 0.2 % offset line — same range, shifted right by 0.002
        offset = 0.002
        offset_strain = [s + offset for s in plot_strain]
        offset_stress = [slope * s + intercept for s in plot_strain]
        result.offset_line = (offset_strain, offset_stress)

        # ---- 6. Yield Strength (0.2 % offset intersection) -------------
        # Search starts AFTER the elastic region to avoid false positives from
        # toe noise / grip-seating at the very beginning of the test.
        yield_result = self._find_yield_point(
            strain, stress, slope, intercept,
            offset=0.002,
            search_start_idx=result.elastic_end_idx
        )
        if yield_result is not None:
            result.yield_stress, result.yield_strain = yield_result
        else:
            result.warnings.append("Yield point (0.2 % offset) could not be determined.")

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _auto_detect_elastic(self, strain: np.ndarray, stress: np.ndarray) -> np.ndarray:
        """
        Two-stage elastic region detection:

        Stage 1: compute local slope at every point using a small sw-point window.
                 The elastic region has the steepest consistent slope.
        Stage 2: extend the elastic end rightward while slope stays above 70%
                 of the peak slope. Stop on first yield-driven slope decay.

        Works for narrow elastic zones (steel: ~0.001 strain) and wide ones (polymers).
        """
        uts_idx = int(np.argmax(stress))
        sw = max(5, len(strain) // 100)   # small local window: 1% of data or min 5 pts

        # Strip toe
        start = int(np.searchsorted(strain, self.toe_strain_threshold))
        start = max(start, sw)

        scan_limit = min(uts_idx, len(strain) - sw)

        if scan_limit <= start:
            w = min(self.elastic_window_size, max(3, len(strain) // 4))
            return np.arange(0, min(w, len(strain)))

        # Stage 1: compute local slopes
        slopes = np.zeros(len(strain))
        for i in range(start, scan_limit):
            sl, _, _, _, _ = stats.linregress(strain[i: i + sw], stress[i: i + sw])
            slopes[i] = sl

        peak_slope_idx = int(np.argmax(slopes[start:scan_limit])) + start
        peak_slope = slopes[peak_slope_idx]

        if peak_slope <= 0:
            w = self.elastic_window_size
            return np.arange(start, min(start + w, len(strain)))

        # Stage 2: extend right while slope >= 70% of peak
        tolerance = 0.70
        elastic_end = peak_slope_idx
        for i in range(peak_slope_idx, min(scan_limit, len(strain) - sw)):
            if slopes[i] > 0 and slopes[i] >= tolerance * peak_slope:
                elastic_end = i
            else:
                break

        elastic_end = min(elastic_end + sw, len(strain) - 1)
        return np.arange(start, elastic_end + 1)


    def _find_yield_point(
        self,
        strain: np.ndarray,
        stress: np.ndarray,
        slope: float,
        intercept: float,
        offset: float = 0.002,
        search_start_idx: int = 0,
    ) -> Optional[Tuple[float, float]]:
        """
        Find intersection of the 0.2% offset line with the stress-strain curve.
        The offset line is the Modulus Fit line shifted right by 0.002 strain.

        Physics of the sign crossing:
        - Inside the elastic zone: the actual curve and the offset line have the
          same slope, but the offset line is shifted right by 0.002.  At any given
          strain, the actual curve therefore sits ABOVE the offset line → diff > 0.
        - After the yield point: the actual curve bends over (plastic deformation)
          while the offset line continues rising at the full modulus slope.  The
          offset line eventually overtakes the actual curve → diff transitions
          from positive to NEGATIVE.
        - The yield point is therefore the FIRST positive→negative zero-crossing
          of diff, NOT a negative→positive one.

        Parameters
        ----------
        search_start_idx : int
            Index from which to begin scanning for the intersection. Should be
            set to elastic_end_idx so the search skips the elastic zone entirely
            and finds only the true plastic-zone crossing.
        """
        # Offset line: σ_offset = slope * ε + (intercept - slope * offset)
        adjusted_intercept = intercept - slope * offset
        offset_stress = slope * strain + adjusted_intercept

        # diff is positive throughout the elastic zone and goes negative after yield.
        diff = stress - offset_stress
        start = max(1, search_start_idx)

        for i in range(start, len(diff)):
            # Look for the first POSITIVE → NEGATIVE crossing (actual curve falls
            # below the offset line), which is the 0.2% proof-strength yield point.
            if diff[i - 1] > 0 and diff[i] <= 0:
                # Linear interpolation to get sub-sample accuracy
                t = diff[i - 1] / (diff[i - 1] - diff[i])
                y_strain = float(strain[i - 1] + t * (strain[i] - strain[i - 1]))
                y_stress = float(stress[i - 1] + t * (stress[i] - stress[i - 1]))
                return y_stress, y_strain

        return None
