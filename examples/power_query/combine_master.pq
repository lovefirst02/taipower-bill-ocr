// 單機版（每人一份 Excel）：把每個人總表 taipower_bills.xlsx 的同一張工作表合併。
// 「工作表」可改成 Pages、Fields、TableRows 或 Files；需要幾張就建立幾個查詢。
// 合併後的 Fields 也能當作 17-column.pq 的來源（把它的「Fields = ...」改成引用這個查詢）。
// 用法同 combine_users.pq：貼到空白查詢的進階編輯器，並把「資料夾」改成輸出資料夾。
let
    資料夾 = "C:\path\to\Bills OCR",
    工作表 = "Fields",
    檔案們 = Folder.Files(資料夾),
    每人總表 = Table.SelectRows(檔案們, each [Name] = "taipower_bills.xlsx"),
    讀取 = Table.AddColumn(每人總表, "_資料", each Table.PromoteHeaders(
        Excel.Workbook([Content], false){[Item = 工作表, Kind = "Sheet"]}[Data],
        [PromoteAllScalars = true])),
    合併 = if Table.IsEmpty(讀取) then #table({}, {}) else Table.Combine(讀取[_資料])
in
    合併
