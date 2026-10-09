"""focus_stream 加载链路冒烟：把插件交给框架的 PluginManager 真装一遍。

和 :mod:`check_focus` 的分工：

- ``check_focus``：插件自身的逻辑与组件元数据（离线替身，跑得快、断言多）；
- **本脚本**：**注册链路**——读 ``manifest.json`` → 导入模块 → 实例化 → 注册组件到全局
  注册表 → 跑 ``on_plugin_loaded``，也就是 Bot 启动时真正会走的那条路。

**不启动 Bot、不连任何平台、不发消息。** 会做的事只有：读一次插件配置
（``config/plugins/focus_stream/config.toml``，没有就由框架生成一份默认的），
以及在自己的进程里写一条 system reminder（进程结束即消失）。

仓库里与装好之后都能跑（路径自己认）：

    # 仓库内
    $env:NEO_ROOT="F:\\Neo-MoFox-Aemeath"
    & "F:\\Neo-MoFox-Aemeath\\.venv\\Scripts\\python.exe" `
        "F:\\mofox插件\\plugin\\repo\\focus_stream\\tests\\check_loading.py"

    # 已装进实例（cwd 建议给实例根，配置会生成到它的 config/plugins 下）
    & "F:\\Neo-MoFox-Aemeath\\.venv\\Scripts\\python.exe" `
        "F:\\Neo-MoFox-Aemeath\\plugins\\focus_stream\\tests\\check_loading.py"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGIN_PARENT = PLUGIN_DIR.parent
_ENV_ROOT = os.environ.get("NEO_ROOT", "").strip()
NEO_ROOT = Path(_ENV_ROOT).resolve() if _ENV_ROOT else PLUGIN_PARENT.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

_failures: list[str] = []

#: 本插件应该注册出来的全部组件签名（``插件:类型:名字``）
EXPECTED_SIGNATURES = {
    "focus_stream:command:focus",
    "focus_stream:command:focus_on",
    "focus_stream:command:focus_off",
    "focus_stream:tool:get_focus_state",
    "focus_stream:event_handler:focus_stream_gate",
    "focus_stream:event_handler:focus_stream_watcher",
}


def check(name: str, ok: bool, extra: str = "") -> bool:
    """打印一条检查结果。"""

    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  — {extra}" if extra else ""))
    if not ok:
        _failures.append(name)
    return ok


async def main() -> int:
    """入口。"""

    sys.path.insert(0, str(NEO_ROOT))
    sys.path.insert(0, str(PLUGIN_PARENT))

    print(f"实例根目录 : {NEO_ROOT}")
    print(f"插件目录   : {PLUGIN_DIR}")
    print(f"测试 cwd   : {Path.cwd()}")
    print("-" * 72)

    try:
        from src.core.components.loader import load_manifest
        from src.core.components.registry import get_global_registry
        from src.core.managers import get_plugin_manager
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 导入框架模块失败: {exc!r}")
        return 2

    from focus_stream.config import FocusStreamConfig
    from focus_stream.gate import FocusGate
    from focus_stream.watcher import FocusWatcher

    manifest = await load_manifest(str(PLUGIN_DIR))
    if manifest is None:
        print("[FAIL] 框架读不出 manifest.json")
        return 2
    check(
        "框架读得出 manifest",
        manifest.name == "focus_stream" and bool(manifest.entry_point),
        f"{manifest.name} v{manifest.version} entry={manifest.entry_point}",
    )
    check(
        "manifest 声明的 include 数量正确",
        len(manifest.include) == 6,
        f"{[f'{i.component_type}:{i.component_name}' for i in manifest.include]}",
    )

    manager = get_plugin_manager()
    loaded = await manager.load_plugin_from_manifest(str(PLUGIN_DIR), manifest)
    check("PluginManager 加载插件成功", bool(loaded))

    plugin = manager.get_plugin("focus_stream")
    check("能按名字取回插件实例", plugin is not None, type(plugin).__name__ if plugin else "None")
    check(
        "插件配置是真配置对象（不是 None）",
        isinstance(getattr(plugin, "config", None), FocusStreamConfig),
        type(getattr(plugin, "config", None)).__name__,
    )

    registry = get_global_registry()
    signatures = {str(sig) for sig in registry.list_all() if "focus_stream" in str(sig)}
    # 框架会把插件的配置类也当成一个组件注册（``focus_stream:config:config``），
    # 那不是本插件声明的 include，比对时先摘出去、单独确认它确实在。
    declared = {sig for sig in signatures if sig.split(":")[1] != "config"}
    missing = sorted(EXPECTED_SIGNATURES - declared)
    extra = sorted(declared - EXPECTED_SIGNATURES)
    check(
        "六个组件都进了全局注册表",
        not missing and not extra,
        f"缺={missing} 多={extra}" if (missing or extra) else f"{len(declared)} 个",
    )
    check(
        "框架顺带注册了配置组件",
        "focus_stream:config:config" in signatures,
        f"{sorted(signatures)}" if "focus_stream:config:config" not in signatures else "",
    )

    check(
        "闸门订阅 on_chatter_step",
        FocusGate.init_subscribe == ["on_chatter_step"],
        f"{FocusGate.init_subscribe}",
    )
    check(
        "观察者订阅 on_message_received",
        FocusWatcher.init_subscribe == ["on_message_received"],
        f"{FocusWatcher.init_subscribe}",
    )

    from focus_stream import focus

    check(
        "on_plugin_loaded 跑过：设置已注入焦点模块",
        focus.settings().state_key == getattr(plugin.config, "plugin").state_key,
        f"state_key={focus.settings().state_key}",
    )

    config_file = NEO_ROOT / "config" / "plugins" / "focus_stream" / "config.toml"
    check(
        "插件配置文件已就位（框架生成或已有）",
        config_file.exists(),
        str(config_file),
    )

    print("-" * 72)
    if _failures:
        print(f"结果：{len(_failures)} 项失败 -> {_failures}")
        return 1
    print("结果：全部通过（未启动 Bot、未连平台）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
