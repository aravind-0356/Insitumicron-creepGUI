# tests/test_recovery_unit.py
"""
Unit Test Suite for Test Data Recovery & File Continuity.
Validates:
1. TestSessionManager session persistence, schema, and corrupted JSON resilience.
2. CSVWriter open vs append mode, header preservation, and uninterrupted data continuity.
"""

import sys
import os
import time
import json
import tempfile
import csv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from test_recovery import TestSessionManager, SESSION_FILE
from data_worker import CSVWriter


def test_session_manager_lifecycle():
    print("[1/5] Testing TestSessionManager save, load, and completion lifecycle...")
    # Clean any pre-existing session
    TestSessionManager.mark_completed()
    assert TestSessionManager.load_active_session() is None

    # 1. Save session
    t_start = time.time()
    sample_info = {
        "sample_id": "TEST_SPECIMEN_001",
        "sample_type": "Rectangular",
        "width": 12.5,
        "thickness": 3.2,
        "gauge_length": 50.0
    }
    dummy_csv = os.path.join(tempfile.gettempdir(), "test_session_dummy.csv")

    TestSessionManager.save_session(
        start_time_epoch=t_start,
        file_path=dummy_csv,
        tare_load=15.2,
        tare_disp=0.34,
        max_disp=2.1,
        sampling_rate=30,
        images_dir="/dummy/images",
        output_folder="/dummy/output",
        sample_data=sample_info
    )

    assert os.path.exists(SESSION_FILE), f"Session file must exist at {SESSION_FILE}"

    # 2. Load and verify contents
    loaded = TestSessionManager.load_active_session()
    assert loaded is not None, "load_active_session() should return active session dict"
    assert loaded["status"] == "RUNNING"
    assert loaded["start_time_epoch"] == t_start
    assert loaded["file_path"] == dummy_csv
    assert loaded["tare_load"] == 15.2
    assert loaded["tare_disp"] == 0.34
    assert loaded["max_disp"] == 2.1
    assert loaded["sampling_rate"] == 30
    assert loaded["sample_data"]["sample_id"] == "TEST_SPECIMEN_001"
    assert loaded["sample_data"]["width"] == 12.5

    # 3. Mark completed
    TestSessionManager.mark_completed()
    assert not os.path.exists(SESSION_FILE), "Session file should be removed on mark_completed()"
    assert TestSessionManager.load_active_session() is None
    print("  -> PASS")


def test_corrupted_session_file_resilience():
    print("[2/5] Testing corrupted session file fault tolerance...")
    # Simulate partial write / power cut right in the middle of writing JSON
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        f.write('{"status": "RUNNING", "start_time_epoch": 12345678, "file_path": "incomp')
        f.flush()

    # Must return None safely without raising JSONDecodeError
    res = TestSessionManager.load_active_session()
    assert res is None, "Corrupted JSON must be caught and return None safely"

    # Simulate random binary garbage
    with open(SESSION_FILE, "wb") as f:
        f.write(b"\x00\xff\xfe\x12\x34\x99\xaa")
        f.flush()

    res = TestSessionManager.load_active_session()
    assert res is None, "Binary garbage must be caught and return None safely"

    # Cleanup
    TestSessionManager.mark_completed()
    print("  -> PASS")


def test_csv_writer_open_and_write():
    print("[3/5] Testing CSVWriter standard creation and row writing...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "test.csv")
        writer = CSVWriter()

        headers = ["Time(s)", "Load(N)", "Disp(mm)"]
        assert writer.open_file(csv_path, headers)
        assert writer.is_open

        # Write 5 rows
        for i in range(5):
            writer.write_row([f"{i*0.5:.3f}", f"{50.0 + i:.3f}", f"{i*0.01:.4f}"])

        writer.close_file()
        assert not writer.is_open

        # Verify file on disk
        with open(csv_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 6, f"Expected 1 header + 5 rows (6 total), got {len(lines)}"
        assert lines[0].strip() == "Time(s),Load(N),Disp(mm)"
        assert lines[1].strip() == "0.000,50.000,0.0000"
        assert lines[5].strip() == "2.000,54.000,0.0400"
    print("  -> PASS")


def test_csv_writer_append_mode_continuity():
    print("[4/5] Testing CSV append mode (crash resumption continuity)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "interrupted_test.csv")

        # Step 1: Pre-crash test run (writes header + 10 rows)
        writer1 = CSVWriter()
        headers = ["Time(s)", "Load(N)", "Disp(mm)"]
        writer1.open_file(csv_path, headers)
        for i in range(10):
            writer1.write_row([f"{i * 10.0:.3f}", f"{100.0 + i:.3f}", f"{i * 0.1:.4f}"])
        # Abrupt close (simulating power off or crash)
        writer1.close_file()

        # Step 2: Post-crash resumption with append_file()
        writer2 = CSVWriter()
        assert writer2.append_file(csv_path), "append_file() must succeed on existing CSV"
        assert writer2.is_open

        # Write 10 additional rows continuing from timestamp 100.0s
        for i in range(10, 20):
            writer2.write_row([f"{i * 10.0:.3f}", f"{100.0 + i:.3f}", f"{i * 0.1:.4f}"])
        writer2.close_file()

        # Step 3: Verify the combined file
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        # Check total rows: 1 header + 20 data rows = 21 rows
        assert len(reader) == 21, f"Expected 21 total rows, got {len(reader)}"

        # Verify header only appears ONCE at index 0
        assert reader[0] == headers
        for row_idx in range(1, len(reader)):
            assert reader[row_idx] != headers, f"Duplicate header found at row {row_idx}!"

        # Verify timestamp continuity
        timestamps = [float(row[0]) for row in reader[1:]]
        assert timestamps[0] == 0.0
        assert timestamps[9] == 90.0
        assert timestamps[10] == 100.0   # Resumed row
        assert timestamps[19] == 190.0  # Final row

        # Monotonic assertion
        for k in range(len(timestamps) - 1):
            assert timestamps[k + 1] > timestamps[k], f"Timestamps must strictly increase: {timestamps[k]} vs {timestamps[k+1]}"
    print("  -> PASS")


def test_csv_writer_io_error_handling():
    print("[5/5] Testing CSVWriter resilience to write failure...")
    writer = CSVWriter()
    # Write to an unopened file
    assert not writer.write_row(["1.0", "50.0", "0.1"]), "Writing to closed file must return False cleanly"
    print("  -> PASS")


def run_all():
    print("=" * 60)
    print("RUNNING RECOVERY UNIT & FILE CONTINUITY TEST SUITE")
    print("=" * 60)
    test_session_manager_lifecycle()
    test_corrupted_session_file_resilience()
    test_csv_writer_open_and_write()
    test_csv_writer_append_mode_continuity()
    test_csv_writer_io_error_handling()
    print("=" * 60)
    print("ALL RECOVERY UNIT TESTS PASSED [5/5]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
