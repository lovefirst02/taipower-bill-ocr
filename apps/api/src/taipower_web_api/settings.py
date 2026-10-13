"""Configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Settings:
    # Uploads, the database and rendered page images.
    data_dir: Path
    # Where submitted results go: the two workbooks and evidence PDFs. Point it
    # at a OneDrive-synced SharePoint folder to share them.
    output_dir: Path
    ocr_backend: str = "crosscheck"
    dpi: int = 200
    web_dist: Path = REPO_ROOT / "apps" / "web" / "dist"

    @property
    def database(self) -> Path:
        return self.data_dir / "taipower_web.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def report_workbook(self) -> Path:
        return self.output_dir / "taipower_bills_17.xlsx"

    @property
    def master_workbook(self) -> Path:
        return self.output_dir / "taipower_bills.xlsx"

    @property
    def evidence_dir(self) -> Path:
        return self.output_dir / "evidence"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("TAIPOWER_DATA_DIR", REPO_ROOT / "data"))
        return cls(
            data_dir=data_dir,
            output_dir=Path(os.environ.get("TAIPOWER_OUTPUT_DIR", data_dir / "output")),
            ocr_backend=os.environ.get("TAIPOWER_OCR_BACKEND", "crosscheck"),
            dpi=int(os.environ.get("TAIPOWER_DPI", "200")),
        )
