from pprint import pprint
from taipower_bill_ocr import extract_pdf
from taipower_bill_ocr.console import configure_utf8_output
from taipower_bill_ocr.mistral_ocr_test import extract_bill_with_mistral
from pathlib import Path
import sys
from time import perf_counter

configure_utf8_output(sys.stdout)
configure_utf8_output(sys.stderr)

current_dir = Path(__file__).parents[2]
sample_dir = current_dir / "samples"


pdf_path = sample_dir / "official_high_voltage_2021_two_page.pdf"

# Reads the key from the MISTRAL_API_KEY environment variable.
result = extract_bill_with_mistral(pdf_path)

pprint(result, sort_dicts=False)