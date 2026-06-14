$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Output = Join-Path $ScriptDir "lolesports-lck.ics"

python (Join-Path $ScriptDir "fetch-lolesports-calendar.py") `
  --output $Output `
  --leagues "lck,msi,worlds,first_stand" `
  --from-days -7 `
  --to-days 120

Write-Host "Updated $Output"
