"""Behavior checks for local cache search, attachment downloads, and exports."""

import asyncio
import gzip
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

import discord_cache as cache
import discord_read as read


def sample_messages():
    return [
        {
            "id": "100", "channel_id": "20", "guild_id": "10",
            "author": {"id": "1", "username": "Alice"},
            "content": "Old note", "timestamp": "2026-09-22T10:00:00Z",
            "attachments": [],
        },
        {
            "id": "101", "channel_id": "20", "guild_id": "10",
            "author": {"id": "2", "username": "Bob"},
            "content": "New note", "timestamp": "2026-09-24T10:00:00+09:00",
            "attachments": [{
                "id": "501", "filename": "../../report.txt", "size": 4,
                "content_type": "text/plain",
                "url": "https://cdn.discordapp.com/attachments/20/501/report.txt",
            }],
        },
    ]


class ReadFeatureTests(unittest.TestCase):
    def test_combined_search_filters_and_message_link(self):
        messages = sample_messages()
        result = read.filter_messages(
            messages, query="NOTE", channel_id="20", guild_id="10",
            author="bob", since="2026-09-24", until="2026-09-24",
        )
        self.assertEqual([item["id"] for item in result], ["101"])
        formatted = read.format_message(result[0])
        self.assertEqual(formatted["message_url"], "https://discord.com/channels/10/20/101")
        self.assertEqual(formatted["attachment_details"][0]["content_type"], "text/plain")
        self.assertTrue(formatted["attachment_details"][0]["has_discord_cdn_url"])
        with self.assertRaisesRegex(ValueError, "Invalid date"):
            read.filter_messages(messages, since="not-a-date")

    def test_single_message_cache_payload_is_recognized(self):
        message = sample_messages()[0]
        entry = cache._sniff_entry(message, json.dumps(message).encode())
        self.assertIsNotNone(entry)
        self.assertEqual(cache.MSG_PATTERN.search(entry.url).group(1), "20")

    def test_cache_rescans_when_existing_file_changes(self):
        previous_dir, previous_cache, previous_signature = cache.CACHE_DIR, cache._cache, cache._cache_signature
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                cache.CACHE_DIR = Path(temp_dir)
                cache._cache = None
                cache._cache_signature = None
                cache_file = cache.CACHE_DIR / "entry_1"
                first = [{"id": "1", "channel_id": "20", "author": {"username": "A"}, "content": "one"}]
                cache_file.write_bytes(b"header" + gzip.compress(json.dumps(first).encode()))
                self.assertEqual(cache.get_cached_messages()["20"][0]["content"], "one")
                old_mtime = cache_file.stat().st_mtime_ns
                second = [{"id": "2", "channel_id": "20", "author": {"username": "A"}, "content": "two"}]
                cache_file.write_bytes(b"header" + gzip.compress(json.dumps(second).encode()))
                os.utime(cache_file, ns=(old_mtime + 1_000_000_000, old_mtime + 1_000_000_000))
                self.assertEqual(cache.get_cached_messages()["20"][0]["content"], "two")
        finally:
            cache.CACHE_DIR, cache._cache, cache._cache_signature = previous_dir, previous_cache, previous_signature

    @unittest.skipUnless(os.name == "nt", "Windows blockfile sharing")
    def test_locked_blockfile_is_read_with_windows_sharing(self):
        previous_dir, previous_cache, previous_signature = cache.CACHE_DIR, cache._cache, cache._cache_signature
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                cache.CACHE_DIR = Path(temp_dir)
                cache._cache = None
                cache._cache_signature = None
                blockfile = cache.CACHE_DIR / "data_1"
                blockfile.write_bytes(b"placeholder")
                payload = b"blockfile-header" + gzip.compress(json.dumps(sample_messages()).encode())
                with patch.object(Path, "read_bytes", side_effect=PermissionError), \
                     patch.object(cache, "_read_windows_shared", return_value=payload) as shared_read:
                    messages = cache.get_cached_messages()
                self.assertEqual([message["id"] for message in messages["20"]], ["101", "100"])
                shared_read.assert_called_once_with(blockfile)
        finally:
            cache.CACHE_DIR, cache._cache, cache._cache_signature = previous_dir, previous_cache, previous_signature

    def test_export_json_and_markdown_with_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(read, "EXPORT_DIR", Path(temp_dir)):
            json_result = read.export_cached_messages("20", sample_messages(), file_format="json", author="Bob")
            payload = json.loads(Path(json_result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["messages"][0]["id"], "101")
            markdown_result = read.export_cached_messages("20", sample_messages(), file_format="markdown")
            markdown = Path(markdown_result["path"]).read_text(encoding="utf-8")
            self.assertLess(markdown.index("Old note"), markdown.index("New note"))
            self.assertIn("https://discord.com/channels/10/20/101", markdown)

    def test_download_uses_cached_cdn_url_and_safe_filename(self):
        class Response:
            headers = {"Content-Length": "4"}

            def __init__(self):
                self.data = b"data"

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, count):
                chunk, self.data = self.data[:count], self.data[count:]
                return chunk

        class Opener:
            def open(self, request, timeout):
                self_url = request.full_url
                assert self_url.startswith("https://cdn.discordapp.com/")
                return Response()

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(read, "DOWNLOAD_DIR", Path(temp_dir)):
            with patch.object(read, "build_opener", return_value=Opener()):
                result = read.download_cached_attachment({"20": sample_messages()}, "20", "101", "501")
            self.assertEqual(Path(result["path"]).read_bytes(), b"data")
            self.assertEqual(Path(result["path"]).parent, Path(temp_dir))
            self.assertTrue(Path(result["path"]).name.endswith("report.txt"))
            self.assertEqual(read.download_cached_attachment({"20": sample_messages()}, "20", "101", "501")["status"], "already_downloaded")
            bad = sample_messages()
            bad[1]["attachments"][0]["url"] = "https://example.com/file"
            with self.assertRaisesRegex(ValueError, "valid Discord CDN URL"):
                read.download_cached_attachment({"20": bad}, "20", "101", "501")

    def test_expired_attachment_has_clear_error(self):
        class ExpiredOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(read, "DOWNLOAD_DIR", Path(temp_dir)):
            with patch.object(read, "build_opener", return_value=ExpiredOpener()):
                with self.assertRaisesRegex(ValueError, "expired or is unavailable"):
                    read.download_cached_attachment({"20": sample_messages()}, "20", "101", "501")
            self.assertEqual(list(Path(temp_dir).iterdir()), [])

    def test_mcp_tool_wrappers_use_local_data(self):
        fake_mcp_package = types.ModuleType("mcp")
        fake_mcp_package.__path__ = []
        fake_mcp_server = types.ModuleType("mcp.server")
        fake_mcp_server.__path__ = []
        fake_fastmcp = types.ModuleType("mcp.server.fastmcp")

        class FakeFastMCP:
            def __init__(self, name):
                self.name = name

            def tool(self):
                return lambda function: function

        fake_fastmcp.FastMCP = FakeFastMCP
        fake_api = types.ModuleType("discord_api")
        async def no_api(*_args, **_kwargs):
            return None
        for name in (
            "api_list_active_threads", "api_list_channels", "api_list_guilds",
            "api_list_threads", "api_read_messages", "api_search_messages",
        ):
            setattr(fake_api, name, no_api)
        fake_api.has_token = lambda: False
        fake_modules = {
            "mcp": fake_mcp_package, "mcp.server": fake_mcp_server,
            "mcp.server.fastmcp": fake_fastmcp, "discord_api": fake_api,
        }
        with patch.dict(sys.modules, fake_modules):
            sys.modules.pop("server", None)
            import server
            with patch.object(server, "get_cached_messages", return_value={"20": sample_messages()}), \
                 patch.object(server, "get_cached_channel_guild_map", return_value={"20": "10"}), \
                 patch.object(server, "get_cached_channels", return_value=[
                     {"id": "30", "name": "Topics", "type": 4, "guild_id": "10"},
                     {"id": "20", "name": "general", "type": 0, "guild_id": "10", "parent_id": "30"},
                 ]):
                result = json.loads(asyncio.run(server.search_messages(query="note", author="Bob", since="2026-09-24")))
                self.assertEqual([message["id"] for message in result["messages"]], ["101"])
                listed = json.loads(asyncio.run(server.list_channels("10")))
                self.assertEqual([channel["id"] for channel in listed], ["30", "20"])
                self.assertEqual(listed[0]["cached_message_count"], 0)
                self.assertEqual(listed[1]["cached_message_count"], 2)
                filtered = json.loads(asyncio.run(server.read_messages("20", source="cache", author="Alice")))
                self.assertEqual([message["id"] for message in filtered["messages"]], ["100"])
                found = json.loads(asyncio.run(server.get_message("20", "101")))
                self.assertEqual(found["source"], "cache")
                self.assertEqual(found["message"]["message_url"], "https://discord.com/channels/10/20/101")
                files = json.loads(server.list_attachments("20", "101"))
                self.assertEqual(files["attachments"][0]["id"], "501")
                api_message = {
                    "id": "102", "channel_id": "20", "author": {"username": "Bob"},
                    "content": "API result", "timestamp": "2026-09-24T02:00:00Z",
                }
                async def api_search(*_args):
                    return [api_message]
                with patch.object(server, "has_token", return_value=True), \
                     patch.object(server, "api_search_messages", side_effect=api_search):
                    found = json.loads(asyncio.run(server.search_messages(query="API result", guild_id="10")))
                self.assertEqual(found["messages"][0]["message_url"], "https://discord.com/channels/10/20/102")
            sys.modules.pop("server", None)


if __name__ == "__main__":
    unittest.main()
