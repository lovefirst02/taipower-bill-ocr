"""Paths that PaddlePaddle's native code can open.

Paddle's C++ inference loader cannot open a file whose Windows path has
characters outside ASCII: with the app unpacked to 桌面 or a Chinese user
name, loading a model fails with "Cannot open file ...inference.json".
These helpers give such folders an ASCII path: the Windows short (8.3) name
when the drive has one, otherwise a copy in an ASCII folder.

Only the standard library is used, so the packaged app's launcher can call
this before PaddleX is imported.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

APP_FOLDER = "TaipowerBillOCR"


def short_path(path: Path) -> str | None:
    """The Windows 8.3 short form of an existing path, if the drive keeps them."""
    if sys.platform != "win32":
        return None
    import ctypes

    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, size):
        return None
    return buffer.value


def ascii_dir(name: str) -> Path | None:
    """A writable folder ``TaipowerBillOCR/<name>`` whose path is ASCII.

    C:\\ProgramData and C:\\Users\\Public are ASCII even for a Chinese user
    name; the temp folder is the last resort.
    """
    bases = [os.environ.get("ProgramData"), os.environ.get("PUBLIC"), tempfile.gettempdir()]
    for base in bases:
        if not base or not base.isascii():
            continue
        folder = Path(base) / APP_FOLDER / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".write-test-{uuid.uuid4().hex[:8]}"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError:
            continue
        return folder
    return None


def _same_files(source: Path, copy: Path) -> bool:
    for file in source.rglob("*"):
        if file.is_file():
            other = copy / file.relative_to(source)
            if not other.is_file() or other.stat().st_size != file.stat().st_size:
                return False
    return True


def paddle_readable(path: Path) -> Path:
    """``path``, or an ASCII path to the same folder that Paddle can open."""
    if str(path).isascii():
        return path
    short = short_path(path)
    if short and short.isascii():
        return Path(short)
    base = ascii_dir("models")
    if base is None:
        return path  # nothing better; Paddle reports the path it could not open
    copy = base / path.name
    if not _same_files(path, copy):
        shutil.copytree(path, copy, dirs_exist_ok=True)
    return copy


def paddle_cache_dir(preferred: Path) -> Path:
    """Where PaddleX keeps its downloads and cache: ``preferred`` if Paddle can use it."""
    if str(preferred).isascii():
        return preferred
    try:
        preferred.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    short = short_path(preferred) if preferred.exists() else None
    if short and short.isascii():
        return Path(short)
    return ascii_dir("paddlex") or preferred
