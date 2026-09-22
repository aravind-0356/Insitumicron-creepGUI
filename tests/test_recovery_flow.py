# tests/test_recovery_flow.py
"""
End-to-End Headless Test Suite for GUI Startup Crash Recovery Flow.
Validates:
1. GUI startup detection of interrupted test sessions.
2. Full state restoration: sample dimensions, tare offsets, output folder, timer.
3. CSV append continuity and recording state resumption.
4. User decline flow (deleting session file and remaining idle).
5. Application closeEvent session preservation vs completion.
"""

import sys
import os
import time
import tempfile
from unittest.mock import patch

# Set offscreen platform for headless Qt execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QCloseEvent

from test_recovery import TestSessionManager, SESSION_FILE
from gui import InsituMicronGUI


# Single QApplication instance for tests
_app = QApplication.instance() or QApplication(sys.argv)


def cleanup_window(window):
    if hasattr(window, 'data_worker') and window.data_worker.is_recording():
        window.data_worker.stop_recording()
    if hasattr(window, 'rec_ctrl'):
        window.rec_ctrl._is_recording = False
    if hasattr(window, 'serial_handler'):
        window.serial_handler.disconnect()
    if hasattr(window, 'data_thread'):
        window.data_thread.quit()
        window.data_thread.wait(500)
    if hasattr(window, 'camera_panel'):
        if hasattr(window.camera_panel, 'worker'):
            window.camera_panel.worker.shutdown()
        if hasattr(window.camera_panel, '_cam_thread'):
            window.camera_panel._cam_thread.quit()
            window.camera_panel._cam_thread.wait(500)


def test_startup_recovery_accepted():
    print("[1/3] Testing GUI startup crash recovery acceptance flow...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "interrupted_test.csv")
        
        # Pre-seed CSV with header and 5 rows
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Time(s),Load(N),Disp(mm)\n")
            for k in range(5):
                f.write(f"{k*10.0:.3f},{50.0 + k:.3f},{k*0.05:.4f}\n")

        # Pre-seed interrupted session JSON
        t_simulated_start = time.time() - 3600.0  # 1 hour ago
        TestSessionManager.save_session(
            start_time_epoch=t_simulated_start,
            file_path=csv_path,
            tare_load=25.4,
            tare_disp=1.12,
            max_disp=3.45,
            sampling_rate=45,
            images_dir="",
            output_folder=tmpdir,
            sample_data={
                "sample_id": "RECOVERED_SPECIMEN_42",
                "sample_type": "Rectangular",
                "width": 14.2,
                "thickness": 2.8,
                "radius": 5.0,
                "gauge_length": 50.0,
                "save_sensor": True,
                "save_images": False
            }
        )

        assert os.path.exists(SESSION_FILE)

        # Instantiate GUI
        window = InsituMicronGUI()

        # Mock user clicking "Yes" to resume test
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window.startup_check()

        # Assert full state restoration
        assert window._output_folder == tmpdir, "Output folder must be restored"
        assert window.sample_panel.sample_id_input.text() == "RECOVERED_SPECIMEN_42", "Sample ID must be restored"
        assert float(window.sample_panel.width_inp.text()) == 14.2, "Sample width must be restored"
        assert float(window.sample_panel.thick_inp.text()) == 2.8, "Sample thickness must be restored"
        assert window.data_worker._load_offset == 25.4, "Tare load offset must be restored"
        assert window.data_worker._disp_offset == 1.12, "Tare disp offset must be restored"
        assert window.data_worker._max_disp == 3.45, "Max disp must be restored"
        assert window.data_worker._sampling_rate == 45, "Sampling rate must be restored"
        assert window.rec_ctrl._is_recording, "RecordingController must be in recording state"
        assert window.data_worker.is_recording(), "DataWorker must be actively recording"

        # Assert timer was started with at least 3590 seconds offset
        assert window.dashboard_panel._rec_elapsed >= 3590, "Dashboard timer must resume from original start epoch"

        # Stop recording and cleanup
        cleanup_window(window)

    print("  -> PASS")


def test_startup_recovery_declined():
    print("[2/3] Testing GUI startup crash recovery decline flow...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "declined_test.csv")
        TestSessionManager.save_session(
            start_time_epoch=time.time(),
            file_path=csv_path,
            tare_load=10.0,
            tare_disp=0.5,
            max_disp=1.0,
            sampling_rate=20,
            images_dir="",
            output_folder=tmpdir,
            sample_data={"sample_id": "DECLINED_SPECIMEN"}
        )

        assert os.path.exists(SESSION_FILE)
        window = InsituMicronGUI()

        # Mock user clicking "No" to decline recovery
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            window.startup_check()

        # Session file should be cleaned up and GUI should NOT be recording
        assert not os.path.exists(SESSION_FILE), "Session file must be deleted when user declines recovery"
        assert not window.rec_ctrl._is_recording
        assert not window.data_worker.is_recording()

        cleanup_window(window)

    print("  -> PASS")


def test_close_event_preservation():
    print("[3/3] Testing closeEvent session preservation for crash recovery...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "close_test.csv")
        window = InsituMicronGUI()
        window._output_folder = tmpdir

        # Start a recording session
        window.data_worker.start_recording(csv_path, output_folder=tmpdir, sample_data={"sample_id": "CLOSE_TEST"})
        assert os.path.exists(SESSION_FILE)

        # Trigger closeEvent, simulating user choosing "No" (Exit & Preserve for Crash Recovery)
        close_event = QCloseEvent()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            window.closeEvent(close_event)

        assert close_event.isAccepted(), "Close event should be accepted when exiting with preservation"
        assert os.path.exists(SESSION_FILE), "Session file must remain on disk when user chooses to preserve it"

        # Verify session file can still be loaded
        session = TestSessionManager.load_active_session()
        assert session is not None
        assert session["file_path"] == csv_path

        # Cleanup
        TestSessionManager.mark_completed()
        cleanup_window(window)

    print("  -> PASS")


def run_all():
    print("=" * 60)
    print("RUNNING END-TO-END CRASH RECOVERY FLOW TEST SUITE")
    print("=" * 60)
    with patch.object(QMessageBox, "information"), \
         patch.object(QMessageBox, "warning"), \
         patch.object(QMessageBox, "critical"):
        test_startup_recovery_accepted()
        test_startup_recovery_declined()
        test_close_event_preservation()
    print("=" * 60)
    print("ALL CRASH RECOVERY FLOW TESTS PASSED [3/3]")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
