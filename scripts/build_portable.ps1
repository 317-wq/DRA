# Build Windows portable package: pyinstaller -> dist\DRA\ -> zip
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== sync dependencies =="
uv sync --quiet
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

Write-Host "== install PyInstaller =="
uv pip install pyinstaller --quiet
if ($LASTEXITCODE -ne 0) { throw "pyinstaller install failed" }

Write-Host "== clean old artifacts =="
foreach ($p in @("build", "dist\DRA", "dist\DRA-portable-win64.zip")) {
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

Write-Host "== build =="
uv run pyinstaller packaging/dra-desktop.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "pyinstaller build failed" }

Write-Host "== attach readme =="
Copy-Item "packaging\PORTABLE-README.txt" "dist\DRA\README.txt" -Force

Write-Host "== zip =="
Compress-Archive -Path "dist\DRA\*" -DestinationPath "dist\DRA-portable-win64.zip" -Force

$size = [math]::Round((Get-Item "dist\DRA-portable-win64.zip").Length / 1MB, 1)
Write-Host "Done: dist\DRA-portable-win64.zip ($size MB)"
