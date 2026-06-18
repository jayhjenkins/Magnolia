# Magnolia one-command installer (Windows). Fetched via curl/irm and run
# standalone. Mirrors install.sh: prerequisites, Claude present + logged in,
# clone, trust seed, magnolia on PATH. Native PowerShell - no WSL.
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/jayhjenkins/Magnolia.git"
$Dest = if ($env:MAGNOLIA_DIR) { $env:MAGNOLIA_DIR } else { Join-Path $HOME "Magnolia" }

function Say($m) { Write-Host "`n$m" }

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
    git -C $Dest pull --ff-only
}

# 5. Seed folder trust + qmd enablement (Inc 1)
python (Join-Path $Dest "scripts/trust_seed.py") seed $Dest

# 6. Put magnolia on PATH (add repo bin so bin\magnolia.cmd resolves in place)
$bin = Join-Path $Dest "bin"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$bin", "User")
    Say "Added $bin to your PATH (open a new terminal to pick it up)."
}

# 7. Done
Say "Magnolia is installed. Type:  magnolia   then press Enter."
