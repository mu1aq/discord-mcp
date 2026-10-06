#Requires -Version 7
$ErrorActionPreference = 'Stop'

$ScriptDir = $PSScriptRoot
$McpName   = 'discord-cache'

Write-Host "=== discord-mcp installer (Windows) ==="
Write-Host ""

# 1. Python version check
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { Write-Host "[X] python not found. Install Python 3.11+."; exit 1 }

$PythonPath = & $py.Source -c "import sys; print(sys.executable)"
$PyVer = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$ok = & $PythonPath -c "import sys; print(int(sys.version_info[:2] >= (3, 11)))"
if ($ok -ne '1') { Write-Host "[X] Python 3.11+ required (found $PyVer)"; exit 1 }
Write-Host "[OK] Python $PyVer"

# 2. Install dependencies
Write-Host ""
Write-Host "Installing dependencies..."
& $PythonPath -m pip install "mcp[cli]" aiohttp --quiet --disable-pip-version-check --no-warn-script-location
Write-Host "[OK] Dependencies installed"

# 3. Locate Discord install + cache
$DiscordExe = Get-ChildItem -Path "$env:LOCALAPPDATA\Discord*\app-*\Discord*.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^Discord(PTB|Canary)?\.exe$' } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($DiscordExe) {
    Write-Host "[OK] Discord found: $($DiscordExe.FullName)"
} else {
    Write-Host "[!] Discord install not found under $env:LOCALAPPDATA"
}

$CacheDir = Get-Item -Path "$env:APPDATA\discord*\Cache\Cache_Data" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($CacheDir) {
    $count = (Get-ChildItem $CacheDir.FullName -File).Count
    Write-Host "[OK] Discord cache found ($count files): $($CacheDir.FullName)"
} else {
    Write-Host "[!] Discord cache not found under $env:APPDATA\discord\Cache\Cache_Data"
    Write-Host "    Install and log into Discord desktop first."
}

# 4. Build server entry + register (Claude Code / Codex / both)
$ServerPy = Join-Path $ScriptDir 'server.py'
$server = @{
    command = $PythonPath
    args    = @($ServerPy)
    timeout = 30000
}
if ($CacheDir) { $server.env = @{ DISCORD_CACHE_DIR = $CacheDir.FullName } }

function Register-Claude {
    $ClaudeJson = Join-Path $env:USERPROFILE '.claude.json'
    if (Test-Path $ClaudeJson) {
        $conf = Get-Content $ClaudeJson -Raw | ConvertFrom-Json -AsHashtable
        if (-not $conf.mcpServers) { $conf.mcpServers = @{} }
        if ($conf.mcpServers.ContainsKey($McpName)) {
            Write-Host "[!] '$McpName' already in ~/.claude.json"
            $ans = if ($env:DISCORD_MCP_FORCE) { 'y' } else { Read-Host "    Overwrite? [y/N]" }
            if ($ans -notmatch '^[Yy]$') { Write-Host "    Skipped Claude registration."; return }
        }
        $conf.mcpServers[$McpName] = $server
        Copy-Item $ClaudeJson "$ClaudeJson.bak" -Force
        $conf | ConvertTo-Json -Depth 20 | Set-Content $ClaudeJson -Encoding utf8
        Write-Host "[OK] Registered in ~/.claude.json (backup: .claude.json.bak)"
    } else {
        @{ mcpServers = @{ $McpName = $server } } | ConvertTo-Json -Depth 20 | Set-Content $ClaudeJson -Encoding utf8
        Write-Host "[OK] Created ~/.claude.json with MCP registration"
    }
}

function Register-Codex {
    $CodexToml = Join-Path $env:USERPROFILE '.codex\config.toml'
    New-Item -ItemType Directory -Path (Split-Path $CodexToml) -Force | Out-Null
    $lines = @(
        "[mcp_servers.$McpName]"
        'command = "' + ($PythonPath -replace '\\', '/') + '"'
        'args = ["' + ($ServerPy -replace '\\', '/') + '"]'
    )
    if ($CacheDir) { $lines += 'env = { DISCORD_CACHE_DIR = "' + ($CacheDir.FullName -replace '\\', '/') + '" }' }
    $block = $lines -join "`n"
    if (Test-Path $CodexToml) {
        $text = Get-Content $CodexToml -Raw
        # strip any existing [mcp_servers.discord-cache] table before re-adding
        $pattern = "(?ms)^\[mcp_servers\.$([regex]::Escape($McpName))\].*?(?=^\[|\z)"
        $text = ([regex]::Replace($text, $pattern, '')).TrimEnd()
        Copy-Item $CodexToml "$CodexToml.bak" -Force
        Set-Content $CodexToml ($text + "`n`n" + $block + "`n") -Encoding utf8
        Write-Host "[OK] Registered in ~/.codex/config.toml (backup: config.toml.bak)"
    } else {
        Set-Content $CodexToml ($block + "`n") -Encoding utf8
        Write-Host "[OK] Created ~/.codex/config.toml with MCP registration"
    }
}

$target = $env:DISCORD_MCP_TARGET
if (-not $target) {
    Write-Host ""
    Write-Host "Register MCP for which client?"
    Write-Host "  [1] Claude Code (default)   [2] Codex   [3] Both"
    $sel = Read-Host "Select [1/2/3]"
    switch ($sel) { '2' { $target = 'codex' } '3' { $target = 'both' } default { $target = 'claude' } }
}
Write-Host ""
if ($target -eq 'claude' -or $target -eq 'both') { Register-Claude }
if ($target -eq 'codex' -or $target -eq 'both') { Register-Codex }

# 5. Token setup hint
Write-Host ""
if (Test-Path (Join-Path $ScriptDir '.env')) {
    Write-Host "[OK] .env found (API mode available)"
} else {
    Write-Host "[i] API mode (optional):"
    Write-Host "    copy .env.example .env"
    Write-Host "    # Edit .env with your Discord token"
    Write-Host "    # Get token: browser discord.com -> F12 -> Network -> Authorization header"
}

Write-Host ""
Write-Host "=== Done ==="
Write-Host "Restart Claude Code to load the MCP server."
