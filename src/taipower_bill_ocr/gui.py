"""Tkinter desktop app for batch-extracting a folder of bills to Excel.

Run with ``python -m taipower_bill_ocr.gui``.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import Any

from .batch import (
    FileFinished,
    FileStarted,
    PageDone,
    PageExtractor,
    find_bill_files,
    iter_batch,
    render_page,
)
from .excel_export import (
    COLUMN_LABELS_EN,
    COLUMNS,
    OTHER_ADJUSTMENT_INPUTS,
    REVIEW_COLUMN,
    Issue,
    recompute_other_adjustment,
    write_rows_to_excel,
)
from .field_locator import FieldLocation
from .preview import PagePreview, caption_for

APP_TITLE = "Taipower Bill OCR"
ENGINES = {
    "PaddleOCR + Windows cross-check (recommended)": "crosscheck",
    "PaddleOCR only (faster)": "paddleocr",
    "Windows OCR only": "windows",
}
DPI_CHOICES = ("200", "250", "300")
TEXT_COLUMNS = {"Billing Period 計費期間", "Custom No. 電號"}
READ_ONLY_COLUMNS = {"來源檔案", "頁碼", REVIEW_COLUMN, "其他調整費用"}
REVIEWED_PREFIX = "已人工確認"

ROW_COLORS = {
    "review": "#FFF4CE",
    "edited": "#E3F0FB",
    "reviewed": "#E6F4EA",
}
ENTRY_COLORS = {
    "normal": "#FFFFFF",
    "flagged": "#FFE9A8",
    "edited": "#D2E7FA",
    "invalid": "#F9DEDC",
    "readonly": "#F1F3F4",
}

FORM_SECTIONS = (
    ("Bill", ("Billing Period 計費期間", "Custom No. 電號")),
    ("Billed kWh", tuple(key for key in COLUMNS if key.startswith("計費度數_"))),
    ("Transferred kWh", tuple(key for key in COLUMNS if key.startswith("轉供度數_"))),
    (
        "Charges (TWD)",
        ("基本電費", "流動電費", "功率因數調整費", "預繳費用", "其他調整費用", "稅前應繳總金額", "應繳總金額"),
    ),
)
FORM_ORDER = tuple(key for _, keys in FORM_SECTIONS for key in keys)


@dataclass
class ResultRow:
    row: dict[str, Any]
    issues: list[Issue]
    path: Path | None = None
    page_number: int | None = None
    dpi: int = 200
    locations: dict[str, FieldLocation] = field(default_factory=dict)
    alternatives: dict[str, Any] = field(default_factory=dict)
    edited: set[str] = field(default_factory=set)
    reviewed: bool = False

    @property
    def needs_review(self) -> bool:
        return bool(self.issues) and not self.reviewed

    @property
    def flagged_columns(self) -> set[str]:
        return {column for issue in self.issues for column in issue.columns}

    def notes_en(self) -> str:
        notes = "; ".join(issue.en for issue in self.issues)
        if self.edited:
            edited = ", ".join(COLUMN_LABELS_EN[key] for key in COLUMNS if key in self.edited)
            notes = f"{notes}; Edited: {edited}" if notes else f"Edited: {edited}"
        if self.reviewed:
            notes = f"Reviewed. {notes}" if notes else "Reviewed"
        return notes

    def export_row(self) -> dict[str, Any]:
        row = dict(self.row)
        if self.reviewed:
            original = row.get(REVIEW_COLUMN)
            row[REVIEW_COLUMN] = (
                f"{REVIEWED_PREFIX}；{original}" if original else REVIEWED_PREFIX
            )
        return row


def _format_cell(key: str, value: Any) -> str:
    if value is None:
        return ""
    if key in TEXT_COLUMNS or key in {"來源檔案", "頁碼"}:
        return str(value)
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _parse_cell(key: str, text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if key in TEXT_COLUMNS:
        return text
    number = float(text.replace(",", ""))
    return int(number) if number.is_integer() else number


class BillOcrApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.master = master
        self.events: queue.Queue[Any] = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.results: dict[str, ResultRow] = {}
        self.file_items: dict[Path, str] = {}
        self.files_total = 0
        self.files_done = 0
        self._current_pages = 1
        self._current_rows = 0
        self._batch_dpi = 200
        self.form_iid: str | None = None
        self.entries: dict[str, tk.Entry] = {}
        self.alternative_buttons: dict[str, ttk.Button] = {}
        self.focused_key: str | None = None

        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.engine_var = tk.StringVar(value=next(iter(ENGINES)))
        self.dpi_var = tk.StringVar(value=DPI_CHOICES[0])
        self.review_only_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a folder of bill PDFs or images to begin.")
        self.summary_var = tk.StringVar(value="")
        self.form_title_var = tk.StringVar(value="No row selected")
        self.form_notes_var = tk.StringVar(value="")

        self._build()
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        master.protocol("WM_DELETE_WINDOW", self._on_close)
        master.bind("<Control-Return>", lambda _event: self._mark_reviewed_and_next())
        master.bind("<Control-Down>", lambda _event: self._go_to_row(+1))
        master.bind("<Control-Up>", lambda _event: self._go_to_row(-1))
        self._poll_id = self.after(100, self._drain_events)

    # ----------------------------------------------------------------- layout
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_source().grid(row=0, column=0, sticky="ew")

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        panes.add(self._build_files_pane(panes), weight=1)
        panes.add(self._build_results_pane(panes), weight=2)
        panes.add(self._build_review_pane(panes), weight=4)

        footer = ttk.Frame(self)
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(footer, textvariable=self.status_var).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(footer, textvariable=self.summary_var).grid(
            row=1, column=1, sticky="e", pady=(4, 0)
        )

    def _build_source(self) -> ttk.LabelFrame:
        source = ttk.LabelFrame(self, text="Source", padding=(10, 6))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="Folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(source, textvariable=self.folder_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(source, text="Browse…", command=self._browse).grid(row=0, column=2)
        ttk.Checkbutton(
            source, text="Include subfolders", variable=self.recursive_var
        ).grid(row=0, column=3, padx=(12, 0))

        options = ttk.Frame(source)
        options.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(options, text="OCR engine:").pack(side="left")
        ttk.Combobox(
            options, textvariable=self.engine_var, values=list(ENGINES),
            state="readonly", width=42,
        ).pack(side="left", padx=(6, 16))
        ttk.Label(options, text="PDF resolution (DPI):").pack(side="left")
        ttk.Combobox(
            options, textvariable=self.dpi_var, values=DPI_CHOICES,
            state="readonly", width=6,
        ).pack(side="left", padx=6)
        self.stop_button = ttk.Button(
            options, text="Stop", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="right")
        self.start_button = ttk.Button(
            options, text="Start Processing", command=self._start
        )
        self.start_button.pack(side="right", padx=6)
        return source

    def _build_files_pane(self, parent: ttk.PanedWindow) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text="Files", padding=(6, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.files_tree = ttk.Treeview(
            frame, columns=("pages", "status"), height=3, selectmode="browse"
        )
        self.files_tree.heading("#0", text="File", anchor="w")
        self.files_tree.heading("pages", text="Pages", anchor="e")
        self.files_tree.heading("status", text="Status", anchor="w")
        self.files_tree.column("#0", width=420, stretch=True)
        self.files_tree.column("pages", width=70, anchor="e", stretch=False)
        self.files_tree.column("status", width=360, stretch=True)
        self.files_tree.tag_configure("failed", foreground="#B3261E")
        self.files_tree.tag_configure("done", foreground="#1E6B34")
        self.files_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.files_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.files_tree.configure(yscrollcommand=scroll.set)
        return frame

    def _build_results_pane(self, parent: ttk.PanedWindow) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text="Results", padding=(6, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(
            toolbar, text="Show only rows needing review",
            variable=self.review_only_var, command=self._refresh_results,
        ).pack(side="left")
        for tag, text in (("review", "Needs review"), ("edited", "Edited"), ("reviewed", "Reviewed")):
            tk.Label(toolbar, width=2, background=ROW_COLORS[tag], relief="solid", bd=1).pack(
                side="left", padx=(16, 4)
            )
            ttk.Label(toolbar, text=text).pack(side="left")
        self.export_button = ttk.Button(
            toolbar, text="Export to Excel…", command=self._export, state="disabled"
        )
        self.export_button.pack(side="right")
        ttk.Button(
            toolbar, text="Mark Selected as Reviewed", command=self._toggle_reviewed
        ).pack(side="right", padx=6)

        self.results_tree = ttk.Treeview(
            frame, columns=COLUMNS, show="headings", selectmode="extended", height=6
        )
        heading_font = tkfont.nametofont("TkHeadingFont")
        body_font = tkfont.nametofont("TkDefaultFont")
        sample_text = {
            "Billing Period 計費期間": "000/00/00~000/00/00",
            "Custom No. 電號": "00-00-0000-00-0",
            "來源檔案": "x" * 28,
        }
        for key in COLUMNS:
            label = COLUMN_LABELS_EN[key]
            is_number = key not in TEXT_COLUMNS | {"來源檔案", REVIEW_COLUMN}
            self.results_tree.heading(key, text=label, anchor="e" if is_number else "w")
            width = (
                body_font.measure("x" * 70)
                if key == REVIEW_COLUMN
                else max(
                    heading_font.measure(label),
                    body_font.measure(sample_text.get(key, "-0,000,000.00")),
                )
                + 24
            )
            self.results_tree.column(
                key, width=width, anchor="e" if is_number else "w", stretch=False
            )
        for tag, color in ROW_COLORS.items():
            self.results_tree.tag_configure(tag, background=color)
        self.results_tree.grid(row=1, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.results_tree.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=self.results_tree.xview)
        y_scroll.grid(row=1, column=1, sticky="ns")
        x_scroll.grid(row=2, column=0, sticky="ew")
        self.results_tree.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )
        self.results_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_select())
        self.results_tree.bind("<Double-1>", self._on_double_click)
        return frame

    def _build_review_pane(self, parent: ttk.PanedWindow) -> ttk.Frame:
        frame = ttk.LabelFrame(parent, text="Review", padding=(6, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        split = ttk.PanedWindow(frame, orient="horizontal")
        split.grid(row=0, column=0, sticky="nsew")
        self.preview = PagePreview(split)
        split.add(self.preview, weight=3)
        split.add(self._build_form(split), weight=2)
        return frame

    def _build_form(self, parent: ttk.PanedWindow) -> ttk.Frame:
        form = ttk.Frame(parent, padding=(10, 0, 0, 0))
        form.columnconfigure(0, weight=1)
        ttk.Label(form, textvariable=self.form_title_var, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.notes_label = ttk.Label(
            form, textvariable=self.form_notes_var, foreground="#8A5A00",
            wraplength=480, justify="left",
        )
        self.notes_label.grid(row=1, column=0, sticky="ew", pady=(2, 6))

        fields = ttk.Frame(form)
        fields.grid(row=2, column=0, sticky="nsew")
        columns = ((0, FORM_SECTIONS[:3]), (3, FORM_SECTIONS[3:]))
        for grid_column, sections in columns:
            row = 0
            for title, keys in sections:
                ttk.Label(fields, text=title, font=("Segoe UI", 9, "bold")).grid(
                    row=row, column=grid_column, columnspan=2, sticky="w",
                    pady=(6 if row else 0, 2), padx=(0 if grid_column == 0 else 18, 0),
                )
                row += 1
                for key in keys:
                    short = COLUMN_LABELS_EN[key].split(" – ")[-1]
                    ttk.Label(fields, text=short).grid(
                        row=row, column=grid_column, sticky="w",
                        padx=(8 if grid_column == 0 else 26, 6),
                    )
                    entry = tk.Entry(
                        fields, width=20, relief="solid", bd=1,
                        justify="left" if key in TEXT_COLUMNS else "right",
                    )
                    entry.grid(row=row, column=grid_column + 1, sticky="ew", pady=1)
                    self._bind_entry(entry, key)
                    self.entries[key] = entry
                    alternative = ttk.Button(
                        fields, command=lambda key=key: self._use_alternative(key)
                    )
                    alternative.grid(row=row, column=grid_column + 2, sticky="w", padx=(4, 0))
                    alternative.grid_remove()
                    self.alternative_buttons[key] = alternative
                    row += 1

        buttons = ttk.Frame(form)
        buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="◀ Previous", command=lambda: self._go_to_row(-1)).pack(side="left")
        ttk.Button(buttons, text="Next ▶", command=lambda: self._go_to_row(+1)).pack(
            side="left", padx=4
        )
        ttk.Button(
            buttons, text="Mark Reviewed & Next  (Ctrl+Enter)",
            command=self._mark_reviewed_and_next,
        ).pack(side="right")
        ttk.Label(
            form,
            text="Click a field to zoom the preview to it. Enter saves and moves on; "
            "Esc undoes; Ctrl+↑/↓ switch rows.",
            foreground="#5F6368", wraplength=480, justify="left",
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        self._set_form_enabled(False)
        return form

    def _bind_entry(self, entry: tk.Entry, key: str) -> None:
        entry.bind("<FocusIn>", lambda _event: self._on_field_focus(key))
        entry.bind("<FocusOut>", lambda _event: self._commit_field(key))
        entry.bind("<Return>", lambda _event: self._commit_and_advance(key))
        entry.bind("<KP_Enter>", lambda _event: self._commit_and_advance(key))
        entry.bind("<Escape>", lambda _event: self._revert_field(key))

    # ---------------------------------------------------------------- actions
    def _browse(self) -> None:
        folder = filedialog.askdirectory(
            title="Select a folder of bills", initialdir=self.folder_var.get() or None
        )
        if folder:
            self.folder_var.set(folder)
            self._list_files()

    def _list_files(self) -> list[Path]:
        folder = Path(self.folder_var.get())
        self.files_tree.delete(*self.files_tree.get_children())
        self.file_items.clear()
        if not folder.is_dir():
            return []
        files = find_bill_files(folder, recursive=self.recursive_var.get())
        for path in files:
            self.file_items[path] = self.files_tree.insert(
                "", "end", text=str(path.relative_to(folder)), values=("", "Queued")
            )
        self.status_var.set(f"Found {len(files)} file(s) in {folder}.")
        return files

    def _start(self) -> None:
        folder = Path(self.folder_var.get())
        if not folder.is_dir():
            messagebox.showwarning(APP_TITLE, "Please choose an existing folder first.")
            return
        if self.results and not messagebox.askyesno(
            APP_TITLE, "Starting a new batch clears the current results. Continue?"
        ):
            return
        files = self._list_files()
        if not files:
            messagebox.showinfo(APP_TITLE, "No PDF or image files were found in this folder.")
            return

        self.results.clear()
        self._load_form(None)
        self._refresh_results()
        self.cancel.clear()
        self.files_total, self.files_done = len(files), 0
        self.progress.configure(maximum=len(files), value=0)
        self._set_running(True)

        self._batch_dpi = int(self.dpi_var.get())
        extractor = PageExtractor(ENGINES[self.engine_var.get()], self._batch_dpi)
        self.worker = threading.Thread(
            target=self._run_batch, args=(files, extractor, folder), daemon=True
        )
        self.worker.start()

    def _run_batch(self, files: list[Path], extractor: PageExtractor, folder: Path) -> None:
        try:
            if extractor.uses_paddle:
                self.events.put(("status", "Loading PaddleOCR model (first run may take a while)…"))
                extractor._engine()
            for event in iter_batch(
                files, extractor, self.cancel,
                source_name=lambda path: str(path.relative_to(folder)),
            ):
                self.events.put(event)
        except Exception as exc:  # noqa: BLE001 - surface in the UI thread
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.events.put(("finished", None))

    def _stop(self) -> None:
        self.cancel.set()
        self.stop_button.configure(state="disabled")
        self.status_var.set("Stopping after the current page…")

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.export_button.configure(
            state="normal" if self.results and not running else "disabled"
        )

    # ----------------------------------------------------------- event intake
    def _drain_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._drain_events)

    def destroy(self) -> None:
        self.after_cancel(self._poll_id)
        super().destroy()

    def _handle_event(self, event: Any) -> None:
        if isinstance(event, FileStarted):
            self._current_pages = event.page_count
            self._current_rows = 0
            item = self.file_items.get(event.path)
            if item:
                self.files_tree.item(item, values=(event.page_count, "Processing…"))
                self.files_tree.see(item)
        elif isinstance(event, PageDone):
            item = self.file_items.get(event.path)
            page = event.page_number or 1
            if event.record is not None:
                self._current_rows += 1
                result = ResultRow(
                    event.record.row,
                    event.record.issues,
                    path=event.path,
                    page_number=event.page_number,
                    dpi=self._batch_dpi,
                    locations=event.locations,
                    alternatives=event.record.alternatives,
                )
                iid = f"row{len(self.results)}"
                self.results[iid] = result
                self._insert_result(iid, result)
                self._update_summary()
                if self.form_iid is None:
                    self._select_row(iid)
            if item:
                self.files_tree.item(
                    item, values=(self._current_pages, f"Page {page} of {self._current_pages}")
                )
            self.progress.configure(
                value=self.files_done + page / max(self._current_pages, 1)
            )
            self.status_var.set(f"{event.path.name}: page {page} of {self._current_pages}")
        elif isinstance(event, FileFinished):
            self.files_done += 1
            self.progress.configure(value=self.files_done)
            item = self.file_items.get(event.path)
            if item:
                pages = self.files_tree.item(item, "values")[0]
                if event.error:
                    self.files_tree.item(
                        item, values=(pages, f"Failed – {event.error}"), tags=("failed",)
                    )
                else:
                    rows = self._current_rows
                    status = f"Done – {rows} bill row(s)" if rows else "Done – no charge page found"
                    self.files_tree.item(item, values=(pages, status), tags=("done",))
        elif isinstance(event, tuple):
            kind, payload = event
            if kind == "status":
                self.status_var.set(payload)
            elif kind == "error":
                messagebox.showerror(APP_TITLE, payload)
            elif kind == "finished":
                self._set_running(False)
                state = "Stopped" if self.cancel.is_set() else "Finished"
                self.status_var.set(
                    f"{state}: {self.files_done} of {self.files_total} file(s) processed."
                )

    # ------------------------------------------------------------ results tree
    def _row_tag(self, result: ResultRow) -> str:
        if result.reviewed:
            return "reviewed"
        if result.issues:
            return "review"
        return "edited" if result.edited else ""

    def _row_values(self, result: ResultRow) -> list[str]:
        return [
            result.notes_en() if key == REVIEW_COLUMN else _format_cell(key, result.row.get(key))
            for key in COLUMNS
        ]

    def _insert_result(self, iid: str, result: ResultRow) -> None:
        if self.review_only_var.get() and not result.needs_review:
            return
        self.results_tree.insert(
            "", "end", iid=iid, values=self._row_values(result), tags=(self._row_tag(result),)
        )

    def _refresh_results(self) -> None:
        self.results_tree.delete(*self.results_tree.get_children())
        for iid, result in self.results.items():
            self._insert_result(iid, result)
        if self.form_iid and self.results_tree.exists(self.form_iid):
            self.results_tree.selection_set(self.form_iid)
        self._update_summary()

    def _update_result(self, iid: str) -> None:
        result = self.results[iid]
        if self.results_tree.exists(iid):
            self.results_tree.item(
                iid, values=self._row_values(result), tags=(self._row_tag(result),)
            )
        self._update_summary()

    def _update_summary(self) -> None:
        total = len(self.results)
        pending = sum(result.needs_review for result in self.results.values())
        self.summary_var.set(f"{total} bill row(s) · {pending} need review" if total else "")

    def _select_row(self, iid: str) -> None:
        # Load immediately: <<TreeviewSelect>> only fires on the next idle
        # cycle, after more queued pages may already have arrived.
        if iid != self.form_iid:
            self._load_form(iid)
        if self.results_tree.exists(iid):
            self.results_tree.selection_set(iid)
            self.results_tree.see(iid)

    def _on_select(self) -> None:
        selection = self.results_tree.selection()
        if len(selection) == 1 and selection[0] != self.form_iid:
            self._load_form(selection[0])

    def _on_double_click(self, event: tk.Event) -> None:
        iid = self.results_tree.identify_row(event.y)
        column_id = self.results_tree.identify_column(event.x)
        if not iid or not column_id:
            return
        if iid != self.form_iid:
            self._select_row(iid)
        key = COLUMNS[int(column_id[1:]) - 1]
        if key in self.entries and key not in READ_ONLY_COLUMNS:
            self.entries[key].focus_set()
            self.entries[key].select_range(0, "end")

    # -------------------------------------------------------------- edit form
    def _set_form_enabled(self, enabled: bool) -> None:
        for key, entry in self.entries.items():
            readonly = key in READ_ONLY_COLUMNS
            entry.configure(
                state="readonly" if readonly or not enabled else "normal",
                readonlybackground=ENTRY_COLORS["readonly"],
            )

    def _entry_color(self, result: ResultRow, key: str) -> str:
        if key in READ_ONLY_COLUMNS:
            return ENTRY_COLORS["readonly"]
        if key in result.edited:
            return ENTRY_COLORS["edited"]
        if key in result.flagged_columns and not result.reviewed:
            return ENTRY_COLORS["flagged"]
        return ENTRY_COLORS["normal"]

    def _set_entry(self, key: str, value: Any, color: str) -> None:
        entry = self.entries[key]
        readonly = entry.cget("state") == "readonly"
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, _format_cell(key, value))
        entry.configure(background=color, readonlybackground=color)
        if readonly:
            entry.configure(state="readonly")

    def _load_form(self, iid: str | None) -> None:
        self.form_iid = iid
        self.focused_key = None
        if iid is None:
            self.form_title_var.set("No row selected")
            self.form_notes_var.set("")
            for key in self.entries:
                self._set_entry(key, None, ENTRY_COLORS["readonly"])
                self.alternative_buttons[key].grid_remove()
            self._set_form_enabled(False)
            self.preview.show_message("Select a row to preview its bill page.")
            return
        result = self.results[iid]
        self._set_form_enabled(True)
        for key in self.entries:
            self._set_entry(key, result.row.get(key), self._entry_color(result, key))
        self._refresh_alternatives(result)
        self._refresh_form_header(result)
        self._load_preview(result)

    def _refresh_form_header(self, result: ResultRow) -> None:
        position = list(self.results).index(self.form_iid) + 1
        page = f", page {result.page_number}" if result.page_number else ""
        self.form_title_var.set(
            f"Row {position} of {len(self.results)} — {result.row.get('來源檔案')}{page}"
        )
        if result.reviewed:
            self.form_notes_var.set("✓ Reviewed. " + "; ".join(i.en for i in result.issues))
        elif result.issues:
            self.form_notes_var.set("⚠ " + "\n⚠ ".join(issue.en for issue in result.issues))
        else:
            self.form_notes_var.set("No issues found.")

    def _load_preview(self, result: ResultRow) -> None:
        if result.path is None:
            self.preview.show_message("No page image available for this row.")
            return
        self.preview.show_message("Loading page…")
        self.update_idletasks()
        try:
            image = render_page(result.path, result.page_number, result.dpi)
        except Exception as exc:  # noqa: BLE001 - preview is best effort
            self.preview.show_message(f"Could not load page: {exc}")
            return
        page = f" · page {result.page_number}" if result.page_number else ""
        self.preview.set_image(image, f"{result.path.name}{page}")
        first_flagged = next(
            (key for key in FORM_ORDER if key in result.flagged_columns and key in result.locations),
            None,
        )
        if first_flagged and not result.reviewed:
            self._show_field(result, first_flagged)

    def _show_field(self, result: ResultRow, key: str) -> None:
        location = result.locations.get(key)
        caption = caption_for(COLUMN_LABELS_EN[key], location)
        if location is None:
            self.preview.highlight(None, caption)
        else:
            self.preview.highlight(location.box, caption, exact=location.exact)

    def _refresh_alternatives(self, result: ResultRow) -> None:
        """Offer the cross-check engine's reading where the engines disagree."""
        for key, button in self.alternative_buttons.items():
            alternative = result.alternatives.get(key)
            if alternative is None or alternative == result.row.get(key):
                button.grid_remove()
            else:
                button.configure(text=f"Use {_format_cell(key, alternative)}")
                button.grid()

    def _use_alternative(self, key: str) -> None:
        if self.form_iid is None:
            return
        result = self.results[self.form_iid]
        alternative = result.alternatives.get(key)
        if alternative is None:
            return
        self.entries[key].delete(0, "end")
        self.entries[key].insert(0, str(alternative))
        self._commit_field(key)
        self._refresh_alternatives(result)
        self._on_field_focus(key)

    def _on_field_focus(self, key: str) -> None:
        self.focused_key = key
        if self.form_iid is not None:
            self._show_field(self.results[self.form_iid], key)

    def _apply_edit(self, iid: str, key: str, text: str) -> bool:
        """Store a corrected value; returns False when the text is invalid."""
        result = self.results[iid]
        try:
            value = _parse_cell(key, text)
        except ValueError:
            return False
        if value != result.row.get(key):
            result.row[key] = value
            result.edited.add(key)
            if key in OTHER_ADJUSTMENT_INPUTS:
                recompute_other_adjustment(result.row)
            self._update_result(iid)
        return True

    def _commit_field(self, key: str) -> bool:
        if self.form_iid is None or key in READ_ONLY_COLUMNS:
            return True
        result = self.results[self.form_iid]
        entry = self.entries[key]
        if not self._apply_edit(self.form_iid, key, entry.get()):
            entry.configure(background=ENTRY_COLORS["invalid"])
            self.status_var.set(f"{COLUMN_LABELS_EN[key]} must be a number.")
            return False
        self._set_entry(key, result.row.get(key), self._entry_color(result, key))
        self._set_entry(
            "其他調整費用", result.row.get("其他調整費用"), ENTRY_COLORS["readonly"]
        )
        self._refresh_alternatives(result)
        return True

    def _commit_and_advance(self, key: str) -> str:
        if not self._commit_field(key):
            self.bell()
            return "break"
        result = self.results[self.form_iid]
        editable = [k for k in FORM_ORDER if k not in READ_ONLY_COLUMNS]
        after = editable[editable.index(key) + 1:]
        flagged = [k for k in after if k in result.flagged_columns and k not in result.edited]
        target = (flagged or after or [key])[0]
        self.entries[target].focus_set()
        self.entries[target].select_range(0, "end")
        return "break"

    def _revert_field(self, key: str) -> str:
        if self.form_iid is not None:
            result = self.results[self.form_iid]
            self._set_entry(key, result.row.get(key), self._entry_color(result, key))
        return "break"

    def _commit_focused(self) -> bool:
        return self.focused_key is None or self._commit_field(self.focused_key)

    def _go_to_row(self, step: int, *, needing_review: bool = False) -> None:
        if not self.results or not self._commit_focused():
            return
        order = list(self.results)
        start = order.index(self.form_iid) if self.form_iid in order else -1
        candidates = order[start + 1:] if step > 0 else list(reversed(order[:max(start, 0)]))
        if needing_review:
            candidates = [iid for iid in candidates if self.results[iid].needs_review]
        if self.review_only_var.get():
            candidates = [iid for iid in candidates if self.results_tree.exists(iid)]
        if not candidates:
            self.status_var.set(
                "No more rows need review." if needing_review else "No more rows."
            )
            return
        self._select_row(candidates[0])
        self.results_tree.focus_set()

    def _mark_reviewed_and_next(self) -> None:
        if self.form_iid is None or not self._commit_focused():
            return
        iid = self.form_iid
        self.results[iid].reviewed = True
        self._update_result(iid)
        if self.review_only_var.get():
            self._refresh_results()
        order = list(self.results)
        pending = [other for other in order if self.results[other].needs_review]
        if pending:
            position = order.index(iid)
            self._select_row(
                next((other for other in pending if order.index(other) > position), pending[0])
            )
        else:
            self._load_form(iid)
            self.status_var.set("All rows reviewed. Ready to export.")

    def _toggle_reviewed(self) -> None:
        selection = self.results_tree.selection()
        if not selection:
            return
        mark = not all(self.results[iid].reviewed for iid in selection)
        for iid in selection:
            self.results[iid].reviewed = mark
            self._update_result(iid)
        if self.review_only_var.get():
            self._refresh_results()
        if self.form_iid in selection:
            self._load_form(self.form_iid)

    # ----------------------------------------------------------------- export
    def _export(self) -> None:
        if not self.results or not self._commit_focused():
            return
        pending = sum(result.needs_review for result in self.results.values())
        if pending and not messagebox.askyesno(
            APP_TITLE,
            f"{pending} row(s) still need review. Their notes will be kept in the "
            "Review Notes column. Export anyway?",
        ):
            return
        path = filedialog.asksaveasfilename(
            title="Export to Excel",
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
            initialdir=self.folder_var.get() or None,
            initialfile=f"taipower_bills_{datetime.now():%Y%m%d_%H%M}.xlsx",
        )
        if not path:
            return
        try:
            write_rows_to_excel([result.export_row() for result in self.results.values()], path)
        except PermissionError:
            messagebox.showerror(APP_TITLE, "Could not write the file. Is it open in Excel?")
            return
        if messagebox.askyesno(APP_TITLE, f"Exported {len(self.results)} row(s).\n\nOpen the file now?"):
            os.startfile(path)  # noqa: S606 - Windows-only desktop app

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "Processing is still running. Quit anyway?"):
                return
            self.cancel.set()
        self.master.destroy()


def main() -> None:
    try:  # Crisp text on high-DPI Windows displays.
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1500x950")
    root.state("zoomed")  # the review layout needs the whole screen
    root.minsize(1100, 700)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    BillOcrApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
