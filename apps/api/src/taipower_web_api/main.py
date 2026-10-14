"""HTTP API for batch upload, review, evidence marking and export."""

from __future__ import annotations

import hashlib
import io
import re
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
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
from .reports import (
    WorkbookWriter,
    archive_evidence,
    download_name,
    evidence_folder,
    evidence_path,
    export_workbook,
    new_evidence_path,
    workbook_dir,
)
from .settings import OCR_BACKENDS, RESTART_REQUIRED, WORKBOOK_MODES, Settings
from .store import Store, now
from .worker import OcrWorker, usage_marks_for

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
    # Another page of the PDF (the bill's back page); None for the bill page.
    page: int | None = None
    # "#rrggbb"; None follows the default colours in the settings.
    color: str | None = Field(default=None, pattern="^#[0-9a-fA-F]{6}$")


class ItemUpdate(BaseModel):
    user: str
    row: dict[str, Any] | None = None
    marks: list[Mark] | None = None


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class ItemDelete(BaseModel):
    user: str
    ids: list[int]


def _clean_user(user: str) -> str:
    user = user.strip()
    if not user or len(user) > 100:
        raise HTTPException(400, "user name is required")
    return user


def _attachment(name: str) -> dict[str, str]:
    """A download header for any file name.

    Header values must be Latin-1, so a name with Chinese characters is sent
    UTF-8 encoded (RFC 6266 ``filename*``) with an ASCII fallback.
    """
    from urllib.parse import quote

    stem, suffix = Path(name).stem, Path(name).suffix
    fallback = (re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "download") + suffix
    return {"Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name)}"}


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
    writer = WorkbookWriter(store, settings.output_dir, settings.workbook_retry_seconds, settings.per_user_workbooks)
    # Choosing a free evidence file name and writing it is one step.
    evidence_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_worker:
            worker.start()
        writer.start()
        yield
        worker.stop()
        writer.stop()

    app = FastAPI(title="Taipower Bill OCR", lifespan=lifespan)
    app.state.store, app.state.worker, app.state.settings = store, worker, settings
    app.state.writer = writer
    # The address the server was started on (set by run() and the launcher).
    app.state.running = None

    def page_or_404(page_id: int) -> dict[str, Any]:
        page = store.page(page_id)
        if page is None or not page["is_bill"]:
            raise HTTPException(404, "bill not found")
        return page

    def page_image(page: dict[str, Any]):
        return render_page(Path(page["path"]), page["page_number"], settings.dpi)

    def marks_of(page: dict[str, Any]) -> list[dict[str, Any]]:
        return page["marks"] or []

    def view_pages(page: dict[str, Any]) -> list[dict[str, Any]]:
        """The bill page and its back pages, which can also hold marks."""
        return [page, *store.companion_pages(page)]

    def view_page(page: dict[str, Any], number: int | None) -> dict[str, Any]:
        if number is None or number == page["page_number"]:
            return page
        found = next((other for other in store.companion_pages(page) if other["page_number"] == number), None)
        if found is None:
            raise HTTPException(404, "page not found")
        return found

    def marks_on(page: dict[str, Any], shown: dict[str, Any]) -> list[dict[str, Any]]:
        """The bill's marks that sit on ``shown`` (the bill page or a back page), in colour."""
        if shown["id"] == page["id"]:
            marks = [mark for mark in marks_of(page) if mark.get("page") in (None, page["page_number"])]
        else:
            marks = [mark for mark in marks_of(page) if mark.get("page") == shown["page_number"]]
        return evidence.with_colors(marks, settings.highlight_color, settings.label_color)

    def build_evidence_pdf(page: dict[str, Any]) -> bytes:
        """The bill page, then each back page that has marks."""
        pages = []
        for shown in view_pages(page):
            marks = marks_on(page, shown)
            if shown["id"] == page["id"] or marks:
                pages.append((shown["page_number"], page_image(shown), marks))
        return evidence.evidence_pdf(Path(page["path"]), pages, settings.dpi)

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

    def own_upload(upload_id: int, user: str) -> dict[str, Any]:
        found = store.upload(upload_id)
        if found is None or found["user"] != _clean_user(user):
            raise HTTPException(404, "upload not found")
        return found

    @app.post("/api/uploads/pause")
    def pause(update: ItemUpdate) -> dict[str, Any]:
        """Pause the user's uploads; the one being read stops after its current page."""
        return {"paused": worker.pause(_clean_user(update.user))}

    @app.post("/api/uploads/resume")
    def resume(update: ItemUpdate) -> dict[str, Any]:
        return {"resumed": worker.resume(_clean_user(update.user))}

    @app.post("/api/uploads/{upload_id}/retry")
    def retry(upload_id: int, update: ItemUpdate) -> dict[str, Any]:
        found = own_upload(upload_id, update.user)
        if found["status"] != "failed":
            raise HTTPException(409, "only a failed upload can be retried")
        worker.retry(upload_id)
        return {"id": upload_id, "status": "queued"}

    def drop_upload(found: dict[str, Any]) -> None:
        store.delete_upload(found["id"])
        Path(found["path"]).unlink(missing_ok=True)

    @app.delete("/api/uploads/{upload_id}")
    def remove(upload_id: int, user: str) -> dict[str, Any]:
        """Remove an upload that is waiting, paused or failed, with its pages."""
        found = own_upload(upload_id, user)
        if found["status"] == "processing" or worker.is_current(upload_id):
            raise HTTPException(409, "it is being read; pause first")
        if found["status"] == "done":
            raise HTTPException(409, "finished uploads stay with their bills")
        drop_upload(found)
        return {"removed": upload_id}

    @app.post("/api/uploads/remove")
    def remove_all(update: ItemUpdate) -> dict[str, Any]:
        """Remove every waiting, paused and failed upload of the user (not the one being read)."""
        removed = 0
        for found in store.uploads(_clean_user(update.user)):
            if found["status"] in {"queued", "paused", "failed"} and not worker.is_current(found["id"]):
                drop_upload(found)
                removed += 1
        return {"removed": removed}

    @app.post("/api/items/delete")
    def delete_items(request: ItemDelete) -> dict[str, Any]:
        """Delete drafts, and clear submitted bills from the user's list.

        A draft goes with its back pages (and its file, once no bill of the
        upload is left). A submitted bill stays in the Excel files and keeps
        its evidence PDF; it is only hidden. Reopen it first to delete it.
        """
        user = _clean_user(request.user)
        deleted, hidden = [], []
        for page_id in request.ids:
            page = store.page(page_id)
            if page is None or not page["is_bill"] or page["user"] != user:
                continue
            upload = store.upload(page["upload_id"])
            if upload["status"] in {"queued", "processing"} or worker.is_current(upload["id"]):
                raise HTTPException(409, f"{upload['filename']} is still being read; pause it first")
            if page["status"] == "submitted":
                store.update_page(page_id, hidden=1)
                hidden.append(page_id)
                continue
            for back in store.companion_pages(page):
                store.delete_page(back["id"])
            store.delete_page(page_id)
            if not store.bill_count(upload["id"]):
                drop_upload(upload)
            deleted.append(page_id)
        return {"deleted": deleted, "hidden": hidden}

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
        for page in store.pages(user=_clean_user(user), bills_only=True, visible_only=True):
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
            "colors": {"highlight": settings.highlight_color, "label": settings.label_color},
            "pages": [
                {"page": shown["page_number"], "width": shown["image_width"], "height": shown["image_height"], "bill": shown["id"] == page["id"]}
                for shown in view_pages(page)
            ],
            "submitted_at": page["submitted_at"],
            "submitted_by": page["submitted_by"],
        }

    @app.get("/api/items/{page_id}/image")
    def image(page_id: int, page: int | None = None) -> Response:
        shown = view_page(page_or_404(page_id), page)
        buffer = io.BytesIO()
        page_image(shown).save(buffer, format="PNG", optimize=True)
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
        content = build_evidence_pdf(page)
        with evidence_lock:
            target = new_evidence_path(settings.output_dir, page)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        store.update_page(page_id, evidence_file=target.relative_to(settings.output_dir).as_posix())
        locked = writer.write(page["user"])
        return {**item(page_id), "evidence_file": str(target), "locked_workbooks": locked}

    @app.post("/api/items/{page_id}/reopen")
    def reopen(page_id: int, update: ItemUpdate) -> dict[str, Any]:
        page = page_or_404(page_id)
        _clean_user(update.user)
        archived = None
        if page["status"] == "submitted":
            try:
                archived = archive_evidence(settings.output_dir, page)
            except PermissionError:
                raise HTTPException(409, "the evidence PDF is open in another program; close it and try again")
        store.update_page(page_id, status="draft", submitted_at=None, submitted_by=None, evidence_file=None)
        locked = writer.write(page["user"])
        return {**item(page_id), "locked_workbooks": locked, "archived_evidence": str(archived) if archived else None}

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
        usage, usage_notes = usage_marks_for(store, page)
        marks, notes = marks + usage, notes + usage_notes
        store.update_page(page_id, marks=marks, mark_notes=notes)
        return item(page_id)

    @app.get("/api/items/{page_id}/evidence.png")
    def evidence_png(page_id: int, page: int | None = None) -> Response:
        bill = page_or_404(page_id)
        shown = view_page(bill, page)
        return Response(evidence.png_bytes(page_image(shown), marks_on(bill, shown)), media_type="image/png")

    @app.get("/api/items/{page_id}/evidence.pdf")
    def evidence_pdf(page_id: int) -> Response:
        page = page_or_404(page_id)
        return Response(build_evidence_pdf(page), media_type="application/pdf", headers=_attachment(download_name(page)))

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
        pages = list(store.pages(user=user, bills_only=True, submitted_only=status == "submitted", visible_only=True))
        if not pages:
            raise HTTPException(404, "no bills to download")
        buffer = io.BytesIO()
        used: set[str] = set()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for page in pages:
                saved = evidence_path(settings.output_dir, page)
                if page["status"] == "submitted" and saved.is_file():
                    content = saved.read_bytes()
                else:
                    content = build_evidence_pdf(page)
                folder = "submitted" if page["status"] == "submitted" else "draft"
                # The uploaded file's own name; bills from one PDF (or files
                # uploaded twice) are numbered like Windows does: "name (2)".
                stem = Path(download_name(page)).stem
                entry, copy = f"{folder}/{stem}.pdf", 1
                while entry in used:
                    copy += 1
                    entry = f"{folder}/{stem} ({copy}).pdf"
                used.add(entry)
                archive.writestr(entry, content)
        name = f"evidence_{re.sub(r'[^A-Za-z0-9_-]+', '_', user) or 'user'}.zip"
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers=_attachment(name),
        )

    @app.get("/api/export.xlsx")
    def export(user: str) -> Response:
        pages = list(store.pages(user=_clean_user(user), bills_only=True, visible_only=True))
        return Response(
            export_workbook(pages),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=_attachment("taipower_bills_mine.xlsx"),
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "label_font": str(evidence.label_font_path()),
            "output_dir": str(settings.output_dir),
            "workbooks": settings.workbooks,
        }

    # -------------------------------------------------------------- settings
    def is_local(request: Request) -> bool:
        """Settings change only from the PC running the server: there is no login."""
        return request.client is not None and request.client.host in {"127.0.0.1", "::1"}

    def settings_view(request: Request) -> dict[str, Any]:
        return {
            "values": settings.editable(),
            "locked": sorted(settings.locked),
            "can_edit": is_local(request),
            "restart_required": sorted(RESTART_REQUIRED),
            "running": app.state.running,
            "options": {"workbooks": list(WORKBOOK_MODES), "ocr_backend": list(OCR_BACKENDS)},
            "dpi": settings.dpi,
            "data_dir": str(settings.data_dir),
        }

    @app.get("/api/settings")
    def get_settings(request: Request) -> dict[str, Any]:
        return settings_view(request)

    @app.put("/api/settings")
    def put_settings(update: SettingsUpdate, request: Request) -> dict[str, Any]:
        """Save settings; everything but the network address applies at once."""
        if not is_local(request):
            raise HTTPException(403, "settings can only be changed on the computer running the server")
        old_output = settings.output_dir
        try:
            changed = settings.update(update.values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if "output_dir" in changed:
            try:
                settings.output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                settings.update({"output_dir": str(old_output)})
                raise HTTPException(400, f"cannot use that folder: {exc}") from None
            # Bills already submitted keep their evidence PDFs where they are.
            for page in store.pages(bills_only=True, submitted_only=True):
                saved = evidence_path(old_output, page)
                if not Path(page.get("evidence_file") or "").is_absolute():
                    store.update_page(page["id"], evidence_file=str(saved))
        settings.save()
        writer.output_dir = settings.output_dir
        writer.per_user = settings.per_user_workbooks
        writer.retry_seconds = settings.workbook_retry_seconds
        if "ocr_backend" in changed:
            worker.reload_engine()
        locked = writer.write_all() if changed & {"output_dir", "workbooks"} else []
        return {
            **settings_view(request),
            "changed": sorted(changed),
            "restart": bool(changed & RESTART_REQUIRED),
            "locked_workbooks": locked,
        }

    @app.post("/api/settings/browse")
    def browse(request: Request) -> dict[str, Any]:
        """A folder picker on the server's screen (it is this PC: settings are local only)."""
        if not is_local(request):
            raise HTTPException(403, "settings can only be changed on the computer running the server")
        from .desktop import ask_output_dir

        chosen = ask_output_dir(settings.output_dir)
        return {"folder": str(chosen) if chosen else None}

    @app.get("/api/output")
    def output(user: str) -> dict[str, Any]:
        """Where this user's submitted results are saved."""
        user = _clean_user(user)
        folder = workbook_dir(settings.output_dir, user if settings.per_user_workbooks else None)
        return {
            "workbooks": settings.workbooks,
            "folder": str(folder),
            "evidence_folder": str(evidence_folder(settings.output_dir, user)),
        }

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
    """The server, on the address in the settings (127.0.0.1 unless changed)."""
    import uvicorn

    settings = Settings.from_env()
    app = create_app(settings)
    app.state.running = {"host": settings.host, "port": settings.port}
    uvicorn.run(app, host=settings.host, port=settings.port)
