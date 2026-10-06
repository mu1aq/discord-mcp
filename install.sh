#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MCP_NAME="discord-cache"

echo "=== discord-mcp installer ==="
echo ""

# 1. Python version check
if ! command -v python3 &>/dev/null; then
    echo "❌ python3 not found. Install Python 3.11+."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "❌ Python 3.11+ required (found $PY_VER)"
    exit 1
fi
echo "✅ Python $PY_VER"

# 2. Install dependencies
echo ""
echo "Installing dependencies..."
pip install "mcp[cli]" aiohttp --quiet 2>/dev/null || pip3 install "mcp[cli]" aiohttp --quiet
echo "✅ Dependencies installed"

# 3. Discord cache check
CACHE_DIR="$HOME/Library/Application Support/discord/Cache/Cache_Data"
if [ -d "$CACHE_DIR" ]; then
    COUNT=$(ls "$CACHE_DIR" | wc -l | tr -d ' ')
    echo "✅ Discord cache found ($COUNT files)"
else
    echo "⚠️  Discord cache not found at:"
    echo "   $CACHE_DIR"
    echo "   Install and log into Discord desktop first."
fi

# 4. Register MCP (Claude Code / Codex / both)
PYTHON_PATH=$(which python3)
CLAUDE_JSON="$HOME/.claude.json"
CODEX_TOML="$HOME/.codex/config.toml"

register_claude() {
    if [ -f "$CLAUDE_JSON" ]; then
        if python3 -c "import json; d=json.load(open('$CLAUDE_JSON')); exit(0 if '$MCP_NAME' in d.get('mcpServers',{}) else 1)" 2>/dev/null; then
            echo "⚠️  '$MCP_NAME' already in ~/.claude.json"
            read -rp "   Overwrite? [y/N] " ans
            if [[ ! "$ans" =~ ^[Yy]$ ]]; then echo "   Skipped Claude registration."; return; fi
        fi
        python3 -c "
import json
with open('$CLAUDE_JSON') as f:
    d = json.load(f)
d.setdefault('mcpServers', {})['$MCP_NAME'] = {
    'command': '$PYTHON_PATH',
    'args': ['$SCRIPT_DIR/server.py'],
    'timeout': 30000
}
with open('$CLAUDE_JSON', 'w') as f:
    json.dump(d, f, indent=2)
"
        echo "✅ Registered in ~/.claude.json"
    else
        python3 -c "
import json
d = {'mcpServers': {'$MCP_NAME': {
    'command': '$PYTHON_PATH',
    'args': ['$SCRIPT_DIR/server.py'],
    'timeout': 30000
}}}
with open('$CLAUDE_JSON', 'w') as f:
    json.dump(d, f, indent=2)
"
        echo "✅ Created ~/.claude.json with MCP registration"
    fi
}

register_codex() {
    mkdir -p "$(dirname "$CODEX_TOML")"
    BLOCK="[mcp_servers.$MCP_NAME]
command = \"$PYTHON_PATH\"
args = [\"$SCRIPT_DIR/server.py\"]"
    if [ -f "$CODEX_TOML" ]; then
        cp "$CODEX_TOML" "$CODEX_TOML.bak"
        # strip any existing [mcp_servers.discord-cache] table before re-adding
        awk -v name="[mcp_servers.$MCP_NAME]" '
            $0==name {skip=1; next}
            skip && /^\[/ {skip=0}
            !skip {print}
        ' "$CODEX_TOML.bak" > "$CODEX_TOML"
        { printf '\n%s\n' "$BLOCK"; } >> "$CODEX_TOML"
        echo "✅ Registered in ~/.codex/config.toml (backup: config.toml.bak)"
    else
        printf '%s\n' "$BLOCK" > "$CODEX_TOML"
        echo "✅ Created ~/.codex/config.toml with MCP registration"
    fi
}

TARGET="${DISCORD_MCP_TARGET:-}"
if [ -z "$TARGET" ]; then
    echo ""
    echo "Register MCP for which client?"
    echo "  [1] Claude Code (default)   [2] Codex   [3] Both"
    read -rp "Select [1/2/3] " sel
    case "$sel" in 2) TARGET=codex;; 3) TARGET=both;; *) TARGET=claude;; esac
fi
echo ""
if [ "$TARGET" = claude ] || [ "$TARGET" = both ]; then register_claude; fi
if [ "$TARGET" = codex ]  || [ "$TARGET" = both ]; then register_codex; fi

# 5. Token setup hint
echo ""
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "✅ .env found (API mode available)"
else
    echo "ℹ️  API mode (optional):"
    echo "   cp .env.example .env"
    echo "   # Edit .env with your Discord token"
    echo "   # Get token: browser discord.com → F12 → Network → Authorization header"
fi

echo ""
echo "=== Done ==="
echo "Restart Claude Code to load the MCP server."
