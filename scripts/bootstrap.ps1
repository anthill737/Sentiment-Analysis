<#
.SYNOPSIS
    SA Runner bootstrap — downloads, verifies, and installs the Bundled Runtime on first launch.
    Called by "Start SA Runner.bat". Re-running is safe; already-installed components at the
    pinned version are skipped.

.PARAMETER ScriptDir
    Absolute path to the project root directory (where the .bat file lives).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ScriptDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # Suppresses WebRequest progress bars (much faster)

# ─── Paths ────────────────────────────────────────────────────────────────────
$AppHome    = "$env:LOCALAPPDATA\sa-runner"
$RuntimeDir = "$AppHome\runtime"
$PythonDir  = "$RuntimeDir\python"
$NodeDir    = "$RuntimeDir\node"
$VenvDir    = "$AppHome\venv"
$SkillDir   = "$AppHome\skill"
$DownDir    = "$AppHome\downloads"
$MasterKey  = "$AppHome\master.key"

# ─── Pinned release versions & SHA256 hashes ─────────────────────────────────
# To refresh hashes after a version bump:
#   Python:  decode base64 digest from the .sigstore file on python.org/ftp/python/<ver>/
#   Node:    https://nodejs.org/dist/v<ver>/SHASUMS256.txt
# (Microsoft Word is NOT bundled — the user must have Word installed; the worker
# drives Word via PowerShell COM to convert each report from .docx to .pdf.)
$Py = @{
    Version  = '3.11.9'
    Url      = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip'
    Sha256   = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
    Zip      = "$DownDir\python-3.11.9-embed-amd64.zip"
    Sentinel = "$PythonDir\.pinned-version"
}

$Node = @{
    Version  = '20.19.0'
    Url      = 'https://nodejs.org/dist/v20.19.0/node-v20.19.0-win-x64.zip'
    Sha256   = 'be72284c7bc62de07d5a9fd0ae196879842c085f11f7f2b60bf8864c0c9d6a4f'
    Zip      = "$DownDir\node-v20.19.0-win-x64.zip"
    Sentinel = "$NodeDir\.pinned-version"
}

# ─── Helper functions ─────────────────────────────────────────────────────────

function Write-Step { param([string]$Msg) Write-Host "[SA Runner] $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "[SA Runner] OK  $Msg" -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "[SA Runner] ERR $Msg" -ForegroundColor Red }

function Get-FileSha256 {
    param([string]$Path)
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
}

# Verify a downloaded file's SHA256. On mismatch: print details, optionally delete CleanupDir, throw.
function Confirm-Sha256 {
    param(
        [string]$Path,
        [string]$Expected,
        [string]$Url,
        [string]$CleanupDir = ''
    )
    $actual = Get-FileSha256 -Path $Path
    if ($actual -ne $Expected.ToLower()) {
        Write-Fail "SHA256 mismatch — download may be corrupt or version has changed."
        Write-Host "  URL:      $Url"      -ForegroundColor Yellow
        Write-Host "  Expected: $($Expected.ToLower())" -ForegroundColor Yellow
        Write-Host "  Actual:   $actual"   -ForegroundColor Yellow
        Remove-Item $Path -Force -ErrorAction SilentlyContinue
        if ($CleanupDir -ne '' -and (Test-Path $CleanupDir)) {
            Remove-Item $CleanupDir -Recurse -Force
            Write-Host "  Removed partial directory: $CleanupDir" -ForegroundColor Yellow
        }
        throw "SHA256 mismatch for $Url"
    }
    Write-Ok "SHA256 verified."
}

function Invoke-Download {
    param([string]$Url, [string]$Dest)
    Write-Step "Downloading $(Split-Path $Dest -Leaf) ..."
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
    } catch {
        Write-Fail "Download failed: $Url"
        Write-Host "  Error: $_" -ForegroundColor Yellow
        throw
    }
}

function Test-PinnedVersion {
    param([string]$Sentinel, [string]$Version)
    return (Test-Path $Sentinel) -and ((Get-Content $Sentinel -Raw -ErrorAction SilentlyContinue).Trim() -eq $Version)
}

function Set-PinnedVersion {
    param([string]$Sentinel, [string]$Version)
    Set-Content -Path $Sentinel -Value $Version -Encoding UTF8 -NoNewline
}

# ─── Create top-level directories ────────────────────────────────────────────
foreach ($dir in @($AppHome, $RuntimeDir, $DownDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

# ─── 1. Python 3.11 embeddable ────────────────────────────────────────────────
if (Test-PinnedVersion $Py.Sentinel $Py.Version) {
    Write-Ok "Python $($Py.Version) embeddable already installed — skipping."
} else {
    Write-Step "Setting up Python $($Py.Version) embeddable..."
    if (Test-Path $PythonDir) { Remove-Item $PythonDir -Recurse -Force }

    if (-not (Test-Path $Py.Zip)) {
        Invoke-Download $Py.Url $Py.Zip
    }
    Confirm-Sha256 -Path $Py.Zip -Expected $Py.Sha256 -Url $Py.Url -CleanupDir $PythonDir

    Write-Step "Extracting Python..."
    Expand-Archive -Path $Py.Zip -DestinationPath $PythonDir

    # Enable site-packages so pip can install into the embeddable distribution
    $pthFile = Get-ChildItem $PythonDir -Filter 'python3*._pth' | Select-Object -First 1
    if ($pthFile) {
        (Get-Content $pthFile.FullName -Raw) -replace '#import site', 'import site' |
            Set-Content $pthFile.FullName -NoNewline
    } else {
        Write-Host "  WARNING: could not locate python3*._pth to enable site-packages." -ForegroundColor Yellow
    }

    # Bootstrap pip via get-pip.py (not included in embeddable ZIPs)
    $getPipPath = "$DownDir\get-pip.py"
    if (-not (Test-Path $getPipPath)) {
        Invoke-Download 'https://bootstrap.pypa.io/get-pip.py' $getPipPath
    }
    Write-Step "Bootstrapping pip..."
    & "$PythonDir\python.exe" $getPipPath --no-warn-script-location --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed (exit $LASTEXITCODE)" }

    Set-PinnedVersion $Py.Sentinel $Py.Version
    Write-Ok "Python $($Py.Version) ready."
}

# ─── 2. Virtual environment ───────────────────────────────────────────────────
if (Test-Path "$VenvDir\Scripts\python.exe") {
    Write-Ok "Virtual environment already exists — skipping creation."
} else {
    Write-Step "Creating virtual environment at $VenvDir ..."
    & "$PythonDir\python.exe" -m pip install --quiet virtualenv
    if ($LASTEXITCODE -ne 0) { throw "Failed to install virtualenv" }
    & "$PythonDir\python.exe" -m virtualenv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment at $VenvDir" }
    Write-Ok "Virtual environment created."
}

# Install Python dependencies — skip if requirements.txt is unchanged since last install
$RequirementsTxt  = Join-Path $ScriptDir 'requirements.txt'
if (-not (Test-Path $RequirementsTxt)) { throw "requirements.txt not found at $RequirementsTxt" }
$PipHashSentinel  = "$VenvDir\.pip-hash"
$reqHash          = (Get-FileHash $RequirementsTxt -Algorithm SHA256).Hash.ToLower()
$storedReqHash    = if (Test-Path $PipHashSentinel) {
    (Get-Content $PipHashSentinel -Raw -ErrorAction SilentlyContinue).Trim().ToLower()
} else { '' }
if ($reqHash -ne $storedReqHash) {
    Write-Step "Installing Python dependencies..."
    & "$VenvDir\Scripts\pip.exe" install --quiet -r $RequirementsTxt
    if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }
    Set-Content -Path $PipHashSentinel -Value $reqHash -Encoding UTF8 -NoNewline
    Write-Ok "Python dependencies installed."
} else {
    Write-Ok "Python dependencies up-to-date — skipping."
}

# ─── 3. Node 20 LTS ──────────────────────────────────────────────────────────
if (Test-PinnedVersion $Node.Sentinel $Node.Version) {
    Write-Ok "Node $($Node.Version) already installed — skipping."
} else {
    Write-Step "Setting up Node $($Node.Version)..."
    if (Test-Path $NodeDir) { Remove-Item $NodeDir -Recurse -Force }

    if (-not (Test-Path $Node.Zip)) {
        Invoke-Download $Node.Url $Node.Zip
    }
    Confirm-Sha256 -Path $Node.Zip -Expected $Node.Sha256 -Url $Node.Url -CleanupDir $NodeDir

    Write-Step "Extracting Node..."
    $TempExtract = "$DownDir\_node_extract"
    if (Test-Path $TempExtract) { Remove-Item $TempExtract -Recurse -Force }
    Expand-Archive -Path $Node.Zip -DestinationPath $TempExtract

    # The ZIP contains a single top-level folder (e.g. node-v20.19.0-win-x64)
    $inner = Get-ChildItem $TempExtract | Select-Object -First 1
    Move-Item $inner.FullName $NodeDir
    Remove-Item $TempExtract -Recurse -Force -ErrorAction SilentlyContinue

    Set-PinnedVersion $Node.Sentinel $Node.Version
    Write-Ok "Node $($Node.Version) ready."
}

# ─── 4. Verify Microsoft Word is available (used for DOCX → PDF) ─────────────
# The worker invokes Word via PowerShell COM (Word.Application) to convert the
# rendered .docx to .pdf at the end of each run. We surface a friendly error
# here so the user finds out at launcher startup, not at job-render time.
Write-Step "Checking for Microsoft Word..."
try {
    $wordCheck = New-Object -ComObject Word.Application
    $wordCheck.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($wordCheck) | Out-Null
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
    Write-Ok "Microsoft Word detected — will be used for PDF conversion."
} catch {
    Write-Host "[SA Runner] WARN: Microsoft Word is not installed or its COM type library is unavailable." -ForegroundColor Yellow
    Write-Host "  SA Runner will still produce a .docx report, but the .pdf conversion step will fail." -ForegroundColor Yellow
    Write-Host "  Install Microsoft Word (any 2016+ version) to enable PDF output." -ForegroundColor Yellow
}

# ─── 5. Extract Skill ────────────────────────────────────────────────────────
$SkillZip = Join-Path $ScriptDir 'sentiment-analysis.skill'
if (-not (Test-Path $SkillZip)) { throw "Skill archive not found: $SkillZip" }

# Hash-check the bundled .skill against a sentinel so that updates to the
# skill (a new .skill ZIP dropped into the project) trigger a re-extraction.
$SkillHashSentinel = "$AppHome\.skill-zip-hash"
$skillZipHash      = (Get-FileHash $SkillZip -Algorithm SHA256).Hash.ToLower()
$storedSkillHash   = if (Test-Path $SkillHashSentinel) {
    (Get-Content $SkillHashSentinel -Raw -ErrorAction SilentlyContinue).Trim().ToLower()
} else { '' }

$skillHasContent = (Test-Path $SkillDir) -and (@(Get-ChildItem $SkillDir -Force -ErrorAction SilentlyContinue).Count -gt 0)
$skillUpToDate   = $skillHasContent -and ($skillZipHash -eq $storedSkillHash)

if ($skillUpToDate) {
    Write-Ok "Skill already extracted (hash matches) — skipping."
} else {
    if ($skillHasContent) {
        Write-Step "Skill archive changed — re-extracting to $SkillDir ..."
    } else {
        Write-Step "Extracting Skill to $SkillDir ..."
    }
    if (Test-Path $SkillDir) { Remove-Item $SkillDir -Recurse -Force }
    $TempSkill = "$DownDir\_skill_extract"
    if (Test-Path $TempSkill) { Remove-Item $TempSkill -Recurse -Force }

    # PowerShell's Expand-Archive refuses any extension other than .zip, but
    # the .skill file is just a renamed ZIP. Copy to a temp .zip path first.
    $TempZipForSkill = Join-Path $DownDir ("_skill_" + [guid]::NewGuid().ToString("N") + ".zip")
    Copy-Item -Path $SkillZip -Destination $TempZipForSkill -Force
    try {
        Expand-Archive -Path $TempZipForSkill -DestinationPath $TempSkill -Force
    } finally {
        Remove-Item -Path $TempZipForSkill -Force -ErrorAction SilentlyContinue
    }

    # Handle both: ZIP with a top-level directory, and ZIP with files at root.
    # Wrap in @(...) so that even when Get-ChildItem returns a single object,
    # strict mode sees it as an array (which has .Count). Otherwise PowerShell
    # throws PropertyNotFoundStrict on the .Count access.
    $children = @(Get-ChildItem $TempSkill)
    if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
        Move-Item $children[0].FullName $SkillDir
        Remove-Item $TempSkill -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Move-Item $TempSkill $SkillDir
    }

    # Record the hash so subsequent launches skip re-extraction unless the
    # bundled .skill file changes.
    Set-Content -Path $SkillHashSentinel -Value $skillZipHash -Encoding UTF8 -NoNewline

    Write-Ok "Skill extracted."
}

# npm install inside Skill — skip if package manifest is unchanged and node_modules exists
$NpmCmd          = if (Test-Path "$NodeDir\npm.cmd") { "$NodeDir\npm.cmd" } else { "$NodeDir\npm" }
$pkgLockPath     = "$SkillDir\package-lock.json"
$pkgJsonPath     = "$SkillDir\package.json"
$NpmHashSentinel = "$SkillDir\.npm-hash"
$pkgSource       = if (Test-Path $pkgLockPath) { $pkgLockPath } elseif (Test-Path $pkgJsonPath) { $pkgJsonPath } else { $null }
$pkgHash         = if ($pkgSource) { (Get-FileHash $pkgSource -Algorithm SHA256).Hash.ToLower() } else { 'no-package-file' }
$storedNpmHash   = if (Test-Path $NpmHashSentinel) {
    (Get-Content $NpmHashSentinel -Raw -ErrorAction SilentlyContinue).Trim().ToLower()
} else { '' }
$nmExists        = Test-Path "$SkillDir\node_modules"
if ($pkgHash -ne $storedNpmHash -or -not $nmExists) {
    Write-Step "Running npm install inside Skill..."
    Push-Location $SkillDir
    try {
        & $NpmCmd install --silent 2>&1 | ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed in Skill directory" }
    } finally {
        Pop-Location
    }
    Set-Content -Path $NpmHashSentinel -Value $pkgHash -Encoding UTF8 -NoNewline
    Write-Ok "Skill dependencies ready."
} else {
    Write-Ok "Skill dependencies up-to-date — skipping npm install."
}

# Install Skill's Python dependencies (pytrends, matplotlib, pillow) into the
# embeddable Python — that's the interpreter the worker invokes for skill
# scripts. The hash is composed of (a) the dependency list and (b) the skill
# bundle hash, so:
#   - If we change the hardcoded dependency list here, sentinel mismatch → re-install.
#   - If the skill bundle is updated (different .skill file), sentinel mismatch → re-install.
# This protects against future skill versions that need different Python packages.
$SkillPyDeps         = @('pytrends', 'matplotlib', 'pillow')
$SkillPyHashSentinel = "$SkillDir\.skill-py-hash"
$SkillPyHashValue    = ($SkillPyDeps -join ',') + '|v2-embeddable|skill=' + $skillZipHash
$storedSkillPyHash   = if (Test-Path $SkillPyHashSentinel) {
    (Get-Content $SkillPyHashSentinel -Raw -ErrorAction SilentlyContinue).Trim()
} else { '' }
if ($SkillPyHashValue -ne $storedSkillPyHash) {
    Write-Step "Installing Skill Python dependencies (pytrends, matplotlib, pillow)..."
    & "$PythonDir\python.exe" -m pip install --quiet $SkillPyDeps
    if ($LASTEXITCODE -ne 0) { throw "pip install for skill dependencies failed" }
    Set-Content -Path $SkillPyHashSentinel -Value $SkillPyHashValue -Encoding UTF8 -NoNewline
    Write-Ok "Skill Python dependencies ready."
} else {
    Write-Ok "Skill Python dependencies up-to-date — skipping."
}

# ─── 6. Fernet master key ────────────────────────────────────────────────────
if (Test-Path $MasterKey) {
    Write-Ok "Master key already exists — skipping."
} else {
    Write-Step "Generating Fernet master key..."
    $keygenCode = 'import sys; from cryptography.fernet import Fernet; import pathlib; pathlib.Path(sys.argv[1]).write_bytes(Fernet.generate_key())'
    & "$VenvDir\Scripts\python.exe" -c $keygenCode $MasterKey
    if ($LASTEXITCODE -ne 0) { throw "Failed to generate master key" }

    # Remove inherited ACEs; grant read-only to current user (encryption key is never written again)
    $null = & icacls $MasterKey /inheritance:r /grant:r "${env:USERNAME}:(R)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: could not restrict master key ACLs (icacls exit $LASTEXITCODE)." -ForegroundColor Yellow
    }

    Write-Ok "Master key generated at $MasterKey (user-only read ACL)."
}

Write-Host ''
Write-Host '[SA Runner] Bootstrap complete.' -ForegroundColor Green
