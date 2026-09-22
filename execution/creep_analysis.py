"""
creep_analysis.py
=================
Pure-math engine for Creep and Strain Rate analysis of long-duration test data.
Compliant with ASTM E139 principles.
No GUI, no Qt, no hardware dependencies — safe to unit-test standalone.

Inputs  : Time array (s), Load array (N), Displacement array (mm),
          Cross-section Area (mm²), Gauge Length (mm)
Outputs : CreepAnalysisResult dataclass containing steady-state creep rate,
          mean hold stress, transition times, rupture metrics, and fit lines.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy import stats, signal


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class CreepAnalysisResult:
    """All key material properties derived from one creep test."""

    # Converted arrays for plotting
    time_hr: List[float] = field(default_factory=list)          # Hours
    time_s: List[float] = field(default_factory=list)           # Seconds
    strain_pct: List[float] = field(default_factory=list)       # Percent (%)
    stress_mpa: List[float] = field(default_factory=list)       # MPa
    strain_rate_per_hr: List[float] = field(default_factory=list) # %/hr

    # Mean hold conditions
    mean_stress_mpa: float = 0.0
    mean_load_n: float = 0.0

    # Steady-State (Secondary) Creep properties
    steady_state_start_hr: float = 0.0
    steady_state_end_hr: float = 0.0
    steady_state_rate_pct_per_hr: float = 0.0                  # % / hr
    steady_state_rate_per_sec: float = 0.0                     # 1 / s
    steady_state_r2: float = 0.0                               # Goodness of fit (0 to 1)

    # Tangent fit line across steady-state window for visualization: ([t1, t2], [s1, s2])
    fit_line: Optional[Tuple[List[float], List[float]]] = None

    # Regimes & Rupture
    primary_duration_hr: Optional[float] = None                # Duration of primary stage
    tertiary_onset_hr: Optional[float] = None                  # Onset of tertiary acceleration
    total_creep_strain_pct: float = 0.0                        # Final strain %
    total_test_duration_hr: float = 0.0                        # Total time in hours
    rupture_detected: bool = False                             # Did specimen rupture?
    rupture_time_hr: Optional[float] = None                    # Time to rupture

    # Diagnostic warnings or observations
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["=== Creep & Strain Rate Analysis Results ==="]
        lines.append(f"  Mean Hold Stress         : {self.mean_stress_mpa:>10.2f} MPa")
        lines.append(f"  Steady-State Rate (dE/dt): {self.steady_state_rate_pct_per_hr:>10.4e} %/hr "
                     f"({self.steady_state_rate_per_sec:>10.4e} 1/s)")
        lines.append(f"  Steady-State R² Fit      : {self.steady_state_r2:>10.4f}")
        lines.append(f"  Steady-State Window      : {self.steady_state_start_hr:.2f} hr -> {self.steady_state_end_hr:.2f} hr")
        lines.append(f"  Total Creep Strain       : {self.total_creep_strain_pct:>10.3f} %")
        lines.append(f"  Total Duration           : {self.total_test_duration_hr:>10.2f} hr")
        if self.rupture_detected:
            lines.append(f"  Rupture Detected At      : {self.rupture_time_hr:>10.2f} hr")
        else:
            lines.append("  Rupture Status           : Not detected (test suspended / ongoing)")
        if self.warnings:
            lines.append("\n  Warnings:")
            for w in self.warnings:
                lines.append(f"    [!] {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class CreepAnalysisEngine:
    """
    Stateless creep analysis engine. Call ``analyse()`` with raw test vectors.
    """

    def __init__(
        self,
        min_steady_window_pts: int = 15,
        tertiary_acceleration_factor: float = 1.30,
        rupture_load_drop_fraction: float = 0.30,
    ):
        self.min_steady_window_pts = min_steady_window_pts
        self.tertiary_acceleration_factor = tertiary_acceleration_factor
        self.rupture_load_drop_fraction = rupture_load_drop_fraction

    def analyse(
        self,
        time_s: List[float],
        load_n: List[float],
        disp_mm: List[float],
        area_mm2: float,
        gauge_length_mm: float,
        manual_steady_state_range: Optional[Tuple[float, float]] = None,
    ) -> CreepAnalysisResult:
        """
        Run the full creep and strain-rate analysis pipeline.

        Parameters
        ----------
        time_s : Elapsed time in seconds.
        load_n : Force values in Newtons.
        disp_mm : Displacement values in mm.
        area_mm2 : Specimen cross-sectional area in mm².
        gauge_length_mm : Initial gauge length in mm.
        manual_steady_state_range : Optional (t_start_hr, t_end_hr) window override.
        """
        result = CreepAnalysisResult()

        # ---- 0. Input validation ----------------------------------------
        n = min(len(time_s), len(load_n), len(disp_mm))
        if n < 5:
            result.warnings.append("Insufficient data points (< 5) for creep analysis.")
            return result

        time_arr = np.array(time_s[:n], dtype=float)
        load_arr = np.array(load_n[:n], dtype=float)
        disp_arr = np.array(disp_mm[:n], dtype=float)

        if area_mm2 <= 0:
            result.warnings.append("Cross-sectional area is <= 0; stress calculation invalid.")
            return result
        if gauge_length_mm <= 0:
            result.warnings.append("Gauge length is <= 0; strain calculation invalid.")
            return result

        # Ensure monotonic positive time
        if time_arr[0] > 0:
            time_arr = time_arr - time_arr[0]

        time_hr = time_arr / 3600.0
        # Strain as percentage: (disp / L0) * 100
        strain_pct = (disp_arr / gauge_length_mm) * 100.0
        stress_mpa = load_arr / area_mm2

        result.time_s = time_arr.tolist()
        result.time_hr = time_hr.tolist()
        result.strain_pct = strain_pct.tolist()
        result.stress_mpa = stress_mpa.tolist()

        result.mean_stress_mpa = float(np.mean(stress_mpa))
        result.mean_load_n = float(np.mean(load_arr))
        result.total_creep_strain_pct = float(strain_pct[-1] - strain_pct[0])
        result.total_test_duration_hr = float(time_hr[-1])

        # ---- 1. Smoothed Strain Rate Calculation ------------------------
        dt = np.diff(time_hr)
        de = np.diff(strain_pct)
        # Avoid zero-division in rate
        valid_dt = np.where(dt > 1e-9, dt, 1e-9)
        raw_rates = de / valid_dt  # %/hr
        raw_rates = np.insert(raw_rates, 0, raw_rates[0] if len(raw_rates) > 0 else 0.0)

        # Smooth rates using a running median/average window to damp sensor step noise
        win_size = min(len(raw_rates), max(5, len(raw_rates) // 25))
        if win_size % 2 == 0:
            win_size += 1
        if win_size >= 5 and len(raw_rates) >= win_size:
            smoothed_rates = signal.medfilt(raw_rates, kernel_size=win_size)
        else:
            smoothed_rates = raw_rates

        result.strain_rate_per_hr = smoothed_rates.tolist()

        # ---- 2. Detect Steady-State Region ------------------------------
        if manual_steady_state_range is not None:
            t_start, t_end = manual_steady_state_range
            idx_mask = (time_hr >= t_start) & (time_hr <= t_end)
            indices = np.where(idx_mask)[0]
            if len(indices) < 3:
                result.warnings.append("Manual steady-state window has fewer than 3 points — auto-detection used.")
                indices = self._auto_detect_steady_state(time_hr, strain_pct)
        else:
            indices = self._auto_detect_steady_state(time_hr, strain_pct)

        if len(indices) < 3:
            result.warnings.append("Could not isolate a stable secondary creep stage.")
            # Fallback to entire dataset
            indices = np.arange(len(time_hr))

        start_idx = int(indices[0])
        end_idx = int(indices[-1])

        result.steady_state_start_hr = float(time_hr[start_idx])
        result.steady_state_end_hr = float(time_hr[end_idx])

        # Linear regression across steady-state window: strain(%) = rate * time(hr) + intercept
        fit_t = time_hr[start_idx:end_idx + 1]
        fit_e = strain_pct[start_idx:end_idx + 1]

        slope, intercept, r_val, _, _ = stats.linregress(fit_t, fit_e)
        result.steady_state_rate_pct_per_hr = float(max(0.0, slope))
        # Convert %/hr -> dimensionless/sec: (rate / 100) / 3600
        result.steady_state_rate_per_sec = float((result.steady_state_rate_pct_per_hr / 100.0) / 3600.0)
        result.steady_state_r2 = float(max(0.0, r_val ** 2)) if not math.isnan(r_val) else 0.0

        # Construct tangent fit line for plotting: span slightly beyond steady-state window
        t_span = result.steady_state_end_hr - result.steady_state_start_hr
        line_t0 = max(0.0, result.steady_state_start_hr - 0.2 * t_span)
        line_t1 = min(result.total_test_duration_hr, result.steady_state_end_hr + 0.2 * t_span)
        line_e0 = slope * line_t0 + intercept
        line_e1 = slope * line_t1 + intercept
        result.fit_line = ([float(line_t0), float(line_t1)], [float(line_e0), float(line_e1)])

        # Primary duration is approximately the start of steady state
        if start_idx > 0:
            result.primary_duration_hr = float(time_hr[start_idx])

        # ---- 3. Rupture / Fracture Detection ----------------------------
        # Check 1: Significant drop in load at end of test (> 30% drop from mean)
        # Check 2: Sudden vertical surge in displacement at end of test
        peak_load = np.max(load_arr)
        end_load = load_arr[-1]
        if peak_load > 0 and (peak_load - end_load) / peak_load > self.rupture_load_drop_fraction:
            result.rupture_detected = True
            result.rupture_time_hr = float(time_hr[-1])
        elif len(smoothed_rates) > 10:
            # If the final rate is > 5x the steady state rate, mark as tertiary rupture
            if smoothed_rates[-1] > 5.0 * max(1e-6, result.steady_state_rate_pct_per_hr):
                result.rupture_detected = True
                result.rupture_time_hr = float(time_hr[-1])

        # ---- 4. Tertiary Onset Detection --------------------------------
        if end_idx < len(time_hr) - 5:
            # Check if rates after steady state accelerate above threshold
            threshold_rate = result.steady_state_rate_pct_per_hr * self.tertiary_acceleration_factor
            for i in range(end_idx, len(time_hr)):
                if smoothed_rates[i] > threshold_rate:
                    result.tertiary_onset_hr = float(time_hr[i])
                    break

        return result

    def _auto_detect_steady_state(self, time_hr: np.ndarray, strain_pct: np.ndarray) -> np.ndarray:
        """
        Locates the secondary creep stage (minimum sustained positive slope with highest linearity).
        """
        n = len(time_hr)
        if n < self.min_steady_window_pts:
            return np.arange(n)

        # Window size: between 15 points and 30% of total points
        w = max(self.min_steady_window_pts, int(n * 0.25))
        w = min(w, n - 2)

        best_score = float("inf")
        best_window = (int(n * 0.2), int(n * 0.7))

        # Scan sliding window
        step = max(1, (n - w) // 50)
        for i in range(0, n - w, step):
            tw = time_hr[i:i + w]
            ew = strain_pct[i:i + w]

            slope, intercept, r_val, _, _ = stats.linregress(tw, ew)
            r2 = r_val ** 2 if not math.isnan(r_val) else 0.0

            # Only consider positive slopes (creep deformation increases with time)
            if slope > 0:
                # Score penalizes high slope (want minimum creep rate) and penalizes low R²
                score = slope / max(0.01, r2 ** 2)
                if score < best_score:
                    best_score = score
                    best_window = (i, i + w)

        return np.arange(best_window[0], best_window[1] + 1)
