import os
import re
import time
import csv
import math
import threading
import collections
from PySide6.QtCore import QObject, Signal, Slot, QMutex, QMutexLocker
from logging_config import get_logger
from test_recovery import TestSessionManager

logger = get_logger(__name__)

# Pre-compiled pattern: LOAD:±xxx.xxx,DISP:±xxx.xxxx
# Load value arrives in Newtons (firmware converts kg*9.81 before sending).
# Displacement is in millimetres.
_LOAD_DISP_RE = re.compile(
    r"LOAD:\s*([+-]?\d+\.?\d*).*?DISP:\s*([+-]?\d+\.?\d*)",
    re.IGNORECASE
)


class CSVWriter(QObject):
    """Streams data rows to CSV file in real-time during recording."""

    def __init__(self):
        super().__init__()
        self.csv_file = None
        self.csv_writer_obj = None
        self.is_open = False
        self.rows_written = 0

    def open_file(self, path, headers):
        try:
            self.csv_file = open(path, 'w', newline='', encoding='utf-8')
            self.csv_writer_obj = csv.writer(self.csv_file)
            self.csv_writer_obj.writerow(headers)
            self.csv_file.flush()
            self.is_open = True
            self.rows_written = 0
            logger.info("CSV file opened: %s", path)
            return True
        except Exception as e:
            logger.error("Failed to open CSV file: %s", e)
            return False

    def append_file(self, path):
        try:
            self.csv_file = open(path, 'a', newline='', encoding='utf-8')
            self.csv_writer_obj = csv.writer(self.csv_file)
            self.csv_file.flush()
            self.is_open = True
            self.rows_written = 0
            logger.info("CSV file opened in append mode: %s", path)
            return True
        except Exception as e:
            logger.error("Failed to append CSV file: %s", e)
            return False

    def write_row(self, row_data):
        if not self.is_open or self.csv_writer_obj is None:
            return False
        try:
            self.csv_writer_obj.writerow(row_data)
            self.csv_file.flush()
            os.fsync(self.csv_file.fileno())  # Force OS to write to physical disk
            self.rows_written += 1
            return True
        except Exception as e:
            logger.error("CSV Writer Error writing row: %s", e)
            return False

    def close_file(self):
        if self.is_open and self.csv_file:
            try:
                self.csv_file.close()
                self.is_open = False
                logger.info("CSV file closed. Rows written: %d", self.rows_written)
            except Exception as e:
                logger.error("CSV Writer Error closing file: %s", e)


class DataWorker(QObject):
    # --- Live sensor signals ---
    ui_update          = Signal(dict)   # keys: load_n, disp_mm
    plot_update        = Signal(dict)   # keys: time, load, disp
    overload_detected  = Signal(str)
    request_image_save = Signal(str)
    recording_error    = Signal(str)
    encoder_updated    = Signal(int)    # encoder pulses (homing only)

    def __init__(self, serial_handler):
        super().__init__()
        self.serial = serial_handler

        # Filtered / tared sensor values
        self._load_filtered  = 0.0   # N (after median filter)
        self._disp_filtered  = 0.0   # mm
        self._load_offset    = 0.0   # N tare
        self._disp_offset    = 0.0   # mm tare
        self._last_data_received_time = 0.0

        # Safety latch
        self._overload_fired = False

        # Median filter (3-sample) for load
        self._load_med_buf = collections.deque(maxlen=3)

        # Recording
        self._is_recording  = False
        self._start_time    = 0.0
        self.csv_writer     = None
        self._images_dir    = ""

        MAX_POINTS = 10000
        self._time_data = collections.deque(maxlen=MAX_POINTS)
        self._load_data = collections.deque(maxlen=MAX_POINTS)
        self._disp_data = collections.deque(maxlen=MAX_POINTS)
        self._stress_data = collections.deque(maxlen=MAX_POINTS)
        self._strain_data = collections.deque(maxlen=MAX_POINTS)

        self._load_offset = 0.0
        self._disp_offset = 0.0
        self._max_disp    = 0.0

        self.last_ui_update = 0.0
        self.last_plot_update = 0.0

        self._sample_area = 10.0
        self._sample_gauge = 50.0

        # Sampling rate in samples per HOUR (default 20 = 1 sample per 3 min)
        self._sampling_rate = 20

        self._rec_thread = None

        if hasattr(self.serial, 'controller_disconnected'):
            self.serial.controller_disconnected.connect(self._on_controller_disconnected)
        if hasattr(self.serial, 'controller_reconnected'):
            self.serial.controller_reconnected.connect(self._on_controller_reconnected)

    @Slot()
    def _on_controller_disconnected(self):
        self._load_med_buf.clear()

    @Slot()
    def _on_controller_reconnected(self):
        self._load_med_buf.clear()

    @Slot(dict)
    def update_sample_params(self, params):
        stype = params.get("sample_type", "Rectangular")
        if stype == "Rectangular":
            w = params.get("width")
            t = params.get("thickness")
            if w is not None and t is not None:
                self._sample_area = float(w) * float(t)
            else:
                self._sample_area = 1.0
            gl = params.get("gauge_length")
            self._sample_gauge = float(gl) if gl is not None else 50.0
        elif stype == "Circular":
            if "diameter" in params and params.get("diameter") is not None:
                d = float(params.get("diameter"))
                self._sample_area = (math.pi / 4.0) * d * d
            elif "radius" in params and params.get("radius") is not None:
                r = float(params.get("radius"))
                self._sample_area = math.pi * r * r
            else:
                self._sample_area = 1.0
            gl = params.get("gauge_length")
            self._sample_gauge = float(gl) if gl is not None else 50.0
        elif stype == "Custom / Direct Area":
            ca = params.get("custom_area")
            self._sample_area = float(ca) if ca is not None else 1.0
            gl = params.get("gauge_length")
            self._sample_gauge = float(gl) if gl is not None else 50.0
        
        if self._sample_area <= 0.001:
            self._sample_area = 1.0
        if self._sample_gauge <= 0.001:
            self._sample_gauge = 1.0

    # =========================================================
    # CORE PARSING & PROCESSINGE PARSER
    # =========================================================

    @Slot(str)
    def process_line(self, line: str):
        current_time = time.perf_counter()
        try:
            # ── Encoder data (homing only) ──
            if line.startswith("E:"):
                try:
                    pos = int(line[2:].strip())
                    self.encoder_updated.emit(pos)
                except ValueError:
                    pass
                return

            # ── Kill switch / hardware overload / stall ──
            if line.startswith("KILL") or line.startswith("OVERLOAD") or line.startswith("ERR:STALL"):
                if not self._overload_fired:
                    self._overload_fired = True
                    self.serial.send_cmd("STOP")
                    self.overload_detected.emit(line.strip())
                return

            # ── Primary parser: LOAD:±xxx.xxx,DISP:±xxx.xxxx ──
            raw_load_n, raw_disp_mm = None, None
            if line.startswith("LOAD:"):
                m = _LOAD_DISP_RE.match(line)
                if m:
                    raw_load_n  = float(m.group(1))  # N (firmware already multiplied by 9.81)
                    raw_disp_mm = float(m.group(2))  # mm

            if raw_load_n is None or raw_disp_mm is None:
                return

            # On reconnect, ignore uninitialized firmware boot packets.
            # The STM32 load conditioner (I2C2) takes ~1.5s to complete its first conversion.
            # During boot, STM32 transmits uninitialized static float zeros: LOAD:0.000,DISP:0.0000.
            if getattr(self.serial, 'connected_since', 0) > 0:
                reconnect_age = time.time() - self.serial.connected_since
                if reconnect_age < 3.0:
                    # If load was tared against dead weights (load_offset != 0),
                    # a raw load near 0.0 is 100% an uninitialized boot packet.
                    if abs(self._load_offset) > 0.5 and abs(raw_load_n) < 0.01:
                        return
                    # If displacement was tared, a raw disp near 0.0 is also an uninitialized boot packet.
                    if abs(self._disp_offset) > 0.05 and abs(raw_disp_mm) < 0.0001:
                        return
                    # If neither was tared, drop literal zero packets within the first 1.5s
                    if reconnect_age < 1.5 and abs(raw_load_n) < 0.001 and abs(raw_disp_mm) < 0.0001:
                        return

            self._last_data_received_time = time.time()

            # Median filter on load
            self._load_med_buf.append(raw_load_n)
            self._load_filtered = sorted(self._load_med_buf)[len(self._load_med_buf) // 2]
            self._disp_filtered = raw_disp_mm

            tared_load = self._load_filtered - self._load_offset
            tared_disp = self._disp_filtered - self._disp_offset
            
            if tared_disp > self._max_disp:
                self._max_disp = tared_disp

        # Note: Physical safety limits are handled exclusively by STM32 firmware
        # via the KILL switch or OVERLOAD serial messages.
            self._overload_fired = False

            # ── Rate-limited UI update (~30 Hz) ──
            if current_time - self.last_ui_update >= 0.033:
                self.ui_update.emit({
                    "load_n":   tared_load,
                    "disp_mm":  tared_disp,
                    "max_disp": self._max_disp,
                    "zero_load": self._load_offset,
                    "zero_disp": self._disp_offset,
                })
                self.last_ui_update = current_time

            if current_time - self.last_plot_update >= 0.5:
                self.plot_update.emit({
                    "time": list(self._time_data),
                    "load": list(self._load_data),
                    "disp": list(self._disp_data),
                    "stress": list(self._stress_data),
                    "strain": list(self._strain_data)
                })
                self.last_plot_update = current_time

        except Exception as e:
            logger.error("DataWorker process_line error: %s", e)

    # =========================================================
    # RECORDING
    # =========================================================

    def record_snapshot(self) -> bool:
        """Called by the recording thread at the configured samples/hour rate.
        Returns True if a snapshot was successfully logged, False otherwise.
        """
        if not self.serial.is_connected():
            return False
        # Skip if we have never received any data at all yet
        if self._last_data_received_time == 0.0:
            return False

        # If recently reconnected, do not record while sensors are still settling
        if getattr(self.serial, 'connected_since', 0) > 0:
            reconnect_age = time.time() - self.serial.connected_since
            if reconnect_age < 3.0:
                if abs(self._load_offset) > 0.5 and abs(self._load_filtered) < 0.01:
                    return False
                if abs(self._disp_offset) > 0.05 and abs(self._disp_filtered) < 0.0001:
                    return False

        t_rel     = time.time() - self._start_time
        load_n    = self._load_filtered - self._load_offset
        disp_mm   = self._disp_filtered - self._disp_offset

        stress = load_n / self._sample_area
        strain = disp_mm / self._sample_gauge

        self._time_data.append(t_rel)
        self._load_data.append(load_n)
        self._disp_data.append(disp_mm)
        self._stress_data.append(stress)
        self._strain_data.append(strain)

        row_data = [f"{t_rel:.3f}", f"{load_n:.3f}", f"{disp_mm:.4f}"]

        # Optional image path
        img_path = ""
        if self._images_dir:
            img_filename = f"img_{t_rel:08.3f}s.tiff"
            img_path = os.path.normpath(os.path.join(self._images_dir, img_filename))
            row_data.append(img_path)

        if self.csv_writer and self.csv_writer.is_open:
            if not self.csv_writer.write_row(row_data):
                self._is_recording = False
                self.recording_error.emit("CSV write failed — recording stopped")
                return False

        if img_path:
            self.request_image_save.emit(img_path)

        return True

    def _recording_loop(self):
        """Background thread: samples at _sampling_rate samples per HOUR.
        Re-reads self._sampling_rate each iteration so live changes take effect.
        """
        next_sample_time = time.time()  # First sample immediately

        while self._is_recording:
            now = time.time()
            if now >= next_sample_time:
                recorded = self.record_snapshot()
                # Re-read rate each time (user may change it mid-test)
                interval_s = 3600.0 / max(1, self._sampling_rate)
                if recorded:
                    next_sample_time = now + interval_s
                else:
                    # Could not record (disconnected / no data yet). Retry in 0.1s.
                    next_sample_time = now + 0.1

            # Sleep in small slices for responsive stopping
            sleep_duration = min(0.1, max(0.01, next_sample_time - time.time()))
            time.sleep(sleep_duration)

    def start_recording(self, csv_path: str, images_dir: str = "", sampling_rate: int = None, output_folder: str = "", sample_data: dict = None):
        if sampling_rate is not None and sampling_rate > 0:
            self._sampling_rate = sampling_rate

        self._time_data.clear()
        self._load_data.clear()
        self._disp_data.clear()
        self._images_dir = images_dir

        self.csv_writer = CSVWriter()
        headers = ["Time(s)", "Load(N)", "Disp(mm)"]
        if images_dir:
            headers.append("Image Path")

        if csv_path:
            if not self.csv_writer.open_file(csv_path, headers):
                logger.error("Failed to open CSV file for recording.")
                return

        self._start_time   = time.time()
        self._is_recording = True
        
        TestSessionManager.save_session(
            self._start_time, csv_path, self._load_offset, self._disp_offset, 
            self._max_disp, self._sampling_rate, images_dir, output_folder, sample_data
        )
        
        self._rec_thread   = threading.Thread(target=self._recording_loop, daemon=True)
        self._rec_thread.start()
        logger.info("Recording STARTED - %.1f samples/hour", self._sampling_rate)

    def resume_recording(self, session_data):
        self._time_data.clear()
        self._load_data.clear()
        self._disp_data.clear()
        
        self._start_time = session_data["start_time_epoch"]
        self._images_dir = session_data["images_dir"]
        self._load_offset = session_data["tare_load"]
        self._disp_offset = session_data["tare_disp"]
        self._max_disp = session_data["max_disp"]
        self._sampling_rate = session_data["sampling_rate"]
        csv_path = session_data["file_path"]

        self.csv_writer = CSVWriter()
        if csv_path:
            if not self.csv_writer.append_file(csv_path):
                logger.error("Failed to append CSV file for resuming.")
                return

        self._is_recording = True
        self._rec_thread   = threading.Thread(target=self._recording_loop, daemon=True)
        self._rec_thread.start()
        logger.info("Recording RESUMED from crash - %.1f samples/hour", self._sampling_rate)

    def request_start_recording(self, csv_path: str, images_dir: str = "", sampling_rate: int = None, output_folder: str = "", sample_data: dict = None):
        self.start_recording(csv_path, images_dir, sampling_rate, output_folder, sample_data)

    @Slot()
    def stop_recording(self):
        self._is_recording = False
        TestSessionManager.mark_completed()
        if self._rec_thread:
            self._rec_thread.join(timeout=2.0)
        if self.csv_writer:
            self.csv_writer.close_file()
            self.csv_writer = None
        logger.info("Recording STOPPED.")

    def is_recording(self) -> bool:
        return self._is_recording

    # =========================================================
    # ZERO / TARE
    # =========================================================

    @Slot()
    def zero_load(self):
        """Software tare for load display."""
        self._load_offset = self._load_filtered

    @Slot()
    def zero_disp(self):
        """Software tare for displacement display."""
        self._disp_offset = self._disp_filtered
        self._max_disp = 0.0

    # Keep old names as aliases for backward-compat with any wired signals
    @Slot()
    def zero_uni_load(self):
        self.zero_load()

    @Slot()
    def zero_uni_disp(self):
        self.zero_disp()

    # =========================================================
    # CONFIGURATION SLOTS
    # =========================================================

    @Slot(int)
    def update_sampling_rate(self, rate: int):
        """Set sampling rate in samples per hour."""
        if rate > 0:
            self._sampling_rate = rate
            logger.info("Sampling rate updated: %d samples/hour", rate)
