"""聚焦闸门：非聚焦流的 Tick 直接跳过，不进入 Chatter。

拦在哪一环
----------
框架的流循环（``run_chat_stream``）每 Tick 会在**调用 Chatter 之前**发布
``on_chatter_step`` 事件，参数里带一个 ``continue`` 开关；订阅者把它置成
``False``，本轮就直接跳过，消息也不会灌进模型（框架顺手清掉积压的未读）。

选这个点而不是更早的 ``on_message_received``，是因为要满足主人的原话：
**「能看到但不回复」**。

- 消息在 ``on_message_received`` 阶段照常入库（历史里留着）；
- 只有「要不要让 Chatter 开口」这一步被拦下来。

于是等她切回那个流时，照样能接上之前的话题（上下文里都有），
只是当时一声没吭。反过来，如果在收消息那一步就 STOP，消息根本不入库，
就成了「连看都没看到」——那是另一种需求，本插件不做。
"""

from __future__ import annotations

from typing import Any

from src.core.components.base import BaseEventHandler
from src.kernel.event import EventDecision
from src.kernel.logger import get_logger

from . import focus

logger = get_logger("focus_stream")


class FocusGate(BaseEventHandler):
    """非聚焦流的 Tick 闸门。"""

    handler_name: str = "focus_stream_gate"
    handler_description: str = "把非聚焦流的 Tick 拦在 Chatter 之前（只看不回）"
    name: str = "focus_stream_gate"
    description: str = "聚焦闸门：只放行聚焦流"
    weight: int = 50
    intercept_message: bool = False
    init_subscribe: list[str] = ["on_chatter_step"]

    async def execute(
        self, event_name: str, params: dict[str, Any]
    ) -> tuple[EventDecision, dict[str, Any]]:
        """按当前聚焦状态决定本 Tick 是否放行。"""

        try:
            stream_id = str(params.get("stream_id") or "")
            if stream_id and await focus.should_silence(stream_id):
                params["continue"] = False
                if focus.settings().debug_log:
                    state = focus.state()
                    logger.info(
                        f"focus_stream: 静音 stream={stream_id[:8]}"
                        f"｜{state.describe()}"
                    )
        except Exception as exc:  # noqa: BLE001 - 拦住自己的异常，绝不拖垮流循环
            logger.error(f"focus_stream: 闸门判定失败，本轮按放行处理：{exc}", exc_info=True)

        return EventDecision.SUCCESS, params


__all__ = ["FocusGate"]
