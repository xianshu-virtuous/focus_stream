"""聚焦状态的持久化。

落在 ``data/json_storage/<state_key>.json``（与 form_state / mode_switcher 同一套存储）：

.. code-block:: json

    {
      "focus_stream_id": "3f1c…",
      "chat_type": "private",
      "platform": "qq",
      "label": "XX的私聊",
      "manual_off": false,
      "focused_at": 1769000000.0,
      "switched_by": "qq:123456789",
      "updated_at": "2026-09-26 01:23:45"
    }

字段含义：

- ``focus_stream_id``：唯一允许回话的流；空串＝还没确定聚焦对象；
- ``manual_off``：主人用 ``/关闭聚焦`` 明确关掉了（此时不拦截任何流）；
- ``switched_by``：最后一次切换是谁触发的（``平台:用户ID`` 或 ``auto``），排查用。

**坏档不抛异常**：启动路径上不要因为一个手改坏的文件把插件卡死——读不出来、
字段类型不对，一律回落成「未聚焦」，最多留一条 warning。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.kernel.storage import json_store

logger = get_logger("focus_stream")

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def _as_str(value: Any) -> str:
    """任何东西都安全地转成字符串（None → ""）。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    """转 float，失败回落默认值。"""

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    """转 bool（字符串 ``"true"`` / ``"1"`` 也算真）。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


@dataclass
class FocusState:
    """当前聚焦状态（内存里的那一份，可与磁盘档互转）。"""

    focus_stream_id: str = ""
    chat_type: str = ""
    platform: str = ""
    label: str = ""
    manual_off: bool = False
    focused_at: float = field(default_factory=time.time)
    switched_by: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------ #
    # 基本属性
    # ------------------------------------------------------------------ #

    @property
    def is_focused(self) -> bool:
        """是否已经锁定了某个流。"""

        return bool(self.focus_stream_id)

    @property
    def focused_minutes(self) -> float:
        """距上次切换过了多少分钟（没时间戳时给 0）。"""

        if self.focused_at <= 0:
            return 0.0
        return max(0.0, (time.time() - self.focused_at) / 60.0)

    def matches(self, stream_id: str) -> bool:
        """这条流是不是当前聚焦流。"""

        return bool(stream_id) and stream_id == self.focus_stream_id

    def describe(self) -> str:
        """一句话描述当前状态（给日志与状态文本用）。"""

        if self.manual_off:
            return "已手动关闭（不拦截任何流）"
        if not self.is_focused:
            return "未聚焦（还没人切过）"
        where = self.label or self.focus_stream_id[:8]
        minutes = self.focused_minutes
        if minutes < 1:
            span = "刚刚"
        elif minutes < 60:
            span = f"{minutes:.0f} 分钟前"
        else:
            span = f"{minutes / 60:.1f} 小时前"
        return f"聚焦于「{where}」（{span}切换）"

    # ------------------------------------------------------------------ #
    # 序列化
    # ------------------------------------------------------------------ #

    def to_payload(self) -> dict[str, Any]:
        """转成可落盘的字典。"""

        return {
            "focus_stream_id": self.focus_stream_id,
            "chat_type": self.chat_type,
            "platform": self.platform,
            "label": self.label,
            "manual_off": self.manual_off,
            "focused_at": self.focused_at,
            "switched_by": self.switched_by,
            "updated_at": self.updated_at or datetime.now().strftime(_TIME_FMT),
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "FocusState":
        """从落盘字典还原（字段缺失/类型不对都不抛异常）。"""

        return cls(
            focus_stream_id=_as_str(data.get("focus_stream_id")).strip(),
            chat_type=_as_str(data.get("chat_type")).strip(),
            platform=_as_str(data.get("platform")).strip(),
            label=_as_str(data.get("label")).strip(),
            manual_off=_as_bool(data.get("manual_off")),
            focused_at=_as_float(data.get("focused_at")),
            switched_by=_as_str(data.get("switched_by")).strip(),
            updated_at=_as_str(data.get("updated_at")).strip(),
        )


async def load(state_key: str) -> FocusState:
    """读聚焦档；读不到 / 坏档都返回「未聚焦」。"""

    try:
        data: dict[str, Any] | None = await json_store.load(state_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"focus_stream: 读聚焦档失败（按未聚焦处理）：{exc}")
        return FocusState()

    if not isinstance(data, dict):
        return FocusState()

    state = FocusState.from_payload(data)
    if state.focus_stream_id:
        logger.info(f"focus_stream: 已读回聚焦档：{state.describe()}")
    return state


async def save(state_key: str, state: FocusState) -> None:
    """写聚焦档（写失败只警告，不影响内存里已经生效的状态）。"""

    state.updated_at = datetime.now().strftime(_TIME_FMT)
    try:
        await json_store.save(state_key, state.to_payload())
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"focus_stream: 写聚焦档失败：{exc}")


__all__ = ["FocusState", "load", "save"]
