# Magnolia one-command installer (Windows). Fetched via curl/irm and run
# standalone. Mirrors install.sh: prerequisites, Claude present + logged in,
# clone, Python deps, trust seed, magnolia on PATH. Native PowerShell - no WSL.
# Compatible with Windows PowerShell 5.1 (no ?? / ternary / && operators).
#
# Everything runs inside one script block so that, under `irm ... | iex`, an
# early stop uses `return` (leaving the window open with the message visible)
# instead of `exit` (which would close the user's PowerShell window), and our
# $ErrorActionPreference never leaks into the caller's session.
& {
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/jayhjenkins/Magnolia.git"
$Dest = if ($env:MAGNOLIA_DIR) { $env:MAGNOLIA_DIR } else { Join-Path $HOME "Magnolia" }
# Per-user shim dir: python3 (and, only if needed, python) launchers that run the
# real interpreter by absolute path - ahead of the Microsoft Store stub.
$ShimDir = Join-Path $env:LOCALAPPDATA "Magnolia\shims"

function Say($m) { Write-Host "`n$m" }
function Warn($m) { Write-Host "`n$m" -ForegroundColor Yellow }

# A gold ASCII welcome, printed once at the end of a successful install. Native
# PowerShell coloring (works on older terminals without ANSI/VT). Single-quoted
# here-strings keep the art's backticks/backslashes literal. Pure ASCII (invariant #8).
function Banner {
    $art = @'
  __  __                        _ _
 |  \/  | __ _  __ _ _ __   ___ | (_) __ _
 | |\/| |/ _` |/ _` | '_ \ / _ \| | |/ _` |
 | |  | | (_| | (_| | | | | (_) | | | (_| |
 |_|  |_|\__,_|\__, |_| |_|\___/|_|_|\__,_|
               |___/
'@
    Write-Host ""
    foreach ($line in ($art -split "`n")) { Write-Host $line.TrimEnd("`r") -ForegroundColor Yellow }
    $lyr = @'
      "She's got everything delightful
       She's got everything I need
       Takes the wheel when I'm seeing double
       Pays my ticket when I speed"
                                  -- Sugar Magnolia
'@
    foreach ($line in ($lyr -split "`n")) { Write-Host $line.TrimEnd("`r") -ForegroundColor DarkGray }
}

# Re-read PATH from the registry (Machine + User) so tools winget just installed
# (git, node/npm, python, py, pandoc) resolve in THIS session without reopening it.
function Update-SessionPath {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

# The User PATH is read/written RAW (REG_EXPAND_SZ, unexpanded) so entries like
# %USERPROFILE%\... survive; [Environment]::SetEnvironmentVariable would flatten
# them to REG_SZ with every variable expanded.
function Get-UserPathRaw {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $false)
    if (-not $key) { return "" }
    try {
        return [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    } finally { $key.Close() }
}
function Set-UserPathRaw($value) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
    try { $key.SetValue("Path", $value, [Microsoft.Win32.RegistryValueKind]::ExpandString) } finally { $key.Close() }
}
function Test-SameDir($a, $b) {
    $x = [Environment]::ExpandEnvironmentVariables($a).TrimEnd("\")
    $y = [Environment]::ExpandEnvironmentVariables($b).TrimEnd("\")
    return ($x -ieq $y)
}
# Put $dir on the User PATH (idempotent). -Prepend moves it to the FRONT (so the
# python3 shim beats ...\WindowsApps, which lives on the User PATH); otherwise it
# is appended only if absent. Returns $true if the PATH changed.
function Add-UserPath([string]$dir, [switch]$Prepend) {
    $raw = Get-UserPathRaw
    $parts = @()
    if ($raw) { $parts = @($raw -split ";" | Where-Object { $_ }) }
    $others = @($parts | Where-Object { -not (Test-SameDir $_ $dir) })
    if ($Prepend) {
        $new = (@($dir) + $others) -join ";"
    } elseif ($others.Count -ne $parts.Count) {
        $new = $raw   # already present somewhere - leave the order alone
    } else {
        $new = (@($others) + @($dir)) -join ";"
    }
    if ($new -ne $raw) { Set-UserPathRaw $new; return $true }
    return $false
}

function Test-Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

# A usable CPython 3 interpreter: exists, is not the Microsoft Store stub under
# ...\WindowsApps (which opens the Store instead of running Python), and runs.
function Test-RealPython($exe) {
    if (-not $exe) { return $false }
    if ($exe -like "*\WindowsApps\*") { return $false }
    if ($exe -like "$ShimDir\*") { return $false }
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
    try {
        $v = & $exe -c "import sys; print(sys.version_info[0])"
        return ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq "3")
    } catch { return $false }
}

# Resolve the real interpreter's absolute path: the `py -3` launcher first, then
# python/python3 on PATH (skipping the Store stub and our own shims), then the
# standard per-user / all-users install folders. $null if none.
function Resolve-Python {
    if (Test-Have "py") {
        try {
            $p = & py -3 -c "import sys; print(sys.executable)"
            if ($LASTEXITCODE -eq 0 -and $p) {
                $p = "$($p | Select-Object -Last 1)".Trim()
                if (Test-RealPython $p) { return $p }
            }
        } catch {}
    }
    foreach ($n in @("python", "python3")) {
        foreach ($c in @(Get-Command $n -All -CommandType Application -ErrorAction SilentlyContinue)) {
            if ($c.Path -like "*.exe" -and (Test-RealPython $c.Path)) { return $c.Path }
        }
    }
    $roots = @((Join-Path $env:LOCALAPPDATA "Programs\Python"), $env:ProgramFiles)
    foreach ($r in $roots) {
        if (-not $r -or -not (Test-Path -LiteralPath $r)) { continue }
        $hits = @(Get-ChildItem -Path $r -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
                  Sort-Object Name -Descending)
        foreach ($h in $hits) {
            $exe = Join-Path $h.FullName "python.exe"
            if (Test-RealPython $exe) { return $exe }
        }
    }
    return $null
}

# Write a shim pair for command $name in $ShimDir that execs $py by absolute path:
#   <name>.cmd  - for cmd.exe / PowerShell (CRLF)
#   <name>      - extensionless sh script for Git Bash, which Claude Code uses for
#                 Bash tool calls and hooks on Windows (LF, no BOM)
# Rewritten every run, so re-installing (or a moved Python) is idempotent.
function Write-PythonShim([string]$name, [string]$py) {
    $cmdPy = $py
    # Keep the .cmd ASCII-safe when Python is under the profile (usernames may
    # be non-ASCII): reference it through %LOCALAPPDATA%.
    if ($env:LOCALAPPDATA -and $py.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)) {
        $cmdPy = "%LOCALAPPDATA%" + $py.Substring($env:LOCALAPPDATA.Length)
    }
    $cmd = "@echo off`r`nrem Magnolia $name shim (generated by install.ps1) - runs the real Python, not the Store stub.`r`n`"$cmdPy`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText((Join-Path $ShimDir "$name.cmd"), $cmd, [Text.Encoding]::Default)
    $shPy = $py -replace "\\", "/"
    $sh = "#!/bin/sh`n# Magnolia $name shim (generated by install.ps1) - runs the real Python, not the Store stub.`nexec `"$shPy`" `"`$@`"`n"
    [IO.File]::WriteAllText((Join-Path $ShimDir $name), $sh, (New-Object System.Text.UTF8Encoding($false)))
}

# 1. Prerequisites via winget - only what's missing (never upgrade what's there)
if (-not (Test-Have "winget")) {
    Say "winget (App Installer) is required. Install 'App Installer' from the Microsoft Store, then re-run this installer."
    return
}
Say "Checking prerequisites (git, node, python, pandoc)..."
$wg = @("-e", "--accept-source-agreements", "--accept-package-agreements")
if (-not (Test-Have "git"))    { winget install --id Git.Git @wg }
if (-not (Test-Have "node"))   { winget install --id OpenJS.NodeJS @wg }
Update-SessionPath
if (-not (Resolve-Python))     { winget install --id Python.Python.3.12 @wg }
if (-not (Test-Have "pandoc")) { winget install --id JohnMacFarlane.Pandoc @wg }
Update-SessionPath

$Py = Resolve-Python
if (-not $Py) {
    Warn ("Could not find a working Python 3 (only the Microsoft Store 'python' stub, or nothing). " +
          "Install Python 3.12 from https://www.python.org/downloads/windows/ (or: winget install --id Python.Python.3.12 -e), " +
          "then open a NEW PowerShell window and re-run this installer.")
    return
}
Say "Using Python: $Py"

# 2. python3 shim + UTF-8 mode. Skills, workers and hooks call `python3`, which on
# Windows is usually the Store stub or absent. The shim dir goes to the FRONT of
# the User PATH so it beats ...\WindowsApps. `python` is shimmed only if it is
# currently broken (absent / Store stub) - a working one is left alone.
New-Item -ItemType Directory -Force -Path $ShimDir | Out-Null
Write-PythonShim "python3" $Py
$pyOk = $false
foreach ($c in @(Get-Command python -All -CommandType Application -ErrorAction SilentlyContinue)) {
    if ($c.Path -like "$ShimDir\*") { $pyOk = $true; break }   # our shim from a previous run
    if ($c.Path -like "*.exe") { $pyOk = (Test-RealPython $c.Path); break }
}
if (-not $pyOk -or (Test-Path -LiteralPath (Join-Path $ShimDir "python.cmd"))) { Write-PythonShim "python" $Py }
if (Add-UserPath $ShimDir -Prepend) { Say "Put $ShimDir at the front of your PATH (python3 -> $Py)." }
# Windows' default cp1252 console encoding breaks decoding Claude's UTF-8 output.
# Set persistently (this also broadcasts the environment change to new windows).
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
$env:PYTHONUTF8 = "1"
# The qmd MCP server (.mcp.json) starts as "${MAGNOLIA_PYTHON:-python3}". Claude
# Code can't spawn our python3 .cmd shim as an MCP command, so pin the real exe.
[Environment]::SetEnvironmentVariable("MAGNOLIA_PYTHON", $Py, "User")
$env:MAGNOLIA_PYTHON = $Py
Update-SessionPath

if (Test-Have "qmd") {
    Say "qmd already present - skipping."
} elseif (Test-Have "npm") {
    Say "Installing qmd (semantic search)..."
    npm install -g @tobilu/qmd
    if ($LASTEXITCODE -ne 0) { Warn "qmd did not install (npm exit $LASTEXITCODE). Search still works without it; 'magnolia doctor' will show how to retry." }
} else {
    Warn "npm was not found, so qmd (semantic search) was skipped. Open a new PowerShell window and run: npm install -g @tobilu/qmd"
}

# 3. Claude CLI: detect-and-direct (never guess an install command)
if (-not (Test-Have "claude")) {
    Say "Claude Code is required and was not found. Install it from https://claude.com/claude-code, then re-run this installer."
    return
}

# 4. Login only if not already authenticated
$cfg = Join-Path $HOME ".claude.json"
$loggedIn = $false
if (Test-Path $cfg) {
    try { if ((Get-Content $cfg -Raw | ConvertFrom-Json).oauthAccount) { $loggedIn = $true } } catch {}
}
if (-not $loggedIn) { Say "Sign in to Claude (a browser will open)..."; claude auth login }

# 5. Clone (or fast-forward)
if (-not (Test-Path (Join-Path $Dest ".git"))) {
    Say "Cloning Magnolia into $Dest ..."
    git clone $RepoUrl $Dest
    if ($LASTEXITCODE -ne 0) { Warn "git clone failed (exit $LASTEXITCODE). Check your network / GitHub access and re-run."; return }
} else {
    Say "Updating existing Magnolia in $Dest ..."
    try { git -C $Dest pull --ff-only } catch { }
}

# 6. Python dependencies (the board server's runtime deps live in requirements.txt)
Say "Installing Python dependencies..."
& $Py -m pip install -r (Join-Path $Dest "requirements.txt") pytest
if ($LASTEXITCODE -ne 0) {
    Warn ("Python dependencies did not install (pip exit $LASTEXITCODE). The board will not start without them. " +
          "Fix the error above, then run:  `"$Py`" -m pip install -r `"$(Join-Path $Dest 'requirements.txt')`"")
    return
}
# Prove the board server imports cleanly now, not at first launch.
Push-Location $Dest
try {
    & $Py -c "import sys; sys.path.insert(0, 'scripts'); import task_server"
    $importRc = $LASTEXITCODE
} finally { Pop-Location }
if ($importRc -ne 0) {
    Warn "The board server failed to import (see the error above). Run 'magnolia doctor' after fixing it."
}

# 7. Seed folder trust + qmd enablement (safe no-op if not logged in)
& $Py (Join-Path $Dest "scripts\trust_seed.py") seed $Dest
if ($LASTEXITCODE -ne 0) {
    Warn "Folder-trust seeding did not complete (exit $LASTEXITCODE). Magnolia still works; Claude may ask you to trust the folder on first use."
}

# 8. Put magnolia on PATH (add repo bin so bin\magnolia.cmd resolves in place)
$bin = Join-Path $Dest "bin"
if (Add-UserPath $bin) { Say "Added $bin to your PATH." }
Update-SessionPath

# 9. Done
Banner
Say "Magnolia is installed. Open a NEW terminal window (so PATH and PYTHONUTF8 apply), type:  magnolia   then press Enter."
}
