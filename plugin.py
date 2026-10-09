"""focus_stream 插件入口：只回主人当前所在的那一个流。

装上之后会发生什么
------------------
她在很多个流（私聊 / 群）里同时醒着。这个插件加一层焦点：

- **私聊**：主人一说话，焦点自动挪到那个私聊（``private_auto``）——于是她在私信
  里就只聊私信，群里再热闹也不插话；
- **群聊**：要「呼唤」才挪过去（``group_requires_call``）——@ 她、或者话里叫到
  她的昵称/别名/自定义称呼。所以「我回群里叫她一声，她就回来了」；
- **别的流**：消息照常入库（她看得到、回头能接上），但那一 Tick 不进入 Chatter，
  一声不吭（见 :mod:`gate` 的说明）；
- **谁能切**：只有白名单里的人（``[whitelist] users`` ＋ 框架 ``owner_list``），
  免得群里谁喊两句就把她的注意力抢走；
- **临时改主意**：``/关闭聚焦`` 恢复成平常那样，``/打开聚焦`` 再锁回当前流——
  这两条命令走命令分发，不走 Chatter，所以**在被静音的那个流里也能用**。

加载/卸载做三件事：注入设置 → 读回上次的聚焦档（可关）→ 把状态写进上下文注入；
卸载时清掉注入、内存状态归零（存档保留，下次装回来还在）。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin

from . import focus, inject, rules
from .commands import ALL_COMMANDS
from .config import FocusStreamConfig
from .gate import FocusGate
from .tools import GetFocusStateTool
from .watcher import FocusWatcher

logger = get_logger("focus_stream")


@register_plugin
class FocusStreamPlugin(BasePlugin):
    """聚焦插件：只回应主人当前所在的流。"""

    plugin_name: str = "focus_stream"
    plugin_description: str = (
        "只回主人当前所在的那一个流：私聊里主人一说话就自动聚焦私聊（于是群消息只看不回），"
        "群里则要 @ 她或叫她的名字才把焦点叫回去；其余流的消息照常入库（回头看得到、接得上），"
        "但那一轮不开口，也就不会白烧 token。"
        "只有白名单里的人（插件名单 ＋ 框架 owner_list）能切换焦点，"
        "随时可用 /打开聚焦、/关闭聚焦、/聚焦 手动接管；"
        "聚焦状态可持久化、可注入上下文让她自己知道，并附带只读的 get_focus_state 工具"
    )
    plugin_version: str = "1.0.0"
    configs: list[type] = [FocusStreamConfig]

    def get_components(self) -> list[type]:
        """插件被关掉时不注册任何组件（等于整体下线）。"""

        config = self.config
        if isinstance(config, FocusStreamConfig) and not config.plugin.enabled:
            return []

        components: list[type] = [FocusGate, FocusWatcher]
        components.extend(ALL_COMMANDS)

        if not isinstance(config, FocusStreamConfig) or config.tools.enabled:
            components.append(GetFocusStateTool)
        return components

    async def on_plugin_loaded(self) -> None:
        """注入设置、读回聚焦档、同步一次状态注入。"""

        config = self.config
        if not isinstance(config, FocusStreamConfig):
            logger.error("focus_stream: 配置加载失败，插件不生效")
            return

        settings = rules.build_settings(config)
        focus.configure(settings)

        if not settings.enabled:
            logger.info("focus_stream: 配置里已禁用，跳过初始化")
            return

        state = await focus.ensure_loaded()

        if not settings.has_whitelist(focus.owner_entries()) and not settings.fallback_allow_all:
            logger.warning(
                "focus_stream: 白名单与框架 owner_list 都是空的——自动切换不会生效"
                "（只剩命令能切，且谁都用不了）。请在 "
                "config/plugins/focus_stream/config.toml 的 [whitelist] users 里"
                "填上主人的 ID，格式 qq:123456789"
            )

        logger.info(f"{focus.apply_config_text(settings)}｜当前：{state.describe()}")

    async def on_plugin_unloaded(self) -> None:
        """清掉状态注入，内存状态归零（存档保留，装回来还在）。"""

        settings = focus.settings()
        try:
            inject.clear(settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"focus_stream: 清理状态注入失败：{exc}")

        focus.reset()
        logger.info("focus_stream: 已卸载（聚焦存档保留，重新启用时接着用）")


__all__ = ["FocusStreamPlugin"]
