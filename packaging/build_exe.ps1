# Build the desktop app into dist\TaipowerBillOCR (folder + zip).
# Usage (from the project root):  powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# A dedicated environment keeps `uv sync` from changing the everyday .venv.
$env:UV_PROJECT_ENVIRONMENT = ".venv-build"
if (-not $env:SKIP_SYNC) {
    Write-Host "==> Syncing build environment"
    uv sync --frozen --all-packages --extra app --group build
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
}

# The web app's pages; an existing build is used when npm is not installed.
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host "==> Building the web pages"
    npm ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} elseif (-not (Test-Path "apps\web\dist\index.html")) {
    throw "npm is needed once to build apps\web\dist"
}

$detModel = "PP-OCRv6_small_det"
$recModel = "PP-OCRv6_small_rec"
$modelCache = Join-Path $env:USERPROFILE ".paddlex\official_models"
if (-not ((Test-Path (Join-Path $modelCache $detModel)) -and (Test-Path (Join-Path $modelCache $recModel)))) {
    Write-Host "==> Downloading PaddleOCR models (first build only)"
    uv run --frozen python -c "from taipower_bill_ocr.paddle_ocr import _create_paddle_engine; _create_paddle_engine('chinese_cht')"
    if ($LASTEXITCODE -ne 0) { throw "model download failed" }
}

Write-Host "==> Running PyInstaller"
uv run --frozen pyinstaller packaging\TaipowerBillOCR.spec --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = Join-Path $root "dist\TaipowerBillOCR"
Write-Host "==> Bundling models for offline use"
$models = Join-Path $app "models"
New-Item -ItemType Directory -Force $models | Out-Null
foreach ($model in @($detModel, $recModel)) {
    Copy-Item -Recurse -Force (Join-Path $modelCache $model) $models
}

$version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$zip = Join-Path $root "dist\TaipowerBillOCR-$version.zip"
Write-Host "==> Creating $zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $app -DestinationPath $zip

$sizeMb = [math]::Round((Get-ChildItem -Recurse $app | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "Done: $app ($sizeMb MB), zip: $zip"
