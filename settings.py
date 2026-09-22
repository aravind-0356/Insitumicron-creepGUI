# settings.py
"""
Persistent settings management for Insitu GUI.
Stores user preferences in a JSON file in the application directory.
"""

import json
import os
from logging_config import get_app_dir, get_logger

logger = get_logger(__name__)

SETTINGS_FILE = os.path.join(get_app_dir(), "settings.json")

# Default settings — user preferences only.
# Hardware limits (max RPM, load cell capacity, travel) live in machine_config.json.
DEFAULT_SETTINGS = {
    # General
    "sampling_rate": 4,
    "break_threshold_n": 1000.0,
    "last_output_folder": "",
    "last_sample_id": "Default",
    "save_images": True,
    "serial_baud_rate": 115200,
    "window_maximized": True,

    # PID — Force Control
    "pid_force_kp": 0.5,
    "pid_force_ki": 0.05,
    "pid_force_kd": 0.01,

    # PID — Displacement Control
    "pid_disp_kp": 1.0,
    "pid_disp_ki": 0.1,
    "pid_disp_kd": 0.005,

    # Control loop timing
    "pid_update_rate_hz": 50,
    "serial_data_rate_hz": 50,

    # Force Control defaults (user preference, not hardware limits)
    "fc_default_return_rate_n_per_min": 6000.0,

    # Methods
    "methods_directory": "./methods",
}
_cache: dict | None = None


def load_settings():
    """
    Load settings from JSON file.
    
    Returns merged settings (saved + defaults for any missing keys).
    This allows new settings to be added in updates without breaking existing installs.
    """
    global _cache
    if _cache is not None:
        return _cache.copy()

    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                # Merge with defaults (saved values override defaults)
                merged = {**DEFAULT_SETTINGS, **saved}
                logger.debug("Settings loaded from %s", SETTINGS_FILE)
                _cache = merged.copy()
                return merged
    except Exception as e:
        logger.warning("Failed to load settings: %s. Using defaults.", e)
    
    _cache = DEFAULT_SETTINGS.copy()
    return _cache.copy()


def save_settings(settings):
    """
    Save settings to JSON file.
    
    Only saves keys that differ from defaults to keep the file clean.
    """
    global _cache
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        logger.debug("Settings saved to %s", SETTINGS_FILE)
        _cache = settings.copy()
        return True
    except Exception as e:
        logger.error("Failed to save settings: %s", e)
        return False


def get_setting(key, default=None):
    """Get a single setting value."""
    settings = load_settings()
    return settings.get(key, default)


def set_setting(key, value):
    """Set a single setting value and save."""
    settings = load_settings()
    settings[key] = value
    return save_settings(settings)
