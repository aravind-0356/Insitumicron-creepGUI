# power_management.py
"""
Windows Power Management Utility.
Prevents system sleep and standby while creep testing/recording is active,
while permitting displays to turn off normally according to user power settings.
"""

import sys
import ctypes
import logging_config
logger = logging_config.get_logger(__name__)

# Windows API execution state flags
ES_CONTINUOUS        = 0x80000000
ES_SYSTEM_REQUIRED   = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


class WindowsPowerManager:
    """
    Manages Windows thread execution state to prevent the computer from
    entering sleep or standby during long-running creep tests.
    """
    _is_preventing_sleep = False

    @classmethod
    def prevent_sleep(cls) -> bool:
        """
        Forces the system to stay in the working state by setting
        ES_SYSTEM_REQUIRED (and ES_AWAYMODE_REQUIRED if supported).
        Display sleep/screensavers are unaffected and will still turn off.
        """
        if sys.platform != "win32":
            return False

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint32

            # Try Away Mode + System Required + Continuous
            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            prev = kernel32.SetThreadExecutionState(flags)
            if prev == 0:
                # Away mode might not be supported on this machine; fallback to System Required only
                flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                prev = kernel32.SetThreadExecutionState(flags)


            if prev != 0:
                cls._is_preventing_sleep = True
                logger.info("Windows sleep prevention ENABLED (system will stay awake during test).")
                return True
            else:
                logger.warning("SetThreadExecutionState returned 0 (failed to prevent sleep).")
                return False
        except Exception as e:
            logger.warning("Error enabling Windows sleep prevention: %s", e)
            return False

    @classmethod
    def allow_sleep(cls) -> bool:
        """
        Restores normal Windows power management by resetting execution state to ES_CONTINUOUS.
        """
        if sys.platform != "win32":
            return False

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint32

            prev = kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            cls._is_preventing_sleep = False
            logger.info("Windows sleep prevention DISABLED (normal power management restored).")
            return prev != 0
        except Exception as e:
            logger.warning("Error restoring Windows sleep state: %s", e)
            return False

    @classmethod
    def is_preventing_sleep(cls) -> bool:
        return cls._is_preventing_sleep
