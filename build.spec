# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, copy_metadata

# Automotive dependency collection for NI-DAQmx 1.4.1 requirements
ni_datas, ni_binaries, ni_hiddenimports = collect_all('nidaqmx') # Main DAQ library - Verified
nt_datas, nt_binaries, nt_hiddenimports = collect_all('nitypes') # Required data types - Verified
ox_datas, ox_binaries, ox_hiddenimports = collect_all('openpyxl') # Excel exports - Verified
cl_datas, cl_binaries, cl_hiddenimports = collect_all('click') # nidaqmx CLI dependency - Verified
dep_datas, dep_binaries, dep_hiddenimports = collect_all('deprecation') # nidaqmx version management - Verified
ht_datas, ht_binaries, ht_hiddenimports = collect_all('hightime') # nidaqmx precision timing - Verified
dc_datas, dc_binaries, dc_hiddenimports = collect_all('python-decouple') # nidaqmx config dependency - Verified
rq_datas, rq_binaries, rq_hiddenimports = collect_all('requests') # nidaqmx update checks - Verified
te_datas, te_binaries, te_hiddenimports = collect_all('typing_extensions') # nidaqmx typing support - Verified
tz_datas, tz_binaries, tz_hiddenimports = collect_all('tzlocal') # nidaqmx timezone dependency - Verified
pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all('packaging') # nidaqmx requirement tracking - Verified
ps_datas, ps_binaries, ps_hiddenimports = collect_all('PySpin') # FLIR camera library - Verified
cv_datas, cv_binaries, cv_hiddenimports = collect_all('cv2')    # OpenCV image saving - Verified

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=ni_binaries + ox_binaries + dep_binaries + pkg_binaries + tz_binaries + nt_binaries + ht_binaries + cl_binaries + te_binaries + ps_binaries + cv_binaries,
    datas=[
        ('LICENSES', 'LICENSES'),
        ('app_icon.ico', '.'),
        ('InsituMicronlogo.jpg', '.'),
    ] + ni_datas + ox_datas + dep_datas + pkg_datas + tz_datas + nt_datas + ht_datas + cl_datas + te_datas + ps_datas + dc_datas + rq_datas + cv_datas + copy_metadata('nidaqmx') + copy_metadata('nitypes') + copy_metadata('python-decouple'),
    hiddenimports=[
        # pyqtgraph
        'pyqtgraph',
        # PySide6
        'PySide6.QtCore',
        'PySide6.QtWidgets',
        'PySide6.QtGui',
        # NumPy internals
        'numpy.core._methods',
        'numpy.lib.format',
    ] + ni_hiddenimports + ox_hiddenimports + dep_hiddenimports + pkg_hiddenimports + tz_hiddenimports + nt_hiddenimports + ht_hiddenimports + cl_hiddenimports + te_hiddenimports + ps_hiddenimports + dc_hiddenimports + rq_hiddenimports + cv_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'pandas',
        'PIL',
        'pytest',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='InsituMicronGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # Disabled — UPX can corrupt Qt/NumPy DLLs silently
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='InsituMicronGUI',
)