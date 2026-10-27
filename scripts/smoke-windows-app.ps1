# Launch a packaged ApplyQueue app on Windows and wait until the backend sidecar it spawned
# answers GET /health as the frozen (bundled) backend.
# Usage: powershell -File scripts/smoke-windows-app.ps1 -Exe <path\to\ApplyQueue.exe>
param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [int]$Port = 8743,
  [int]$TimeoutSec = 180
)
$ErrorActionPreference = "Stop"
$url = "http://127.0.0.1:$Port/health"

if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { throw "app not found: $Exe" }
$busy = $false
try { Invoke-RestMethod -Uri $url -TimeoutSec 2 | Out-Null; $busy = $true } catch { }
if ($busy) { throw "something already answers on port $Port before launch" }

$app = Start-Process -FilePath $Exe -PassThru
$body = $null
try {
  for ($i = 1; $i -le $TimeoutSec; $i++) {
    try { $body = Invoke-RestMethod -Uri $url -TimeoutSec 2; break } catch { }
    if ($app.HasExited) { throw "app exited early (code $($app.ExitCode))" }
    Start-Sleep -Seconds 1
  }
  if (-not $body) { throw "/health did not answer within $TimeoutSec s" }
  Write-Host "/health after $i s: $($body | ConvertTo-Json -Compress -Depth 4)"
  if ($body.app -ne "applyqueue") { throw "not the ApplyQueue backend" }
  if ($body.frozen -ne $true) { throw "backend is not the bundled (frozen) sidecar" }
  Write-Host "Packaged app launched and its bundled backend answered /health"
}
finally {
  if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
  Get-Process | Where-Object { $_.ProcessName -like "applyqueue-backend*" } | Stop-Process -Force -ErrorAction SilentlyContinue
}
