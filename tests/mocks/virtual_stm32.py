# tests/mocks/virtual_stm32.py
"""
Virtual STM32 Hardware Emulator for Insitumicron Creep GUI.
Simulates STM32 firmware (creep_firmware.c) over a duck-typed serial interface compatible with pySerial.

Features:
- Telemetry generation: LOAD (N) and DISP (mm) at 20 Hz (real-time) or accelerated burst.
- Encoder emulation for homing: E:<pulses>.
- Full STM32 command processing: START, STOP, VEL, VELSET, ACC, DEC.
- Specimen mechanics simulation (viscoelastic creep, elastic tension, noise).
- Fault injection: disconnect, reconnect, packet corruption, E-stop KILL, silence.
"""

import time
import math
import random
import threading
import queue
from typing import Optional


class MockSerial:
    """
    Duck-typed drop-in replacement for serial.Serial.
    Exposes readline(), write(), close(), is_open, and in_waiting.
    """

    def __init__(self, emulator: "VirtualSTM32", timeout: float = 0.01):
        self.emulator = emulator
        self.timeout = timeout
        self.is_open = True
        self._write_buffer = bytearray()
        self._rx_queue: queue.Queue = queue.Queue()
        self._disconnected = False
        self._exception_on_next_op: Optional[Exception] = None

    @property
    def in_waiting(self) -> int:
        return self._rx_queue.qsize()

    def feed_line(self, line: str):
        """Called by emulator to push a telemetry line to the host PC."""
        if not self._disconnected and self.is_open:
            if not line.endswith("\n"):
                line += "\r\n"
            self._rx_queue.put(line.encode("utf-8", errors="ignore"))

    def readline(self) -> bytes:
        if self._exception_on_next_op:
            exc = self._exception_on_next_op
            self._exception_on_next_op = None
            raise exc

        if not self.is_open or self._disconnected:
            import serial
            raise serial.SerialException("Mock device disconnected")

        try:
            return self._rx_queue.get(timeout=self.timeout)
        except queue.Empty:
            return b""

    def write(self, data: bytes) -> int:
        if self._exception_on_next_op:
            exc = self._exception_on_next_op
            self._exception_on_next_op = None
            raise exc

        if not self.is_open or self._disconnected:
            import serial
            raise serial.SerialException("Mock device disconnected")

        self._write_buffer.extend(data)
        while b"\n" in self._write_buffer:
            idx = self._write_buffer.index(b"\n")
            line = self._write_buffer[:idx].decode("utf-8", errors="ignore").strip()
            self._write_buffer = self._write_buffer[idx + 1:]
            if line:
                self.emulator.handle_command(line)
        return len(data)

    def close(self):
        self.is_open = False
        self._disconnected = True

    def flush(self):
        pass


class VirtualSTM32:
    """
    Simulated STM32 microcontroller running creep_firmware.c logic.
    """

    def __init__(self, noise_std: float = 0.02, accelerated: bool = False):
        self.noise_std = noise_std
        self.accelerated = accelerated

        # Hardware states
        self.motor_running = False
        self.current_velocity_rpm = 0
        self.current_acc = 50
        self.current_dec = 50

        # Physical kinematics
        # 1 RPM = (2 mm / 1 rev) / 60 s = 0.0333 mm/s
        self.disp_mm = 0.0
        self.encoder_pulses = 0  # 1 mm = 5000 pulses
        self.load_n = 0.0
        self.baseline_load_n = 50.0  # Constant creep deadweight (e.g. 50 N)

        # Creep specimen parameters
        self.creep_rate_mm_s = 0.0001  # Natural specimen creep strain rate
        self.stiffness_n_per_mm = 400.0  # Elastic crosshead coupling

        # Transport
        self.mock_serial = MockSerial(self)
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Fault injection controls
        self.silence_until = 0.0
        self.kill_triggered = False

    def start(self):
        """Start real-time background telemetry loop."""
        self._running = True
        self.mock_serial.is_open = True
        self.mock_serial._disconnected = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop telemetry generator."""
        self._running = False
        self.mock_serial.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def handle_command(self, cmd: str):
        """Processes incoming host serial commands identical to creep_firmware.c."""
        if cmd == "START":
            self.motor_running = True
        elif cmd.startswith("START:"):
            try:
                self.current_velocity_rpm = int(cmd[6:])
            except ValueError:
                pass
            self.motor_running = True
        elif cmd == "STOP":
            self.motor_running = False
            self.current_velocity_rpm = 0
        elif cmd.startswith("VEL:"):
            try:
                self.current_velocity_rpm = int(cmd[4:])
            except ValueError:
                pass
        elif cmd.startswith("VELSET:"):
            try:
                self.current_velocity_rpm = int(cmd[7:])
            except ValueError:
                pass
        elif cmd.startswith("ACC:"):
            try:
                self.current_acc = int(cmd[4:])
            except ValueError:
                pass
        elif cmd.startswith("DEC:"):
            try:
                self.current_dec = int(cmd[4:])
            except ValueError:
                pass

    def step_physics(self, dt: float = 0.05):
        """Simulates crosshead motion, specimen creep, and sensor updates."""
        # Crosshead motion: mm/s = rpm * (2.0) / 60.0
        crosshead_speed_mm_s = (self.current_velocity_rpm * 2.0) / 60.0 if self.motor_running else 0.0

        # Update crosshead position
        self.disp_mm += (crosshead_speed_mm_s + self.creep_rate_mm_s) * dt

        # Encoder pulses: 1 rev = 10000 pulses = 2 mm
        # 1 mm = 5000 pulses
        self.encoder_pulses = int(self.disp_mm * 5000.0)

        # Load calculation: deadweight baseline + elastic coupling to crosshead
        raw_load = self.baseline_load_n + (self.disp_mm * 10.0)
        noise = random.gauss(0, self.noise_std) if self.noise_std > 0 else 0.0
        self.load_n = max(0.0, raw_load + noise)

    def generate_telemetry_packet(self) -> str:
        """Returns standard firmware telemetry line: LOAD:%.3f,DISP:%.4f"""
        return f"LOAD:{self.load_n:.3f},DISP:{self.disp_mm:.4f}\r\n"

    def generate_encoder_packet(self) -> str:
        """Returns standard homing encoder line: E:%ld"""
        return f"E:{self.encoder_pulses}\r\n"

    def _run_loop(self):
        """20 Hz real-time telemetry generator."""
        last_time = time.perf_counter()
        encoder_counter = 0

        while self._running:
            now = time.perf_counter()
            dt = now - last_time
            last_time = now

            if now >= self.silence_until:
                self.step_physics(dt)

                if self.kill_triggered:
                    self.mock_serial.feed_line("KILL\r\n")
                    self.kill_triggered = False
                else:
                    self.mock_serial.feed_line(self.generate_telemetry_packet())

                    # Periodic encoder update (every 4th cycle = 5 Hz)
                    encoder_counter += 1
                    if encoder_counter >= 4:
                        self.mock_serial.feed_line(self.generate_encoder_packet())
                        encoder_counter = 0

            time.sleep(0.05)  # 20 Hz

    # =========================================================
    # Fault Injection Hooks
    # =========================================================

    def inject_disconnect(self):
        """Simulates physical USB cable unplug."""
        import serial
        self.mock_serial._disconnected = True
        self.mock_serial._exception_on_next_op = serial.SerialException("Device physically disconnected")

    def reconnect(self):
        """Simulates physical USB cable re-insert."""
        self.mock_serial.is_open = True
        self.mock_serial._disconnected = False
        self.mock_serial._exception_on_next_op = None

    def inject_silence(self, duration_seconds: float):
        """Simulates controller silence / freeze without disconnecting."""
        self.silence_until = time.perf_counter() + duration_seconds

    def inject_corrupted_packet(self, custom_string: Optional[str] = None):
        """Injects malformed or garbled data into the read stream."""
        garbled = custom_string or "LOAD:NaN,DISP:broken_float\r\n"
        self.mock_serial.feed_line(garbled)

    def inject_kill_switch(self):
        """Simulates hardware E-stop active-low kill switch on PB4."""
        self.kill_triggered = True
        self.motor_running = False
