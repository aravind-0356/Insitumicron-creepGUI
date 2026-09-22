# tests/test_virtual_stm32.py
"""
Unit verification for VirtualSTM32 Hardware Emulator.
Validates telemetry syntax, command processing, and fault injection mechanisms.
"""

import sys
import os
import time

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.mocks.virtual_stm32 import VirtualSTM32, MockSerial


def test_command_processing():
    print("[1/5] Testing command handling (START, STOP, VELSET)...")
    emu = VirtualSTM32()
    assert not emu.motor_running

    emu.handle_command("START")
    assert emu.motor_running, "Motor should be running after START"

    emu.handle_command("VELSET:500")
    assert emu.current_velocity_rpm == 500, "VELSET:500 should set RPM to 500"

    emu.handle_command("STOP")
    assert not emu.motor_running, "Motor should stop after STOP"
    assert emu.current_velocity_rpm == 0, "RPM should reset to 0 after STOP"
    print("  -> PASS")


def test_telemetry_format():
    print("[2/5] Testing telemetry syntax...")
    emu = VirtualSTM32()
    emu.step_physics(dt=0.1)
    packet = emu.generate_telemetry_packet()
    assert packet.startswith("LOAD:"), f"Packet must start with LOAD:, got: {packet}"
    assert ",DISP:" in packet, f"Packet must contain ,DISP:, got: {packet}"
    assert packet.endswith("\r\n"), f"Packet must terminate with \\r\\n, got: {packet}"

    enc_packet = emu.generate_encoder_packet()
    assert enc_packet.startswith("E:"), f"Encoder packet must start with E:, got: {enc_packet}"
    print("  -> PASS")


def test_mock_serial_transport():
    print("[3/5] Testing MockSerial communication...")
    emu = VirtualSTM32()
    ser = emu.mock_serial
    assert ser.is_open

    # Test bidirectional transmission
    ser.write(b"VEL:1200\nSTART\n")
    assert emu.motor_running
    assert emu.current_velocity_rpm == 1200

    # Test feed line and readline
    ser.feed_line("LOAD:100.500,DISP:2.3456\r\n")
    line = ser.readline().decode("utf-8").strip()
    assert line == "LOAD:100.500,DISP:2.3456"
    print("  -> PASS")


def test_fault_injection():
    print("[4/5] Testing fault injection hooks...")
    emu = VirtualSTM32()
    ser = emu.mock_serial

    # Inject disconnect
    emu.inject_disconnect()
    disconnected_raised = False
    try:
        ser.readline()
    except Exception as e:
        disconnected_raised = True
        assert "disconnected" in str(e).lower()
    assert disconnected_raised, "MockSerial must raise SerialException when disconnected"

    # Reconnect
    emu.reconnect()
    assert ser.is_open
    ser.feed_line("LOAD:50.0,DISP:0.0\r\n")
    line = ser.readline().decode("utf-8").strip()
    assert line == "LOAD:50.0,DISP:0.0"

    # Inject Kill switch
    emu.inject_kill_switch()
    assert emu.kill_triggered
    print("  -> PASS")


def test_real_time_streaming():
    print("[5/5] Testing live streaming loop (0.2s sample)...")
    emu = VirtualSTM32()
    emu.start()
    time.sleep(0.2)

    lines_received = []
    start_t = time.time()
    while time.time() - start_t < 0.25:
        line = emu.mock_serial.readline()
        if line:
            lines_received.append(line.decode("utf-8", errors="ignore").strip())

    emu.stop()
    assert len(lines_received) >= 2, f"Expected at least 2 telemetry lines, got: {len(lines_received)}"
    print(f"  -> PASS (Received {len(lines_received)} packets)")


def run_all():
    print("=" * 60)
    print("RUNNING VIRTUAL STM32 EMULATOR TEST SUITE")
    print("=" * 60)
    test_command_processing()
    test_telemetry_format()
    test_mock_serial_transport()
    test_fault_injection()
    test_real_time_streaming()
    print("=" * 60)
    print("ALL VIRTUAL STM32 TESTS PASSED [5/5]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
