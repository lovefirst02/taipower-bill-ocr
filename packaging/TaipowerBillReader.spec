# PyInstaller spec for the CSV tool. Build with packaging/build_reader.ps1.
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = collect_data_files("pdfminer")
binaries = collect_dynamic_libs("onnxruntime")
hiddenimports = collect_submodules("winrt")

a = Analysis(
    [str(ROOT / "packaging" / "reader_launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # This tool reads with ONNX alone: none of the desktop app's engines or web server.
    excludes=["paddle", "paddlex", "paddleocr", "torch", "tensorflow", "matplotlib", "IPython", "jupyter", "pytest", "fastapi", "uvicorn"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TaipowerBillReader", console=True, upx=False)

coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="TaipowerBillReader")
