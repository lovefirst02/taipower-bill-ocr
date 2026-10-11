# PyInstaller spec for the desktop app. Build with packaging/build_exe.ps1.
# -*- mode: python ; coding: utf-8 -*-

import re
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

ROOT = Path(SPECPATH).parent

datas, binaries, hiddenimports = [], [], []

# PaddleX imports most of its pipeline code lazily and checks installed
# dependencies through package metadata, so collect code, data and metadata.
for package in ("paddlex", "paddleocr"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
    datas += copy_metadata(package, recursive=True)
    # PaddleOCR verifies its pipeline's dependencies -- including ones only
    # pulled in through PaddleX extras -- by reading their metadata.
    for requirement in requires(package) or []:
        name = re.split(r"[\s;<>=!~\[(]", requirement, maxsplit=1)[0]
        try:
            version(name)
        except PackageNotFoundError:
            continue  # an optional extra this app does not install
        datas += copy_metadata(name, recursive=True)

binaries += collect_dynamic_libs("paddle")
datas += collect_data_files("paddle")
hiddenimports += collect_submodules("paddle")

# pdfminer ships CMaps; winrt projections are separate namespace packages.
datas += collect_data_files("pdfminer")
hiddenimports += collect_submodules("winrt")
datas += copy_metadata("taipower-bill-ocr")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "IPython", "jupyter", "pytest", "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TaipowerBillOCR",
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="TaipowerBillOCR",
)
