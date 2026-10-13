"""Background OCR: one upload at a time, loading the OCR model only once."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from taipower_bill_ocr.batch import FileFinished, FileStarted, PageDone, PageExtractor, iter_batch, render_page
from taipower_bill_ocr.dataset import page_record

from .evidence import default_marks
from .settings import Settings
from .store import Store

log = logging.getLogger("taipower_web_api.worker")


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
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="ocr-worker", daemon=True)

    def start(self) -> None:
        # Uploads interrupted by a restart are processed again.
        for upload_id in self.store.pending_upload_ids():
            self._queue.put(upload_id)
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)

    def submit(self, upload_id: int) -> None:
        self._queue.put(upload_id)

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
            try:
                self.process(upload_id)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                log.exception("Upload %s failed", upload_id)
                self.store.set_upload(upload_id, status="failed", error=f"{type(exc).__name__}: {exc}")

    def process(self, upload_id: int) -> None:
        upload = self.store.upload(upload_id)
        if upload is None:
            return
        path = Path(upload["path"])
        self.store.delete_pages(upload_id)
        self.store.set_upload(upload_id, status="processing", error=None, pages_done=0)
        done = 0
        errors: list[str] = []
        for event in iter_batch(
            [path], self.extractor,
            source_name=lambda _: upload["filename"],
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
        self.store.set_upload(
            upload_id,
            status="failed" if errors else "done",
            error="; ".join(errors) or None,
            processed_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _store_page(self, upload_id: int, filename: str, path: Path, event: PageDone) -> None:
        dataset = page_record(
            event.bill,
            file=filename,
            page=event.page_number,
            cross_check=event.cross_check,
            cross_check_error=event.cross_check_error,
        ).as_json()
        width, height = render_page(path, event.page_number, self.settings.dpi).size
        # A page counts as a bill when OCR found a charge breakdown with at
        # least a period or an amount; an empty layout match (e.g. a photo
        # that is not a bill) is kept for the record but not sent to review.
        is_bill = event.record is not None and any(
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
