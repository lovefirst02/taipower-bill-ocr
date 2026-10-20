"""SQLite storage for uploads, bill pages, reviewers' edits and evidence marks."""

from __future__ import annotations

import json
import re
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
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,       -- a company of the group (a legal entity)
    code TEXT,                       -- e.g. TW21, as written on file names
    tax_id TEXT,                     -- 統一編號, on retailers' notices
    note TEXT
);
CREATE TABLE IF NOT EXISTS meters (
    customer_number TEXT PRIMARY KEY, -- 電號, digits only
    site_id INTEGER NOT NULL REFERENCES sites(id)
);
CREATE TABLE IF NOT EXISTS budgets (
    month TEXT NOT NULL,             -- YYYY-MM
    entity TEXT,                     -- names as in the settings
    site TEXT NOT NULL,
    values_json TEXT NOT NULL,       -- grey_kwh, grey_cost, solar_kwh, ... (None when not budgeted)
    PRIMARY KEY (month, site)
);
CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER,               -- NULL: every entity
    energy TEXT NOT NULL,            -- 太陽能, 風力, ...
    retailer TEXT,
    service TEXT,
    price REAL NOT NULL,             -- NT$/kWh before tax, as in the contract
    transfer_rate REAL NOT NULL DEFAULT 0,     -- Taipower's transfer fees charged on top, NT$/kWh
    certificate_rate REAL NOT NULL DEFAULT 0,  -- certificate fees on top, NT$/kWh
    start_month TEXT,                -- YYYY-MM; NULL: open
    end_month TEXT,
    note TEXT
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
    "green_json": "green",
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
            ("green_json", "TEXT"),  # a green power statement's period and lines
            ("entity_id", "INTEGER"),  # chosen by hand; otherwise from its site or its text
        ):
            if name not in columns:
                self._db.execute(f"ALTER TABLE pages ADD COLUMN {name} {definition}")
        if "entity_id" not in {row[1] for row in self._db.execute("PRAGMA table_info(sites)")}:
            self._db.execute("ALTER TABLE sites ADD COLUMN entity_id INTEGER")
        # The site chosen when uploading: every page of the upload is placed in it.
        upload_columns = {row[1] for row in self._db.execute("PRAGMA table_info(uploads)")}
        if "site_id" not in upload_columns:
            self._db.execute("ALTER TABLE uploads ADD COLUMN site_id INTEGER")
        # "green": uploaded as green power documents (never read as Taipower bills).
        if "kind" not in upload_columns:
            self._db.execute("ALTER TABLE uploads ADD COLUMN kind TEXT")
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
    def add_upload(self, user: str, filename: str, path: Path, sha256: str, site_id: int | None = None, kind: str | None = None) -> int:
        cursor = self._execute(
            "INSERT INTO uploads (user, filename, path, sha256, status, created_at, site_id, kind) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
            (user, filename, str(path), sha256, now(), site_id, kind),
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
                values = (site["name"], site.get("code") or None, site.get("note") or None, site.get("entity_id"))
                if site.get("id") in kept:
                    db.execute("UPDATE sites SET name = ?, code = ?, note = ?, entity_id = ? WHERE id = ?", (*values, site["id"]))
                    site_id = site["id"]
                else:
                    site_id = db.execute("INSERT INTO sites (name, code, note, entity_id) VALUES (?, ?, ?, ?)", values).lastrowid
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
        # A green power document lists meters: its site when they are all in one.
        statement = page.get("green") or {}
        numbers = {meter_number(line.get("meter")) for line in statement.get("lines") or []}
        numbers |= {meter_number(row.get("user_meter")) for row in statement.get("rows") or []}
        numbers.discard(None)
        if numbers:
            marks = ", ".join("?" for _ in numbers)
            rows = self._query(
                f"SELECT DISTINCT sites.* FROM meters JOIN sites ON sites.id = meters.site_id WHERE meters.customer_number IN ({marks})",
                tuple(numbers),
            )
            if len(rows) == 1:
                return {**dict(rows[0]), "how": "meter"}
            if len(rows) > 1:
                return {"id": None, "name": None, "code": None, "entity_id": None, "how": "several",
                        "names": sorted(row["name"] for row in rows)}
        return None

    # --------------------------------------------------------------- entities
    def entities(self) -> list[dict[str, Any]]:
        """The group's companies, by name."""
        return [dict(row) for row in self._query("SELECT * FROM entities ORDER BY name")]

    def replace_entities(self, entities: list[dict[str, Any]]) -> None:
        """Save the whole list at once; entities keep their id, so sites and documents stay with them."""
        with self._lock:
            db = self._db
            existing = {row[0] for row in db.execute("SELECT id FROM entities")}
            kept = {entity["id"] for entity in entities if entity.get("id") in existing}
            for removed in existing - kept:
                db.execute("UPDATE sites SET entity_id = NULL WHERE entity_id = ?", (removed,))
                db.execute("UPDATE pages SET entity_id = NULL WHERE entity_id = ?", (removed,))
                db.execute("DELETE FROM entities WHERE id = ?", (removed,))
            for entity_id in kept:
                db.execute("UPDATE entities SET name = ? WHERE id = ?", (f"__renaming_{entity_id}", entity_id))
            for entity in entities:
                values = (entity["name"], entity.get("code") or None, entity.get("tax_id") or None, entity.get("note") or None)
                if entity.get("id") in kept:
                    db.execute("UPDATE entities SET name = ?, code = ?, tax_id = ?, note = ? WHERE id = ?", (*values, entity["id"]))
                else:
                    db.execute("INSERT INTO entities (name, code, tax_id, note) VALUES (?, ?, ?, ?)", values)
            db.commit()

    def entity_of(self, page: dict[str, Any], site: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The company a document is for (``how``: "manual", "site" or "text").

        Chosen by hand, else its site's, else the one whose code, 統一編號 or
        name is in the file name or on the document (a retailer's notice is
        addressed to one company).
        """
        entities = {entity["id"]: entity for entity in self.entities()}
        if not entities:
            return None
        if page.get("entity_id") in entities:
            return {**entities[page["entity_id"]], "how": "manual"}
        site = site if site is not None else self.site_of(page)
        if site and site.get("entity_id") in entities:
            return {**entities[site["entity_id"]], "how": "site"}
        if page.get("kind") in (None, "taipower"):
            return None  # a bill has its 電號
        lines = (page.get("dataset") or {}).get("ocr_lines") or []
        text = re.sub(r"[\s\-_()（）]", "", " ".join([str(page.get("filename") or ""), *(str(line.get("text", "")) for line in lines)])).upper()
        flat = lambda word: re.sub(r"[\s\-_()（）]", "", str(word or "")).upper()
        # A code or 統一編號 first: a group's companies often share most of a name.
        for fields in (("code", "tax_id"), ("name",)):
            found = [entity for entity in entities.values() if any(flat(entity.get(field)) and flat(entity.get(field)) in text for field in fields)]
            if len(found) == 1:
                return {**found[0], "how": "text"}
        return None

    def meter_entities(self) -> dict[str, dict[str, Any]]:
        """Each meter's company, through its site."""
        entities = {entity["id"]: entity for entity in self.entities()}
        rows = self._query("SELECT meters.customer_number, sites.entity_id FROM meters JOIN sites ON sites.id = meters.site_id")
        return {row["customer_number"]: entities[row["entity_id"]] for row in rows if row["entity_id"] in entities}

    # ---------------------------------------------------------------- budgets
    def budgets(self) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM budgets ORDER BY month, entity, site")
        return [{"month": row["month"], "entity": row["entity"], "site": row["site"], **json.loads(row["values_json"])} for row in rows]

    def replace_budgets(self, rows: list[dict[str, Any]]) -> None:
        """An import replaces the budget of the months it has (other months stay)."""
        with self._lock:
            db = self._db
            for month in {row["month"] for row in rows}:
                db.execute("DELETE FROM budgets WHERE month = ?", (month,))
            for row in rows:
                values = {key: value for key, value in row.items() if key not in ("month", "entity", "site")}
                db.execute("INSERT OR REPLACE INTO budgets (month, entity, site, values_json) VALUES (?, ?, ?, ?)",
                           (row["month"], row.get("entity"), row["site"], json.dumps(values, ensure_ascii=False)))
            db.commit()

    def clear_budgets(self, year: str | None = None) -> None:
        self._execute("DELETE FROM budgets WHERE ? IS NULL OR month LIKE ?", (year, f"{year}-%"))

    # -------------------------------------------------------------- contracts
    def contracts(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._query("SELECT * FROM contracts ORDER BY energy, start_month, id")]

    def replace_contracts(self, contracts: list[dict[str, Any]]) -> None:
        with self._lock:
            db = self._db
            db.execute("DELETE FROM contracts")
            for contract in contracts:
                db.execute(
                    "INSERT INTO contracts (entity_id, energy, retailer, service, price, transfer_rate, certificate_rate, start_month, end_month, note) "
                    "VALUES (:entity_id, :energy, :retailer, :service, :price, :transfer_rate, :certificate_rate, :start_month, :end_month, :note)",
                    {key: contract.get(key) for key in ("entity_id", "energy", "retailer", "service", "price", "transfer_rate",
                                                        "certificate_rate", "start_month", "end_month", "note")},
                )
            db.commit()

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
        if "site_id" not in values:
            rows = self._query("SELECT site_id FROM uploads WHERE id = ?", (upload_id,))
            if rows and rows[0]["site_id"]:
                values["site_id"] = rows[0]["site_id"]
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
