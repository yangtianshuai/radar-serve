# =============================================================================
# RADAR one-click deploy for Windows (native mode, no WSL/Docker required)
#
#   powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1
#   powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1 -DownloadCkpt
#
# Output is intentionally ASCII-only so it renders correctly in both
# Windows PowerShell 5.1 and PowerShell 7.
# =============================================================================
[CmdletBinding()]
param(
    [switch]$DownloadCkpt,
    [int]$Port = 8000,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# 官方开源项目目录（RADAR_inference / ckpt / download_scripts 都在其下）
$DamoRadarDir = Join-Path $Root 'DAMO-RADAR'
if ($env:DAMO_RADAR_DIR) { $DamoRadarDir = $env:DAMO_RADAR_DIR }
$CkptDir = Join-Path $DamoRadarDir 'ckpt'
if ($env:MODEL_ROOT) { $CkptDir = $env:MODEL_ROOT }
$BackendDir = Join-Path $Root 'webapp\backend'
$FrontendDir = Join-Path $Root 'webapp\frontend'
$RuntimeDir = Join-Path $Root 'webapp\runtime'
$VenvDir = Join-Path $Root '.venv'

function Ok($m)   { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Info($m) { Write-Host "[ .. ] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[FAIL] $m" -ForegroundColor Red }
function Step($m) { Write-Host "`n==> $m" -ForegroundColor White }

$RequiredCkpt = @(
    'checkpoint_radar_pretrain.pth',
    'infer_text_embedding_radar.pt',
    'bert-base-chinese\config.json'
)

# --------------------------------------------------------------- checkpoints
function Get-MissingCkpt {
    $RequiredCkpt | Where-Object { -not (Test-Path (Join-Path $CkptDir $_)) }
}

function Ensure-Ckpt {
    Step 'Checking model checkpoints'
    $missing = Get-MissingCkpt
    if (-not $missing) { Ok "Checkpoints present in $CkptDir"; return }

    Warn 'Missing files:'
    $missing | ForEach-Object { Write-Host "         $_" }
    if (-not $DownloadCkpt) {
        Write-Host @"

Re-run with -DownloadCkpt to fetch them automatically:
  powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1 -DownloadCkpt

Or download manually:
  cd DAMO-RADAR\download_scripts
  python download_checkpoints.py

"@
        throw 'Incomplete checkpoints, aborted'
    }

    Step 'Downloading checkpoints from HuggingFace'
    $py = Get-PythonExe
    & $py -m pip install --quiet --upgrade huggingface_hub
    Push-Location $DamoRadarDir
    try {
        & $py -c @"
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='radar-generalist/RADAR',
    repo_type='model',
    local_dir='./ckpt',
    allow_patterns=[
        'checkpoint_radar_pretrain.pth',
        'infer_text_embedding_radar.pt',
        'bert-base-chinese/*',
    ],
)
"@
    } finally { Pop-Location }

    $missing = Get-MissingCkpt
    if ($missing) { throw "Still missing after download: $($missing -join ', ')" }
    Ok 'Checkpoints ready'
}

# ------------------------------------------------------------------ toolchain
function Get-PythonExe {
    foreach ($cand in @('py', 'python', 'python3')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            if ($cand -eq 'py') { & $cand -3 --version *> $null }
            else { & $cand --version *> $null }
            if ($LASTEXITCODE -eq 0) { return $cand }
        } catch { continue }
    }
    throw 'Python not found. Install Python 3.10+ from https://www.python.org/downloads/'
}

function Check-Prereq {
    Step 'Checking environment'
    $py = Get-PythonExe
    $ver = (& $py -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
    if ([version]$ver -lt [version]'3.10') {
        throw "Python $ver detected, 3.10+ required (PEP 604 union types)"
    }
    Ok "Python $ver"

    $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'NVIDIA' }
    if ($gpu) {
        Ok "GPU: $($gpu[0].Name)"
        if ($gpu.Count -gt 1) { Warn "$($gpu.Count) NVIDIA GPUs detected; only the default device is used" }
    } else {
        Warn 'No NVIDIA GPU detected. Inference will fall back to CPU and take tens of minutes per case.'
        Warn 'Set $env:RADAR_DEVICE = "cpu" if you really want to run on CPU.'
    }

    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw 'Node.js not found (needed to build the frontend). Install from https://nodejs.org/'
    }
    Ok "Node $(node --version)"

    return $py
}

# ------------------------------------------------------------------- frontend
function Build-Frontend {
    Step 'Building frontend'
    Push-Location $FrontendDir
    try {
        if (-not (Test-Path 'node_modules')) {
            Info 'Installing npm packages...'
            npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
        } else {
            Info 'Reusing existing node_modules'
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'vite build failed' }
    } finally { Pop-Location }

    if (-not (Test-Path (Join-Path $FrontendDir 'dist\index.html'))) {
        throw 'Frontend build output missing'
    }
    Ok 'Frontend built to webapp\frontend\dist'
}

# --------------------------------------------------------------------- python
function Setup-Venv($py) {
    Step 'Preparing Python environment'
    if (-not (Test-Path $VenvDir)) {
        Info 'Creating .venv ...'
        & $py -m venv $VenvDir
    }
    $venvPy = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPy)) { throw "venv python not found at $venvPy" }

    & $venvPy -m pip install --quiet --upgrade pip

    $hasTorch = $false
    try { & $venvPy -c 'import torch' *> $null; $hasTorch = ($LASTEXITCODE -eq 0) } catch { }

    if (-not $hasTorch) {
        Info 'Installing torch (CUDA 12.1 wheel, ~2.5 GB, first run takes a while)...'
        Warn 'If your driver only supports an older CUDA, replace cu121 with cu118 in deploy.ps1'
        & $venvPy -m pip install torch==2.4.0+cu121 --index-url https://download.pytorch.org/whl/cu121
        if ($LASTEXITCODE -ne 0) { throw 'torch install failed' }
    } else {
        $tv = (& $venvPy -c 'import torch; print(torch.__version__)').Trim()
        Ok "torch already installed: $tv"
    }

    Info 'Installing inference dependencies...'
    & $venvPy -m pip install --quiet -r (Join-Path $BackendDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'dependency install failed' }
    Ok 'Dependencies ready'

    return $venvPy
}

# ---------------------------------------------------------------------- start
function Start-Service($venvPy) {
    Step 'Starting service'
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

    if (Test-Path (Join-Path $RuntimeDir 'server.pid')) {
        $oldPid = Get-Content (Join-Path $RuntimeDir 'server.pid') -ErrorAction SilentlyContinue
        if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            Warn "Previous instance still running (PID $oldPid); stopping it first"
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }

    $env:MODEL_ROOT = $CkptDir
    $env:CONFIGS_ROOT = $CkptDir
    $env:RADAR_DATA_DIR = $RuntimeDir
    if (-not $env:RADAR_DEVICE) { $env:RADAR_DEVICE = 'cuda' }

    $outLog = Join-Path $RuntimeDir 'server.out.log'
    $errLog = Join-Path $RuntimeDir 'server.err.log'

    $proc = Start-Process -FilePath $venvPy `
        -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '0.0.0.0',
                        '--port', "$Port", '--timeout-keep-alive', '120') `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog

    $proc.Id | Out-File -Encoding ascii (Join-Path $RuntimeDir 'server.pid')
    Ok "Service started in background (PID $($proc.Id))"
    Info "Log: $outLog"
}

function Wait-Healthy {
    Step 'Waiting for service to become ready (model load takes time)'
    $uri = "http://127.0.0.1:$Port/api/health"
    for ($i = 1; $i -le 90; $i++) {
        Start-Sleep -Seconds 4
        $status = ''
        try {
            $status = (Invoke-RestMethod -Uri $uri -TimeoutSec 3).status
        } catch { }

        switch ($status) {
            'ready'        { Ok "Service ready after ~$($i * 4)s"; return $true }
            'ckpt_missing' { Fail "Checkpoints missing - check MODEL_ROOT=$CkptDir"; return $false }
            default        { Write-Host '.' -NoNewline }
        }
    }
    Write-Host ''
    Warn "Timed out after 360s. The model may still be loading; check manually:"
    Write-Host "        Invoke-RestMethod $uri"
    return $false
}

# ----------------------------------------------------------------------- main
Write-Host '============================================================'
Write-Host " RADAR deploy (Windows)  |  port=$Port"
Write-Host " Root: $Root"
Write-Host '============================================================'

Ensure-Ckpt
$py = Check-Prereq
if (-not $SkipFrontend) { Build-Frontend }
$venvPy = Setup-Venv $py
Start-Service $venvPy
Wait-Healthy | Out-Null

$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
    Select-Object -First 1).IPAddress

Write-Host @"

============================================================
 Done
============================================================
 Local      http://localhost:$Port
 LAN        http://$($ip):$Port
 API docs   http://localhost:$Port/docs

 Logs       Get-Content "$RuntimeDir\server.out.log" -Wait
 Stop       Stop-Process -Id (Get-Content "$RuntimeDir\server.pid")
 Restart    powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1 -SkipFrontend

 NOTE: the model stays resident in GPU memory while the service
       runs. Stop it before gaming / heavy GPU work.
============================================================
"@
