# machine_config.py
"""
Machine hardware limits for the Creep Testing Machine.

All physical hardware constraints live in config/machine_config.json.
This module loads that file and exposes typed, validated accessors.

Usage:
    from machine_config import MCfg
    max_rpm = MCfg.motor_max_rpm()
    capacity = MCfg.load_cell_capacity_n()
"""

import json
import os
from logging_config import get_logger, get_app_dir

logger = get_logger(__name__)

_CONFIG_PATH = os.path.join(get_app_dir(), "config", "machine_config.json")

# Hard-coded fallback defaults — used only if the JSON file cannot be read.
_DEFAULTS = {
    "motor": {
        "max_rpm": 1500,
        "min_rpm": -1500
    },
    "load_cell": {
        # Capacity in Newtons (e.g. 50 kg * 9.81 = 491 N)
        "capacity_n": 491.0,
        "min_n": 0.0
    },
    "displacement": {
        "upper_limit_mm": 25.0,
        "lower_limit_mm": -1.0
    },
    "crosshead": {
        "max_speed_mm_per_sec": 50.0,
        "default_return_speed_mm_per_sec": 5.0
    },
}

_cfg: dict | None = None


def _load() -> dict:
    """Load (or return cached) machine config."""
    global _cfg
    if _cfg is not None:
        return _cfg
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _cfg = json.load(f)
        logger.info("machine_config loaded from %s", _CONFIG_PATH)
    except Exception as e:
        logger.warning(
            "Cannot read machine_config.json (%s) - using built-in defaults.", e
        )
        _cfg = _DEFAULTS.copy()
    return _cfg


class MCfg:
    """Typed accessors for creep machine hardware limits."""

    # --- Motor ---
    @staticmethod
    def motor_max_rpm() -> int:
        return int(_load()["motor"]["max_rpm"])

    @staticmethod
    def motor_min_rpm() -> int:
        return int(_load()["motor"]["min_rpm"])

    # --- Load Cell (values in Newtons) ---
    @staticmethod
    def load_cell_capacity_n() -> float:
        return float(_load()["load_cell"]["capacity_n"])

    @staticmethod
    def load_cell_min_n() -> float:
        return float(_load()["load_cell"]["min_n"])

    # --- Displacement travel ---
    @staticmethod
    def disp_upper_limit_mm() -> float:
        return float(_load()["displacement"]["upper_limit_mm"])

    @staticmethod
    def disp_lower_limit_mm() -> float:
        return float(_load()["displacement"]["lower_limit_mm"])

    # --- Crosshead speed ---
    @staticmethod
    def max_crosshead_speed_mm_per_sec() -> float:
        return float(_load()["crosshead"]["max_speed_mm_per_sec"])

    @staticmethod
    def default_return_speed_mm_per_sec() -> float:
        return float(_load()["crosshead"]["default_return_speed_mm_per_sec"])
