"""Train the bill digit reader on synthetic crops, on a GPU; export ONNX for local use.

    python training/train.py --out tmp/finetune/run1 --steps 60000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402


class DigitReader(nn.Module):
    """A small convolutional + LSTM reader: HEIGHT-tall grey crop → characters, CTC."""

    def __init__(self, classes: int) -> None:
        super().__init__()

        def block(a: int, b: int, pool: tuple[int, int] | None) -> list[nn.Module]:
            layers: list[nn.Module] = [nn.Conv2d(a, b, 3, padding=1, bias=False), nn.BatchNorm2d(b), nn.ReLU(inplace=True)]
            if pool:
                layers.append(nn.MaxPool2d(pool, pool))
            return layers

        self.features = nn.Sequential(
            *block(1, 32, (2, 2)),    # 24
            *block(32, 64, (2, 2)),   # 12, width / 4
            *block(64, 128, None),
            *block(128, 128, (2, 1)),  # 6
            *block(128, 192, None),
            *block(192, 192, (2, 1)),  # 3
            nn.Conv2d(192, 256, (3, 1), bias=False), nn.BatchNorm2d(256), nn.ReLU(inplace=True),  # 1
        )
        self.lstm = nn.LSTM(256, 192, num_layers=2, bidirectional=True, batch_first=True, dropout=0.1)
        self.head = nn.Linear(384, classes)

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        features = self.features(images).squeeze(2).permute(0, 2, 1)
        output, _ = self.lstm(features)
        return self.head(output)


class Synthetic(IterableDataset):
    def __init__(self, seed: int) -> None:
        self.seed = seed

    def __iter__(self):
        worker = get_worker_info()
        rng = random.Random(self.seed + (worker.id if worker else 0) * 1000)
        while True:
            yield synth.sample(rng)


LOOKUP = {char: index + 1 for index, char in enumerate(synth.CHARSET)}


def collate(batch: list[tuple[np.ndarray, str]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, list[str]]:
    width = max(image.shape[1] for image, _ in batch)
    width += (4 - width % 4) % 4
    images = np.ones((len(batch), 1, synth.HEIGHT, width), dtype=np.float32)
    frames = []
    for index, (image, _) in enumerate(batch):
        images[index, 0, :, :image.shape[1]] = image
        frames.append(image.shape[1] // 4)
    texts = [text for _, text in batch]
    targets = torch.tensor([LOOKUP[char] for text in texts for char in text], dtype=torch.long)
    return torch.from_numpy(images) * 2 - 1, targets, torch.tensor(frames), torch.tensor([len(text) for text in texts]), texts


def decode(indices: np.ndarray) -> str:
    out, last = [], 0
    for index in indices:
        if index and index != last:
            out.append(synth.CHARSET[index - 1])
        last = index
    return "".join(out)


@torch.no_grad()
def validate(model: nn.Module, device: torch.device, count: int = 3000) -> float:
    model.eval()
    rng = random.Random(987654)
    samples = [synth.sample(rng) for _ in range(count)]
    right = 0
    for start in range(0, count, 128):
        images, _, frames, _, texts = collate(samples[start:start + 128])
        predictions = model(images.to(device)).argmax(-1).cpu().numpy()
        for prediction, frame, text in zip(predictions, frames, texts):
            right += decode(prediction[:frame]) == text
    model.train()
    return right / count


def export(model: nn.Module, path: Path) -> None:
    import copy

    model = copy.deepcopy(model).cpu().eval()
    torch.onnx.export(
        model, (torch.zeros(1, 1, synth.HEIGHT, 160),), str(path), opset_version=17, dynamo=False,
        input_names=["image"], output_names=["logits"],
        dynamic_axes={"image": {0: "batch", 3: "width"}, "logits": {0: "batch", 1: "frames"}},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=60000)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--init", type=Path, help="start from this checkpoint's layers, where their shape still fits")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda")
    model = DigitReader(len(synth.CHARSET) + 1).to(device)
    start = 0
    if args.resume:
        state = torch.load(args.resume, map_location=device)
        model.load_state_dict(state["model"])
        start = state["step"]
    if args.init:
        state = torch.load(args.init, map_location=device)["model"]
        own = model.state_dict()
        model.load_state_dict({key: value for key, value in state.items() if own[key].shape == value.shape}, strict=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, args.lr, total_steps=args.steps - start + 1, pct_start=0.05)
    scaler = torch.amp.GradScaler("cuda")
    criterion = nn.CTCLoss(blank=0, zero_infinity=True)
    loader = DataLoader(Synthetic(int(time.time())), batch_size=args.batch, num_workers=args.workers, collate_fn=collate,
                        persistent_workers=True, prefetch_factor=4)
    best, started, running = 0.0, time.time(), 0.0
    log = (args.out / "log.jsonl").open("a", encoding="utf-8")
    for step, (images, targets, frames, lengths, _) in enumerate(loader, start=start + 1):
        images, targets = images.to(device, non_blocking=True), targets.to(device, non_blocking=True)
        with torch.amp.autocast("cuda"):
            logits = model(images)
        # CTC on full precision: it is numerically touchy under autocast.
        loss = criterion(logits.float().log_softmax(-1).permute(1, 0, 2), targets, frames, lengths)
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        nn.utils.clip_grad_norm_(model.parameters(), 5)
        scaler.step(optimizer)
        scaler.update()
        schedule.step()
        running += loss.item()
        if step % 500 == 0:
            print(f"step {step} loss {running / 500:.4f} {step / (time.time() - started):.1f} it/s", flush=True)
            running = 0.0
        if step % 5000 == 0 or step == args.steps:
            accuracy = validate(model, device)
            entry = {"step": step, "synthetic_accuracy": accuracy, "minutes": round((time.time() - started) / 60, 1)}
            print(entry, flush=True)
            log.write(json.dumps(entry) + "\n")
            log.flush()
            torch.save({"model": model.state_dict(), "step": step}, args.out / "last.pt")
            if accuracy >= best:
                best = accuracy
                torch.save({"model": model.state_dict(), "step": step}, args.out / "best.pt")
                export(model, args.out / "bill_reader.onnx")
                (args.out / "bill_reader.chars.json").write_text(json.dumps(synth.CHARSET, ensure_ascii=False), encoding="utf-8")
        if step >= args.steps:
            break


if __name__ == "__main__":
    main()
