<#
.SYNOPSIS
  Install Minty on Windows and start the guided setup.

.DESCRIPTION
  From a checkout of this repository:        .\install.ps1        (or double-click install.cmd)
  From the web, no checkout needed:          irm <raw url of this file> | iex

  It makes a private virtual environment (.venv) next to the code, installs Minty into it, puts a `minty` command in
  %LOCALAPPDATA%\Minty\bin (added to YOUR PATH only, never the system's) and runs `minty onboard`.
  It never needs administrator rights. It downloads only the Python packages Minty needs (and the code itself when you did not
  start it from a checkout). Anything else (Python itself, ffmpeg) is offered, one thing at a time, after asking.

  -NoOnboard   install only            -Relink     replace a different `minty` command
  -Uninstall   remove the command, the PATH entry and the start-at-login task (your settings and notes are kept)
  -Yes         answer yes to "install Python with winget?"      -Python <path>   use this Python
  -Repo <url>  where to get the code (or set MINTY_REPO)        -Branch <name>   which branch (or set MINTY_BRANCH)
  Other settings: MINTY_HOME (where the code and the command go), MINTY_BIN (where the command goes).

  This file is plain ASCII on purpose: Windows PowerShell 5.1 reads a script without a byte-order mark in the old code page.
#>
[CmdletBinding()]
param(
  [switch]$NoOnboard,
  [switch]$Relink,
  [switch]$Uninstall,
  [switch]$Yes,
  [string]$Python = "",
  [string]$Repo = $(if ($env:MINTY_REPO) { $env:MINTY_REPO } else { "https://github.com/hrmnisthetuffest/minty.git" }),
  [string]$Branch = $(if ($env:MINTY_BRANCH) { $env:MINTY_BRANCH } else { "main" })
)

# Errors are checked by hand (a native program writing to stderr must not abort the script on Windows PowerShell 5.1).
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads many times slower on 5.1
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

$script:HomeDir = if ($env:MINTY_HOME) { $env:MINTY_HOME } else { Join-Path $env:LOCALAPPDATA "Minty" }
$script:BinDir  = if ($env:MINTY_BIN)  { $env:MINTY_BIN }  else { Join-Path $script:HomeDir "bin" }
$script:Interactive = [Environment]::UserInteractive -and -not [Console]::IsInputRedirected

function Say([string]$Message)  { Write-Host ("> " + $Message) -ForegroundColor DarkYellow }
function Warn([string]$Message) { Write-Host ("! " + $Message) -ForegroundColor Yellow }
function Stop-Install([string]$Message) { throw $Message }

function Test-PythonOk([string]$Exe, [string[]]$Before) {
  # Returns the full path of a Python 3.10 or newer, or "". The Microsoft Store stub (python.exe in WindowsApps) answers 9009 and prints
  # an advertisement instead: that fails the version check like any other missing Python.
  try {
    $out = & $Exe @Before -c "import sys; sys.stdout.write(sys.executable); sys.exit(0 if sys.version_info >= (3, 10) else 3)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -First 1).Trim() }
  } catch { }
  return ""
}

function Find-Python {
  if ($Python) {
    $found = Test-PythonOk $Python @()
    if (-not $found) { Stop-Install "The Python you gave ($Python) is not Python 3.10 or newer." }
    return $found
  }
  $candidates = @(
    @{ Exe = "py"; Before = @("-3.13") }, @{ Exe = "py"; Before = @("-3.12") }, @{ Exe = "py"; Before = @("-3.11") }, @{ Exe = "py"; Before = @("-3.10") },
    @{ Exe = "python"; Before = @() }, @{ Exe = "python3"; Before = @() }
  )
  foreach ($c in $candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    $found = Test-PythonOk $c.Exe $c.Before
    if ($found) { return $found }
  }
  # Installed by winget for this user, but this window's PATH was not refreshed yet
  foreach ($v in "313", "312", "311", "310") {
    $guess = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
    if (Test-Path $guess) { $found = Test-PythonOk $guess @(); if ($found) { return $found } }
  }
  return ""
}

function Ensure-Python {
  $found = Find-Python
  if ($found) { return $found }
  $hint = "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') or run: winget install -e --id Python.Python.3.12"
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Stop-Install $hint }
  $answer = if ($Yes) { "y" } elseif ($script:Interactive) { Read-Host "Python 3.10 or newer was not found. Install Python 3.12 for you now with winget? [y/N]" } else { "n" }
  if ($answer -notmatch "^[yY]") { Stop-Install $hint }
  Say "Installing Python 3.12 with winget (for your user only)"
  & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  $found = Find-Python
  if (-not $found) { Stop-Install "Python was installed but this window cannot find it yet. Close this window, open a new one and run the installer again." }
  return $found
}

function Get-Root {
  # Where the code is: this checkout when the script was run from one, else a copy downloaded to the home folder.
  $here = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
  if ($here -and (Test-Path (Join-Path $here "pyproject.toml")) -and (Test-Path (Join-Path $here "src\minty_core"))) { return $here }
  $root = Join-Path $script:HomeDir "src"
  New-Item -ItemType Directory -Force -Path $script:HomeDir -ErrorAction Stop | Out-Null
  if (Get-Command git -ErrorAction SilentlyContinue) {
    if (Test-Path (Join-Path $root ".git")) {
      Say "Updating $root"
      & git -C $root pull --ff-only
      if ($LASTEXITCODE -ne 0) { Stop-Install "Could not update $root (git pull failed)." }
    } else {
      Say "Downloading Minty into $root"
      & git clone --depth 1 --branch $Branch $Repo $root
      if ($LASTEXITCODE -ne 0) { Stop-Install "Could not download Minty (git clone failed). Check the address: $Repo" }
    }
    return $root
  }
  # No git: the repository as a zip file (no history, so updating means running this again)
  $base = $Repo -replace "\.git$", ""
  $zipUrl = "$base/archive/refs/heads/$Branch.zip"
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ("minty-" + [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $tmp -ErrorAction Stop | Out-Null
  try {
    Say "Downloading Minty ($zipUrl)"
    Invoke-WebRequest -Uri $zipUrl -OutFile (Join-Path $tmp "minty.zip") -UseBasicParsing -ErrorAction Stop
    Expand-Archive -Path (Join-Path $tmp "minty.zip") -DestinationPath (Join-Path $tmp "x") -Force -ErrorAction Stop
    $inner = Get-ChildItem (Join-Path $tmp "x") -Directory | Select-Object -First 1
    if (-not $inner) { Stop-Install "The download was empty." }
    New-Item -ItemType Directory -Force -Path $root -ErrorAction Stop | Out-Null
    & robocopy $inner.FullName $root /E /XD .venv /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Stop-Install "Could not copy the files into $root." }
  } finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  }
  return $root
}

function Get-UserPathEntries { ([Environment]::GetEnvironmentVariable("Path", "User") -split ";") | Where-Object { $_ } }

function Add-ToUserPath([string]$Dir) {
  $entries = @(Get-UserPathEntries)
  $has = $entries | Where-Object { $_.TrimEnd("\") -ieq $Dir.TrimEnd("\") }
  if (-not $has) {
    # Set through .NET, not setx: setx cuts the value at 1024 characters and would damage a long PATH.
    [Environment]::SetEnvironmentVariable("Path", (($entries + $Dir) -join ";"), "User")
    Say "Added $Dir to your PATH (open a new terminal for it to take effect)"
  }
  if (($env:Path -split ";") -notcontains $Dir) { $env:Path = $env:Path + ";" + $Dir }
}

function Remove-FromUserPath([string]$Dir) {
  $entries = @(Get-UserPathEntries)
  $kept = $entries | Where-Object { $_.TrimEnd("\") -ine $Dir.TrimEnd("\") }
  if (@($kept).Count -ne $entries.Count) {
    [Environment]::SetEnvironmentVariable("Path", ($kept -join ";"), "User")
    Say "Removed $Dir from your PATH"
  }
}

function Get-ShimText([string]$Root) {
  # A one-line command that calls the launcher in the checkout, so updating the checkout updates the command.
  $under = $Root.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)
  $target = if ($under) { "%LOCALAPPDATA%" + $Root.Substring($env:LOCALAPPDATA.Length) } else { $Root }
  return "@echo off`r`n`"$target\bin\minty.cmd`" %*`r`nexit /b %ERRORLEVEL%`r`n"
}

function Write-Shim([string]$Path, [string]$Root) {
  # Written in the console's own code page (a .cmd file is read in it), so a user name with an accent survives.
  $enc = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
  [IO.File]::WriteAllText($Path, (Get-ShimText $Root), $enc)
}

function Test-CppRuntime {
  # onnxruntime (the wake word and the voices) needs the Visual C++ runtime; a fresh Windows may not have it. A warning, not an error.
  $key = "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
  try { if ((Get-ItemProperty $key -ErrorAction Stop).Installed -eq 1) { return } } catch { }
  Warn "The Microsoft Visual C++ runtime was not found. Voice needs it. If voice fails to start, run: winget install Microsoft.VCRedist.2015+.x64"
}

function Uninstall-Minty {
  $shim = Join-Path $script:BinDir "minty.cmd"
  if (Test-Path $shim) { Remove-Item -Force $shim; Say "Removed $shim" }
  Remove-FromUserPath $script:BinDir
  & schtasks /Query /TN Minty *> $null
  if ($LASTEXITCODE -eq 0) { & schtasks /Delete /TN Minty /F *> $null; Say "Removed the start-at-login task" }
  $cfg = if ($env:MINTY_CONFIG_DIR) { $env:MINTY_CONFIG_DIR } else { Join-Path $env:LOCALAPPDATA "Minty" }
  Say "Done. Your settings, keys and notes are still in $cfg, and the code in $(Join-Path $script:HomeDir 'src'). Delete those folders yourself if you want them gone."
}

function Install-Minty {
  Write-Host "Minty installer" -ForegroundColor Red
  if ($Uninstall) { Uninstall-Minty; return }
  if ($PSVersionTable.PSVersion.Major -lt 5) { Stop-Install "Windows PowerShell 5.1 or newer is needed (Windows 10 and 11 have it)." }

  # 1. Python
  $py = Ensure-Python
  $version = (& $py --version 2>&1) -join " "
  Say "Using $version ($py)"

  # 2. The code
  $root = Get-Root
  if ($root.Length -gt 120) { Warn "The install folder's path is long ($($root.Length) characters); Windows limits paths to 260. If the install fails, run it again from a shorter folder such as C:\Minty." }

  # 3. A private environment
  $venvPy = Join-Path $root ".venv\Scripts\python.exe"
  if (-not (Test-Path $venvPy)) {
    Say "Creating the virtual environment"
    & $py -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Stop-Install "Could not create a virtual environment with $py." }
  }
  $env:PYTHONUTF8 = "1"
  Say "Installing Minty (this takes a minute or two)"
  & $venvPy -m pip install --quiet --disable-pip-version-check --upgrade pip
  & $venvPy -m pip install --quiet --disable-pip-version-check -e $root
  if ($LASTEXITCODE -ne 0) { Stop-Install "Installing Minty's Python packages failed (the lines above say why). Check your internet connection and run the installer again." }
  $startError = & $venvPy -c "import minty_core.app" 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { Stop-Install ("Minty was installed but does not start. Run scripts\windows_check.ps1 and send me what it prints. What Python said:" + [Environment]::NewLine + $startError.Trim()) }
  Test-CppRuntime

  # 4. The `minty` command
  New-Item -ItemType Directory -Force -Path $script:BinDir -ErrorAction Stop | Out-Null
  $shim = Join-Path $script:BinDir "minty.cmd"
  $wanted = $true
  if (Test-Path $shim) {
    $enc = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
    $current = [IO.File]::ReadAllText($shim, $enc)
    if ($current.Trim() -eq (Get-ShimText $root).Trim()) {
      Say "$shim already points here"
      $wanted = $false
    } elseif (-not $Relink) {
      Say "A ``minty`` command already exists at $shim and points somewhere else, so I left it alone. Run this one with $root\bin\minty.cmd, or run install.ps1 -Relink to replace it."
      $wanted = $false
    }
  }
  if ($wanted) { Write-Shim $shim $root; Say "Wrote $shim" }
  Add-ToUserPath $script:BinDir

  # 5. The guided setup
  if (-not $NoOnboard) {
    Say "Starting the guided setup"
    & (Join-Path $root "bin\minty.cmd") onboard
    return
  }
  Say "Done. Open a new terminal and run:  minty onboard"
}

try {
  Install-Minty
} catch {
  Write-Host ("x " + $_.Exception.Message) -ForegroundColor Red
  # `exit` would close the window when this was started with `irm | iex`; only a script run from a file sets an exit code.
  if ($PSCommandPath) { exit 1 }
}
