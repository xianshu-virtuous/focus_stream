"""命令回执怎么发：默认发一条「她看不到」的。

为什么需要这个
--------------
框架的 ``send_api`` 发送成功后**无条件**把消息写进聊天流历史
（``MessageSender._persist_sent_message_to_history``，框架只提供"发就记"这一条路）。
于是「/流聚焦 开」这类系统回执会留在她的上下文里：她知道有人在拨开关，
甚至可能把回执当成自己说过的话接下去。

做法
----
照 ``MessageSender.send_message`` 的流程走到「适配器已经发出去」为止，**不落历史**：

1. 从聊天流拿 platform / chat_type 与投递目标（群 ID 或私聊对方 ID）；
2. 推断目标 Adapter 并取实例；
3. 让 sender 帮忙补上 bot 身份，转成 ``MessageEnvelope``；
4. 直接 ``adapter._send_platform_message(envelope)`` —— 主人能看到，她的历史里没有。

公开 API 没有这个开关，所以这里用的是框架内部件。因此每一步都做了存在性检查，
**任何一步不符合预期就退回 ``send_api.send_text``**（回执变成可见，但至少发得出去），
并打一条 warning —— 回执发不出去比"她多看见一句话"更糟。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("focus_stream")


async def send_hidden(text: str, stream_id: str) -> bool:
    """发一条不进聊天流历史的文本消息。

    Args:
        text: 要发的文字
        stream_id: 目标聊天流

    Returns:
        是否发送成功（False＝调用方应退回普通发送）
    """

    if not text or not stream_id:
        return False

    try:
        from src.core.managers.adapter_manager import get_adapter_manager
        from src.core.managers.stream_manager import get_stream_manager
        from src.core.models.message import Message, MessageType
        from src.core.transport.message_send.message_sender import get_message_sender

        info = await get_stream_manager().get_stream_info(stream_id)
        if not isinstance(info, dict):
            logger.debug(f"focus_stream: 取不到流信息，隐身回执退回普通发送（{stream_id[:8]}）")
            return False

        platform = str(info.get("platform") or "")
        chat_type = str(info.get("chat_type") or "private")
        if not platform:
            return False

        # 投递目标：群聊给 group_id，私聊给对方的平台 user_id
        extra: dict[str, Any] = {}
        if chat_type == "group":
            group_id = info.get("group_id")
            if group_id:
                extra["target_group_id"] = str(group_id)
        else:
            person_id = info.get("person_id")
            if person_id:
                try:
                    from src.core.utils.user_query_helper import get_user_query_helper

                    person = await get_user_query_helper().person_crud.get_by(
                        person_id=person_id
                    )
                    if person and getattr(person, "user_id", None):
                        extra["target_user_id"] = str(person.user_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"focus_stream: 查私聊目标失败（{exc}）")

        message = Message(
            message_id=f"focus_reply_{uuid4().hex}",
            content=text,
            processed_plain_text=text,
            message_type=MessageType.TEXT,
            platform=platform,
            chat_type=chat_type,
            stream_id=stream_id,
            **extra,
        )

        sender = get_message_sender()
        infer = getattr(sender, "_infer_adapter_signature", None)
        adapter_signature = infer(message) if callable(infer) else None
        if not adapter_signature:
            logger.debug("focus_stream: 推断不出适配器，隐身回执退回普通发送")
            return False

        adapter = get_adapter_manager().get_adapter(adapter_signature)
        if adapter is None:
            logger.debug(f"focus_stream: 适配器未启动（{adapter_signature}）")
            return False

        apply_bot = getattr(sender, "_apply_bot_sender_info", None)
        if callable(apply_bot):
            await apply_bot(message, adapter)

        converter = getattr(sender, "_converter", None)
        if converter is None or not hasattr(converter, "message_to_envelope"):
            return False

        envelope = await converter.message_to_envelope(message)

        # ★ 只发不记：到这里为止都不碰流历史
        result = await adapter._send_platform_message(envelope)  # noqa: SLF001
        if result is None:
            return True

        ok = bool(getattr(result, "success", True))
        if not ok:
            logger.warning(
                f"focus_stream: 隐身回执被平台拒收：{getattr(result, 'error', '') or ''}"
            )
        return ok

    except Exception as exc:  # noqa: BLE001 - 兜底：退回可见发送
        logger.warning(f"focus_stream: 隐身回执发送异常（退回普通发送）：{exc}")
        return False


async def reply(text: str, stream_id: str, *, hidden: bool = True) -> bool:
    """给命令回一句话：默认走隐身通道，失败则退回普通发送。

    Args:
        text: 回执文字
        stream_id: 目标聊天流
        hidden: 是否尝试隐身发送（``False`` 时直接走 ``send_api``）

    Returns:
        是否发送成功
    """

    if hidden and await send_hidden(text, stream_id):
        return True

    if hidden:
        logger.info("focus_stream: 隐身回执没发出去，退回普通发送（她会看到这一条）")

    from src.app.plugin_system.api.send_api import send_text

    return await send_text(text, stream_id=stream_id)


__all__ = ["send_hidden", "reply"]
