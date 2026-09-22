# AGENTS.md
> Project: Insitumicron Creep GUI v1.1
> Industrial PySide6 desktop application for electromechanical test equipment control.
> Hardware: STM32 over USB-Serial (Leedshine Modbus RTU, Micro-Epsilon LVDT, I2C Load Cell), FLIR Spinnaker camera.
>
> This is production, safety-critical industrial software. Not a demo. Not a prototype.
> A motor control bug is not a UX issue â€” it is a safety issue.

---

## The 3-Layer Architecture

Every task you perform maps to one of these layers. Do not collapse layers. Do not skip layers.
LLMs are probabilistic. Hardware interaction must be deterministic. This architecture fixes
that mismatch.

**Layer 1 â€” Directive (What to do)**
- SOPs written in Markdown, stored in `directives/`
- Define goals, inputs, required scripts, expected outputs, edge cases, hardware constraints
- Written in natural language â€” instructions you would give a Junior-level embedded/Qt engineer
- Examples: `directives/add_new_controller.md`, `directives/add_daq_channel.md`

**Layer 2 â€” Orchestration (You â€” decision making)**
- Your job: intelligent routing between directives and execution scripts
- Read the relevant directive FIRST. Do not improvise architecture without reading it.
- Call execution scripts in the right order, handle errors, ask for clarification before
  any destructive or hardware-touching operation
- You do NOT directly mutate hardware-facing code (DataWorker,
  SerialHandler, DataWorker) without reading the relevant directive and confirming
  the thread-safety contract is preserved
- Update directives with learnings (API limits, hardware quirks, Qt edge cases)

**Layer 3 â€” Execution (Deterministic scripts)**
- Python scripts in `execution/` that do one thing reliably
- Handle file operations, code generation, validation, data processing, test scaffolding
- Must be commented, testable, and side-effect free unless explicitly stated
- Device names, baud rates, DAQ channels, voltage ranges: stored in `.env`

**Why this matters:** A Qt signal-slot wiring error in a 5-step refactor compounds.
90% accuracy per step = 59% success over 5 steps. Push complexity into deterministic scripts.
You focus on decision-making and routing.

---

## Operating Principles

### 1. Check directives and execution scripts first
Before writing any code:
- Check `directives/` for an existing SOP matching the task
- Check `execution/` for scripts that already do the work
- Only create new scripts or directives if none exist â€” confirm with the user first

### 2. Self-anneal when things break
When a script, pattern, or architectural assumption fails:
1. Read the full error and stack trace
2. Fix the script
3. Test it â€” if the fix touches any hardware path (serial TX, DAQ task setup,
   AO voltage write, camera init), flag the user before running. Do not auto-execute.
4. Update the directive with what you learned (timing constraints, Qt thread requirements,
   NI-DAQmx error codes, PySpin exceptions, Inno Setup quirks)
5. The system is now stronger

### 3. Directives are living documents
Update them as you discover Qt constraints, hardware timing requirements, and edge cases.
Do NOT overwrite or delete directives without asking. They are institutional memory.
Improve them. Do not discard them.

### 4. Hardware safety gate
Any change touching these files requires explicit user confirmation before execution:
- `serial_handler.py` â€” direct STM32 serial communication
- `gui.py
    DataWorker (combines Serial parsing and data processing) + CameraWorker + MainWindow.
    MainWindow is the composition root â€” it wires all workers, panels, controllers.
    DataWorker processes serial lines. It is NOT a QThread subclass.
    CameraWorker uses cooperative QTimer.singleShot(0, _acquire_frame) loop.

gui_panels.py
    ConnectionPanel + SamplePanel + setup_plot_style() + PlotResizeFilter.
    Stateless UI panels. No business logic. Emit signals upward.

gui_test_views.py
    All test-view panels: UniaxialMotorControlPanel, SensorDisplayPanel, SampleInspectionPanel, TestDashboardPanel, StyledCheckBox.
    Motor velocity validation lives here. No serial TX except via signal chain.

controllers.py
    The correct home for all extracted business logic.
    Current controllers: ErrorManager, DAQController, HomingController,
                         PreloadController, RecordingController, GraphController.
    When MainWindow grows a new domain: extract to here, not the reverse.
```

---

## Critical Thread-Safety Rules

Non-negotiable. Violations cause intermittent data corruption, race conditions, or crashes
that are extremely hard to reproduce on hardware.

### Rule T-1: All cross-thread communication via Signal/Slot ONLY
- No shared mutable state between Qt-managed threads
- No `threading.Event` or `queue.Queue` between QThread workers
- Exception: `ImageSaveWorker` and `DataWorker._recording_loop` use `threading.Thread`
  (not QThread) with `queue.Queue` â€” this is intentional and documented

### Rule T-2: Copy all list/dict payloads before emission
```python
# CORRECT
data = {
    "time": self._time_data.copy(),
    "uni_L": self._load_data.copy(),
    "bi_L": [ch.copy() for ch in self._bi_load_data]
}
self.plot_update.emit(data)

# WRONG â€” GUI thread may read while worker mutates
data = {"time": self._time_data, "uni_L": self._load_data}
self.plot_update.emit(data)
```

### Rule T-3: No QMutex on the DAQ hot path
STM32 handles hardware polling internally. QMutex acquisition causes jitter and
timing drift. Use atomic Python float assignment (atomic in CPython) for single values.
`with self._data_lock` is only acceptable for multi-value consistency snapshots outside
the per-sample loop.

### Rule T-5: Workers never reference each other
Workers hold no references to other workers. All coordination goes through MainWindow
or a Controller. DAQWorker does not know DataWorker exists and vice versa.

### Rule T-6: Cooperative camera acquisition loop
`CameraWorker._acquire_frame()` reschedules itself with `QTimer.singleShot(0, self._acquire_frame)`.
This yields to the event loop between frames so pending Slot calls execute.
Do not replace with a `while self.running:` loop â€” it will starve the event loop.

### Rule T-7: No UI access from non-GUI threads
Workers emit signals. Panels receive them via Slots. A worker calling any QWidget method
directly (setText, setEnabled, update, etc.) is a threading violation.

---

## SensorWorkerProtocol Compliance

Any new worker that feeds sensor data to the GUI MUST implement every method in
`interfaces.SensorWorkerProtocol`. Verify against the protocol before writing a worker.

Required methods:
- `set_mode(mode: str)`
- `set_break_detection(enabled: bool, threshold: float)`
- `update_sampling_rate(rate: int)`
- `zero_uni_load()`, `zero_uni_disp()`
- `zero_bi_single(idx: int)`, `zero_bi_all()`
- `request_start_recording(csv_path: str, mode: str)`
- `start_recording(csv_path: str, mode: str)`
- `stop_recording()`
- `is_recording() -> bool`
- `request_configure_preload(target: float, indices: list)`
- `configure_preload(target: float, indices: list)`
- `get_current_load() -> float`

Required signals (Protocol cannot enforce these â€” verify manually):
- `ui_update = Signal(dict)` â€” payload must match `UIUpdatePayload`
- `plot_update = Signal(dict)` â€” payload must match `PlotUpdatePayload`
- `break_detected_stop = Signal(str)`
- `overload_detected = Signal(str)`
- `request_image_save = Signal()`
- `recording_error = Signal(str)`
- `preload_finished = Signal()`

---

## Controller Extraction Pattern

When MainWindow grows a new domain of logic, extract it to `controllers.py`:

```python
class NewController(QObject):
    some_signal = Signal(...)

    def __init__(self, serial_handler, some_worker, main_window):
        super().__init__(main_window)   # parent = main_window for lifetime management
        self._serial = serial_handler
        self._worker = some_worker
        self._mw = main_window          # for QMessageBox parent + state reads only

    @Slot(...)
    def handle_something(self, ...):
        ...
```

Rules:
- Constructor receives only what it needs â€” no god-object references
- Parent is always `main_window` â€” automatic cleanup on window close
- State that belongs to the controller stays in the controller
- MainWindow wires signals in `__init__` after all panels and workers are created

---

## Settings Pattern

Adding a new persistent setting:
1. Add key + default to `DEFAULT_SETTINGS` in `settings.py` FIRST
2. Read via `get_setting("key", fallback)` at construction time
3. Save via `set_setting("key", value)`
4. Never read directly from the JSON file
5. DAQ calibration settings: `daq_*` prefix (e.g. `daq_load_v_min`)

---

## Styling and DPI Rules

DPI scaling is handled exclusively by two environment variables set in `main.py`:
```python
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
os.environ["QT_SCALE_FACTOR"] = "0.6667"
```


The `0.6667` counter-scale neutralizes 150% Windows display scaling at the deployment
site (IIT Patna lab machine). Formula: `1.0 / 1.5 = 0.6667`.

Styling rules:
- Colors: use `PRIMARY_COLOR` and `ACCENT_COLOR` from `constants.py` â€” always
- Global styles: belong in `theme.py` GLOBAL_STYLESHEET only
- Component inline overrides: `setStyleSheet()` in panel `__init__`, f-string referencing
  constants â€” never raw hex strings

```python
# CORRECT
from constants import PRIMARY_COLOR
btn.setStyleSheet(f"background-color: {PRIMARY_COLOR}; color: white;")

# WRONG
btn.setStyleSheet("background-color: #005b7f; color: white;")
```

---

## Logging Rules

```python
# CORRECT
from logging_config import get_logger
logger = get_logger(__name__)

logger.debug("Voltage raw: %.4f V", v)         # Hot path only â€” never INFO on hot path
logger.info("Recording started: %s", path)      # State transitions
logger.warning("STM32 not found on %s", port)   # Recoverable issues
logger.error("CSV write failed: %s", e)          # Failures requiring action
logger.exception("Unexpected crash in loop")     # Includes full traceback

# WRONG
print("value:", x)
logging.info("message")    # Root logger â€” loses module name context
```

---

## Serial / STM32 Communication Patterns

All TX goes through `serial_handler.send_cmd(cmd: str)` â€” never write to `.ser` directly.

| Command | Effect |
|---|---|
| `VEL:{rpm}` | Set velocity (signed int, RPM) |
| `ACC:{val}` | Set acceleration |
| `DEC:{val}` | Set deceleration |
| `START` | Start motor |
| `STOP` | Stop motor |
| `VELSET:{rpm}` | Live velocity update |
| `TARE_DISP` | Tare LVDT |

- Multi-command sequences use `_process_command_sequence()` with
  `QTimer.singleShot(delay_ms, self._process_command_sequence)` for inter-command timing
- Encoder data from STM32: `"E:{int32}"` â€” parsed in `DataWorker.process_line()`
- Overload messages from STM32: `"OVERLOAD:{msg}"` â€” handled separately, never as sensor data
- STM32 auto-detection by USB VID `0x0483` via `SerialHandler.detect_stm32_port()`

---

## Motor Safety Constraints

Enforced in panel code. Any refactor must preserve all limits.

| System | Max Speed | Conversion Formula |
|---|---|---|
| Creep (Uniaxial) | ±3000 RPM (Modbus range) | Modbus raw |

Load safety limits are handled by STM32 (Kill Switch) and GUI software limits. Overload messages from STM32: "OVERLOAD:{msg}" trigger emergency stop.
---|---|---|
| Uniaxial | Â±5.0 mm/min | `rpm = mm_min * 800 / 3` |
| Biaxial Individual | Â±10.0 mm/min | `rpm = (mm_min * 225) / 4` |
| Biaxial Axial | Â±10.0 mm/min | `rpm = (mm_min * 225) / 8` |
| Biaxial Max RPM | 281 RPM | Hard gate in `emit_preload()` |

Load safety limits in `DAQWorker._acquisition_loop()` per sample:
- Compression overload: load >= +5000 N AND `_motor_direction >= 0`
- Tension overload: load <= -5000 N AND `_motor_direction <= 0`
- `_overload_fired` latch prevents popup flooding â€” reset on `zero_uni_load()` or new session
- Direction tracking: `set_motor_direction(1/-1/0)` called from GUI via Signal

---

## FLIR Camera Patterns

- `CameraWorker` uses `QObject` + `moveToThread()` (not QThread subclass)
- All public methods emit internal signals for thread dispatch â€” never call `_apply_*` directly
  from the GUI thread
- Camera recovery from force-kill: catch `SpinnakerException -1004`, call `EndAcquisition()`,
  retry `cam.Init()`
- Image save: `threading.Thread` (`ImageSaveWorker`) + `queue.Queue` â€” not Qt-managed
- Format: TIFF with `cv2.IMWRITE_TIFF_COMPRESSION = 1` (no compression) â€” required
- `stop_and_drain()` on app exit flushes the save queue before thread termination

---

## Recording Pipeline

```
RecordingController.start_record()
    Validates: connection | camera live (if saving images) | output folder | save options
    Creates: {output_folder}/{sample_id}_{YYYYMMDD_HHMMSS}/
    Creates: Images/ subfolder if saving images
    Calls: CameraWorker.start_recording(images_dir)
    Calls: worker.request_start_recording(csv_path, mode)
           DAQWorker path  -> _start_recording_requested Signal -> start_recording() Slot
           DataWorker path -> direct call (spins off threading.Thread for _recording_loop)
    Updates UI: sensor_panel.set_recording_state(True)

RecordingController.stop_record()
    Calls: CameraWorker.stop_recording()
    Calls: daq_ctrl.worker.stop_recording()
    Calls: data_worker.stop_recording()
    Updates UI: sensor_panel.set_recording_state(False)
    If save_sensor_data: runs ExcelExportWorker on QThread (progress dialog, non-blocking)
```

CSV headers:
- Uniaxial: `["Time(s)", "Load(N)", "Disp(mm)"]`
- Biaxial: `["Time(s)", "Load 1 (N)", "Load 2 (N)", "Load 3 (N)", "Load 4 (N)"]`

---

## Application Shutdown Order

The `closeEvent` shutdown sequence is order-dependent. Do not reorder.

```
1. Stop recording (if active)     # Flush CSV, join _rec_thread
2. serial_handler.disconnect()    # Close serial port
3. data_thread.quit() + wait()    # Join DataWorker thread
4. camera_panel shutdown          # EndAcquisition, DeInit, drain save queue          # EndAcquisition, DeInit, drain save queue
```



---

## Error Handling Hierarchy

| Error Type | Handler | Mechanism |
|---|---|---|
| Serial disconnect | `ErrorManager.collect("STM32 Controller", msg)` | 3s debounce, consolidated popup |
| DAQ hardware error | `DAQController.handle_error(msg)` | Updates UI, restarts poll timer |
| Camera error | `ErrorManager.collect("Camera", msg)` | Debounced via signal |
| Multiple simultaneous | `ErrorManager._flush()` | Single "USB Hub Disconnected" popup |
| CSV write failure | `handle_recording_error(msg)` | Stops recording, critical popup |
| Break detection | `handle_break_stop(msg)` | Freezes plots, stops motor + recording |
| Overload | `handle_overload_stop(msg)` | Freezes plots, warning popup |

The `ErrorManager` 3-second debounce is intentional â€” hub unplugs emit multiple
simultaneous error signals. Do not tighten or remove this window.

---

## Build and Installer

### PyInstaller
- Entry point: `main.py`
- Mode: onedir (`--onedir`), places all internals in `_internal/` subfolder
- Output exe: `dist/Insitumicron Creep GUI/Insitumicron Creep GUI.exe`
- Resource paths must use `resource_path()` from `logging_config.py` for frozen compatibility
- `sys._MEIPASS` is the bundle dir in frozen mode â€” never hardcode paths relative to `__file__`
- `resource_path()` handles the `_internal/` subfolder automatically for PyInstaller 6+ onedir

### Inno Setup (Installer Packaging)
- Tool: Inno Setup 6.7.0
- Script: `installer.iss` (project root)
- Output: `Output/InsituMicron_GUI_v3.1.2_Setup.exe`
- Source: `dist/Insitumicron Creep GUI/` (full PyInstaller onedir output)
- Build time: ~17 minutes (1038 seconds) â€” expected for PySide6 + numpy + OpenCV bundle

Bundled driver installers in `drivers/`:
- `drivers/stsw-link009/dpinst_amd64.exe` — ST-LINK USB Driver package (`STSW-LINK009`) for ST-LINK/V2-1 & STLINK-V3 Virtual COM Port (VCP), signed for Windows 7/8/10/11. Silent install: `dpinst_amd64.exe /q /se`
- `drivers/NIPackageManager26.0.0_online.exe` — NI Package Manager (installs NI-DAQmx runtime)

Known open issue in `installer.iss`:
- Architecture identifier `"x64"` is deprecated in Inno Setup 6.7.0
- Current warning: `Architecture identifier "x64" is deprecated. Substituting "x64os",
  but note that "x64compatible" is preferred in most cases.`
- Fix: in the [Setup] section, change `ArchitecturesInstallIn64BitMode=x64`
  to `ArchitecturesInstallIn64BitMode=x64compatible`

Post-install run (line 54 in `.iss`): verify that the app launch entry does not execute
before driver installers complete â€” NI-DAQmx must be installed before the app can
initialize `LightController` or `DAQWorker` successfully.

---

## File Organization

```
.tmp/                    Intermediate files â€” never commit, always regeneratable
execution/               Deterministic Python scripts â€” one job each, well-commented
directives/              SOPs in Markdown â€” living documents, improve not discard
.env                     Device names, baud rates, DAQ channels, voltage ranges, API tokens
logs/                    Rotating log files (auto-created by logging_config.py)
drivers/                 Bundled hardware driver installers for Inno Setup packaging
home_config.json         Persisted encoder home position (user config dir at runtime)
settings.json            Persisted user settings (app dir or APPDATA at runtime)
Output/                  Inno Setup output â€” installer .exe lives here
dist/                    PyInstaller output â€” never manually edit, regenerated on build
```

Deliverables (outputs the user accesses after a test run):
- `.xlsx` test results in user-selected output folder
- `.tiff` images in timestamped subfolder (`{sample_id}_{YYYYMMDD_HHMMSS}/Images/`)
- Log files in `%APPDATA%/Insitumicron Creep GUI/logs/` (frozen) or `./logs/` (dev)

Intermediates (`.tmp/`):
- Temp exports, scraped data, intermediate files â€” never commit

---

## Directives Index

Build these directives to cover the most common engineering tasks on this project.

| File | Covers |
|---|---|
| `directives/add_new_controller.md` | Extracting logic from MainWindow into controllers.py |
| `directives/add_new_sensor_worker.md` | Implementing SensorWorkerProtocol on a new worker |
| `directives/add_daq_channel.md` | Adding a new NI-DAQ AI channel end-to-end |
| `directives/add_settings_key.md` | Adding a new persistent setting end-to-end |
| `directives/add_serial_command.md` | New STM32 command with panel UI and send_cmd path |
| `directives/refactor_panel.md` | Moving a panel widget from gui.py to gui_panels.py |
| `directives/add_plot_curve.md` | New pyqtgraph curve with GraphController integration |
| `directives/recording_pipeline_change.md` | Any modification to recording/CSV/Excel path |
| `directives/build_and_release.md` | PyInstaller build + Inno Setup packaging + versioning |

---

## Self-Annealing Loop for This Project

When an agent task breaks something:

1. Identify the layer â€” panel (UI), controller (business logic), or worker/handler (hardware)?
2. Check the thread boundary â€” if the traceback crosses a thread boundary, T-1 through T-8 apply
3. Fix deterministically â€” prefer fixing execution scripts before touching GUI code
4. Test the signal chain â€” for any worker change, verify:
   signal emitted -> slot received -> UI updated -> no thread violation
5. Update the directive â€” if a new Qt, NI-DAQmx, or PySpin constraint was discovered,
   document it in the relevant `directives/` file
6. Hardware gate â€” if the fix involves any command to hardware (serial, AO voltage, camera),
   flag the user. Do not auto-execute.

---

## Summary

You sit between human intent (directives) and deterministic execution (scripts).
Read the architecture map. Respect the thread-safety rules. Respect the hardware safety gate.
Check the relevant directive before writing code. Self-anneal when things break.
Update directives with what you learn.

Be pragmatic. Be reliable. Self-anneal.