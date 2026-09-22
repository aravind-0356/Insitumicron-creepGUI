# theme.py
from constants import PRIMARY_COLOR, ACCENT_COLOR

# --- Global Stylesheet (original 18px-based sizes) ---
GLOBAL_STYLESHEET = f"""
QMainWindow {{
    background-color: #f8f9fa; 
}}
QWidget {{
    background-color: #ffffff; 
    color: #2c3e50; 
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 18px;
}}
QToolTip {{
    background-color: #2c3e50;
    color: white;
    border: 1px solid #34495e;
    border-radius: 4px;
    padding: 5px;
    font-size: 14px;
}}
QLineEdit {{
    background-color: #f4f6f7; 
    border: 1px solid #bdc3c7; 
    padding: 10px;
    border-radius: 6px;
    color: #2c3e50;
    selection-background-color: {PRIMARY_COLOR};
    font-size: 18px;
}}
QLineEdit:focus {{
    border: 1px solid {PRIMARY_COLOR};
}}
QPushButton {{
    background-color: {PRIMARY_COLOR}; 
    border: none;
    padding: 12px 24px;
    border-radius: 6px;
    color: white;
    font-weight: bold;
    font-size: 18px;
}}
QPushButton:pressed {{
    background-color: #004a66; 
    padding-top: 14px;        
    padding-left: 26px;       
    padding-bottom: 10px;
    padding-right: 22px;
}}
QPushButton:hover {{
    background-color: {ACCENT_COLOR};
}}
QPushButton:disabled {{
    background-color: #dfe6e9; 
    color: #b2bec3;
}}

/* --- Modern QComboBox with Standard Arrow --- */
QComboBox {{
    background-color: #ffffff;
    border: 1px solid #bdc3c7;
    border-radius: 6px;
    padding: 10px 35px 10px 12px;
    min-width: 8em;
    color: #2c3e50;
    selection-background-color: {PRIMARY_COLOR};
    font-size: 18px;
}}
QComboBox:hover {{
    border: 1px solid {PRIMARY_COLOR};
}}
QComboBox:on {{
    padding-top: 12px;
    padding-left: 14px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 30px;
    border-left-width: 1px;
    border-left-color: #bdc3c7;
    border-left-style: solid;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #ecf0f1;
}}
QComboBox::drop-down:hover {{
    background-color: #d5dbdb;
}}
/* CSS TRIANGLE ARROW - Reliable rendering */
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid #2c3e50;
    margin-right: 5px;
}}
QComboBox::down-arrow:on {{
    border-top: 5px solid {PRIMARY_COLOR};
}}
QComboBox QAbstractItemView {{
    border: 1px solid {PRIMARY_COLOR};
    selection-background-color: {PRIMARY_COLOR};
    selection-color: white;
    outline: 0px;
    font-size: 18px;
    padding: 6px;
}}

QGroupBox {{
    border: 1px solid #ecf0f1;
    border-radius: 8px;
    margin-top: 1.4em;
    font-weight: bold;
    color: {PRIMARY_COLOR}; 
    background-color: white;
    font-size: 20px;
    padding-top: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left; 
    padding: 0 8px;
    background-color: white;
}}



/* Fading effect for disabled widgets */
QWidget:disabled {{
    color: #95a5a6;
}}
QGroupBox:disabled {{
    color: #bdc3c7;
    border-color: #f1f2f6;
}}

QLabel {{
    font-size: 18px;
}}


QCheckBox {{
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid #bdc3c7;
    border-radius: 3px;
    background: #ffffff;
}}

QCheckBox::indicator:hover {{
    border: 2px solid {PRIMARY_COLOR};
    background: #eaf4fb;
}}

QCheckBox::indicator:checked {{
    border: 2px solid {PRIMARY_COLOR};
    background-color: {PRIMARY_COLOR};
    image: url(assets/checkbox_tick.png);
}}

QSlider::groove:horizontal {{
    border: 1px solid #bdc3c7;
    height: 8px;
    background: #e0e0e0;
    margin: 2px 0;
    border-radius: 4px;
}}

QSlider::handle:horizontal {{
    background: {PRIMARY_COLOR};
    border: 1px solid {PRIMARY_COLOR};
    width: 18px;
    height: 18px;
    margin: -5px 0;
    border-radius: 9px;
}}

QSlider::handle:horizontal:hover {{
    background: {ACCENT_COLOR};
    border: 1px solid {ACCENT_COLOR};
}}
"""
