"""Discord REST API client — mimics real Chrome browser fingerprint."""

import asyncio
import base64
import json
import os
import random
import time
import uuid
from pathlib import Path

import aiohttp

TOKEN_PATH = Path(__file__).parent / ".env"

_IS_WIN = os.name == "nt"
_OS_NAME = "Windows" if _IS_WIN else "Mac OS X"
_OS_VERSION = "10" if _IS_WIN else "10.15.7"
_UA_PLATFORM = "Windows NT 10.0; Win64; x64" if _IS_WIN else "Macintosh; Intel Mac OS X 10_15_7"
_SEC_CH_PLATFORM = '"Windows"' if _IS_WIN else '"macOS"'

_USER_AGENT = (
    f"Mozilla/5.0 ({_UA_PLATFORM}) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
_BUILD_NUMBER = 589596

_MIN_DELAY = 2.0
_MAX_DELAY = 5.0
_last_request_time = 0.0


def _make_super_properties() -> str:
    return base64.b64encode(json.dumps({
        "os": _OS_NAME,
        "browser": "Chrome",
        "device": "",
        "system_locale": "ko-KR",
        "has_client_mods": False,
        "browser_user_agent": _USER_AGENT,
        "browser_version": "151.0.0.0",
        "os_version": _OS_VERSION,
        "referrer": "",
        "referring_domain": "",
        "referrer_current": "https://discord.com/",
        "referring_domain_current": "discord.com",
        "release_channel": "stable",
        "client_build_number": _BUILD_NUMBER,
        "client_event_source": None,
        "client_launch_id": str(uuid.uuid4()),
        "launch_signature": str(uuid.uuid4()),
        "client_heartbeat_session_id": str(uuid.uuid4()),
        "client_app_state": "focused",
    }, separators=(",", ":")).encode()).decode()


_SUPER_PROPERTIES = _make_super_properties()


def _load_token() -> str | None:
    if TOKEN_PATH.exists():
        for line in TOKEN_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISCORD_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return os.environ.get("DISCORD_TOKEN")


async def _throttle():
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    delay = random.uniform(_MIN_DELAY, _MAX_DELAY)
    if elapsed < delay:
        await asyncio.sleep(delay - elapsed)
    _last_request_time = time.monotonic()


def _headers(token: str) -> dict:
    return {
        "Authorization": token,
        "User-Agent": _USER_AGENT,
        "X-Super-Properties": _SUPER_PROPERTIES,
        "X-Discord-Locale": "ko-KR",
        "X-Discord-Timezone": "Asia/Seoul",
        "X-Debug-Options": "bugReporterEnabled",
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": _SEC_CH_PLATFORM,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://discord.com/channels/@me",
    }


async def _request(method: str, path: str, token: str, params: dict | None = None) -> dict | list | None:
    await _throttle()
    url = f"https://discord.com/api/v9{path}"
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, headers=_headers(token), params=params) as resp:
            if resp.status == 429:
                retry_after = (await resp.json()).get("retry_after", 5)
                await asyncio.sleep(retry_after + random.uniform(1, 3))
                return await _request(method, path, token, params)
            if resp.status == 401:
                return {"error": "Invalid token. Update .env file."}
            if resp.status == 403:
                return {"error": "Forbidden — no access to this resource."}
            if resp.status != 200:
                return {"error": f"HTTP {resp.status}"}
            return await resp.json()


async def api_list_guilds() -> list[dict] | None:
    token = _load_token()
    if not token:
        return None
    data = await _request("GET", "/users/@me/guilds", token, {"limit": "200"})
    if isinstance(data, list):
        return [{"id": g["id"], "name": g["name"], "icon": g.get("icon")} for g in data]
    return None


async def api_list_channels(guild_id: str) -> list[dict] | None:
    token = _load_token()
    if not token:
        return None
    data = await _request("GET", f"/guilds/{guild_id}/channels", token)
    if isinstance(data, list):
        return [
            {
                "id": c["id"], "name": c.get("name"), "type": c.get("type"),
                "topic": c.get("topic"), "parent_id": c.get("parent_id"),
            }
            for c in data if c.get("id")
        ]
    return None


async def api_read_messages(channel_id: str, limit: int = 50, before: str | None = None) -> list[dict] | None:
    token = _load_token()
    if not token:
        return None
    params = {"limit": str(min(limit, 100))}
    if before:
        params["before"] = before
    data = await _request("GET", f"/channels/{channel_id}/messages", token, params)
    if isinstance(data, list):
        return data
    return None


async def api_search_messages(guild_id: str, query: str, limit: int = 25) -> list[dict] | None:
    token = _load_token()
    if not token:
        return None
    params = {"content": query, "limit": str(min(limit, 25))}
    data = await _request("GET", f"/guilds/{guild_id}/messages/search", token, params)
    if isinstance(data, dict) and "messages" in data:
        return [msg[0] for msg in data["messages"] if msg]
    return None


async def api_list_threads(channel_id: str) -> list[dict] | None:
    """List archived threads in a forum/text channel."""
    token = _load_token()
    if not token:
        return None
    results = []
    # Active threads come from guild, but we need the guild_id
    # Archived threads come from the channel endpoint
    for archive_type in ("public", "private"):
        data = await _request("GET", f"/channels/{channel_id}/threads/archived/{archive_type}", token)
        if isinstance(data, dict) and "threads" in data:
            results.extend(data["threads"])
    return results or None


async def api_list_active_threads(guild_id: str) -> list[dict] | None:
    """List all active threads in a guild."""
    token = _load_token()
    if not token:
        return None
    data = await _request("GET", f"/guilds/{guild_id}/threads/active", token)
    if isinstance(data, dict) and "threads" in data:
        return data["threads"]
    return None


async def api_get_user_info() -> dict | None:
    token = _load_token()
    if not token:
        return None
    return await _request("GET", "/users/@me", token)


def has_token() -> bool:
    return _load_token() is not None
