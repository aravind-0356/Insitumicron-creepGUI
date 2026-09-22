# tests/test_fault_injection.py
"""
Chaos & Fault Injection Resilience Test Suite.
Validates:
1. DataWorker regex parser survival during bursts of 10,000 malformed/corrupted packets.
2. Handling of NaN, infinities, empty lines, partial packets, and Unicode noise.
3. Hardware E-stop KILL switch and OVERLOAD detection and motor safety shutdown.
4. Instant recovery of valid sensor parsing immediately after noisy bursts.
"""

import sys
import os
import random
import string
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from serial_handler import SerialHandler
from data_worker import DataWorker


def generate_garbage_packet():
    """Generates various realistic electrical noise artifacts and malformed strings."""
    corruptions = [
        "",  # Empty string
        "   \t\r\n",  # Whitespace
        "LOAD:",  # Truncated load
        "LOAD:12.345",  # Missing disp
        "DISP:1.2345",  # Missing load
        "LOAD:12.345,DISP:",  # Truncated disp
        "LOAD:NaN,DISP:1.234",  # NaN float
        "LOAD:Infinity,DISP:-Infinity",  # Infinite values
        "LOAD:99999999999999999999,DISP:0.0",  # Overflow
        "LOAD:--50.0,DISP:++1.2",  # Bad signs
        "LOAD:12.3.4,DISP:5.6.7",  # Multiple decimals
        "ERR:COMM_FAIL",  # Firmware error string
        "E:INVALID_ENCODER",  # Bad encoder value
        "LOAD:💡⚡,DISP:🔥",  # Unicode corruption
        "".join(random.choices(string.printable, k=random.randint(5, 50))),  # Random noise
    ]
    return random.choice(corruptions)


def test_malformed_packet_fuzzing(num_packets: int = 10_000):
    print(f"[1/3] Injecting {num_packets:,} malformed/noisy packets into DataWorker...")
    mock_serial = MagicMock(spec=SerialHandler)
    worker = DataWorker(mock_serial)

    exceptions_caught = 0

    for _ in range(num_packets):
        packet = generate_garbage_packet()
        try:
            worker.process_line(packet)
        except Exception as e:
            exceptions_caught += 1
            print(f"FAILED on packet: {repr(packet)} - Error: {e}")

    assert exceptions_caught == 0, f"Parser threw {exceptions_caught} unhandled exceptions during fuzzing!"
    print(f"  -> PASS (0 unhandled exceptions across {num_packets:,} corrupted packets)")


def test_recovery_after_fuzzing():
    print("[2/3] Verifying immediate sensor recovery after noise burst...")
    mock_serial = MagicMock(spec=SerialHandler)
    worker = DataWorker(mock_serial)

    # Burst of 500 garbage lines
    for _ in range(500):
        worker.process_line(generate_garbage_packet())

    # Send valid calibrated data (3 samples to settle the 3-sample median filter window)
    for _ in range(3):
        worker.process_line("LOAD:125.500,DISP:3.4560\r\n")

    # Verify worker correctly parsed the valid line
    assert abs(worker._load_filtered - 125.500) < 0.001, f"Expected load 125.500, got {worker._load_filtered}"
    assert abs(worker._disp_filtered - 3.4560) < 0.0001, f"Expected disp 3.4560, got {worker._disp_filtered}"
    print("  -> PASS")


def test_estop_kill_and_overload_safety():
    print("[3/3] Testing hardware E-stop KILL switch and OVERLOAD detection...")
    mock_serial = MagicMock(spec=SerialHandler)
    worker = DataWorker(mock_serial)

    overload_signals = []
    worker.overload_detected.connect(lambda msg: overload_signals.append(msg))

    # 1. Test KILL switch
    worker.process_line("KILL_SWITCH_ACTIVE\r\n")
    assert len(overload_signals) == 1, "overload_detected must fire on KILL"
    assert "KILL" in overload_signals[0]
    mock_serial.send_cmd.assert_called_with("STOP")

    # 2. Reset latch with valid data
    worker.process_line("LOAD:10.0,DISP:0.1\r\n")
    assert not worker._overload_fired

    # 3. Test OVERLOAD signal
    mock_serial.reset_mock()
    worker.process_line("OVERLOAD_PEAK_DETECTED\r\n")
    assert len(overload_signals) == 2, "overload_detected must fire on OVERLOAD"
    assert "OVERLOAD" in overload_signals[1]
    mock_serial.send_cmd.assert_called_with("STOP")

    print("  -> PASS")


def run_all():
    print("=" * 60)
    print("RUNNING CHAOS & FAULT INJECTION TEST SUITE")
    print("=" * 60)
    test_malformed_packet_fuzzing(10_000)
    test_recovery_after_fuzzing()
    test_estop_kill_and_overload_safety()
    print("=" * 60)
    print("ALL FAULT INJECTION TESTS PASSED [3/3]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
