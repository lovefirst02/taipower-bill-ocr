"""Taipower bills to a CSV, with ONNX models on the CPU: no Paddle, no server.

    TaipowerBillReader.exe bills_folder -o bills.csv
    TaipowerBillReader.exe bill1.pdf bill2.jpg -o bills.csv --details

Run without arguments, it asks for a folder and writes bills.csv into it.
``--details`` adds the source file, the page and what needs checking.
Pages that are not a bill's charge page are left out. A value not read stays
empty, never a guess; opened in Excel, the file reads as Traditional Chinese.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from .batch import FileFinished, PageDone, PageExtractor, find_bill_files, has_bill_values, iter_batch

_KWH = ("尖峰度數", "半尖峰度數", "週六半尖峰度數", "離峰度數")
CSV_COLUMNS = (
    "Billing Period 計費期間",
    "Custom No. 電號",
    *(f"計費度數_{label}" for label in _KWH),
    *(f"轉供度數_{label}" for label in _KWH),
    "基本電費",
    "流動電費",
    "功率因數調整費",
    "預繳費用",
    "稅前應繳總金額",
    "應繳總金額",
)
DETAIL_COLUMNS = ("來源檔案", "頁碼", "需人工檢查")
BACKEND = "onnx"


def _inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            files += find_bill_files(path, recursive=True)
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"找不到檔案或資料夾：{name}")
    return files


def write_csv(files: list[Path], out: Path, extractor: PageExtractor | None = None, *, details: bool = False) -> tuple[int, int, list[str]]:
    """Returns (bills written, other pages left out, files that failed)."""
    extractor = extractor or PageExtractor(BACKEND)
    columns = CSV_COLUMNS + DETAIL_COLUMNS if details else CSV_COLUMNS
    written = skipped = 0
    failures: list[str] = []
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, columns, extrasaction="ignore")
        writer.writeheader()
        for event in iter_batch(files, extractor):
            if isinstance(event, PageDone):
                if event.record is None or (event.bill is not None and not has_bill_values(event.bill)):
                    skipped += 1
                    continue
                writer.writerow({column: _cell(event.record.row.get(column)) for column in columns})
                written += 1
                page = f" 第 {event.page_number} 頁" if event.page_number else ""
                print(f"{event.path.name}{page}：{event.record.row.get('Custom No. 電號') or '電號未讀到'}", flush=True)
            elif isinstance(event, FileFinished) and event.error:
                failures.append(f"{event.path.name}: {event.error}")
                print(f"{event.path.name}：失敗（{event.error}）", flush=True)
    return written, skipped, failures


def _cell(value: object) -> object:
    return "" if value is None else value


def _ask() -> tuple[Path | None, bool]:
    """The folder of bills, and whether to add the source file, page and what needs checking."""
    from tkinter import Tk, filedialog, messagebox

    root = Tk()
    root.withdraw()
    chosen = filedialog.askdirectory(title="選擇放台電帳單的資料夾")
    details = bool(chosen) and messagebox.askyesno("輸出欄位", "要多輸出「來源檔案」「頁碼」「需人工檢查」三欄嗎？")
    root.destroy()
    return (Path(chosen) if chosen else None), details


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把台電帳單（PDF、圖片）讀成 CSV")
    parser.add_argument("inputs", nargs="*", help="帳單檔案或資料夾")
    parser.add_argument("-o", "--output", type=Path, help="輸出的 CSV，預設是資料夾裡的 bills.csv")
    parser.add_argument("--details", action="store_true", help="多輸出來源檔案、頁碼、需人工檢查三欄")
    args = parser.parse_args(argv)

    asked = False
    paths, details = args.inputs, args.details
    if not paths:
        folder, details = _ask()
        if folder is None:
            return 0
        paths, asked = [str(folder)], True
    files = [path for path in _inputs(paths) if path != args.output]
    if not files:
        print("沒有找到帳單檔案（支援 PDF、PNG、JPG、TIF、BMP）")
        return 1
    first = Path(paths[0])
    out = args.output or (first if first.is_dir() else first.parent) / "bills.csv"

    started = time.time()
    written, skipped, failures = write_csv(files, out, details=details)
    print(f"\n完成：{written} 張帳單，略過 {skipped} 頁非帳單頁，{len(failures)} 個檔案失敗，共 {time.time() - started:.1f} 秒")
    print(f"輸出：{out.resolve()}")
    if asked:
        input("\n按 Enter 關閉")
    return 1 if failures and not written else 0


if __name__ == "__main__":
    sys.exit(main())
