"""SQLite storage for uploads, bill pages, reviewers' edits and evidence marks."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY,
    user TEXT NOT NULL,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued | processing | paused | done | failed
    error TEXT,
    page_count INTEGER,
    pages_done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id),
    page_number INTEGER,             -- NULL for image uploads
    is_bill INTEGER NOT NULL,        -- has a charge breakdown: shown for review
    status TEXT NOT NULL,            -- draft | submitted
    row_json TEXT,                   -- current 17-column values (reviewer edits applied)
    ocr_row_json TEXT,               -- 17-column values as OCR read them
    issues_json TEXT,
    alternatives_json TEXT,
    locations_json TEXT,
    marks_json TEXT,
    mark_notes_json TEXT,
    dataset_json TEXT NOT NULL,      -- every field read from the page (PageRecord)
    image_width INTEGER NOT NULL,
    image_height INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    submitted_by TEXT
);
CREATE INDEX IF NOT EXISTS pages_upload ON pages(upload_id);
CREATE INDEX IF NOT EXISTS uploads_user ON uploads(user);
"""

_JSON_COLUMNS = {
    "row_json": "row",
    "ocr_row_json": "ocr_row",
    "issues_json": "issues",
    "alternatives_json": "alternatives",
    "locations_json": "locations",
    "marks_json": "marks",
    "mark_notes_json": "mark_notes",
    "dataset_json": "dataset",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    for column, name in _JSON_COLUMNS.items():
        if column in record:
            raw = record.pop(column)
            record[name] = json.loads(raw) if raw else None
    return record


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    reverse = {name: column for column, name in _JSON_COLUMNS.items()}
    return {
        reverse.get(key, key): json.dumps(value, ensure_ascii=False) if key in reverse else value
        for key, value in values.items()
    }


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._db.execute(sql, params)
            self._db.commit()
            return cursor

    def _query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    # ---------------------------------------------------------------- uploads
    def add_upload(self, user: str, filename: str, path: Path, sha256: str) -> int:
        cursor = self._execute(
            "INSERT INTO uploads (user, filename, path, sha256, status, created_at) VALUES (?, ?, ?, ?, 'queued', ?)",
            (user, filename, str(path), sha256, now()),
        )
        return int(cursor.lastrowid)

    def upload(self, upload_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM uploads WHERE id = ?", (upload_id,))
        return dict(rows[0]) if rows else None

    def uploads(self, user: str | None = None) -> list[dict[str, Any]]:
        if user is None:
            rows = self._query("SELECT * FROM uploads ORDER BY id DESC")
        else:
            rows = self._query("SELECT * FROM uploads WHERE user = ? ORDER BY id DESC", (user,))
        return [dict(row) for row in rows]

    def pending_upload_ids(self) -> list[int]:
        rows = self._query("SELECT id FROM uploads WHERE status IN ('queued', 'processing') ORDER BY id")
        return [row["id"] for row in rows]

    def delete_upload(self, upload_id: int) -> None:
        self.delete_pages(upload_id)
        self._execute("DELETE FROM uploads WHERE id = ?", (upload_id,))

    def set_upload(self, upload_id: int, **values: Any) -> None:
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        self._execute(f"UPDATE uploads SET {assignments} WHERE id = :id", {**values, "id": upload_id})

    # ------------------------------------------------------------------ pages
    def add_page(self, upload_id: int, **values: Any) -> int:
        record = _encode({**values, "upload_id": upload_id, "updated_at": now()})
        columns = ", ".join(record)
        placeholders = ", ".join(f":{key}" for key in record)
        cursor = self._execute(f"INSERT INTO pages ({columns}) VALUES ({placeholders})", record)
        return int(cursor.lastrowid)

    def delete_pages(self, upload_id: int) -> None:
        self._execute("DELETE FROM pages WHERE upload_id = ?", (upload_id,))

    def page_numbers(self, upload_id: int) -> set[int | None]:
        """Pages of an upload already read (None for an image)."""
        rows = self._query("SELECT page_number FROM pages WHERE upload_id = ?", (upload_id,))
        return {row["page_number"] for row in rows}

    def page(self, page_id: int) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT pages.*, uploads.user, uploads.filename, uploads.path FROM pages "
            "JOIN uploads ON uploads.id = pages.upload_id WHERE pages.id = ?",
            (page_id,),
        )
        return _decode(rows[0]) if rows else None

    def pages(self, *, user: str | None = None, bills_only: bool = False, submitted_only: bool = False) -> Iterator[dict[str, Any]]:
        conditions, params = [], []
        if user is not None:
            conditions.append("uploads.user = ?")
            params.append(user)
        if bills_only:
            conditions.append("pages.is_bill = 1")
        if submitted_only:
            conditions.append("pages.status = 'submitted'")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._query(
            "SELECT pages.*, uploads.user, uploads.filename, uploads.path FROM pages "
            f"JOIN uploads ON uploads.id = pages.upload_id {where} "
            "ORDER BY uploads.id, pages.page_number",
            tuple(params),
        )
        for row in rows:
            yield _decode(row)

    def update_page(self, page_id: int, **values: Any) -> None:
        record = _encode({**values, "updated_at": now()})
        assignments = ", ".join(f"{key} = :{key}" for key in record)
        self._execute(f"UPDATE pages SET {assignments} WHERE id = :id", {**record, "id": page_id})
