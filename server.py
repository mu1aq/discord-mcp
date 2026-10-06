"""Discord MCP Server — cache-first with REST API fallback."""

import asyncio
import json

try:
    from mcp.server.fastmcp import FastMCP  # mcp SDK 1.x
except ImportError:
    from mcp.server import MCPServer as FastMCP  # mcp SDK 2.x

from discord_api import (
    api_list_active_threads,
    api_list_channels,
    api_list_guilds,
    api_list_threads,
    api_read_messages,
    api_search_messages,
    has_token,
)
from discord_cache import (
    get_cached_channel_guild_map,
    get_cached_channels,
    get_cached_guild_info,
    get_cached_guilds,
    get_cached_messages,
)
from discord_read import (
    attachment_details,
    download_cached_attachment,
    export_cached_messages,
    filter_messages,
    find_message,
    format_message,
)

mcp = FastMCP("discord-cache")


def _format_msg(msg: dict, channel_guild_map: dict[str, str] | None = None) -> dict:
    return format_message(msg, channel_guild_map)


@mcp.tool()
async def list_guilds() -> str:
    """List Discord servers. Reads from local cache first, falls back to API if token is configured."""
    guilds = get_cached_guilds()

    if has_token():
        api_guilds = await api_list_guilds()
        if api_guilds:
            cached_ids = {g.get("id") for g in guilds}
            for g in api_guilds:
                if g["id"] not in cached_ids:
                    guilds.append(g)
            # Update names for cached guilds that only had IDs
            api_names = {g["id"]: g["name"] for g in api_guilds}
            for g in guilds:
                gid = g.get("id")
                name = g.get("name") or ""
                if (not name or name.startswith("(guild")) and gid in api_names:
                    g["name"] = api_names[gid]

    seen = {str(g.get("id")) for g in guilds}
    for guild in _guilds_from_messages():
        if str(guild.get("id")) not in seen:
            guilds.append(guild)
            seen.add(str(guild.get("id")))

    result = []
    for g in guilds:
        result.append({
            "id": g.get("id"),
            "name": g.get("name") or f"(guild {g.get('id')})",
            "icon": bool(g.get("icon")),
        })
    result.sort(key=lambda guild: str(guild["name"]).casefold())
    return json.dumps(result, ensure_ascii=False, indent=2)


def _guilds_from_messages() -> list[dict]:
    channels = get_cached_messages()
    guild_ids = set()
    for ch_id, msgs in channels.items():
        for msg in msgs:
            gid = msg.get("guild_id")
            if gid:
                guild_ids.add(gid)
    result = []
    for gid in guild_ids:
        info = get_cached_guild_info(gid)
        if info:
            result.append({"id": gid, "name": info.get("name", gid)})
        else:
            result.append({"id": gid, "name": f"(guild {gid})"})
    return result


@mcp.tool()
async def list_channels(guild_id: str) -> str:
    """List cached channels for a guild, including categories and forum channels.

    Args:
        guild_id: Discord guild (server) ID
    """
    channels = get_cached_channels(guild_id)
    if not channels and has_token():
        channels = await api_list_channels(guild_id) or []
    by_id = {}
    for channel in channels:
        if channel.get("id"):
            by_id[str(channel["id"])] = {
                "id": str(channel["id"]), "name": channel.get("name"),
                "type": channel.get("type"), "topic": channel.get("topic"),
                "parent_id": channel.get("parent_id"),
            }
    all_msgs = get_cached_messages()
    for ch_id, msgs in all_msgs.items():
        if any(str(msg.get("guild_id")) == guild_id for msg in msgs):
            by_id.setdefault(ch_id, {
                "id": ch_id, "name": f"(channel {ch_id})", "type": None,
                "topic": None, "parent_id": None,
            })
        if ch_id in by_id:
            by_id[ch_id]["cached_message_count"] = len(msgs)
    for channel in by_id.values():
        channel.setdefault("cached_message_count", 0)
    return json.dumps(sorted(by_id.values(), key=lambda c: (c["type"] != 4, str(c["name"]).casefold())), ensure_ascii=False, indent=2)


@mcp.tool()
async def read_messages(
    channel_id: str, limit: int = 50, source: str = "auto",
    author: str | None = None, since: str | None = None, until: str | None = None,
) -> str:
    """Read messages from a Discord channel.

    Args:
        channel_id: Discord channel ID
        limit: Max messages to return (default 50)
        source: "cache" = cache only, "api" = API only, "auto" = cache first then API if insufficient
        author: Optional exact author ID, username, or display name
        since: Inclusive UTC date or ISO 8601 timestamp
        until: Inclusive UTC date or ISO 8601 timestamp
    """
    if source not in ("cache", "api", "auto"):
        return json.dumps({"error": "source must be cache, api, or auto"})
    limit = max(1, min(limit, 100))
    msgs = []

    if source in ("cache", "auto"):
        all_cached = get_cached_messages()
        msgs = all_cached.get(channel_id, [])

    if source == "api" or (source == "auto" and len(msgs) < limit and has_token()):
        api_msgs = await api_read_messages(channel_id, limit)
        if api_msgs:
            seen_ids = {m["id"] for m in msgs}
            for m in api_msgs:
                if m.get("id") not in seen_ids:
                    msgs.append(m)
            msgs.sort(key=lambda m: m.get("timestamp", ""), reverse=True)

    channel_guild_map = get_cached_channel_guild_map()
    try:
        msgs = filter_messages(
            msgs, channel_id=channel_id, author=author, since=since, until=until,
            channel_guild_map=channel_guild_map,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    if not msgs:
        return json.dumps({"error": f"No matching messages for channel {channel_id}", "source": source})

    formatted = [_format_msg(m, channel_guild_map) for m in msgs[:limit]]
    return json.dumps({
        "channel_id": channel_id,
        "count": len(formatted),
        "total_available": len(msgs),
        "messages": formatted,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_message(channel_id: str, message_id: str) -> str:
    """Get one cached message by ID, with its Discord link and attachment metadata.

    Args:
        channel_id: Discord channel ID
        message_id: Discord message ID
    """
    message = find_message(get_cached_messages(), channel_id, message_id)
    if message is None:
        return json.dumps({"error": "Message is not available in the local cache", "channel_id": channel_id, "message_id": message_id})
    return json.dumps({
        "message": _format_msg(message, get_cached_channel_guild_map()),
        "source": "cache",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_messages(
    query: str = "", channel_id: str | None = None, guild_id: str | None = None,
    limit: int = 30, author: str | None = None,
    since: str | None = None, until: str | None = None,
) -> str:
    """Search messages by text, channel, guild, author, and date.

    Args:
        query: Optional case-insensitive text to match
        channel_id: Optional channel ID
        guild_id: Optional guild ID for API-powered server-wide search
        author: Optional exact author ID, username, or display name
        since: Inclusive UTC date or ISO 8601 timestamp
        until: Inclusive UTC date or ISO 8601 timestamp
        limit: Max results (default 30)
    """
    limit = max(1, min(limit, 100))
    all_cached = get_cached_messages()
    channel_guild_map = get_cached_channel_guild_map()
    cached = [message for messages in all_cached.values() for message in messages]
    try:
        results = filter_messages(
            cached, query=query, channel_id=channel_id, guild_id=guild_id,
            author=author, since=since, until=until,
            channel_guild_map=channel_guild_map,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})

    if query and guild_id and has_token() and len(results) < limit:
        api_results = await api_search_messages(guild_id, query, limit)
        if api_results:
            seen_ids = {m["id"] for m in results}
            for m in api_results:
                if m.get("id") not in seen_ids:
                    message = dict(m)
                    message.setdefault("guild_id", guild_id)
                    results.append(message)

    results = filter_messages(
        results, query=query, channel_id=channel_id, guild_id=guild_id,
        author=author, since=since, until=until,
        channel_guild_map=channel_guild_map,
    )
    formatted = [_format_msg(m, channel_guild_map) for m in results[:limit]]
    return json.dumps({
        "query": query,
        "count": len(formatted),
        "messages": formatted,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def list_attachments(channel_id: str, message_id: str) -> str:
    """List file metadata for one message in the local Discord cache.

    Args:
        channel_id: Discord channel ID
        message_id: Discord message ID
    """
    message = find_message(get_cached_messages(), channel_id, message_id)
    if message is None:
        return json.dumps({"error": "Message is not available in the local cache"})
    attachments = [attachment_details(a) for a in (message.get("attachments") or []) if isinstance(a, dict)]
    return json.dumps({
        "channel_id": channel_id, "message_id": message_id,
        "count": len(attachments), "attachments": attachments,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def download_attachment(channel_id: str, message_id: str, attachment_id: str) -> str:
    """Download a cached message's attachment from Discord CDN into ./downloads.

    Args:
        channel_id: Discord channel ID
        message_id: Discord message ID
        attachment_id: Attachment ID returned by list_attachments
    """
    try:
        result = await asyncio.to_thread(
            download_cached_attachment, get_cached_messages(),
            channel_id, message_id, attachment_id,
        )
    except (ValueError, OSError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def export_messages(
    channel_id: str, format: str = "json", author: str | None = None,
    since: str | None = None, until: str | None = None,
) -> str:
    """Save cached channel messages to a JSON or Markdown file in ./exports.

    Args:
        channel_id: Discord channel or thread ID
        format: "json" or "markdown"
        author: Optional exact author ID, username, or display name
        since: Inclusive UTC date or ISO 8601 timestamp
        until: Inclusive UTC date or ISO 8601 timestamp
    """
    try:
        result = export_cached_messages(
            channel_id, get_cached_messages().get(channel_id, []),
            file_format=format, author=author, since=since, until=until,
            channel_guild_map=get_cached_channel_guild_map(),
        )
    except (ValueError, OSError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_threads(channel_id: str | None = None, guild_id: str | None = None) -> str:
    """List threads in a forum channel (archived) or all active threads in a guild. Requires API token.

    Args:
        channel_id: Forum/text channel ID to list its archived threads
        guild_id: Guild ID to list all active threads in the server
    """
    if not has_token():
        return json.dumps({"error": "API token required for thread listing. Set DISCORD_TOKEN in .env"})

    results = []

    if guild_id:
        active = await api_list_active_threads(guild_id)
        if active:
            for t in active:
                results.append({
                    "id": t.get("id"),
                    "name": t.get("name"),
                    "parent_id": t.get("parent_id"),
                    "message_count": t.get("message_count"),
                    "type": t.get("type"),
                    "archived": t.get("thread_metadata", {}).get("archived", False),
                })

    if channel_id:
        archived = await api_list_threads(channel_id)
        if archived:
            seen = {r["id"] for r in results}
            for t in archived:
                if t.get("id") not in seen:
                    results.append({
                        "id": t.get("id"),
                        "name": t.get("name"),
                        "parent_id": t.get("parent_id"),
                        "message_count": t.get("message_count"),
                        "type": t.get("type"),
                        "archived": t.get("thread_metadata", {}).get("archived", True),
                    })

    return json.dumps({
        "count": len(results),
        "threads": results,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def read_thread(thread_id: str, limit: int = 50) -> str:
    """Read messages from a forum thread or any thread. Threads are channels — uses the same messages API.

    Args:
        thread_id: Thread ID (same as channel ID for threads)
        limit: Max messages to return (default 50)
    """
    # Threads are just channels, try cache first then API
    all_cached = get_cached_messages()
    msgs = all_cached.get(thread_id, [])

    if len(msgs) < limit and has_token():
        api_msgs = await api_read_messages(thread_id, limit)
        if api_msgs:
            seen_ids = {m["id"] for m in msgs}
            for m in api_msgs:
                if m.get("id") not in seen_ids:
                    msgs.append(m)
            msgs.sort(key=lambda m: m.get("timestamp", ""), reverse=True)

    if not msgs:
        return json.dumps({"error": f"No messages for thread {thread_id}"})

    channel_guild_map = get_cached_channel_guild_map()
    formatted = [_format_msg(m, channel_guild_map) for m in msgs[:limit]]
    return json.dumps({
        "thread_id": thread_id,
        "count": len(formatted),
        "messages": formatted,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def cache_stats() -> str:
    """Show statistics about locally cached Discord data and API availability."""
    all_msgs = get_cached_messages()
    total_msgs = sum(len(msgs) for msgs in all_msgs.values())
    guild_ids = set()
    authors = set()
    oldest = None
    newest = None

    for msgs in all_msgs.values():
        for msg in msgs:
            gid = msg.get("guild_id")
            if gid:
                guild_ids.add(gid)
            authors.add(msg.get("author", {}).get("username", "unknown"))
            ts = msg.get("timestamp", "")
            if ts:
                if oldest is None or ts < oldest:
                    oldest = ts
                if newest is None or ts > newest:
                    newest = ts

    return json.dumps({
        "channels_with_messages": len(all_msgs),
        "total_messages": total_msgs,
        "unique_guilds": len(guild_ids),
        "unique_authors": len(authors),
        "oldest_message": oldest,
        "newest_message": newest,
        "api_available": has_token(),
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
