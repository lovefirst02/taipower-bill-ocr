"""Zoomable bill-page viewer that highlights where a field was read."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from PIL import Image, ImageTk

Box = tuple[float, float, float, float]

HIGHLIGHT_EXACT = "#D93025"
HIGHLIGHT_APPROXIMATE = "#F29900"
MAX_SCALE = 4.0
MIN_SCALE = 0.05


class PagePreview(ttk.Frame):
    """Canvas showing one page image; wheel zooms, drag pans."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.caption_var = tk.StringVar(value="Select a row to preview its bill page.")
        ttk.Label(toolbar, textvariable=self.caption_var).pack(side="left")
        ttk.Button(toolbar, text="Fit Page", command=self.fit_page, width=9).pack(
            side="right"
        )
        ttk.Button(toolbar, text="Zoom to Field", command=self.zoom_to_field).pack(
            side="right", padx=4
        )
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.zoom_by(1.25)).pack(
            side="right"
        )
        ttk.Button(toolbar, text="−", width=3, command=lambda: self.zoom_by(0.8)).pack(
            side="right", padx=(0, 2)
        )

        self.canvas = tk.Canvas(self, background="#D9D9D9", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._schedule_render())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        self.image: Image.Image | None = None
        self.box: Box | None = None
        self.box_exact = True
        self.scale = 1.0
        self.origin = (0.0, 0.0)  # image coordinate shown at the canvas top-left
        self._photo: ImageTk.PhotoImage | None = None
        self._drag_start: tuple[int, int, float, float] | None = None
        self._render_pending = False
        self._fit_on_first_render = False

    # ---------------------------------------------------------------- content
    def show_message(self, message: str) -> None:
        self.image = None
        self.box = None
        self.caption_var.set(message)
        self._render()

    def set_image(self, image: Image.Image, caption: str) -> None:
        if image is not self.image:
            self.image = image
            self._fit_on_first_render = True
        self.box = None
        self.caption_var.set(caption)
        self.fit_page()

    def highlight(self, box: Box | None, caption: str, *, exact: bool = True) -> None:
        self.box = box
        self.box_exact = exact
        self.caption_var.set(caption)
        if box is None:
            self._render()
        else:
            self.zoom_to_field()

    # ------------------------------------------------------------------- view
    def _canvas_size(self) -> tuple[int, int]:
        return max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)

    def fit_page(self) -> None:
        if self.image is None:
            return
        width, height = self._canvas_size()
        self.scale = min(width / self.image.width, height / self.image.height)
        self.origin = (
            -(width / self.scale - self.image.width) / 2,
            -(height / self.scale - self.image.height) / 2,
        )
        self._render()

    def zoom_to_field(self) -> None:
        if self.image is None or self.box is None:
            return
        x, y, w, h = self.box
        width, height = self._canvas_size()
        # Keep surrounding rows in view so the value can be read in context.
        region_w = w + max(w * 0.3, 120)
        region_h = h + max(h * 3, 160)
        self.scale = max(MIN_SCALE, min(width / region_w, height / region_h, 2.5))
        center_x, center_y = x + w / 2, y + h / 2
        self.origin = (
            center_x - width / self.scale / 2,
            center_y - height / self.scale / 2,
        )
        self._render()

    def zoom_by(self, factor: float, anchor: tuple[float, float] | None = None) -> None:
        if self.image is None:
            return
        width, height = self._canvas_size()
        ax, ay = anchor or (width / 2, height / 2)
        image_x = self.origin[0] + ax / self.scale
        image_y = self.origin[1] + ay / self.scale
        self.scale = max(MIN_SCALE, min(self.scale * factor, MAX_SCALE))
        self.origin = (image_x - ax / self.scale, image_y - ay / self.scale)
        self._render()

    def _on_wheel(self, event: tk.Event) -> None:
        self.zoom_by(1.2 if event.delta > 0 else 1 / 1.2, (event.x, event.y))

    def _on_press(self, event: tk.Event) -> None:
        self._drag_start = (event.x, event.y, *self.origin)

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_start is None or self.image is None:
            return
        start_x, start_y, origin_x, origin_y = self._drag_start
        self.origin = (
            origin_x - (event.x - start_x) / self.scale,
            origin_y - (event.y - start_y) / self.scale,
        )
        self._render()

    # ----------------------------------------------------------------- render
    def _schedule_render(self) -> None:
        if not self._render_pending:
            self._render_pending = True
            self.after_idle(self._render_after_resize)

    def _render_after_resize(self) -> None:
        self._render_pending = False
        if self._fit_on_first_render and self.box is None:
            self.fit_page()
        elif self.box is not None:
            self.zoom_to_field()
        else:
            self._render()

    def _render(self) -> None:
        self.canvas.delete("all")
        width, height = self._canvas_size()
        if self.image is None:
            self.canvas.create_text(
                width / 2,
                height / 2,
                text=self.caption_var.get(),
                fill="#5F6368",
                width=width - 40,
            )
            return
        self._fit_on_first_render = False
        ox, oy = self.origin
        left = max(0, int(ox))
        top = max(0, int(oy))
        right = min(self.image.width, int(ox + width / self.scale) + 1)
        bottom = min(self.image.height, int(oy + height / self.scale) + 1)
        if right > left and bottom > top:
            # Only the visible region is resampled, so zooming stays fast.
            crop = self.image.crop((left, top, right, bottom))
            size = (
                max(1, round((right - left) * self.scale)),
                max(1, round((bottom - top) * self.scale)),
            )
            resample = Image.LANCZOS if self.scale < 1 else Image.BILINEAR
            self._photo = ImageTk.PhotoImage(crop.resize(size, resample))
            self.canvas.create_image(
                (left - ox) * self.scale, (top - oy) * self.scale,
                image=self._photo, anchor="nw",
            )
        if self.box is not None:
            x, y, w, h = self.box
            color = HIGHLIGHT_EXACT if self.box_exact else HIGHLIGHT_APPROXIMATE
            self.canvas.create_rectangle(
                (x - ox) * self.scale,
                (y - oy) * self.scale,
                (x + w - ox) * self.scale,
                (y + h - oy) * self.scale,
                outline=color,
                width=3,
                dash=() if self.box_exact else (6, 4),
            )


def caption_for(label: str, location: Any) -> str:
    if location is None:
        return f"{label}: location not found on this page"
    if location.exact:
        return f"{label}: highlighted in red"
    return f"{label}: exact position unknown – showing its section (orange)"
