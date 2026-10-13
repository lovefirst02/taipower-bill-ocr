"""Entry point for the packaged desktop app (PyInstaller).

TaipowerBillOCR.exe opens the Tk GUI; TaipowerBillOCRWeb.exe (the same
program with a console window) runs the web app on this PC only.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    path = Path(base) / "TaipowerBillOCR"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prepare_frozen_environment() -> Path:
    data_dir = _app_data_dir()
    # A windowed exe has no console: libraries that print or log would fail
    # on a missing stream, so send their output to a log file instead.
    log = open(data_dir / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log
    # Bundled models make the app work offline; keep PaddleX's cache in a
    # writable per-user folder rather than beside the executable.
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(data_dir / "paddlex"))
    return data_dir


def _self_test(bill_path: str, output: Path) -> int:
    """Run OCR on one file without the GUI and report the outcome as JSON.

    Used to verify a build (``TaipowerBillOCR.exe --self-test bill.pdf``).
    """
    import json
    import time

    from taipower_bill_ocr.batch import FileFinished, PageDone, PageExtractor, iter_batch
    from taipower_bill_ocr.paddle_ocr import bundled_model_dir

    started = time.perf_counter()
    report: dict = {"file": bill_path, "bundled_models": str(bundled_model_dir())}
    try:
        rows, errors = [], []
        for event in iter_batch(
            [Path(bill_path)], PageExtractor("crosscheck"), page_numbers=[1]
        ):
            if isinstance(event, PageDone) and event.record is not None:
                rows.append(
                    {
                        "row": event.record.row,
                        "issues": [issue.en for issue in event.record.issues],
                    }
                )
            elif isinstance(event, FileFinished) and event.error:
                errors.append(event.error)
        report.update(ok=bool(rows) and not errors, rows=rows, errors=errors)
    except Exception:  # noqa: BLE001 - reported in the JSON
        report.update(ok=False, error=traceback.format_exc())
    report["seconds"] = round(time.perf_counter() - started, 1)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def _run_web(argv: list[str]) -> int:
    """The web app on this PC: no server, results saved per user."""
    from taipower_web_api.desktop import main as web_main

    web_dist = Path(getattr(sys, "_MEIPASS", "")) / "web" if getattr(sys, "frozen", False) else None
    try:
        return web_main(argv, web_dist=web_dist)
    except Exception:  # noqa: BLE001 - keep the console open to show it
        traceback.print_exc()
        input("Press Enter to close...")
        return 1


def main() -> None:
    frozen = getattr(sys, "frozen", False)
    data_dir = _prepare_frozen_environment() if frozen else None
    if (frozen and Path(sys.executable).stem.lower().endswith("web")) or sys.argv[1:2] == ["--web"]:
        sys.exit(_run_web([arg for arg in sys.argv[1:] if arg != "--web"]))
    if len(sys.argv) > 1 and sys.argv[1] == "--watch":
        # TaipowerBillOCR.exe --watch IN --output OUT [--once] [--interval N]
        from taipower_bill_ocr.watcher import main as watch_main

        sys.exit(watch_main(sys.argv[2:]))
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        output = (data_dir or Path.cwd()) / "self_test.json"
        sys.exit(_self_test(sys.argv[2], output))
    try:
        from taipower_bill_ocr.gui import main as run_gui

        run_gui()
    except Exception:  # noqa: BLE001 - last-resort report for a windowed app
        details = traceback.format_exc()
        if data_dir is not None:
            (data_dir / "crash.log").write_text(details, encoding="utf-8")
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Taipower Bill OCR",
                "The app hit an unexpected error and has to close.\n\n"
                + details[-1500:]
                + (f"\n\nDetails were saved to {data_dir / 'crash.log'}" if data_dir else ""),
            )
        finally:
            raise


if __name__ == "__main__":
    main()
