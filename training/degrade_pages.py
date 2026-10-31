"""Real bill pages as a phone would photograph them, for testing only.

    python training/degrade_pages.py samples/labels.json samples/degraded
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import cv2
import numpy as np

LEVELS = {
    "phone": {"skew": 0.012, "tilt": 1.2, "scale": 0.7, "blur": 0.8, "noise": 5, "jpeg": 55, "shade": 0.8},
    "bad": {"skew": 0.025, "tilt": 2.5, "scale": 0.5, "blur": 1.2, "noise": 10, "jpeg": 35, "shade": 0.65},
}


def degrade(image: np.ndarray, level: dict, rng: random.Random) -> np.ndarray:
    h, w = image.shape[:2]
    jitter = lambda: rng.uniform(-level["skew"], level["skew"])
    source = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    target = np.float32([[w * jitter(), h * jitter()], [w * (1 + jitter()), h * jitter()],
                         [w * (1 + jitter()), h * (1 + jitter())], [w * jitter(), h * (1 + jitter())]])
    out = cv2.warpPerspective(image, cv2.getPerspectiveTransform(source, target), (w, h), borderValue=(200, 200, 200))
    rotate = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-level["tilt"], level["tilt"]), 1)
    out = cv2.warpAffine(out, rotate, (w, h), borderValue=(200, 200, 200))
    shade = np.linspace(level["shade"], 1, w, dtype=np.float32)[None, :, None]
    out = np.clip(out * (shade if rng.random() < 0.5 else shade[:, ::-1]), 0, 255).astype(np.uint8)
    out = cv2.resize(out, (int(w * level["scale"]), int(h * level["scale"])), interpolation=cv2.INTER_AREA)
    out = cv2.GaussianBlur(out, (0, 0), level["blur"])
    noise = np.random.default_rng(rng.getrandbits(32)).normal(0, level["noise"], out.shape)
    out = np.clip(out + noise, 0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, level["jpeg"]])
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def main() -> None:
    labels = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_dir = Path(sys.argv[2])
    extended = dict(labels)
    for name, truth in labels.items():
        image = cv2.imdecode(np.fromfile(name, dtype=np.uint8), cv2.IMREAD_COLOR)
        for level_name, level in LEVELS.items():
            # Each page's distortion is fixed, so every model sees the same photos.
            rng = random.Random(f"{name}-{level_name}")
            folder = out_dir / level_name
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / (Path(name).stem + ".jpg")
            ok, encoded = cv2.imencode(".jpg", degrade(image, level, rng), [cv2.IMWRITE_JPEG_QUALITY, 95])
            encoded.tofile(str(path))
            extended[path.as_posix()] = truth
    Path(sys.argv[1]).with_name("labels_all.json").write_text(json.dumps(extended, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
