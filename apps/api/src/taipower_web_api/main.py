"""HTTP API for batch upload, review, evidence marking and export."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from taipower_bill_ocr.batch import SUPPORTED_SUFFIXES, render_page
from taipower_bill_ocr.excel_export import (
    COLUMN_LABELS_EN,
    COLUMNS,
    OTHER_ADJUSTMENT_INPUTS,
    recompute_other_adjustment,
)

from . import evidence
from .reports import export_workbook, evidence_path, write_workbooks
from .settings import Settings
from .store import Store, now
from .worker import OcrWorker

TEXT_COLUMNS = {"Billing Period 計費期間", "Custom No. 電號"}
EDITABLE_COLUMNS = [column for column in COLUMNS if column not in {"其他調整費用", "來源檔案", "頁碼", "需人工檢查"}]


class Mark(BaseModel):
    id: str
    type: str = Field(pattern="^(highlight|label)$")
    x: float
    y: float
    w: float | None = None
    h: float | None = None
    text: str | None = None
    fontSize: float | None = None
    group: str | None = None


class ItemUpdate(BaseModel):
    user: str
    row: dict[str, Any] | None = None
    marks: list[Mark] | None = None


def _clean_user(user: str) -> str:
    user = user.strip()
    if not user or len(user) > 100:
        raise HTTPException(400, "user name is required")
    return user


def _safe_name(filename: str) -> str:
    name = Path(filename).name
    return re.sub(r"[^\w.\- ()㐀-鿿]", "_", name) or "upload"


def create_app(
    settings: Settings | None = None,
    extractor_factory: Callable[[], Any] | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    worker = OcrWorker(store, settings, extractor_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Taipower Bill OCR", lifespan=lifespan)
    app.state.store, app.state.worker, app.state.settings = store, worker, settings

    def page_or_404(page_id: int) -> dict[str, Any]:
        page = store.page(page_id)
        if page is None or not page["is_bill"]:
            raise HTTPException(404, "bill not found")
        return page

    def page_image(page: dict[str, Any]):
        return render_page(Path(page["path"]), page["page_number"], settings.dpi)

    def marks_of(page: dict[str, Any]) -> list[dict[str, Any]]:
        return page["marks"] or []

    # --------------------------------------------------------------- columns
    @app.get("/api/columns")
    def columns() -> list[dict[str, Any]]:
        return [
            {
                "key": column,
                "label": COLUMN_LABELS_EN[column],
                "editable": column in EDITABLE_COLUMNS,
                "numeric": column not in TEXT_COLUMNS | {"來源檔案", "需人工檢查"},
            }
            for column in COLUMNS
        ]

    # ---------------------------------------------------------------- upload
    @app.post("/api/uploads")
    async def upload(user: str = Form(...), files: list[UploadFile] = File(...)) -> dict[str, Any]:
        user = _clean_user(user)
        accepted, rejected = [], []
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        for file in files:
            name = _safe_name(file.filename or "upload")
            if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                rejected.append({"filename": name, "reason": "unsupported file type"})
                continue
            content = await file.read()
            if not content:
                rejected.append({"filename": name, "reason": "empty file"})
                continue
            target = settings.uploads_dir / f"{uuid.uuid4().hex[:12]}_{name}"
            target.write_bytes(content)
            upload_id = store.add_upload(user, name, target, hashlib.sha256(content).hexdigest())
            worker.submit(upload_id)
            accepted.append({"id": upload_id, "filename": name})
        return {"accepted": accepted, "rejected": rejected}

    @app.get("/api/uploads")
    def uploads(user: str) -> list[dict[str, Any]]:
        return [
            {key: upload[key] for key in ("id", "filename", "status", "error", "page_count", "pages_done", "created_at")}
            for upload in store.uploads(_clean_user(user))
        ]

    # ----------------------------------------------------------------- items
    @app.get("/api/items")
    def items(user: str) -> list[dict[str, Any]]:
        result = []
        for page in store.pages(user=_clean_user(user), bills_only=True):
            row = page["row"] or {}
            result.append({
                "id": page["id"],
                "upload_id": page["upload_id"],
                "filename": page["filename"],
                "page": page["page_number"],
                "status": page["status"],
                "needs_review": bool(page["issues"]),
                "issue_count": len(page["issues"] or []),
                "billing_period": row.get("Billing Period 計費期間"),
                "customer_number": row.get("Custom No. 電號"),
                "total": row.get("應繳總金額"),
                "submitted_at": page["submitted_at"],
            })
        return result

    @app.get("/api/items/{page_id}")
    def item(page_id: int) -> dict[str, Any]:
        page = page_or_404(page_id)
        return {
            "id": page["id"],
            "user": page["user"],
            "filename": page["filename"],
            "page": page["page_number"],
            "status": page["status"],
            "row": page["row"],
            "ocr_row": page["ocr_row"],
            "issues": page["issues"],
            "alternatives": page["alternatives"],
            "locations": page["locations"],
            "marks": marks_of(page),
            "mark_notes": page["mark_notes"],
            "image": {"width": page["image_width"], "height": page["image_height"], "dpi": settings.dpi},
            "submitted_at": page["submitted_at"],
            "submitted_by": page["submitted_by"],
        }

    @app.get("/api/items/{page_id}/image")
    def image(page_id: int) -> Response:
        page = page_or_404(page_id)
        buffer = io.BytesIO()
        page_image(page).save(buffer, format="PNG", optimize=True)
        return Response(buffer.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    def apply_update(page: dict[str, Any], update: ItemUpdate) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if update.row is not None:
            row = dict(page["row"])
            for column in EDITABLE_COLUMNS:
                if column in update.row:
                    row[column] = update.row[column]
            if any(column in update.row for column in OTHER_ADJUSTMENT_INPUTS):
                recompute_other_adjustment(row)
            values["row"] = row
        if update.marks is not None:
            values["marks"] = [mark.model_dump(exclude_none=True) for mark in update.marks]
        if values:
            store.update_page(page["id"], **values)
        return store.page(page["id"])

    @app.put("/api/items/{page_id}")
    def save(page_id: int, update: ItemUpdate) -> dict[str, Any]:
        page = page_or_404(page_id)
        _clean_user(update.user)
        if page["status"] == "submitted":
            raise HTTPException(409, "already submitted; reopen it to edit")
        apply_update(page, update)
        return item(page_id)

    @app.post("/api/items/{page_id}/submit")
    def submit(page_id: int, update: ItemUpdate) -> dict[str, Any]:
        page = page_or_404(page_id)
        user = _clean_user(update.user)
        page = apply_update(page, update)
        store.update_page(page_id, status="submitted", submitted_at=now(), submitted_by=user)
        page = store.page(page_id)
        target = evidence_path(settings.output_dir, page)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            evidence.pdf_bytes(Path(page["path"]), page["page_number"], page_image(page), settings.dpi, marks_of(page))
        )
        locked = write_workbooks(store, settings.output_dir)
        return {**item(page_id), "evidence_file": str(target), "locked_workbooks": locked}

    @app.post("/api/items/{page_id}/reopen")
    def reopen(page_id: int, update: ItemUpdate) -> dict[str, Any]:
        page_or_404(page_id)
        _clean_user(update.user)
        store.update_page(page_id, status="draft", submitted_at=None, submitted_by=None)
        locked = write_workbooks(store, settings.output_dir)
        return {**item(page_id), "locked_workbooks": locked}

    @app.post("/api/items/{page_id}/reset-marks")
    def reset_marks(page_id: int) -> dict[str, Any]:
        page = page_or_404(page_id)
        if page["status"] == "submitted":
            raise HTTPException(409, "already submitted; reopen it to edit")
        locations = {
            column: evidence.FieldLocation(tuple(value["box"]), value["exact"])
            for column, value in (page["locations"] or {}).items()
        }
        printed = [(line["x"], line["y"], line["width"], line["height"]) for line in page["dataset"]["ocr_lines"]]
        regions = [
            (bounds["x"], bounds["y"], bounds["width"], bounds["height"])
            for bounds in page["dataset"].get("blocks", {}).values()
        ]
        marks, notes = evidence.default_marks(
            locations, (page["image_width"], page["image_height"]), page["row"], printed, regions
        )
        store.update_page(page_id, marks=marks, mark_notes=notes)
        return item(page_id)

    @app.get("/api/items/{page_id}/evidence.png")
    def evidence_png(page_id: int) -> Response:
        page = page_or_404(page_id)
        return Response(evidence.png_bytes(page_image(page), marks_of(page)), media_type="image/png")

    @app.get("/api/items/{page_id}/evidence.pdf")
    def evidence_pdf(page_id: int) -> Response:
        page = page_or_404(page_id)
        content = evidence.pdf_bytes(Path(page["path"]), page["page_number"], page_image(page), settings.dpi, marks_of(page))
        name = Path(evidence_path(settings.output_dir, page)).name
        return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---------------------------------------------------------------- export
    @app.get("/api/evidence.zip")
    def evidence_zip(user: str, status: str = "all") -> Response:
        """Every evidence PDF of a user's bills in one ZIP.

        Submitted bills use the PDF written at submission (exactly what was
        sent); drafts are rendered from their current marks.
        """
        if status not in {"all", "submitted"}:
            raise HTTPException(400, "status must be 'all' or 'submitted'")
        user = _clean_user(user)
        pages = list(store.pages(user=user, bills_only=True, submitted_only=status == "submitted"))
        if not pages:
            raise HTTPException(404, "no bills to download")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for page in pages:
                saved = evidence_path(settings.output_dir, page)
                if page["status"] == "submitted" and saved.is_file():
                    content = saved.read_bytes()
                else:
                    content = evidence.pdf_bytes(
                        Path(page["path"]), page["page_number"], page_image(page), settings.dpi, marks_of(page)
                    )
                folder = "submitted" if page["status"] == "submitted" else "draft"
                archive.writestr(f"{folder}/{saved.name}", content)
        name = f"evidence_{re.sub(r'[^A-Za-z0-9_-]+', '_', user) or 'user'}.zip"
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/export.xlsx")
    def export(user: str) -> Response:
        pages = list(store.pages(user=_clean_user(user), bills_only=True))
        return Response(
            export_workbook(pages),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="taipower_bills_mine.xlsx"'},
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "label_font": str(evidence.label_font_path()), "output_dir": str(settings.output_dir)}

    # The built web app is served from the same origin in production.
    if settings.web_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=settings.web_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            candidate = settings.web_dist / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(settings.web_dist / "index.html")

    return app


def run() -> None:
    import uvicorn

    uvicorn.run("taipower_web_api.main:create_app", factory=True, host="127.0.0.1", port=8765)
