"""Background OCR: one upload at a time, loading the OCR model only once.

Users can pause their own uploads: queued ones wait, and the one being read
stops after its current page. Pages already read are kept, so resuming (or
retrying a failed upload, or restarting the server) reads only the rest.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from taipower_bill_ocr.batch import (
    PDF_SUFFIXES,
    FileFinished,
    FileStarted,
    PageDone,
    PageExtractor,
    count_pages,
    iter_batch,
    render_page,
)
from taipower_bill_ocr.dataset import page_record

from . import documents
from .evidence import USAGE_LABEL, current_usage_kwh, default_marks, is_usage_mark, usage_marks
from .settings import Settings
from .store import Store

log = logging.getLogger("taipower_web_api.worker")


def usage_marks_for(store: Store, page: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], Any]:
    """Marks for 本期 usage on the bill's back page, and the kWh read there.

    節電資訊 is printed on the back (page 2), never on the bill page, so the
    bill page is not searched: a 本期 there would be the wrong thing to mark.
    """
    for back in store.companion_pages(page):
        marks, notes = usage_marks(
            back["dataset"], (back["image_width"], back["image_height"]), back["page_number"]
        )
        if marks:
            return marks, notes, current_usage_kwh(back["dataset"])
    return [], [f"{USAGE_LABEL}: 本期度數 (節電資訊) not found on the back page, add the mark by hand"], None


class OcrWorker:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        extractor_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self._extractor_factory = extractor_factory or (
            lambda: PageExtractor(settings.ocr_backend, settings.dpi)
        )
        self._extractor: Any | None = None
        # Set when the OCR engine setting changes: rebuilt before the next upload.
        self._reload = False
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="ocr-worker", daemon=True)
        # The upload being read, and the flag that stops it between pages.
        self._current: int | None = None
        self._cancel = threading.Event()
        self._state = threading.Lock()

    def start(self) -> None:
        # Uploads interrupted by a restart are processed again.
        for upload_id in self.store.pending_upload_ids():
            self._queue.put(upload_id)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)

    def submit(self, upload_id: int) -> None:
        self._queue.put(upload_id)

    def pause(self, user: str) -> int:
        """Pause a user's waiting uploads and stop theirs being read; returns how many."""
        paused = 0
        with self._state:
            for upload in self.store.uploads(user):
                if upload["status"] == "queued":
                    self.store.set_upload(upload["id"], status="paused")
                    paused += 1
                elif upload["status"] == "processing" and upload["id"] == self._current:
                    self._cancel.set()  # marked paused once the current page is stored
                    paused += 1
        return paused

    def resume(self, user: str) -> int:
        """Queue a user's paused uploads again; returns how many."""
        resumed = 0
        with self._state:
            for upload in reversed(self.store.uploads(user)):  # oldest first
                if upload["status"] == "paused":
                    self.store.set_upload(upload["id"], status="queued")
                    self._queue.put(upload["id"])
                    resumed += 1
        return resumed

    def retry(self, upload_id: int) -> None:
        with self._state:
            self.store.set_upload(upload_id, status="queued", error=None)
            self._queue.put(upload_id)

    def reload_engine(self) -> None:
        """Use the current OCR engine setting from the next upload on."""
        self._reload = True

    def is_current(self, upload_id: int) -> bool:
        return self._current == upload_id

    def wait_idle(self, timeout: float = 600) -> None:
        """Block until queued uploads are done (used by tests and scripts)."""
        done = threading.Event()
        self._idle_marker = done  # set before the marker can be dequeued
        self._queue.put(-1)
        done.wait(timeout)

    @property
    def extractor(self) -> Any:
        if self._extractor is None:
            self._extractor = self._extractor_factory()
            if getattr(self._extractor, "uses_paddle", False):
                self._extractor._engine()
        return self._extractor

    def _run(self) -> None:
        while True:
            upload_id = self._queue.get()
            if upload_id is None:
                return
            if upload_id == -1:
                self._idle_marker.set()
                continue
            with self._state:
                upload = self.store.upload(upload_id)
                # Paused, removed, or already read (queued twice).
                if upload is None or upload["status"] not in {"queued", "processing"}:
                    continue
                self._current = upload_id
                self._cancel.clear()
                if self._reload:
                    self._extractor, self._reload = None, False
            try:
                self.process(upload_id)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                log.exception("Upload %s failed", upload_id)
                self.store.set_upload(upload_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                with self._state:
                    self._current = None

    def process(self, upload_id: int) -> None:
        upload = self.store.upload(upload_id)
        if upload is None:
            return
        path = Path(upload["path"])
        # Pages read before a pause, failure or restart are kept.
        read = self.store.page_numbers(upload_id)
        done = len(read)
        self.store.set_upload(upload_id, status="processing", error=None, pages_done=done)
        remaining: list[int] | None = None
        if read and path.suffix.lower() in PDF_SUFFIXES:
            remaining = [number for number in range(1, count_pages(path) + 1) if number not in read]
        if read and not remaining:
            self.store.set_upload(upload_id, status="done", processed_at=datetime.now().isoformat(timespec="seconds"))
            return
        errors: list[str] = []
        for event in iter_batch(
            [path], self.extractor, self._cancel,
            source_name=lambda _: upload["filename"],
            page_numbers=remaining,
            cross_check_all_pages=True,
        ):
            if isinstance(event, FileStarted):
                self.store.set_upload(upload_id, page_count=event.page_count)
            elif isinstance(event, PageDone) and event.bill is not None:
                self._store_page(upload_id, upload["filename"], path, event)
                done += 1
                self.store.set_upload(upload_id, pages_done=done)
            elif isinstance(event, FileFinished) and event.error:
                errors.append(event.error)
        if self._cancel.is_set():
            self.store.set_upload(upload_id, status="paused")
            return
        if not self.store.bill_count(upload_id):
            self._add_document_item(upload_id)
        self._add_usage_marks(upload_id)
        self.store.set_upload(
            upload_id,
            status="failed" if errors else "done",
            error="; ".join(errors) or None,
            processed_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _add_document_item(self, upload_id: int) -> None:
        """No Taipower bill in the upload: review its first page as another kind of document.

        Every PDF and image is accepted; what keywords do not find is picked
        on the page or typed in by the reviewer.
        """
        pages = sorted(
            (page for page in self.store.pages() if page["upload_id"] == upload_id),
            key=lambda page: page["page_number"] or 0,
        )
        if not pages:
            return
        first = pages[0]
        self.store.update_page(first["id"], **documents.detect(self.store, first, self.settings))

    def _add_usage_marks(self, upload_id: int) -> None:
        """Once every page is read, mark 本期 usage, which is often on the back page."""
        for page in self.store.pages(bills_only=True):
            if page["upload_id"] != upload_id or page["status"] != "draft" or page.get("kind") == documents.OTHER:
                continue
            marks, notes = page["marks"] or [], page["mark_notes"] or []
            if any(is_usage_mark(mark) for mark in marks) or any(USAGE_LABEL in note for note in notes):
                continue  # already done before a pause or restart
            usage, usage_notes, kwh = usage_marks_for(self.store, page)
            values: dict[str, Any] = {"marks": marks + usage, "mark_notes": notes + usage_notes}
            if kwh is not None and (page["row"] or {}).get(documents.USAGE) is None:
                values["row"] = {**page["row"], documents.USAGE: kwh}
                values["ocr_row"] = {**(page["ocr_row"] or {}), documents.USAGE: kwh}
            self.store.update_page(page["id"], **values)

    def _store_page(self, upload_id: int, filename: str, path: Path, event: PageDone) -> None:
        dataset = page_record(
            event.bill,
            file=filename,
            page=event.page_number,
            cross_check=event.cross_check,
            cross_check_error=event.cross_check_error,
        ).as_json()
        width, height = render_page(path, event.page_number, self.settings.dpi).size
        # A page counts as a Taipower bill when OCR found a charge breakdown
        # with at least a period or an amount, on a page that is one. Other
        # pages are kept for the record; an upload with none becomes another
        # kind of document (documents.detect).
        is_bill = event.record is not None and documents.is_taipower_bill(event.bill) and any(
            event.record.row.get(column) is not None
            for column in ("Billing Period 計費期間", "基本電費", "流動電費", "稅前應繳總金額", "應繳總金額")
        )
        values: dict[str, Any] = {
            "page_number": event.page_number,
            "is_bill": is_bill,
            "status": "draft",
            "dataset": dataset,
            "image_width": width,
            "image_height": height,
        }
        if is_bill:
            # Red labels avoid text either engine read on the page.
            printed = [
                (line.x, line.y, line.width, line.height)
                for bill in (event.bill, event.cross_check)
                if bill is not None
                for line in bill.ocr_lines
            ]
            regions = [
                (bounds["x"], bounds["y"], bounds["width"], bounds["height"])
                for bounds in dataset.get("blocks", {}).values()
            ]
            marks, notes = default_marks(event.locations, (width, height), event.record.row, printed, regions)
            values.update(
                row=event.record.row,
                ocr_row=dict(event.record.row),
                issues=[asdict(issue) for issue in event.record.issues],
                alternatives=event.record.alternatives,
                locations={
                    column: {"box": list(location.box), "exact": location.exact}
                    for column, location in event.locations.items()
                },
                marks=marks,
                mark_notes=notes,
            )
        self.store.add_page(upload_id, **values)
