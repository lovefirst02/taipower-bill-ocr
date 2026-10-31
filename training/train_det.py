"""Train the bill text detector on synthetic pages, on a GPU; export ONNX for local use.

A small mobile network with a feature pyramid, in the manner of DBNet but without
its threshold branch: it marks the middle of each line, and the box is grown back
from that. Trained on synth_det.py's pages only: no real bill goes in.

    python training/train_det.py --out tmp/finetune/det1 --steps 20000
    python training/train_det.py --out tmp/finetune/det2 --steps 25000 --lr 1e-3 --seed 2 --init tmp/finetune/det1/last.pt
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import synth_det  # noqa: E402
from taipower_bill_ocr.onnx_ocr import BILL_DETECTOR, boxes_from_map  # noqa: E402

CROP = 512
CROPS_PER_PAGE = 12
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _conv(a: int, b: int, kernel: int = 3, stride: int = 1, groups: int = 1) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(a, b, kernel, stride, kernel // 2, groups=groups, bias=False), nn.BatchNorm2d(b), nn.ReLU(inplace=True))


class Inverted(nn.Module):
    """MobileNet's block: cheap enough for a whole page on a CPU."""

    def __init__(self, a: int, b: int, stride: int = 1, expand: int = 3) -> None:
        super().__init__()
        middle = a * expand
        self.residual = stride == 1 and a == b
        self.layers = nn.Sequential(_conv(a, middle, 1), _conv(middle, middle, 5, stride, middle),
                                    nn.Conv2d(middle, b, 1, bias=False), nn.BatchNorm2d(b))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.layers(x) if self.residual else self.layers(x)


class Detector(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.stem = nn.Sequential(_conv(3, 16, 3, 2), Inverted(16, 16))

        def stage(a: int, b: int, count: int) -> nn.Sequential:
            return nn.Sequential(Inverted(a, b, 2), *[Inverted(b, b) for _ in range(count - 1)])

        self.stage2 = stage(16, 32, 3)   # 1/4
        self.stage3 = stage(32, 56, 4)   # 1/8
        self.stage4 = stage(56, 96, 5)   # 1/16
        self.stage5 = stage(96, 160, 3)  # 1/32
        self.lateral = nn.ModuleList(nn.Conv2d(size, 64, 1) for size in (32, 56, 96, 160))
        self.smooth = nn.ModuleList(_conv(64, 64, 3, groups=64) for _ in range(4))
        self.head = nn.Sequential(_conv(64 * 4, 64, 3), nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False),
                                  _conv(64, 32, 3), nn.Conv2d(32, 1, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        c2 = self.stage2(self.stem(x))
        c3 = self.stage3(c2)
        c4 = self.stage4(c3)
        c5 = self.stage5(c4)
        features = [c2, c3, c4, c5]
        levels = [layer(feature) for layer, feature in zip(self.lateral, features)]
        for index in (2, 1, 0):
            levels[index] = levels[index] + F.interpolate(levels[index + 1], scale_factor=2, mode="nearest")
        levels = [layer(level) for layer, level in zip(self.smooth, levels)]
        joined = [levels[0], *[F.interpolate(level, scale_factor=2 ** (i + 1), mode="bilinear", align_corners=False) for i, level in enumerate(levels[1:])]]
        logits = self.head(torch.cat(joined, 1))
        return F.interpolate(logits, scale_factor=2, mode="bilinear", align_corners=False)


class ForExport(nn.Module):
    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.model(x))


def tensor_of(image: np.ndarray) -> np.ndarray:
    return ((image.astype(np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1)


def maps_of(shape: tuple[int, int], kernels: np.ndarray, unread: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    target = np.zeros(shape, dtype=np.uint8)
    keep = np.ones(shape, dtype=np.uint8)
    if len(kernels):
        cv2.fillPoly(target, [box.round().astype(np.int32) for box in kernels], 1)
    if len(unread):
        cv2.fillPoly(keep, [box.round().astype(np.int32) for box in unread], 0)
    return target, keep


class Pages(IterableDataset):
    def __init__(self, seed: int) -> None:
        self.seed = seed

    def __iter__(self):
        worker = get_worker_info()
        rng = random.Random(self.seed + (worker.id if worker else 0) * 1000)
        # One thread each: a dozen workers each starting a pool only fight over the cores.
        cv2.setNumThreads(0)
        torch.set_num_threads(1)
        while True:
            image, kernels, unread, _ = synth_det.sample_page(rng)
            target, keep = maps_of(image.shape[:2], kernels, unread)
            height, width = image.shape[:2]
            for _ in range(CROPS_PER_PAGE):
                top = rng.randint(0, max(0, height - CROP))
                left = rng.randint(0, max(0, width - CROP))
                piece = np.full((CROP, CROP, 3), 128, dtype=np.uint8)
                wanted = np.zeros((CROP, CROP), dtype=np.uint8)
                kept = np.zeros((CROP, CROP), dtype=np.uint8)
                part = image[top:top + CROP, left:left + CROP]
                high, wide = part.shape[:2]
                piece[:high, :wide] = part
                wanted[:high, :wide] = target[top:top + CROP, left:left + CROP]
                kept[:high, :wide] = keep[top:top + CROP, left:left + CROP]
                yield piece, wanted, kept


def loss_of(logits: torch.Tensor, target: torch.Tensor, keep: torch.Tensor) -> torch.Tensor:
    logits = logits.squeeze(1).float()
    each = F.binary_cross_entropy_with_logits(logits, target, reduction="none") * keep
    positive = (target * keep) > 0.5
    negative = ((1 - target) * keep) > 0.5
    # The hardest background only, about three to each line pixel: most of a page is blank.
    # The cut-off comes from a sample: sorting millions of pixels every step took longer than the model.
    hard = each[negative].detach()
    if hard.numel():
        share = min(1.0, max(int(positive.sum()) * 3, 1000) / hard.numel())
        picked = hard[torch.randint(hard.numel(), (min(hard.numel(), 100_000),), device=hard.device)]
        cut = picked.quantile(1 - share)
        hard = each[negative & (each >= cut)]
    bce = (each[positive].sum() + hard.sum()) / max(int(positive.sum()) + hard.numel(), 1)
    probability = torch.sigmoid(logits) * keep
    wanted = target * keep
    dice = 1 - (2 * (probability * wanted).sum() + 1) / (probability.sum() + wanted.sum() + 1)
    return bce + dice


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    overlap, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    union = cv2.contourArea(a.astype(np.float32)) + cv2.contourArea(b.astype(np.float32)) - overlap
    return overlap / union if union > 0 else 0.0


def score_page(found: list[np.ndarray], truth: np.ndarray, unread: np.ndarray) -> tuple[int, int, int, list[float]]:
    """Lines found at IoU 0.5 or better; lines there are; boxes given, less those on unreadable areas."""
    taken: set[int] = set()
    hits, ratio = 0, []
    for line in truth:
        best, best_iou = -1, 0.5
        for index, box in enumerate(found):
            if index in taken:
                continue
            iou = _iou(line, box)
            if iou >= best_iou:
                best, best_iou = index, iou
        if best >= 0:
            taken.add(best)
            hits += 1
            ratio.append(float(np.linalg.norm(found[best][3] - found[best][0]) / max(np.linalg.norm(line[3] - line[0]), 1)))
    counted = 0
    for index, box in enumerate(found):
        centre = box.mean(0)
        if index in taken or not any(cv2.pointPolygonTest(area.astype(np.float32), (float(centre[0]), float(centre[1])), False) >= 0 for area in unread):
            counted += 1
    return hits, len(truth), counted, ratio


@torch.no_grad()
def validate(model: nn.Module, device: torch.device, pages: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> dict:
    model.eval()
    hits = lines = boxes = 0
    ratios: list[float] = []
    for image, truth, unread in pages:
        high, wide = image.shape[:2]
        resized = cv2.resize(image, (max(32, round(wide / 32) * 32), max(32, round(high / 32) * 32)))
        tensor = torch.from_numpy(tensor_of(resized))[None].to(device)
        with torch.amp.autocast("cuda"):
            probability = torch.sigmoid(model(tensor))[0, 0].float().cpu().numpy()
        found = boxes_from_map(probability, wide, high, **BILL_DETECTOR)
        a, b, c, ratio = score_page(found, truth, unread)
        hits, lines, boxes = hits + a, lines + b, boxes + c
        ratios += ratio
    model.train()
    recall, precision = hits / max(lines, 1), hits / max(boxes, 1)
    return {"recall": round(recall, 4), "precision": round(precision, 4), "f1": round(2 * recall * precision / max(recall + precision, 1e-6), 4),
            "height_ratio": round(float(np.median(ratios)), 3) if ratios else None}


def export(model: nn.Module, path: Path) -> None:
    wrapped = ForExport(copy.deepcopy(model).cpu().eval()).eval()
    torch.onnx.export(wrapped, (torch.zeros(1, 3, 640, 640),), str(path), opset_version=17, dynamo=False,
                      input_names=["image"], output_names=["probability"],
                      dynamic_axes={"image": {0: "batch", 2: "height", 3: "width"}, "probability": {0: "batch", 2: "height", 3: "width"}})


def collate(batch: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    # Bytes between processes: the GPU makes floats of them, a quarter of the data to pass along.
    # The copy matters: a merely transposed array stays channels-last in memory, and torch keeps
    # that layout, which made the GPU's grouped convolutions ten times slower.
    images = np.stack([item[0] for item in batch]).transpose(0, 3, 1, 2).copy()
    return torch.from_numpy(images), torch.from_numpy(np.stack([item[1] for item in batch])), torch.from_numpy(np.stack([item[2] for item in batch]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=20000)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--init", type=Path, help="start from this checkpoint")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)

    device = torch.device("cuda")
    model = Detector().to(device)
    if args.init:
        model.load_state_dict(torch.load(args.init, map_location=device)["model"])
    print(f"{sum(p.numel() for p in model.parameters()) / 1e6:.2f} M parameters", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, args.lr, total_steps=args.steps + 1, pct_start=0.05)
    scaler = torch.amp.GradScaler("cuda")
    rng = random.Random("detector-validation")  # always the same pages, never seen in training
    held_out = []
    for _ in range(40):
        image, _, unread, whole = synth_det.sample_page(rng)
        held_out.append((image, whole, unread))
    loader = DataLoader(Pages(args.seed * 7919), batch_size=args.batch, num_workers=args.workers, collate_fn=collate,
                        persistent_workers=True, prefetch_factor=4, multiprocessing_context="spawn")
    mean = torch.tensor(MEAN, device=device)[None, :, None, None] * 255
    std = torch.tensor(STD, device=device)[None, :, None, None] * 255
    best, started, running = 0.0, time.time(), 0.0
    log = (args.out / "log.jsonl").open("a", encoding="utf-8")
    for step, (images, wanted, kept) in enumerate(loader, start=1):
        images = (images.to(device, non_blocking=True).float() - mean) / std
        wanted, kept = wanted.to(device, non_blocking=True).float(), kept.to(device, non_blocking=True).float()
        with torch.amp.autocast("cuda"):
            logits = model(images)
        loss = loss_of(logits, wanted, kept)
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        nn.utils.clip_grad_norm_(model.parameters(), 5)
        scaler.step(optimizer)
        scaler.update()
        schedule.step()
        running += loss.item()
        if step % 100 == 0:
            print(f"step {step} loss {running / 100:.4f} {step / (time.time() - started):.1f} it/s", flush=True)
            running = 0.0
        if step % 2500 == 0 or step == args.steps:
            entry = {"step": step, "minutes": round((time.time() - started) / 60, 1), **validate(model, device, held_out)}
            print(entry, flush=True)
            log.write(json.dumps(entry) + "\n")
            log.flush()
            torch.save({"model": model.state_dict(), "step": step}, args.out / "last.pt")
            if entry["f1"] >= best:
                best = entry["f1"]
                torch.save({"model": model.state_dict(), "step": step}, args.out / "best.pt")
                export(model, args.out / "bill_detector.onnx")
        if step >= args.steps:
            break


if __name__ == "__main__":
    main()
