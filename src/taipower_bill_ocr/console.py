"""Console helpers for Windows OCR output."""

from __future__ import annotations

from typing import Any


def configure_utf8_output(stream: Any) -> None:
    """Use UTF-8 when the output stream supports runtime reconfiguration."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="backslashreplace")
