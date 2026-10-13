# 台電帳單 OCR

離線辨識台電電費帳單（PaddleOCR 與 Windows `Windows.Media.Ocr`），保留文字座標後依帳單版面切成 Block，
輸出方便程式查詢的 Python `dict`／JSON，或整理成 Excel；另附可預覽原圖、逐欄修正的英文桌面程式。

## 安裝與執行

需要 Windows 10/11 與 [uv](https://docs.astral.sh/uv/)。專案已以 `.python-version` 與 `uv.lock` 固定版本，
沒有 Python 時 uv 會自動下載 3.13。
Python 3.14 目前無法使用：PaddlePaddle（含最新 3.3.1 與預覽版）尚未提供 3.14 的套件。

```powershell
uv sync --extra app
```

`--extra app` 會安裝 Excel 匯出與 PaddleOCR；只需要 Windows OCR 的 JSON 輸出時執行 `uv sync` 即可。

```powershell
# 桌面程式
uv run taipower-bill-gui

# 多個帳單輸出成一張 Excel（預設 PaddleOCR + Windows OCR 交叉檢查）
uv run python -m taipower_bill_ocr bill-2024.pdf bill-2025.pdf --excel bills.xlsx

# 單一帳單輸出 JSON（Windows OCR）
uv run python -m taipower_bill_ocr samples\public_commons_2024.jpg --pretty
uv run python -m taipower_bill_ocr samples\high_voltage_asia_university_2024.pdf --pages 1,2,3 --pretty
```

- Windows OCR 需要在「設定 → 時間與語言 → 語言與地區」安裝**中文（繁體）**的 OCR 功能。
- 第一次執行 PaddleOCR 會下載模型（約 30 MB，存於 `%USERPROFILE%\.paddlex`）；離線電腦可設定
  `TAIPOWER_PADDLE_MODELS` 指向含 `PP-OCRv6_small_det`、`PP-OCRv6_small_rec` 的資料夾。

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
uv run python -m unittest discover -s tests -v
```

> OCR 不可能對任意模糊、遮蔽或極低解析度影像保證 100%。建議以 `quality` 與業務規則覆核關鍵金額；`raw_lines` 始終保留可供追查的 OCR 文字。

## PaddleOCR 對照測試

PaddleOCR 是獨立的路徑，不會改動上面的 Windows OCR。`uv sync --extra app`（或只裝
`--extra paddleocr`）已包含 CPU 版 PaddlePaddle 3.2 與 PaddleOCR。

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

Paddle 偶爾會漏掉某一列的數值（文字偵測沒框到），漏掉哪一列會隨圖片處理方式（彩色或灰階）而變。
因此若已知標籤（如基本電費、流動電費、各時段度數、計費期間）右側沒有數字，程式會只裁下那一列重新辨識
（先彩色、再灰階），找回的欄位記錄在 `quality.paddle_row_retry`。

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
## 本期預繳款欄位

一般與 detailed 帳單的「應繳總金額」及「繳費總金額」皆對應
`result.get("charge_info.total_amount_twd")`；稅前總額另存於
`charge_info.pretax_total_twd`。

一般與 detailed 帳單支援「本期預繳」／「本期預繳金額」，取值方式：

```python
prepaid = result.get("charge_info.prepaid_applied_twd")
```

其他計費內容可獨立取值（一般與 detailed 版型皆支援）：

```python
result.get("charge_info.current_prepaid_balance_twd")       # 本期預繳餘額
result.get("charge_info.previous_prepaid_balance_twd")      # 上期預繳餘額
result.get("charge_info.electricity_price_adjustment_twd")  # 電價調整金額
result.get("charge_info.supplemental_charge_refund_twd")    # 補收(退)金額(含停電扣減)
```

各欄位保留帳單原始金額，不互相加總或用餘額推算抵扣款；補收退項目不會因標籤含「退」就自動改為負數。
有預繳餘額、電價調整或補收退標籤時，也停用推測總額的補正。
這是 OCR 原文的 parser 支援，並不保證辨識引擎能讀出所有模糊／缺字影像。

`prepaid_applied_twd` 代表帳單明列的本期預繳抵扣款，不是預繳餘額或歷史預繳總額。
保留印出的正負號與 `0`；缺漏時回傳 `None`，不要自行補零。
Excel 的「預繳費用」可對應此 key。辨識到該標籤時停用推測總額的補正，
避免把抵扣後的零元／低額總額改成流動電費。本欄位不代表自動寫入 Excel。

## 匯出 Excel

Excel 匯出（指令列與桌面程式）預設使用 **PaddleOCR + Windows OCR 交叉檢查**（`--ocr-backend crosscheck`）：

- 一律採用 PaddleOCR 的值；Windows OCR 只補 Paddle 漏讀的欄位，並標記「由 Windows OCR 補值」。
- 兩者讀到不同值時保留 Paddle 的值並標記需檢查，桌面程式會在欄位旁提供「Use …」按鈕一鍵改用 Windows 的值。
- 帳單本身的算式能判斷對錯時不打擾使用者：例如 Windows 讀出的計費期間起訖顛倒，或應繳總金額不等於
  稅前金額 × 1.05 而 Paddle 的值相符，就直接採用 Paddle 的值。
- 只有含計費內容的頁面才會再跑 Windows OCR；處理時間約為只用 Paddle 的兩倍。
  可用 `--ocr-backend paddleocr`（較快）或 `windows` 改為單一引擎。

可一次處理多個檔案並輸出成一張工作表；每一頁有「計費內容」的帳單會變成一列，
沒有計費內容的頁面（例如高壓帳單第二頁）會自動略過：

```powershell
uv run python -m taipower_bill_ocr bill-2024.pdf bill-2025.pdf --excel bills.xlsx
```

Python API（與指令列相同的交叉檢查流程）：

```python
from pathlib import Path

from taipower_bill_ocr.batch import PageDone, PageExtractor, iter_batch
from taipower_bill_ocr.excel_export import write_rows_to_excel

rows = [
    event.record.row
    for event in iter_batch([Path("bill.pdf")], PageExtractor("crosscheck"))
    if isinstance(event, PageDone) and event.record is not None
]
write_rows_to_excel(rows, "bills.xlsx")
```

欄位依序為計費期間、電號、計費度數與轉供度數（尖峰／半尖峰／週六半尖峰／離峰）、基本電費、
流動電費、功率因數調整費、預繳費用、其他調整費用、稅前應繳總金額、應繳總金額，最後附上
來源檔案、頁碼與「需人工檢查」。

- `其他調整費用 = 應繳總金額 − 基本電費 − 流動電費 − 功率因數調整費 − 預繳費用`。
  帳單上計費內容各項都是含稅金額，加總即為應繳總金額（稅前應繳總金額是其未稅金額），因此這一欄
  等於優惠金額、電子帳單優惠、電器租費、配合減少用電減收等其餘項目的合計，連程式尚未認得的新項目
  也會算進來。應繳總金額、基本電費或流動電費缺漏時留空；未辨識到功率因數調整費時以 0 計算並標記，
  沒有預繳時以 0 計算。
- 辨識不到的欄位維持空白，不補 0。
- 「需人工檢查」會列出缺漏欄位、計費期間起訖顛倒、應繳總金額與「稅前金額＋營業稅」不符，
  以及 `quality.warnings` 的推導紀錄。

## 桌面程式（英文介面）

```powershell
uv run taipower-bill-gui
```

選擇資料夾（可含子資料夾）後按 **Start Processing**，會逐頁處理資料夾內所有 PDF 與圖片。

下方 **Review** 區會並排顯示帳單原圖與欄位表單：

- 點選表格中的一列，左側顯示該頁帳單；有問題的列會自動放大到第一個需檢查的欄位。
- 點選右側任一欄位，預覽會縮放到帳單上對應的位置並以紅框標示；找不到精確位置時以橘色虛線框出所在區塊。
- 黃色欄位為需檢查的值。輸入後按 Enter 儲存並跳到下一個需檢查欄位，Esc 還原；修改應繳總金額、基本電費、
  流動電費、功率因數調整費或預繳費用會自動重算其他調整費用。
- **Mark Reviewed & Next**（Ctrl+Enter）標記為已確認並跳到下一筆需檢查的列；Ctrl+↑/↓ 切換列。
  匯出時已確認列的「需人工檢查」欄會加上「已人工確認」。
- 預覽可用滑鼠滾輪縮放、拖曳平移，或按 Fit Page／Zoom to Field。

## 打包成 exe

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
```

產出 `dist\TaipowerBillOCR\`（約 650 MB）與 `dist\TaipowerBillOCR-<版本>.zip`（約 250 MB）。建置使用獨立的
`.venv-build`，不會更動日常使用的 `.venv`。

使用者端：解壓縮後執行 `TaipowerBillOCR.exe`，不需安裝 Python。PaddleOCR 模型已內含於 `models\`，可離線使用；
交叉檢查同樣需要 Windows 的繁體中文 OCR 功能。紀錄與錯誤報告存於 `%LOCALAPPDATA%\TaipowerBillOCR\`。

在新電腦上確認 exe 可用（不開視窗，結果寫入 `%LOCALAPPDATA%\TaipowerBillOCR\self_test.json`）：

```powershell
.\TaipowerBillOCR.exe --self-test C:\path\to\bill.pdf
```
