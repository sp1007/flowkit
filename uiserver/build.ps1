# Build FlowKitLauncher.exe (one file, no console window).
#   .\build.ps1            # build
#   .\build.ps1 -Clean     # build sạch, xoá cache trước
param([switch]$Clean)

Set-Location $PSScriptRoot

python -m pip install --upgrade --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { Write-Error "Không cài được pyinstaller"; exit 1 }

if ($Clean) { Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue }

python -m PyInstaller --noconfirm --onefile --windowed `
    --name FlowKitLauncher `
    --distpath . --workpath build --specpath build `
    flowkit_launcher.py
if ($LASTEXITCODE -ne 0) { Write-Error "Build hỏng"; exit 1 }

Write-Host ""
Write-Host "OK -> $PSScriptRoot\FlowKitLauncher.exe" -ForegroundColor Green
Write-Host "config.json nam canh .exe, tao tu dong lan chay dau." -ForegroundColor DarkGray
