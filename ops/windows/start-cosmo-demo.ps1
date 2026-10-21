<#
.SYNOPSIS
  Runs the real Cosmo Experts backend on THIS computer and gives it a temporary, secure (https) address, so the Android app on any phone can sign in and use every feature:
  reports, photos, history, care plan, quotation and the AI conversation. FOR DEMOS AND TESTING ONLY.

.DESCRIPTION
  What it does
    1. starts the backend on 127.0.0.1 (nothing on your Wi-Fi or network can reach it directly);
    2. starts Cloudflare's tunnel program (cloudflared), which gives it a random address such as https://four-random-words.trycloudflare.com that works from anywhere;
    3. checks that address from the internet side, prints it and copies it to the clipboard;
    4. keeps both running until you press Ctrl+C or close this window. Then the address stops working.

  In the app: on the first screen tap "Set the server address" (later: Settings > Server connection), paste the address, tap Save, then sign in as patient1, patient2 or doctor1
  with the demo password.

  Safety
    * a NEW random session-signing secret is created on every start and kept in memory only (the built-in development secret is never used on the internet);
    * the demo accounts get the password you choose the first time (at least 10 characters, never the documented development default); the demo administrator account is switched off;
    * only the Android app's own origin (https://localhost) may call the API from a browser (CORS);
    * the data lives in its own folder (default: cosmo-demo-data next to the repository folder), separate from your development database. It is synthetic demo data: do not use real patient data;
    * a stray Supabase or production setting in your shell is ignored: this never talks to a cloud database.

  Limits
    * this computer must stay on, awake and online, and the address changes every time you start this (Cloudflare quick tunnels have no uptime promise);
    * the consent screen is not enforced in this local mode (its legal text needs the clinic's approval first, docs\PRIVACY_AND_CONSENT.md);
    * for anything beyond a demo, deploy the backend to a server with its own https address: docs\ANDROID_APK.md, section 5b.

.PARAMETER Port
  Local port of the backend (default 8010; 8000 stays free for your development server).
.PARAMETER DataDir
  Folder for the demo database and uploads. Default: cosmo-demo-data next to the repository folder. To start over, stop this script, delete that folder and run it again.
.PARAMETER Cloudflared
  Path of cloudflared.exe. Default: the copy in <folder next to the repository>\tools\cloudflared, downloaded once (pinned version, SHA-256 checked) if missing.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\windows\start-cosmo-demo.ps1
#>
param(
  [int]$Port = 8010,
  [string]$DataDir = "",
  [string]$Cloudflared = ""
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# The tunnel program is pinned: the release published by Cloudflare and its SHA-256 (also verified to carry Cloudflare's code signature when it was first fetched).
$CF_VERSION = "2026.10.2"
$CF_URL = "https://github.com/cloudflare/cloudflared/releases/download/$CF_VERSION/cloudflared-windows-amd64.exe"
$CF_SHA256 = "12c7c887f8aaab779aef1dfa9b60673530a5adb88ecc55dd6df8f56e0b70f199"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$outside = Split-Path $root -Parent
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "The backend environment was not found at $python. See README.md (Run)." }
if (-not $DataDir) { $DataDir = Join-Path $outside "cosmo-demo-data" }
$DataDir = [IO.Path]::GetFullPath($DataDir)
if ($DataDir.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "Keep the demo data outside the repository ($root): it must never be committed." }

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { throw "Port $Port is already in use. Stop whatever uses it, or pass -Port <another number>." }

# ---- cloudflared (pinned, checksum verified)
$cf = $Cloudflared
if (-not $cf -and $env:COSMO_CLOUDFLARED) { $cf = $env:COSMO_CLOUDFLARED }
if ($cf) {
  if (-not (Test-Path $cf)) { throw "cloudflared was not found at $cf" }
} else {
  $toolsDir = Join-Path $outside "tools\cloudflared"
  $cf = Join-Path $toolsDir "cloudflared.exe"
  if (-not (Test-Path $cf)) {
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq "ARM64") { throw "Automatic download covers 64-bit Intel/AMD Windows only. Install cloudflared yourself and pass -Cloudflared <path>." }
    New-Item -ItemType Directory -Force $toolsDir | Out-Null
    $tmp = "$cf.download"
    Write-Host "Downloading Cloudflare's tunnel program $CF_VERSION (about 53 MB, one time) ..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $CF_URL -OutFile $tmp -UseBasicParsing
    $got = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $CF_SHA256) { Remove-Item $tmp -Force; throw "The download does not match the published checksum ($got). Nothing was installed." }
    Move-Item $tmp $cf
  }
  if ((Get-FileHash $cf -Algorithm SHA256).Hash.ToLower() -ne $CF_SHA256) { throw "$cf is not the pinned cloudflared $CF_VERSION (checksum differs). Delete it and run this again to download the verified copy." }
}

# ---- the demo database and its password (asked only when the database is created)
New-Item -ItemType Directory -Force $DataDir | Out-Null
$logDir = Join-Path $DataDir "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$dbFile = Join-Path $DataDir "cosmo.sqlite3"
if (-not (Test-Path $dbFile)) {
  $pw = $env:COSMO_DEV_PASSWORD
  if (-not $pw) {
    $secure = Read-Host "Choose the demo password for patient1, patient2 and doctor1 (at least 10 characters)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
  }
  $pw = $pw.TrimEnd("`r", "`n")                                             # a password passed in through the environment may carry a stray line ending
  if ($pw.Length -lt 10) { throw "Use at least 10 characters." }
  if ($pw -eq "cosmo-dev") { throw "That is the documented development default: anyone could guess it. Choose another." }
  $env:COSMO_DEV_PASSWORD = $pw
  Write-Host "Creating a new demo database in $DataDir" -ForegroundColor Cyan
} else {
  Remove-Item Env:COSMO_DEV_PASSWORD -ErrorAction SilentlyContinue         # existing accounts keep the password chosen when the database was created
  Write-Host "Using the existing demo database in $DataDir (accounts keep their password)." -ForegroundColor Cyan
}

# ---- the backend's environment: local demo mode, no cloud, nothing inherited from the shell that could change that
foreach ($n in "COSMO_MODE", "COSMO_AUTH", "COSMO_DB", "COSMO_DATABASE_URL", "COSMO_STORAGE", "COSMO_RATE_LIMIT", "COSMO_REQUIRE_CONSENT", "COSMO_SECRET_FILE",
               "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET") { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes); $rng.Dispose()
$env:COSMO_SECRET = [Convert]::ToBase64String($bytes)                      # new on every start, never written anywhere: signs the sign-in tokens
$env:COSMO_DATA_DIR = $DataDir
$env:COSMO_CORS = "https://localhost"                                      # exactly the Android app's own origin
$models = Join-Path $backend "data\models"
if (Test-Path (Join-Path $models "embed\bge-small-en-v1.5")) { $env:COSMO_EMBED_MODEL_DIR = Join-Path $models "embed\bge-small-en-v1.5" }
if (Test-Path (Join-Path $models "stt\faster-whisper-base")) { $env:COSMO_WHISPER_MODEL_DIR = Join-Path $models "stt\faster-whisper-base" }

$be = $null
$tun = $null
try {
  Write-Host "Starting the backend on 127.0.0.1:$Port ..." -ForegroundColor Cyan
  $beOut = Join-Path $logDir "backend.out.log"; $beErr = Join-Path $logDir "backend.err.log"
  $be = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1") `
    -WorkingDirectory $backend -NoNewWindow -PassThru -RedirectStandardOutput $beOut -RedirectStandardError $beErr
  $local = "http://127.0.0.1:$Port/api/v1/health/live"
  $up = $false
  for ($i = 0; $i -lt 180 -and -not $up; $i++) {
    if ($be.HasExited) { throw "The backend stopped while starting. Last lines of $beErr :`n$((Get-Content $beErr -Tail 15 -ErrorAction SilentlyContinue) -join "`n")" }
    try { $up = (Invoke-WebRequest -Uri $local -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { Start-Sleep -Seconds 1 }
  }
  if (-not $up) { throw "The backend did not answer within 3 minutes. See $beErr" }

  # the demo administrator account is not needed by the app and must not be reachable from the internet
  & $python -c "import sqlite3, sys; c = sqlite3.connect(sys.argv[1], timeout=30); c.execute('UPDATE users SET disabled=1 WHERE id=?', ('admin1',)); c.commit(); c.close()" $dbFile
  if ($LASTEXITCODE -ne 0) { throw "Could not switch off the demo administrator account; not opening the address." }

  Write-Host "Opening the secure address ..." -ForegroundColor Cyan
  $cfOut = Join-Path $logDir "cloudflared.out.log"; $cfErr = Join-Path $logDir "cloudflared.err.log"
  Remove-Item $cfOut, $cfErr -ErrorAction SilentlyContinue
  $tun = Start-Process -FilePath $cf -ArgumentList @("tunnel", "--no-autoupdate", "--protocol", "http2", "--url", "http://127.0.0.1:$Port") `
    -NoNewWindow -PassThru -RedirectStandardOutput $cfOut -RedirectStandardError $cfErr
  $url = $null
  for ($i = 0; $i -lt 90 -and -not $url; $i++) {
    if ($tun.HasExited) { throw "The tunnel program stopped. Last lines of $cfErr :`n$((Get-Content $cfErr -Tail 15 -ErrorAction SilentlyContinue) -join "`n")" }
    $m = $null
    try { $m = [regex]::Match((Get-Content $cfErr -Raw -ErrorAction Stop), "https://[a-z0-9-]+\.trycloudflare\.com") } catch { }
    if ($m -and $m.Success) { $url = $m.Value } else { Start-Sleep -Seconds 1 }
  }
  if (-not $url) { throw "No address was issued within 90 seconds. Is the internet reachable? See $cfErr" }

  # The connector must be registered with Cloudflare before the new name can work.
  $registered = $false
  for ($i = 0; $i -lt 60 -and -not $registered; $i++) {
    if ($tun.HasExited) { throw "The tunnel program stopped. See $cfErr" }
    try { $registered = (Get-Content $cfErr -Raw -ErrorAction Stop) -match "Registered tunnel connection" } catch { }
    if (-not $registered) { Start-Sleep -Seconds 1 }
  }
  if (-not $registered) { throw "The tunnel did not register with Cloudflare within 60 seconds. See $cfErr" }

  # Check the address from the internet side WITHOUT this computer's DNS cache: Windows remembers a failed lookup of a brand-new name for up to 15 minutes, which would make a perfectly good
  # address look dead from here (phones are not affected). The name is resolved through Cloudflare's DNS-over-HTTPS and the request is sent to that address.
  $hostName = ([Uri]$url).Host
  $curl = Join-Path $env:SystemRoot "System32\curl.exe"
  $public = $false
  for ($i = 0; $i -lt 45 -and -not $public; $i++) {
    if ($tun.HasExited) { throw "The tunnel program stopped. See $cfErr" }
    try {
      $dns = Invoke-RestMethod -Uri "https://cloudflare-dns.com/dns-query?name=$hostName&type=A" -Headers @{ accept = "application/dns-json" } -TimeoutSec 8
      $ip = @($dns.Answer | Where-Object { $_.type -eq 1 } | ForEach-Object { $_.data }) | Select-Object -First 1
      if ($ip -and (Test-Path $curl)) {
        $body = & $curl -s --max-time 12 --resolve "${hostName}:443:$ip" "$url/api/v1/health/live"
        $public = ($LASTEXITCODE -eq 0 -and "$body" -match "alive")
      } elseif ($ip) {
        $r = Invoke-WebRequest -Uri "$url/api/v1/health/live" -UseBasicParsing -TimeoutSec 10; $public = ($r.StatusCode -eq 200 -and $r.Content -match "alive")
      }
    } catch { }
    if (-not $public) { Start-Sleep -Seconds 2 }
  }
  if (-not $public) { throw "The address $url was issued and registered but did not answer from the internet within 90 seconds. See $cfErr" }

  try { Set-Clipboard -Value $url } catch { }
  Write-Host ""
  Write-Host "READY. The real Cosmo Experts backend is reachable at:" -ForegroundColor Green
  Write-Host ""
  Write-Host "    $url" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "  In the app: Set the server address (first screen) or Settings > Server connection, paste it, Save."
  Write-Host "  Sign in as patient1, patient2 or doctor1 with the demo password you chose. (The address is also on this computer's clipboard.)"
  Write-Host "  Keep this window open and this computer on. Press Ctrl+C to stop: the address then stops working and is not reused."
  Write-Host ""
  while (-not $be.HasExited -and -not $tun.HasExited) { Start-Sleep -Seconds 2 }
  Write-Warning "The backend or the tunnel stopped (see the logs in $logDir)."
}
finally {
  foreach ($p in @($tun, $be)) { if ($p -and -not $p.HasExited) { try { Stop-Process -Id $p.Id -Force } catch { } } }
  Remove-Item Env:COSMO_SECRET, Env:COSMO_DEV_PASSWORD -ErrorAction SilentlyContinue
  Write-Host "Stopped. The temporary address no longer works." -ForegroundColor Cyan
}
