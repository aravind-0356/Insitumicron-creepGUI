# gui_panels.py
import math
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFormLayout, QMessageBox, QComboBox, QFileDialog, QSizePolicy, QGroupBox,
    QTextEdit
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QDoubleValidator

# --- Corporate Colors (centralized) ---
from constants import PRIMARY_COLOR, ACCENT_COLOR

from logging_config import get_logger
logger = get_logger(__name__)

from PySide6.QtCore import QEvent, QObject

class PlotResizeFilter(QObject):
    """Event filter to handle dynamic repositioning of the reset button on plot resize."""
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            if hasattr(obj, '_custom_reset_btn'):
                obj._custom_reset_btn.move(5, obj.height() - 30)
        return super().eventFilter(obj, event)

# --- Helper for graph styling ---
def setup_plot_style(plot, title, xlabel, ylabel):
    """
    Set up professional plot styling with larger, bolder fonts and thicker lines.
    """
    plot.setBackground('w')
    plot.setTitle(title, color='#2c2f33', size='18pt')
    plot.setLabel('left', ylabel, color='#2c2f33', size='16pt')
    plot.setLabel('bottom', xlabel, color='#2c2f33', size='16pt')
    plot.showGrid(x=True, y=True, alpha=0.3)

    # Thicker, more visible axes
    pen = pg.mkPen(color='#2c2f33', width=2)
    plot.getAxis('left').setPen(pen)
    plot.getAxis('bottom').setPen(pen)
    plot.getAxis('left').setTextPen(pen)
    plot.getAxis('bottom').setTextPen(pen)

    # Increase tick font size for better readability
    font = pg.QtGui.QFont()
    font.setPointSize(11)
    plot.getAxis('left').setStyle(tickFont=font)
    plot.getAxis('bottom').setStyle(tickFont=font)

    # Hide the default auto-range button ("A") and add custom "Reset" button
    plot_item = plot.getPlotItem()
    plot_item.hideButtons()  # Hide the default "A" button
    
    # Create custom reset button
    from PySide6.QtWidgets import QPushButton
    reset_btn = QPushButton("Reset", plot)
    reset_btn.setStyleSheet("""
        QPushButton {
            background-color: rgba(255, 255, 255, 0.9);
            border: 1px solid #bdc3c7;
            border-radius: 3px;
            padding: 3px 8px;
            font-size: 11px;
            font-weight: bold;
            color: #2c3e50;
        }
        QPushButton:hover {
            background-color: #ecf0f1;
            border-color: #005b7f;
        }
    """)
    reset_btn.setFixedSize(50, 22)
    reset_btn.move(5, plot.height() - 30)  # Position bottom-left
    reset_btn.clicked.connect(lambda: plot.getPlotItem().getViewBox().enableAutoRange(x=True, y=True))
    
    # Store reference and set up resize handler via EventFilter
    plot._custom_reset_btn = reset_btn
    
    # Use EventFilter instead of monkey-patching resizeEvent
    filter_obj = PlotResizeFilter(plot)
    plot.installEventFilter(filter_obj)
    plot._resize_filter = filter_obj  # Keep reference so it isn't garbage collected

# =========================================================
# CONNECTION PANEL
# =========================================================
class ConnectionPanel(QWidget):
    def __init__(self, serial_handler):
        super().__init__()
        self.serial = serial_handler
        self._user_disconnected = False  # True when user explicitly clicks Disconnect
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Serial Port Connection")
        title.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {PRIMARY_COLOR}; margin-bottom: 10px;")
        layout.addWidget(title, alignment=Qt.AlignCenter)

        form_layout = QFormLayout()
        form_layout.setSpacing(20)
        self.port_combo = QComboBox()
        lbl_port = QLabel("Port:")
        lbl_port.setStyleSheet("font-weight: bold;")
        form_layout.addRow(lbl_port, self.port_combo)
        layout.addLayout(form_layout)

        layout.addSpacing(25)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        self.refresh_btn = QPushButton("Refresh")
        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")

        button_layout.addWidget(self.refresh_btn)
        button_layout.addWidget(self.connect_btn)
        button_layout.addWidget(self.disconnect_btn)
        layout.addLayout(button_layout)

        layout.addSpacing(25)

        self.status_label = QLabel("Disconnected")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("""
            background-color: #e74c3c; 
            color: white; 
            padding: 14px; 
            border-radius: 6px; 
            font-weight: bold;
        """)
        layout.addWidget(self.status_label)
        
        # NI-DAQ section removed
        layout.addStretch()

        self.setLayout(layout)

        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.connect_port)
        self.disconnect_btn.clicked.connect(self.disconnect_port)
        self.serial.connection_status_changed.connect(self.update_status_display)
        self.refresh_ports()
        
        # Auto-refresh timer - refreshes COM ports every 2 seconds when not connected
        from PySide6.QtCore import QTimer
        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self._auto_refresh_ports)
        self.auto_refresh_timer.start(2000)  # Every 2 seconds

    def refresh_ports(self):
        ports = self.serial.get_ports()
        current_items = [self.port_combo.itemText(i) for i in range(self.port_combo.count())]
        
        if not ports:
            if current_items != ["No COM Ports Found"]:
                self.port_combo.clear()
                self.port_combo.addItem("No COM Ports Found")
            self.connect_btn.setEnabled(False)
        else:
            if current_items != ports:
                curr = self.port_combo.currentText()
                self.port_combo.clear()
                self.port_combo.addItems(ports)
                idx = self.port_combo.findText(curr)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)
            self.connect_btn.setEnabled(not self.serial.is_connected())

    def connect_port(self):
        port = self.port_combo.currentText()
        if port and port != "No COM Ports Found":
            # Resume auto-scan on manual connect attempt
            self._user_disconnected = False
            if not self.auto_refresh_timer.isActive():
                self.auto_refresh_timer.start(2000)

            try:
                success = self.serial.connect(port, baudrate=115200)
                if not success:
                    QMessageBox.critical(self, "Connection Error",
                                         f"Failed to open {port}.\nCheck if the device is busy or permissions are denied.")
            except Exception as e:
                logger.error("Connection attempt crashed: %s", e)
                QMessageBox.critical(
                    self, "Connection Failed",
                    f"Could not connect to {port}.\n\n"
                    f"This may not be the correct device port.\n"
                    f"Error: {e}")

    def disconnect_port(self):
        # User explicitly disconnected — stop auto-scan
        self._user_disconnected = True
        self.auto_refresh_timer.stop()
        self.serial.disconnect()
        logger.info("User disconnected — auto-scan stopped")

    def _auto_refresh_ports(self):
        """Auto-detect STM32 via VID/PID and auto-connect when found."""
        # Skip if user explicitly disconnected
        if self._user_disconnected:
            return

        if self.serial.is_connected() or getattr(self.serial, 'reconnecting', False):
            return  # Already connected or serial_handler reconnect loop is actively handling it

        # Refresh the dropdown
        self.refresh_ports()

        # Try to auto-detect STM32
        stm32_port = self.serial.detect_stm32_port()
        if stm32_port:
            # Select the detected port in the dropdown
            idx = self.port_combo.findText(stm32_port)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            logger.info("STM32 auto-detected on %s, connecting...", stm32_port)
            try:
                success = self.serial.connect(stm32_port, baudrate=115200)
                if not success:
                    logger.warning("STM32 auto-connect failed on %s", stm32_port)
            except Exception as e:
                logger.warning("STM32 auto-connect error on %s: %s", stm32_port, e)
        else:
            self.status_label.setText("Scanning for Controller...")
            self.status_label.setStyleSheet(
                "background-color: #f39c12; color: white; padding: 14px;"
                " border-radius: 6px; font-weight: bold;")

    @Slot(bool, str)
    def update_status_display(self, is_connected, msg):
        if is_connected:
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet(
                "background-color: #2ecc71; color: white; padding: 14px; border-radius: 6px; font-weight: bold;")
        elif self._user_disconnected:
            # User manually disconnected — show clear status, no scanning
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet(
                "background-color: #e74c3c; color: white; padding: 14px; border-radius: 6px; font-weight: bold;")
        else:
            # Hardware unplug or error — auto-scan will continue
            self.status_label.setText("Disconnected — Scanning...")
            self.status_label.setStyleSheet(
                "background-color: #f39c12; color: white; padding: 14px; border-radius: 6px; font-weight: bold;")
        self.connect_btn.setEnabled(not is_connected)
        self.disconnect_btn.setEnabled(is_connected)
        self.port_combo.setEnabled(not is_connected)

# =========================================================
# SAMPLE PANEL (WITH FOLDER SELECTION AND SAVE OPTIONS)
# =========================================================
class SamplePanel(QWidget):
    sample_parameters_changed = Signal(dict)
    output_folder_changed = Signal(str)
    save_sensor_changed = Signal(bool)
    save_images_changed = Signal(bool)

    def __init__(self, init_sample_id="Default", init_sample_type="Rectangular", 
                 init_width=None, init_thick=None, init_rad=None, init_diam=None,
                 init_custom_area=None, init_gauge=None, init_extra="",
                 init_temp="", init_stress="", init_weight=""):
        super().__init__()
        self._init_sample_id = init_sample_id
        self._init_sample_type = init_sample_type
        self._init_width = init_width
        self._init_thick = init_thick
        if init_rad is not None and init_diam is None:
            self._init_diam = float(init_rad) * 2.0
        else:
            self._init_diam = init_diam
        self._init_custom_area = init_custom_area
        self._init_gauge = init_gauge
        self._init_extra = init_extra
        self._init_temp = str(init_temp) if init_temp is not None else ""
        self._init_stress = str(init_stress) if init_stress is not None else ""
        self._init_weight = str(init_weight) if init_weight is not None else ""
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Sample Configuration")
        title.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {PRIMARY_COLOR}; margin-bottom: 10px;")
        layout.addWidget(title, alignment=Qt.AlignCenter)

        # === DOWNLOAD SETTINGS GROUP ===
        download_grp = QGroupBox("Download Settings")
        download_grp.setStyleSheet(f"""
            QGroupBox {{
                font-size: 22px;
                font-weight: bold;
                color: {PRIMARY_COLOR};
                border: 1px solid #bdc3c7;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
            }}
        """)
        download_layout = QVBoxLayout(download_grp)
        download_layout.setContentsMargins(15, 20, 15, 15)
        download_layout.setSpacing(15)

        # Save Location
        folder_label = QLabel("Save Location:")
        folder_label.setStyleSheet("font-weight: bold; color: #2c3e50;")
        download_layout.addWidget(folder_label)

        folder_row = QHBoxLayout()
        self.folder_path_display = QLineEdit()
        self.folder_path_display.setReadOnly(True)
        self.folder_path_display.setPlaceholderText("No folder selected - Click Browse to select...")
        self.folder_path_display.setStyleSheet("background-color: #ecf0f1; font-style: italic;")

        self.btn_browse_folder = QPushButton("Browse...")
        self.btn_browse_folder.setMinimumWidth(120)
        self.btn_browse_folder.clicked.connect(self.browse_output_folder)

        folder_row.addWidget(self.folder_path_display)
        folder_row.addWidget(self.btn_browse_folder)
        download_layout.addLayout(folder_row)

        # Data to Record
        save_opts_label = QLabel("Data to Record:")
        save_opts_label.setStyleSheet("font-weight: bold; color: #2c3e50; margin-top: 5px;")
        download_layout.addWidget(save_opts_label)

        save_opts_row = QHBoxLayout()
        save_opts_row.setSpacing(30)

        from gui_test_views import StyledCheckBox
        self.chk_save_sensor = StyledCheckBox("Sensor Data (.xlsx)")
        self.chk_save_sensor.setChecked(True)
        self.chk_save_sensor.toggled.connect(self.save_sensor_changed.emit)

        self.chk_save_images = StyledCheckBox("Camera Test Images (.tiff)")
        self.chk_save_images.setChecked(False)
        self.chk_save_images.toggled.connect(self.save_images_changed.emit)

        save_opts_row.addWidget(self.chk_save_sensor)
        save_opts_row.addWidget(self.chk_save_images)
        save_opts_row.addStretch()
        download_layout.addLayout(save_opts_row)

        layout.addWidget(download_grp)

        # === SAMPLE DIMENSIONS GROUP ===
        sample_grp = QGroupBox("Sample Dimensions")
        sample_grp.setStyleSheet(f"""
            QGroupBox {{
                font-size: 22px;
                font-weight: bold;
                color: {PRIMARY_COLOR};
                border: 1px solid #bdc3c7;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
            }}
        """)
        sample_layout = QVBoxLayout(sample_grp)
        sample_layout.setContentsMargins(15, 20, 15, 15)

        form_layout = QFormLayout()
        form_layout.setSpacing(18)

        self.sample_id_input = QLineEdit(self._init_sample_id)
        self.sample_id_input.editingFinished.connect(self.push_params)

        id_label = QLabel("Sample ID:")
        form_layout.addRow(id_label, self.sample_id_input)

        self.sample_type_combo = QComboBox()
        self.sample_type_combo.addItems(["Rectangular", "Circular", "Custom / Direct Area"])
        self.sample_type_combo.setCurrentText(self._init_sample_type)
        self.sample_type_combo.currentTextChanged.connect(self.update_inputs)

        type_label = QLabel("Sample Type:")
        form_layout.addRow(type_label, self.sample_type_combo)

        from PySide6.QtWidgets import QStackedWidget
        self.stack = QStackedWidget()

        # Rectangular
        rect_w = QWidget()
        rect_f = QFormLayout(rect_w)
        rect_f.setSpacing(18)
        rect_f.setContentsMargins(0, 10, 0, 0)

        self.width_inp = QLineEdit(str(self._init_width) if self._init_width is not None else "")
        self.width_inp.setPlaceholderText("e.g. 10.0")
        self.thick_inp = QLineEdit(str(self._init_thick) if self._init_thick is not None else "")
        self.thick_inp.setPlaceholderText("e.g. 1.0")
        self.rect_gauge_inp = QLineEdit(str(self._init_gauge) if self._init_gauge is not None else "")
        self.rect_gauge_inp.setPlaceholderText("e.g. 50.0")

        for inp in [self.width_inp, self.thick_inp, self.rect_gauge_inp]:
            inp.setValidator(QDoubleValidator(0, 1000, 2))
            inp.editingFinished.connect(self.push_params)
        self.width_inp.textChanged.connect(self._update_calc_area)
        self.thick_inp.textChanged.connect(self._update_calc_area)

        rect_f.addRow(QLabel("Width (mm):"), self.width_inp)
        rect_f.addRow(QLabel("Thickness (mm):"), self.thick_inp)
        rect_f.addRow(QLabel("Gauge Length (mm):"), self.rect_gauge_inp)
        self.stack.addWidget(rect_w)

        # Circular
        circ_w = QWidget()
        circ_f = QFormLayout(circ_w)
        circ_f.setSpacing(18)
        circ_f.setContentsMargins(0, 10, 0, 0)

        self.diam_inp = QLineEdit(str(self._init_diam) if self._init_diam is not None else "")
        self.diam_inp.setPlaceholderText("e.g. 10.0")
        self.diam_inp.setValidator(QDoubleValidator(0, 1000, 3))
        self.diam_inp.editingFinished.connect(self.push_params)
        self.diam_inp.textChanged.connect(self._update_calc_area)

        self.circ_gauge_inp = QLineEdit(str(self._init_gauge) if self._init_gauge is not None else "")
        self.circ_gauge_inp.setPlaceholderText("e.g. 50.0")
        self.circ_gauge_inp.setValidator(QDoubleValidator(0, 1000, 2))
        self.circ_gauge_inp.editingFinished.connect(self.push_params)

        circ_f.addRow(QLabel("Diameter (mm):"), self.diam_inp)
        circ_f.addRow(QLabel("Gauge Length (mm):"), self.circ_gauge_inp)
        self.stack.addWidget(circ_w)

        # Custom / Direct Area
        custom_w = QWidget()
        custom_f = QFormLayout(custom_w)
        custom_f.setSpacing(18)
        custom_f.setContentsMargins(0, 10, 0, 0)

        self.custom_area_inp = QLineEdit(str(self._init_custom_area) if self._init_custom_area is not None else "")
        self.custom_area_inp.setPlaceholderText("e.g. 50.0")
        self.custom_area_inp.setValidator(QDoubleValidator(0, 100000, 3))
        self.custom_area_inp.editingFinished.connect(self.push_params)
        self.custom_area_inp.textChanged.connect(self._update_calc_area)

        self.custom_gauge_inp = QLineEdit(str(self._init_gauge) if self._init_gauge is not None else "")
        self.custom_gauge_inp.setPlaceholderText("e.g. 50.0")
        self.custom_gauge_inp.setValidator(QDoubleValidator(0, 1000, 2))
        self.custom_gauge_inp.editingFinished.connect(self.push_params)

        custom_f.addRow(QLabel("Cross-Section Area (mm²):"), self.custom_area_inp)
        custom_f.addRow(QLabel("Gauge Length (mm):"), self.custom_gauge_inp)
        self.stack.addWidget(custom_w)

        sample_layout.addLayout(form_layout)
        sample_layout.addWidget(self.stack)

        # Real-time calculated cross-sectional area display
        self.lbl_calc_area = QLabel("Calculated Area: —")
        self.lbl_calc_area.setStyleSheet("font-weight: bold; color: #005b7f; margin-top: 6px; font-size: 13px;")
        sample_layout.addWidget(self.lbl_calc_area)

        layout.addWidget(sample_grp)

        # === TEST CONDITIONS GROUP ===
        cond_grp = QGroupBox("Test Conditions")
        cond_grp.setStyleSheet(f"""
            QGroupBox {{
                font-size: 22px;
                font-weight: bold;
                color: {PRIMARY_COLOR};
                border: 1px solid #bdc3c7;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
            }}
        """)
        cond_layout = QFormLayout(cond_grp)
        cond_layout.setContentsMargins(15, 20, 15, 15)
        cond_layout.setSpacing(18)

        self.temp_inp = QLineEdit(self._init_temp)
        self.temp_inp.setPlaceholderText("e.g. 650")
        self.temp_inp.setValidator(QDoubleValidator(-273, 2000, 2))
        self.temp_inp.editingFinished.connect(self.push_params)

        self.target_stress_inp = QLineEdit(self._init_stress)
        self.target_stress_inp.setPlaceholderText("e.g. 150")
        self.target_stress_inp.setValidator(QDoubleValidator(0, 5000, 2))
        self.target_stress_inp.editingFinished.connect(self.push_params)

        self.dead_weight_inp = QLineEdit(self._init_weight)
        self.dead_weight_inp.setPlaceholderText("e.g. 25.5")
        self.dead_weight_inp.setValidator(QDoubleValidator(0, 10000, 3))
        self.dead_weight_inp.editingFinished.connect(self.push_params)

        cond_layout.addRow(QLabel("Test Temperature (°C):"), self.temp_inp)
        cond_layout.addRow(QLabel("Target Nominal Stress (MPa):"), self.target_stress_inp)
        cond_layout.addRow(QLabel("Applied Dead Weight (kg):"), self.dead_weight_inp)

        layout.addWidget(cond_grp)

        layout.addStretch()
        self.setLayout(layout)
        self.update_inputs(self.sample_type_combo.currentText())

    def browse_output_folder(self):
        """Open folder browser dialog"""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder for Test Data",
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if folder:
            self.folder_path_display.setText(folder)
            self.folder_path_display.setStyleSheet("background-color: #d5f4e6; font-style: normal;")
            self.output_folder_changed.emit(folder)
            logger.info("Output folder selected: %s", folder)

    def _update_calc_area(self):
        stype = self.sample_type_combo.currentText()
        try:
            if stype == "Rectangular":
                w_str = self.width_inp.text().strip()
                t_str = self.thick_inp.text().strip()
                if not w_str or not t_str:
                    self.lbl_calc_area.setText("Calculated Area: —")
                    return
                area = float(w_str) * float(t_str)
            elif stype == "Circular":
                d_str = self.diam_inp.text().strip()
                if not d_str:
                    self.lbl_calc_area.setText("Calculated Area: —")
                    return
                area = (math.pi / 4.0) * (float(d_str) ** 2)
            elif stype == "Custom / Direct Area":
                a_str = self.custom_area_inp.text().strip()
                if not a_str:
                    self.lbl_calc_area.setText("Calculated Area: —")
                    return
                area = float(a_str)
            else:
                area = 0.0
            self.lbl_calc_area.setText(f"Calculated Area: {area:.2f} mm²")
        except (ValueError, TypeError):
            self.lbl_calc_area.setText("Calculated Area: —")

    def update_inputs(self, text):
        if text == "Rectangular":
            self.stack.setCurrentIndex(0)
        elif text == "Circular":
            self.stack.setCurrentIndex(1)
        elif text == "Custom / Direct Area":
            self.stack.setCurrentIndex(2)
        self._update_calc_area()
        self.push_params()

    def push_params(self):
        try:
            stype = self.sample_type_combo.currentText()
            w_str = self.width_inp.text().strip()
            t_str = self.thick_inp.text().strip()
            d_str = self.diam_inp.text().strip()
            ca_str = self.custom_area_inp.text().strip()

            w_val = float(w_str) if w_str else None
            t_val = float(t_str) if t_str else None
            d_val = float(d_str) if d_str else None
            ca_val = float(ca_str) if ca_str else None

            if stype == "Rectangular":
                gl_str = self.rect_gauge_inp.text().strip()
            elif stype == "Circular":
                gl_str = self.circ_gauge_inp.text().strip()
            else:
                gl_str = self.custom_gauge_inp.text().strip()
            gl_val = float(gl_str) if gl_str else None

            params = {
                "sample_id": self.sample_id_input.text(),
                "sample_type": stype,
                "width": w_val,
                "thickness": t_val,
                "diameter": d_val,
                "radius": (d_val / 2.0) if d_val is not None else None,
                "custom_area": ca_val,
                "gauge_length": gl_val,
                "extra_dims": "",
                "temperature": self.temp_inp.text().strip(),
                "target_stress": self.target_stress_inp.text().strip(),
                "dead_weight": self.dead_weight_inp.text().strip()
            }
            self._update_calc_area()
            self.sample_parameters_changed.emit(params)
        except ValueError:
            QMessageBox.warning(
                self, "Invalid Input", 
                "Please enter valid numbers for sample dimensions."
            )

    def set_output_folder_path(self, folder):
        if folder:
            self.folder_path_display.setText(folder)
            self.folder_path_display.setStyleSheet("background-color: #d5f4e6; font-style: normal;")
            self.output_folder_changed.emit(folder)

    def get_sample_data(self) -> dict:
        try:
            stype = self.sample_type_combo.currentText()
            w_str = self.width_inp.text().strip()
            t_str = self.thick_inp.text().strip()
            d_str = self.diam_inp.text().strip()
            ca_str = self.custom_area_inp.text().strip()
            if stype == "Rectangular":
                gl_str = self.rect_gauge_inp.text().strip()
            elif stype == "Circular":
                gl_str = self.circ_gauge_inp.text().strip()
            else:
                gl_str = self.custom_gauge_inp.text().strip()

            diam = float(d_str) if d_str else None
            return {
                "sample_id": self.sample_id_input.text(),
                "sample_type": stype,
                "width": float(w_str) if w_str else None,
                "thickness": float(t_str) if t_str else None,
                "diameter": diam,
                "radius": (diam / 2.0) if diam is not None else None,
                "custom_area": float(ca_str) if ca_str else None,
                "gauge_length": float(gl_str) if gl_str else None,
                "extra_dims": "",
                "temperature": self.temp_inp.text().strip(),
                "target_stress": self.target_stress_inp.text().strip(),
                "dead_weight": self.dead_weight_inp.text().strip(),
                "save_sensor": self.chk_save_sensor.isChecked(),
                "save_images": self.chk_save_images.isChecked()
            }
        except:
            return {}

    def restore_sample_data(self, data: dict):
        if not data:
            return
        if "sample_id" in data and data["sample_id"] is not None:
            self.sample_id_input.setText(str(data["sample_id"]))
        if "sample_type" in data and data["sample_type"] is not None:
            stype = str(data["sample_type"])
            idx = self.sample_type_combo.findText(stype)
            if idx >= 0:
                self.sample_type_combo.setCurrentIndex(idx)
            self.update_inputs(stype)
        if "width" in data and data["width"] is not None:
            self.width_inp.setText(str(data["width"]))
        if "thickness" in data and data["thickness"] is not None:
            self.thick_inp.setText(str(data["thickness"]))
        if "diameter" in data and data["diameter"] is not None:
            self.diam_inp.setText(str(data["diameter"]))
        elif "radius" in data and data["radius"] is not None:
            try:
                self.diam_inp.setText(str(float(data["radius"]) * 2.0))
            except (ValueError, TypeError):
                pass
        if "custom_area" in data and data["custom_area"] is not None:
            self.custom_area_inp.setText(str(data["custom_area"]))
        if "gauge_length" in data and data["gauge_length"] is not None:
            gl_str = str(data["gauge_length"])
            self.rect_gauge_inp.setText(gl_str)
            self.circ_gauge_inp.setText(gl_str)
            self.custom_gauge_inp.setText(gl_str)
        if "temperature" in data and data["temperature"] is not None:
            self.temp_inp.setText(str(data["temperature"]))
        if "target_stress" in data and data["target_stress"] is not None:
            self.target_stress_inp.setText(str(data["target_stress"]))
        if "dead_weight" in data and data["dead_weight"] is not None:
            self.dead_weight_inp.setText(str(data["dead_weight"]))
        if "save_sensor" in data:
            self.chk_save_sensor.setChecked(bool(data["save_sensor"]))
        if "save_images" in data:
            self.chk_save_images.setChecked(bool(data["save_images"]))
        self._update_calc_area()
        self.push_params()
