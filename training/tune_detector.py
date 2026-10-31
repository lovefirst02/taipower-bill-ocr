"""Try the detector's thresholds on the tuning pages only; never looks at the test pages.

    set PYTHONPATH=src & set TAIPOWER_ONNX_MODELS=folder
    python training/tune_detector.py samples/truth_rows.json 0.3,0.5,1.8 0.3,0.5,2.2 ...
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eval_detector import split_of, tally  # noqa: E402

from taipower_bill_ocr import onnx_ocr  # noqa: E402
from taipower_bill_ocr.batch import PageDone, PageExtractor, iter_batch  # noqa: E402


def main() -> None:
    truth = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    pages = [path for path in truth if "synth" not in path and split_of(path) == "tuning"]
    extractor = PageExtractor("onnx")
    print(f"{'thresh,box,unclip':20} {'right':>7} {'wrong':>7} {'blank':>7} {'not boxed':>9}   by quality", flush=True)
    for setting in sys.argv[2:]:
        thresh, box, unclip = (float(value) for value in setting.split(","))
        # The engine holds this same dict.
        onnx_ocr.BILL_DETECTOR.update(thresh=thresh, box_thresh=box, unclip=unclip)
        reads = {}
        for path in pages:
            for event in iter_batch([Path(path)], extractor):
                if isinstance(event, PageDone):
                    reads[path] = {"row": dict(event.record.row) if event.record else None,
                                   "lines": [{"text": line.text} for line in (getattr(event.bill, "ocr_lines", None) or [])]}
        table = tally(truth, reads)
        whole: Counter = table[("tuning", "ALL", "ALL")]
        total = sum(whole.values())
        each = "  ".join(f"{quality} {table[('tuning', quality, 'ALL')]['right'] / sum(table[('tuning', quality, 'ALL')].values()):.1%}" for quality in ("original", "phone", "bad"))
        print(f"{setting:20} {whole['right'] / total:7.1%} {whole['wrong'] / total:7.1%} {whole['blank'] / total:7.1%} {whole['not boxed'] / total:9.1%}   {each}", flush=True)


if __name__ == "__main__":
    main()
