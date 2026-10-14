"""Run the web app on this PC only, without a server.

Starts the API and opens the browser. Each person's submitted results go to
``<output>/<user>/`` (``workbooks="per_user"``), so several PCs can save into
one OneDrive-synced SharePoint folder without writing the same Excel file;
Power Query combines them (examples/power_query/combine_users.pq).

Settings (output folder, network address, ...) are changed on the app's
Settings page and kept in ``<data>/config.json``.

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

from .settings import Settings, read_config


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "TaipowerBillOCR" / "web"


def default_output_dir() -> Path:
    return Path.home() / "Documents" / "TaipowerBillOCR"


def ask_output_dir(current: Path) -> Path | None:
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


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def _lan_address() -> str | None:
    """This PC's address on the local network, for other computers to use."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("10.255.255.255", 1))  # no packet is sent
            return probe.getsockname()[0]
        except OSError:
            return None


def main(argv: list[str] | None = None, web_dist: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taipower-web-local", description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="folder for submitted Excel files and evidence PDFs (remembered)")
    parser.add_argument("--choose-output", action="store_true", help="pick the output folder again")
    parser.add_argument("--data", type=Path, default=default_data_dir(), help="uploads, the local database and config.json")
    parser.add_argument("--port", type=int, help="port (remembered; default 8765)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    config = read_config(args.data / "config.json")
    port = args.port or int(config.get("port") or 8765)
    if not args.output and not args.choose_output and _running_here(port):
        print(f"Already running: http://127.0.0.1:{port}/")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0

    overrides: dict = {}
    if args.port:
        overrides["port"] = args.port
    if args.output:
        overrides["output_dir"] = args.output
    elif (args.choose_output or not config.get("output_dir")) and "TAIPOWER_OUTPUT_DIR" not in os.environ:
        current = Path(config.get("output_dir") or default_output_dir())
        overrides["output_dir"] = ask_output_dir(current) or current
    extra = {"web_dist": web_dist} if web_dist is not None else {}
    settings = Settings.load(
        args.data,
        defaults={"workbooks": "per_user", "output_dir": default_output_dir()},
        overrides=overrides,
        **extra,
    )
    settings.output_dir = settings.output_dir.resolve()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.save()
    if not settings.web_dist.is_dir():
        print(f"Web pages not found at {settings.web_dist}; build them with `npm run build`.", file=sys.stderr)
        return 1

    host = settings.host
    port = settings.port if _port_free(host, settings.port) else _free_port(host)
    url = f"http://127.0.0.1:{port}/"
    print("Taipower Bill OCR")
    print(f"  Open:          {url}")
    if host != "127.0.0.1":
        lan = _lan_address()
        print(f"  Other PCs:     http://{lan or '<this PC address>'}:{port}/  (anyone on the network can use it)")
    print(f"  Results go to: {settings.output_dir}")
    print("  Change settings on the Settings page (top right).")
    print("Keep this window open while you work; close it to stop.")

    import uvicorn

    from .main import create_app

    app = create_app(settings)
    app.state.running = {"host": host, "port": port}
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
