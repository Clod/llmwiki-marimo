# LLM Wiki installer for Windows. The explanation, step by step, is in INSTALLER.md.
#
# Run it with a double click on install.cmd. Alternative, from PowerShell opened in the
# LLM Wiki folder:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# Options:
#   -Wikis <folder>   the folder for your wikis (skips the question)
#   -Yes              accept every default and do not start the web interface at the end
#
# Steps: 1 system data, 2 uv, 3 dependencies, 4 check, 5 wikis folder, 6 shortcut.
# On a failure the installer stops and leaves all its output in install.log.

param(
    [string]$Wikis = "",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:NO_COLOR = "1"
$env:PYTHONUTF8 = "1"

$Log = Join-Path $PSScriptRoot "install.log"
$Utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Log, "", $Utf8)
$script:StepNumber = 0
$script:StepName = ""
$Total = 6

# Write a line to the console and to install.log.
function Say([string]$Line) {
    Write-Host $Line
    [System.IO.File]::AppendAllText($Log, $Line + [Environment]::NewLine, $Utf8)
}

# Announce a step and remember it for the failure message.
function Step([int]$Number, [string]$Name) {
    $script:StepNumber = $Number
    $script:StepName = $Name
    Say ""
    Say "== Step ${Number} of ${Total}: $Name =="
}

# Stop the installer with the failure message of the current step.
function Fail([string]$Detail) {
    Say ""
    Say "ERROR: $Detail"
    Say "Step $($script:StepNumber) ($($script:StepName)) failed. The full output is in: $Log"
    exit 1
}

# Run a program, copy its output to the console and to install.log, and stop the
# installer if the program ends with a code other than 0.
function Run([string]$Program, [string[]]$Arguments) {
    Say "> $Program $($Arguments -join ' ')"
    $ErrorActionPreference = "Continue"
    & $Program @Arguments 2>&1 | ForEach-Object { Say "$_" }
    $Code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($Code -ne 0) {
        Fail "the command ended with code $Code."
    }
}

# -- Step 1 ---------------------------------------------------------------------------
Step 1 "system data"
try {
    $System = Get-CimInstance Win32_OperatingSystem
    Say "System: $($System.Caption) $($System.Version)"
} catch {
    Say "System: $([Environment]::OSVersion.VersionString)"
}
Say "PowerShell: $($PSVersionTable.PSVersion)"
Say "LLM Wiki folder: $PSScriptRoot"
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "pyproject.toml"))) {
    Fail "pyproject.toml was not found. Run install.cmd from the folder where you extracted LLM Wiki."
}

# -- Step 2 ---------------------------------------------------------------------------
Step 2 "uv"
$Candidates = @(
    (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
    (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
)
$Uv = $null
$UvOnPath = Get-Command uv -ErrorAction SilentlyContinue
if ($UvOnPath) {
    $Uv = $UvOnPath.Source
} else {
    $Uv = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($Uv) {
    Say "uv is already installed: $Uv"
} else {
    Say "uv is not installed. Installing it with the official installer of Astral."
    Run "powershell" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "irm https://astral.sh/uv/install.ps1 | iex")
    $Uv = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Uv) {
        Fail "the uv installer finished, but uv.exe is not in $($Candidates -join ' nor in ')."
    }
}
Run $Uv @("--version")

# -- Step 3 ---------------------------------------------------------------------------
Step 3 "dependencies"
Say "uv downloads Python 3.12 and the libraries of LLM Wiki. It can take several minutes."
Run $Uv @("sync", "--group", "web", "--no-progress")

# -- Step 4 ---------------------------------------------------------------------------
Step 4 "check"
Run $Uv @("run", "--group", "web", "--no-progress", "python", "scripts\install_steps.py", "check")

# -- Step 5 ---------------------------------------------------------------------------
Step 5 "wikis folder"
if (-not $Wikis) {
    $Default = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "LLM Wiki"
    if ($Yes) {
        $Wikis = $Default
    } else {
        Say "Your wikis (documents, pages and index) live in a folder of their own, apart from"
        Say "the LLM Wiki folder, so that updating LLM Wiki never touches them."
        $Wikis = Read-Host "Where should your wikis live? [$Default]"
        if (-not $Wikis) { $Wikis = $Default }
    }
}
Say "Wikis folder: $Wikis"
Run $Uv @("run", "--group", "web", "--no-progress", "python", "scripts\install_steps.py", "wikis", "--folder", $Wikis)

# -- Step 6 ---------------------------------------------------------------------------
Step 6 "shortcut"
Run $Uv @("run", "--group", "web", "--no-progress", "python", "scripts\install_steps.py", "finish",
    "--system", "windows", "--uv", $Uv, "--folder", $Wikis)

if (-not $Yes) {
    Say ""
    $Start = Read-Host "Start the web interface now? [Y/n]"
    if ($Start -notmatch "^[nN]") {
        & (Join-Path $PSScriptRoot "open_web.cmd")
    }
}
exit 0
