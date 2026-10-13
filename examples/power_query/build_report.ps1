# Build an Excel report from taipower_bills.xlsx using a Power Query file.
# Usage: powershell -ExecutionPolicy Bypass -File build_report.ps1 -SourceWorkbook "<...>\taipower_bills.xlsx" -Output "<...>\report.xlsx" [-QueryFile <file.pq>]
# The report keeps the query, so it can also be refreshed later inside Excel (Data > Refresh All).
param(
    [Parameter(Mandatory)][string]$SourceWorkbook,
    [Parameter(Mandatory)][string]$Output,
    [string]$QueryFile = ""
)
$ErrorActionPreference = "Stop"
if (-not $QueryFile) {
    # $PSScriptRoot is empty in parameter defaults on Windows PowerShell 5.1.
    $QueryFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "17-column.pq"
}
# Power Query needs an absolute path to the source workbook.
$source = (Resolve-Path -LiteralPath $SourceWorkbook).Path
# Resolve against the PowerShell location; .NET's current directory does not follow cd.
$target = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
$m = [System.IO.File]::ReadAllText($QueryFile, [System.Text.Encoding]::UTF8)
$m = $m.Replace('C:\path\to\Bills OCR\taipower_bills.xlsx', $source)
$name = "TaipowerBills17"

$xl = New-Object -ComObject Excel.Application
$xl.DisplayAlerts = $false
$xl.Visible = $false
try {
    $wb = $xl.Workbooks.Add()
    [void]$wb.Queries.Add($name, $m)
    $ws = $wb.Worksheets.Item(1)
    $ws.Name = $name
    $conn = "OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=`$Workbook`$;Location=$name;Extended Properties=`"`""
    $lo = $ws.ListObjects.Add(0, $conn, $true, 1, $ws.Range("A1"))
    $lo.Name = "TaipowerBills"
    $lo.QueryTable.CommandType = 2
    $lo.QueryTable.CommandText = "SELECT * FROM [$name]"
    $lo.QueryTable.BackgroundQuery = $false
    [void]$lo.QueryTable.Refresh($false)
    "rows loaded: " + $lo.ListRows.Count
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target }
    $wb.SaveAs($target, 51)
    $wb.Close($false)
} catch {
    "ERROR: " + $_.Exception.Message
    exit 1
} finally {
    $xl.Quit()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
}
