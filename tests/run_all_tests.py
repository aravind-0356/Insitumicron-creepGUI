# tests/run_all_tests.py
"""
Master Test Runner for Insitumicron Creep GUI Test Suite.
Executes all test suites in sequence and prints a consolidated report.
"""

import sys
import os
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

from tests.test_virtual_stm32 import run_all as run_virtual_stm32
from tests.test_recovery_unit import run_all as run_recovery_unit
from tests.test_reconnect_recovery import run_all as run_reconnect_recovery
from tests.test_recovery_flow import run_all as run_recovery_flow
from tests.test_fault_injection import run_all as run_fault_injection
from tests.test_io_resilience import run_all as run_io_resilience
from tests.test_data_pipeline_soak import run_accelerated_soak_benchmark
from tests.test_analysis_dual_mode import run_all as run_analysis_dual_mode


def main():
    print("#" * 75)
    print("#  INSITUMICRON CREEP GUI - MASTER TEST SUITE EXECUTION")
    print("#  Simulating 1,000-Hour Endurance, Fault Injection & Crash Recovery")
    print("#" * 75)
    print()

    suites = [
        ("Virtual STM32 Hardware Emulator", run_virtual_stm32),
        ("Recovery Unit & File Continuity", run_recovery_unit),
        ("Mid-Test Reconnect & Auto-Resume", run_reconnect_recovery),
        ("GUI Startup Crash Recovery Flow", run_recovery_flow),
        ("Chaos Fuzzing & Safety E-Stop", run_fault_injection),
        ("File I/O & 20k-Row Excel Resilience", run_io_resilience),
        ("Accelerated 1,000-Hour Soak & Memory Leak Benchmark", lambda: run_accelerated_soak_benchmark(50_000)),
        ("Dual-Mode Analysis & Reporting Engine", run_analysis_dual_mode),
    ]

    results = []
    total_start = time.time()

    for name, runner in suites:
        print("\n" + ">" * 70)
        print(f"STARTING SUITE: {name}")
        print(">" * 70)
        suite_start = time.time()
        try:
            runner()
            duration = time.time() - suite_start
            results.append((name, "PASS", duration))
        except Exception as e:
            duration = time.time() - suite_start
            results.append((name, f"FAIL ({e})", duration))
            print(f"FAILED: {e}")

    total_duration = time.time() - total_start

    print("\n" + "=" * 75)
    print("                      FINAL TEST SCORECARD")
    print("=" * 75)
    print(f"{'Suite Name':<50} | {'Status':<10} | {'Time':<8}")
    print("-" * 75)

    all_passed = True
    for name, status, duration in results:
        status_str = "[PASS]" if status == "PASS" else "[FAIL]"
        if status != "PASS":
            all_passed = False
        print(f"{name:<50} | {status_str:<10} | {duration:6.2f}s")

    print("-" * 75)
    print(f"Total Test Execution Time: {total_duration:.2f} seconds")
    print(f"Overall Result: {'ALL TEST SUITES PASSED' if all_passed else 'SOME TESTS FAILED'}")
    print("=" * 75)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
