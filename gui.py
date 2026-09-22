import time
import re
import csv
import os
import threading
import queue
import collections

# --- Camera Library ---
try:
    import PySpin
except ImportError:
    PySpin = None

# --- Computer Vision Library ---
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    HAS_CV2 = False

# --- PyQtGraph Imports ---
import pyqtgraph as pg

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QStackedWidget, QLabel, QMessageBox, QSizePolicy
)
from PySide6.QtCore import (Qt, QTimer, Signal, QObject, Slot, QThread,
                            QMutex, QMutexLocker)
from PySide6.QtGui import QImage, QPixmap

from serial_handler import SerialHandler
from data_worker import DataWorker
from gui_panels import ConnectionPanel, SamplePanel, setup_plot_style
from gui_test_views import (
    UniaxialMotorControlPanel, SensorDisplayPanel, SampleInspectionPanel,
    TestDashboardPanel
)
from gui_analysis import AnalysisReportingPanel
from controllers import (
    ErrorManager, PresetPositionController,
    RecordingController, GraphController,
    CaptureController
)

# --- Corporate Colors (centralized) ---
from constants import PRIMARY_COLOR, ACCENT_COLOR

# --- Logging ---
from logging_config import get_logger, resource_path
from test_recovery import TestSessionManager
from power_management import WindowsPowerManager
logger = get_logger(__name__)


# =========================================================
# IMAGE SAVE WORKER (TIFF with No Compression)
# =========================================================
class ImageSaveWorker(QObject):
    def __init__(self, save_queue):
        super().__init__()
        self.save_queue = save_queue
        self.running = True
        self.images_saved = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while self.running:
            try:
                item = self.save_queue.get(timeout=0.1)
                if item is None:
                    break
                img_data, path = item
                try:
                    if cv2 is not None:
                        cv2.imwrite(path, img_data, [cv2.IMWRITE_TIFF_COMPRESSION, 1])
                        self.images_saved += 1
                    else:
                        logger.error("ImageSaveWorker Error: cv2 not installed.")
                except Exception as e:
                    logger.error("ImageSaveWorker Error: %s", e)
                finally:
                    self.save_queue.task_done()
            except queue.Empty:
                continue

    def stop(self):
        self.running = False
        self.save_queue.put(None)
        self.thread.join(timeout=2.0)

    def stop_and_drain(self):
        try:
            self.save_queue.join()
        except Exception:
            pass
        self.running = False
        self.save_queue.put(None)
        self.thread.join(timeout=5.0)


# =========================================================
# CAMERA WORKER
# =========================================================
class CameraWorker(QObject):
    image_received = Signal(QImage)
    error_occurred = Signal(str)
    stats_updated = Signal(float)
    limits_ready = Signal(dict)
    capture_saved = Signal(str)

    _do_start_stream = Signal()
    _do_set_gain = Signal(float, bool)
    _do_set_exposure = Signal(float, bool)
    _do_set_black_level = Signal(float, bool)
    _do_set_gamma = Signal(float)
    _do_capture_frame = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self._shutting_down = False
        self.system = None
        self.cam = None
        self.recording = False
        self.record_dir = ""
        self.snapshot_pending_paths = []
        self.snapshot_lock = threading.Lock()

        self.frame_count = 0
        self.last_fps_time = time.time()
        
        self.save_queue = queue.Queue()
        self.image_saver = None

        self._do_start_stream.connect(self._start_stream_impl)
        self._do_set_gain.connect(self._set_gain_impl)
        self._do_set_exposure.connect(self._set_exposure_impl)
        self._do_set_black_level.connect(self._set_black_level_impl)
        self._do_set_gamma.connect(self._set_gamma_impl)
        self._do_capture_frame.connect(self._capture_frame_impl)

    def request_start(self):
        self._shutting_down = False
        self._do_start_stream.emit()

    def request_stop(self):
        self._shutting_down = True
        self.running = False

    def shutdown(self):
        self.request_stop()
        if self.image_saver:
            self.image_saver.stop_and_drain()

    def set_gain(self, val, auto):
        if not self.running or self._shutting_down: return
        self._do_set_gain.emit(val, auto)

    def set_exposure(self, val, auto):
        if not self.running or self._shutting_down: return
        self._do_set_exposure.emit(val, auto)

    def set_black_level(self, val, auto):
        if not self.running or self._shutting_down: return
        self._do_set_black_level.emit(val, auto)

    def set_gamma(self, val):
        if not self.running or self._shutting_down: return
        self._do_set_gamma.emit(val)

    def request_capture(self, target_path):
        if not self.running or self._shutting_down: return
        with self.snapshot_lock:
            self.snapshot_pending_paths.append(target_path)
            
    @Slot(str)
    def trigger_save_snapshot(self, img_path):
        self.request_capture(img_path)

    def start_recording(self, save_dir):
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
            except Exception as e:
                logger.error("Failed to create image recording directory: %s", e)
                return
        self.record_dir = save_dir
        self.recording = True
        logger.info("Camera image recording STARTED.")

    def stop_recording(self):
        self.recording = False
        self.record_dir = ""
        logger.info("Camera image recording STOPPED.")

    @Slot()
    def _start_stream_impl(self):
        if self.running or PySpin is None: return
        try:
            self.system = PySpin.System.GetInstance()
            cam_list = self.system.GetCameras()
            if cam_list.GetSize() == 0:
                cam_list.Clear()
                self.system.ReleaseInstance()
                self.error_occurred.emit("No FLIR cameras found.")
                return

            self.cam = cam_list.GetByIndex(0)
            self.cam.Init()
            
            node_map = self.cam.GetNodeMap()
            try:
                node_acq_mode = PySpin.CEnumerationPtr(node_map.GetNode('AcquisitionMode'))
                if PySpin.IsAvailable(node_acq_mode) and PySpin.IsWritable(node_acq_mode):
                    node_acq_mode_cont = node_acq_mode.GetEntryByName('Continuous')
                    if PySpin.IsAvailable(node_acq_mode_cont) and PySpin.IsReadable(node_acq_mode_cont):
                        node_acq_mode.SetIntValue(node_acq_mode_cont.GetValue())
            except PySpin.SpinnakerException as e:
                logger.warning("Could not set AcquisitionMode to Continuous: %s", e)
                
            self.cam.BeginAcquisition()
            
            if self.image_saver is None or not self.image_saver.running:
                self.save_queue = queue.Queue()
                self.image_saver = ImageSaveWorker(self.save_queue)
                
            self.running = True
            self.frame_count = 0
            self.last_fps_time = time.time()
            
            limits = {}
            for name in ["Gain", "ExposureTime", "BlackLevel", "Gamma"]:
                try:
                    node = PySpin.CFloatPtr(node_map.GetNode(name))
                    if PySpin.IsAvailable(node):
                        limits[name if name != "ExposureTime" else "Exposure"] = (node.GetMin(), node.GetMax())
                except PySpin.SpinnakerException:
                    pass
            self.limits_ready.emit(limits)
            
            QTimer.singleShot(0, self._acquire_frame)
            logger.info("Camera stream started.")
        except Exception as e:
            self.error_occurred.emit(f"Camera Initialization Error: {e}")
            self._cleanup_camera()

    @Slot(float, bool)
    def _set_gain_impl(self, val, auto):
        self._set_node("GainAuto", "Gain", val, auto)

    @Slot(float, bool)
    def _set_exposure_impl(self, val, auto):
        self._set_node("ExposureAuto", "ExposureTime", val, auto)

    @Slot(float, bool)
    def _set_black_level_impl(self, val, auto):
        self._set_node("BlackLevelAuto", "BlackLevel", val, auto)

    @Slot(float)
    def _set_gamma_impl(self, val):
        self._set_node(None, "Gamma", val, False)

    def _set_node(self, auto_name, val_name, val, auto):
        if not self.cam: return
        try:
            node_map = self.cam.GetNodeMap()
            if auto_name:
                node_auto = PySpin.CEnumerationPtr(node_map.GetNode(auto_name))
                if PySpin.IsAvailable(node_auto) and PySpin.IsWritable(node_auto):
                    entry = node_auto.GetEntryByName('Continuous' if auto else 'Off')
                    if entry: node_auto.SetIntValue(entry.GetValue())
            if not auto:
                node_val = PySpin.CFloatPtr(node_map.GetNode(val_name))
                if PySpin.IsAvailable(node_val) and PySpin.IsWritable(node_val):
                    node_val.SetValue(val)
        except PySpin.SpinnakerException as e:
            logger.error("Camera node set error [%s]: %s", val_name, e)

    @Slot(str)
    def _capture_frame_impl(self, target_path):
        pass

    def _acquire_frame(self):
        if not self.running:
            self._cleanup_camera()
            return
            
        try:
            image_result = self.cam.GetNextImage(1000)
            if image_result.IsIncomplete():
                image_result.Release()
                QTimer.singleShot(0, self._acquire_frame)
                return
                
            np_img = image_result.GetNDArray()
            
            with self.snapshot_lock:
                paths_to_save = self.snapshot_pending_paths.copy()
                self.snapshot_pending_paths.clear()
                
            for path in paths_to_save:
                if self.image_saver and self.image_saver.running:
                    self.save_queue.put((np_img.copy(), path))
                    self.capture_saved.emit(path)
                    
            height, width = np_img.shape
            bytes_per_line = width
            q_img = QImage(np_img.data, width, height, bytes_per_line, QImage.Format_Grayscale8)
            self.image_received.emit(q_img.copy())
            
            image_result.Release()
            
            self.frame_count += 1
            current_time = time.time()
            elapsed = current_time - self.last_fps_time
            if elapsed >= 1.0:
                fps = self.frame_count / elapsed
                self.stats_updated.emit(fps)
                self.frame_count = 0
                self.last_fps_time = current_time
                
            QTimer.singleShot(0, self._acquire_frame)
            
        except PySpin.SpinnakerException as e:
            if not self._shutting_down:
                self.error_occurred.emit(f"Acquisition Error: {e}")
            self.running = False
            self._cleanup_camera()

    def _cleanup_camera(self):
        try:
            if self.cam:
                try:
                    self.cam.EndAcquisition()
                except Exception as e:
                    logger.warning("Error ending acquisition during cleanup: %s", e)
                try:
                    self.cam.DeInit()
                except Exception as e:
                    logger.warning("Error de-initializing camera during cleanup: %s", e)
                del self.cam
                self.cam = None
            if self.system:
                self.system.ReleaseInstance()
                del self.system
                self.system = None
            logger.info("Camera resources released successfully.")
        except Exception as e:
            logger.error("Error during camera cleanup: %s", e)


# =========================================================
# MAIN GUI APPLICATION
# =========================================================
class InsituMicronGUI(QMainWindow):
    show_error_signal = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Insitumicron Creep GUI")
        self.setMinimumSize(1200, 800)

        # Hardware Handlers
        self.serial_handler = SerialHandler()
        self.data_thread = QThread(self)
        self.data_worker = DataWorker(self.serial_handler)
        self.data_worker.moveToThread(self.data_thread)

        self.serial_handler.data_received.connect(self.data_worker.process_line)
        self.data_worker.ui_update.connect(self.update_labels)
        self.data_worker.plot_update.connect(self.update_graphs)
        self.data_worker.overload_detected.connect(self.handle_overload_stop)
        self.data_worker.recording_error.connect(self.handle_recording_error)

        self.serial_handler.connection_status_changed.connect(self.handle_connection_status)
        self.serial_handler.controller_disconnected.connect(self.handle_controller_disconnected)
        self.serial_handler.controller_reconnected.connect(self.handle_controller_reconnected)
        
        QTimer.singleShot(500, self.startup_check)
        self.show_error_signal.connect(self.show_critical_error_box)

        self._is_connected = False
        self._output_folder = ""
        self._save_sensor_data = True
        self._save_images = False

        self._sample_id = "Default"
        self._sample_type = "Rectangular"
        self._sample_width = None
        self._sample_thickness = None
        self._sample_diameter = None
        self._sample_radius = None
        self._sample_custom_area = None
        self._sample_gauge_length = None
        self._sample_extra_dims = ""
        self._sample_temperature = ""
        self._sample_target_stress = ""
        self._sample_dead_weight = ""

        # UI Panels
        self.conn_panel = ConnectionPanel(self.serial_handler)
        self.sample_panel = SamplePanel(
            init_sample_id=self._sample_id,
            init_sample_type=self._sample_type,
            init_width=self._sample_width,
            init_thick=self._sample_thickness,
            init_diam=self._sample_diameter,
            init_custom_area=self._sample_custom_area,
            init_gauge=self._sample_gauge_length,
            init_extra=self._sample_extra_dims,
            init_temp=self._sample_temperature,
            init_stress=self._sample_target_stress,
            init_weight=self._sample_dead_weight
        )
        self.sample_panel.output_folder_changed.connect(self.set_output_folder)
        self.sample_panel.save_sensor_changed.connect(self.set_save_sensor)
        self.sample_panel.save_images_changed.connect(self.set_save_images)
        self.sample_panel.sample_parameters_changed.connect(self.update_sample_parameters)
        self.sample_panel.push_params()  # Pass initial sample geometry to MainWindow and DataWorker

        self.error_mgr = ErrorManager(self)
        self.camera_panel = SampleInspectionPanel()
        self.camera_panel.camera_error.connect(lambda msg: self.show_critical_error_box("Camera Error", msg))

        # Image save triggers
        self.data_worker.request_image_save.connect(self.camera_panel.worker.trigger_save_snapshot)

        self.dashboard_panel = TestDashboardPanel()

        self.capture_ctrl = CaptureController(self, self.camera_panel, self.dashboard_panel)
        self.camera_panel.capture_image_requested.connect(self.capture_ctrl.capture_image)
        self.capture_ctrl.capture_success.connect(self.camera_panel.show_capture_success)
        self.capture_ctrl.capture_error.connect(self.camera_panel.show_capture_error)
        self.camera_panel.worker.capture_saved.connect(self.capture_ctrl.capture_success)
        self.camera_panel.worker.image_received.connect(self.dashboard_panel.update_image)
        self.dashboard_panel.stop_all_requested.connect(lambda: self.serial_handler.send_cmd("STOP"))
        self.dashboard_panel.stop_rec_requested.connect(self.stop_record)
        self.dashboard_panel.capture_requested.connect(self.capture_ctrl.capture_image)
        self.capture_ctrl.capture_success.connect(self.dashboard_panel.show_capture_success)
        self.capture_ctrl.capture_error.connect(self.dashboard_panel.show_capture_error)

        self.graph_ctrl = GraphController(self, self.dashboard_panel)
        self.dashboard_panel.bl_graph_changed.connect(self.graph_ctrl.on_bl_graph_changed)

        self.uni_control = UniaxialMotorControlPanel(self.serial_handler)
        self.uni_control.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self.sensor_panel = SensorDisplayPanel()
        self.sensor_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.sensor_panel.set_zero_load.connect(self.data_worker.zero_load)
        self.sensor_panel.set_zero_disp.connect(self.data_worker.zero_disp)

        self.rec_ctrl = RecordingController(self, self.data_worker, self.camera_panel)
        self.sensor_panel.request_start_rec.connect(self.start_record)
        self.sensor_panel.request_stop_rec.connect(self.stop_record)
        self.sensor_panel.sampling_rate_changed.connect(self.data_worker.update_sampling_rate)


        # Preset Position Controller
        self.preset_ctrl = PresetPositionController(self.serial_handler, self.uni_control, self)
        self.uni_control.set_load_pos.connect(self.preset_ctrl.set_load_pos)
        self.uni_control.set_unload_pos.connect(self.preset_ctrl.set_unload_pos)
        self.uni_control.clear_load_pos.connect(self.preset_ctrl.clear_load_pos)
        self.uni_control.clear_unload_pos.connect(self.preset_ctrl.clear_unload_pos)
        
        # We need a lambda to capture the rpm and target type correctly, 
        # or we could connect directly if the signal signature matches.
        # But wait, go_load_pos emits (int). start_travel_to takes (target_type, rpm).
        self.uni_control.go_load_pos.connect(lambda rpm: self.preset_ctrl.start_travel_to("load", rpm))
        self.uni_control.go_unload_pos.connect(lambda rpm: self.preset_ctrl.start_travel_to("unload", rpm))
        self.uni_control.cancel_travel.connect(self.preset_ctrl.stop_travel)
        
        self.data_worker.encoder_updated.connect(self.preset_ctrl.on_encoder_updated)

        self.setup_layout()
        # Initialize labels done in PresetPositionController init already, 
        # but if we wanted to be safe:
        # self.uni_control.update_load_label(self.preset_ctrl._load_pos)
        
        self.data_thread.start()

    def setup_layout(self):
        main = QWidget()
        self.setCentralWidget(main)
        layout = QHBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar_drawer = QWidget()
        self.sidebar_drawer.setFixedWidth(250)
        self.sidebar_drawer.setStyleSheet("background-color: #ecf0f1; border-right: 1px solid #bdc3c7;")
        drawer_layout = QVBoxLayout(self.sidebar_drawer)
        drawer_layout.setContentsMargins(0, 10, 0, 0)

        logo_path = resource_path("InsituMicronlogo.jpg")
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignCenter)
        self.logo_label.setStyleSheet("background-color: white; margin: 8px 8px 5px 8px; padding: 5px; border-radius: 4px;")
        if os.path.exists(logo_path):
            logo_pixmap = QPixmap(logo_path)
            self.logo_label.setPixmap(logo_pixmap.scaledToHeight(35, Qt.SmoothTransformation))
        drawer_layout.addWidget(self.logo_label)

        self.lbl_menu = QLabel("MENU")
        self.lbl_menu.setStyleSheet("font-weight: bold; font-size: 22px; color: #2c3e50; margin-left: 15px; margin-bottom: 10px;")
        self.sidebar = QListWidget()
        self.sidebar.addItems([
            "Connection",
            "Sample Configuration",
            "Test Configuration",
            "Image Setup",
            "Test Dashboard",
            "Analysis & Reporting"
        ])
        
        self.sidebar.setStyleSheet(f"QListWidget {{ background-color: transparent; border: none; font-size: 20px; }} QListWidget::item {{ padding: 16px; margin: 2px 5px; color: #2c3e50; }} QListWidget::item:selected {{ background-color: {PRIMARY_COLOR}; color: white; border-radius: 5px; }} QListWidget::item:hover:!selected {{ background-color: #dfe6e9; border-radius: 5px; }}")
        self.sidebar.currentRowChanged.connect(self.change_page)

        drawer_layout.addWidget(self.lbl_menu)
        drawer_layout.addWidget(self.sidebar)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)

        self.pages = QStackedWidget()
        self.pages.addWidget(self.conn_panel)         # 0
        self.pages.addWidget(self.sample_panel)       # 1

        self.test_setup_page = QWidget()
        ts_layout = QVBoxLayout(self.test_setup_page)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)

        motion_container = QWidget()
        mc_layout = QVBoxLayout(motion_container)
        mc_layout.setContentsMargins(0, 0, 0, 0)
        mc_layout.addWidget(self.uni_control)
        mc_layout.addStretch()

        sensor_container = QWidget()
        sc_layout = QVBoxLayout(sensor_container)
        sc_layout.setContentsMargins(0, 0, 0, 0)
        sc_layout.addWidget(self.sensor_panel)
        sc_layout.addStretch()

        top_row.addWidget(motion_container, 7)
        top_row.addWidget(sensor_container, 3)

        ts_layout.addLayout(top_row, 0)

        self.plot_uni_lt = pg.PlotWidget()
        setup_plot_style(self.plot_uni_lt, "Displacement vs Time", "Time (s)", "Displacement (mm)")
        self.uni_curve = self.plot_uni_lt.plot(
            pen=pg.mkPen(color='#2980b9', width=3),
            autoDownsample=True, 
            clipToView=True
        )
        
        ts_layout.addWidget(self.plot_uni_lt, 1)

        self.pages.addWidget(self.test_setup_page)    # 2
        self.pages.addWidget(self.camera_panel)       # 3
        self.pages.addWidget(self.dashboard_panel)    # 4

        self.analysis_panel = AnalysisReportingPanel()
        self.pages.addWidget(self.analysis_panel)     # 5

        content_layout.addWidget(self.pages)
        layout.addWidget(self.sidebar_drawer)
        layout.addWidget(content_widget)
        
        self.graph_ctrl.setup_curves("Creep")

    def change_page(self, idx):
        self.pages.setCurrentIndex(idx)

    @Slot(str)
    def set_output_folder(self, folder):
        self._output_folder = folder

    @Slot(dict)
    def update_sample_parameters(self, params):
        self._sample_id = params.get("sample_id", "Default")
        self._sample_type = params.get("sample_type", "Rectangular")
        self._sample_width = params.get("width")
        self._sample_thickness = params.get("thickness")
        self._sample_diameter = params.get("diameter")
        self._sample_radius = params.get("radius")
        self._sample_custom_area = params.get("custom_area")
        self._sample_gauge_length = params.get("gauge_length")
        self._sample_extra_dims = params.get("extra_dims", "")
        self._sample_temperature = params.get("temperature", "")
        self._sample_target_stress = params.get("target_stress", "")
        self._sample_dead_weight = params.get("dead_weight", "")
        if hasattr(self, 'data_worker') and self.data_worker:
            self.data_worker.update_sample_params(params)

    @Slot(bool)
    def set_save_sensor(self, enabled):
        self._save_sensor_data = enabled

    @Slot(bool)
    def set_save_images(self, enabled):
        self._save_images = enabled

    @Slot(dict)
    def update_labels(self, data):
        self.sensor_panel.update_sensors(data["load_n"], data["disp_mm"], data.get("max_disp", 0.0))
        self.sensor_panel.update_offsets(data["zero_load"], data["zero_disp"])
        self.dashboard_panel.update_side_readouts(data["load_n"], data["disp_mm"])
        
        # Keep plot_uni_lt updated for Motor & Homing page
        t_data = self.data_worker._time_data
        d_data = self.data_worker._disp_data
        if len(t_data) > 0 and len(d_data) > 0:
            self.uni_curve.setData(t_data, d_data)

    @Slot(dict)
    def update_graphs(self, data):
        self.graph_ctrl.update_graphs(data)

    @Slot(str)
    def handle_overload_stop(self, msg):
        self.serial_handler.send_cmd("STOP")

        # Stop active travel if in progress
        if hasattr(self, 'preset_ctrl') and self.preset_ctrl._travel_active:
            self.preset_ctrl.stop_travel()

        # Stop recording if currently recording
        if hasattr(self, 'rec_ctrl') and getattr(self.rec_ctrl, '_is_recording', False):
            self.rec_ctrl.stop_record()

        msg_clean = str(msg).strip()
        msg_upper = msg_clean.upper()

        if "KILL" in msg_upper:
            title = "Emergency Stop"
            user_msg = "Kill switch enabled — Motor stopped."
            status_text = "Status: KILL SWITCH ACTIVE"
            status_style = "background-color: #c0392b; color: white; padding: 8px; border-radius: 4px; font-weight: bold;"
        elif "STALL" in msg_upper:
            title = "Motor Stalled"
            user_msg = "Motor stall detected (no motion) — Motor stopped."
            status_text = "Status: MOTOR STALLED"
            status_style = "background-color: #e67e22; color: white; padding: 8px; border-radius: 4px; font-weight: bold;"
        elif "OVERLOAD" in msg_upper:
            title = "Safety Overload"
            user_msg = f"Safety limit reached ({msg_clean}) — Motor stopped."
            status_text = "Status: OVERLOAD STOP"
            status_style = "background-color: #c0392b; color: white; padding: 8px; border-radius: 4px; font-weight: bold;"
        else:
            title = "Safety Stop"
            user_msg = f"Safety stop ({msg_clean}) — Motor stopped."
            status_text = f"Status: FAULT ({msg_clean})"
            status_style = "background-color: #c0392b; color: white; padding: 8px; border-radius: 4px; font-weight: bold;"

        # Update Uniaxial Motor Control Panel status label
        if hasattr(self, 'uni_control') and hasattr(self.uni_control, 'lbl_status'):
            self.uni_control.lbl_status.setText(status_text)
            self.uni_control.lbl_status.setStyleSheet(status_style)

        # Update Test Dashboard recording status if present
        if hasattr(self, 'dashboard_panel') and hasattr(self.dashboard_panel, 'side_rec_status_lbl'):
            self.dashboard_panel.side_rec_status_lbl.setText("STOPPED")
            self.dashboard_panel.side_rec_status_lbl.setStyleSheet("color: #e74c3c; font-size: 12px; font-weight: bold; background: transparent;")

        logger.warning("[Safety] %s: %s", title, user_msg.replace('\n', ' '))
        QMessageBox.critical(self, title, user_msg)

    @Slot(str)
    def handle_recording_error(self, msg):
        self.error_mgr.collect("Recording", msg)

    @Slot(bool, str)
    def handle_connection_status(self, is_connected, msg):
        self.conn_panel.update_status_display(is_connected, msg)

    @Slot()
    def handle_controller_disconnected(self):
        self.conn_panel.update_status_display(False, "Controller Disconnected. Paused and waiting for reconnect...")
        
        # Show non-modal popup
        if not hasattr(self, '_disconnect_msgbox') or self._disconnect_msgbox is None:
            from PySide6.QtWidgets import QMessageBox
            self._disconnect_msgbox = QMessageBox(self)
            self._disconnect_msgbox.setIcon(QMessageBox.Warning)
            self._disconnect_msgbox.setWindowTitle("Controller Disconnected")
            self._disconnect_msgbox.setText("Controller disconnected, check connection. Test is on pause until reconnected.")
            self._disconnect_msgbox.setStandardButtons(QMessageBox.Ok)
            self._disconnect_msgbox.setModal(False)
            
            def on_close(*args):
                self._disconnect_msgbox = None
            
            self._disconnect_msgbox.finished.connect(on_close)
            self._disconnect_msgbox.show()
        
    @Slot()
    def handle_controller_reconnected(self):
        self.conn_panel.update_status_display(True, "Connected (Resumed)")
        
        # Close the disconnect popup if it's still open
        if hasattr(self, '_disconnect_msgbox') and self._disconnect_msgbox is not None:
            try:
                self._disconnect_msgbox.close()
            except:
                pass
            self._disconnect_msgbox = None

        # Show non-invasive toast (mobile style) at the TOP
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
        self._toast = QDialog(self, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._toast.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._toast.setStyleSheet("background-color: #2ecc71; color: white; border-radius: 8px;")
        layout = QVBoxLayout(self._toast)
        lbl = QLabel("Controller reconnected successfully. Test resumed.")
        lbl.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px;")
        layout.addWidget(lbl)
        self._toast.adjustSize()
        # Move to top-center
        geo = self.geometry()
        self._toast.move(geo.center().x() - self._toast.width()//2, geo.top() + 80)
        self._toast.show()
        QTimer.singleShot(2500, self._toast.close)

    def startup_check(self):
        session = TestSessionManager.load_active_session()
        logger.info("Startup check: session=%s", session)
        if session:
            reply = QMessageBox.question(
                self, "Interrupted Test Detected",
                "An unfinished creep test was found.\n\n"
                "The specimen has remained under load during power loss.\n"
                "Do you want to resume data acquisition for this test?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                # 1. Restore Output Folder
                out_dir = session.get("output_folder", "")
                if out_dir:
                    self.set_output_folder(out_dir)
                    if hasattr(self.sample_panel, "set_output_folder_path"):
                        self.sample_panel.set_output_folder_path(out_dir)

                # 2. Restore Sample Configuration
                sample_data = session.get("sample_data", {})
                if sample_data and hasattr(self.sample_panel, "restore_sample_data"):
                    self.sample_panel.restore_sample_data(sample_data)
                elif sample_data:
                    self.update_sample_parameters(sample_data)

                # 3. Restore Tare Settings
                tare_load = float(session.get("tare_load", 0.0))
                tare_disp = float(session.get("tare_disp", 0.0))
                max_disp = float(session.get("max_disp", 0.0))
                self.data_worker._load_offset = tare_load
                self.data_worker._disp_offset = tare_disp
                self.data_worker._max_disp = max_disp
                self.sensor_panel.update_offsets(tare_load, tare_disp)
                self.sensor_panel.update_sensors(0.0, 0.0, max_disp)

                # 4. Restore Sampling Rate
                rate = int(session.get("sampling_rate", 20))
                self.sensor_panel.inp_rate.setText(str(rate))
                self.data_worker.update_sampling_rate(rate)

                # 5. Restore UI Recording State
                if hasattr(self.sensor_panel, 'set_recording_state'):
                    self.sensor_panel.set_recording_state(True)
                if hasattr(self, "dashboard_panel"):
                    if hasattr(self.dashboard_panel, "bl_graph_toggle"):
                        self.dashboard_panel.bl_graph_toggle.setEnabled(False)
                    elif hasattr(self.dashboard_panel, "bl_graph_combo"):
                        self.dashboard_panel.bl_graph_combo.setEnabled(False)
                    elapsed = max(0.0, time.time() - session.get("start_time_epoch", time.time()))
                    self.dashboard_panel.start_rec_timer(initial_seconds=int(elapsed))

                csv_path = session["file_path"]
                self.rec_ctrl.current_save_dir = os.path.dirname(csv_path)
                self.rec_ctrl._is_recording = True
                self.rec_ctrl.recording_started.emit()

                # 6. Resume Worker Recording
                self.data_worker.resume_recording(session)
                WindowsPowerManager.prevent_sleep()
                logger.info("Test resumed from crash recovery: %s", csv_path)
            else:
                TestSessionManager.mark_completed()
                logger.info("User declined crash recovery, session cleared.")

    @Slot()
    def start_record(self):
        self.rec_ctrl.start_record()

    @Slot()
    def stop_record(self):
        self.rec_ctrl.stop_record()

    def show_critical_error_box(self, title, message):
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event):
        if hasattr(self, 'data_worker') and self.data_worker.is_recording():
            reply = QMessageBox.question(
                self, 'Test In Progress',
                "A creep test is currently recording!\n\n"
                "• Click 'Yes' to End the test normally (saves Excel export and finishes test).\n"
                "• Click 'No' to Exit and preserve session for Crash Recovery (test will resume when restarted).\n"
                "• Click 'Cancel' to keep running.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel
            )
            if reply == QMessageBox.Cancel:
                event.ignore()
                return
            elif reply == QMessageBox.Yes:
                if hasattr(self, 'rec_ctrl') and self.rec_ctrl:
                    self.stop_record()
            else:
                # User selected 'No' -> Preserve session file intact for recovery!
                logger.info("Preserving active test session for crash recovery on exit.")
                WindowsPowerManager.allow_sleep()
                self.data_worker._is_recording = False
                if self.data_worker.csv_writer:
                    self.data_worker.csv_writer.close_file()
                self.serial_handler.disconnect()
                if hasattr(self, 'data_thread'):
                    self.data_thread.quit()
                    self.data_thread.wait()
                if hasattr(self.camera_panel, 'worker'):
                    self.camera_panel.worker.shutdown()
                if hasattr(self.camera_panel, '_cam_thread'):
                    self.camera_panel._cam_thread.quit()
                    self.camera_panel._cam_thread.wait()
                event.accept()
                return
        else:
            reply = QMessageBox.question(
                self, 'Confirm Exit',
                "Are you sure you want to close Insitumicron Creep GUI?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return

        # Normal shutdown
        WindowsPowerManager.allow_sleep()
        self.serial_handler.disconnect()
        if hasattr(self, 'data_thread'):
            self.data_thread.quit()
            self.data_thread.wait()
        if hasattr(self.camera_panel, 'worker'):
            self.camera_panel.worker.shutdown()
        if hasattr(self.camera_panel, '_cam_thread'):
            self.camera_panel._cam_thread.quit()
            self.camera_panel._cam_thread.wait()
        event.accept()

