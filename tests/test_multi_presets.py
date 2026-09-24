import os
import sys
import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QMessageBox
_app = QApplication.instance() or QApplication(sys.argv)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from controllers import PresetPositionController
from gui_test_views import UniaxialMotorControlPanel, PresetManagerDialog


@pytest.fixture(autouse=True)
def mock_qdialogs():
    with patch.object(QMessageBox, "information"), \
         patch.object(QMessageBox, "warning"), \
         patch.object(QMessageBox, "critical"), \
         patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
         patch.object(QMessageBox, "exec", return_value=0), \
         patch.object(QMessageBox, "exec_", return_value=0):
        yield


@pytest.fixture
def temp_config_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, "preset_positions.json")
        monkeypatch.setattr(PresetPositionController, "POSITIONS_CONFIG_FILE", cfg_path)
        yield cfg_path


def test_legacy_migration(temp_config_file):
    """Ensure older preset_positions.json with single 'load_position' migrates cleanly."""
    legacy_data = {
        "load_position": 125000,
        "unload_position": 25000
    }
    with open(temp_config_file, "w") as f:
        json.dump(legacy_data, f)

    mock_serial = MagicMock()
    mock_serial.is_connected.return_value = True
    panel = UniaxialMotorControlPanel(mock_serial)
    ctrl = PresetPositionController(mock_serial, panel, None)

    presets = ctrl.get_load_presets()
    assert len(presets) == 1
    assert presets[0]["name"] == "Default Load"
    assert presets[0]["pulses"] == 125000
    assert presets[0]["cm"] == 2.5
    assert presets[0]["mm"] == 25.0
    assert ctrl.get_selected_load_id() == "load_default"
    assert ctrl._unload_pos == 25000


def test_add_and_limit_presets(temp_config_file):
    """Test adding presets and enforcing the maximum 10-preset limit."""
    mock_serial = MagicMock()
    panel = UniaxialMotorControlPanel(mock_serial)
    ctrl = PresetPositionController(mock_serial, panel, None)

    assert len(ctrl.get_load_presets()) == 0

    # Add 10 presets
    for i in range(10):
        success, msg = ctrl.add_load_preset(f"Position {i+1}", (i + 1) * 10000)
        assert success is True

    assert len(ctrl.get_load_presets()) == 10

    # Attempting to add 11th preset must fail
    success, msg = ctrl.add_load_preset("Overflow Preset", 110000)
    assert success is False
    assert "Maximum limit of 10 presets reached" in msg
    assert len(ctrl.get_load_presets()) == 10


def test_delete_and_rename_preset(temp_config_file):
    """Test deleting and renaming presets."""
    mock_serial = MagicMock()
    panel = UniaxialMotorControlPanel(mock_serial)
    ctrl = PresetPositionController(mock_serial, panel, None)

    ctrl.add_load_preset("200 N Hold", 50000)
    ctrl.add_load_preset("500 N Hold", 100000)

    presets = ctrl.get_load_presets()
    assert len(presets) == 2
    id_1, id_2 = presets[0]["id"], presets[1]["id"]

    # Rename
    renamed = ctrl.rename_load_preset(id_1, "200 N Test")
    assert renamed is True
    assert ctrl.get_load_presets()[0]["name"] == "200 N Test"

    # Delete
    deleted = ctrl.delete_load_preset(id_2)
    assert deleted is True
    assert len(ctrl.get_load_presets()) == 1
    assert ctrl.get_selected_load_id() == id_1


def test_ui_dynamic_sync(temp_config_file):
    """Verify UniaxialMotorControlPanel UI updates dynamically with presets."""
    mock_serial = MagicMock()
    panel = UniaxialMotorControlPanel(mock_serial)
    ctrl = PresetPositionController(mock_serial, panel, None)

    # Initial empty state
    assert panel.combo_load_presets.count() == 1
    assert panel.combo_load_presets.currentText() == "No Positions Saved"
    assert panel.btn_go_load.isEnabled() is False

    # Add preset
    ctrl.add_load_preset("Tare Position", 25000)
    assert panel.combo_load_presets.count() == 1
    assert "Tare Position" in panel.combo_load_presets.currentText()
    assert panel.btn_go_load.isEnabled() is True
    assert panel.btn_go_load.text() == "Go to [Tare Position]"

    # Unload position
    ctrl.on_encoder_updated(5000)
    ctrl.set_unload_pos()
    assert "0.10 cm" in panel.lbl_unload_pos.text()
    assert panel.btn_go_unload.isEnabled() is True

    # Travel active state locks out configuration
    panel.set_travel_active_state(True)
    assert panel.btn_manage_presets.isEnabled() is False
    assert panel.combo_load_presets.isEnabled() is False
    assert panel.btn_set_unload.isEnabled() is False
    assert panel.btn_cancel_travel.isEnabled() is True

    panel.set_travel_active_state(False)
    assert panel.btn_manage_presets.isEnabled() is True
    assert panel.combo_load_presets.isEnabled() is True
    assert panel.btn_set_unload.isEnabled() is True
    assert panel.btn_cancel_travel.isEnabled() is False


def test_encoder_conversion_math():
    """Verify bidirectional pulses <-> cm conversions and boundary conditions."""
    # Lead screw pitch = 2.0 mm per rev; 1 rev = 10,000 pulses -> 10,000 pulses = 0.2 cm
    assert PresetPositionController.pulses_to_cm(0) == 0.0
    assert PresetPositionController.cm_to_pulses(0.0) == 0

    # 1 revolution (10,000 pulses = 2 mm = 0.2 cm)
    assert PresetPositionController.pulses_to_cm(10000) == 0.2
    assert PresetPositionController.cm_to_pulses(0.2) == 10000

    # 1 cm (50,000 pulses = 10 mm = 1.0 cm)
    assert PresetPositionController.pulses_to_cm(50000) == 1.0
    assert PresetPositionController.cm_to_pulses(1.0) == 50000

    # 80 cm crosshead length (4,000,000 pulses = 800 mm = 80.0 cm)
    assert PresetPositionController.pulses_to_cm(4000000) == 80.0
    assert PresetPositionController.cm_to_pulses(80.0) == 4000000

    # Negative positions (below zero reference)
    assert PresetPositionController.pulses_to_cm(-50000) == -1.0
    assert PresetPositionController.cm_to_pulses(-1.0) == -50000

    # Round-trip fidelity
    for test_pulse in [0, 500, 10000, 25000, 100000, 4000000, -100000]:
        cm = PresetPositionController.pulses_to_cm(test_pulse)
        assert PresetPositionController.cm_to_pulses(cm) == test_pulse
