"""Discord local cache parser — reads Chromium Simple Cache entries from the Discord desktop client."""

import gzip
import json
import os
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


def _default_cache_dir() -> Path:
    # Windows/macOS Discord both keep Chromium cache under the app data root,
    # but the on-disk entry format differs (blockfile vs simple cache).
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / "Library" / "Application Support"
    return base / "discord" / "Cache" / "Cache_Data"


# DISCORD_CACHE_DIR overrides (installer sets it when Discord lives elsewhere)
CACHE_DIR = Path(os.environ.get("DISCORD_CACHE_DIR") or _default_cache_dir())
SIMPLE_CACHE_MAGIC = 0xFCFB6D1BA7725C30


@dataclass
class CacheEntry:
    url: str
    body: bytes
    headers_raw: bytes


def _read_windows_shared(filepath: Path) -> bytes:
    """Read a Chromium blockfile while Discord has it open on Windows."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    # Python's regular open can be denied by Chromium's sharing mode. Match
    # Chromium's shared access without changing or locking the cache file.
    handle = create_file(str(filepath), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        close_handle(handle)
        raise
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def _read_cache_bytes(filepath: Path) -> bytes:
    try:
        return filepath.read_bytes()
    except PermissionError:
        if os.name != "nt":
            raise
        return _read_windows_shared(filepath)


def _parse_simple_cache(data: bytes) -> CacheEntry | None:
    """Parse a Chromium Simple Cache entry when its header is present."""
    if len(data) < 24:
        return None

    magic = struct.unpack("<Q", data[:8])[0]
    if magic != SIMPLE_CACHE_MAGIC:
        return None

    key_len = struct.unpack("<I", data[12:16])[0]
    if key_len > 2000 or 20 + key_len > len(data):
        return None

    raw_key = data[20 : 20 + key_len].decode("utf-8", errors="replace")
    url = re.sub(r"^\s*\d+/\d+/", "", raw_key).strip()

    body_start = 20 + key_len
    for i in range(body_start, min(body_start + 200, len(data) - 2)):
        if data[i] == 0x1F and data[i + 1] == 0x8B and data[i + 2] == 0x08:
            try:
                dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
                body = dec.decompress(data[i:])
                return CacheEntry(url=url, body=body, headers_raw=b"")
            except Exception:
                continue

    body = data[body_start:]
    return CacheEntry(url=url, body=body, headers_raw=b"")


def parse_cache_file(filepath: Path) -> CacheEntry | None:
    try:
        return _parse_simple_cache(_read_cache_bytes(filepath))
    except OSError:
        return None


# URL patterns for Discord API endpoints
MSG_PATTERN = re.compile(r"/api/v\d+/channels/(\d+)/messages")
GUILD_PATTERN = re.compile(r"/api/v\d+/users/@me/guilds")
CHANNEL_PATTERN = re.compile(r"/api/v\d+/guilds/(\d+)/channels")
SEARCH_PATTERN = re.compile(r"/api/v\d+/guilds/(\d+)/messages/search")
GUILD_DETAIL_PATTERN = re.compile(r"/api/v\d+/guilds/(\d+)(?:/pro|$)")
GUILD_URL_PATTERN = re.compile(r"/api/v\d+/guilds/(\d+)")


def _json_blobs(data: bytes):
    """Yield every JSON document in a cache file, gzipped or plain.

    Blockfile entries (Windows) carry no usable URL key, so we brute-scan the
    payload instead of trusting the simple-cache header layout.
    """
    i = 0
    while True:
        i = data.find(b"\x1f\x8b\x08", i)
        if i < 0:
            break
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data[i:])
            yield json.loads(body.decode("utf-8"))
        except Exception:
            pass
        i += 1
    for start in (data.find(b"[{"), data.find(b'{"')):
        if start >= 0:
            try:
                yield json.loads(data[start:].decode("utf-8"))
            except Exception:
                pass


def _sniff_entry(obj, raw: bytes) -> CacheEntry | None:
    """Recognize a Discord API payload by its shape and synthesize its URL."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        first = obj[0]
        if "author" in first and "channel_id" in first:
            return CacheEntry(
                url=f"https://discord.com/api/v9/channels/{first['channel_id']}/messages",
                body=raw, headers_raw=b"")
        if "type" in first and "guild_id" in first and "position" in first:
            return CacheEntry(
                url=f"https://discord.com/api/v9/guilds/{first['guild_id']}/channels",
                body=raw, headers_raw=b"")
        if "id" in first and "name" in first and "owner" in first:
            return CacheEntry(
                url="https://discord.com/api/v9/users/@me/guilds", body=raw, headers_raw=b"")
    elif isinstance(obj, dict):
        if "author" in obj and "channel_id" in obj and "id" in obj:
            return CacheEntry(
                url=f"https://discord.com/api/v9/channels/{obj['channel_id']}/messages/{obj['id']}",
                body=raw, headers_raw=b"")
        if "id" in obj and "name" in obj and "roles" in obj:
            return CacheEntry(
                url=f"https://discord.com/api/v9/guilds/{obj['id']}", body=raw, headers_raw=b"")
    return None


_cache: list[CacheEntry] | None = None
_cache_signature: tuple[tuple[str, int, int], ...] | None = None


def scan_cache(force: bool = False) -> list[CacheEntry]:
    global _cache, _cache_signature
    if not CACHE_DIR.exists():
        return []

    files = []
    signature = []
    for file in CACHE_DIR.iterdir():
        if file.name.startswith(".") or not file.is_file():
            continue
        try:
            stat = file.stat()
        except OSError:
            continue
        files.append(file)
        signature.append((file.name, stat.st_size, stat.st_mtime_ns))
    current_signature = tuple(sorted(signature))
    if not force and _cache is not None and current_signature == _cache_signature:
        return _cache

    entries = []
    for f in files:
        try:
            data = _read_cache_bytes(f)
        except OSError:
            continue
        entry = _parse_simple_cache(data)
        if entry and "discord.com/api/" in entry.url:
            entries.append(entry)
            continue
        for obj in _json_blobs(data):
            sniffed = _sniff_entry(obj, json.dumps(obj).encode())
            if sniffed:
                entries.append(sniffed)

    _cache = entries
    _cache_signature = current_signature
    return entries


def get_cached_messages() -> dict[str, list[dict]]:
    """Return {channel_id: [messages]} from cache."""
    channels: dict[str, list[dict]] = {}
    for entry in scan_cache():
        m = MSG_PATTERN.search(entry.url)
        if not m:
            continue
        channel_id = m.group(1)
        try:
            text = entry.body.decode("utf-8", errors="replace")
            payload = json.loads(text)
            msgs = payload if isinstance(payload, list) else [payload]
            existing = channels.get(channel_id, [])
            seen_ids = {msg["id"] for msg in existing}
            for msg in msgs:
                if not isinstance(msg, dict) or not msg.get("id"):
                    continue
                if msg.get("channel_id") and str(msg["channel_id"]) != channel_id:
                    continue
                if msg["id"] not in seen_ids:
                    msg.setdefault("channel_id", channel_id)
                    existing.append(msg)
                    seen_ids.add(msg["id"])
            channels[channel_id] = existing
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            continue
    for ch_id in channels:
        channels[ch_id].sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return channels


def get_cached_guilds() -> list[dict]:
    """Return guild list from cache. Merges @me/guilds with guild info found in other endpoints."""
    guilds_by_id: dict[str, dict] = {}

    entries = scan_cache()

    for entry in entries:
        if GUILD_PATTERN.search(entry.url):
            try:
                guilds = json.loads(entry.body.decode("utf-8", errors="replace"))
                if isinstance(guilds, list):
                    for g in guilds:
                        gid = g.get("id")
                        if gid:
                            guilds_by_id[gid] = g
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue

    for entry in entries:
        m = GUILD_URL_PATTERN.search(entry.url)
        if not m:
            continue
        gid = m.group(1)
        try:
            data = json.loads(entry.body.decode("utf-8", errors="replace"))
            if isinstance(data, dict) and "name" in data and "id" in data and data["id"] == gid:
                if gid not in guilds_by_id:
                    guilds_by_id[gid] = {"id": gid, "name": data["name"]}
                elif not guilds_by_id[gid].get("name"):
                    guilds_by_id[gid]["name"] = data["name"]
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            continue

    for entry in entries:
        m = GUILD_URL_PATTERN.search(entry.url)
        if m and m.group(1) not in guilds_by_id:
            gid = m.group(1)
            guilds_by_id[gid] = {"id": gid, "name": f"(guild {gid})"}

    return list(guilds_by_id.values())


def get_cached_channels(guild_id: str) -> list[dict]:
    """Return channel list for a guild from cache."""
    by_id: dict[str, dict] = {}
    for entry in scan_cache():
        m = CHANNEL_PATTERN.search(entry.url)
        if m and m.group(1) == guild_id:
            try:
                channels = json.loads(entry.body.decode("utf-8", errors="replace"))
                if isinstance(channels, list):
                    for channel in channels:
                        if isinstance(channel, dict) and channel.get("id"):
                            by_id[str(channel["id"])] = channel
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue
    return list(by_id.values())


def get_cached_channel_guild_map() -> dict[str, str]:
    """Map cached channel IDs to guild IDs for reliable message links."""
    result = {}
    for entry in scan_cache():
        match = CHANNEL_PATTERN.search(entry.url)
        if not match:
            continue
        try:
            channels = json.loads(entry.body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(channels, list):
            for channel in channels:
                if isinstance(channel, dict) and channel.get("id"):
                    result[str(channel["id"])] = str(channel.get("guild_id") or match.group(1))
    return result


def get_cached_guild_info(guild_id: str) -> dict | None:
    for entry in scan_cache():
        m = GUILD_DETAIL_PATTERN.search(entry.url)
        if m and m.group(1) == guild_id:
            try:
                return json.loads(entry.body.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue
    return None


def search_cached_messages(query: str, channel_id: str | None = None) -> list[dict]:
    """Search across all cached messages."""
    query_lower = query.lower()
    results = []
    all_msgs = get_cached_messages()

    for ch_id, msgs in all_msgs.items():
        if channel_id and ch_id != channel_id:
            continue
        for msg in msgs:
            content = msg.get("content", "")
            if query_lower in content.lower():
                results.append(msg)

    results.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return results
