from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


MISTRAL_BILL_PROMPT = """
你是台灣電力公司電費帳單的 OCR 資料標準化器。請逐頁閱讀文件，依照提供的
JSON Schema 輸出，不要摘要，也不要根據常識補值。

規則：
1. 僅輸出文件中實際可見的內容；無法確認時填 null，並將原因加入 warnings。
2. 中文欄名使用帳單上實際出現的繁體中文，例如「電號」、「本期」、「度數」。
3. canonical_key 使用穩定 snake_case；未知欄位可填 null，但仍保留中文原標籤。
4. 電號、表號、統一編號、期別、日期及其他識別碼一律輸出為字串，保留前導零。
5. 金額與度數去除千分位逗號，但不要自行換算單位；value 統一輸出為字串。
6. 同名欄位不可互相覆蓋。每次出現都建立獨立 field，並以 occurrence 標示順序，
   使用 context 區分「本期」、「去年同期」、不同表別或不同資料列。
7. 表格必須依視覺上的列與欄重建。無框線表格也要依文字位置還原；空白儲存格填 null，
   不可讓後面的數字向左錯位。
8. 優先辨識：繳費資訊、用戶資訊、計費內容、其他資訊、發票資訊、節電資訊、
   公設分攤明細、電表資料。新出現的 Block 也必須保留。
9. 頁碼從 1 開始。不要把欄位標題誤當成欄位值。
""".strip()


class StandardField(BaseModel):
    label_zh: str = Field(description="帳單上實際出現的繁體中文欄名")
    canonical_key: str | None = Field(
        default=None, description="穩定的 snake_case 欄位名稱"
    )
    value: str | None = Field(default=None, description="OCR 原值的標準化字串")
    unit: str | None = Field(default=None, description="例如 元、kW、kWh、公斤")
    context: str | None = Field(
        default=None, description="例如本期、去年同期、表別01"
    )
    occurrence: int = Field(default=1, ge=1, description="同名欄位的出現順序")


class TableCell(BaseModel):
    column_zh: str
    value: str | None = None
    unit: str | None = None


class TableRow(BaseModel):
    context: str | None = None
    cells: list[TableCell] = Field(default_factory=list)


class StandardTable(BaseModel):
    table_name_zh: str
    canonical_key: str | None = None
    rows: list[TableRow] = Field(default_factory=list)


class StandardBlock(BaseModel):
    block_name_zh: str
    canonical_key: str | None = None
    fields: list[StandardField] = Field(default_factory=list)
    tables: list[StandardTable] = Field(default_factory=list)


class StandardPage(BaseModel):
    page_number: int = Field(ge=1)
    blocks: list[StandardBlock] = Field(default_factory=list)


class StandardBill(BaseModel):
    document_type: str | None = None
    pages: list[StandardPage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _default_annotation_format() -> Any:
    try:
        from mistralai.extra import response_format_from_pydantic_model
    except ImportError as exc:
        raise RuntimeError(
            '尚未安裝 Mistral SDK，請執行：uv add "mistralai==2.9.4"'
        ) from exc
    return response_format_from_pydantic_model(StandardBill)


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return value


def extract_bill_with_mistral(
    pdf_path: str | Path,
    *,
    api_key: str | None = None,
    model: str = "mistral-ocr-latest",
    client: Any | None = None,
    document_annotation_format: Any | None = None,
) -> dict[str, Any]:
    """OCR one local PDF and return structured data plus per-page Markdown."""
    path = Path(pdf_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("目前測試函式只接受 PDF")

    if client is None:
        try:
            from mistralai.client import Mistral
        except ImportError as exc:
            raise RuntimeError(
                '尚未安裝 Mistral SDK，請執行：uv add "mistralai==2.9.4"'
            ) from exc
        resolved_key = api_key or os.getenv("MISTRAL_API_KEY")
        if not resolved_key:
            raise RuntimeError("請設定環境變數 MISTRAL_API_KEY")
        client = Mistral(api_key=resolved_key)

    annotation_format = (
        document_annotation_format
        if document_annotation_format is not None
        else _default_annotation_format()
    )
    uploaded = client.files.upload(
        file={"file_name": path.name, "content": path.read_bytes()},
        purpose="ocr",
    )
    try:
        signed_url = client.files.get_signed_url(file_id=uploaded.id)
        response = client.ocr.process(
            model=model,
            document={
                "type": "document_url",
                "document_url": signed_url.url,
            },
            document_annotation_format=annotation_format,
            document_annotation_prompt=MISTRAL_BILL_PROMPT,
            table_format="markdown",
            include_blocks=True,
            confidence_scores_granularity="block",
        )
    finally:
        client.files.delete(file_id=uploaded.id)

    annotation = response.document_annotation
    if isinstance(annotation, str):
        structured = json.loads(annotation)
    else:
        structured = _plain(annotation) or {}

    pages = [
        {
            "page_number": int(page.index) + 1,
            "markdown": page.markdown,
            "blocks": _plain(getattr(page, "blocks", None)),
            "confidence_scores": _plain(
                getattr(page, "confidence_scores", None)
            ),
        }
        for page in response.pages
    ]
    return {
        "model": response.model,
        "structured": structured,
        "pages": pages,
        "usage_info": _plain(getattr(response, "usage_info", None)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用 Mistral OCR 測試台電圖片型 PDF"
    )
    parser.add_argument("pdf", type=Path, help="本機 PDF 路徑")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("mistral_ocr_result.json"),
        help="JSON 輸出路徑",
    )
    args = parser.parse_args()

    result = extract_bill_with_mistral(args.pdf)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"完成：{args.output.resolve()}")


if __name__ == "__main__":
    main()
