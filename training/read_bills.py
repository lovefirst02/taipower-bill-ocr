"""Read bill images with the app's own pipeline; write each page's fields to JSON.

Run with the app's environment:
    set PYTHONPATH=src
    python training/read_bills.py out.json img1.png img2.jpg ...
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from taipower_bill_ocr.batch import PageDone, PageExtractor, iter_batch


def main() -> None:
    backend = os.environ.get("BACKEND", "crosscheck")
    out, files = Path(sys.argv[1]), [Path(name) for name in sys.argv[2:]]
    extractor = PageExtractor(backend)
    results = {}
    for path in files:
        started = time.time()
        for event in iter_batch([path], extractor):
            if isinstance(event, PageDone):
                row = dict(event.record.row) if event.record else None
                lines = [{"text": line.text, "box": [line.x, line.y, line.width, line.height]}
                         for line in (getattr(event.bill, "ocr_lines", None) or [])]
                # The first page also loads the models: not a page's reading time.
                seconds = round(time.time() - started, 2) if results else None
                results[path.as_posix()] = {"row": row, "lines": lines, "seconds": seconds}
        print(path.name, results.get(path.as_posix(), {}).get("seconds"), flush=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
