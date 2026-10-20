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
    submitted_by TEXT,
    hidden INTEGER NOT NULL DEFAULT 0, -- submitted, then cleared from the user's list
    evidence_file TEXT,              -- the submitted PDF, relative to the output folder
    kind TEXT,                       -- taipower (NULL) | other: not a Taipower bill
    sources_json TEXT                -- where each value came from: keyword, pick, manual...
);
CREATE INDEX IF NOT EXISTS pages_upload ON pages(upload_id);
CREATE INDEX IF NOT EXISTS uploads_user ON uploads(user);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    code TEXT,                       -- e.g. the site's code in Intelex
    note TEXT
);
CREATE TABLE IF NOT EXISTS meters (
    customer_number TEXT PRIMARY KEY, -- 電號, digits only
    site_id INTEGER NOT NULL REFERENCES sites(id)
);
CREATE TABLE IF NOT EXISTS preferences (
    user TEXT NOT NULL,
    key TEXT NOT NULL,               -- e.g. mark_tags: the user's own evidence tags
    value_json TEXT NOT NULL,
    PRIMARY KEY (user, key)
);
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
    "sources_json": "sources",
}


def meter_number(value: Any) -> str | None:
    """A 電號 as digits only: 00-12-3456-78-9 and 00123456789 are the same meter."""
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits or None


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
        # Columns added since the first release.
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(pages)")}
        for name, definition in (
            ("hidden", "INTEGER NOT NULL DEFAULT 0"), ("evidence_file", "TEXT"), ("kind", "TEXT"), ("sources_json", "TEXT"),
            ("site_id", "INTEGER"),  # chosen by hand; otherwise found from the 電號
        ):
            if name not in columns:
                self._db.execute(f"ALTER TABLE pages ADD COLUMN {name} {definition}")
        self._db.commit()
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

    # ------------------------------------------------------------------ sites
    def sites(self) -> list[dict[str, Any]]:
        """Every site with its meters (電號), by name."""
        sites = [dict(row) for row in self._query("SELECT * FROM sites ORDER BY name")]
        meters: dict[int, list[str]] = {}
        for row in self._query("SELECT customer_number, site_id FROM meters ORDER BY customer_number"):
            meters.setdefault(row["site_id"], []).append(row["customer_number"])
        for site in sites:
            site["meters"] = meters.get(site["id"], [])
        return sites

    def replace_sites(self, sites: list[dict[str, Any]]) -> None:
        """Save the whole site list at once (as edited in the settings).

        Sites keep their id, so bills placed in a site by hand stay there;
        a removed site's bills go back to being found by their 電號.
        """
        with self._lock:
            db = self._db
            existing = {row[0] for row in db.execute("SELECT id FROM sites")}
            kept = {site["id"] for site in sites if site.get("id") in existing}
            for removed in existing - kept:
                db.execute("UPDATE pages SET site_id = NULL WHERE site_id = ?", (removed,))
                db.execute("DELETE FROM sites WHERE id = ?", (removed,))
            db.execute("DELETE FROM meters")
            # Free the names first: two sites may swap names in one save.
            for site_id in kept:
                db.execute("UPDATE sites SET name = ? WHERE id = ?", (f"__renaming_{site_id}", site_id))
            for site in sites:
                values = (site["name"], site.get("code") or None, site.get("note") or None)
                if site.get("id") in kept:
                    db.execute("UPDATE sites SET name = ?, code = ?, note = ? WHERE id = ?", (*values, site["id"]))
                    site_id = site["id"]
                else:
                    site_id = db.execute("INSERT INTO sites (name, code, note) VALUES (?, ?, ?)", values).lastrowid
                for number in site.get("meters") or []:
                    db.execute("INSERT INTO meters (customer_number, site_id) VALUES (?, ?)", (number, site_id))
            db.commit()

    def site_of(self, page: dict[str, Any]) -> dict[str, Any] | None:
        """The page's site: chosen by hand, else the one its 電號 belongs to.

        The result says which (``how``: "manual" or "meter").
        """
        if page.get("site_id"):
            rows = self._query("SELECT * FROM sites WHERE id = ?", (page["site_id"],))
            if rows:
                return {**dict(rows[0]), "how": "manual"}
        number = meter_number((page.get("row") or {}).get("Custom No. 電號"))
        if number:
            rows = self._query(
                "SELECT sites.* FROM meters JOIN sites ON sites.id = meters.site_id WHERE meters.customer_number = ?",
                (number,),
            )
            if rows:
                return {**dict(rows[0]), "how": "meter"}
        return None

    # ------------------------------------------------------------ preferences
    def preference(self, user: str, key: str) -> Any:
        rows = self._query("SELECT value_json FROM preferences WHERE user = ? AND key = ?", (user, key))
        return json.loads(rows[0]["value_json"]) if rows else None

    def set_preference(self, user: str, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO preferences (user, key, value_json) VALUES (?, ?, ?) "
            "ON CONFLICT(user, key) DO UPDATE SET value_json = excluded.value_json",
            (user, key, json.dumps(value, ensure_ascii=False)),
        )

    def clear_preference(self, user: str, key: str) -> None:
        self._execute("DELETE FROM preferences WHERE user = ? AND key = ?", (user, key))

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

    def pages(
        self,
        *,
        user: str | None = None,
        bills_only: bool = False,
        submitted_only: bool = False,
        visible_only: bool = False,
    ) -> Iterator[dict[str, Any]]:
        conditions, params = [], []
        if visible_only:
            conditions.append("pages.hidden = 0")
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

    def delete_page(self, page_id: int) -> None:
        self._execute("DELETE FROM pages WHERE id = ?", (page_id,))

    def bill_count(self, upload_id: int) -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM pages WHERE upload_id = ? AND is_bill = 1", (upload_id,))
        return int(rows[0]["n"])

    def item_pages(self, page: dict[str, Any]) -> list[dict[str, Any]]:
        """Every page that belongs to a bill page, in the document's order.

        Its back pages, and for the upload's first bill also the pages
        before it (a cover or a letter), so no page of an upload is left out.
        """
        if page["page_number"] is None:
            return [page]
        rows = self._query(
            "SELECT pages.*, uploads.user, uploads.filename, uploads.path FROM pages "
            "JOIN uploads ON uploads.id = pages.upload_id "
            "WHERE pages.upload_id = ? AND pages.page_number < ? ORDER BY pages.page_number",
            (page["upload_id"], page["page_number"]),
        )
        before = [_decode(row) for row in rows]
        leading = [] if any(other["is_bill"] for other in before) else before
        return [*leading, page, *self.companion_pages(page)]

    def companion_pages(self, page: dict[str, Any]) -> list[dict[str, Any]]:
        """The pages after a bill page in its PDF, up to the next bill (its back pages)."""
        if page["page_number"] is None:
            return []
        rows = self._query(
            "SELECT pages.*, uploads.user, uploads.filename, uploads.path FROM pages "
            "JOIN uploads ON uploads.id = pages.upload_id "
            "WHERE pages.upload_id = ? AND pages.page_number > ? ORDER BY pages.page_number",
            (page["upload_id"], page["page_number"]),
        )
        companions = []
        for row in rows:
            other = _decode(row)
            if other["is_bill"]:
                break
            companions.append(other)
        return companions

    def update_page(self, page_id: int, **values: Any) -> None:
        record = _encode({**values, "updated_at": now()})
        assignments = ", ".join(f"{key} = :{key}" for key in record)
        self._execute(f"UPDATE pages SET {assignments} WHERE id = :id", {**record, "id": page_id})
