# Insitumicron Creep GUI v1.1

Industrial PySide6 desktop application for electromechanical test equipment control.

## System Overview

Insitumicron Creep GUI is designed for precision electromechanical testing equipment, interfacing with:
- **STM32 Microcontroller** over USB-Serial (Leedshine Modbus RTU, Micro-Epsilon LVDT, I2C Load Cell)
- **FLIR Spinnaker Industrial Camera** for synchronized visual tracking and image acquisition

## Architecture

The project follows a 3-layer architecture:
- **Directives (`directives/`)**: Standard Operating Procedures (SOPs) defining hardware constraints, operational flows, and integration rules.
- **Controllers (`controllers.py`)**: Extracted business logic (DAQ, homing, preload, recording, graph controls, and error management).
- **UI Panels (`gui_panels.py`, `gui_test_views.py`, `gui_analysis.py`)**: PySide6 presentation layer communicating with workers strictly via Qt Signal/Slot mechanisms.
- **Workers (`data_worker.py`, FLIR Camera Worker)**: Background threads handling deterministic serial protocol parsing and cooperative image acquisition.

## Project Structure

```
├── assets/               # Logos, icons, and UI assets
├── config/               # Machine & calibration configuration
├── directives/           # SOPs and architectural directives
├── drivers/              # Bundled hardware driver packages (ST-LINK, etc.)
├── execution/            # Deterministic automation and maintenance scripts
├── sample_data/          # Reference test datasets
├── tests/                # Unit and integration test suites
├── controllers.py        # Business logic controllers
├── data_worker.py        # STM32 serial data parsing & processing
├── gui.py                # Main window & worker coordination
├── gui_panels.py         # Primary UI panels & plot setup
├── gui_test_views.py     # Test-view panels & motor controls
├── gui_analysis.py       # Post-test data analysis & reporting
├── machine_config.py     # Machine parameter definitions
├── main.py               # Application entry point & DPI configuration
├── serial_handler.py     # STM32 serial communication handler
├── settings.py           # Settings management
├── theme.py              # UI styling and color themes
├── build.spec            # PyInstaller build specification
└── installer.iss         # Inno Setup installer packaging script
```

## Getting Started

### Prerequisites
- Python 3.10+ (64-bit recommended)
- ST-LINK USB drivers (for STM32 VCP)
- FLIR Spinnaker SDK & `PySpin` (for camera acquisition)
- NI-DAQmx runtime (if DAQ hardware is enabled)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/aravind-0356/Insitumicron-creepGUI.git
   cd Insitumicron-creepGUI
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   # On Windows PowerShell:
   .\venv\Scripts\Activate.ps1
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Running the Application
```bash
python main.py
```

## Packaging & Release

- **PyInstaller**:
  ```bash
  pyinstaller build.spec
  ```
- **Inno Setup**:
  Open `installer.iss` in Inno Setup Compiler to produce the Windows installer setup executable.
