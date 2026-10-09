"""``/打开聚焦``、``/关闭聚焦``、``/聚焦``：聚焦开关。

三种叫法（都在命令前缀之后，默认 ``/``）：

.. code-block:: text

    /打开聚焦            只回应当前这个流（别处只看不回）
    /关闭聚焦            关掉聚焦，恢复成平常那样
    /聚焦                看当前聚焦状态
    /聚焦 这里           把焦点切到当前流（等价于 /打开聚焦）
    /聚焦 开 / 关        等价于上面两个开关

**权限**：插件自带的**白名单**说了算（``[whitelist] users`` ＋ 框架
``permissions.owner_list``）。这里刻意不把 ``permission_level`` 抬到 OWNER——
框架的 OWNER 只认 owner_list，而白名单可能另有人在；两套混着用会让「名单里有人却
用不了」。所以命令本身不设框架门槛，改由 :func:`_check` 统一按插件白名单判。
不在名单里的人会收到一句「只认主人」，状态一个字都不改。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.app.plugin_system.types import PermissionLevel

from .. import focus, rules

logger = get_logger("focus_stream.command")

#: 只看状态的关键词
_STATUS_WORDS = ("", "状态", "status", "show", "now", "?", "？")

#: 打开 / 关闭 / 就地切 的关键词
_ON_WORDS = ("on", "开", "打开", "开启", "open")
_OFF_WORDS = ("off", "关", "关闭", "停", "close", "stop")
_HERE_WORDS = ("here", "这里", "这边", "这儿", "就地")

_DENIED_TEXT = "这个开关只认主人（白名单里的人才动得了），别人改不了她的注意力。"


class _FocusCommandBase(BaseCommand):
    """三个焦点命令的公共部分：回话、身份、白名单校验。"""

    #: 不设框架权限门槛（默认 USER），交给插件白名单判（见模块 docstring）
    permission_level: PermissionLevel = PermissionLevel.USER

    async def _reply(self, text: str) -> None:
        """给当前聊天流回消息。"""

        await send_text(text, stream_id=self.stream_id)

    def _identity(self) -> tuple[str, str]:
        """触发命令的人是谁（拿不到消息时返回空）。"""

        message = getattr(self, "_message", None)
        if message is None:
            return "", ""
        platform = str(getattr(message, "platform", "") or "")
        user_id = str(getattr(message, "sender_id", "") or "")
        return platform, user_id

    async def _check(self) -> bool:
        """白名单校验；不过关时顺手回一句，返回是否放行。"""

        platform, user_id = self._identity()
        if not platform and not user_id:
            # 非消息上下文（自检 / 内部调用）无从校验，放行
            return True
        if focus.allowed_identity(platform, user_id):
            return True

        logger.info(
            f"focus_stream: 命令被白名单挡下（{focus.identity_text(platform, user_id)}）"
        )
        await self._reply(_DENIED_TEXT)
        return False

    def _current_label(self) -> str:
        """当前流的名字（用于回话与状态）。"""

        message = getattr(self, "_message", None)
        if message is None:
            return "这边"
        return rules.message_label(message) or "这边"


# --------------------------------------------------------------------------- #
# /打开聚焦
# --------------------------------------------------------------------------- #


class FocusOnCommand(_FocusCommandBase):
    """打开聚焦：只回应当前这个流。"""

    name: str = "focus_on"
    description: str = "打开聚焦：只回应当前这个流（仅白名单可用）"

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """``/打开聚焦``、``/开启聚焦``、``/focus_on`` 都算。"""

        if not parts:
            return 0
        if parts[0].strip().lower() in (
            "focus_on",
            "focuson",
            "打开聚焦",
            "开启聚焦",
            "聚焦开",
        ):
            return 1
        return 0

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """把焦点切到当前流。"""

        if not await self._check():
            return False, "focus_denied"

        platform, user_id = self._identity()
        changed, text = await focus.focus_on_here(
            stream_id=self.stream_id,
            chat_type=self._chat_type(),
            platform=platform,
            label=self._current_label(),
            by=focus.identity_text(platform, user_id) if user_id else "command",
        )
        await self._reply(text)
        return True, "focus_on" if changed else "focus_same"

    def _chat_type(self) -> str:
        message = getattr(self, "_message", None)
        return str(getattr(message, "chat_type", "") or "") if message is not None else ""


# --------------------------------------------------------------------------- #
# /关闭聚焦
# --------------------------------------------------------------------------- #


class FocusOffCommand(_FocusCommandBase):
    """关闭聚焦：她恢复成平常那样。"""

    name: str = "focus_off"
    description: str = "关闭聚焦：她恢复成平常那样（仅白名单可用）"

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """``/关闭聚焦``、``/关掉聚焦``、``/focus_off`` 都算。"""

        if not parts:
            return 0
        if parts[0].strip().lower() in (
            "focus_off",
            "focusoff",
            "关闭聚焦",
            "关掉聚焦",
            "聚焦关",
        ):
            return 1
        return 0

    @cmd_route()
    async def handle_root(self) -> tuple[bool, str]:
        """关掉聚焦。"""

        if not await self._check():
            return False, "focus_denied"

        platform, user_id = self._identity()
        _changed, text = await focus.focus_off(
            by=focus.identity_text(platform, user_id) if user_id else "command",
            reason="主人用命令关闭",
        )
        await self._reply(text)
        return True, "focus_off"


# --------------------------------------------------------------------------- #
# /聚焦
# --------------------------------------------------------------------------- #


class FocusCommand(_FocusCommandBase):
    """查看或切换聚焦状态。"""

    name: str = "focus"
    description: str = "查看聚焦状态，或用 /聚焦 开|关|这里 切换（仅白名单可用）"

    @classmethod
    def match(cls, parts: list[str]) -> int:
        """``/聚焦``、``/焦点``、``/focus`` 都算。"""

        if not parts:
            return 0
        if parts[0].strip().lower() in ("focus", "聚焦", "焦点"):
            return 1
        return 0

    @cmd_route()
    async def handle_root(self, target: str = "") -> tuple[bool, str]:
        """按参数查看 / 切换。

        Args:
            target: 留空或 ``状态`` ＝ 只看状态；``开``/``on`` ＝ 打开；
                ``关``/``off`` ＝ 关闭；``这里``/``here`` ＝ 把焦点切到当前流。
        """

        if not await self._check():
            return False, "focus_denied"

        wanted = (target or "").strip().lower()

        if wanted in _STATUS_WORDS:
            await self._reply(focus.status_text())
            return True, "focus_status"

        platform, user_id = self._identity()
        by = focus.identity_text(platform, user_id) if user_id else "command"

        if wanted in _ON_WORDS or wanted in _HERE_WORDS:
            _changed, text = await focus.focus_on_here(
                stream_id=self.stream_id,
                chat_type=self._chat_type(),
                platform=platform,
                label=self._current_label(),
                by=by,
            )
            await self._reply(text)
            return True, "focus_on"

        if wanted in _OFF_WORDS:
            _changed, text = await focus.focus_off(by=by, reason="主人用命令关闭")
            await self._reply(text)
            return True, "focus_off"

        await self._reply(
            "用法：\n"
            "  /聚焦            看当前聚焦状态\n"
            "  /聚焦 这里       把焦点切到当前这个流\n"
            "  /打开聚焦        同上（快捷写法）\n"
            "  /关闭聚焦        关掉聚焦，恢复成平常那样"
        )
        return True, "focus_help"

    def _chat_type(self) -> str:
        message = getattr(self, "_message", None)
        return str(getattr(message, "chat_type", "") or "") if message is not None else ""


#: ``get_components`` 与自检脚本都用它列命令
ALL_COMMANDS: tuple[type[_FocusCommandBase], ...] = (
    FocusCommand,
    FocusOnCommand,
    FocusOffCommand,
)

__all__ = ["ALL_COMMANDS", "FocusCommand", "FocusOffCommand", "FocusOnCommand"]
