"""聚焦运行时：内存里的那一份状态 + 所有切换动作。

分工：:mod:`rules` 是纯逻辑，:mod:`state` 只管落盘，本模块把它们串起来，
给事件处理器、命令和工具提供同一套动作与文案。**只在这里改状态**，
别处一律调这里的函数，免得出现「内存说聚焦在 A、存档说 B」的分叉。

三个动作：

- :func:`focus_on` —— 把焦点挪到某个流（自动聚焦、命令、工具都走它）；
- :func:`focus_off` —— 手动关闭（``/关闭聚焦``），此时不拦截任何流；
- :func:`maybe_release_stale` —— 聚焦挂太久（``stale_hours``）自动放开。

关闭（``manual_off``）与未聚焦（``focus_stream_id`` 为空）是两件事：
前者是「主人明确说别管了」，后者是「还没人切过」。区分开，状态文案才说得清。
"""

from __future__ import annotations

import time
from typing import Any

from src.app.plugin_system.api.log_api import get_logger

from . import inject, rules
from .state import FocusState, load as load_state_file, save as save_state_file

logger = get_logger("focus_stream")

#: 运行时设置（插件加载时注入）
_settings: rules.Settings = rules.Settings()

#: 当前状态（内存里的唯一真相）
_state: FocusState = FocusState()

#: 是否已经从存档里读过一次
_loaded = False

#: 是否由本插件主动写回内存状态（自检里用来判断是否真的落盘过）
_last_persist_at: float = 0.0


# --------------------------------------------------------------------------- #
# 设置与状态
# --------------------------------------------------------------------------- #


def configure(settings: rules.Settings) -> None:
    """注入设置（插件加载 / 配置变更时调用）。"""

    global _settings
    _settings = settings


def settings() -> rules.Settings:
    """取当前设置。"""

    return _settings


def state() -> FocusState:
    """取当前聚焦状态。"""

    return _state


def owner_entries() -> list[str]:
    """框架 ``permissions.owner_list`` 里的主人（``平台:用户ID``）。"""

    try:
        from src.core.config import get_core_config

        return [str(x) for x in (get_core_config().permissions.owner_list or [])]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"focus_stream: 读 owner_list 失败：{exc}")
        return []


def reset() -> None:
    """把内存状态与「已读档」标志清掉（卸载 / 自检用）。"""

    global _state, _loaded, _last_persist_at
    _state = FocusState()
    _loaded = False
    _last_persist_at = 0.0


async def ensure_loaded() -> FocusState:
    """懒加载：第一次用到时从存档读一次。"""

    global _state, _loaded
    if _loaded:
        return _state

    _loaded = True
    if not _settings.restore_after_restart:
        return _state

    loaded = await load_state_file(_settings.state_key)
    if loaded.focus_stream_id or loaded.manual_off:
        _state = loaded
        _after_change()
    return _state


async def _persist() -> None:
    """落盘（写失败不影响内存状态）。"""

    global _last_persist_at
    _last_persist_at = time.time()
    await save_state_file(_settings.state_key, _state)


def _after_change() -> None:
    """状态变了之后同步一次上下文注入（失败不影响切换本身）。"""

    try:
        inject.sync(_state, _settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"focus_stream: 同步状态注入失败：{exc}")


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #


async def focus_on(
    *,
    stream_id: str,
    chat_type: str = "",
    platform: str = "",
    label: str = "",
    by: str = "auto",
) -> tuple[bool, str]:
    """把焦点挪到某个流。

    Args:
        stream_id: 目标流 ID（空串＝不切，直接返回失败）
        chat_type: 聊型（private / group / discuss）
        platform: 平台名
        label: 人类可读的名字（群名 / 对方的私聊）
        by: 触发者（``平台:用户ID`` 或 ``auto``）

    Returns:
        (是否发生了变化, 给调用方的一句话)
    """

    global _state

    stream_id = str(stream_id or "").strip()
    if not stream_id:
        return False, "没拿到流 ID，无法聚焦"

    await ensure_loaded()

    where = label or stream_id[:8]
    if _state.focus_stream_id == stream_id and not _state.manual_off:
        # 已经在聚焦这个流：只刷新一下活跃时间，不当作「切换」
        _state.focused_at = time.time()
        _state.switched_by = by
        await _persist()
        return False, f"已经在聚焦「{where}」了"

    _state.focus_stream_id = stream_id
    _state.chat_type = str(chat_type or "")
    _state.platform = str(platform or "")
    _state.label = str(label or "")
    _state.manual_off = False
    _state.focused_at = time.time()
    _state.switched_by = by
    await _persist()

    logger.info(f"focus_stream: 聚焦切到「{where}」（由 {by} 触发）")
    _after_change()
    return True, f"聚焦已切到「{where}」"


async def focus_off(*, by: str = "auto", reason: str = "") -> tuple[bool, str]:
    """关闭聚焦（不再拦截任何流）。"""

    global _state

    await ensure_loaded()

    already = _state.manual_off
    _state.manual_off = True
    _state.focus_stream_id = ""
    _state.label = ""
    _state.chat_type = ""
    _state.platform = ""
    _state.focused_at = time.time()
    _state.switched_by = by
    await _persist()
    _after_change()

    if already:
        return False, "聚焦本来就是关着的"
    detail = f"（{reason}）" if reason else ""
    logger.info(f"focus_stream: 聚焦已关闭{detail}（由 {by}）")
    return True, "聚焦已关闭，她恢复成平常那样，哪儿说话都接"


async def focus_on_here(
    *,
    stream_id: str,
    chat_type: str = "",
    platform: str = "",
    label: str = "",
    by: str = "command",
) -> tuple[bool, str]:
    """把焦点切到「当前这个流」（命令用）。

    与 :func:`focus_on` 的区别只在语义：这里明确是「就在这儿聊」，所以
    即使当前处于手动关闭状态也会重新打开。
    """

    global _state

    await ensure_loaded()
    _state.manual_off = False
    changed, text = await focus_on(
        stream_id=stream_id,
        chat_type=chat_type,
        platform=platform,
        label=label,
        by=by,
    )
    if changed:
        return True, f"好，她只看这边了——{text}。别处的消息她照样看得到，只是不接话。"
    return False, f"她本来就在这边（{text}）。"


async def maybe_release_stale() -> bool:
    """聚焦挂太久就自动放开（返回是否刚放开）。"""

    await ensure_loaded()
    if not rules.is_stale(_state, _settings):
        return False

    minutes = _state.focused_minutes
    logger.info(
        f"focus_stream: 聚焦已持续 {minutes:.0f} 分钟，超过 stale_hours="
        f"{_settings.stale_hours:g}，自动放开"
    )
    await focus_off(by="stale", reason=f"挂了 {minutes / 60:.1f} 小时")
    return True


# --------------------------------------------------------------------------- #
# 判定（给事件处理器用）
# --------------------------------------------------------------------------- #


async def should_silence(stream_id: str) -> bool:
    """这条流此刻该不该静音（＝跳过 Chatter，不开口）。"""

    await ensure_loaded()
    if await maybe_release_stale():
        return False
    return rules.should_silence(_state, _settings, stream_id)


def allowed_identity(platform: str, user_id: str) -> bool:
    """这位发送者能不能切换聚焦。"""

    if not rules.is_allowed(
        _settings,
        platform=platform,
        user_id=user_id,
        owner_entries=owner_entries(),
    ):
        return False
    return True


def identity_text(platform: str, user_id: str) -> str:
    """把发送者身份拼成 ``平台:用户ID``（日志与 ``switched_by`` 用）。"""

    return rules.identity_key(platform, user_id) or "unknown"


# --------------------------------------------------------------------------- #
# 文案
# --------------------------------------------------------------------------- #


def describe_line() -> str:
    """当前状态的一句话（日志用）。"""

    return _state.describe()


def status_text() -> str:
    """``/聚焦`` 与工具返回的状态文本。"""

    s = _settings
    st = _state

    if not s.enabled:
        return "【聚焦】插件在配置里是关着的（enabled = false）。"

    if st.manual_off:
        head = "【聚焦】已关闭——她现在哪儿说话都接。"
    elif st.is_focused:
        where = st.label or st.focus_stream_id[:8]
        minutes = st.focused_minutes
        span = (
            "刚刚"
            if minutes < 1
            else f"{minutes:.0f} 分钟"
            if minutes < 60
            else f"{minutes / 60:.1f} 小时"
        )
        head = f"【聚焦】开着——只回「{where}」，已持续 {span}。"
    elif s.silence_when_unfocused:
        head = "【聚焦】开着，但还没确定是哪一边——现在谁也不回，等你先开口或呼唤。"
    else:
        head = "【聚焦】开着，还没确定是哪一边——暂时按平常那样回。"

    if s.private_auto:
        rule_private = "私聊：你一说话就自动切过去"
    else:
        rule_private = "私聊：不自动切（要手动 /聚焦 这里）"
    if s.group_requires_call:
        how = "只认 @" if s.call_requires_at else "@ 她、或者叫她的名字/称呼"
        rule_group = f"群聊：要呼唤（{how}）才切过去"
    else:
        rule_group = "群聊：你一说话就切过去"

    entries = s.whitelist_entries()
    if entries and s.use_owner_list:
        who = f"白名单 {len(entries)} 人 ＋ 框架主人列表"
    elif entries:
        who = f"白名单 {len(entries)} 人"
    elif s.use_owner_list:
        who = "只用框架主人列表"
    else:
        who = "名单为空：自动切换已停用"

    lines = [
        head,
        f"规则：{rule_private}；{rule_group}",
        f"谁能切：{who}",
        "开关：/流聚焦 开启、/流聚焦 关闭、/流聚焦（看状态）、/聚焦 这里（就地切过来）",
    ]
    return "\n".join(lines)


def apply_config_text(settings: rules.Settings) -> str:
    """加载时给日志看的一行摘要。"""

    return (
        f"聚焦插件已加载｜私聊自动={'开' if settings.private_auto else '关'}"
        f"｜群里需呼唤={'开' if settings.group_requires_call else '关'}"
        f"｜白名单={len(settings.whitelist_entries())}人"
        f"｜状态注入={'开' if settings.inject_enabled else '关'}"
        f"｜回执隐身={'开' if settings.hidden_reply else '关'}"
    )


__all__ = [
    "configure",
    "settings",
    "state",
    "owner_entries",
    "reset",
    "ensure_loaded",
    "focus_on",
    "focus_off",
    "focus_on_here",
    "maybe_release_stale",
    "should_silence",
    "allowed_identity",
    "identity_text",
    "describe_line",
    "status_text",
    "apply_config_text",
]
