"""Read-only helpers for filtering, exporting, and downloading cached Discord data."""

import json
import re
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = ROOT_DIR / "downloads"
EXPORT_DIR = ROOT_DIR / "exports"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
_CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date_bound(value: str | None, *, end: bool) -> datetime | None:
    if not value:
        return None
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime.combine(day, time.max if end else time.min, timezone.utc)
        return _as_utc(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD or ISO 8601.") from exc


def filter_messages(
    messages: list[dict], *, query: str = "", channel_id: str | None = None,
    guild_id: str | None = None, author: str | None = None,
    since: str | None = None, until: str | None = None,
    channel_guild_map: dict[str, str] | None = None,
) -> list[dict]:
    """Filter messages using inclusive UTC date bounds and exact author identity."""
    lower = _date_bound(since, end=False)
    upper = _date_bound(until, end=True)
    if lower and upper and lower > upper:
        raise ValueError("since must be earlier than or equal to until")
    needle = query.casefold()
    author_needle = author.casefold() if author else None
    channel_guild_map = channel_guild_map or {}
    results = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        mid_channel = str(message.get("channel_id") or "")
        mid_guild = str(message.get("guild_id") or channel_guild_map.get(mid_channel) or "")
        if channel_id and mid_channel != channel_id:
            continue
        if guild_id and mid_guild != guild_id:
            continue
        if needle and needle not in str(message.get("content") or "").casefold():
            continue
        if author_needle:
            user = message.get("author") or {}
            identities = (
                str(user.get("id") or ""), str(user.get("username") or ""),
                str(user.get("global_name") or ""), str(user.get("display_name") or ""),
            )
            if author_needle not in (item.casefold() for item in identities):
                continue
        if lower or upper:
            try:
                timestamp = _as_utc(str(message["timestamp"]))
            except (KeyError, ValueError):
                continue
            if lower and timestamp < lower:
                continue
            if upper and timestamp > upper:
                continue
        results.append(message)
    return sorted(results, key=lambda m: str(m.get("timestamp") or ""), reverse=True)


def find_message(messages_by_channel: dict[str, list[dict]], channel_id: str, message_id: str) -> dict | None:
    return next(
        (message for message in messages_by_channel.get(channel_id, [])
         if str(message.get("id")) == message_id),
        None,
    )


def _safe_cdn_url(url: str | None) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https" and parsed.hostname in _CDN_HOSTS
            and not parsed.username and not parsed.password and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def attachment_details(attachment: dict) -> dict:
    url = attachment.get("url")
    return {
        "id": str(attachment.get("id")) if attachment.get("id") is not None else None,
        "filename": attachment.get("filename"),
        "size": attachment.get("size"),
        "content_type": attachment.get("content_type"),
        "description": attachment.get("description"),
        "width": attachment.get("width"),
        "height": attachment.get("height"),
        "url": url,
        "has_discord_cdn_url": _safe_cdn_url(url),
    }


def format_message(message: dict, channel_guild_map: dict[str, str] | None = None) -> dict:
    user = message.get("author") or {}
    channel_id = str(message.get("channel_id")) if message.get("channel_id") else None
    guild_id = message.get("guild_id") or (channel_guild_map or {}).get(channel_id or "")
    message_id = str(message.get("id")) if message.get("id") else None
    scope = str(guild_id) if guild_id else "@me"
    message_url = (
        f"https://discord.com/channels/{scope}/{channel_id}/{message_id}"
        if channel_id and message_id else None
    )
    attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
    return {
        "id": message_id,
        "channel_id": channel_id,
        "guild_id": str(guild_id) if guild_id else None,
        "author": user.get("username") or "unknown",
        "author_id": str(user.get("id")) if user.get("id") else None,
        "author_display_name": user.get("global_name") or user.get("display_name"),
        "content": message.get("content") or "",
        "timestamp": message.get("timestamp"),
        "edited_timestamp": message.get("edited_timestamp"),
        "message_url": message_url,
        "attachments": [a.get("filename") for a in attachments],
        "attachment_details": [attachment_details(a) for a in attachments],
        "embeds_count": len(message.get("embeds") or []),
    }


def _safe_filename(name: str) -> str:
    basename = re.split(r"[/\\]", name)[-1]
    basename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", basename).strip(" .")
    return basename[:150] or "attachment"


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        if not _safe_cdn_url(target):
            raise ValueError("Attachment redirected outside Discord CDN")
        return super().redirect_request(req, fp, code, msg, headers, target)


def download_cached_attachment(
    messages_by_channel: dict[str, list[dict]], channel_id: str,
    message_id: str, attachment_id: str,
) -> dict:
    """Download only an attachment referenced by a cached message."""
    if not all(value.isdigit() for value in (channel_id, message_id, attachment_id)):
        raise ValueError("channel_id, message_id, and attachment_id must be numeric Discord IDs")
    message = find_message(messages_by_channel, channel_id, message_id)
    if not message:
        raise ValueError("Message is not available in the local cache")
    attachment = next(
        (a for a in (message.get("attachments") or [])
         if isinstance(a, dict) and str(a.get("id")) == attachment_id),
        None,
    )
    if not attachment:
        raise ValueError("Attachment is not available in the cached message")
    url = attachment.get("url")
    if not _safe_cdn_url(url):
        raise ValueError("Cached attachment has no valid Discord CDN URL")
    if isinstance(attachment.get("size"), int) and attachment["size"] > MAX_DOWNLOAD_BYTES:
        raise ValueError("Attachment exceeds the 100 MiB download limit")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(str(attachment.get("filename") or "attachment"))
    target = DOWNLOAD_DIR / f"{channel_id}_{message_id}_{attachment_id}_{filename}"
    if target.exists():
        return {"status": "already_downloaded", "path": str(target), "size": target.stat().st_size}

    request = Request(url, headers={"User-Agent": "discord-cache-mcp/0.2"})
    temp_path = None
    try:
        opener = build_opener(_SafeRedirect())
        with opener.open(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_DOWNLOAD_BYTES:
                raise ValueError("Attachment exceeds the 100 MiB download limit")
            with tempfile.NamedTemporaryFile(dir=DOWNLOAD_DIR, delete=False) as temp:
                temp_path = Path(temp.name)
                size = 0
                while chunk := response.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Attachment exceeds the 100 MiB download limit")
                    temp.write(chunk)
        if target.exists():
            return {"status": "already_downloaded", "path": str(target), "size": target.stat().st_size}
        temp_path.replace(target)
        temp_path = None
        return {"status": "downloaded", "path": str(target), "size": size}
    except HTTPError as exc:
        if exc.code in (403, 404):
            raise ValueError("Attachment URL expired or is unavailable") from exc
        raise ValueError(f"Attachment download failed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"Attachment download failed: {exc.reason}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def export_cached_messages(
    channel_id: str, messages: list[dict], *, file_format: str = "json",
    author: str | None = None, since: str | None = None, until: str | None = None,
    channel_guild_map: dict[str, str] | None = None,
) -> dict:
    """Write a filtered cached conversation to a local JSON or Markdown file."""
    if not channel_id.isdigit():
        raise ValueError("channel_id must be a numeric Discord ID")
    file_format = file_format.lower()
    if file_format not in ("json", "markdown"):
        raise ValueError("format must be 'json' or 'markdown'")
    selected = filter_messages(
        messages, channel_id=channel_id, author=author, since=since, until=until,
        channel_guild_map=channel_guild_map,
    )
    if not selected:
        raise ValueError("No cached messages match the export filters")
    formatted = [format_message(message, channel_guild_map) for message in reversed(selected)]
    exported_at = datetime.now(timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    extension = "json" if file_format == "json" else "md"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = EXPORT_DIR / f"channel-{channel_id}-{stamp}.{extension}"
    if file_format == "json":
        body = json.dumps({
            "source": "local_cache", "channel_id": channel_id,
            "exported_at": exported_at, "count": len(formatted), "messages": formatted,
        }, ensure_ascii=False, indent=2)
    else:
        lines = [f"# Discord channel {channel_id}", "", f"Exported: {exported_at}",
                 f"Messages: {len(formatted)}", ""]
        for message in formatted:
            lines.extend([
                f"## {message['timestamp'] or 'Unknown time'} — {message['author']}", "",
                f"Message: {message['message_url'] or 'link unavailable'}", "",
                message["content"] or "(no text)", "",
            ])
            if message["attachment_details"]:
                lines.append("Attachments:")
                for item in message["attachment_details"]:
                    lines.append(f"- {item['filename'] or 'attachment'}: {item['url'] or 'URL unavailable'}")
                lines.append("")
        body = "\n".join(lines)
    with target.open("x", encoding="utf-8") as output:
        output.write(body)
    return {"path": str(target), "format": file_format, "count": len(formatted), "source": "local_cache"}
