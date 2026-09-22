# tests/test_io_resilience.py
"""
Test Suite for File I/O Resilience & Long-Run Excel Export Performance.
Validates:
1. Mid-test disk write failure / disk full (OSError) error handling.
2. Clean worker shutdown upon I/O error without crashing the process.
3. Excel conversion benchmark on a simulated 1,000-hour CSV file (20,000 data rows).
"""

import sys
import os
import time
import tempfile
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from serial_handler import SerialHandler
from data_worker import DataWorker, CSVWriter
from controllers import ExcelExportWorker


def test_disk_write_error_handling():
    print("[1/2] Testing mid-test disk write error / disk full resilience...")
    mock_serial = MagicMock(spec=SerialHandler)
    worker = DataWorker(mock_serial)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "io_error_test.csv")
        mock_serial.is_connected.return_value = True
        worker._last_data_received_time = time.time()

        worker.csv_writer = CSVWriter()
        worker.csv_writer.open_file(csv_path, ["Time(s)", "Load(N)", "Disp(mm)"])
        worker._is_recording = True

        recording_errors = []
        worker.recording_error.connect(lambda msg: recording_errors.append(msg))

        try:
            # Mock write_row to simulate OSError / disk full
            with patch.object(worker.csv_writer, "write_row", return_value=False):
                res = worker.record_snapshot()
                assert res is False, "record_snapshot must return False on write failure"

            assert len(recording_errors) == 1, "recording_error signal must fire on write failure"
            assert not worker.is_recording(), "Worker must halt recording on write failure"
        finally:
            if worker.csv_writer:
                worker.csv_writer.close_file()
                worker.csv_writer = None
            time.sleep(0.05)
    print("  -> PASS")


def test_large_excel_export_benchmark():
    print("[2/2] Testing 1,000-hour CSV to Excel export (20,000 rows)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "test_result(csv format).csv")
        sample_id = "BENCHMARK_SPECIMEN_1000H"

        # Generate a realistic 20,000-row CSV file
        print("  Generating 20,000-row CSV data file...")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Time(s),Load(N),Disp(mm)\n")
            for i in range(20_000):
                f.write(f"{i * 180.0:.3f},{120.0 + (i % 10)*0.1:.3f},{i * 0.0005:.4f}\n")

        # Run ExcelExportWorker directly
        worker = ExcelExportWorker(save_dir=tmpdir, sample_id=sample_id)

        finished_called = False
        error_called = None

        worker.finished.connect(lambda: globals().update(finished_called=True))
        worker.error.connect(lambda msg: globals().update(error_called=msg))

        t0 = time.perf_counter()
        worker.run()
        elapsed = time.perf_counter() - t0

        xlsx_path = os.path.join(tmpdir, "test_results.xlsx")
        assert os.path.exists(xlsx_path), "test_results.xlsx must be created"
        file_size_kb = os.path.getsize(xlsx_path) / 1024.0

        print(f"  Export Duration : {elapsed:.2f} seconds (Target: < 10.0s)")
        print(f"  Output Size     : {file_size_kb:.1f} KB")

        assert elapsed < 10.0, f"Excel export was too slow: {elapsed:.2f}s"
        assert file_size_kb > 100.0, "Exported file seems empty or corrupted"

    print("  -> PASS")


def run_all():
    print("=" * 60)
    print("RUNNING FILE I/O & EXCEL RESILIENCE TEST SUITE")
    print("=" * 60)
    test_disk_write_error_handling()
    test_large_excel_export_benchmark()
    print("=" * 60)
    print("ALL FILE I/O RESILIENCE TESTS PASSED [2/2]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
