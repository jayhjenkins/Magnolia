# Magnolia one-command installer (Windows). Fetched via curl/irm and run
# standalone. Mirrors install.sh: prerequisites, Claude present + logged in,
# clone, trust seed, magnolia on PATH. Native PowerShell - no WSL.
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/jayhjenkins/Magnolia.git"
$Dest = if ($env:MAGNOLIA_DIR) { $env:MAGNOLIA_DIR } else { Join-Path $HOME "Magnolia" }

function Say($m) { Write-Host "`n$m" }

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

# 1. Prerequisites via winget
Say "Installing prerequisites (git, node, python, pandoc)..."
winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
winget install --id OpenJS.NodeJS -e
winget install --id Python.Python.3.12 -e
winget install --id JohnMacFarlane.Pandoc -e
Say "Installing qmd (semantic search)..."
npm install -g @tobilu/qmd
Say "Installing Python dependencies..."
python -m pip install ruamel.yaml pytest

# 2. Claude CLI: detect-and-direct
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Say "Claude Code is required and was not found. Install it from https://claude.com/claude-code, then re-run this installer."
    exit 1
}

# 3. Login only if not already authenticated
$cfg = Join-Path $HOME ".claude.json"
$loggedIn = $false
if (Test-Path $cfg) {
    try { if ((Get-Content $cfg -Raw | ConvertFrom-Json).oauthAccount) { $loggedIn = $true } } catch {}
}
if (-not $loggedIn) { Say "Sign in to Claude (a browser will open)..."; claude login }

# 4. Clone (or fast-forward)
if (-not (Test-Path (Join-Path $Dest ".git"))) {
    Say "Cloning Magnolia into $Dest ..."
    git clone $RepoUrl $Dest
} else {
    Say "Updating existing Magnolia in $Dest ..."
    try { git -C $Dest pull --ff-only } catch { }
}

# 5. Seed folder trust + qmd enablement (Inc 1; safe no-op if not logged in)
try { python (Join-Path $Dest "scripts/trust_seed.py") seed $Dest } catch { }

# 6. Put magnolia on PATH (add repo bin so bin\magnolia.cmd resolves in place)
$bin = Join-Path $Dest "bin"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$bin", "User")
    Say "Added $bin to your PATH (open a new terminal to pick it up)."
}

# 7. Done
Banner
Say "Magnolia is installed. Type:  magnolia   then press Enter."
