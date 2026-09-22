import time
from functools import partial
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
    QGroupBox, QFormLayout, QGridLayout, QMessageBox, QSizePolicy, QStackedWidget,
    QCheckBox, QFrame, QScrollArea, QButtonGroup
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QDoubleValidator, QIntValidator, QPixmap, QImage
import pyqtgraph as pg

from constants import PRIMARY_COLOR, ACCENT_COLOR
from gui_panels import setup_plot_style
from machine_config import MCfg
from logging_config import get_logger

logger = get_logger(__name__)

# =========================================================
# UTILITIES
# =========================================================
class StyledCheckBox(QCheckBox):
    """Custom checkbox with larger hit area and styling."""
    def __init__(self, text):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)

# =========================================================
# UNIAXIAL MOTOR CONTROL PANEL
# =========================================================
class UniaxialMotorControlPanel(QWidget):
    motor_started = Signal()
    motor_direction_changed = Signal(int)
    set_load_pos = Signal()
    set_unload_pos = Signal()
    clear_load_pos = Signal()
    clear_unload_pos = Signal()
    go_load_pos = Signal(int)
    go_unload_pos = Signal(int)
    cancel_travel = Signal()

    def __init__(self, serial_handler):
        super().__init__()
        self.serial = serial_handler
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        # --- Manual Actuation ---
        grp_cont = QGroupBox("Manual Actuation - Crosshead Jog")
        cont_lay = QVBoxLayout(grp_cont)
        cont_lay.setContentsMargins(8, 12, 8, 8)
        cont_lay.setSpacing(10)

        row_vel = QHBoxLayout()
        lbl_vel = QLabel("Speed (mm/sec):")
        self.vel = QLineEdit("0")
        self.vel.setPlaceholderText("Speed in mm/sec")
        self.vel.setValidator(QDoubleValidator())

        row_vel.addWidget(lbl_vel)
        row_vel.addWidget(self.vel)
        cont_lay.addLayout(row_vel)

        row_btns = QHBoxLayout()
        self.btn_start = QPushButton("Start Motor")
        self.btn_stop = QPushButton("Stop Motor")
        self.btn_stop.setStyleSheet("background-color: #c0392b;")

        row_btns.addWidget(self.btn_start)
        row_btns.addWidget(self.btn_stop)
        cont_lay.addLayout(row_btns)

        self.lbl_status = QLabel("Status: IDLE")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(
            "background-color: #bdc3c7; color: #2c3e50; padding: 8px; border-radius: 4px; font-weight: bold;"
        )
        cont_lay.addWidget(self.lbl_status)
        layout.addWidget(grp_cont)

        # --- Automatic Actuation ---
        grp_auto = QGroupBox("Automatic Actuation - Targeted Positioning")
        auto_lay = QVBoxLayout(grp_auto)
        auto_lay.setContentsMargins(8, 12, 8, 8)
        auto_lay.setSpacing(8)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(8)

        # 1) Load Position
        grp_load = QGroupBox("Load Position")
        load_lay = QVBoxLayout(grp_load)
        load_lay.setContentsMargins(8, 8, 8, 8)
        load_lay.setSpacing(6)

        self.lbl_load_pos = QLabel("Not Set")
        self.lbl_load_pos.setStyleSheet("font-weight: bold; color: #2c3e50;")
        load_lay.addWidget(self.lbl_load_pos)

        self.btn_set_load = QPushButton("Set")
        self.btn_clear_load = QPushButton("Clear")
        self.btn_clear_load.setStyleSheet("background-color: #7f8c8d;")
        load_lay.addWidget(self.btn_set_load)
        load_lay.addWidget(self.btn_clear_load)
        presets_row.addWidget(grp_load, 3)

        # 2) Unload Position
        grp_unload = QGroupBox("Unload Position")
        unload_lay = QVBoxLayout(grp_unload)
        unload_lay.setContentsMargins(8, 8, 8, 8)
        unload_lay.setSpacing(6)

        self.lbl_unload_pos = QLabel("Not Set")
        self.lbl_unload_pos.setStyleSheet("font-weight: bold; color: #2c3e50;")
        unload_lay.addWidget(self.lbl_unload_pos)

        self.btn_set_unload = QPushButton("Set")
        self.btn_clear_unload = QPushButton("Clear")
        self.btn_clear_unload.setStyleSheet("background-color: #7f8c8d;")
        unload_lay.addWidget(self.btn_set_unload)
        unload_lay.addWidget(self.btn_clear_unload)
        presets_row.addWidget(grp_unload, 3)

        # 3) Travel
        grp_travel = QGroupBox("Auto Travel")
        travel_lay = QVBoxLayout(grp_travel)
        travel_lay.setContentsMargins(8, 8, 8, 8)
        travel_lay.setSpacing(6)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed (mm/s):"))
        self.travel_speed = QLineEdit("2.0")
        self.travel_speed.setPlaceholderText("mm/s")
        self.travel_speed.setValidator(QDoubleValidator())
        speed_row.addWidget(self.travel_speed)
        travel_lay.addLayout(speed_row)

        travel_btns = QHBoxLayout()
        self.btn_go_load = QPushButton("Go to Load")
        self.btn_go_unload = QPushButton("Go to Unload")
        travel_btns.addWidget(self.btn_go_load)
        travel_btns.addWidget(self.btn_go_unload)
        travel_lay.addLayout(travel_btns)

        self.btn_cancel_travel = QPushButton("Cancel")
        self.btn_cancel_travel.setStyleSheet("background-color: #c0392b;")
        travel_lay.addWidget(self.btn_cancel_travel)

        self.lbl_live_encoder = QLabel("")
        self.lbl_live_encoder.setStyleSheet("font-weight: bold; color: #7f8c8d;")
        self.lbl_live_encoder.setVisible(False)
        travel_lay.addWidget(self.lbl_live_encoder)

        presets_row.addWidget(grp_travel, 5)

        auto_lay.addLayout(presets_row)
        layout.addWidget(grp_auto)
        
        layout.addStretch()
        self.setLayout(layout)

        self.btn_start.clicked.connect(self.send_start)
        self.btn_stop.clicked.connect(self.send_stop)
        self.btn_set_load.clicked.connect(self.set_load_pos.emit)
        self.btn_set_unload.clicked.connect(self.set_unload_pos.emit)
        self.btn_clear_load.clicked.connect(self.clear_load_pos.emit)
        self.btn_clear_unload.clicked.connect(self.clear_unload_pos.emit)
        self.btn_go_load.clicked.connect(self.go_load_clicked)
        self.btn_go_unload.clicked.connect(self.go_unload_clicked)
        self.btn_cancel_travel.clicked.connect(self.cancel_travel.emit)
        
        # Connect velocity change to VELSET command
        self.vel.editingFinished.connect(self.send_velset)

    @staticmethod
    def _mm_sec_to_rpm(mm_sec):
        """Convert mm/sec to RPM: rpm = mm_sec * 30."""
        return int(round(mm_sec * 30.0))

    def send_start(self):
        if not self.serial.is_connected():
            QMessageBox.critical(self, "Connection Error", "USB is disconnected!")
            return
        try:
            mm_sec = float(self.vel.text())
            max_speed = MCfg.max_crosshead_speed_mm_per_sec()
            if abs(mm_sec) > max_speed:
                QMessageBox.warning(self, "Speed Limit Exceeded", f"Max limit is ±{max_speed} mm/sec.")
                return
            rpm = self._mm_sec_to_rpm(mm_sec)
            direction = 1 if rpm > 0 else -1 if rpm < 0 else 0
            self.motor_direction_changed.emit(direction)
            self.serial.send_cmd(f"VEL:{rpm}")
            self.serial.send_cmd("START")
            self.lbl_status.setText("Status: RUNNING")
            self.lbl_status.setStyleSheet("background-color: #2ecc71; color: white; padding: 8px; border-radius: 4px; font-weight: bold;")
            logger.info("Motor STARTED at %.3f mm/sec (%d RPM)", mm_sec, rpm)
            self.motor_started.emit()
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Enter a valid speed value.")
            
    def send_velset(self):
        """Send VELSET command to adjust velocity on the fly if motor is running."""
        if not self.serial.is_connected():
            return
        if self.lbl_status.text() == "Status: IDLE":
            return # Only update on the fly if running
            
        try:
            mm_sec = float(self.vel.text())
            max_speed = MCfg.max_crosshead_speed_mm_per_sec()
            if abs(mm_sec) > max_speed:
                QMessageBox.warning(self, "Speed Limit Exceeded", f"Max limit is ±{max_speed} mm/sec.")
                return
            rpm = self._mm_sec_to_rpm(mm_sec)
            direction = 1 if rpm > 0 else -1 if rpm < 0 else 0
            self.motor_direction_changed.emit(direction)
            self.serial.send_cmd(f"VELSET:{rpm}")
            logger.info("Motor VELSET updated to %.3f mm/sec (%d RPM)", mm_sec, rpm)
        except ValueError:
            pass

    def send_stop(self):
        if not self.serial.is_connected():
            QMessageBox.critical(self, "Connection Error", "USB is disconnected!")
            return
        self.motor_direction_changed.emit(0)
        self.serial.send_cmd("STOP")
        self.lbl_status.setText("Status: IDLE")
        self.lbl_status.setStyleSheet("background-color: #bdc3c7; color: #2c3e50; padding: 8px; border-radius: 4px; font-weight: bold;")
        logger.info("Motor STOPPED")

    def _get_travel_rpm(self):
        if not self.serial.is_connected():
            QMessageBox.critical(self, "Connection Error", "USB is disconnected!")
            return None
        try:
            mm_sec = float(self.travel_speed.text())
            max_speed = MCfg.max_crosshead_speed_mm_per_sec()
            if abs(mm_sec) > max_speed:
                QMessageBox.warning(self, "Speed Limit Exceeded", f"Max limit is ±{max_speed} mm/sec.")
                return None
            rpm = self._mm_sec_to_rpm(mm_sec)
            if rpm == 0:
                QMessageBox.warning(self, "Input Error", "Speed too low.")
                return None
            return rpm
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Enter a valid speed value.")
            return None

    def go_load_clicked(self):
        rpm = self._get_travel_rpm()
        if rpm is not None:
            self.go_load_pos.emit(rpm)

    def go_unload_clicked(self):
        rpm = self._get_travel_rpm()
        if rpm is not None:
            self.go_unload_pos.emit(rpm)

    def update_load_label(self, pos):
        if pos is None:
            self.lbl_load_pos.setText("Not Set")
        else:
            mm = (pos / 10000.0) * 2.0
            self.lbl_load_pos.setText(f"{mm:.4f} mm ({pos:,} pulses)")

    def update_unload_label(self, pos):
        if pos is None:
            self.lbl_unload_pos.setText("Not Set")
        else:
            mm = (pos / 10000.0) * 2.0
            self.lbl_unload_pos.setText(f"{mm:.4f} mm ({pos:,} pulses)")


# =========================================================
# SENSOR DISPLAY PANEL
# =========================================================
class SensorDisplayPanel(QWidget):
    request_start_rec = Signal()
    request_stop_rec = Signal()
    set_zero_load = Signal()
    set_zero_disp = Signal()
    sampling_rate_changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.is_recording = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        top = QVBoxLayout()
        title = QLabel("Sensor Readings")
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {PRIMARY_COLOR};")
        title.setAlignment(Qt.AlignCenter)

        top.addWidget(title)
        layout.addLayout(top)

        # Main sensor view
        uni_lay = QVBoxLayout()
        uni_grp = QGroupBox("Sensor Data")
        uni_grid = QFormLayout(uni_grp)
        uni_grid.setSpacing(12)

        self.lbl_load = QLabel("0 N")
        self.lbl_load.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {PRIMARY_COLOR};")
        self.lbl_load_zero = QLabel("(Zero: 0)")

        btn_tare_load = QPushButton("Tare")
        btn_tare_load.setMinimumWidth(80)
        btn_tare_load.clicked.connect(self.set_zero_load.emit)

        l_row = QHBoxLayout()
        l_row.addWidget(self.lbl_load)
        l_row.addWidget(self.lbl_load_zero)
        l_row.addStretch()
        l_row.addWidget(btn_tare_load)
        uni_grid.addRow(QLabel("Load:"), l_row)

        self.lbl_disp = QLabel("0.00 mm")
        self.lbl_disp.setStyleSheet("font-size: 24px; font-weight: bold; color: #27ae60;")
        self.lbl_disp_zero = QLabel("(Zero: 0.00)")

        btn_tare_disp = QPushButton("Tare")
        btn_tare_disp.setMinimumWidth(80)
        btn_tare_disp.clicked.connect(self.set_zero_disp.emit)

        d_row = QHBoxLayout()
        d_row.addWidget(self.lbl_disp)
        d_row.addWidget(self.lbl_disp_zero)
        d_row.addStretch()
        d_row.addWidget(btn_tare_disp)
        uni_grid.addRow(QLabel("Disp:"), d_row)

        uni_lay.addWidget(uni_grp)

        # Sampling Rate row
        samp_grp = QGroupBox("Data Sampling Rate")
        samp_lay = QHBoxLayout(samp_grp)
        samp_lay.setContentsMargins(12, 12, 12, 12)
        samp_lay.setSpacing(10)

        lbl_rate = QLabel("Samples/hour:")
        lbl_rate.setStyleSheet("color: #2c3e50; font-weight: bold;")
        self.inp_rate = QLineEdit("20")
        self.inp_rate.setValidator(QIntValidator(1, 72000))  # Max 20 Hz
        self.inp_rate.setMinimumWidth(80)
        self.inp_rate.setMaximumWidth(100)

        # Max Displacement inline to the right
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        lbl_max = QLabel("Max Disp:")
        lbl_max.setStyleSheet("color: #2c3e50; font-weight: bold;")
        self.lbl_max_disp_val = QLabel("0.0000 mm")
        self.lbl_max_disp_val.setStyleSheet("color: #e74c3c; font-weight: bold;")

        samp_lay.addWidget(lbl_rate)
        samp_lay.addWidget(self.inp_rate)
        samp_lay.addStretch()
        samp_lay.addWidget(sep)
        samp_lay.addWidget(lbl_max)
        samp_lay.addWidget(self.lbl_max_disp_val)

        uni_lay.addWidget(samp_grp)

        # Data Recording
        rec_grp = QGroupBox("Data Recording")
        rec_lay = QHBoxLayout(rec_grp)
        rec_lay.setContentsMargins(12, 12, 12, 12)

        self.btn_rec = QPushButton("Start Rec")
        self.btn_rec.setMinimumWidth(80)
        self.btn_stop_rec = QPushButton("Stop Rec")
        self.btn_stop_rec.setMinimumWidth(80)
        self.btn_stop_rec.setEnabled(False)
        self.btn_stop_rec.setStyleSheet("background-color: #c0392b;")


        self.lbl_rec_status = QLabel("IDLE")
        self.lbl_rec_status.setStyleSheet("font-weight: bold; margin-left: 5px;")

        rec_lay.addWidget(self.btn_rec)
        rec_lay.addWidget(self.btn_stop_rec)
        rec_lay.addStretch()
        rec_lay.addWidget(self.lbl_rec_status)

        uni_lay.addWidget(rec_grp)
        uni_lay.addStretch()
        layout.addLayout(uni_lay)
        self.setLayout(layout)

        self.btn_rec.clicked.connect(self._on_start_rec_clicked)
        self.btn_stop_rec.clicked.connect(self.request_stop_rec.emit)
        self.inp_rate.editingFinished.connect(self._on_rate_changed)
        self.inp_rate.textChanged.connect(self._on_text_changed)

    def get_sampling_rate(self) -> int:
        try:
            val = int(self.inp_rate.text())
            if val < 1: val = 1
            if val > 72000: val = 72000
            return val
        except ValueError:
            return 20

    def _on_start_rec_clicked(self):
        self._on_rate_changed()
        self.request_start_rec.emit()

    def _on_text_changed(self, text):
        try:
            val = int(text)
            if 1 <= val <= 72000:
                self.sampling_rate_changed.emit(val)
        except ValueError:
            pass

    def _on_rate_changed(self):
        val = self.get_sampling_rate()
        self.inp_rate.setText(str(val))
        self.sampling_rate_changed.emit(val)

    def set_recording_state(self, is_recording):
        self.is_recording = is_recording
        self.btn_rec.setEnabled(not is_recording)
        self.btn_stop_rec.setEnabled(is_recording)

        status_text = "REC" if is_recording else "IDLE"
        status_style = "color: #27ae60; font-weight: bold;" if is_recording else "color: #2c2f33; font-weight: bold;"
        self.lbl_rec_status.setText(status_text)
        self.lbl_rec_status.setStyleSheet(status_style)


    def update_sensors(self, load_n, disp_mm, max_disp=0.0):
        self.lbl_load.setText(f"{load_n:.0f} N")
        self.lbl_disp.setText(f"{disp_mm:.3f} mm")
        if hasattr(self, 'lbl_max_disp_val'):
            self.lbl_max_disp_val.setText(f"{max_disp:.4f} mm")

    def update_offsets(self, load_off, disp_off):
        self.lbl_load_zero.setText(f"(Zero: {load_off:.0f})")
        self.lbl_disp_zero.setText(f"(Zero: {disp_off:.3f})")


# =========================================================
# SAMPLE INSPECTION PANEL
# =========================================================
class SampleInspectionPanel(QWidget):
    camera_error = Signal(str)
    capture_image_requested = Signal()
    def __init__(self):
        super().__init__()
        from gui import CameraWorker
        from PySide6.QtCore import QThread
        self._cam_thread = QThread(self)
        self.worker = CameraWorker()
        self.worker.moveToThread(self._cam_thread)
        self.worker.image_received.connect(self.update_image)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.stats_updated.connect(self.update_stats)
        self.worker.limits_ready.connect(self.on_limits_received)
        self._shutting_down = False
        self.init_ui()
        self.limits = {}
        self.last_image_update = 0.0
        self._cam_thread.start()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(20)

        image_container = QWidget()
        image_container.setStyleSheet("background-color: transparent; border: none;")
        image_layout = QVBoxLayout(image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)
        
        self.image_label = QLabel("Camera Feed Offline")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("color: #bdc3c7; font-size: 22px; font-weight: bold; border: 2px dashed #bdc3c7;")
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        image_layout.addWidget(self.image_label)
        layout.addWidget(image_container, stretch=3)

        controls_container = QWidget()
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(18)

        title = QLabel("Image Setup")
        title.setStyleSheet(f"font-size: 28px; font-weight: bold; color: {PRIMARY_COLOR}; margin-bottom: 10px;")
        controls_layout.addWidget(title)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Start Stream")
        self.btn_start.clicked.connect(self.start_camera)
        self.btn_stop = QPushButton("Stop Stream")
        self.btn_stop.setStyleSheet("background-color: #c0392b;")
        self.btn_stop.clicked.connect(self.stop_camera)
        self.btn_stop.setEnabled(False)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        controls_layout.addLayout(btn_layout)

        capture_group = QGroupBox("Image Capture")
        capture_lay = QVBoxLayout(capture_group)
        self.btn_capture = QPushButton("Capture Image")
        self.btn_capture.clicked.connect(self.capture_image_requested.emit)
        capture_lay.addWidget(self.btn_capture)
        self.lbl_capture_status = QLabel("Ready")
        self.lbl_capture_status.setAlignment(Qt.AlignCenter)
        capture_lay.addWidget(self.lbl_capture_status)
        controls_layout.addWidget(capture_group)
        self._capture_count = 0

        self.controls_group = QGroupBox("Camera Parameters")
        self.controls_group.setEnabled(False)
        form_layout = QVBoxLayout(self.controls_group)
        self.sliders, self.labels, self.autos = {}, {}, {}
        from PySide6.QtWidgets import QSlider
        
        def add_control(name, has_auto=True):
            row = QWidget()
            h_layout = QVBoxLayout(row)
            h_layout.setContentsMargins(0,0,0,0)
            header = QHBoxLayout()
            header.addWidget(QLabel(name))
            header.addStretch()
            if has_auto:
                chk = QCheckBox("Auto")
                chk.toggled.connect(lambda v: self.on_auto_changed(name, v))
                self.autos[name] = chk
                header.addWidget(chk)
            lbl_val = QLabel("0.0")
            header.addWidget(lbl_val)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 1000)
            slider.valueChanged.connect(lambda v: self.on_slider_changed(name, v))
            h_layout.addLayout(header)
            h_layout.addWidget(slider)
            form_layout.addWidget(row)
            self.sliders[name] = slider
            self.labels[name] = lbl_val

        add_control("Gain")
        add_control("Exposure")
        add_control("BlackLevel", False)
        add_control("Gamma", False)
        controls_layout.addWidget(self.controls_group)

        # Remove light group completely
        controls_layout.addStretch()

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        controls_layout.addWidget(self.status_lbl)
        layout.addWidget(controls_container, stretch=1)

    def start_camera(self):
        self._shutting_down = False
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.image_label.setText("Initializing...")
        self.worker.request_start()

    def stop_camera(self):
        if self._shutting_down: return
        self._shutting_down = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.worker.request_stop()
        self._stop_check_timer = QTimer(self)
        self._stop_check_timer.setInterval(100)
        self._stop_check_timer.timeout.connect(self._check_stream_stopped)
        self._stop_check_count = 0
        self._stop_check_timer.start()

    def _check_stream_stopped(self):
        self._stop_check_count += 1
        if not self.worker.running or self._stop_check_count > 30:
            self._stop_check_timer.stop()
            self._on_stream_stopped()

    def _on_stream_stopped(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.controls_group.setEnabled(False)
        self.image_label.setText("Stream Stopped")
        self.status_lbl.setText("Stopped")
        self._shutting_down = False

    def shutdown_camera(self):
        self.worker.shutdown()
        self._cam_thread.quit()
        self._cam_thread.wait(5000)

    def is_live(self):
        return self.worker.running

    @Slot(dict)
    def on_limits_received(self, limits):
        self.limits = limits
        if not limits: return
        self.controls_group.setEnabled(True)
        self.status_lbl.setText("Camera Active")

    def on_slider_changed(self, name, val):
        if name not in self.limits: return
        min_val, max_val = self.limits[name]
        mapped_val = min_val + (val / 1000.0) * (max_val - min_val)
        self.labels[name].setText(f"{mapped_val:.2f}")
        is_auto = self.autos[name].isChecked() if name in self.autos else False
        if name == "Gain": self.worker.set_gain(mapped_val, is_auto)
        elif name == "Exposure": self.worker.set_exposure(mapped_val, is_auto)
        elif name == "BlackLevel": self.worker.set_black_level(mapped_val, False)
        elif name == "Gamma": self.worker.set_gamma(mapped_val)

    def on_auto_changed(self, name, is_checked):
        self.sliders[name].setEnabled(not is_checked)
        self.on_slider_changed(name, self.sliders[name].value())

    @Slot(QImage)
    def update_image(self, q_img):
        current_time = time.time()
        if current_time - self.last_image_update < 0.033: return
        self.last_image_update = current_time
        pixmap = QPixmap.fromImage(q_img)
        if not pixmap.isNull():
            scaled = pixmap.scaled(self.image_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
            self.image_label.setPixmap(scaled)

    @Slot(float)
    def update_stats(self, fps):
        self.status_lbl.setText(f"LIVE | FPS: {fps:.1f}")

    @Slot(str)
    def show_capture_success(self, path):
        self._capture_count += 1
        self.lbl_capture_status.setText(f"Captured ({self._capture_count})")
        QTimer.singleShot(5000, lambda: self.lbl_capture_status.setText("Ready"))

    @Slot(str)
    def show_capture_error(self, msg):
        self.lbl_capture_status.setText(f"Error: {msg}")
        QTimer.singleShot(5000, lambda: self.lbl_capture_status.setText("Ready"))

    @Slot(str)
    def handle_error(self, msg):
        if self._shutting_down: return
        self.stop_camera()
        self.camera_error.emit(msg)
        self.status_lbl.setText("Error")


# =========================================================
# TEST DASHBOARD PANEL
# =========================================================
class TestDashboardPanel(QWidget):
    stop_all_requested = Signal()
    stop_rec_requested = Signal()
    capture_requested = Signal()
    bl_graph_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.last_image_update = 0.0
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        
        self.image_label = QLabel("Camera Feed")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("color: #bdc3c7; font-size: 18px; font-weight: bold; border: 2px dashed #bdc3c7;")
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        # Top-Left: Camera + Control Panel
        cam_container = QWidget()
        cam_lay = QHBoxLayout(cam_container)
        cam_lay.setContentsMargins(0,0,0,0)
        
        self.side_panel = QWidget()
        self.side_panel.setFixedWidth(140)
        self.side_panel.setStyleSheet(
            "QWidget#side_panel { background-color: #f4f6f7; border: 1px solid #dce1e5; border-radius: 6px; }")
        self.side_panel.setObjectName("side_panel")
        sp_lay = QVBoxLayout(self.side_panel)
        sp_lay.setContentsMargins(8, 10, 8, 10)
        sp_lay.setSpacing(8)

        self.btn_stop_all = QPushButton("Stop Motor")
        self.btn_stop_all.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; font-weight: bold;"
            " padding: 10px 4px; font-size: 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e74c3c; }")
        self.btn_stop_all.setMinimumHeight(40)
        self.btn_stop_all.setCursor(Qt.PointingHandCursor)
        self.btn_stop_all.clicked.connect(self.stop_all_requested.emit)
        sp_lay.addWidget(self.btn_stop_all)

        self.btn_stop_rec_dash = QPushButton("Stop\nRecording")
        self.btn_stop_rec_dash.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; font-weight: bold;"
            " padding: 10px 4px; font-size: 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e74c3c; }")
        self.btn_stop_rec_dash.setMinimumHeight(40)
        self.btn_stop_rec_dash.setCursor(Qt.PointingHandCursor)
        self.btn_stop_rec_dash.clicked.connect(self.stop_rec_requested.emit)
        sp_lay.addWidget(self.btn_stop_rec_dash)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet("background-color: #dce1e5; max-height: 1px;")
        sp_lay.addWidget(sep1)

        lbl_load_title = QLabel("Load")
        lbl_load_title.setStyleSheet(f"color: {PRIMARY_COLOR}; font-size: 11px; font-weight: bold; background: transparent;")
        lbl_load_title.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(lbl_load_title)
        self.side_load_lbl = QLabel("-- N")
        self.side_load_lbl.setStyleSheet("color: #2c3e50; font-size: 16px; font-weight: bold; background: transparent;")
        self.side_load_lbl.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(self.side_load_lbl)

        lbl_disp_title = QLabel("Displacement")
        lbl_disp_title.setStyleSheet(f"color: {PRIMARY_COLOR}; font-size: 11px; font-weight: bold; background: transparent;")
        lbl_disp_title.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(lbl_disp_title)
        self.side_disp_lbl = QLabel("-- mm")
        self.side_disp_lbl.setStyleSheet("color: #2c3e50; font-size: 16px; font-weight: bold; background: transparent;")
        self.side_disp_lbl.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(self.side_disp_lbl)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("background-color: #dce1e5; max-height: 1px;")
        sp_lay.addWidget(sep2)

        lbl_rec_title = QLabel("Rec Duration")
        lbl_rec_title.setStyleSheet(f"color: {PRIMARY_COLOR}; font-size: 11px; font-weight: bold; background: transparent;")
        lbl_rec_title.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(lbl_rec_title)
        self.side_rec_timer_lbl = QLabel("--:--")
        self.side_rec_timer_lbl.setStyleSheet("color: #95a5a6; font-size: 18px; font-weight: bold; background: transparent;")
        self.side_rec_timer_lbl.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(self.side_rec_timer_lbl)

        self.side_rec_status_lbl = QLabel("IDLE")
        self.side_rec_status_lbl.setStyleSheet("color: #95a5a6; font-size: 12px; font-weight: bold; background: transparent;")
        self.side_rec_status_lbl.setAlignment(Qt.AlignCenter)
        sp_lay.addWidget(self.side_rec_status_lbl)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("background-color: #dce1e5; max-height: 1px;")
        sp_lay.addWidget(sep3)

        self.btn_capture_dash = QPushButton("Capture\nImage")
        self.btn_capture_dash.setMinimumHeight(40)
        self.btn_capture_dash.setCursor(Qt.PointingHandCursor)
        self.btn_capture_dash.clicked.connect(self.capture_requested.emit)
        sp_lay.addWidget(self.btn_capture_dash)

        self.side_capture_status_lbl = QLabel("Ready")
        self.side_capture_status_lbl.setStyleSheet("color: #95a5a6; font-size: 11px; font-style: italic; background: transparent;")
        self.side_capture_status_lbl.setAlignment(Qt.AlignCenter)
        self.side_capture_status_lbl.setWordWrap(True)
        sp_lay.addWidget(self.side_capture_status_lbl)

        sp_lay.addStretch()
        cam_lay.addWidget(self.side_panel)
        
        cam_area = QVBoxLayout()
        cam_area.addWidget(self.image_label, stretch=1)
        cam_lay.addLayout(cam_area, stretch=1)
        grid.addWidget(cam_container, 0, 0)

        # Plots
        self.plot_load_time = pg.PlotWidget()
        setup_plot_style(self.plot_load_time, "Load vs Time", "Time (s)", "Load (N)")
        grid.addWidget(self.plot_load_time, 0, 1)

        self.plot_load_disp = pg.PlotWidget()
        setup_plot_style(self.plot_load_disp, "", "Displacement (mm)", "Load (N)")
        self.plot_load_disp.plotItem.setContentsMargins(0, 42, 0, 0)
        
        # Segmented toggle for graph mode (matching Test Analysis style)
        self.bl_graph_toggle = QFrame(self.plot_load_disp)
        self.bl_graph_toggle.setStyleSheet(
            "QFrame { background-color: #e4e7ea; border-radius: 8px; padding: 3px; border: 1px solid #bdc3c7; }"
        )
        toggle_layout = QHBoxLayout(self.bl_graph_toggle)
        toggle_layout.setContentsMargins(3, 3, 3, 3)
        toggle_layout.setSpacing(6)

        self.btn_graph_ld = QPushButton("Load vs Displacement")
        self.btn_graph_ld.setCheckable(True)
        self.btn_graph_ld.setChecked(True)
        self.btn_graph_ld.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.btn_graph_ss = QPushButton("Stress vs Strain")
        self.btn_graph_ss.setCheckable(True)
        self.btn_graph_ss.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.bl_mode_group = QButtonGroup(self)
        self.bl_mode_group.setExclusive(True)
        self.bl_mode_group.addButton(self.btn_graph_ld, 0)
        self.bl_mode_group.addButton(self.btn_graph_ss, 1)

        toggle_layout.addWidget(self.btn_graph_ld)
        toggle_layout.addWidget(self.btn_graph_ss)

        self.btn_graph_ld.clicked.connect(lambda: self._on_bl_graph_switch("Load vs Displacement"))
        self.btn_graph_ss.clicked.connect(lambda: self._on_bl_graph_switch("Stress vs Strain"))

        self._update_bl_toggle_styles()

        # Backward compatibility alias
        self.bl_graph_combo = self.bl_graph_toggle

        def _reposition_bl_toggle(event=None):
            if hasattr(self, 'bl_graph_toggle'):
                cw = self.bl_graph_toggle.sizeHint().width()
                x = (self.plot_load_disp.width() - cw) // 2
                self.bl_graph_toggle.move(max(0, x), 4)
        self.plot_load_disp._bl_reposition = _reposition_bl_toggle

        orig_resize = self.plot_load_disp.resizeEvent
        def _bl_resize(event):
            if orig_resize:
                orig_resize(event)
            _reposition_bl_toggle()
        self.plot_load_disp.resizeEvent = _bl_resize
        _reposition_bl_toggle()
        
        grid.addWidget(self.plot_load_disp, 1, 0)

        self.plot_disp_time = pg.PlotWidget()
        setup_plot_style(self.plot_disp_time, "Displacement vs Time", "Time (s)", "Displacement (mm)")
        grid.addWidget(self.plot_disp_time, 1, 1)

        layout.addLayout(grid)
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(1000)
        self._rec_timer.timeout.connect(self._tick_rec_timer)
        self._rec_elapsed = 0
        self._dash_capture_count = 0

    def update_side_readouts(self, load, disp):
        self.side_load_lbl.setText(f"{load:.0f} N")
        self.side_disp_lbl.setText(f"{disp:.3f} mm")

    def start_rec_timer(self, initial_seconds: int = 0):
        self._rec_elapsed = int(initial_seconds)
        mins = self._rec_elapsed // 60
        secs = self._rec_elapsed % 60
        self.side_rec_timer_lbl.setText(f"{mins:02d}:{secs:02d}")
        self._rec_timer.start()

    def stop_rec_timer(self):
        self._rec_timer.stop()

    def _tick_rec_timer(self):
        self._rec_elapsed += 1
        mins = self._rec_elapsed // 60
        secs = self._rec_elapsed % 60
        self.side_rec_timer_lbl.setText(f"{mins:02d}:{secs:02d}")

    @Slot(QImage)
    def update_image(self, q_img):
        current_time = time.time()
        if current_time - self.last_image_update < 0.033: return
        self.last_image_update = current_time
        pixmap = QPixmap.fromImage(q_img)
        if not pixmap.isNull():
            scaled = pixmap.scaled(self.image_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
            self.image_label.setPixmap(scaled)

    @Slot(str)
    def show_capture_success(self, path):
        self._dash_capture_count += 1

    @Slot(str)
    def show_capture_error(self, msg):
        pass

    def _update_bl_toggle_styles(self):
        active_style = (
            f"QPushButton {{ background-color: {PRIMARY_COLOR}; color: white; font-weight: bold; "
            f"border-radius: 5px; padding: 6px 18px; font-size: 14px; border: none; min-height: 28px; }}"
            f"QPushButton:disabled {{ background-color: #7f8c8d; color: #ecf0f1; }}"
        )
        inactive_style = (
            "QPushButton { background-color: transparent; color: #576574; font-weight: bold; "
            "border: none; padding: 6px 18px; font-size: 14px; border-radius: 5px; min-height: 28px; }"
            "QPushButton:hover { background-color: #dcdde1; color: #2c3e50; }"
            "QPushButton:disabled { color: #bdc3c7; }"
        )
        if hasattr(self, 'btn_graph_ld') and hasattr(self, 'btn_graph_ss'):
            if self.btn_graph_ld.isChecked():
                self.btn_graph_ld.setStyleSheet(active_style)
                self.btn_graph_ss.setStyleSheet(inactive_style)
            else:
                self.btn_graph_ss.setStyleSheet(active_style)
                self.btn_graph_ld.setStyleSheet(inactive_style)

    def _on_bl_graph_switch(self, text):
        if text == "Stress vs Strain":
            if hasattr(self, 'btn_graph_ss'):
                self.btn_graph_ss.setChecked(True)
            self.plot_load_disp.setLabel('left', 'Stress (N/mm²)')
            self.plot_load_disp.setLabel('bottom', 'Strain (mm/mm)')
        else:
            if hasattr(self, 'btn_graph_ld'):
                self.btn_graph_ld.setChecked(True)
            self.plot_load_disp.setLabel('left', 'Load (N)')
            self.plot_load_disp.setLabel('bottom', 'Displacement (mm)')
        self._update_bl_toggle_styles()
        self.bl_graph_changed.emit(text)

