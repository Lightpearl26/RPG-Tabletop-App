# Builds both applications and their installers on this computer.
# Usage (from the project root, venv active):  .\packaging\build.ps1
# Result: dist\installers\RPGTabletop-Setup.exe, RPGTabletopPlayer-Setup.exe and SHA256SUMS.txt
#
# Needs PyInstaller (pip install -e .[dev]) and Inno Setup 6 (https://jrsoftware.org/isdl.php,
# or: winget install JRSoftware.InnoSetup).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = python -c "import rpg_tabletop; print(rpg_tabletop.__version__)"
Write-Host "Version $version"

foreach ($spec in "gm", "player") {
    pyinstaller --noconfirm "packaging\$spec.spec"
    if ($LASTEXITCODE) { throw "PyInstaller failed ($spec)" }
}

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 not found (ISCC.exe)" }

foreach ($app in "gm", "player") {
    & $iscc "/DApp=$app" "/DAppVersion=$version" "packaging\installer.iss"
    if ($LASTEXITCODE) { throw "Inno Setup failed ($app)" }
}

$installers = Join-Path $root "dist\installers"
Get-ChildItem $installers -Filter *-Setup.exe | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name
} | Set-Content -Encoding ascii (Join-Path $installers "SHA256SUMS.txt")

Write-Host "Installers written to $installers"
