"""Self-check: blockfile sniffing + platform fingerprint. Run: python test_cache.py"""

import gzip
import json
import os

import discord_api
import discord_cache as dc


def test_sniff_messages():
    msgs = [{"id": "1", "channel_id": "42", "author": {"username": "a"}, "content": "hi"}]
    raw = gzip.compress(json.dumps(msgs).encode())
    blobs = list(dc._json_blobs(b"\x00\x11junk" + raw))
    assert blobs and blobs[0] == msgs, blobs
    entry = dc._sniff_entry(blobs[0], json.dumps(blobs[0]).encode())
    assert entry and dc.MSG_PATTERN.search(entry.url).group(1) == "42", entry


def test_sniff_channels_and_guilds():
    chans = [{"id": "9", "name": "gen", "type": 0, "guild_id": "7", "position": 0}]
    e = dc._sniff_entry(chans, b"")
    assert e and dc.CHANNEL_PATTERN.search(e.url).group(1) == "7"

    guilds = [{"id": "7", "name": "srv", "owner": True}]
    e = dc._sniff_entry(guilds, b"")
    assert e and dc.GUILD_PATTERN.search(e.url)

    assert dc._sniff_entry([{"nothing": 1}], b"") is None


def test_plain_json_blob():
    guild = {"id": "7", "name": "srv", "roles": []}
    assert list(dc._json_blobs(json.dumps(guild).encode()))[0] == guild


def test_fingerprint_matches_host():
    props = json.loads(__import__("base64").b64decode(discord_api._SUPER_PROPERTIES))
    expected = "Windows" if os.name == "nt" else "Mac OS X"
    assert props["os"] == expected, props["os"]
    assert expected.split()[0].lower() in props["browser_user_agent"].lower()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
