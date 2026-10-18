# Registers the daily encrypted backup and the 5-minute health check as Windows scheduled tasks. Run once, elevated. NOT executed by the build.
# Usage: .\register-tasks.ps1 -Backend F:\Cosmo\backend -Dest D:\cosmo-backups -KeyFile C:\ProgramData\cosmo\backup_key.txt
param([Parameter(Mandatory)][string]$Backend, [Parameter(Mandatory)][string]$Dest, [Parameter(Mandatory)][string]$KeyFile, [string]$WebhookFile = "")
$py = Join-Path $Backend ".venv\Scripts\python.exe"
# The key file must be readable only by the service account (icacls); the task reads it at run time so the key is never stored in the task definition.
$bk = "`$env:COSMO_BACKUP_KEY=(Get-Content -Raw '$KeyFile').Trim(); & '$py' -m scripts.backup backup-encrypted '$Dest'"
Register-ScheduledTask -TaskName "Cosmo Backup" -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -Command `"$bk`"" -WorkingDirectory $Backend) `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 2:15am) -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)) -Force
$hc = "& '$py' -m scripts.healthcheck --url http://127.0.0.1:8000"
if ($WebhookFile) { $hc += " --webhook (Get-Content -Raw '$WebhookFile').Trim()" }
Register-ScheduledTask -TaskName "Cosmo Healthcheck" -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -Command `"$hc`"" -WorkingDirectory $Backend) `
  -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)) -Force
