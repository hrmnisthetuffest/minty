<#
.SYNOPSIS
  Check that Minty works on this Windows PC, and keep a copy of the result to send back if it does not.

.DESCRIPTION
  Run it in PowerShell from the Minty folder:   .\scripts\windows_check.ps1
  or from anywhere after installing:            & "$env:LOCALAPPDATA\Minty\src\scripts\windows_check.ps1"
  Add -Quick to skip the check that really starts Minty (it takes under a minute).

  It uses a scratch settings folder and free ports. The only thing it does in your real settings folder is create and delete a tiny
  test file to prove the folder can be written to: your settings, keys and notes are not read or changed.
  The whole output is also saved to minty-check.txt in your temp folder; send that file if anything says FAIL.

  This file is plain ASCII on purpose (Windows PowerShell 5.1 reads a script without a byte-order mark in the old code page).
#>
[CmdletBinding()]
param([switch]$Quick)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path ([IO.Path]::GetTempPath()) "minty-check.txt"

function Find-VenvPython {
  $venv = Join-Path $root ".venv\Scripts\python.exe"
  if (Test-Path $venv) { return $venv }
  foreach ($c in "py", "python", "python3") {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
      $out = & $c -c "import sys; sys.stdout.write(sys.executable); sys.exit(0 if sys.version_info >= (3, 10) else 3)" 2>$null
      if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -First 1).Trim() }
    }
  }
  return ""
}

$header = @(
  "Minty on Windows: install check",
  ("Windows:     " + [Environment]::OSVersion.VersionString + " (" + $(if ([Environment]::Is64BitOperatingSystem) { "64-bit" } else { "32-bit" }) + ")"),
  ("PowerShell:  " + $PSVersionTable.PSVersion),
  ("Folder:      " + $root),
  ""
)
$py = Find-VenvPython
$body = @()
if (-not $py) {
  $body += "FAIL  No Python 3.10 or newer was found, and there is no Minty environment at $root\.venv."
  $body += "      Run install.ps1 first (double-click install.cmd)."
  $code = 1
} else {
  $env:PYTHONUTF8 = "1"
  $arguments = @((Join-Path $PSScriptRoot "install_check.py"))
  if ($Quick) { $arguments += "--quick" }
  $body = & $py @arguments 2>&1 | ForEach-Object { "$_" }
  $code = $LASTEXITCODE
}
$all = $header + $body
$all | ForEach-Object { Write-Host $_ }
try { [IO.File]::WriteAllLines($log, [string[]]$all, (New-Object Text.UTF8Encoding($false))); Write-Host ""; Write-Host "A copy was saved to $log" } catch { }
if ($code -ne 0) { exit 1 }
