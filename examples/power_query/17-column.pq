// 從總表 taipower_bills.xlsx 的 Fields 表，整理出「每張帳單一列」的 17 欄格式。
// 用法：Excel →「資料」→「取得資料」→「從其他來源」→「空白查詢」→「進階編輯器」，貼上本檔內容，
// 並把「來源」改成總表的位置。總表在 SharePoint 時，可改用：
//   來源 = SharePoint.Files("https://<公司>.sharepoint.com/sites/<網站>", [ApiVersion = 15]),
//   檔案 = 來源{[Name = "taipower_bills.xlsx", #"Folder Path" = "<資料夾完整網址>/"]}[Content],
let
    來源 = File.Contents("C:\path\to\Bills OCR\taipower_bills.xlsx"),
    活頁簿 = Excel.Workbook(來源, true),
    Fields = 活頁簿{[Item = "Fields", Kind = "Table"]}[Data],

    // 帳單頁以「檔案＋頁碼」識別；圖片檔沒有頁碼，視為第 1 頁
    加鍵 = Table.AddColumn(Fields, "_頁", each [file] & "#" & Text.From(if [page] = null then 1 else [page]), type text),

    // 需要的欄位：總表的 block.key → 報表欄名（高壓帳單詳細版的度數在 energy_usage 區塊）
    時段 = {{"peak_kwh", "尖峰度數"}, {"semi_peak_kwh", "半尖峰度數"}, {"saturday_semi_peak_kwh", "週六半尖峰度數"}, {"off_peak_kwh", "離峰度數"}},
    度數對照 = List.Combine(List.Transform({"user_info", "energy_usage"}, (區塊) =>
        List.Combine(List.Transform(時段, (t) => {
            {區塊, "energy_consumption." & t{0}, "計費度數_" & t{1}},
            {區塊, "transferred_energy." & t{0}, "轉供度數_" & t{1}}
        })))),
    對照 = #table({"block", "key", "欄位"},
        {
            {"user_info", "billing_period.start", "_起"},
            {"user_info", "billing_period.end", "_迄"},
            {"charge_info", "basic_charge_twd", "基本電費"},
            {"charge_info", "energy_charge_twd", "流動電費"},
            {"charge_info", "power_factor_adjustment_twd", "功率因數調整費"},
            {"charge_info", "prepaid_applied_twd", "預繳費用"},
            {"charge_info", "pretax_total_twd", "稅前應繳總金額"},
            {"charge_info", "total_amount_twd", "應繳總金額"}
        } & 度數對照),

    取欄位 = Table.NestedJoin(加鍵, {"block", "key"}, 對照, {"block", "key"}, "對照", JoinKind.Inner),
    展開 = Table.ExpandTableColumn(取欄位, "對照", {"欄位"}),
    精簡 = Table.SelectColumns(展開, {"_頁", "欄位", "value"}),
    報表欄位 = List.Distinct(對照[欄位]),
    // 同一頁同一欄若有兩個值（不應發生），取第一個而不是讓整個查詢失敗
    樞紐 = Table.Pivot(精簡, 報表欄位, "欄位", "value", each if List.IsEmpty(_) then null else List.First(_)),

    // 只保留有「計費內容」的頁（與原本 17 欄匯出相同：帳單背面、電表資料頁不列入）
    帳單頁 = Table.Distinct(Table.SelectColumns(
        Table.SelectRows(加鍵, each [block] = "charge_info"), {"_頁", "file", "page", "customer_number"})),
    合併頁 = Table.ExpandTableColumn(
        Table.NestedJoin(帳單頁, {"_頁"}, 樞紐, {"_頁"}, "樞紐", JoinKind.LeftOuter), "樞紐", 報表欄位),

    數字 = (x) => if x = null then null else try Number.From(x) otherwise null,
    計費期間 = Table.AddColumn(合併頁, "Billing Period 計費期間",
        each if [_起] <> null and [_迄] <> null then Text.From([_起]) & "~" & Text.From([_迄]) else null, type text),
    // 其他調整費用 = 應繳總金額 − 基本電費 − 流動電費 − 功率因數調整費 − 預繳費用
    其他調整 = Table.AddColumn(計費期間, "其他調整費用", each
        let 總 = 數字([應繳總金額]), 基本 = 數字([基本電費]), 流動 = 數字([流動電費]),
            功率 = 數字([功率因數調整費]), 預繳 = 數字([預繳費用])
        in if 總 = null or 基本 = null or 流動 = null then null
           else Number.Round(總 - 基本 - 流動 - (if 功率 = null then 0 else 功率) - (if 預繳 = null then 0 else 預繳), 2),
        type nullable number),

    // 需人工檢查：本報表欄位中信心等級為 review 的，附上另一引擎讀到的值與檢查結果。
    // 想連報表以外的欄位（如饋線代號）一起列出，把 and 之後的條件拿掉即可。
    報表鍵 = List.Transform(Table.ToRecords(對照), each [block] & "." & [key]),
    待檢欄位 = Table.SelectRows(加鍵, each [confidence] = "review" and List.Contains(報表鍵, [block] & "." & [key])),
    待檢說明 = Table.Group(待檢欄位, {"_頁"}, {{"需人工檢查", each Text.Combine(List.Transform(Table.ToRecords(_), (r) =>
        r[label] & "：" & Text.From(r[value])
        & (if r[cross_check_value] <> null then "／另一引擎 " & Text.From(r[cross_check_value]) else "")
        & (if r[checks] <> null then "（" & r[checks] & "）" else "")), "；"), type text}}),
    加檢查 = Table.ExpandTableColumn(
        Table.NestedJoin(其他調整, {"_頁"}, 待檢說明, {"_頁"}, "待檢", JoinKind.LeftOuter), "待檢", {"需人工檢查"}),

    // 再加上缺漏的關鍵欄位（與原本 17 欄匯出相同）
    合併說明 = Table.AddColumn(加檢查, "_說明", each Text.Combine(List.Select({
            if [應繳總金額] = null then "缺少應繳總金額" else null,
            if [#"Billing Period 計費期間"] = null then "缺少計費期間" else null,
            if [customer_number] = null then "缺少電號" else null,
            if [其他調整費用] = null then "其他調整費用無法計算" else null,
            [需人工檢查]
        }, each _ <> null and _ <> ""), "；"), type text),
    換說明 = Table.RenameColumns(Table.RemoveColumns(合併說明, {"需人工檢查"}), {{"_說明", "需人工檢查"}}),
    改名 = Table.RenameColumns(換說明, {{"customer_number", "Custom No. 電號"}, {"file", "來源檔案"}, {"page", "頁碼"}}),
    欄序 = {"Billing Period 計費期間", "Custom No. 電號"}
        & List.Transform(時段, each "計費度數_" & _{1})
        & List.Transform(時段, each "轉供度數_" & _{1})
        & {"基本電費", "流動電費", "功率因數調整費", "預繳費用", "其他調整費用", "稅前應繳總金額", "應繳總金額",
           "來源檔案", "頁碼", "需人工檢查"},
    補欄 = List.Accumulate(欄序, 改名, (表, 欄) => if Table.HasColumns(表, 欄) then 表 else Table.AddColumn(表, 欄, each null)),
    結果 = Table.Sort(Table.SelectColumns(補欄, 欄序), {{"來源檔案", Order.Ascending}, {"頁碼", Order.Ascending}})
in
    結果
