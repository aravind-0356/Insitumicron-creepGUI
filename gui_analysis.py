# gui_analysis.py
"""
Analysis & Reporting Panel for Insitumicron Creep GUI.
Provides dual analysis engines:
1. Strain Rate & Creep Analysis (Default) - ASTM E139 compliant steady-state rate & rupture metrics.
2. Stress vs. Strain Analysis - Monotonic tensile properties (E, Yield, UTS, Break).
"""

import os
import csv
import time
import numpy as np
import pyqtgraph as pg

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QGroupBox, QFormLayout, QLineEdit, QMessageBox, QSplitter,
    QSizePolicy, QTextEdit, QButtonGroup, QRadioButton, QStackedWidget, QFrame
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator

from constants import PRIMARY_COLOR, ACCENT_COLOR

try:
    from execution.material_analysis import MaterialAnalysisEngine, AnalysisResult
except ImportError:
    MaterialAnalysisEngine = None
    AnalysisResult = None

try:
    from execution.creep_analysis import CreepAnalysisEngine, CreepAnalysisResult
except ImportError:
    CreepAnalysisEngine = None
    CreepAnalysisResult = None

try:
    from execution.pdf_reporter import PDFReporter
except ImportError:
    PDFReporter = None

import pyqtgraph.exporters

from logging_config import get_logger
logger = get_logger(__name__)

from gui_panels import setup_plot_style


class AnalysisReportingPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.tensile_engine = MaterialAnalysisEngine() if MaterialAnalysisEngine else None
        self.creep_engine = CreepAnalysisEngine() if CreepAnalysisEngine else None

        # Modes: "creep" (Default) or "tensile"
        self.current_mode = "creep"
        self.current_data = None  # Dict: {"time": arr, "load": arr, "disp": arr}
        self.analysis_result = None  # CreepAnalysisResult or AnalysisResult

        self.init_ui()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # ------------------------------------------------------------------
        # Top Bar: File Loading & Mode Selector Toggle
        # ------------------------------------------------------------------
        top_bar = QHBoxLayout()
        top_bar.setSpacing(15)

        self.btn_load_csv = QPushButton("Load Test Data (CSV)")
        self.btn_load_csv.setMinimumHeight(42)
        self.btn_load_csv.setStyleSheet(
            f"QPushButton {{ background-color: {PRIMARY_COLOR}; color: white; font-weight: bold; padding: 0 20px; font-size: 15px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {ACCENT_COLOR}; }}"
        )
        self.lbl_loaded_file = QLabel("No file loaded")
        self.lbl_loaded_file.setStyleSheet("color: #7f8c8d; font-style: italic; font-size: 15px;")
        
        top_bar.addWidget(self.btn_load_csv)
        top_bar.addWidget(self.lbl_loaded_file)
        top_bar.addStretch()

        # Mode Selector Toggle Group
        lbl_mode = QLabel("Analysis Mode:")
        lbl_mode.setStyleSheet("font-weight: bold; font-size: 16px; color: #2c3e50;")
        top_bar.addWidget(lbl_mode)

        mode_container = QFrame()
        mode_container.setStyleSheet(
            "QFrame { background-color: #e4e7ea; border-radius: 8px; padding: 4px; border: 1px solid #bdc3c7; }"
        )
        mode_layout = QHBoxLayout(mode_container)
        mode_layout.setContentsMargins(4, 3, 4, 3)
        mode_layout.setSpacing(6)

        self.btn_mode_creep = QPushButton("Strain Rate - Creep (Default)")
        self.btn_mode_creep.setCheckable(True)
        self.btn_mode_creep.setChecked(True)
        self.btn_mode_creep.setMinimumHeight(42)
        self.btn_mode_creep.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.btn_mode_tensile = QPushButton("Stress vs. Strain")
        self.btn_mode_tensile.setCheckable(True)
        self.btn_mode_tensile.setMinimumHeight(42)
        self.btn_mode_tensile.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.btn_mode_creep, 0)
        self.mode_group.addButton(self.btn_mode_tensile, 1)

        self._update_toggle_styles()

        mode_layout.addWidget(self.btn_mode_creep)
        mode_layout.addWidget(self.btn_mode_tensile)
        top_bar.addWidget(mode_container)

        main_layout.addLayout(top_bar)

        # ------------------------------------------------------------------
        # Main Splitter: Left Controls & Right Plot
        # ------------------------------------------------------------------
        splitter = QSplitter(Qt.Horizontal)
        
        # Left Panel: Parameters & Dynamic Results
        left_panel = QWidget()
        left_panel.setMinimumWidth(380)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(15)

        # Specimen Dimensions Group
        grp_dims = QGroupBox("Specimen Dimensions")
        dim_form = QFormLayout(grp_dims)
        dim_form.setSpacing(10)

        self.inp_area = QLineEdit("37.5")
        self.inp_area.setValidator(QDoubleValidator())
        self.inp_gauge = QLineEdit("50.0")
        self.inp_gauge.setValidator(QDoubleValidator())

        dim_form.addRow("Area (mm²):", self.inp_area)
        dim_form.addRow("Gauge Length (mm):", self.inp_gauge)
        left_layout.addWidget(grp_dims)

        # Action Buttons
        self.btn_analyze = QPushButton("Run Analysis")
        self.btn_analyze.setMinimumHeight(45)
        self.btn_analyze.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: white; font-weight: bold; font-size: 16px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {PRIMARY_COLOR}; }}"
        )
        left_layout.addWidget(self.btn_analyze)

        self.btn_report = QPushButton("Generate PDF Report")
        self.btn_report.setMinimumHeight(45)
        self.btn_report.setStyleSheet(
            "QPushButton { background-color: #27ae60; color: white; font-weight: bold; font-size: 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #2ecc71; }"
            "QPushButton:disabled { background-color: #bdc3c7; }"
        )
        self.btn_report.setEnabled(False)
        left_layout.addWidget(self.btn_report)

        # Dynamic Results Stack
        self.results_stack = QStackedWidget()

        # Page 0: Creep & Strain Rate Results
        page_creep = QWidget()
        page_creep_layout = QVBoxLayout(page_creep)
        page_creep_layout.setContentsMargins(0, 0, 0, 0)

        grp_creep_results = QGroupBox("Creep && Strain Rate Properties")
        res_creep_form = QFormLayout(grp_creep_results)
        res_creep_form.setSpacing(10)

        val_style = f"font-size: 16px; font-weight: bold; color: {PRIMARY_COLOR};"

        self.lbl_creep_stress = QLabel("--")
        self.lbl_creep_stress.setStyleSheet(val_style)
        self.lbl_creep_rate_hr = QLabel("--")
        self.lbl_creep_rate_hr.setStyleSheet(val_style)
        self.lbl_creep_rate_sec = QLabel("--")
        self.lbl_creep_rate_sec.setStyleSheet(val_style)
        self.lbl_creep_r2 = QLabel("--")
        self.lbl_creep_r2.setStyleSheet(val_style)
        self.lbl_creep_strain = QLabel("--")
        self.lbl_creep_strain.setStyleSheet(val_style)
        self.lbl_creep_duration = QLabel("--")
        self.lbl_creep_duration.setStyleSheet(val_style)
        self.lbl_creep_rupture = QLabel("--")
        self.lbl_creep_rupture.setStyleSheet(val_style)

        res_creep_form.addRow("Mean Hold Stress:", self.lbl_creep_stress)
        res_creep_form.addRow("Steady-State Creep Rate (%/hr):", self.lbl_creep_rate_hr)
        res_creep_form.addRow("Steady-State Creep Rate (1/s):", self.lbl_creep_rate_sec)
        res_creep_form.addRow("Steady-State Linear Fit (R²):", self.lbl_creep_r2)
        res_creep_form.addRow("Total Creep Strain:", self.lbl_creep_strain)
        res_creep_form.addRow("Total Test Duration:", self.lbl_creep_duration)
        res_creep_form.addRow("Rupture Status:", self.lbl_creep_rupture)

        self.lbl_creep_hint = QLabel("Note: Drag the blue shaded region on the\nchart to manually adjust the steady-state window.")
        self.lbl_creep_hint.setStyleSheet("color: #7f8c8d; font-style: italic; font-size: 13px;")

        page_creep_layout.addWidget(grp_creep_results)
        page_creep_layout.addWidget(self.lbl_creep_hint)
        self.results_stack.addWidget(page_creep)  # Index 0

        # Page 1: Tensile Results
        page_tensile = QWidget()
        page_tensile_layout = QVBoxLayout(page_tensile)
        page_tensile_layout.setContentsMargins(0, 0, 0, 0)

        grp_tensile_results = QGroupBox("Tensile Mechanical Properties")
        res_tensile_form = QFormLayout(grp_tensile_results)
        res_tensile_form.setSpacing(10)

        self.lbl_e = QLabel("--")
        self.lbl_e.setStyleSheet(val_style)
        self.lbl_ys = QLabel("--")
        self.lbl_ys.setStyleSheet(val_style)
        self.lbl_uts = QLabel("--")
        self.lbl_uts.setStyleSheet(val_style)
        self.lbl_break = QLabel("--")
        self.lbl_break.setStyleSheet(val_style)

        res_tensile_form.addRow(QLabel("Young's Modulus (E):"), self.lbl_e)
        res_tensile_form.addRow(QLabel("Yield Strength (0.2% Offset):"), self.lbl_ys)
        res_tensile_form.addRow(QLabel("Ultimate Tensile Strength (UTS):"), self.lbl_uts)
        res_tensile_form.addRow(QLabel("Break Stress:"), self.lbl_break)

        self.lbl_tensile_hint = QLabel("Note: Drag the blue shaded region on the\nchart to manually override the elastic range.")
        self.lbl_tensile_hint.setStyleSheet("color: #7f8c8d; font-style: italic; font-size: 13px;")

        page_tensile_layout.addWidget(grp_tensile_results)
        page_tensile_layout.addWidget(self.lbl_tensile_hint)
        self.results_stack.addWidget(page_tensile)  # Index 1

        left_layout.addWidget(self.results_stack)

        # Operator Comments Group
        grp_comments = QGroupBox("Operator Notes")
        com_layout = QVBoxLayout(grp_comments)
        com_layout.setSpacing(5)
        
        self.txt_comments = QTextEdit()
        self.txt_comments.setPlaceholderText("Enter any test notes or operator comments here...")
        self.txt_comments.setMaximumHeight(80)
        
        self.btn_save_notes = QPushButton("Save Notes")
        self.btn_save_notes.setMinimumHeight(38)
        self.btn_save_notes.setStyleSheet(
            f"QPushButton {{ background-color: {PRIMARY_COLOR}; color: white; font-weight: bold; font-size: 13px; padding: 5px; border-radius: 4px; }}"
            f"QPushButton:hover {{ background-color: {ACCENT_COLOR}; }}"
        )
        self.btn_save_notes.clicked.connect(self.save_notes)
        
        com_layout.addWidget(self.txt_comments)
        com_layout.addWidget(self.btn_save_notes)
        left_layout.addWidget(grp_comments)
        
        left_layout.addStretch()
        splitter.addWidget(left_panel)

        # ------------------------------------------------------------------
        # Right Panel: Plot Canvas
        # ------------------------------------------------------------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.addLegend(labelTextSize='11pt')

        # Curves for Creep Mode (legend items added dynamically)
        self.curve_creep = self.plot_widget.plot(pen=pg.mkPen(color='#1b4f72', width=3))
        self.curve_steady_tangent = self.plot_widget.plot(pen=pg.mkPen(color='#e67e22', width=2, style=Qt.DashLine))

        # Curves for Tensile Mode (legend items added dynamically)
        self.curve_tensile = self.plot_widget.plot(pen=pg.mkPen(color='#2c3e50', width=3))
        self.curve_modulus = self.plot_widget.plot(pen=pg.mkPen(color='#2980b9', width=2, style=Qt.DashLine))
        self.curve_offset = self.plot_widget.plot(pen=pg.mkPen(color='#8e44ad', width=2, style=Qt.DotLine))
        self.curve_yield = self.plot_widget.plot(pen=None, symbol='o', symbolSize=14, symbolBrush='#8e44ad')
        self.curve_uts = self.plot_widget.plot(pen=None, symbol='t', symbolSize=16, symbolBrush='#e74c3c')
        self.curve_break = self.plot_widget.plot(pen=None, symbol='x', symbolSize=16, symbolBrush='#e67e22')
        
        # Interactive region for steady-state / elastic adjustment
        self.interactive_region = pg.LinearRegionItem(brush=pg.mkBrush(41, 128, 185, 45))
        self.interactive_region.setZValue(-10)
        self.plot_widget.addItem(self.interactive_region)
        self.interactive_region.hide()
        self.interactive_region.sigRegionChangeFinished.connect(self.on_region_changed)

        right_layout.addWidget(self.plot_widget)
        splitter.addWidget(right_panel)
        
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        splitter.setSizes([400, 800])
        main_layout.addWidget(splitter, 1)

        # ------------------------------------------------------------------
        # Signals & Event Handlers
        # ------------------------------------------------------------------
        self.btn_load_csv.clicked.connect(self.load_csv)
        self.btn_analyze.clicked.connect(self.run_analysis)
        self.btn_report.clicked.connect(self.generate_report)
        self.btn_mode_creep.clicked.connect(lambda: self.set_mode("creep"))
        self.btn_mode_tensile.clicked.connect(lambda: self.set_mode("tensile"))

        # Initialize in creep mode
        self.apply_mode_ui()

    def _update_toggle_styles(self):
        active_style = (
            f"QPushButton {{ background-color: {PRIMARY_COLOR}; color: white; font-weight: bold; "
            f"border-radius: 6px; padding: 7px 22px; font-size: 15px; border: none; }}"
        )
        inactive_style = (
            "QPushButton { background-color: transparent; color: #576574; font-weight: bold; "
            "border: none; padding: 7px 22px; font-size: 15px; border-radius: 6px; }"
            "QPushButton:hover { background-color: #dcdde1; color: #2c3e50; }"
        )
        if self.current_mode == "creep":
            self.btn_mode_creep.setStyleSheet(active_style)
            self.btn_mode_tensile.setStyleSheet(inactive_style)
        else:
            self.btn_mode_tensile.setStyleSheet(active_style)
            self.btn_mode_creep.setStyleSheet(inactive_style)

    def set_mode(self, mode: str):
        if self.current_mode == mode:
            return
        self.current_mode = mode
        self._update_toggle_styles()
        self.apply_mode_ui()
        if self.current_data is not None:
            self.run_analysis()

    def apply_mode_ui(self):
        """Configure UI layout, plot labels, and curves according to active mode."""
        self.interactive_region.hide()

        # Dynamically refresh legend so only curves for the selected engine appear
        legend = self.plot_widget.plotItem.legend
        if legend is not None:
            legend.clear()

        if self.current_mode == "creep":
            self.results_stack.setCurrentIndex(0)
            setup_plot_style(self.plot_widget, "Creep Curve — Strain vs. Time", "Time (hours)", "Creep Strain (%)")
            self.curve_creep.show()
            self.curve_steady_tangent.show()
            self.curve_tensile.hide()
            self.curve_modulus.hide()
            self.curve_offset.hide()
            self.curve_yield.hide()
            self.curve_uts.hide()
            self.curve_break.hide()

            if legend is not None:
                legend.addItem(self.curve_creep, "Creep Strain")
                legend.addItem(self.curve_steady_tangent, "Steady-State Fit")
        else:
            self.results_stack.setCurrentIndex(1)
            setup_plot_style(self.plot_widget, "Stress-Strain Analysis", "Strain (mm/mm)", "Stress (MPa)")
            self.curve_creep.hide()
            self.curve_steady_tangent.hide()
            self.curve_tensile.show()
            self.curve_modulus.show()
            self.curve_offset.show()
            self.curve_yield.show()
            self.curve_uts.show()
            self.curve_break.show()

            if legend is not None:
                legend.addItem(self.curve_tensile, "Stress-Strain")
                legend.addItem(self.curve_modulus, "Modulus Fit")
                legend.addItem(self.curve_offset, "0.2% Offset")
                legend.addItem(self.curve_yield, "Yield Point")
                legend.addItem(self.curve_uts, "UTS")
                legend.addItem(self.curve_break, "Break")

    def load_csv(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open Test Data", "", "CSV Files (*.csv);;All Files (*)")
        if not filepath:
            return

        try:
            time_data = []
            load_data = []
            disp_data = []
            
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    QMessageBox.warning(self, "CSV Error", "The CSV file appears to be empty or missing headers.")
                    return

                headers = [h.strip() for h in reader.fieldnames]
                
                # Identify columns
                time_col = next((h for h in headers if 'time' in h.lower()), None)
                
                load_col = next((h for h in headers if 'load' in h.lower() and 'uni' in h.lower()), None)
                if not load_col:
                    load_col = next((h for h in headers if 'load' in h.lower()), None)
                    
                disp_col = next((h for h in headers if 'disp' in h.lower() and 'uni' in h.lower()), None)
                if not disp_col:
                    disp_col = next((h for h in headers if 'disp' in h.lower()), None)
                
                if not load_col or not disp_col:
                    QMessageBox.warning(self, "Column Error", "Could not automatically identify Load and Displacement columns.")
                    return

                row_idx = 0
                for row in reader:
                    try:
                        l_val = float(row[load_col])
                        d_val = float(row[disp_col])
                        if time_col and row.get(time_col):
                            t_val = float(row[time_col])
                        else:
                            t_val = float(row_idx)  # Fallback to sequential index
                        time_data.append(t_val)
                        load_data.append(l_val)
                        disp_data.append(d_val)
                        row_idx += 1
                    except (ValueError, TypeError):
                        pass

            if not load_data:
                QMessageBox.warning(self, "Data Error", "No valid numeric data found in selected columns.")
                return

            self.current_data = {
                "time": np.array(time_data, dtype=float),
                "load": np.array(load_data, dtype=float),
                "disp": np.array(disp_data, dtype=float)
            }
            self.lbl_loaded_file.setText(os.path.basename(filepath))
            
            # Auto-run analysis when data is loaded
            self.run_analysis()
            
        except Exception as e:
            logger.error("Error loading CSV for analysis: %s", e)
            QMessageBox.critical(self, "File Error", f"Failed to load file: {e}")

    def run_analysis(self):
        if not self.current_data:
            QMessageBox.warning(self, "No Data", "Please load a test data CSV first.")
            return

        try:
            area = float(self.inp_area.text())
            gauge = float(self.inp_gauge.text())
            if area <= 0 or gauge <= 0:
                raise ValueError("Dimensions must be positive.")
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please enter valid positive numbers for dimensions.")
            return

        time_arr = self.current_data["time"]
        load_arr = self.current_data["load"]
        disp_arr = self.current_data["disp"]

        # Check manual region override
        override_range = None
        if self.interactive_region.isVisible():
            r_min, r_max = self.interactive_region.getRegion()
            override_range = (r_min, r_max)

        if self.current_mode == "creep":
            if not self.creep_engine:
                QMessageBox.critical(self, "Engine Error", "CreepAnalysisEngine is not available.")
                return
            try:
                self.analysis_result = self.creep_engine.analyse(
                    time_arr.tolist(), load_arr.tolist(), disp_arr.tolist(),
                    area, gauge, manual_steady_state_range=override_range
                )
                self.update_creep_ui_results()
            except Exception as e:
                logger.error("Creep engine error: %s", e)
                QMessageBox.critical(self, "Analysis Error", f"Failed to run creep analysis:\n{str(e)}")
        else:
            if not self.tensile_engine:
                QMessageBox.critical(self, "Engine Error", "MaterialAnalysisEngine is not available.")
                return
            try:
                self.analysis_result = self.tensile_engine.analyse(
                    load_arr.tolist(), disp_arr.tolist(), area, gauge,
                    manual_elastic_range=override_range
                )
                self.update_tensile_ui_results()
            except Exception as e:
                logger.error("Tensile engine error: %s", e)
                QMessageBox.critical(self, "Analysis Error", f"Failed to run tensile analysis:\n{str(e)}")

    def update_creep_ui_results(self):
        res: CreepAnalysisResult = self.analysis_result
        if not res:
            return

        # Update metric labels
        self.lbl_creep_stress.setText(f"{res.mean_stress_mpa:,.2f} MPa")
        self.lbl_creep_rate_hr.setText(f"{res.steady_state_rate_pct_per_hr:.4e} %/hr")
        self.lbl_creep_rate_sec.setText(f"{res.steady_state_rate_per_sec:.4e} 1/s")
        self.lbl_creep_r2.setText(f"{res.steady_state_r2:.4f}")
        self.lbl_creep_strain.setText(f"{res.total_creep_strain_pct:,.3f} %")
        self.lbl_creep_duration.setText(f"{res.total_test_duration_hr:,.2f} hr")
        if res.rupture_detected:
            self.lbl_creep_rupture.setText(f"Ruptured @ {res.rupture_time_hr:.2f} hr")
            self.lbl_creep_rupture.setStyleSheet("font-size: 16px; font-weight: bold; color: #c0392b;")
        else:
            self.lbl_creep_rupture.setText("Ongoing / Completed (No Rupture)")
            self.lbl_creep_rupture.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {PRIMARY_COLOR};")

        self.btn_report.setEnabled(True)

        # Plot curves
        t_hr = np.array(res.time_hr)
        strain = np.array(res.strain_pct)
        self.curve_creep.setData(t_hr, strain)

        if res.fit_line:
            self.curve_steady_tangent.setData(res.fit_line[0], res.fit_line[1])
        else:
            self.curve_steady_tangent.setData([], [])

        # Update interactive region bounds to match steady-state window
        if not self.interactive_region.isVisible() and res.steady_state_end_hr > res.steady_state_start_hr:
            self.interactive_region.show()
            self.interactive_region.blockSignals(True)
            self.interactive_region.setRegion([res.steady_state_start_hr, res.steady_state_end_hr])
            self.interactive_region.blockSignals(False)

        self.plot_widget.autoRange()

        if res.warnings:
            warn_text = "\n".join(f"• {w}" for w in res.warnings)
            QMessageBox.information(self, "Analysis Warnings", f"Creep analysis completed with notes:\n\n{warn_text}")

    def update_tensile_ui_results(self):
        res: AnalysisResult = self.analysis_result
        if not res:
            return

        # Update labels
        self.lbl_e.setText(f"{res.youngs_modulus:,.1f} MPa" if res.youngs_modulus and res.youngs_modulus > 0 else "--")
        self.lbl_ys.setText(f"{res.yield_stress:,.2f} MPa" if res.yield_stress is not None else "--")
        self.lbl_uts.setText(f"{res.uts_stress:,.2f} MPa" if res.uts_stress is not None else "--")
        self.lbl_break.setText(f"{res.break_stress:,.2f} MPa" if res.break_stress is not None else "--")

        self.btn_report.setEnabled(True)

        # Update plot
        strain = np.array(res.strain)
        stress = np.array(res.stress)
        self.curve_tensile.setData(strain, stress)
        
        if res.modulus_line:
            self.curve_modulus.setData(res.modulus_line[0], res.modulus_line[1])
        else:
            self.curve_modulus.setData([], [])
            
        if res.offset_line:
            self.curve_offset.setData(res.offset_line[0], res.offset_line[1])
        else:
            self.curve_offset.setData([], [])

        # Points
        if res.yield_stress is not None:
            self.curve_yield.setData([res.yield_strain], [res.yield_stress])
        else:
            self.curve_yield.setData([], [])
            
        if res.uts_stress is not None:
            self.curve_uts.setData([res.uts_strain], [res.uts_stress])
        else:
            self.curve_uts.setData([], [])
            
        if res.break_stress is not None:
            self.curve_break.setData([res.break_strain], [res.break_stress])
        else:
            self.curve_break.setData([], [])

        # Update interactive region bounds to match elastic region
        if not self.interactive_region.isVisible() and res.elastic_end_idx > res.elastic_start_idx:
            self.interactive_region.show()
            self.interactive_region.blockSignals(True)
            self.interactive_region.setRegion([strain[res.elastic_start_idx], strain[res.elastic_end_idx]])
            self.interactive_region.blockSignals(False)

        self.plot_widget.autoRange()

        if res.warnings:
            warn_text = "\n".join(f"• {w}" for w in res.warnings)
            QMessageBox.information(self, "Analysis Warnings", f"Tensile analysis completed with notes:\n\n{warn_text}")

    def on_region_changed(self):
        """When user manually drags the interactive region, re-run analysis with override."""
        if self.current_data is not None:
            self.run_analysis()

    def save_notes(self):
        """Acknowledge operator notes saved."""
        QMessageBox.information(self, "Notes Saved", "Operator notes have been saved for the PDF report.")

    def generate_report(self):
        if not self.analysis_result:
            QMessageBox.warning(self, "No Data", "Please run analysis before generating a report.")
            return

        default_name = "Creep_Test_Report.pdf" if self.current_mode == "creep" else "Tensile_Test_Report.pdf"
        save_path, _ = QFileDialog.getSaveFileName(self, "Save PDF Report", default_name, "PDF Files (*.pdf)")
        if not save_path:
            return

        if not save_path.lower().endswith('.pdf'):
            save_path += '.pdf'

        # 1. Capture pyqtgraph plot to temporary image
        temp_img_path = os.path.join(os.path.dirname(save_path), "temp_plot_export.png")
        try:
            exporter = pyqtgraph.exporters.ImageExporter(self.plot_widget.scene())
            exporter.export(temp_img_path)
        except Exception as e:
            logger.error("Failed to export plot image: %s", e)
            QMessageBox.critical(self, "Export Error", f"Failed to capture graph image:\n{e}")
            return

        # 2. Gather metadata
        filename = self.lbl_loaded_file.text()
        area = self.inp_area.text()
        gauge = self.inp_gauge.text()
        comments = self.txt_comments.toPlainText()

        metadata = {
            'filename': filename,
            'area': area,
            'gauge_length': gauge,
            'date': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Pull main window parameters if available
        mw = self.window()
        if hasattr(mw, '_sample_id'):
            metadata['sample_id'] = getattr(mw, '_sample_id', '--')
            metadata['sample_type'] = getattr(mw, '_sample_type', '--')
            metadata['width'] = getattr(mw, '_sample_width', '--')
            metadata['thickness'] = getattr(mw, '_sample_thickness', '--')
            metadata['radius'] = getattr(mw, '_sample_radius', '--')
            metadata['gauge_length'] = getattr(mw, '_sample_gauge_length', metadata.get('gauge_length', '--'))

        # 3. Format results according to active mode
        if self.current_mode == "creep":
            c_res: CreepAnalysisResult = self.analysis_result
            results = {
                'steady_state_rate_hr': f"{c_res.steady_state_rate_pct_per_hr:.4e}",
                'steady_state_rate_sec': f"{c_res.steady_state_rate_per_sec:.4e}",
                'steady_state_r2': f"{c_res.steady_state_r2:.4f}",
                'mean_stress': f"{c_res.mean_stress_mpa:,.2f}",
                'total_strain': f"{c_res.total_creep_strain_pct:,.3f}",
                'test_duration': f"{c_res.total_test_duration_hr:,.2f}",
                'rupture_status': f"Ruptured @ {c_res.rupture_time_hr:.2f} hr" if c_res.rupture_detected else "Completed (No Rupture)"
            }
        else:
            t_res: AnalysisResult = self.analysis_result
            results = {
                'e_modulus': f"{t_res.youngs_modulus:,.1f}" if t_res.youngs_modulus and t_res.youngs_modulus > 0 else "--",
                'yield_strength': f"{t_res.yield_stress:,.2f}" if t_res.yield_stress is not None else "--",
                'uts': f"{t_res.uts_stress:,.2f}" if t_res.uts_stress is not None else "--",
                'break_stress': f"{t_res.break_stress:,.2f}" if t_res.break_stress is not None else "--"
            }

        # 4. Generate PDF Report
        reporter = PDFReporter()
        try:
            reporter.generate_report(save_path, metadata, results, comments, temp_img_path, report_type=self.current_mode)
            
            msg = QMessageBox(self)
            msg.setWindowTitle("Success")
            msg.setText(f"Report successfully generated at:\n{save_path}")
            open_btn = msg.addButton("Open Report", QMessageBox.ActionRole)
            msg.addButton(QMessageBox.Ok)
            msg.exec()
            
            if msg.clickedButton() == open_btn:
                os.startfile(os.path.normpath(save_path))
                
        except Exception as e:
            QMessageBox.critical(self, "Report Error", f"Failed to generate PDF report:\n{e}")
        finally:
            if os.path.exists(temp_img_path):
                try:
                    os.remove(temp_img_path)
                except Exception:
                    pass
