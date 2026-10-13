"""Run the web app on this PC only, without a server.

Starts the API on 127.0.0.1 and opens the browser. Each person's submitted
results go to ``<output>/<user>/`` (``workbooks="per_user"``), so several PCs
can save into one OneDrive-synced SharePoint folder without writing the same
Excel file; Power Query combines them (examples/power_query/combine_users.pq).

    taipower-web-local [--output DIR] [--choose-output] [--port N] [--no-browser]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from .settings import Settings

DEFAULT_PORT = 8765


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "TaipowerBillOCR" / "web"


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "TaipowerBillOCR"


def _load_config(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ask_output_dir(current: Path) -> Path | None:
    """A folder picker; None when cancelled or no display is available."""
    try:
        from tkinter import Tk, filedialog
    except ImportError:
        return None
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    chosen = filedialog.askdirectory(
        parent=root,
        title="Where should submitted Excel files and evidence PDFs be saved? (e.g. a synced SharePoint folder)",
        initialdir=str(current if current.is_dir() else Path.home()),
        mustexist=False,
    )
    root.destroy()
    return Path(chosen) if chosen else None


def _running_here(port: int) -> bool:
    """Whether this app already answers on ``port``."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return "workbooks" in json.load(response)
    except (OSError, ValueError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main(argv: list[str] | None = None, web_dist: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taipower-web-local", description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="folder for submitted Excel files and evidence PDFs (remembered)")
    parser.add_argument("--choose-output", action="store_true", help="pick the output folder again")
    parser.add_argument("--data", type=Path, default=default_data_dir(), help="uploads and the local database")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    url = f"http://127.0.0.1:{args.port}/"
    if not args.output and not args.choose_output and _running_here(args.port):
        print(f"Already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    args.data.mkdir(parents=True, exist_ok=True)
    config_path = args.data / "config.json"
    config = _load_config(config_path)
    output = args.output
    if output is None and not args.choose_output and config.get("output_dir"):
        output = Path(config["output_dir"])
    if output is None:
        output = _ask_output_dir(Path(config.get("output_dir") or default_output_dir())) or Path(
            config.get("output_dir") or default_output_dir()
        )
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    config["output_dir"] = str(output)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    port = args.port if _port_free(args.port) else _free_port()
    url = f"http://127.0.0.1:{port}/"
    extra = {"web_dist": web_dist} if web_dist is not None else {}
    settings = Settings(data_dir=args.data, output_dir=output, workbooks="per_user", **extra)
    if not settings.web_dist.is_dir():
        print(f"Web pages not found at {settings.web_dist}; build them with `npm run build`.", file=sys.stderr)
        return 1

    print("Taipower Bill OCR (this PC only)")
    print(f"  Open:         {url}")
    print(f"  Results go to: {output}\\<your name>\\  (evidence PDFs: {output}\\evidence\\<your name>\\)")
    print("  Change the folder: run again with --choose-output")
    print("Keep this window open while you work; close it to stop.")

    import uvicorn

    from .main import create_app

    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
