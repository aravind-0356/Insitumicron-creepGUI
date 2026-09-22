# main.py
"""
Insitumicron Creep GUI - Main Entry Point
Copyright (c) 2025 Insitu Instruments. All rights reserved.
"""

import sys
import os

# --- Version Information ---
__version__ = "1.1"
__app_name__ = "Insitumicron Creep GUI"

# =========================================================
# DPI-AWARE SCALING
# =========================================================
# Strategy: Enable Qt's auto-scaling, but apply a counter-scale factor
# to compensate for Windows recommended scaling.
#
# Your laptop at 150% scaling: Qt would scale by 1.5x (too big!)
# Counter-scale by 0.67: 0.67 × 1.5 = 1.0 (neutral)
#
# Other monitor at 200%: 0.67 × 2.0 = 1.33 (properly scaled up)
# Other monitor at 100%: 0.67 × 1.0 = 0.67 (properly scaled down)
# =========================================================

# Enable Qt's automatic screen scale factor
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

# Counter-scale to neutralize 150% baseline (1.0 / 1.5 = 0.6667)
# This ensures your original 18px design looks the same at 150%
os.environ["QT_SCALE_FACTOR"] = "0.6667"

# Initialize logging BEFORE other imports
from logging_config import setup_logging, get_logger, resource_path
logger = setup_logging()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from gui import InsituMicronGUI

# --- Corporate Colors (centralized) ---
from constants import PRIMARY_COLOR, ACCENT_COLOR

from theme import GLOBAL_STYLESHEET

if __name__ == "__main__":
    logger.info("Starting %s v%s", __app_name__, __version__)
    
    app = QApplication(sys.argv)
    app.setStyleSheet(GLOBAL_STYLESHEET)

    # Set Default Font
    font = app.font()
    font.setFamily("Segoe UI")
    font.setPointSize(12)
    font.setStyleStrategy(font.StyleStrategy.PreferAntialias)
    font.setHintingPreference(font.HintingPreference.PreferNoHinting)
    app.setFont(font)

    # Set Application Icon
    icon_path = resource_path("app_icon.ico")
    if os.path.exists(icon_path):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))
    else:
        logger.warning("Application icon not found at: %s", icon_path)

    window = InsituMicronGUI()
    window.setWindowTitle(f"{__app_name__} v{__version__}")
    window.showMaximized()
    
    logger.info("Application started successfully")
    exit_code = app.exec()
    
    logger.info("Application exiting with code %d", exit_code)
    sys.exit(exit_code)
