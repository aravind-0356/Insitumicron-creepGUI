# tests/test_power_management.py
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from power_management import WindowsPowerManager


def test_windows_power_manager_lifecycle():
    # Test prevent sleep
    success_prevent = WindowsPowerManager.prevent_sleep()
    if sys.platform == 'win32':
        assert success_prevent is True
        assert WindowsPowerManager.is_preventing_sleep() is True

    # Test allow sleep
    success_allow = WindowsPowerManager.allow_sleep()
    if sys.platform == 'win32':
        assert success_allow is True
        assert WindowsPowerManager.is_preventing_sleep() is False


def test_recording_controller_sleep_management(qapp):
    from PySide6.QtWidgets import QWidget
    from controllers import RecordingController

    # Mock dependencies
    mock_mw = QWidget()
    mock_mw.serial_handler = MagicMock()
    mock_mw.serial_handler.is_connected.return_value = True
    mock_mw._save_sensor_data = True
    mock_mw._save_images = False
    mock_mw._output_folder = os.path.abspath('.')
    mock_mw.sample_panel = MagicMock()
    mock_mw.sample_panel.get_sample_data.return_value = {}
    mock_mw.sensor_panel = MagicMock()
    mock_mw.sensor_panel.get_sampling_rate.return_value = 20
    mock_mw.dashboard_panel = MagicMock()

    mock_dw = MagicMock()
    mock_cam = MagicMock()

    rc = RecordingController(mock_mw, mock_dw, mock_cam)

    with patch.object(WindowsPowerManager, 'prevent_sleep') as mock_prevent,          patch.object(WindowsPowerManager, 'allow_sleep') as mock_allow,          patch.object(rc, '_run_excel_export'):

        # Start recording
        rc.start_record()
        assert rc._is_recording is True
        mock_prevent.assert_called_once()

        # Stop recording
        rc.stop_record()
        assert rc._is_recording is False
        mock_allow.assert_called_once()
