"""Configuration: defaults, then ``<data dir>/config.json``, then environment variables.

The settings page edits ``config.json``. A value set by an environment
variable wins at every start, so it is shown locked there.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[4]

WORKBOOK_MODES = ("shared", "per_user")
OCR_BACKENDS = ("crosscheck", "paddleocr", "windows")

# Editable settings: the environment variable that locks each, and its parser.
EDITABLE: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "output_dir": ("TAIPOWER_OUTPUT_DIR", lambda value: Path(str(value).strip()).expanduser()),
    "workbooks": ("TAIPOWER_WORKBOOKS", str),
    "ocr_backend": ("TAIPOWER_OCR_BACKEND", str),
    "workbook_retry_seconds": ("TAIPOWER_WORKBOOK_RETRY_SECONDS", float),
    "host": ("TAIPOWER_HOST", lambda value: str(value).strip()),
    "port": ("TAIPOWER_PORT", int),
    "highlight_color": ("TAIPOWER_HIGHLIGHT_COLOR", lambda value: str(value).strip().lower()),
    "label_color": ("TAIPOWER_LABEL_COLOR", lambda value: str(value).strip().lower()),
}
COLOR = re.compile(r"^#[0-9a-f]{6}$")
# Used only when the server starts.
RESTART_REQUIRED = {"host", "port"}


@dataclass
class Settings:
    # Uploads, the database and rendered page images.
    data_dir: Path
    # Where submitted results go: the two workbooks and evidence PDFs. Point it
    # at a OneDrive-synced SharePoint folder to share them.
    output_dir: Path
    ocr_backend: str = "crosscheck"
    # Not editable on the page: marks are stored in pixels at this resolution.
    dpi: int = 200
    # How often a workbook left open in Excel is retried.
    workbook_retry_seconds: float = 60
    # "shared": one pair of workbooks for everyone (a server writes them).
    # "per_user": each user's own pair in <output>/<user>/, for copies of the
    # app running on several PCs that all save into one SharePoint folder.
    workbooks: str = "shared"
    # 127.0.0.1: this PC only; 0.0.0.0: every computer on the network.
    host: str = "127.0.0.1"
    port: int = 8765
    # Evidence marks without a colour of their own: highlighter and red pen.
    highlight_color: str = "#ffeb3b"
    label_color: str = "#d32f2f"
    web_dist: Path = REPO_ROOT / "apps" / "web" / "dist"
    # Settings fixed by environment variables.
    locked: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.workbooks not in WORKBOOK_MODES:
            raise ValueError("workbooks must be 'shared' or 'per_user'")
        if self.ocr_backend not in OCR_BACKENDS:
            raise ValueError(f"OCR engine must be one of {', '.join(OCR_BACKENDS)}")
        if not 1 <= self.workbook_retry_seconds <= 3600:
            raise ValueError("retry interval must be between 1 and 3600 seconds")
        if not 1024 <= self.port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        if not self.host:
            raise ValueError("host is required")
        if not str(self.output_dir).strip() or str(self.output_dir) == ".":
            raise ValueError("output folder is required")
        for name in ("highlight_color", "label_color"):
            if not COLOR.match(getattr(self, name)):
                raise ValueError(f"{name.replace('_', ' ')} must look like #ffeb3b")

    @property
    def database(self) -> Path:
        return self.data_dir / "taipower_web.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def report_workbook(self) -> Path:
        return self.output_dir / "taipower_bills_17.xlsx"

    @property
    def master_workbook(self) -> Path:
        return self.output_dir / "taipower_bills.xlsx"

    @property
    def evidence_dir(self) -> Path:
        return self.output_dir / "evidence"

    @property
    def per_user_workbooks(self) -> bool:
        return self.workbooks == "per_user"

    def editable(self) -> dict[str, Any]:
        return {key: str(getattr(self, key)) if key == "output_dir" else getattr(self, key) for key in EDITABLE}

    def update(self, values: Mapping[str, Any]) -> set[str]:
        """Apply edits from the settings page; returns the keys that changed.

        Nothing changes when a value is invalid or locked.
        """
        parsed = {}
        for key, value in values.items():
            if key not in EDITABLE:
                raise ValueError(f"unknown setting: {key}")
            try:
                parsed[key] = EDITABLE[key][1](value)
            except (TypeError, ValueError):
                raise ValueError(f"invalid value for {key}") from None
        changed = {key for key, value in parsed.items() if value != getattr(self, key)}
        if changed & self.locked:
            raise ValueError(f"set by an environment variable: {', '.join(sorted(changed & self.locked))}")
        previous = {key: getattr(self, key) for key in changed}
        for key in changed:
            setattr(self, key, parsed[key])
        try:
            self.validate()
        except ValueError:
            for key, value in previous.items():
                setattr(self, key, value)
            raise
        return changed

    def save(self) -> None:
        """Write the editable settings (not the locked ones) to config.json."""
        config = read_config(self.config_file)
        for key, value in self.editable().items():
            if key not in self.locked:
                config[key] = value
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(
        cls,
        data_dir: Path | None = None,
        defaults: Mapping[str, Any] | None = None,
        overrides: Mapping[str, Any] | None = None,
        **fixed: Any,
    ) -> "Settings":
        """Defaults, then config.json, then ``overrides`` (command line), then the environment."""
        data_dir = Path(data_dir or os.environ.get("TAIPOWER_DATA_DIR") or REPO_ROOT / "data")
        values: dict[str, Any] = {"output_dir": data_dir / "output", **(defaults or {})}
        config = read_config(data_dir / "config.json")
        values.update({key: value for key, value in config.items() if key in EDITABLE})
        values.update(overrides or {})
        locked = set()
        for key, (variable, _) in EDITABLE.items():
            if variable in os.environ:
                values[key] = os.environ[variable]
                locked.add(key)
        values = {key: EDITABLE[key][1](value) for key, value in values.items()}
        dpi = int(os.environ.get("TAIPOWER_DPI", "200"))
        return cls(data_dir=data_dir, dpi=dpi, locked=frozenset(locked), **values, **fixed)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.load()


def read_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}
