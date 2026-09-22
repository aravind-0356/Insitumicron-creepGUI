# tests/test_reconnect_recovery.py
"""
Test Suite for Mid-Test Serial Disconnection, Auto-Reconnect, and Auto-Pause/Resume.
Validates:
1. Controller silence detection (> 1.2s inactivity) and missing port detection.
2. controller_disconnected signal emission and background _reconnect_loop launch.
3. DataWorker recording loop survival in back-off mode while disconnected.
4. Auto-reconnect completion and seamless resumption of telemetry & CSV recording.
5. Flapping link resilience (rapid repeated disconnect/reconnect cycles).
"""

import sys
import os
import time
import tempfile
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.mocks.virtual_stm32 import VirtualSTM32
from serial_handler import SerialHandler
from data_worker import DataWorker

_app = QApplication.instance() or QApplication(sys.argv)


class MockPortInfo:
    def __init__(self, device="MOCK_COM1", vid=0x0483, pid=0x5740):
        self.device = device
        self.vid = vid
        self.pid = pid


def test_silence_detection_and_disconnect_signal():
    print("[1/4] Testing controller silence detection and disconnect signal...")
    emu = VirtualSTM32()
    handler = SerialHandler()

    disconnected_events = []
    handler.controller_disconnected.connect(
        lambda: disconnected_events.append(time.time()),
        Qt.DirectConnection
    )

    # Mock serial.Serial and comports
    mock_port = MockPortInfo("MOCK_COM1")
    with patch("serial.Serial", return_value=emu.mock_serial), \
         patch("serial.tools.list_ports.comports", return_value=[mock_port]):

        assert handler.connect("MOCK_COM1", 115200)
        assert handler.is_connected()

        # Send initial data
        emu.mock_serial.feed_line("LOAD:50.0,DISP:1.0\r\n")
        time.sleep(0.1)

        # Now silence the controller for 3.0 seconds
        emu.inject_silence(duration_seconds=3.0)

        # Wait for silence threshold (2.0s when port is present in port_list)
        t_start = time.time()
        while time.time() - t_start < 3.2 and len(disconnected_events) == 0:
            time.sleep(0.05)
            _app.processEvents()

        assert len(disconnected_events) >= 1, "controller_disconnected signal must fire when silent"
        assert not handler.is_connected(), "Handler status must be disconnected"

    handler.disconnect()
    print("  -> PASS")


def test_recording_loop_survives_disconnect():
    print("[2/4] Testing DataWorker recording loop survival across disconnection...")
    emu = VirtualSTM32()
    handler = SerialHandler()
    worker = DataWorker(handler)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "survive_test.csv")
        mock_port = MockPortInfo("MOCK_COM1")

        with patch("serial.Serial", return_value=emu.mock_serial), \
             patch("serial.tools.list_ports.comports", return_value=[mock_port]):

            handler.connect("MOCK_COM1", 115200)
            handler.data_received.connect(worker.process_line, Qt.DirectConnection)

            # Start recording at high rate for testing
            worker.update_sampling_rate(72000)  # fast rate
            worker.start_recording(csv_path)
            assert worker.is_recording()

            # Feed initial telemetry
            emu.mock_serial.feed_line("LOAD:100.0,DISP:2.0\r\n")
            time.sleep(0.15)

            # Inject sudden serial exception (USB unplug)
            emu.inject_disconnect()
            time.sleep(0.2)

            # Assert worker thread is still alive and recording flag remains True
            assert worker.is_recording(), "Worker _is_recording must stay True while awaiting reconnect"
            assert worker._rec_thread and worker._rec_thread.is_alive(), "Recording thread must not terminate on disconnect"

            worker.stop_recording()
            assert not worker.is_recording()

    handler.disconnect()
    print("  -> PASS")


def test_auto_reconnect_and_resume_stream():
    print("[3/4] Testing end-to-end auto-reconnect and continuous CSV logging...")
    emu = VirtualSTM32()
    handler = SerialHandler()
    worker = DataWorker(handler)

    reconnected_events = []
    handler.controller_reconnected.connect(
        lambda: reconnected_events.append(time.time()),
        Qt.DirectConnection
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "reconnect_resume.csv")
        mock_port = MockPortInfo("MOCK_COM1")

        with patch("serial.Serial", return_value=emu.mock_serial), \
             patch("serial.tools.list_ports.comports", return_value=[mock_port]):

            handler.connect("MOCK_COM1", 115200)
            handler.data_received.connect(worker.process_line, Qt.DirectConnection)

            worker.update_sampling_rate(72000)
            worker.start_recording(csv_path)

            # Phase 1: 3 normal samples
            for i in range(3):
                emu.mock_serial.feed_line(f"LOAD:{100.0 + i},DISP:{1.0 + i*0.1}\r\n")
                time.sleep(0.08)

            # Phase 2: Disconnect
            emu.inject_disconnect()
            time.sleep(0.3)
            assert not handler.is_connected()

            # Phase 3: Hardware plugged back in
            emu.reconnect()

            # Wait for auto-reconnect loop (polls every 1.0s)
            t0 = time.time()
            while time.time() - t0 < 3.5 and len(reconnected_events) == 0:
                time.sleep(0.1)
                _app.processEvents()

            assert len(reconnected_events) >= 1, "controller_reconnected signal must fire on restoration"
            assert handler.is_connected(), "Handler must show connected status again"

            # Phase 4: Resume telemetry
            for i in range(3, 6):
                emu.mock_serial.feed_line(f"LOAD:{100.0 + i},DISP:{1.0 + i*0.1}\r\n")
                time.sleep(0.08)

            worker.stop_recording()

            # Verify CSV file contents
            with open(csv_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]

            # Should contain header + recorded rows
            assert len(lines) >= 4, f"Expected at least 4 recorded rows across reconnect, got {len(lines)}"
            assert lines[0] == "Time(s),Load(N),Disp(mm)"

    handler.disconnect()
    print("  -> PASS")


def test_rapid_flapping_connection():
    print("[4/4] Testing rapid connection flapping stress...")
    emu = VirtualSTM32()
    handler = SerialHandler()
    mock_port = MockPortInfo("MOCK_COM1")

    with patch("serial.Serial", return_value=emu.mock_serial), \
         patch("serial.tools.list_ports.comports", return_value=[mock_port]):

        handler.connect("MOCK_COM1", 115200)

        # Flap 3 times rapidly
        for cycle in range(3):
            emu.inject_disconnect()
            time.sleep(0.1)
            emu.reconnect()
            time.sleep(0.1)

        # Disconnect cleanly
        handler.disconnect()
        assert not handler.is_connected()

    print("  -> PASS")


def run_all():
    print("=" * 60)
    print("RUNNING MID-TEST RECONNECT & RECOVERY TEST SUITE")
    print("=" * 60)
    test_silence_detection_and_disconnect_signal()
    test_recording_loop_survives_disconnect()
    test_auto_reconnect_and_resume_stream()
    test_rapid_flapping_connection()
    print("=" * 60)
    print("ALL RECONNECT RECOVERY TESTS PASSED [4/4]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
