# discord-mcp

An MCP server that lets Claude read your Discord conversation history.

It keeps its own message store in the project folder (`./cache`), seeded for free
from the Discord desktop cache and refreshed on demand. Reads hit this store, so
it keeps working even when the Discord client stops flushing its own cache, and
in store/seed mode it makes **zero** network requests — no account-detection risk.
The Discord REST API is an optional, opt-in refresh source.

**Windows / macOS supported** (the on-disk cache format differs per platform and
is handled automatically).

> 🇰🇷 한국어 README: [README.ko.md](README.ko.md)

---

## ⚠️ Disclaimer — read before enabling API mode

This tool has two independent modes:

- **Cache mode (default, safe).** Reads files Discord already wrote to your local
  disk. No API calls, no token, nothing sent to Discord. Only reads channels you
  have already opened in the Discord app.
- **API mode (optional, opt-in, your risk).** If you set a `DISCORD_TOKEN`, the
  server calls the Discord REST API with your **personal user token**. This is a
  *self-bot*, which **violates the Discord Terms of Service** and the Developer
  Policy, and **can get your account suspended or terminated**. API mode is off
  until you create a `.env` with a token yourself.

Use this software only on your own account and your own data, and only where
local law allows. You are solely responsible for how you use it. The authors
provide it "as is" with no warranty (see [LICENSE](LICENSE)). If you do not want
the ToS risk, do not configure a token — cache mode works on its own.

---

## Why

- Discord gives you no built-in way to search or summarize your own history from
  an assistant. The official bot API requires inviting a bot per server and can't
  reach DMs or read-only servers.
- So this reads the cache the Discord desktop app already stores locally.
  **Zero API calls in cache mode → no account detection, no rate limits.** Any
  channel you've opened in the app is readable with no network request.
- Only when you explicitly need older messages or threads that aren't cached does
  the optional token-based REST fallback come into play.
- Result: Claude can answer "what was said in this channel yesterday?" purely by
  reading local files.

## Tools

| Tool | Description |
|---|---|
| `list_guilds` | List servers you're in |
| `list_channels` | List channels per server (categories & forums, with cached message counts) |
| `read_messages` | Read channel messages, with author/date filters and `before`/`after` cursors |
| `get_message` | Fetch one message by ID, with its Discord link and attachment info |
| `search_messages` | Search by text, server, channel, author, or date |
| `list_attachments` | List a cached message's attachments (size, type, URL) |
| `download_attachment` | Save a cached Discord CDN file into `downloads/` |
| `export_messages` | Export a cached channel/thread to JSON or Markdown in `exports/` |
| `list_threads` | List forum/threads (API token required) |
| `read_thread` | Read messages inside a thread |
| `sync_cache` | Snapshot the Discord desktop cache into the local store (no API calls) |
| `cache_status` | Per-channel freshness: local store vs. the Discord desktop cache |
| `cache_stats` | Store statistics + API availability |

### Query & file tools

- `author` in `read_messages` / `search_messages` matches an author ID, username,
  or display name exactly. `since` / `until` accept `YYYY-MM-DD` or ISO 8601 and
  are inclusive on both ends; a bare date is interpreted in UTC.
- `search_messages` works with an empty `query` — filter by channel, author, or
  date alone. Results and single-message lookups include the Discord message link
  and attachment metadata.
- Pass the `attachment_id` from `list_attachments(channel_id, message_id)` to
  `download_attachment`. The download tool only accepts Discord CDN URLs that a
  cached message points to, and caps each file at 100 MiB. Expired or uncached
  URLs return an error.
- `export_messages(channel_id, format="json")` or `format="markdown"` saves a
  cached conversation; `author` / `since` / `until` filters apply. Exports are
  written oldest-message-first.
- Downloads and exports create local files. The local store `cache/`, plus
  `downloads/`, `exports/`, and the original backup `backups/`, are git-ignored.

File downloads and exports operate on **messages present in the cache**. Message
links use the server link when the guild ID can be found in cache, otherwise a DM
style link — so some links may not open.

## Local store & freshness

Reads come from a persistent store in `./cache` (git-ignored), not from the
Discord desktop cache directly. This means the server keeps answering even when
the Discord client stops flushing its own cache to disk.

- **First read** seeds the store for free from the Discord desktop cache.
- **`sync_cache`** re-snapshots the desktop cache into the store (no API calls) —
  run it after opening a channel in the app to pull whatever the client flushed.
- **`cache_status`** shows, per channel, how far the store reaches vs. how far the
  desktop cache has flushed, so you know whether a `sync_cache` would add anything.
- **`read_messages(..., source="api")`** fetches live from the REST API and merges
  the result into the store (token required; see the disclaimer).

### Pagination (`read_messages`)

Messages return newest-first. Each response includes `has_more`, `newest_id`, and
`oldest_id`. Use message-ID cursors to walk beyond the first page:

- `before=<message_id>` — only messages **older** than it. Page back through
  history by passing the previous response's `oldest_id`.
- `after=<message_id>` — only messages **newer** than it. Read incrementally by
  passing the last ID you saw (e.g. the previous `newest_id`).

Both bounds are exclusive and compared by numeric message ID.

## Install

```sh
git clone https://github.com/mu1aq/discord-mcp.git
cd discord-mcp

# or install from release
```

**Windows** (PowerShell 7+):
```powershell
./install.ps1
```

**macOS / Linux**:
```bash
./install.sh
```

What the installer does:
1. Check Python 3.11+
2. Install dependencies (`mcp[cli]`, `aiohttp`)
3. Auto-detect the Discord install / cache location
4. **Pick a target** — Claude Code / Codex / both
   - Claude Code → `~/.claude.json`
   - Codex → `~/.codex/config.toml`
   - injects the cache path as `DISCORD_CACHE_DIR`

Multiple clients share the same server (identical code). To run non-interactively,
set `DISCORD_MCP_TARGET=claude|codex|both`.

## API mode (optional)

To read messages that aren't cached, set a Discord token. **See the disclaimer
above first** — this is a self-bot and violates Discord's ToS.

```bash
cp .env.example .env
# open .env and set DISCORD_TOKEN
```

**Getting the token:**
1. Log in to the Discord desktop app or [discord.com](https://discord.com)
2. `Ctrl+Shift+I` (macOS: `Cmd+Option+I`) → Network tab
3. Click any channel → click a `/api/v9/` request → Headers → copy the
   `Authorization` value

## How it works

```
Discord client → desktop cache (Chromium Cache)
                     ↓ seed / sync_cache (zero API calls, undetectable)
                local store (./cache)  ← primary read source
                     ↓
                MCP Server (localhost)
                     ↑ source="api" (refresh, token required, rate-limited)
                REST API
                     ↓
                Claude Code / Claude Desktop
```

- **Store (default)**: reads come from `./cache`. It is seeded from the Discord
  desktop cache (channels you've opened in the app) and keeps working after the
  client stops flushing.
  - Windows: blockfile format (payload brute-scan), macOS: simple cache format
- **API mode**: refreshes the store from the Discord API for uncached or newer
  messages (self-bot, ToS-violation risk).

## Requirements

- Windows or macOS
- Python 3.11+
- Discord desktop client installed and logged in

## Manual install

```bash
pip install "mcp[cli]" aiohttp
```

Add to `mcpServers` in `~/.claude.json` (`command` is your python path):

```json
{
  "discord-cache": {
    "command": "python",
    "args": ["/path/to/discord-mcp/server.py"],
    "timeout": 30000,
    "env": { "DISCORD_CACHE_DIR": "<Discord Cache_Data path>" }
  }
}
```

Codex — add to `~/.codex/config.toml`:

```toml
[mcp_servers.discord-cache]
command = "python"
args = ["/path/to/discord-mcp/server.py"]
env = { DISCORD_CACHE_DIR = "<Discord Cache_Data path>" }
```

Default cache path — Windows: `%APPDATA%\discord\Cache\Cache_Data`,
macOS: `~/Library/Application Support/discord/Cache/Cache_Data`.
Use `DISCORD_CACHE_DIR` for other locations.

## Self-check

```bash
python test_cache.py
python test_read_features.py
```

## License

[MIT](LICENSE)
