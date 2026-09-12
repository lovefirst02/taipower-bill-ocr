# 台電帳單 Windows OCR

離線呼叫 Windows `Windows.Media.Ocr`，保留文字座標後依帳單版面切成 Block，最後輸出方便程式查詢的 Python `dict`／JSON。

## 安裝與執行

需要 Windows 10/11，並在「語言與地區」安裝繁體中文 OCR。

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m taipower_bill_ocr samples\public_commons_2024.jpg --pretty
python -m taipower_bill_ocr samples\high_voltage_asia_university_2024.pdf --pages 1,2,3 --pretty
```

Python API：

```python
from taipower_bill_ocr import extract_bill, extract_pdf

bill = extract_bill("bill.jpg")

print(bill["blocks"]["user_info"]["fields"])
print(bill.get("user_info.tariff_type"))
print(bill.get("charge_info.total_amount_twd"))
print(bill["by_label"]["用戶資訊"]["電價種類"])

# 圖片型 PDF；頁碼從 1 開始，省略 page_numbers 會處理全部頁面
monthly_bills = extract_pdf(
    "high_voltage_bill.pdf",
    page_numbers=[1, 2, 3],
    dpi=200,
)
print(monthly_bills[0].get("charge_info.total_amount_twd"))

# 帳單上方摘要資訊
print(monthly_bills[0].get("payment_info.customer_number"))
print(monthly_bills[0].get("payment_info.due_date"))
print(monthly_bills[0].get("payment_info.total_amount_twd"))
print(monthly_bills[0].get("user_info.billing_period"))
# {"start": "109/12/01", "end": "109/12/31"}

# 不需要預先判斷版型；每一頁都會自動分類
print(monthly_bills[0]["document_type"])
# "high_voltage_bill"、"bank_autopay_receipt" 或 "electricity_bill"

# 高壓帳單第二頁的無框線表格
back_page = extract_pdf(
    "high_voltage_bill.pdf",
    page_numbers=[2],
    dpi=250,
)[0]
print(back_page.get("energy_saving_info.comparison_rows"))
print(back_page.get("meter_info.customer_number"))
print(back_page.get("meter_info.readings"))
```

每個 Block 都有：

```python
{
    "title": "用戶資訊 Basic Info.",
    "fields": {"tariff_type": "表燈非營業用", "...": "..."},
    "raw_lines": ["OCR 原始行", "..."],
}
```

已知欄位使用穩定英文鍵名。新版帳單若增加未知欄位，程式會以帳面原始標籤作為鍵名；完全未知且標題以「資訊」或 `Info.` 結尾的 Block，會建立 `unknown_1`、`unknown_2`。因此資料不會只因 schema 尚未更新就被靜默捨棄。

`quality.warnings` 會揭露推導行為，例如舊式無 Block 版面使用 `legacy_virtual_blocks`，或由帳內金額一致性重建 OCR 漏字的 `total_reconstructed_from_charge`。這些資料適合交由人工覆核。

## 圖片型高壓帳單快速路徑

圖片型 PDF 不使用文字層。每頁由 `pdfplumber` 直接渲染後，紫色／粉紅色帳單先走原有的精準快速路徑；若顏色不符，程式會從不限色的高飽和長條尋找候選區域，再以標題 OCR 確認 Block，避免直接退回整頁辨識。標題 OCR 同時嘗試正常與反相影像，因此支援亮底深色字及深底白字；亮珊瑚紅 `#F66A75` 已加入回歸測試。各區塊會分別裁切、放大及執行繁中／英文數字 OCR。第一頁支援 `payment_info`、`user_info`、`charge_info`、`other_info` 與 `invoice_info`；高壓帳單第二頁支援 `charge_introduction`、`energy_saving_info` 與 `meter_info`。欄位配對只使用同一個 Block 內的文字，避免跨欄誤配。

可透過品質欄位確認實際採用的偵測方式：

```python
print(bill["quality"]["block_detection"])
# "pink_template"、"pink_bank_autopay"、
# "color_independent" 或 "full_page_fallback"
```

`節電資訊` 與 `電表資料` 的表格沒有格線，因此程式不依賴線框，而是保留 OCR 字詞座標，依 Block 內的相對欄位位置重建列。空白的「拆表指數」和「裝表指數」會保留為 `None`，不會把右側的本期指數錯位。例如：

```python
{
    "energy_saving_info": {
        "comparison_rows": [
            {
                "period": "current",
                "days": 31,
                "energy_kwh": 15000,
                "daily_average_kwh": 483.87,
            }
        ]
    },
    "meter_info": {
        "customer_number": "00123456789",
        "billing_period_code": "11001",
        "group_code": "01",
        "group_name": "經常",
        "meter_number": "012345678",
        "multiplier": 600,
        "readings": [
            {
                "meter_type": "01",
                "previous": "03000.7",
                "removed": None,
                "installed": None,
                "current": "04000.0",
            }
        ],
    },
}
```

### 大量混合 PDF

`extract_pdf()` 會逐頁查看帳單標題，自動區分高壓繳費通知單、金融機構代繳繳費憑證與一般電費單，不需要呼叫端先分類。金融代繳憑證會使用獨立 Block 邊界，並將日期存為 `payment_info.payment_date`；高壓通知單仍使用原有路徑及 `payment_info.due_date`。

```python
for bill in extract_pdf("mixed-bills.pdf", dpi=250):
    print(bill["document_type"])
    print(bill.get("payment_info.payment_date"))
    print(bill.get("payment_info.due_date"))
```

每個已偵測 Block 會多出原圖座標：

```python
print(bill["blocks"]["charge_info"]["bounds"])
# {"x": 693, "y": 634, "width": 518, "height": 1188}
```

若已經自行將 PDF 頁面存成圖片，也可以指定相同模式：

```python
bill = extract_bill(
    "rendered-page.png",
    ocr_profile="blockwise_high_accuracy",
)
```

預設 200 DPI 是目前速度與準確率的平衡點。若原始掃描模糊，可改成 `dpi=250` 或 `dpi=300`，代價是辨識時間增加。

## 已驗證版型

- 2013 舊式無 Block 帳單：以虛擬 Block 相容。
- 2021 住宅夏月帳單與使用者提供的完整電子帳單。
- 2024 住宅電子帳單。
- 2024 高壓、三段式時間電價圖片型 PDF，多月份驗證（含契約容量、各時段需量／度數、功率因數、稅額與折扣）。
- 台電官方 2021 高壓兩頁範例，驗證第二頁「節電資訊」與「電表資料」無框線表格。
- 台電官方 2024 金融機構代繳憑證，驗證自動分類、獨立 Block 裁切及付款／用電欄位。
- 合成的未來新增欄位及全新 Block 測試。

執行全部測試：

```powershell
python -m unittest discover -s tests -v
```

> OCR 不可能對任意模糊、遮蔽或極低解析度影像保證 100%。建議以 `quality` 與業務規則覆核關鍵金額；`raw_lines` 始終保留可供追查的 OCR 文字。

## PaddleOCR 對照測試

PaddleOCR 是獨立的實驗路徑，不會改動上面的 Windows OCR。CPU 環境先安裝
PaddlePaddle 3.x 與本專案的 PaddleOCR 選配依賴：

```powershell
python -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install -e ".[paddleocr]"
```

圖片測試：

```python
from pprint import pprint
from taipower_bill_ocr import extract_bill

result = extract_bill(
    "bill.jpg",
    ocr_backend="paddleocr",
    paddle_score_threshold=0.45,
)
pprint(result)
print(result.get("user_info.customer_number"))
print(result.get("user_info.billing_period"))
```

圖片型 PDF 測試（頁碼從 1 開始）：

```python
from taipower_bill_ocr import extract_pdf

results = extract_pdf(
    "high_voltage_bill.pdf",
    page_numbers=[1, 2],
    dpi=250,
    ocr_backend="paddleocr",
    paddle_score_threshold=0.45,
)
for result in results:
    print(result["quality"])
    print(result.get("meter_info.readings"))
    print(result.get("energy_saving_info.comparison_rows"))
```

第一次執行會下載模型，因此會比後續執行久。Paddle PDF 路徑會在所有
選定頁面共用同一個模型，不會每頁重新初始化。Paddle 路徑會先偵測台電帳單
Block，再逐區裁切、增強對比並辨識；每行文字會帶回原圖座標與 `block_key`，因此
同樣能使用第二頁「電表資料」與「節電資訊」的無框線表格重建。若找不到已知版型，
才會退回全頁 OCR。可用同一份 PDF 分別呼叫 `extract_pdf()` 和
`extract_pdf(..., ocr_backend="paddleocr")`。省略 `ocr_backend` 時仍固定使用原本的
Windows OCR，因此既有程式不需要修改：

```python
# 原本 Windows OCR（預設值）
windows_result = extract_pdf("high_voltage_bill.pdf")

# 同一個 Pipeline，明確改用 PaddleOCR
paddle_result = extract_pdf(
    "high_voltage_bill.pdf",
    ocr_backend="paddleocr",
    paddle_score_threshold=0.45,
)
```

### Windows＋Paddle Hybrid

第三條 Pipeline 會以 Windows OCR 為主結果，使用 PaddleOCR 補上 Windows 缺漏的
欄位。兩邊皆有值但內容不同時不會猜測或覆寫，而是在 `quality.warnings` 標記衝突，
並將雙方原值保存在 `quality.hybrid.conflicts`：

```python
results = extract_pdf(
    "high_voltage_bill.pdf",
    dpi=200,
    ocr_backend="hybrid",
    paddle_score_threshold=0.45,
)

result = results[0]
print(result["quality"]["hybrid"]["agreement_count"])
print(result["quality"]["hybrid"]["filled_from_paddle"])
print(result["quality"]["hybrid"]["conflicts"])
print(result["quality"]["warnings"])
```

衝突資料格式：

```python
{
    "path": "other_info.carbon_emission_kg",
    "windows": 3114,
    "paddleocr": 311414,
}
```

每個 Hybrid Block 也會以 `ocr_raw_lines.windows` 與
`ocr_raw_lines.paddleocr` 保留兩套 OCR 原文，方便後續人工核對。單獨使用
`ocr_backend="windows"` 或 `ocr_backend="paddleocr"` 的既有輸出不會改變。
