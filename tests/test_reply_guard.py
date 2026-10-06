"""Offline contract tests; no AstrBot installation or model calls required."""

import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch


class Reply:
    def __init__(self, sender_id="42", chain=None, message_str="", id="123"):
        self.sender_id = sender_id
        self.chain = chain
        self.message_str = message_str
        self.id = id


def segment(kind, **data):
    return {"type": kind, "data": data}


IMAGE_LINK = [segment("image", url="https://example.com/image.png"),
              segment("text", text="详情 https://example.com/post")]


def load_plugin():
    modules = {name: ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.event",
        "astrbot.api.message_components", "astrbot.api.star")}
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("test")
    event_api = modules["astrbot.api.event"]
    event_api.AstrMessageEvent = object
    event_api.filter = SimpleNamespace(
        EventMessageType=SimpleNamespace(ALL=1),
        event_message_type=lambda *a, **kw: lambda fn: fn)
    modules["astrbot.api.message_components"].Reply = Reply
    star_api = modules["astrbot.api.star"]
    star_api.Context = object
    star_api.Star = type("Star", (), {"__init__": lambda self, ctx: None})
    star_api.register = lambda *a: lambda cls: cls
    spec = importlib.util.spec_from_file_location(
        "reply_guard_test_module", Path(__file__).resolve().parents[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin = load_plugin()

EXPLICIT_RULES = {"format_patterns": [r"(?s)(?=.*\[图片\])(?=.*https?://).+"]}


class Event:
    def __init__(self, messages, private=False, platform="aiocqhttp"):
        self.messages = messages
        self.private = private
        self.platform = platform
        self.blocked = False
        self.stopped = False
        self.bot = SimpleNamespace(call_action=AsyncMock())

    def get_messages(self): return self.messages
    def get_self_id(self): return "42"
    def get_platform_name(self): return self.platform
    def is_private_chat(self): return self.private
    def should_call_llm(self, value): self.blocked = value
    def stop_event(self): self.stopped = True


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_defaults_or_legacy_mode_interception(self):
        for config in ({}, {"mode": "all"}, {"mode": "image_link"}, {"format_patterns": []}):
            event = await self.run_guard(Reply(sender_id=0, chain=IMAGE_LINK), config)
            self.assertFalse(event.blocked)
            event.bot.call_action.assert_not_awaited()
            event = await self.run_guard(Reply(chain=IMAGE_LINK), config)
            self.assertFalse(event.blocked)

    async def test_custom_keyword_matches_original_only(self):
        config = {"format_patterns": ["签到成功"]}
        event = await self.run_guard(Reply(chain=[segment("text", text="签到成功，积分+1")]), config)
        self.assertTrue(event.blocked)
        event = Event([Reply(chain=[segment("text", text="普通聊天")])])
        event.message_str = "签到成功"
        await plugin.ReplyGuard(None, config).guard_reply(event)
        self.assertFalse(event.blocked)

    async def test_custom_exact_component_order(self):
        config = {"format_patterns": [r"^\[图片\]https?://\S+$"]}
        chain = [segment("image"), segment("text", text="https://example.com")]
        self.assertTrue((await self.run_guard(Reply(chain=chain), config)).blocked)
        self.assertFalse((await self.run_guard(Reply(chain=list(reversed(chain))), config)).blocked)

    async def test_custom_rules_or_empty_and_invalid(self):
        for patterns, expected in [([], False), (["", "["], False),
                                   (["不匹配", r"\[图片\]"], True)]:
            event = await self.run_guard(Reply(chain=IMAGE_LINK),
                                         {"format_patterns": patterns})
            self.assertEqual(event.blocked, expected)

    async def test_custom_pattern_update_and_text_fallback(self):
        config = {"format_patterns": ["旧通知"], "fetch_missing_reply": False}
        guard = plugin.ReplyGuard(None, config)
        event = Event([Reply(message_str="新通知")])
        await guard.guard_reply(event)
        self.assertFalse(event.blocked)
        config["format_patterns"][:] = ["新通知"]
        await guard.guard_reply(event)
        self.assertTrue(event.blocked)

    async def run_guard(self, reply=None, config=None, **kwargs):
        event = Event([reply] if reply else [], **kwargs)
        guard = plugin.ReplyGuard(None, EXPLICIT_RULES if config is None else config)
        await guard.guard_reply(event)
        return event

    async def test_explicit_image_link_rule_blocks_default_llm_only(self):
        event = await self.run_guard(Reply(chain=IMAGE_LINK))
        self.assertTrue(event.blocked)
        self.assertFalse(event.stopped)
        event.bot.call_action.assert_not_awaited()

    async def test_other_user_never_blocks_or_fetches(self):
        event = await self.run_guard(Reply(sender_id="88"))
        self.assertFalse(event.blocked)
        event.bot.call_action.assert_not_awaited()

    async def test_image_download_url_is_not_text_link(self):
        event = await self.run_guard(Reply(chain=IMAGE_LINK[:1]))
        self.assertFalse(event.blocked)

    async def test_text_only_is_allowed(self):
        event = await self.run_guard(Reply(chain=IMAGE_LINK[1:]))
        self.assertFalse(event.blocked)

    async def test_no_quote_is_allowed(self):
        self.assertFalse((await self.run_guard()).blocked)

    async def test_explicit_match_all_rule_blocks_text_quote(self):
        self.assertTrue((await self.run_guard(
            Reply(sender_id=42, message_str="hello"), {"format_patterns": ["(?s).*"], "fetch_missing_reply": False})).blocked)

    async def test_disabled_and_chat_scopes(self):
        for config, private in [({"enabled": False}, False),
                                ({"apply_group": False}, False),
                                ({"apply_private": False}, True)]:
            with self.subTest(config=config):
                self.assertFalse((await self.run_guard(
                    Reply(chain=IMAGE_LINK), {**EXPLICIT_RULES, **config}, private=private)).blocked)

    async def test_stop_event_option(self):
        event = await self.run_guard(Reply(chain=IMAGE_LINK), {**EXPLICIT_RULES, "stop_event": True})
        self.assertTrue(event.stopped)

    async def test_missing_quote_is_fetched(self):
        event = Event([Reply(sender_id=0)])
        event.bot.call_action.return_value = {"sender": {"user_id": 42}, "message": IMAGE_LINK}
        await plugin.ReplyGuard(None, EXPLICIT_RULES).guard_reply(event)
        self.assertTrue(event.blocked)
        event.bot.call_action.assert_awaited_once_with(
            action="get_msg", message_id=123, self_id="42")

    async def test_failed_fetch_and_unknown_author_allow(self):
        event = Event([Reply(sender_id=0, chain=IMAGE_LINK)])
        event.bot.call_action.side_effect = TimeoutError()
        await plugin.ReplyGuard(None, EXPLICIT_RULES).guard_reply(event)
        self.assertFalse(event.blocked)

    async def test_fetch_disabled_or_unsupported_platform(self):
        for config, platform in [({"fetch_missing_reply": False}, "aiocqhttp"),
                                 ({}, "telegram")]:
            event = await self.run_guard(Reply(sender_id=0), {**EXPLICIT_RULES, **config}, platform=platform)
            self.assertFalse(event.blocked)
            event.bot.call_action.assert_not_awaited()

    async def test_real_component_shapes_and_share(self):
        chain = [SimpleNamespace(type=SimpleNamespace(value="Image")),
                 SimpleNamespace(type="Share", url="https://example.com/post")]
        self.assertTrue((await self.run_guard(Reply(chain=chain))).blocked)

    async def test_multiple_quotes_and_user_reply_text_not_used(self):
        event = Event([Reply(sender_id="88", chain=IMAGE_LINK), Reply(chain=IMAGE_LINK)])
        await plugin.ReplyGuard(None, EXPLICIT_RULES).guard_reply(event)
        self.assertTrue(event.blocked)
        event = Event([Reply(chain=IMAGE_LINK[:1])])
        event.message_str = "https://example.com/user-link"
        await plugin.ReplyGuard(None, EXPLICIT_RULES).guard_reply(event)
        self.assertFalse(event.blocked)


if __name__ == "__main__":
    unittest.main()
