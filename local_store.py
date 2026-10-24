"""Persistent local cache in the project folder.

The MCP reads from this store first, so it keeps working even when the Discord
desktop client stops flushing its APPDATA cache. The APPDATA parser
(``discord_cache``) is now only a free seed/refresh source and a status probe —
it is never the live read path. Refresh (APPDATA re-snapshot or live API) writes
back here; reads never touch APPDATA once the store is populated.
"""

import json
from pathlib import Path

STORE_DIR = Path(__file__).resolve().parent / "cache"
_MSG_DIR = STORE_DIR / "messages"
_CH_DIR = STORE_DIR / "channels"
_GI_DIR = STORE_DIR / "guild_info"
_GUILDS = STORE_DIR / "guilds.json"
_MAP = STORE_DIR / "channel_guild_map.json"

_seed_attempted = False


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic — never leave a half-written cache file


def _merge_messages(existing: list[dict], incoming: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {m["id"]: m for m in existing if isinstance(m, dict) and m.get("id")}
    for m in incoming:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        by_id[m["id"]] = {**by_id.get(m["id"], {}), **m}
    return sorted(by_id.values(), key=lambda m: str(m.get("timestamp") or ""), reverse=True)


# --- write side (used by sync + API refresh) ---

def save_messages(channel_id: str, msgs: list[dict]) -> list[dict]:
    """Merge messages by id into the channel's store file, newest first."""
    merged = _merge_messages(load_messages(channel_id), msgs)
    _write_json(_MSG_DIR / f"{channel_id}.json", merged)
    return merged


def save_guilds(guilds: list[dict]) -> None:
    _write_json(_GUILDS, guilds)


def save_channels(guild_id: str, channels: list[dict]) -> None:
    _write_json(_CH_DIR / f"{guild_id}.json", channels)


def save_channel_guild_map(mapping: dict[str, str]) -> None:
    current = _read_json(_MAP, {})
    current.update({str(k): str(v) for k, v in mapping.items()})
    _write_json(_MAP, current)


def save_guild_info(guild_id: str, info: dict) -> None:
    _write_json(_GI_DIR / f"{guild_id}.json", info)


# --- read side (same names the server imported from discord_cache) ---

def load_messages(channel_id: str) -> list[dict]:
    return _read_json(_MSG_DIR / f"{channel_id}.json", [])


def get_cached_messages() -> dict[str, list[dict]]:
    _ensure_seeded()
    if not _MSG_DIR.exists():
        return {}
    return {p.stem: _read_json(p, []) for p in _MSG_DIR.glob("*.json")}


def get_cached_guilds() -> list[dict]:
    _ensure_seeded()
    return _read_json(_GUILDS, [])


def get_cached_channels(guild_id: str) -> list[dict]:
    _ensure_seeded()
    return _read_json(_CH_DIR / f"{guild_id}.json", [])


def get_cached_channel_guild_map() -> dict[str, str]:
    _ensure_seeded()
    mapping = _read_json(_MAP, {})
    if mapping:
        return mapping
    # Derive from stored channel lists if the map file is absent.
    derived: dict[str, str] = {}
    if _CH_DIR.exists():
        for p in _CH_DIR.glob("*.json"):
            for ch in _read_json(p, []):
                if isinstance(ch, dict) and ch.get("id"):
                    derived[str(ch["id"])] = str(ch.get("guild_id") or p.stem)
    return derived


def get_cached_guild_info(guild_id: str) -> dict | None:
    _ensure_seeded()
    return _read_json(_GI_DIR / f"{guild_id}.json", None)


# --- seeding / refresh from the APPDATA parser ---

def sync_from_appdata() -> dict:
    """Snapshot the Discord client's APPDATA cache into the local store (no API)."""
    import discord_cache as appdata

    messages = appdata.get_cached_messages()
    for channel_id, msgs in messages.items():
        save_messages(channel_id, msgs)

    save_guilds(appdata.get_cached_guilds())
    mapping = appdata.get_cached_channel_guild_map()
    if mapping:
        save_channel_guild_map(mapping)

    guild_ids = {str(g.get("id")) for g in appdata.get_cached_guilds() if g.get("id")}
    guild_ids.update(mapping.values())
    for gid in guild_ids:
        channels = appdata.get_cached_channels(gid)
        if channels:
            save_channels(gid, channels)
        info = appdata.get_cached_guild_info(gid)
        if info:
            save_guild_info(gid, info)

    return {
        "channels_synced": len(messages),
        "messages_synced": sum(len(m) for m in messages.values()),
        "guilds_synced": len(guild_ids),
    }


def _ensure_seeded() -> None:
    """One free APPDATA seed when the store is empty, so the MCP works out of the box."""
    global _seed_attempted
    if _seed_attempted or _MSG_DIR.exists():
        return
    _seed_attempted = True
    try:
        sync_from_appdata()
    except Exception:
        pass  # APPDATA may be missing/locked; store just stays empty until a manual sync


def _newest(msgs: list[dict]) -> str | None:
    stamps = [str(m.get("timestamp")) for m in msgs if isinstance(m, dict) and m.get("timestamp")]
    return max(stamps) if stamps else None


def store_status() -> dict:
    """Per-channel freshness: what the local store holds vs. what APPDATA has flushed."""
    import discord_cache as appdata

    stored = get_cached_messages()
    try:
        appdata_msgs = appdata.get_cached_messages()
    except Exception:
        appdata_msgs = {}

    channels = []
    for channel_id in sorted(set(stored) | set(appdata_msgs)):
        store_newest = _newest(stored.get(channel_id, []))
        appdata_newest = _newest(appdata_msgs.get(channel_id, []))
        channels.append({
            "channel_id": channel_id,
            "stored_count": len(stored.get(channel_id, [])),
            "store_newest": store_newest,
            "appdata_newest": appdata_newest,
            "appdata_ahead": bool(appdata_newest and (not store_newest or appdata_newest > store_newest)),
        })
    return {
        "store_dir": str(STORE_DIR),
        "channels": channels,
        "channels_appdata_ahead": [c["channel_id"] for c in channels if c["appdata_ahead"]],
    }
