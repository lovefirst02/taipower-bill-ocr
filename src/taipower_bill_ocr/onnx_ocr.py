"""OCR on the CPU with ONNX models: no Paddle needed, and fast enough to wait for.

``OnnxOcrEngine.predict`` answers like PaddleOCR's, so the rest of the pipeline
does not change. A small model trained on bills re-reads every line (see
``training/``): the general model is built for all of Chinese, this one only
for what a bill prints, and its reading counts only where it is a figure or
one of the bill's own labels. Names and addresses stay the general model's.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .bill_vocabulary import is_label, normal, split_label

DETECTION_FILE = "PP-OCRv6_small_det.onnx"
BILL_DETECTOR_FILE = "bill_detector.onnx"
RECOGNITION_FILE = "PP-OCRv6_small_rec.onnx"
CHARACTERS_FILE = "PP-OCRv6_small_rec.chars.json"
READER_FILE = "bill_reader.onnx"
READER_CHARS_FILE = "bill_reader.chars.json"

# As PaddleOCR's own pipeline sets them.
_DET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_DET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
PADDLE_DETECTOR = {"thresh": 0.3, "box_thresh": 0.6, "unclip": 1.5}
# Its target regions are larger than Paddle's (training/synth_det.py: SHRINK), so they grow back by more.
BILL_DETECTOR = {"thresh": 0.3, "box_thresh": 0.5, "unclip": 1.8}
_DET_MAX_SIDE = 4000
_BILL_DET_SIDE = 2048  # the bill detector cuts up text larger than it trained on: a larger picture is looked at shrunk
_ANGLE_SIDE = 1280
_SMALL_TEXT = 18  # pixels high, the middle line of a page: below this the detector misses lines
_KEPT_SHARE = 0.5  # of a page's lines read as something a bill prints: below this, look again at half size
_ANGLE_LINES = 5
_REC_HEIGHT = 48
_REC_BATCH = 16

_READER_HEIGHT = 48
_READER_MAX_WIDTH = 640
_READER_MARGIN = 0.15  # of the crop's height: measured best of 0, 0.15, 0.3, 0.5, 0.8
# A line the digit reader may read: nothing but figures on it, as far as Paddle saw.
_FIGURES = re.compile(r"^[\d\s,.，\-/~～*:：%至元年月日]+$")


def onnx_model_dir() -> Path | None:
    """``TAIPOWER_ONNX_MODELS``, or the ``onnx_models`` folder beside a packaged app."""
    configured = os.environ.get("TAIPOWER_ONNX_MODELS")
    candidates = [Path(configured)] if configured else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "onnx_models")
    return next((path for path in candidates if (path / DETECTION_FILE).is_file() or (path / BILL_DETECTOR_FILE).is_file()), None)


def _session(path: Path, threads: int = 0) -> Any:
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3  # the converted models warn about unused constants
    if threads:
        options.intra_op_num_threads = threads
    # onnxruntime cannot open a path with some non-ASCII names on Windows; bytes always work.
    return onnxruntime.InferenceSession(path.read_bytes(), options, providers=["CPUExecutionProvider"])


def _read_image(source: Any) -> np.ndarray:
    if isinstance(source, np.ndarray):
        return source
    image = cv2.imdecode(np.fromfile(str(source), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot read image: {source}")
    return image


def _decode_ctc(probabilities: np.ndarray) -> tuple[list[int], list[float]]:
    best, confidence = probabilities.argmax(-1), probabilities.max(-1)
    keep = (best != 0) & np.concatenate(([True], best[1:] != best[:-1]))
    return best[keep].tolist(), confidence[keep].tolist()


class OnnxOcrEngine:
    """Text detection, recognition and the bill reader, all ONNX on the CPU."""

    reads_whole_page = True

    def __init__(self, model_dir: str | Path, *, bill_reader: bool = True, general_reader: bool = True) -> None:
        """``general_reader=False`` reads with the bill reader alone: Taipower bills only."""
        folder = Path(model_dir)
        # The detector trained on bills goes with the bill reader alone: nothing has tested it on
        # other pages. TAIPOWER_DETECTOR=paddle keeps Paddle's converted one, to compare with.
        own = folder / BILL_DETECTOR_FILE
        paddle = folder / DETECTION_FILE
        wanted = not general_reader and os.environ.get("TAIPOWER_DETECTOR") != "paddle"
        if own.is_file() and (wanted or not paddle.is_file()):
            self._detector, self._detector_settings, self._detector_side = _session(own), BILL_DETECTOR, _BILL_DET_SIDE
        else:
            self._detector, self._detector_settings, self._detector_side = _session(paddle), PADDLE_DETECTOR, _DET_MAX_SIDE
        self._recognizer = self._characters = None
        if general_reader:
            self._recognizer = _session(folder / RECOGNITION_FILE)
            self._characters = ["", *json.loads((folder / CHARACTERS_FILE).read_text(encoding="utf-8")), " "]
        reader = folder / READER_FILE
        # Tiny jobs, one line at a time: more threads only cost time to start them up.
        self._reader = _session(reader, threads=2) if bill_reader and reader.is_file() else None
        self._reader_chars = json.loads((folder / READER_CHARS_FILE).read_text(encoding="utf-8")) if self._reader else ""

    # ------------------------------------------------------------ detection
    def _detect(self, image: np.ndarray, side: int | None = None) -> list[np.ndarray]:
        height, width = image.shape[:2]
        scale = min(1.0, (side or self._detector_side) / max(height, width))
        new_h, new_w = max(32, round(height * scale / 32) * 32), max(32, round(width * scale / 32) * 32)
        resized = cv2.resize(image, (new_w, new_h))
        tensor = ((resized.astype(np.float32) / 255 - _DET_MEAN) / _DET_STD).transpose(2, 0, 1)[None]
        probability = self._detector.run(None, {self._detector.get_inputs()[0].name: tensor})[0][0, 0]
        return boxes_from_map(probability, width, height, **self._detector_settings)

    def page_angle(self, source: Any) -> float:
        """How far the page's lines of text slope, in degrees; 0 if it cannot be told."""
        image = _read_image(source)
        scale = min(1.0, _ANGLE_SIDE / max(image.shape[:2]))
        small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
        angles = []
        for box in self._detect(small):
            run, rise = box[1] - box[0]
            if run > 3 * np.linalg.norm(box[3] - box[0]) and run > 40:
                angles.append(float(np.degrees(np.arctan2(rise, run))))
        return float(np.median(angles)) if len(angles) >= _ANGLE_LINES else 0.0

    # ---------------------------------------------------------- recognition
    def _recognize(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        results: list[tuple[str, float]] = [("", 0.0)] * len(crops)
        order = sorted(range(len(crops)), key=lambda index: crops[index].shape[1] / max(crops[index].shape[0], 1))
        name = self._recognizer.get_inputs()[0].name
        for start in range(0, len(order), _REC_BATCH):
            batch = order[start:start + _REC_BATCH]
            widths = [max(8, int(np.ceil(_REC_HEIGHT * crops[i].shape[1] / max(crops[i].shape[0], 1)))) for i in batch]
            longest = max(*widths, 320) if len(widths) > 1 else max(widths[0], 320)
            tensor = np.zeros((len(batch), 3, _REC_HEIGHT, longest), dtype=np.float32)
            for row, (index, width) in enumerate(zip(batch, widths)):
                resized = cv2.resize(crops[index], (width, _REC_HEIGHT)).astype(np.float32)
                tensor[row, :, :, :width] = ((resized / 255 - 0.5) / 0.5).transpose(2, 0, 1)
            output = self._recognizer.run(None, {name: tensor})[0]
            for row, index in enumerate(batch):
                ids, confidence = _decode_ctc(output[row])
                text = "".join(self._characters[i] if i < len(self._characters) else "" for i in ids)
                results[index] = (text, float(np.mean(confidence)) if confidence else 0.0)
        return results

    def read_bill_text(self, crop: np.ndarray) -> tuple[str, float]:
        """The bill reader on one crop: its text, and its least sure character."""
        grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # A character touching the crop's edge gets lost; the model learned from crops with some margin.
        margin = int(grey.shape[0] * _READER_MARGIN)
        grey = cv2.copyMakeBorder(grey, margin // 2, margin // 2, margin, margin, cv2.BORDER_REPLICATE)
        height, width = grey.shape[:2]
        new_width = min(_READER_MAX_WIDTH, max(8, round(width * _READER_HEIGHT / max(height, 1))))
        resized = cv2.resize(grey, (new_width, _READER_HEIGHT), interpolation=cv2.INTER_AREA if height > _READER_HEIGHT else cv2.INTER_CUBIC)
        tensor = (resized.astype(np.float32) / 255 * 2 - 1)[None, None]
        logits = self._reader.run(None, {self._reader.get_inputs()[0].name: tensor})[0][0]
        probabilities = np.exp(logits - logits.max(-1, keepdims=True))
        probabilities /= probabilities.sum(-1, keepdims=True)
        ids, confidence = _decode_ctc(probabilities)
        text = "".join(self._reader_chars[i - 1] for i in ids if i - 1 < len(self._reader_chars))
        return text, min(confidence, default=0.0)

    def _read_all_with_bill_reader(self, crops: list[np.ndarray]) -> tuple[list[str], list[float]]:
        """Every line by the bill reader, kept only where it reads as something a bill prints."""
        texts, scores = [], []
        for crop in crops:
            read, confidence = self.read_bill_text(crop)
            flat = normal(read)
            known = is_label(flat) or split_label(flat) is not None or (_FIGURES.match(read) and re.search(r"\d", read))
            texts.append(read if known else "")
            scores.append(confidence if known else 0.0)
        return texts, scores

    def _refine(self, texts: list[str], scores: list[float], crops: list[np.ndarray]) -> None:
        """Where the two readers differ, the bill reader decides on what it was trained for."""
        for index, text in enumerate(texts):
            read, confidence = self.read_bill_text(crops[index])
            if not read:
                continue
            taken = choose_reading(text, read, confidence, scores[index])
            if taken is not None:
                texts[index], scores[index] = taken, max(confidence, scores[index])

    def _boxes_and_crops(self, image: np.ndarray) -> tuple[list[np.ndarray], list[np.ndarray]]:
        """Lines of text, looked for again at twice the size if they came out small.

        Text only a few pixels high is what the detector misses. The boxes stay
        in the given image's coordinates; the crops keep the larger pixels.
        """
        boxes = self._detect(image)
        longest = max(image.shape[:2])
        heights = [np.linalg.norm(box[3] - box[0]) for box in boxes]
        small = not heights or float(np.median(heights)) * min(1.0, self._detector_side / longest) < _SMALL_TEXT
        if small and longest * 2 <= _DET_MAX_SIDE:
            larger = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            found = self._detect(larger, _DET_MAX_SIDE)
            return [box / 2 for box in found], [_crop(larger, box) for box in found]
        if small and longest > self._detector_side:
            boxes = self._detect(image, _DET_MAX_SIDE)
        return boxes, [_crop(image, box) for box in boxes]

    def predict(self, source: Any) -> list[dict[str, Any]]:
        image = _read_image(source)
        boxes, crops = self._boxes_and_crops(image)
        if self._recognizer is None:
            texts, scores = self._read_all_with_bill_reader(crops)
            first = (len(texts), sum(map(bool, texts)))
            used_half = False
            # Text larger than the detector learned comes apart into pieces that read as nothing.
            if sum(map(bool, texts)) < _KEPT_SHARE * len(texts):
                found = [box * 2 for box in self._detect(cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))]
                crops = [_crop(image, box) for box in found]
                again, again_scores = self._read_all_with_bill_reader(crops)
                if _labels(again) > _labels(texts):
                    boxes, texts, scores = found, again, again_scores
                    used_half = True
            detector = "bill_detector" if self._detector_settings is BILL_DETECTOR else "Paddle 偵測"
            self.last_read = {"models": f"{detector} + bill_reader", "image": f"{image.shape[1]}x{image.shape[0]}", "found": first[0], "kept": first[1],
                              "half_size": used_half, "kept_in_the_end": sum(map(bool, texts)), "labels": _labels(texts)}
            return [{"rec_texts": texts, "rec_scores": scores, "rec_polys": boxes}]
        read = self._recognize(crops)
        texts, scores = [text for text, _ in read], [score for _, score in read]
        if self._reader is not None:
            self._refine(texts, scores, crops)
        return [{"rec_texts": texts, "rec_scores": scores, "rec_polys": boxes}]


def _labels(texts: list[str]) -> int:
    """How many lines are a label a bill prints; figures say little, as scraps read as figures too."""
    return sum(1 for text in map(normal, texts) if is_label(text) or split_label(text) is not None)


def boxes_from_map(probability: np.ndarray, width: int, height: int, *, thresh: float, box_thresh: float, unclip: float) -> list[np.ndarray]:
    """A detector's map of the middles of lines, as boxes around those lines in a width x height image."""
    new_h, new_w = probability.shape
    mask = (probability > thresh).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        if len(contour) < 4:
            continue
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if area < 3 or perimeter == 0:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        region = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(region, [contour - (x, y)], 1)
        if cv2.mean(probability[y:y + h, x:x + w], region)[0] < box_thresh:
            continue
        # Grown back by what the model's shrunken text regions lost.
        grow = area * unclip / perimeter
        (centre, size, angle) = cv2.minAreaRect(contour)
        box = cv2.boxPoints((centre, (size[0] + 2 * grow, size[1] + 2 * grow), angle))
        box[:, 0] = np.clip(box[:, 0] * width / new_w, 0, width - 1)
        box[:, 1] = np.clip(box[:, 1] * height / new_h, 0, height - 1)
        if min(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[1] - box[2])) <= 3:
            continue
        boxes.append(_order_points(box))
    boxes.sort(key=lambda box: (round(box[0][1] / 10), box[0][0]))
    return boxes


def accept_figures(paddle: str, read: str, confidence: float, paddle_score: float) -> bool:
    """Take the bill reader's figures over Paddle's? Wrong figures are worse than missing ones.

    Only if it is sure, and it does not lose marks: a dropped decimal point
    (346.4 → 3464) reads as a perfectly good figure. One Paddle dropped
    (14272108 → 1427210.8) is the reader seeing more, so that may stand.
    """
    plain = lambda text: re.sub(r"[\s,，*元]", "", text)
    a, b = plain(paddle), plain(read)
    if a == b or confidence < 0.9:
        return False
    digits = lambda text: re.sub(r"\D", "", text)
    marks = lambda text: re.sub(r"\d", "", text)
    if len(digits(a)) != len(digits(b)):
        return paddle_score < 0.7
    if marks(a) == marks(b) or len(marks(b)) > len(marks(a)):
        return True
    return paddle_score < 0.7


def choose_reading(paddle: str, read: str, confidence: float, paddle_score: float) -> str | None:
    """The bill reader's text if it should replace Paddle's, else None."""
    ours, theirs = normal(read), normal(paddle)
    if ours == theirs:
        return None
    if _FIGURES.match(paddle) and re.search(r"\d", paddle):
        return read if _FIGURES.match(read) and accept_figures(paddle, read, confidence, paddle_score) else None
    if is_label(ours):
        # One label for another needs certainty; a garbled one only that it reads as a label.
        return read if confidence >= (0.9 if is_label(theirs) else 0.6) else None
    parts = split_label(ours)
    if parts and confidence >= 0.8:
        return read
    return None


def _order_points(box: np.ndarray) -> np.ndarray:
    """Clockwise from the top left."""
    by_x = box[np.argsort(box[:, 0])]
    left, right = by_x[:2], by_x[2:]
    top_left, bottom_left = left[np.argsort(left[:, 1])]
    top_right, bottom_right = right[np.argsort(right[:, 1])]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def _crop(image: np.ndarray, box: np.ndarray) -> np.ndarray:
    width = int(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3])))
    height = int(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2])))
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    crop = cv2.warpPerspective(image, cv2.getPerspectiveTransform(box, target), (width, height),
                               borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if height / max(width, 1) >= 1.5:
        crop = np.rot90(crop)
    return crop
