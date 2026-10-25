<#
.SYNOPSIS
  One-command setup of a self-hosted GitHub Actions runner for ApplyQueue on Windows (x64).

.DESCRIPTION
  Run from an ELEVATED PowerShell (Run as administrator). Idempotent: safe to rerun.
  One-liner (fetches this script from the private repo with the signed-in gh; see
  docs/applyqueue/CI_RUNNERS.md):

    winget install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements; $env:Path=[Environment]::GetEnvironmentVariable('Path','Machine')+';'+[Environment]::GetEnvironmentVariable('Path','User'); gh auth status 2>$null; if ($LASTEXITCODE) { gh auth login -h github.com -p https -w }; cmd /c "gh api -H ""Accept: application/vnd.github.raw"" ""repos/JonathanLiu1401/ApplyQueue/contents/scripts/runners/setup-windows-runner.ps1?ref=main"" > %TEMP%\aq-runner-setup.ps1"; powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\aq-runner-setup.ps1

  Steps:
    1. Prerequisites via winget (machine scope): Git, GitHub CLI, Node LTS, Python 3.11, uv,
       Visual Studio 2022 Build Tools (C++ workload, needed by Rust MSVC), WebView2 runtime.
       Rust (stable-x86_64-pc-windows-msvc) via rustup into C:\rust so the runner service account
       can use it. msedgedriver is fetched per run by the workflow (matches the WebView2 version).
    2. gh sign-in if needed; checks you are an admin of the repo.
    3. Latest official actions/runner (win-x64), SHA-256 verified, in C:\actions-runner-applyqueue.
    4. Registers it to JonathanLiu1401/ApplyQueue ONLY with labels self-hosted,windows,x64,applyqueue
       and installs it as a Windows service (account: NT AUTHORITY\NETWORK SERVICE, unprivileged).
  The registration token is fetched with gh (or passed with -Token / -TokenStdin), handed to
  config.cmd through the environment only, and never written to disk or printed.

.PARAMETER Token      Registration token (avoid: it lands in PowerShell history; prefer gh or -TokenStdin).
.PARAMETER TokenStdin Read the registration token from stdin.
.PARAMETER Name       Runner name (default applyqueue-win-<COMPUTERNAME>).
.PARAMETER SkipPrereqs Only (re)install the runner.
.PARAMETER Uninstall  Stop + remove the service and unregister the runner.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '', Justification = 'Interactive setup script: coloured console output is the point')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = 'Private helpers of a one-shot setup script')]
[CmdletBinding()]
param(
  [string]$Token = '',
  [switch]$TokenStdin,
  [string]$Name = '',
  [switch]$SkipPrereqs,
  [switch]$Uninstall
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = 'JonathanLiu1401/ApplyQueue'
$RunnerDir = 'C:\actions-runner-applyqueue'
$RustRoot = 'C:\rust'
$Labels = 'self-hosted,windows,x64,applyqueue'
if (-not $Name) { $Name = "applyqueue-win-$($env:COMPUTERNAME.ToLower())" }

function Write-Step([string]$Msg) { Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Stop-WithError([string]$Msg) { Write-Host "ERROR: $Msg" -ForegroundColor Red; exit 1 }
function Update-SessionPath {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Install-WingetPackage([string]$Id, [string[]]$Extra = @()) {
  $null = winget list -e --id $Id --accept-source-agreements 2>$null
  if ($LASTEXITCODE -eq 0) { Write-Host "ok: $Id"; return }
  Write-Host "installing $Id"
  winget install -e --id $Id --scope machine --silent --accept-source-agreements --accept-package-agreements @Extra
  if ($LASTEXITCODE -ne 0) {
    # some packages do not offer a machine-scope installer
    winget install -e --id $Id --silent --accept-source-agreements --accept-package-agreements @Extra
    if ($LASTEXITCODE -ne 0) { Stop-WithError "winget could not install $Id" }
  }
}
function Get-RunnerId {
  $id = gh api "repos/$Repo/actions/runners?per_page=100" --jq ".runners[] | select(.name==`"$Name`") | .id" 2>$null
  if ($LASTEXITCODE -ne 0) { return '' }
  return "$id".Trim()
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Stop-WithError 'run this from an elevated PowerShell (right-click PowerShell > Run as administrator)'
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { Stop-WithError "x64 Windows required (found $env:PROCESSOR_ARCHITECTURE)" }
Write-Host "Runner name: $Name, labels: $Labels"

# --- uninstall -------------------------------------------------------------------------------------
if ($Uninstall) {
  Write-Step "Removing runner $Name"
  if (Test-Path "$RunnerDir\.runner") {
    $env:ACTIONS_RUNNER_INPUT_TOKEN = gh api -X POST "repos/$Repo/actions/runners/remove-token" --jq .token
    Push-Location $RunnerDir; & .\config.cmd remove; Pop-Location
    Remove-Item Env:\ACTIONS_RUNNER_INPUT_TOKEN -ErrorAction SilentlyContinue
  }
  $id = Get-RunnerId
  if ($id) { gh api -X DELETE "repos/$Repo/actions/runners/$id" | Out-Null; Write-Host "deleted runner $id on GitHub" }
  Write-Host "Runner removed. Delete $RunnerDir (and $RustRoot if unused) to free disk space."
  exit 0
}

# --- prerequisites ---------------------------------------------------------------------------------
if (-not $SkipPrereqs) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Stop-WithError 'winget is missing: install "App Installer" from the Microsoft Store, then rerun'
  }
  Write-Step 'Prerequisites (winget)'
  Install-WingetPackage 'Git.Git'
  Install-WingetPackage 'GitHub.cli'
  Install-WingetPackage 'OpenJS.NodeJS.LTS'
  Install-WingetPackage 'Python.Python.3.11'
  Install-WingetPackage 'astral-sh.uv'
  Install-WingetPackage 'Microsoft.EdgeWebView2Runtime'
  Install-WingetPackage 'Microsoft.VisualStudio.2022.BuildTools' @('--override', '--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended')
  Update-SessionPath

  Write-Step "Rust (rustup, machine-wide in $RustRoot)"
  [Environment]::SetEnvironmentVariable('RUSTUP_HOME', "$RustRoot\rustup", 'Machine')
  [Environment]::SetEnvironmentVariable('CARGO_HOME', "$RustRoot\cargo", 'Machine')
  $env:RUSTUP_HOME = "$RustRoot\rustup"; $env:CARGO_HOME = "$RustRoot\cargo"
  $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  if ($machinePath -notlike "*$RustRoot\cargo\bin*") {
    [Environment]::SetEnvironmentVariable('Path', "$machinePath;$RustRoot\cargo\bin", 'Machine')
  }
  Update-SessionPath
  if (-not (Test-Path "$RustRoot\cargo\bin\rustup.exe")) {
    $init = Join-Path $env:TEMP 'rustup-init.exe'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe' -OutFile $init
    & $init -y --profile minimal --default-toolchain stable-x86_64-pc-windows-msvc --no-modify-path
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'rustup-init failed' }
    Remove-Item $init -Force
  } else {
    & "$RustRoot\cargo\bin\rustup.exe" update stable --no-self-update | Out-Null
  }
  # The service account (NETWORK SERVICE) needs to write cargo caches (cargo install tauri-driver).
  icacls $RustRoot /grant '*S-1-5-20:(OI)(CI)M' /T /Q | Out-Null
  Write-Host "ok: $(& "$RustRoot\cargo\bin\rustc.exe" --version)"
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Update-SessionPath }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Stop-WithError 'gh is not installed (rerun without -SkipPrereqs)' }

# --- GitHub sign-in --------------------------------------------------------------------------------
Write-Step 'GitHub sign-in'
gh auth status -h github.com 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host "A browser window opens: sign in as the owner of $Repo and enter the code shown here."
  gh auth login -h github.com -p https -w
}
$isAdmin = gh api "repos/$Repo" --jq .permissions.admin 2>$null
if ("$isAdmin".Trim() -ne 'true') { Stop-WithError "the signed-in GitHub account is not an admin of $Repo" }
Write-Host "ok: signed in as $(gh api user --jq .login)"

# --- runner download -------------------------------------------------------------------------------
Write-Step 'GitHub Actions runner'
New-Item -ItemType Directory -Force -Path $RunnerDir | Out-Null
Set-Location $RunnerDir
if (-not (Test-Path '.\bin\Runner.Listener.exe')) {
  $ver = (gh api repos/actions/runner/releases/latest --jq .tag_name).TrimStart('v')
  $body = gh api repos/actions/runner/releases/latest --jq .body | Out-String
  $m = [regex]::Match($body, 'BEGIN SHA win-x64 -->([0-9a-f]{64})<')
  if (-not $m.Success) { Stop-WithError 'could not read the runner checksum from the release notes' }
  $zip = "actions-runner-win-x64-$ver.zip"
  Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/actions/runner/releases/download/v$ver/$zip" -OutFile $zip
  if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $m.Groups[1].Value) { Remove-Item $zip; Stop-WithError 'runner checksum mismatch' }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $RunnerDir $zip), $RunnerDir)
  Remove-Item $zip
  Write-Host "installed runner $ver"
} else {
  Write-Host 'runner already installed (it self-updates)'
}

# --- registration + service ------------------------------------------------------------------------
$registered = Get-RunnerId
if ((Test-Path '.\.runner') -and $registered) {
  Write-Host "already registered as $Name (id $registered)"
  $svc = Get-Service -Name 'actions.runner.*' -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*$Name*" }
  if ($svc -and $svc.Status -ne 'Running') { Start-Service $svc.Name }
} else {
  if (Test-Path '.\.runner') {
    Get-Service -Name 'actions.runner.*' -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*$Name*" } | ForEach-Object {
      Stop-Service $_.Name -ErrorAction SilentlyContinue; sc.exe delete $_.Name | Out-Null
    }
    Remove-Item -Force -ErrorAction SilentlyContinue .runner, .credentials, .credentials_rsaparams, .service
  }
  if ($TokenStdin) { $Token = [Console]::In.ReadLine() }
  if (-not $Token) { $Token = gh api -X POST "repos/$Repo/actions/runners/registration-token" --jq .token }
  if (-not $Token) { Stop-WithError 'no registration token' }
  $env:ACTIONS_RUNNER_INPUT_TOKEN = "$Token".Trim(); $Token = $null
  try {
    & .\config.cmd --unattended --url "https://github.com/$Repo" --name $Name --labels $Labels `
      --no-default-labels --work _work --replace --runasservice
    if ($LASTEXITCODE -ne 0) { Stop-WithError 'config.cmd failed' }
  } finally {
    Remove-Item Env:\ACTIONS_RUNNER_INPUT_TOKEN -ErrorAction SilentlyContinue
  }
}

Write-Step 'Checking the runner is online'
$st = ''
foreach ($i in 1..12) {
  $st = gh api "repos/$Repo/actions/runners?per_page=100" --jq ".runners[] | select(.name==`"$Name`") | .status" 2>$null
  if ("$st".Trim() -eq 'online') { break }
  Start-Sleep -Seconds 5
}
Write-Host "runner ${Name}: $st"
if ("$st".Trim() -ne 'online') { Stop-WithError "runner not online yet; check $RunnerDir\_diag and Get-Service actions.runner.*" }
Write-Host "`nDone. $Name is online for $Repo with labels $Labels." -ForegroundColor Green
Write-Host 'Keep this PC on, plugged in and connected (Settings > System > Power: Sleep = Never when plugged in).'
