# logging_config.py
"""
Centralized logging configuration for Insitu GUI.
Provides rotating file logging with console output.
"""

import logging
from logging.handlers import RotatingFileHandler
import os
import sys


def get_app_dir():
    """
    Get application directory (where exe is located).
    Works for both development and PyInstaller frozen executables.
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return os.path.dirname(sys.executable)
    # Running as script
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller.
    PyInstaller 6+ places resources in _internal/ folder for onedir mode.
    """
    if getattr(sys, 'frozen', False):
        # When running as frozen, resources are in the same folder as the exe
        # or in the _internal folder (PyInstaller 6+ default).
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        
        # Check if we are in PyInstaller 6+ onedir mode where resources are in _internal
        internal_path = os.path.join(base_path, '_internal')
        if os.path.exists(internal_path):
            base_path = internal_path
    else:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def get_resource_dir():
    """
    Get resource directory (where bundled data files are located).
    For PyInstaller, data files are in _internal folder (in one-dir mode)
    or root (in one-file mode). resource_path() is more reliable.
    """
    return resource_path(".")


def get_user_config_dir():
    """
    Get writable directory for user configuration files.
    Uses APPDATA/Insitumicron Creep GUI/config/ for installed apps.
    """
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        cfg_dir = os.path.join(appdata, 'Insitumicron Creep GUI', 'config')
    else:
        cfg_dir = os.path.join(get_app_dir(), 'config')

    os.makedirs(cfg_dir, exist_ok=True)
    return cfg_dir


def setup_logging(log_level=logging.INFO):
    """
    Configure rotating file logger + console output.
    
    Log files are stored in %APPDATA%/Insitumicron Creep GUI/logs/ for installed apps,
    or ./logs/ for development.
    Rotation: 5MB per file, keep 5 backup files (25MB max total).
    
    Returns:
        Logger instance for the main application.
    """
    # Use APPDATA for installed apps (avoids Program Files permission issues)
    if getattr(sys, 'frozen', False):
        # Running as compiled executable - use APPDATA
        appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
        log_dir = os.path.join(appdata, 'Insitumicron Creep GUI', 'logs')
    else:
        # Running as script - use app directory
        log_dir = os.path.join(get_app_dir(), 'logs')
    
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "insitu_gui.log")
    
    # Rotating file handler (5MB × 5 files = 25MB max)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # Log everything to file
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    
    # Console handler (for development/debugging)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Only INFO+ to console
    console_handler.setFormatter(logging.Formatter(
        '%(levelname)s: %(message)s'
    ))
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove any existing handlers (prevents duplicates on reload)
    root_logger.handlers.clear()
    
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Create and return application logger
    logger = logging.getLogger("InsituGUI")
    logger.info("Logging initialized. Log file: %s", log_file)
    
    return logger


def get_logger(name):
    """
    Get a logger for a specific module.
    
    Usage:
        from logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Message")
    """
    return logging.getLogger(name)
