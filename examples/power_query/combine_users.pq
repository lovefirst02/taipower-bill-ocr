// 單機版（每人一份 Excel）：把資料夾裡每個人的 taipower_bills_17.xlsx 合併成一張表。
// 每台電腦的程式只寫自己的「<輸出資料夾>\<名字>\taipower_bills_17.xlsx」，這個查詢只讀不寫，所以不會衝突。
// 用法：Excel →「資料」→「取得資料」→「從其他來源」→「空白查詢」→「進階編輯器」，貼上本檔內容，
// 並把「資料夾」改成輸出資料夾（例如 OneDrive 同步的 SharePoint 資料夾）。
// 直接連 SharePoint 時，可把「檔案們」改成：
//   檔案們 = SharePoint.Files("https://<公司>.sharepoint.com/sites/<網站>", [ApiVersion = 15]),
// 再用 Text.StartsWith([Folder Path], "<輸出資料夾完整網址>/") 篩選。
let
    資料夾 = "C:\path\to\Bills OCR",
    檔案們 = Folder.Files(資料夾),
    // 每人一份；略過 Excel 開啟時產生的 ~$ 暫存檔
    每人報表 = Table.SelectRows(檔案們, each [Name] = "taipower_bills_17.xlsx"),
    // 讀工作表而不是表格：還沒有送出任何帳單的檔案只有標題列，沒有 Excel 表格
    讀取 = Table.AddColumn(每人報表, "_資料", each Table.PromoteHeaders(
        Excel.Workbook([Content], false){[Item = "Bills", Kind = "Sheet"]}[Data],
        [PromoteAllScalars = true])),
    合併 = if Table.IsEmpty(讀取) then #table({}, {}) else Table.Combine(讀取[_資料]),
    排序 = if Table.HasColumns(合併, "送出時間") then Table.Sort(合併, {{"送出時間", Order.Ascending}}) else 合併
in
    排序
