"""聚焦插件的纯逻辑：设置对象、白名单判定、呼唤识别、状态文案。

这一层**不碰框架运行时**（除了读一次核心配置拿昵称与主人列表），所有函数都能在
离线自检里直接喂造出来的数据跑，这是本插件自检脚本的主力测试面。

三块判定：

1. **谁有资格**（:func:`is_allowed`）——白名单里的用户，加上（可配的）框架
   ``permissions.owner_list``。名单为空又不信 owner_list 时，按配置决定是
   「整体关掉自动聚焦」还是「谁都能抢」（默认前者，安全）。
2. **算不算呼唤**（:func:`match_call`）——@ 她，或者话里出现她的昵称/别名/自定义
   称呼；可配成「只认 @」。
3. **这条流该不该静音**（:func:`should_silence`）——只有聚焦流放行，其余全静音；
   手动关闭时不拦任何人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .state import FocusState

#: ``Setting`` 里「关掉／未配置」的哨兵值说明：见各字段默认值。
_DEFAULT_CALL_FALLBACK = ""


# --------------------------------------------------------------------------- #
# 设置对象
# --------------------------------------------------------------------------- #


@dataclass
class Settings:
    """运行时设置（由插件配置翻译而来，配置改了要重新 configure）。"""

    # [plugin]
    enabled: bool = True
    state_key: str = "focus_stream"
    debug_log: bool = False
    restore_after_restart: bool = True

    # [whitelist]
    whitelist_users: list[str] = field(default_factory=list)
    use_owner_list: bool = True
    fallback_allow_all: bool = False

    # [focus]
    private_auto: bool = True
    group_requires_call: bool = True
    call_requires_at: bool = False
    call_words: list[str] = field(default_factory=list)
    silence_when_unfocused: bool = True
    stale_hours: float = 0.0

    # [inject]
    inject_enabled: bool = True
    inject_buckets: list[str] = field(default_factory=lambda: ["actor"])
    inject_name: str = "focus_stream_now"
    inject_text: str = ""
    inject_guard: str = ""

    # [tools]
    tools_enabled: bool = True

    # [reply]
    hidden_reply: bool = True

    def whitelist_entries(self) -> list[str]:
        """本插件自带的名单条目（已去空、去重，保持顺序）。"""

        seen: set[str] = set()
        entries: list[str] = []
        for raw in self.whitelist_users:
            item = str(raw or "").strip()
            if not item or item in seen:
                continue
            seen.add(item)
            entries.append(item)
        return entries

    def has_whitelist(self, owner_entries: Iterable[str] = ()) -> bool:
        """有没有可用的名单（自带名单 或 框架主人列表）。"""

        if self.whitelist_entries():
            return True
        if self.use_owner_list and any(str(x).strip() for x in owner_entries):
            return True
        return False


def build_settings(config: Any) -> Settings:
    """把插件配置对象翻译成 :class:`Settings`。"""

    plugin = config.plugin
    wl = config.whitelist
    focus = config.focus
    inject = config.inject
    tools = config.tools

    return Settings(
        enabled=bool(plugin.enabled),
        state_key=str(plugin.state_key or "focus_stream").strip() or "focus_stream",
        debug_log=bool(plugin.debug_log),
        restore_after_restart=bool(plugin.restore_after_restart),
        whitelist_users=[str(x) for x in wl.users],
        use_owner_list=bool(wl.use_owner_list),
        fallback_allow_all=bool(wl.fallback_allow_all),
        private_auto=bool(focus.private_auto),
        group_requires_call=bool(focus.group_requires_call),
        call_requires_at=bool(focus.call_requires_at),
        call_words=[str(x).strip() for x in focus.call_words if str(x).strip()],
        silence_when_unfocused=bool(focus.silence_when_unfocused),
        stale_hours=max(0.0, float(focus.stale_hours)),
        inject_enabled=bool(inject.enabled),
        inject_buckets=[str(x).strip() for x in inject.buckets if str(x).strip()]
        or ["actor"],
        inject_name=str(inject.name or "focus_stream_now").strip() or "focus_stream_now",
        inject_text=str(inject.text or ""),
        inject_guard=str(inject.guard or ""),
        tools_enabled=bool(tools.enabled),
        hidden_reply=bool(config.reply.hidden),
    )


# --------------------------------------------------------------------------- #
# 身份与白名单
# --------------------------------------------------------------------------- #


def identity_key(platform: str, user_id: str) -> str:
    """拼出 ``平台:用户ID``（平台为空时只留 ID）。"""

    platform = str(platform or "").strip()
    user_id = str(user_id or "").strip()
    if not user_id:
        return ""
    if not platform:
        return user_id
    return f"{platform}:{user_id}"


def split_identity(raw: str) -> tuple[str, str]:
    """拆开 ``平台:用户ID``；没有冒号时平台为空。"""

    text = str(raw or "").strip()
    if not text:
        return "", ""
    if ":" in text:
        platform, _, user_id = text.partition(":")
        return platform.strip().lower(), user_id.strip()
    return "", text


def entry_matches(entry: str, platform: str, user_id: str) -> bool:
    """单个名单条目是否命中这位发送者。

    - ``qq:123456`` → 平台与 ID 都要一致（平台不区分大小写）；
    - ``123456`` → 只看 ID，对所有平台生效。
    """

    want_platform, want_user = split_identity(entry)
    if not want_user:
        return False
    if want_user != str(user_id or "").strip():
        return False
    if want_platform and want_platform != str(platform or "").strip().lower():
        return False
    return True


def is_allowed(
    settings: Settings,
    *,
    platform: str,
    user_id: str,
    owner_entries: Iterable[str] = (),
) -> bool:
    """这位发送者有没有资格切换聚焦。"""

    if not settings.enabled:
        return False

    entries = settings.whitelist_entries()
    if settings.use_owner_list:
        entries = entries + [str(x) for x in owner_entries if str(x).strip()]

    if entries:
        return any(entry_matches(entry, platform, user_id) for entry in entries)

    # 名单为空：按配置决定是「关掉自动切换」还是「谁都能切」
    return bool(settings.fallback_allow_all)


# --------------------------------------------------------------------------- #
# 呼唤识别
# --------------------------------------------------------------------------- #


def mentions_bot(at_users: Any, self_id: str, text: str) -> bool:
    """消息里有没有 @ 到她自己。

    ``at_users`` 是 ``message.extra["at_users"]``（形如 ``[{"user_id": "…"}]``）；
    拿不到自己的 ID 时退一步看文本里的 ``@<名字:ID>`` 占位符。
    """

    my_id = str(self_id or "").strip()
    if not my_id:
        return False

    if isinstance(at_users, list):
        for item in at_users:
            if isinstance(item, dict) and str(item.get("user_id", "")).strip() == my_id:
                return True

    return f":{my_id}>" in str(text or "")


def match_call(
    *,
    text: str,
    at_users: Any,
    self_id: str,
    names: Iterable[str],
    settings: Settings,
) -> str:
    """这条消息算不算在「呼唤」她。

    Returns:
        命中的方式（``"at"`` / ``"name:小爱"``），没命中返回空串。
    """

    if mentions_bot(at_users, self_id, text):
        return "at"

    if settings.call_requires_at:
        return ""

    lowered = str(text or "").lower()
    if not lowered:
        return ""

    for name in names:
        wanted = str(name or "").strip().lower()
        if wanted and wanted in lowered:
            return f"name:{name}"
    return _DEFAULT_CALL_FALLBACK


def bot_names(extra_words: Iterable[str] = ()) -> list[str]:
    """她的所有称呼：框架昵称 + 别名 + 自定义呼唤词（去重、去空）。"""

    names: list[str] = []
    seen: set[str] = set()

    try:
        from src.core.config import get_core_config

        personality = get_core_config().personality
        candidates = [str(personality.nickname or "")] + [
            str(x) for x in (personality.alias_names or [])
        ]
    except Exception:  # noqa: BLE001 - 配置没起来时不该炸
        candidates = []

    candidates.extend(str(x) for x in extra_words)

    for raw in candidates:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


# --------------------------------------------------------------------------- #
# 展示用
# --------------------------------------------------------------------------- #


def stream_label(chat_type: str, *, group_name: str = "", user_name: str = "") -> str:
    """给一个流起个人类看得懂的名字（日志、状态文本、注入都用它）。"""

    kind = str(chat_type or "").strip().lower()
    group = str(group_name or "").strip()
    user = str(user_name or "").strip()

    if kind == "group" and group:
        return group
    if kind == "private":
        return f"{user}的私聊" if user else "私聊"
    if group:
        return group
    if user:
        return f"{user}的私聊"
    return "某个流"


def message_label(message: Any) -> str:
    """从一条消息里推出它所在流的名字（命令与观察者共用）。"""

    if message is None:
        return ""

    extra = getattr(message, "extra", None)
    group_name = ""
    if isinstance(extra, dict):
        group_name = str(extra.get("group_name") or extra.get("group_id") or "")

    user_name = str(
        getattr(message, "sender_cardname", "") or getattr(message, "sender_name", "") or ""
    )
    chat_type = str(getattr(message, "chat_type", "") or "")
    return stream_label(chat_type, group_name=group_name, user_name=user_name)


def should_silence(state: FocusState, settings: Settings, stream_id: str) -> bool:
    """这条流此刻该不该被静音（＝不放行给 Chatter）。"""

    if not settings.enabled:
        return False
    if state.manual_off:
        return False
    if state.is_focused:
        return not state.matches(stream_id)
    return bool(settings.silence_when_unfocused)


def is_stale(state: FocusState, settings: Settings) -> bool:
    """聚焦挂了太久（超过 ``stale_hours``）该自动解除。"""

    if settings.stale_hours <= 0 or not state.is_focused:
        return False
    return state.focused_minutes >= settings.stale_hours * 60.0


__all__ = [
    "Settings",
    "build_settings",
    "identity_key",
    "split_identity",
    "entry_matches",
    "is_allowed",
    "mentions_bot",
    "match_call",
    "bot_names",
    "stream_label",
    "message_label",
    "should_silence",
    "is_stale",
]
