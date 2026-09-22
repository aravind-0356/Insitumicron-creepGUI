"""
test_analysis_dual_mode.py
==========================
End-to-End Automated Test Suite for Dual-Mode Analysis & Reporting:
1. Validates default mode is Strain Rate & Creep.
2. Validates parsing of Time(s), Load(N), and Disp(mm) from Creep CSV.
3. Validates Creep Analysis calculation and metric UI population.
4. Validates interactive mode switching between Creep and Tensile.
5. Validates interactive steady-state region slider recalculation.
6. Validates headless PDF report generation for both Creep and Tensile modes.
"""

import sys
import os
import time
import tempfile
import csv
import numpy as np
import pytest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox
_app = QApplication.instance() or QApplication(sys.argv)

from gui_analysis import AnalysisReportingPanel
from execution.creep_analysis import CreepAnalysisResult
from execution.material_analysis import AnalysisResult


@pytest.fixture(autouse=True)
def mock_qmessagebox_dialogs():
    """Ensure no modal dialogs block headless test execution."""
    with patch.object(QMessageBox, "information"), \
         patch.object(QMessageBox, "warning"), \
         patch.object(QMessageBox, "critical"), \
         patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
         patch.object(QMessageBox, "exec", return_value=0), \
         patch.object(QMessageBox, "exec_", return_value=0), \
         patch("gui_analysis.QMessageBox.information"), \
         patch("gui_analysis.QMessageBox.warning"), \
         patch("gui_analysis.QMessageBox.critical"):
        yield


def create_sample_creep_csv(path: str, n_pts: int = 200, steady_rate: float = 0.02):
    """Generates a standard CSV matching data_worker.py output format."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for i in range(n_pts):
            t = i * 360.0  # 360s = 0.1 hr step -> 20 hours total
            t_hr = t / 3600.0
            load = 500.0 + (i % 3) * 0.1
            disp = 0.1 + steady_rate * t_hr * 0.5  # creep displacement in mm
            writer.writerow([f"{t:.3f}", f"{load:.3f}", f"{disp:.4f}"])


def create_sample_tensile_csv(path: str, n_pts: int = 200):
    """Generates a standard monotonic tensile test CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Time(s)", "Load(N)", "Disp(mm)"])
        for i in range(n_pts):
            disp = i * 0.05
            if i < 40:
                load = i * 15.0  # Elastic
            elif i < 150:
                load = 600.0 + (i - 40) * 1.5  # Plastic hardening
            else:
                load = max(0.0, 765.0 - (i - 150) * 10.0)  # Necking/drop
            writer.writerow([f"{i*0.1:.3f}", f"{load:.3f}", f"{disp:.4f}"])


def test_default_mode_and_init():
    print("[1/5] Testing default initialization and mode state...")
    panel = AnalysisReportingPanel()
    assert panel.current_mode == "creep", "Default mode must be 'creep'"
    assert panel.btn_mode_creep.isChecked(), "Creep toggle button must be checked by default"
    assert not panel.btn_mode_tensile.isChecked()
    assert panel.btn_mode_creep.minimumHeight() >= 40, "Toggle button height must be enlarged (>= 40px)"
    assert panel.btn_mode_tensile.minimumHeight() >= 40, "Toggle button height must be enlarged (>= 40px)"
    assert panel.results_stack.currentIndex() == 0, "Results stack should be on Creep page (index 0)"
    assert panel.plot_widget.plotItem.titleLabel.text == "Creep Curve — Strain vs. Time"
    
    assert panel.btn_mode_creep.text() == "Strain Rate - Creep (Default)", "Toggle button must have dash"
    
    # Verify legend contains ONLY Creep curves
    legend_items = panel.plot_widget.plotItem.legend.items
    assert len(legend_items) == 2, f"Creep legend should contain exactly 2 items, got {len(legend_items)}"
    legend_names = [label.text for _, label in legend_items]
    assert "Creep Strain" in legend_names and "Steady-State Fit" in legend_names
    assert "Stress-Strain" not in legend_names and "Yield Point" not in legend_names
    print("  -> PASS")


def test_creep_csv_loading_and_analysis():
    print("[2/5] Testing CSV loading, Time/Load/Disp parsing & Creep analysis...")
    panel = AnalysisReportingPanel()
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, "test_result(csv format).csv")
        create_sample_creep_csv(csv_file, n_pts=200, steady_rate=0.02)

        # Mock QFileDialog to select this file
        with patch.object(panel, "load_csv", wraps=panel.load_csv):
            with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(csv_file, "CSV Files (*.csv)")):
                panel.load_csv()

        assert panel.current_data is not None
        assert "time" in panel.current_data
        assert "load" in panel.current_data
        assert "disp" in panel.current_data
        assert len(panel.current_data["time"]) == 200

        # Verify CreepAnalysisResult was generated
        assert isinstance(panel.analysis_result, CreepAnalysisResult)
        assert panel.analysis_result.mean_stress_mpa > 10.0
        assert panel.analysis_result.steady_state_rate_pct_per_hr > 0.0
        assert panel.analysis_result.steady_state_r2 > 0.95

        # Verify UI labels populated
        assert panel.lbl_creep_stress.text() != "--"
        assert panel.lbl_creep_rate_hr.text() != "--"
        assert panel.lbl_creep_r2.text() != "--"
        assert panel.btn_report.isEnabled(), "Generate Report button must be enabled"

    print("  -> PASS")


def test_mode_toggling():
    print("[3/5] Testing dynamic mode toggling between Creep and Tensile...")
    panel = AnalysisReportingPanel()
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, "tensile_test.csv")
        create_sample_tensile_csv(csv_file)

        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(csv_file, "CSV")):
            panel.load_csv()

        # Switch to Tensile mode
        panel.btn_mode_tensile.click()
        assert panel.current_mode == "tensile"
        assert panel.results_stack.currentIndex() == 1
        assert isinstance(panel.analysis_result, AnalysisResult)
        assert panel.plot_widget.plotItem.titleLabel.text == "Stress-Strain Analysis"
        assert panel.lbl_e.text() != "--"
        assert panel.lbl_uts.text() != "--"

        # Verify legend contains ONLY Tensile curves
        tensile_items = panel.plot_widget.plotItem.legend.items
        assert len(tensile_items) == 6, f"Tensile legend should contain exactly 6 items, got {len(tensile_items)}"
        tensile_names = [label.text for _, label in tensile_items]
        assert "Stress-Strain" in tensile_names and "Yield Point" in tensile_names
        assert "Creep Strain" not in tensile_names

        # Switch back to Creep mode
        panel.btn_mode_creep.click()
        assert panel.current_mode == "creep"
        assert panel.results_stack.currentIndex() == 0
        assert isinstance(panel.analysis_result, CreepAnalysisResult)
        assert panel.plot_widget.plotItem.titleLabel.text == "Creep Curve — Strain vs. Time"

        # Verify legend contains ONLY Creep curves again
        creep_items = panel.plot_widget.plotItem.legend.items
        assert len(creep_items) == 2
        creep_names = [label.text for _, label in creep_items]
        assert "Creep Strain" in creep_names
        assert "Stress-Strain" not in creep_names

    print("  -> PASS")


def test_interactive_region_override():
    print("[4/5] Testing interactive region manual override...")
    panel = AnalysisReportingPanel()
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, "creep_data.csv")
        create_sample_creep_csv(csv_file, n_pts=250, steady_rate=0.03)

        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(csv_file, "CSV")):
            panel.load_csv()

        assert panel.interactive_region.isVisible()
        
        # Override region manually to a specific window [5.0 hr, 15.0 hr]
        panel.interactive_region.setRegion([5.0, 15.0])
        panel.on_region_changed()

        assert abs(panel.analysis_result.steady_state_start_hr - 5.0) < 0.5
        assert abs(panel.analysis_result.steady_state_end_hr - 15.0) < 0.5
        assert panel.analysis_result.steady_state_rate_pct_per_hr > 0.0

    print("  -> PASS")


def test_pdf_report_generation():
    print("[5/5] Testing headless PDF report generation for both modes...")
    panel = AnalysisReportingPanel()
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_file = os.path.join(tmpdir, "test.csv")
        create_sample_creep_csv(csv_file)

        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(csv_file, "CSV")):
            panel.load_csv()

        # Generate Creep PDF
        creep_pdf = os.path.join(tmpdir, "report_creep.pdf")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(creep_pdf, "PDF")), \
             patch("PySide6.QtWidgets.QMessageBox.exec", return_value=0), \
             patch("PySide6.QtWidgets.QMessageBox.exec_", return_value=0), \
             patch("gui_analysis.QMessageBox.information"):
            panel.generate_report()

        assert os.path.exists(creep_pdf) and os.path.getsize(creep_pdf) > 2000
        print(f"    Creep PDF created: {os.path.getsize(creep_pdf)} bytes")

        # Switch to Tensile and generate Tensile PDF
        panel.set_mode("tensile")
        tensile_pdf = os.path.join(tmpdir, "report_tensile.pdf")
        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(tensile_pdf, "PDF")), \
             patch("PySide6.QtWidgets.QMessageBox.exec", return_value=0), \
             patch("PySide6.QtWidgets.QMessageBox.exec_", return_value=0), \
             patch("gui_analysis.QMessageBox.information"):
            panel.generate_report()

        assert os.path.exists(tensile_pdf) and os.path.getsize(tensile_pdf) > 2000
        print(f"    Tensile PDF created: {os.path.getsize(tensile_pdf)} bytes")

    print("  -> PASS")


def run_all():
    print("=" * 60)
    print("RUNNING DUAL-MODE ANALYSIS & REPORTING TEST SUITE")
    print("=" * 60)
    with patch.object(QMessageBox, "information"), \
         patch.object(QMessageBox, "warning"), \
         patch.object(QMessageBox, "critical"), \
         patch.object(QMessageBox, "exec", return_value=QMessageBox.Ok):
        test_default_mode_and_init()
        test_creep_csv_loading_and_analysis()
        test_mode_toggling()
        test_interactive_region_override()
        test_pdf_report_generation()
    print("=" * 60)
    print("ALL DUAL-MODE ANALYSIS TESTS PASSED [5/5]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
