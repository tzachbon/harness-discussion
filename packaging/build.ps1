#Requires -Version 7.4
# Builds dist\HarnessDiscussion-Setup.exe: PyInstaller app folder, then the Inno Setup 6 installer.
param([switch]$Cuda)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true  # a failing uv/pyinstaller/iscc stops the build
Set-Location (Split-Path $PSScriptRoot)
$cudaArgs = if ($Cuda) { @('--extra', 'cuda') } else { @() }
$cudaCollect = if ($Cuda) { @('--collect-all', 'nvidia.cublas', '--collect-all', 'nvidia.cudnn',
                             '--collect-all', 'nvidia.cuda_nvrtc') } else { @() }
uv sync --locked @cudaArgs
$version = uv run @cudaArgs python -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
if ($env:GITHUB_REF_TYPE -eq 'tag' -and $env:GITHUB_REF_NAME -cne "v$version") {
    throw "Release tag must match pyproject.toml version"
}
New-Item -ItemType Directory -Force build | Out-Null
uv run @cudaArgs python -c "from harness_discussion.icons import icon_image; icon_image(256).save('build/icon.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 256)])"
uv run @cudaArgs pyinstaller @cudaCollect --noconfirm --clean --windowed --name HarnessDiscussion --icon "$PWD\build\icon.ico" `
    --collect-all uiautomation --hidden-import pystray._win32 --hidden-import harness_discussion.win.main --copy-metadata harness-discussion `
    --collect-all kokoro_onnx --collect-all espeakng_loader `
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all tokenizers `
    --distpath build\dist --workpath build\work --specpath build packaging\launcher.py
if ($LASTEXITCODE) { throw "pyinstaller failed" }
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }
& $iscc /Qp "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw "iscc failed" }
Get-Item dist\HarnessDiscussion-Setup.exe
