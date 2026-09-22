# serial_handler.py
import serial
import serial.tools.list_ports
import threading
import time
from PySide6.QtCore import QObject, Signal, Slot
from logging_config import get_logger

logger = get_logger(__name__)

class SerialHandler(QObject):
    # Signals to be emitted from this QObject
    data_received = Signal(str)
    connection_status_changed = Signal(bool, str)
    controller_disconnected = Signal()
    controller_reconnected = Signal()

    def __init__(self):
        super().__init__()
        self.ser = None
        self.read_thread = None
        self.running = False
        self._is_connected_status = False
        self._write_lock = threading.Lock()
        
        self.reconnecting = False
        self.last_port = None
        self.last_baudrate = 115200

    def is_connected(self):
        return self._is_connected_status

    def get_ports(self):
        """Returns a list of available serial port devices."""
        return [port.device for port in serial.tools.list_ports.comports()]

    def detect_stm32_port(self):
        """Scan COM ports for STM32 device (ST Microelectronics VID=0x0483).
        Returns the port name if found, None otherwise."""
        ST_VID = 0x0483
        for port_info in serial.tools.list_ports.comports():
            if port_info.vid == ST_VID:
                logger.debug("STM32 detected on %s (VID:%04X PID:%04X)",
                             port_info.device, port_info.vid, port_info.pid or 0)
                return port_info.device
        return None

    def connect(self, port, baudrate=115200):
        if self.is_connected():
            logger.info("Already connected, disconnecting first.")
            self.disconnect()
            time.sleep(0.05)

        self.reconnecting = False
        self.last_port = port
        self.last_baudrate = baudrate

        try:
            self.ser = serial.Serial(port, baudrate, timeout=0.2)
            self.running = True
            self._is_connected_status = True
            self.connected_since = time.time()
            self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
            self.read_thread.start()
            self.connection_status_changed.emit(True, f"Connected to {port} @ {baudrate}")
            logger.info("Successfully connected to %s at %d baud.", port, baudrate)
            return True
        except Exception as e:
            self._is_connected_status = False
            self.connection_status_changed.emit(False, f"Connection Error: {e}")
            logger.error("Could not connect to %s - %s", port, e)
            return False

    def disconnect(self):
        self.reconnecting = False
        self.running = False
        if self.read_thread and self.read_thread.is_alive():
            self.read_thread.join(timeout=0.5)
            if self.read_thread.is_alive():
                logger.warning("Read thread did not terminate cleanly.")

        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                logger.info("Serial port closed.")
            except Exception as e:
                logger.error("Error closing serial port: %s", e)

        self._is_connected_status = False
        self.connection_status_changed.emit(False, "Disconnected")
        logger.info("Disconnected.")

    def send_cmd(self, cmd):
        if self.is_connected():
            try:
                with self._write_lock:
                    self.ser.write((cmd + "\n").encode('utf-8'))
                logger.debug("[TX] %s", cmd)
            except serial.SerialException as e:
                logger.error("[Serial TX Error] %s - Disconnecting due to error.", e)
                self.trigger_reconnect()
            except Exception as e:
                logger.error("[Serial TX Error] An unexpected error occurred during send: %s", e)
        else:
            logger.warning("[TX Failed] Not connected. Command: %s", cmd)

    def send_velset(self, rpm: int):
        """
        Send VELSET command to adjust motor speed on the fly.
        """
        self.send_cmd(f"VELSET:{int(rpm)}")

    def read_loop(self):
        logger.info("read_loop STARTED.")
        last_rx_time = time.time()
        while self.running and self.is_connected():
            try:
                raw_line_bytes = self.ser.readline()
                if raw_line_bytes:
                    line = raw_line_bytes.decode(errors="ignore").strip()
                    if line:
                        last_rx_time = time.time()
                        self.data_received.emit(line)
                else:
                    # Timeout on readline (0.01s). Check if controller has gone silent.
                    # STM32 streams telemetry at 20 Hz (every 50ms). If 1.2s passes with NO data,
                    # or if the port is physically unplugged from Windows, detect disconnect.
                    now = time.time()
                    if now - last_rx_time > 1.2:
                        port_list = self.get_ports()
                        if (self.last_port not in port_list) or (now - last_rx_time > 2.0):
                            logger.warning("Controller silence (%.2fs) or port missing. Initiating auto-reconnect...", now - last_rx_time)
                            self.trigger_reconnect()
                            break

            except serial.SerialException as e:
                logger.error("Serial port error: %s. Initiating auto-reconnect...", e)
                self.trigger_reconnect()
                break
            except Exception as e:
                logger.error("An unexpected error occurred: %s. Stopping read loop.", e)
                self.trigger_reconnect()
                break
        logger.info("read_loop STOPPED.")

    def trigger_reconnect(self):
        if self.reconnecting:
            return
        self.reconnecting = True
        self.running = False
        self._is_connected_status = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except:
                pass
        self.connection_status_changed.emit(False, "Controller Disconnected - Waiting for reconnect...")
        self.controller_disconnected.emit()
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        logger.info("Auto-reconnect loop started...")
        while self.reconnecting:
            time.sleep(1.0)
            if not self.reconnecting:
                break
            try:
                # Only attempt if the port exists physically, to prevent freezing
                port_list = self.get_ports()
                port_to_try = self.last_port if (self.last_port and self.last_port in port_list) else self.detect_stm32_port()
                
                if not port_to_try:
                    continue
                    
                self.ser = serial.Serial(port_to_try, self.last_baudrate, timeout=0.2)
                self.running = True
                self._is_connected_status = True
                self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
                self.read_thread.start()
                self.reconnecting = False
                self.last_port = port_to_try
                self.connected_since = time.time()
                self.connection_status_changed.emit(True, f"Connected to {port_to_try} @ {self.last_baudrate}")
                self.controller_reconnected.emit()
                logger.info("Reconnected successfully to %s", port_to_try)
                break
            except Exception as e:
                logger.debug("Reconnect failed: %s", e)
