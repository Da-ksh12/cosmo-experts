<#
.SYNOPSIS
  Starts the Cosmo Experts backend so the Android app (on a phone on the same Wi-Fi, or in the Android emulator) can reach it. DEVELOPMENT AND DEMO ONLY.

.DESCRIPTION
  * listens on every network interface, port 8000 (change with -Port)
  * lets the Android app's own origin (http://localhost and https://localhost) call the API (CORS); nothing else is opened
  * asks you for the password of the demo accounts (patient1, patient2, doctor1) instead of using the documented default; it applies when the accounts are first created,
    so use -DataDir with a new folder for a clean demo database
  * prints the addresses to type into the app (Settings > Server connection)

  Windows Firewall will ask once whether to allow Python on private networks: allow it. A plain http:// address works from the app only if its host is listed in
  frontend\android.config.local.json ("devCleartextHosts") when the DEBUG app was built; an https:// server always works (docs\ANDROID_APK.md).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File ops\windows\android-dev-backend.ps1 -DataDir F:\cosmo-demo-data
#>
param(
  [int]$Port = 8000,
  [string]$DataDir = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "The backend environment was not found at $python. See README.md (Run)." }

$secure = Read-Host "Choose a password for the demo accounts (patient1, patient2, doctor1)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:COSMO_DEV_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ($env:COSMO_DEV_PASSWORD.Length -lt 8) { throw "Use at least 8 characters." }
$env:COSMO_CORS = "http://localhost,https://localhost"
if ($DataDir) { $env:COSMO_DATA_DIR = $DataDir }
$models = Join-Path $backend "data\models"
if (Test-Path (Join-Path $models "embed\bge-small-en-v1.5")) { $env:COSMO_EMBED_MODEL_DIR = Join-Path $models "embed\bge-small-en-v1.5" }
if (Test-Path (Join-Path $models "stt\faster-whisper-base")) { $env:COSMO_WHISPER_MODEL_DIR = Join-Path $models "stt\faster-whisper-base" }

Write-Host ""
Write-Host "Type one of these into the app (Settings > Server connection):" -ForegroundColor Cyan
Write-Host "  Android emulator : http://10.0.2.2:$Port"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' -and $_.PrefixOrigin -ne "WellKnown" } |
  ForEach-Object { Write-Host ("  Phone on Wi-Fi   : http://{0}:{1}" -f $_.IPAddress, $Port) }
Write-Host "Stop the server with Ctrl+C." -ForegroundColor DarkGray
Write-Host ""
Set-Location $backend
& $python -m uvicorn app.main:app --host 0.0.0.0 --port $Port
