[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$venvPython = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
$specPath = Join-Path $projectRoot "razer_opus_x_control.spec"
$buildDir = Join-Path $projectRoot "build"
$distDir = Join-Path $projectRoot "dist"
$exePath = Join-Path $distDir "Razer Opus X Control\\Razer Opus X Control.exe"

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment python not found: $venvPython"
}

if (-not (Test-Path $specPath)) {
    throw "PyInstaller spec file not found: $specPath"
}

Push-Location $projectRoot
try {
    if (-not $SkipDependencyInstall) {
        & $venvPython -m pip install -r requirements.txt -r requirements-build.txt
    }

    if (Test-Path $buildDir) {
        Remove-Item -Path $buildDir -Recurse -Force
    }

    if (Test-Path $distDir) {
        Remove-Item -Path $distDir -Recurse -Force
    }

    & $venvPython -m PyInstaller --clean --noconfirm $specPath

    if (-not (Test-Path $exePath)) {
        throw "Build finished, but executable was not found at: $exePath"
    }

    Write-Host "Build succeeded. Executable: $exePath"
}
finally {
    Pop-Location
}
