"""Prevent default AI replies to quotes of selected bot messages."""

import asyncio
import re
from collections.abc import Mapping

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Reply
from astrbot.api.star import Context, Star, register


def component_data(component):
    """Accept both AstrBot components and OneBot array segments."""
    if isinstance(component, Mapping):
        kind = str(component.get("type", "")).lower()
        data = component.get("data", {})
        return kind, data if isinstance(data, Mapping) else {}
    kind = getattr(component, "type", "")
    return str(getattr(kind, "value", kind)).lower(), vars(component)


def known_id(value):
    return value is not None and str(value).strip() not in {"", "0"}


COMPONENT_LABELS = {
    "image": "图片", "record": "语音", "video": "视频", "file": "文件",
    "at": "艾特", "atall": "艾特全体", "face": "表情", "reply": "引用",
    "forward": "转发", "node": "转发", "nodes": "转发", "json": "卡片",
}


def message_format(chain, text):
    """Represent original components in order; never include media download URLs."""
    parts = []
    for component in chain or []:
        kind, data = component_data(component)
        if kind in {"plain", "text"}:
            parts.append(str(data.get("text", "")))
        elif kind == "share":
            parts.append("[链接]" + str(data.get("url", "")))
        else:
            parts.append("[" + COMPONENT_LABELS.get(kind, kind) + "]")
    return "".join(parts) if chain else (text or "")


@register("astrbot_plugin_reply_guard", "千雪琉音", "屏蔽引用机器人指定消息触发的默认 AI 回复", "1.2.0")
class ReplyGuard(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._pattern_source = None
        self._patterns = []

    def matches_custom(self, chain, text):
        sources = self.config.get("format_patterns", [])
        if not isinstance(sources, list):
            sources = []
        if sources != self._pattern_source:
            self._pattern_source = list(sources)
            self._patterns = []
            for source in sources:
                if not isinstance(source, str) or not source.strip():
                    continue
                try:
                    self._patterns.append(re.compile(source))
                except re.error as exc:
                    logger.warning("[ReplyGuard] 忽略无效格式正则 %r: %s", source, exc)
        formatted = message_format(chain, text)
        return any(pattern.search(formatted) for pattern in self._patterns)

    async def resolve_reply(self, event, reply):
        sender = reply.sender_id
        chain = reply.chain
        text = reply.message_str or ""
        # OneBot normally populates Reply itself; retry only missing information.
        incomplete = not known_id(sender) or not chain
        if incomplete and self.config.get("fetch_missing_reply", True):
            bot = getattr(event, "bot", None)
            if event.get_platform_name() == "aiocqhttp" and bot is not None:
                try:
                    data = await asyncio.wait_for(
                        bot.call_action(
                            action="get_msg", message_id=int(reply.id),
                            self_id=event.get_self_id(),
                        ),
                        timeout=5,
                    )
                    if isinstance(data, Mapping):
                        sender_info = data.get("sender", {})
                        if isinstance(sender_info, Mapping):
                            sender = sender_info.get("user_id", sender)
                        message = data.get("message")
                        if isinstance(message, list):
                            chain = message
                            text = ""  # Use the retrieved original message only.
                except Exception as exc:
                    logger.debug("[ReplyGuard] 补查引用消息失败 (%s)", type(exc).__name__)
        return sender, chain, text

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def guard_reply(self, event: AstrMessageEvent):
        """Set only the default LLM suppression flag, keeping commands available."""
        if not self.config.get("enabled", True):
            return
        if event.is_private_chat() and not self.config.get("apply_private", True):
            return
        if not event.is_private_chat() and not self.config.get("apply_group", True):
            return
        self_id = event.get_self_id()
        if not known_id(self_id):
            return
        # No configured rules means no interception and no original-message lookup.
        if not self.config.get("format_patterns"):
            return
        for reply in event.get_messages():
            if not isinstance(reply, Reply):
                continue
            # A known quote of another user never requires an API lookup.
            if known_id(reply.sender_id) and str(reply.sender_id) != str(self_id):
                continue
            sender, chain, text = await self.resolve_reply(event, reply)
            matched = self.matches_custom(chain, text)
            if known_id(sender) and str(sender) == str(self_id) and matched:
                # AstrBot's API is inverted: True means prohibit the default request.
                event.should_call_llm(True)
                if self.config.get("stop_event", False):
                    event.stop_event()
                logger.debug("[ReplyGuard] 已屏蔽引用消息的 AI 触发，message_id=%s", reply.id)
                return
