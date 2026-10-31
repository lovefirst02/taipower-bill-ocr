"""How many of a bill's labelled values were read right: Paddle alone, and with the digit reader.

    python training/eval_reads.py samples/labels_all.json baseline.json [more.json] --model bill_digits.onnx
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

VALUE_LIKE = re.compile(r"^[\d\s,.\-/~*:%至元年月日]+$")


def normal(text: str) -> str:
    text = re.sub(r"[\s,，*]", "", text).replace("~", "至").replace("～", "至")
    return text.replace("元", "")


def found(value: str, texts: list[str]) -> bool:
    pattern = rf"(?<![\d.]){re.escape(normal(value))}(?![\d.])"
    return any(re.search(pattern, text) for text in texts)


class Reader:
    def __init__(self, path: Path) -> None:
        import onnxruntime

        self.session = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    def read(self, crop: np.ndarray) -> tuple[str, float]:
        image = synth.fit_height(crop)
        logits = self.session.run(None, {"image": (image[None, None] * 2 - 1).astype(np.float32)})[0][0]
        probabilities = np.exp(logits - logits.max(-1, keepdims=True))
        probabilities /= probabilities.sum(-1, keepdims=True)
        best = probabilities.argmax(-1)
        out, confidence, last = [], [], 0
        for index, probability in zip(best, probabilities.max(-1)):
            if index and index != last:
                out.append(synth.CHARSET[index - 1])
                confidence.append(probability)
            last = index
        return "".join(out), float(min(confidence)) if confidence else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", type=Path)
    parser.add_argument("reads", type=Path, nargs="+")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--min-confidence", type=float, default=0.8)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    pages = {}
    for path in args.reads:
        pages.update(json.loads(path.read_text(encoding="utf-8")))
    reader = Reader(args.model) if args.model else None

    totals: dict[tuple[str, str, str], Counter] = {}
    for name, truth in labels.items():
        if name not in pages:
            continue
        level = name.split("/")[2] if "degraded" in name else "original"
        lines = pages[name]["lines"]
        paddle = [normal(line["text"]) for line in lines]
        combined = list(paddle)
        if reader:
            image = cv2.imdecode(np.fromfile(name, dtype=np.uint8), cv2.IMREAD_COLOR)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            for index, line in enumerate(lines):
                if not VALUE_LIKE.match(line["text"]) or not re.search(r"\d", line["text"]):
                    continue
                x, y, w, h = line["box"]
                pad = max(2, int(h * 0.12))
                crop = image[max(0, int(y) - pad):int(y + h) + pad, max(0, int(x) - pad):int(x + w) + pad]
                if crop.size == 0:
                    continue
                text, confidence = reader.read(crop)
                if confidence >= args.min_confidence and text:
                    if args.show and normal(text) != combined[index]:
                        print(f"{Path(name).name}: paddle {combined[index]} -> reader {normal(text)} ({confidence:.2f})")
                    combined[index] = normal(text)
        for field, values in truth.items():
            for value in values:
                counter = totals.setdefault((level, field, "all"), Counter())
                counter["total"] += 1
                counter["paddle"] += found(value, paddle)
                counter["reader"] += found(value, combined)
                if args.show and not found(value, combined):
                    print(f"{level} {Path(name).name}: missed {field} {value}")
    print(f"{'level':9} {'field':9} {'values':>6} {'paddle':>7} {'reader':>7}")
    for (level, field, _), counter in sorted(totals.items()):
        print(f"{level:9} {field:9} {counter['total']:6d} {counter['paddle'] / counter['total']:7.1%} {counter['reader'] / counter['total']:7.1%}")
    for level in sorted({key[0] for key in totals}):
        counters = [counter for key, counter in totals.items() if key[0] == level]
        total = sum(counter["total"] for counter in counters)
        print(f"{level:9} {'ALL':9} {total:6d} {sum(c['paddle'] for c in counters) / total:7.1%} {sum(c['reader'] for c in counters) / total:7.1%}")


if __name__ == "__main__":
    main()
