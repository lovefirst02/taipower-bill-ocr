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

使用者端：解壓縮後執行 `TaipowerBillOCR.exe`（Tk 桌面程式）或 `TaipowerBillOCRWeb.exe`（單機版網頁，見
「網頁版」的〈沒有伺服器時：單機版〉），不需安裝 Python。PaddleOCR 模型已內含於 `models\`，可離線使用；
交叉檢查同樣需要 Windows 的繁體中文 OCR 功能。
可以放在含中文的資料夾（例如桌面）或中文使用者名稱的電腦：Paddle 無法讀取中文路徑的模型檔，程式會自動改用
`C:\ProgramData\TaipowerBillOCR\` 下的副本（第一次執行時複製，約 20 MB）。紀錄與錯誤報告存於 `%LOCALAPPDATA%\TaipowerBillOCR\`。

在新電腦上確認 exe 可用（不開視窗，結果寫入 `%LOCALAPPDATA%\TaipowerBillOCR\self_test.json`）：

```powershell
.\TaipowerBillOCR.exe --self-test C:\path\to\bill.pdf
```

## SharePoint 資料夾自動處理（OneDrive 同步）

在一台常開的 Windows 電腦上，用 OneDrive 把 SharePoint 資料夾同步到本機（文件庫上方的「同步」或
「新增捷徑到我的檔案」），再讓程式監看該資料夾。新的或內容有變動的 PDF／圖片會自動 OCR，結果寫入
輸出資料夾的 `taipower_bills.xlsx`；輸出資料夾若也在 OneDrive 內，結果會自動傳回 SharePoint。

```powershell
# 持續監看（每 60 秒掃描一次）
uv run taipower-bill-watch "C:\Users\me\公司\Bills" --output "C:\Users\me\公司\Bills OCR"

# 只處理一次就結束（給工作排程器定時執行）
uv run taipower-bill-watch "C:\Users\me\公司\Bills" --output "C:\Users\me\公司\Bills OCR" --once

# 用 exe（不需要 Python）
TaipowerBillOCR.exe --watch "C:\Users\me\公司\Bills" --output "C:\Users\me\公司\Bills OCR"
```

- 程式只負責把帳單上**所有資訊**取出，不預設任何報表格式；需要哪些欄位，由使用者用 Excel
  Power Query、樞紐分析或其他工具自行從總表取得。
- `taipower_bills.xlsx` 的每張工作表都是同名的 Excel 表格，可直接用 Power Query 讀取：

  | 表格 | 內容 |
  |---|---|
  | `Pages` | 每頁一列：檔案、頁碼、帳單類型、電號、計費期間、應繳總金額、是否需人工檢查與各類欄位數 |
  | `Fields` | 每個欄位一列（長格式）：區塊、欄位代號、帳單上的中文名稱、值、證據狀態、另一引擎讀到的值、在頁面上的位置。每列都帶檔案、頁碼、電號與計費期間，不需另外合併 |
  | `TableRows` | 電表指數、節電比較等表格，每個儲存格一列 |
  | `Files` | 每個檔案的處理狀態、頁數、需檢查頁數與錯誤 |

- 證據狀態（`Fields.status`）：`engines_agree` 兩個 OCR 引擎讀到相同值；`engines_disagree` 不同
  （`cross_check_value` 是另一個引擎的值）；`resolved_by_check` 兩個引擎不同，但只有一個讀法通過帳單
  檢查，程式採用該讀法；`filled_from_cross_check` 只有 Windows OCR 讀到；`primary_only` 只有
  PaddleOCR 讀到；`unverified` 只有一個引擎讀到、且程式尚未認得的項目。
- 帳單檢查（`Fields.checks`，例如 `tax:ok(Δ0.3)`）：依樣本帳單實測訂定，每項都是正確帳單必然成立的性質：

  | 檢查 | 內容 |
  |---|---|
  | `charges_sum` | 計費內容各項（含正負號）加總等於應繳總金額（±1 元）；成立時每一項都得到驗證 |
  | `tax` | 稅前應繳總金額 × 1.05 等於應繳總金額（±1 元） |
  | `carbon_per_kwh` | 電力排碳量 ÷ 計費度數介於 0.2–0.8 kg/度（實際約 0.49） |
  | `billing_period` | 計費期間為有效日期，且長度 20–70 天（月繳或雙月繳） |
  | `range` | 每度燃料成本、每度再生基金、每度平均電價、功率因數在合理範圍內 |
  | `date` | `…_date` 欄位為存在的民國日期 |

- 信心等級（`Fields.confidence`）：`high` 兩個引擎一致、由檢查判定，或通過跨欄位算式檢查；
  `medium` 只有一個引擎讀到，但通過範圍或日期檢查；`low` 只有一個引擎讀到且沒有可用的檢查；
  `review` 兩個引擎不同且無法判定，或未通過檢查。頁面有任何 `review` 欄位時 `Pages.needs_review` 為
  TRUE。`Pages.charges_difference_twd`／`tax_difference_twd` 是該頁的算式差額，可用來找出漏讀的項目。
- `recognized`：TRUE 表示欄位對應到固定的欄位代號；FALSE 表示程式尚未認得，以帳單上的中文標籤作為
  `key` 保存。尚未認得的項目若兩個引擎讀到相同的標籤與值，狀態仍為 `engines_agree`。
- `printed_label`：帳單上實際印的（或 OCR 讀到的）標籤與 `label` 不同時才有值，例如 OCR 把
  「功率因數調整費」讀成「功率因數调整簧」時，欄位仍歸到功率因數調整費，原文保留在這裡供追查。
  標籤只容許「同長度、錯一到兩個字」的誤讀，不會把「逾繳費期限」當成「繳費期限」。
- `origin`：`parser` 由版面解析取得；`ocr_line` 由解析未用到的 OCR 文字補取，例如同一行的
  「標籤：值」、跨行的地址、「本次抄表日/收費日：日期;日期」這類合併欄位。
- `Pages.document_type`：`high_voltage_bill`（高壓，含背面）、`electricity_bill`（表燈、低壓等一般
  電費單；依電價種類判斷）、`bank_autopay_receipt`（金融機構代繳憑證）、`unknown`。
- `json\` 資料夾每份帳單一個 JSON，除上述內容外還保留原始 OCR 文字與座標，程式改進後可據此重新解析。
- 處理紀錄在 `taipower_ocr.log`。
- 原始 PDF 不會被搬移或修改。以檔案內容判斷是否處理過；內容不變不重跑，內容改了會重新處理並取代舊結果。
- 檔案須靜止 `--settle` 秒（預設 30）才讀取，避免讀到 OneDrive 下載到一半的檔案；若仍讀到不完整的檔案，
  會記為失敗，檔案完成後自動重新處理。失敗最多重試 3 次。
- 總表被 Excel 開著時會延到下次掃描再寫入，不會遺失結果。
- 同一個輸出資料夾只允許一個監看程式執行。

開機登入後自動執行（需在該使用者登入的工作階段，OneDrive 與 Windows OCR 才可用）：

```powershell
schtasks /Create /TN "Taipower Bill OCR" /SC ONLOGON /RL LIMITED /TR "\"C:\Apps\TaipowerBillOCR\TaipowerBillOCR.exe\" --watch \"C:\Users\me\公司\Bills\" --output \"C:\Users\me\公司\Bills OCR\""
```

## 從總表整理出自己的報表（範例：原本的 17 欄格式）

總表包含所有資訊，報表格式由使用者決定。`examples/power_query/17-column.pq` 是一個 Power Query 範例，
從 `Fields` 表整理出每張帳單一列的 17 欄格式（計費期間、電號、計費／轉供度數、各項電費、其他調整費用、
稅前與應繳總金額，再加上來源檔案、頁碼與需人工檢查），只讀取有「計費內容」的頁面。

- 其他調整費用在查詢中計算：應繳總金額 − 基本電費 − 流動電費 − 功率因數調整費 − 預繳費用。
- 需人工檢查列出缺漏的關鍵欄位，以及報表欄位中信心等級為 `review` 的值（附另一引擎的讀法與檢查結果）。

在 Excel 使用：「資料」→「取得資料」→「從其他來源」→「空白查詢」→「進階編輯器」，貼上 `.pq` 內容，
並把第一行的檔案路徑改成總表位置（總表在 SharePoint 時，檔案開頭的註解有 `SharePoint.Files` 寫法）。
之後總表更新，按「全部重新整理」即可。

也可以用指令直接產生報表檔（需安裝 Excel，產生的檔案保留查詢，日後可在 Excel 中重新整理）：

```powershell
powershell -ExecutionPolicy Bypass -File examples\power_query\build_report.ps1 -SourceWorkbook "C:\Users\me\公司\Bills OCR\taipower_bills.xlsx" -Output "C:\Users\me\公司\Bills OCR\電費帳單17欄.xlsx"
```

以 8 個樣本檔驗證：查詢結果與程式直接匯出的 17 欄 Excel 相同（同樣 8 張帳單、144 個資料格中 141 格
相同）；其餘 3 格是代繳憑證上程式直接匯出讀不到、而總表透過第二階段補回且通過「各項加總＝應繳總金額」
檢查的流動電費、功率因數調整費與據此算出的其他調整費用。

## 網頁版（批量上傳、檢查、證據標記）

Monorepo 結構：OCR 核心在專案根目錄（`src/taipower_bill_ocr`），後端 `apps/api`（FastAPI，與核心同一個
uv workspace），前端 `apps/web`（React + TypeScript + Vite，npm workspaces）。

```powershell
uv sync --all-packages --extra app --group test   # 後端與 OCR
npm install                                        # 前端
npm run dev        # 開發：後端 http://127.0.0.1:8765、前端 http://localhost:5173（自動轉送 /api）
npm run build      # 正式：建置前端後，只需執行後端，網址 http://127.0.0.1:8765
uv run taipower-web
npm test           # 前端與後端測試
```

使用流程：

1. 第一次開啟時輸入名字（不需密碼），之後的上傳與匯出都以此區分。
2. 把多個 PDF／PNG／JPG 拖到左側（或點擊選擇），背景逐一以 PaddleOCR＋Windows OCR 交叉檢查辨識，
   左側顯示進度。帳單背面（電表資料）會保留在完整總表中，但不需檢查。
   「Pause」暫停自己的上傳：等待中的檔案不再處理，正在讀的檔案讀完目前這一頁就停；「Resume」從停下的頁繼續，
   已讀的頁不會重讀（伺服器重新啟動後也一樣）。只影響自己，不影響其他人的上傳。
   失敗的檔案（例如損毀的 PDF）可按「Retry」重試，或按 ✕ 移除；等待中或已暫停的檔案也可以移除，「Remove all」一次移除全部（正在讀的那一個除外）。
   左側帳單清單：滑過帳單按 ✕ 刪除單張，或按「Clear」清除目前分頁（All／Needs review／Draft／Submitted）的全部帳單。
   草稿會連同 OCR 結果與上傳檔一起刪除；已送出的帳單只從清單移除，仍保留在 Excel 與證據 PDF 中
   （要從 Excel 移除請先按「Reopen」再刪除）。
3. 點選帳單：左側為原圖（滑鼠中鍵拖曳、按住空白鍵拖曳，或開啟「✋ Move view」後拖曳可移動畫面；Ctrl + 滾輪縮放），右側為 17 欄欄位。需檢查的欄位為黃色，點選欄位時原圖會捲動並框出位置；
   兩個 OCR 引擎讀法不同時可一鍵改用另一個讀法。修改金額時其他調整費用即時重算。
4. 證據標記：自動在「計費期間」與「稅前應繳總金額」（住宅帳單沒有這一列時改標「應繳總金額」並提示）
   畫上黃色螢光，並在空白處以紅色粗體字標註 **Billing period** 與 **Grey Electricity Cost**。
   帳單背面（例如高壓帳單第 2 頁）「節電資訊」的「本期」度數也會標上螢光並寫 **Electricity Usage (kWh)**；
   有背面的帳單在原圖上方會出現頁籤（Page 1 · bill／Page 2），每一頁的標記可分別調整，
   證據 PDF 包含這張帳單的每一頁（帳單前面的封面或說明頁、後面的背面頁），依原本順序，有沒有標記都保留。本期度數只在背面尋找，不會標在帳單頁；找不到時會提示，可切到該頁手動加上。
   標記可拖曳、拉四個角縮放（文字依比例縮放、對角固定）、修改文字、字級與顏色、新增或刪除（預設顏色可在「Settings」更改）；
   在原圖上雙擊紅字可直接修改文字（Enter 確認、Esc 取消），右側清單也能修改。
   「Tags」列出常用的標註文字：按一下再到原圖點選或框住數值，就同時加上螢光筆與該文字（同一個標籤可重複使用，不會取代既有標記）；
   「Edit tags」可自訂（一行一個，每位使用者各自保存），「Default tags」還原預設。
   上方的警告與標記提示可按 ✕ 個別清除或「Clear all」，存檔後保存，Excel 的「需人工檢查」只保留未清除的項目。
   「Reset」只還原標記，不影響未存的欄位。
   「Preview evidence」顯示伺服器實際輸出的圖，與下載的 PDF／PNG 相同。
5. 「Submit」送出：寫入證據 PDF（PDF 上傳時保留原始向量內容並疊上標記），並重建兩個共用 Excel：
   - `taipower_bills_17.xlsx`：每張帳單一列的 17 欄格式，加上上傳者、送出者、送出時間與證據檔路徑。
   - `taipower_bills.xlsx`：完整總表（Pages／Fields／TableRows／Files），人工修正的值標為
     `reviewer_corrected`，原 OCR 讀值保留在 `cross_check_value`。
   完整總表的 `Pages` 也記錄 `uploaded_by`、`submitted_by` 與 `submitted_at`（資料夾監看程式產生的帳單這三欄為空）。
   兩個 Excel 由伺服器依資料庫整份重建，同一時間只有一個寫入（同時送出不會互相覆蓋）；檔案被 Excel 開啟時會提示，並在背景每 60 秒重試（`TAIPOWER_WORKBOOK_RETRY_SECONDS`），不必等下一次送出。
   放在 SharePoint 時請將這兩個 Excel 設為唯讀：直接在 Excel 修改的內容會在下一次送出時被覆蓋，需要修改請在網頁上按「Reopen」後重新送出。
   按「Reopen」時，該帳單會從兩個 Excel 移除，舊的證據 PDF 移到 `evidence\_reopened\<名字>\`（檔名加上時間），
   不會刪除；重新送出後產生新的證據 PDF。
   證據 PDF 以上傳的原始檔名命名（`帳單.png` → `evidence\<名字>\帳單.pdf`）；同名時依 Windows 方式編號為 `帳單 (2).pdf`。
6. 「Download all PDFs」把自己所有帳單的證據 PDF 打包成一個 ZIP：`submitted/` 是送出時存下的版本，
   `draft/` 依目前的標記產生。「Export my Excel」下載自己上傳的所有帳單（含草稿）17 欄格式。

設定：按右上角「Settings」在畫面上修改，存在 `<資料資料夾>\config.json`。
只能在執行伺服器的那台電腦上修改（沒有登入機制，其他電腦只能查看）。網路位址與 port 重新啟動後生效，其他設定存檔後立即生效。
更換輸出資料夾時，已送出的帳單仍保留在原資料夾，兩個 Excel 會在新資料夾重建並指向原本的證據 PDF。

也可以用環境變數指定；以環境變數指定的項目每次啟動都會優先使用，因此在畫面上會鎖定：

| 變數 | 預設 | 說明 |
|---|---|---|
| `TAIPOWER_DATA_DIR` | `data` | 上傳檔、資料庫、`config.json`（只能用環境變數指定） |
| `TAIPOWER_OUTPUT_DIR` | `data/output` | 兩個 Excel 與 `evidence\`；可設為 OneDrive 同步的 SharePoint 資料夾 |
| `TAIPOWER_WORKBOOKS` | `shared` | `shared`：所有人一份共用 Excel；`per_user`：每人一份，寫在 `<輸出資料夾>\<名字>\` |
| `TAIPOWER_OCR_BACKEND` | `crosscheck` | 或 `paddleocr`、`windows` |
| `TAIPOWER_WORKBOOK_RETRY_SECONDS` | `60` | Excel 被開啟時多久重試一次 |
| `TAIPOWER_HOST` | `127.0.0.1` | `127.0.0.1`：只有本機；`0.0.0.0`：同一網路的其他電腦也能連（沒有登入，任何人都能使用） |
| `TAIPOWER_PORT` | `8765` | |
| `TAIPOWER_GREEN_TOLERANCE` | `3` | 綠電明細每列容許的差額（元） |
| `TAIPOWER_GREEN_SERVICES` | 空 | 轉供服務編號的能源別，一行一個，例如 `1-11210-08-00210 太陽能` |
| `TAIPOWER_HIGHLIGHT_COLOR` | `#ffeb3b` | 螢光筆預設顏色 |
| `TAIPOWER_LABEL_COLOR` | `#d32f2f` | 標註文字預設顏色 |
| `TAIPOWER_DPI` | `200` | PDF 轉圖解析度（只能用環境變數指定；已上傳帳單的標記以此解析度記錄，請勿任意變更） |

### 據點與每月拆分

- 「Settings」→「Sites」設定據點（名稱、代碼如 Intelex 的據點代碼、備註）與各據點的電號（一行一個）。
  電號只比對數字：`00-12-3456-78-9` 與 `00123456789` 視為同一個。只有執行程式的那台電腦可以修改。
- 帳單依電號自動歸屬據點；沒有電號的文件（例如房東分攤單）在檢查頁上方的「Site」手動選擇，手動選擇優先。
- 帳單期間依天數拆到各月份（天數含頭尾，例如 113/12/15～114/01/14 → 12 月 17 天、1 月 14 天），
  度數、轉供度數、未稅與含稅金額依天數比例分配；以十進位計算、尾數用最大餘數法分配，**拆完加總一定等於原值**。
  檢查頁右側「By month」顯示拆分結果。
- 17 欄 Excel 多一張 `Monthly` 工作表：每個據點、電號、月份一列（用電度數、轉供度數、金額、文件類型、證據檔），
  供預算與匯入 Intelex 使用；「Export my Excel」也包含這張表。

### 綠電單據（售電明細、購電通知單、台電轉供費用清單）

三種綠電單據都可以上傳（PDF、截圖或照片皆可），系統依內容自動分辨，清單上分別標示 `售電明細`、`購電通知`、`台電清單`：

| 單據 | 來源 | 讀出的內容 | 單據本身的核對 |
| --- | --- | --- | --- |
| 售電明細 | 售電業者（例如風電） | 逐列電號、能源別、基準價、轉供費率、憑證費率、轉供度數、本月電費 | 本月電費 vs 度數 ×（基準價＋轉供費率＋憑證費率），容許 ±3 元（Settings 可改） |
| 企業購電電費通知單 | 售電業者（例如太陽能） | 購售費率 A、購售電量 B、躉購費率 C、餘電電量 D、電能供應費用 E、憑證審查費 F、未稅、稅金、總額 | A×B、(A−C)×D、未稅 = A×B + (A−C)×D + E + F、稅金 5%、總額，必須完全相符 |
| 轉供費用清單 | 台電 | 服務編號、每個費用表（輸電、配電…）的用戶電號、發電電號、當月服務使用量、費率、費用 | 費用 = 度數 × 費率四捨五入，必須完全相符；照片上被游標遮住而誤讀的費率會自動更正並註明 |

- **多頁與合併的 PDF**：同一個 PDF 裡可以有台電帳單、多份台電清單（不同服務編號）與售電明細，各自成為一筆單據；
  清單或明細跨好幾頁時合併成一份（後面的頁沒有表頭時沿用第一頁的欄位）。台電清單上雖有「台灣電力公司」字樣，不會被當成台電帳單。
- **表頭讀不清楚也能讀**：印得很小、被印章蓋住或拍歪的清單，依每列的樣子（11 碼電號、表號、度數、0.0778 這類費率、費用）辨識；
  沒讀到的費率若用同表常見費率乘得出印出的費用就補上並註明；沒讀到的用戶電號不會沿用上一列，留空請自行填寫。
- **售電明細表格外的費用**：明細上「交易手續費」計入綠電費用（依各電號度數分攤），並讀出稅額與 Total；
  畫面驗算「本月電費＋手續費＋稅額 = Total」，不符時提示。
- **同一電號有太陽能也有風電**：售電明細只和同一服務（度數最接近的那份台電清單）比對，台電帳單則和所有清單的合計比對。
- **以台電轉供費用清單的度數為準。** 清單上有其他買家的電號，系統用「Settings」→「Sites」設定的電號只取本公司的列；
  沒設定 Sites 時全部列入並提示。
- **三方核對**（每月、每個本公司電號）：台電清單度數 vs 售電明細度數（不含「餘電加收」）vs 台電帳單的轉供度數
  （帳單期間不同時依天數換算並註明）。
- **購電通知單 vs 台電清單**（每個服務編號）：B 應等於台電清單本公司電號的度數；E 應等於台電清單本公司電號各費用表的
  費用合計。畫面會列出已讀到的費用表，E 不符時請先確認台電清單的每一頁都已上傳。
- 每種單據都可直接修改（期間、服務編號、各列或各欄位），修改時即時重新核對；核對結果在儲存後更新。
- **OCR 沒讀到的也能補**：台電清單每個費用表都有「+ Row」可新增列（預填同表的費率與發電電號），下方「+ Fee table」新增費用表，
  每列可按 ✕ 刪除。整份沒被認出來（例如被當成台電帳單或其他單據）時，在上方「Document type…」選「台電轉供費用清單」、
  「購電通知單」或「售電明細」，系統會依該種類重讀，讀不到就給空白的表手動填。綠電單據也有「Detect again」可重讀所有頁。
- **報表以售電業者的金額為準**（售電明細的本月電費、通知單的未稅金額）。17 欄 Excel 多四張工作表：
  `GreenPower`（售電明細每列依月份拆分）、`TransferList`（台電清單每列，含是否為本公司電號）、
  `GreenCheck`（各電號三方核對）、`GreenServices`（通知單 vs 台電清單）。綠電單據不會列入 `Bills` 與 `Monthly`。

### 預算 vs 實際

- **預算（Excel 匯入）**：Dashboard 的「Budget vs actual」按「Budget template 2026」下載範本，每月 × Site（含 Entity）一列，
  填灰電度數與費用、太陽能度數與費用、風力度數與費用；總用電與總費用留空會自動加總。在執行伺服器的電腦按「Import budget…」匯入，
  檔案中的月份整月取代，Site 名稱對不上時列出錯誤、整份不匯入。
- **綠電合約單價**（Settings → Green power contracts）：每份合約設定 Entity（或全部）、能源別、合約單價（未稅），以及另外照實際收的
  每度轉供費率與憑證費率、起訖月份。預算只填綠電度數、費用留空時，以度數 × (合約價 + 轉供費率 + 憑證費率) 推估，畫面以 * 標示。
- **比較**：可選總費用、總用電、灰電／太陽能／風力的費用或度數，依月份、Site 或 Entity 分組，顯示預算、實際、差異與差異 %
  （超支紅色）；也套用上方的年份、Entity、Site 篩選。尚無單據的月份顯示「no documents yet」，合計只比較已有單據的月份，並另列全年預算。

### Entity（集團內的公司）

「Settings」→「Entities」設定集團內各公司（名稱、檔名上的代碼如 `TW21`、統一編號），每個 Site 選擇所屬的 Entity：
Entity → 多個廠區／辦公室（Site）→ 多個電號。

- 同一個台電轉供服務編號常有多家公司一起購電，每家各有自己的購電通知單。通知單依序以檢查頁上方「Entity」手動指定、
  所屬 Site 的 Entity、檔名或單據內容中的代碼／統編判斷歸屬。
- **只計入自己的部分**：計入費用 = 該 Entity 的電號在台電清單上的度數 × 該通知單每度單價（未稅合計 ÷ B），再依各電號度數
  分到各廠區；不屬於設定 Entity 的通知單不計入。同一服務編號有多張通知單又判斷不出歸屬時不計入並提示指定。
- 通知單 vs 台電清單的核對改為每張通知單一列，與其 Entity 的電號比對；Dashboard 可依 Entity 篩選並有「By entity」合計，
  Excel 的 `EnergyMix` 與 `GreenServices` 多一欄 Entity。

### Dashboard：灰電與綠電

上方「Dashboard」按鈕顯示每月、每個據點的用電組成（所有使用者的單據；可勾選只看已送出的）：

- **灰電度數**：台電帳單的計費度數（台電扣掉轉供度數後計費的度數）；房東分攤單等沒有計費度數的文件用用電度數。
- **綠電度數**：依序取台電轉供費用清單（本公司電號）、售電業者明細、台電帳單的轉供度數，並分成太陽能、風力等能源別。
  售電明細上寫了能源別就直接用；台電清單與購電通知單依「Settings」→「Green power services」設定的服務編號對應，
  沒設定時參考同月同電號的售電明細，仍不知道的列為「未分類」並提示。
- **綠電占比** = 綠電 ÷（灰電＋綠電），只計算有台電帳單的電號與月份。
- **費用**：灰電為台電帳單稅前金額；綠電為售電業者金額（售電明細的本月電費、購電通知單的未稅合計依台電清單度數分到各電號），
  並算出平均每度單價；太陽能、風力各有自己的費用與每度單價（餘電加收與手續費歸入該電號的能源別）。
- 畫面有各月堆疊長條圖、依據點與依月份的合計表，以及每個電號每月的明細；17 欄 Excel 新增 `EnergyMix` 工作表（已送出的單據）。

### 不是台電帳單的文件（例如房東的電費分攤單）

整棟共用電表、由房東分攤電費時，沒有自己的台電帳單。所有 PDF 與圖片都可以上傳：

- **台電帳單**：照原本方式辨識（版面中有「流動電費」「基本電費」「計費度數」「台灣電力」等台電用語才算）。
- **其他文件**：清單上標示 `other`，自動尋找「計費期間、用電度數、未稅金額、含稅金額」；其他欄位一樣保留，可自行填寫。程式依關鍵字在值的同一行、右側或下方
  （表格欄位）尋找並排序：含公司名稱或「本公司」等字的那一列優先，靠近「全棟、公設、上期」等字的數字降低優先；
  分得出未稅與含稅，只有含稅金額時以 ÷ 1.05 推算並註明；期間長度或每度電價異常時提示。找到的值標黃色，送出前請確認。
- **找不到或抓錯**：按欄位旁的「Pick on image」，再到原圖上點那個數值（或框住它），數值會自動填入，同時畫上螢光與紅字標記。
  圖片模糊讀不到字時，框住後再手動輸入。也可以按「Use …」改用其他候選值。
- **關鍵字**在「Settings」→「Other documents」設定，每行一個：公司名稱、代表本公司的字、期間／度數／金額的關鍵字、要避開的字。
  改了之後對新上傳的文件生效，已上傳的可按「Detect again」重新尋找（會取代目前的值與標記）。
- Excel 多三欄：`用電度數`（台電帳單取自背面節電資訊的本期度數）、`文件類型`（台電帳單／其他單據）、
  `欄位來源`（台電帳單辨識、關鍵字、圖上點選、手動輸入）；完整總表的 `Fields` 以 `document` 區塊記錄這些值與來源。

### 沒有伺服器時：單機版

同一套網頁程式也能只在自己的電腦上執行，不需要伺服器；介面、證據標記與送出流程都和伺服器版相同。

```powershell
TaipowerBillOCRWeb.exe                    # exe（不需 Python）
uv run taipower-web-local                 # 或用 uv（需先 npm run build）
```

- 第一次執行會跳出資料夾選擇視窗，選擇送出結果要存放的位置（例如 OneDrive 同步的 SharePoint 資料夾），之後會記住；
  之後可在「Settings」更換，或加上 `--choose-output` 重新選擇、用 `--output <資料夾>` 直接指定。
- 程式會自動開啟瀏覽器（`http://127.0.0.1:8765`）；黑色視窗保持開啟即可使用，關閉視窗就結束。
  已經在執行時再開一次，只會再開一個瀏覽器分頁。
- 上傳檔與資料庫存在 `%LOCALAPPDATA%\TaipowerBillOCR\web\`，只在這台電腦。
- **每人一份 Excel**：送出的結果寫在 `<輸出資料夾>\<名字>\taipower_bills_17.xlsx` 與 `taipower_bills.xlsx`，
  證據 PDF 在 `<輸出資料夾>\evidence\<名字>\`。多台電腦存到同一個 SharePoint 資料夾時，每台只寫自己的檔案，
  不會互相衝突。請每個人使用不同的名字。
- 合併所有人的結果：用 Power Query「從資料夾」讀取，只讀不寫。範例在 `examples/power_query/`：
  `combine_users.pq`（17 欄表）與 `combine_master.pq`（完整總表的 Pages／Fields／TableRows／Files）。

日後架設伺服器時，把同一套程式部署上去（`TAIPOWER_WORKBOOKS` 可維持 `per_user`，讓合併方式不變，或改回 `shared`），
使用方式不變。

紅色粗體字使用 Windows 內建的 Arial Bold，標籤含中文時改用微軟正黑體粗體。圖片上傳的證據 PDF 以高品質 JPEG 內嵌（約為無損的 1/6 大小）。後端需在 Windows 上執行
（Windows OCR 交叉檢查）。
