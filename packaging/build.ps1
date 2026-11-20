# Builds the RPG Tabletop installer on this computer.
# Usage (from anywhere):  .\packaging\build.ps1
# Result: dist\installers\RPGTabletop-Setup.exe
#
# The installer holds an embedded Python with every dependency, the current code and
# the launcher. It only needs to be rebuilt for new users or when the Python itself
# changes: the code is updated by the launcher from the main branch of GitHub.
#
# Needs Inno Setup 6 (winget install JRSoftware.InnoSetup) and an internet connection
# (Python and the dependencies are downloaded; they are cached in build\cache).

param(
    # Embedded Python: a version for which python.org publishes an "embeddable package"
    [string]$PythonVersion = "3.12.10"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is much faster without it
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Checked([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE) { throw "$what failed (exit code $LASTEXITCODE)" }
}

$init = Get-Content "rpg_tabletop\__init__.py" -Raw
$version = [regex]::Match($init, '__version__\s*=\s*["'']([^"'']+)["'']').Groups[1].Value
if (-not $version) { throw "No __version__ in rpg_tabletop\__init__.py" }
Write-Host "RPG Tabletop $version, Python $PythonVersion" -ForegroundColor Cyan

$cache = Join-Path $root "build\cache"
$stage = Join-Path $root "build\installer"
$python = Join-Path $stage "python\python.exe"
New-Item -ItemType Directory -Force $cache | Out-Null
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null

# 1. Embedded Python ------------------------------------------------------------------
$embed = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $embed)) {
    Write-Host "Downloading Python $PythonVersion"
    Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $embed
}
Expand-Archive $embed (Join-Path $stage "python")

# The ._pth file sets sys.path: standard library, site-packages and ..\app (the code)
$pth = Get-ChildItem (Join-Path $stage "python") -Filter "python*._pth" | Select-Object -First 1
$stdlib = (Get-ChildItem (Join-Path $stage "python") -Filter "python*.zip" | Select-Object -First 1).Name
Set-Content $pth.FullName -Encoding ascii -Value @($stdlib, ".", "Lib\site-packages", "..\app", "import site")

# 2. pip and the dependencies ---------------------------------------------------------
$getPip = Join-Path $cache "get-pip.py"
if (-not (Test-Path $getPip)) {
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
}
Invoke-Checked "get-pip" { & $python $getPip --no-warn-script-location --disable-pip-version-check }
Invoke-Checked "pip install" {
    & $python -m pip install -r requirements.txt --no-warn-script-location --disable-pip-version-check
}

# 3. Code and launcher -----------------------------------------------------------------
$app = Join-Path $stage "app"
New-Item -ItemType Directory -Force $app | Out-Null
Copy-Item -Recurse "rpg_tabletop" (Join-Path $app "rpg_tabletop")
Get-ChildItem $app -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item "requirements.txt" $app
Copy-Item "packaging\launcher.py" (Join-Path $app "launcher.py")
Copy-Item "packaging\launcher.py" (Join-Path $stage "launcher.pyw")

# 4. Check that the bundle can load both applications -----------------------------------
Invoke-Checked "import check" {
    & $python -c "import PySide6.QtWidgets, rpg_tabletop.apps.gm, rpg_tabletop.apps.player; print('imports OK')"
}
Get-ChildItem $app -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 5. Installer ----------------------------------------------------------------------------
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 not found (ISCC.exe): winget install JRSoftware.InnoSetup" }
Invoke-Checked "Inno Setup" { & $iscc "/DAppVersion=$version" "/DStageDir=$stage" "packaging\installer.iss" }

$setup = Join-Path $root "dist\installers\RPGTabletop-Setup.exe"
Write-Host ("Installer: {0} ({1:N0} Mo)" -f $setup, ((Get-Item $setup).Length / 1MB)) -ForegroundColor Green
