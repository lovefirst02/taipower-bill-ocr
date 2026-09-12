from pprint import pprint
from taipower_bill_ocr import extract_pdf
from taipower_bill_ocr.console import configure_utf8_output
from pathlib import Path
import sys
from time import perf_counter

configure_utf8_output(sys.stdout)
configure_utf8_output(sys.stderr)

current_dir = Path(__file__).parents[2]
sample_dir = current_dir / "samples"


pdf_path = sample_dir / "official_high_voltage_2021_two_page.pdf"

print(f"開始辨識：{pdf_path}", flush=True)
started_at = perf_counter()
results = extract_pdf(
    pdf_path,
    page_numbers=[1],
    dpi=150,
    ocr_backend="paddleocr",
    paddle_score_threshold=0.45,
)

for result in results:
    print(f"\n第 {result.get('quality.page_number')} 頁結果：")
    pprint(result)

print(f"\n完成，共 {len(results)} 頁，耗時 {perf_counter() - started_at:.1f} 秒。")
