# Build the CSV tool into dist\TaipowerBillReader (folder + zip). ONNX only: no Paddle.
# Usage (from the project root):  powershell -ExecutionPolicy Bypass -File packaging\build_reader.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Its own small environment: this tool ships without the desktop app's engines.
$venv = Join-Path $root ".venv-reader"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    uv venv --python 3.12 $venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
}
$env:VIRTUAL_ENV = $venv
uv pip install onnxruntime opencv-python-headless numpy Pillow pdfplumber openpyxl pyinstaller `
    "winrt-runtime>=3.2" "winrt-Windows.Foundation>=3.2" "winrt-Windows.Foundation.Collections>=3.2" `
    "winrt-Windows.Storage.Streams>=3.2" "winrt-Windows.Globalization>=3.2" "winrt-Windows.Graphics.Imaging>=3.2" `
    "winrt-Windows.Media.Ocr>=3.2" "winrt-Windows.Storage>=3.2"
if ($LASTEXITCODE -ne 0) { throw "installing the build environment failed" }

$models = if ($env:TAIPOWER_ONNX_MODELS) { $env:TAIPOWER_ONNX_MODELS } else { Join-Path $root "models\onnx" }
if (-not (Test-Path (Join-Path $models "bill_reader.onnx"))) { throw "ONNX models not found in $models" }

Write-Host "==> Running PyInstaller"
& (Join-Path $venv "Scripts\pyinstaller.exe") packaging\TaipowerBillReader.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller_reader
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = Join-Path $root "dist\TaipowerBillReader"
Write-Host "==> Bundling models"
$target = Join-Path $app "onnx_models"
New-Item -ItemType Directory -Force $target | Out-Null
# Bills only, and only models trained here (training/): no general model, no Paddle's.
foreach ($name in "bill_detector.onnx", "bill_reader.onnx", "bill_reader.chars.json") {
    Copy-Item (Join-Path $models $name) $target -Force
}

$version = if ($env:READER_VERSION) { $env:READER_VERSION } else { "dev" }
$zip = Join-Path $root "dist\TaipowerBillReader-$version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Start-Sleep -Seconds 5  # antivirus may still hold the fresh files
Compress-Archive -Path $app -DestinationPath $zip
$sizeMb = [math]::Round((Get-ChildItem $app -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "Done: $app ($sizeMb MB), zip: $zip"
