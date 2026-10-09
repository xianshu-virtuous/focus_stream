"""聚焦观察者：谁在说话，要不要把焦点挪过去。

只读判断 + 一个副作用（切聚焦），**不拦任何消息**：谁的消息都照常入库，
这里纯粹是「主人的注意力在哪一边」的跟踪器。

四道门，按顺序过：

1. **是消息吗**：拿不到 ``message`` / ``stream_id`` / ``sender_id`` 就放过去不管；
2. **插件开着吗**：``[plugin] enabled = false`` 时不介入；
3. **是白名单里的人吗**：只有主人（或名单里的人）能把焦点挪走 —— 免得群里
   谁喊两句就把她的注意力抢过去；
4. **够不够格切**：私聊一般直接切（``private_auto``）；群里默认要「呼唤」
   （``group_requires_call``）——@ 她、或者话里出现她的昵称/别名/自定义称呼。

白名单之外的人想在群里 @ 她？不切焦点，但那条消息照样会进流里；
等主人自己把焦点切回那个群，她才看得到完整的前因后果。
"""

from __future__ import annotations

from typing import Any

from src.core.components.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from . import focus, rules

logger = get_logger("focus_stream")


def _text_of(message: Any) -> str:
    """取消息的纯文本（优先用处理后的文本）。"""

    for attr in ("processed_plain_text", "plain_text"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return str(content).strip()


def _self_id(message: Any) -> str:
    """她自己在这个平台上的 ID（判断有没有被 @ 用）。"""

    raw = getattr(message, "raw_data", None)
    if isinstance(raw, dict):
        value = raw.get("self_id") or raw.get("selfId")
        if value:
            return str(value)

    # 兜底：从已经建好的聊天流里取 bot_id
    stream_id = str(getattr(message, "stream_id", "") or "")
    if not stream_id:
        return ""
    try:
        from src.core.managers.stream_manager import get_stream_manager

        stream = get_stream_manager()._streams.get(stream_id)
        bot_id = getattr(stream, "bot_id", "") if stream is not None else ""
        return str(bot_id or "")
    except Exception:  # noqa: BLE001
        return ""


def _label_of(message: Any, chat_type: str) -> str:
    """给这条消息所在的流起个名字。"""

    return rules.message_label(message) or rules.stream_label(chat_type)


class FocusWatcher(BaseEventHandler):
    """跟踪「主人在哪一边说话」，据此切换聚焦。"""

    handler_name: str = "focus_stream_watcher"
    handler_description: str = "认出主人与呼唤，把聚焦切到主人正在说话的那个流"
    name: str = "focus_stream_watcher"
    description: str = "聚焦观察者：识别主人与呼唤"
    #: 正权重＝比默认分发器（priority 0）先跑：消息刚落地就把焦点摆好，
    #: 免得这一拍刚启动的流循环抢在切换之前 Tick 一次。只读判断，不拦消息。
    weight: int = 5
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_message_received"]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """观察一条消息，必要时切换聚焦。"""

        try:
            await self._handle(params)
        except Exception as exc:  # noqa: BLE001 - 观察者绝不阻断消息主流程
            logger.error(f"focus_stream: 观察者出错，本条按不切换处理：{exc}", exc_info=True)

        return EventDecision.SUCCESS, params

    # ------------------------------------------------------------------ #
    # 主逻辑
    # ------------------------------------------------------------------ #

    async def _handle(self, params: dict[str, Any]) -> None:
        message = params.get("message")
        if message is None:
            return

        settings = focus.settings()
        if not settings.enabled:
            return

        stream_id = str(getattr(message, "stream_id", "") or "")
        sender_id = str(getattr(message, "sender_id", "") or "")
        platform = str(getattr(message, "platform", "") or "")
        if not stream_id or not sender_id:
            return

        # ── 第三道门：白名单 ──────────────────────────────────────────
        if not focus.allowed_identity(platform, sender_id):
            if settings.debug_log:
                logger.debug(
                    f"focus_stream: {sender_id} 不在白名单，忽略其切换请求"
                )
            return

        chat_type = str(getattr(message, "chat_type", "") or "").strip().lower()
        by = focus.identity_text(platform, sender_id)
        label = _label_of(message, chat_type)

        # ── 私聊：默认一说话就切过去 ──────────────────────────────────
        if chat_type == "private":
            if not settings.private_auto:
                if settings.debug_log:
                    logger.debug("focus_stream: 私聊自动聚焦关着，手动 /聚焦 才切")
                return
            changed, text = await focus.focus_on(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                label=label,
                by=by,
            )
            if changed:
                logger.info(f"focus_stream: 主人在私聊说话，聚焦切到「{label}」")
            return

        # ── 群 / 讨论组：默认要呼唤 ───────────────────────────────────
        if not settings.group_requires_call:
            changed, _ = await focus.focus_on(
                stream_id=stream_id,
                chat_type=chat_type,
                platform=platform,
                label=label,
                by=by,
            )
            if changed:
                logger.info(f"focus_stream: 主人在「{label}」说话，聚焦切过去")
            return

        extra = getattr(message, "extra", None)
        at_users = extra.get("at_users") if isinstance(extra, dict) else None
        reason = rules.match_call(
            text=_text_of(message),
            at_users=at_users,
            self_id=_self_id(message),
            names=rules.bot_names(settings.call_words),
            settings=settings,
        )
        if not reason:
            if settings.debug_log:
                logger.debug(
                    f"focus_stream: 「{label}」里没人叫她，保持当前聚焦不变"
                )
            return

        changed, _ = await focus.focus_on(
            stream_id=stream_id,
            chat_type=chat_type,
            platform=platform,
            label=label,
            by=by,
        )
        if changed:
            logger.info(f"focus_stream: 主人在「{label}」叫她（{reason}），聚焦切过去")


__all__ = ["FocusWatcher"]
