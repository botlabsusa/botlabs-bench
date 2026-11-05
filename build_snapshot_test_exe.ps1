# Build BotlabsBench-snapshot-test.exe (onefile + UAC admin). Does NOT overwrite BotlabsBench-1.1.1.exe.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating venv..."
    python -m venv venv
}
Write-Host "Installing dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

$src = Join-Path $PSScriptRoot "src"
$entry = Join-Path $src "main.py"
$glfwDir = Join-Path $PSScriptRoot "venv\Lib\site-packages\glfw"
$outName = "BotlabsBench-snapshot-test"

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build
Remove-Item -Force -ErrorAction SilentlyContinue "$outName.spec"

Write-Host "Running PyInstaller (onefile, console, --uac-admin) -> $outName.exe ..."
& $venvPython -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --uac-admin `
  --name $outName `
  --icon "$PSScriptRoot\assets\botlabsbench.ico" `
  --add-data "$PSScriptRoot\assets\botlabsbench.ico;assets" `
  --add-data "$PSScriptRoot\assets\botlabsbench-128.png;assets" `
  --add-data "$PSScriptRoot\assets\botlabsbench.png;assets" `
  --hidden-import theme `
  --hidden-import sv_ttk `
  --collect-all sv_ttk `
  --paths $src `
  --hidden-import numpy `
  --hidden-import psutil `
  --hidden-import matplotlib `
  --hidden-import matplotlib.backends.backend_tkagg `
  --hidden-import matplotlib.backends.backend_agg `
  --hidden-import openpyxl `
  --hidden-import PIL `
  --hidden-import tests `
  --hidden-import tests.cpu `
  --hidden-import tests.memory `
  --hidden-import tests.disk `
  --hidden-import tests.network `
  --hidden-import tests.gpu `
  --hidden-import validate `
  --hidden-import moderngl `
  --hidden-import glcontext `
  --hidden-import glfw `
  --hidden-import certifi `
  --collect-data certifi `
  --hidden-import version `
  --collect-all moderngl `
  --collect-all glcontext `
  --collect-all glfw `
  --add-binary "$glfwDir\glfw3.dll;glfw" `
  --add-binary "$glfwDir\msvcr120.dll;glfw" `
  --collect-submodules matplotlib `
  --collect-data matplotlib `
  $entry

$exe = Join-Path $PSScriptRoot "dist\$outName.exe"
if (-not (Test-Path $exe)) { throw "Build failed - exe missing" }
Get-Item $exe | Format-List FullName, Length, LastWriteTime
Write-Host "BUILD_OK $exe"
