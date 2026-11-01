# Build BotlabsBench.exe with PyInstaller (run from the repo root)
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

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build
Remove-Item -Force -ErrorAction SilentlyContinue BotlabsBench.spec

Write-Host "Running PyInstaller (onefile, console for headless+GUI)..."
& $venvPython -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --console `
  --name BotlabsBench `
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

$exe = Join-Path $PSScriptRoot "dist\BotlabsBench.exe"
if (-not (Test-Path $exe)) { throw "Build failed - exe missing" }
Get-Item $exe | Format-List FullName, Length, LastWriteTime
Write-Host "BUILD_OK $exe"
