import time
from dataclasses import dataclass, field
from typing import List, Dict, Callable, Any, Optional
from logging_config import get_logger

logger = get_logger(__name__)

# --- Exit Conditions ---

class ExitCondition:
    """Base class for test phase exit conditions."""
    def __init__(self):
        self.is_met = False
        
    def reset(self):
        self.is_met = False

    def check(self, current_time: float, current_load: float, current_disp: float) -> bool:
        raise NotImplementedError

class ForceReached(ExitCondition):
    def __init__(self, target: float, tolerance: float, persist_samples: int = 5):
        super().__init__()
        self.target = target
        self.tolerance = tolerance
        self.persist_samples = persist_samples
        self.count = 0
        
    def reset(self):
        super().reset()
        self.count = 0
        
    def check(self, current_time: float, current_load: float, current_disp: float) -> bool:
        if abs(current_load - self.target) <= self.tolerance:
            self.count += 1
        else:
            self.count = 0
            
        if self.count >= self.persist_samples:
            self.is_met = True
        return self.is_met

class DisplacementReached(ExitCondition):
    def __init__(self, target: float, tolerance: float):
        super().__init__()
        self.target = target
        self.tolerance = tolerance
        
    def check(self, current_time: float, current_load: float, current_disp: float) -> bool:
        if abs(current_disp - self.target) <= self.tolerance:
            self.is_met = True
        return self.is_met

class TimeElapsed(ExitCondition):
    def __init__(self, duration_s: float):
        super().__init__()
        self.duration_s = duration_s
        self.start_time = None
        
    def reset(self):
        super().reset()
        self.start_time = None
        
    def check(self, current_time: float, current_load: float, current_disp: float) -> bool:
        if self.start_time is None:
            self.start_time = current_time
            return False
            
        if (current_time - self.start_time) >= self.duration_s:
            self.is_met = True
        return self.is_met

class BreakDetected(ExitCondition):
    def __init__(self, drop_threshold_n: float, persist_samples: int = 5):
        super().__init__()
        self.drop_threshold_n = drop_threshold_n
        self.persist_samples = persist_samples
        self.peak_load = 0.0
        self.count = 0
        
    def reset(self):
        super().reset()
        self.peak_load = 0.0
        self.count = 0
        
    def check(self, current_time: float, current_load: float, current_disp: float) -> bool:
        mag = abs(current_load)
        self.peak_load = max(self.peak_load, mag)
        if (self.peak_load - mag) > self.drop_threshold_n:
            self.count += 1
        else:
            self.count = 0
            
        if self.count >= self.persist_samples:
            self.is_met = True
        return self.is_met

# --- Data Classes ---

@dataclass
class TestPhase:
    name: str
    control_mode: str  # "force" or "displacement"
    target: float
    rate: float
    hold_time_s: float
    auto_zero_on_complete: bool
    exit_condition: ExitCondition

@dataclass
class TestMethod:
    name: str
    version: int = 1
    author: str = ""
    created: str = ""
    gauge_length_mm: float = 50.0
    global_limits: Dict[str, float] = field(default_factory=dict)
    phases: List[TestPhase] = field(default_factory=list)

# --- FSM Engine ---

class EngineState:
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PHASE_COMPLETE = "PHASE_COMPLETE"
    TEST_COMPLETE = "TEST_COMPLETE"
    ABORTED = "ABORTED"

class TestEngine:
    """
    Finite State Machine for sequencing test phases.
    Ticked once per incoming serial frame by DataWorker.
    """
    def __init__(self, pid_controller, ramp_generator, serial_handler):
        self.pid = pid_controller
        self.ramp = ramp_generator
        self.serial = serial_handler
        
        self.method: Optional[TestMethod] = None
        self.state = EngineState.IDLE
        self.current_phase_idx = 0
        self.current_phase: Optional[TestPhase] = None
        
        # Callbacks (set by DataWorker)
        self.on_phase_start: Optional[Callable] = None
        self.on_phase_complete: Optional[Callable] = None
        self.on_test_complete: Optional[Callable] = None
        self.on_abort: Optional[Callable] = None

    def load_method(self, method: TestMethod):
        self.method = method
        self.state = EngineState.IDLE
        self.current_phase_idx = 0
        
    def start_test(self, current_load: float, current_disp: float):
        if not self.method or not self.method.phases:
            logger.error("Cannot start test: no method loaded")
            return
            
        self.state = EngineState.RUNNING
        self.current_phase_idx = 0
        self._start_current_phase(current_load, current_disp)
        
    def _start_current_phase(self, current_load: float, current_disp: float):
        self.current_phase = self.method.phases[self.current_phase_idx]
        logger.info(f"Starting phase: {self.current_phase.name}")
        
        self.current_phase.exit_condition.reset()
        self.pid.reset()
        
        # Configure PID gains based on mode (DataWorker handles this via settings)
        # Here we just initialize the ramp
        if self.current_phase.control_mode == "force":
            start_val = current_load
        else:
            start_val = current_disp
            
        self.ramp.start(start_val, self.current_phase.target, self.current_phase.rate)
        
        # The motor START command is sent ONCE at the beginning of the phase if not running,
        # or we just rely on VELSET.
        # But per specs: VELSET is used for adjustments. START is used once.
        # DataWorker will handle emitting the appropriate START if needed,
        # or TestEngine can send it.
        # Since we just use VELSET for running motor velocity without re-init,
        # maybe we send START here.
        # Wait, the spec says "START is used only once at the beginning of a test phase."
        self.serial.send_cmd("START")
        
        if self.on_phase_start:
            self.on_phase_start(self.current_phase)

    def tick(self, current_time: float, current_load: float, current_disp: float):
        """
        Main execution loop. Called for every serial frame.
        """
        if self.state != EngineState.RUNNING:
            return
            
        if not self.current_phase:
            return

        # 1. Global safety conditions
        if self._check_global_safety(current_load, current_disp):
            self.abort("Global safety limit exceeded")
            return

        # 2. Update setpoint
        setpoint = self.ramp.tick()
        
        # 3. Compute PID
        measured = current_load if self.current_phase.control_mode == "force" else current_disp
        vel_cmd = self.pid.compute(setpoint, measured)
        
        # 4. Command motor
        # Only send VELSET if it changed or periodically
        # (Assuming serial handler can handle fast VELSETs, or we rate-limit it)
        # We will send VELSET integer RPM
        rpm_cmd = int(round(vel_cmd))
        self.serial.send_cmd(f"VELSET:{rpm_cmd}")
        
        # 5. Check exit condition
        if self.current_phase.exit_condition.check(current_time, current_load, current_disp):
            self._complete_phase()
            
    def _complete_phase(self):
        self.serial.send_cmd("STOP")
        self.pid.reset()
        logger.info(f"Phase complete: {self.current_phase.name}")
        
        if self.on_phase_complete:
            self.on_phase_complete(self.current_phase)
            
        self.state = EngineState.PHASE_COMPLETE
        
        # Advance
        self.current_phase_idx += 1
        if self.current_phase_idx < len(self.method.phases):
            # We don't transition to RUNNING immediately, 
            # maybe we do, or we wait for next tick? Let's transition immediately.
            self.state = EngineState.RUNNING
            # We can't safely know the current load/disp here without passing it.
            # We will start it on the next tick, or require start values.
            # Let's rely on DataWorker calling advance() or we do it inline?
            pass 
        else:
            self.state = EngineState.TEST_COMPLETE
            logger.info("Test complete.")
            if self.on_test_complete:
                self.on_test_complete()

    def advance_phase(self, current_load: float, current_disp: float):
        if self.state == EngineState.PHASE_COMPLETE and self.current_phase_idx < len(self.method.phases):
            self.state = EngineState.RUNNING
            self._start_current_phase(current_load, current_disp)

    def abort(self, reason: str):
        self.serial.send_cmd("STOP")
        self.state = EngineState.ABORTED
        logger.critical(f"Test Aborted: {reason}")
        if self.on_abort:
            self.on_abort(reason)

    def _check_global_safety(self, load: float, disp: float) -> bool:
        limits = self.method.global_limits if self.method else {}
        if not limits:
            return False
            
        if "force_upper_n" in limits and load > limits["force_upper_n"]: return True
        if "force_lower_n" in limits and load < limits["force_lower_n"]: return True
        if "disp_upper_mm" in limits and disp > limits["disp_upper_mm"]: return True
        if "disp_lower_mm" in limits and disp < limits["disp_lower_mm"]: return True
        return False
