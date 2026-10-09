"""focus_stream 自检：真实框架导入冒烟 + 白名单/呼唤/闸门/观察者的断言。

两种跑法都支持（同一份文件，路径自己认）：

1) 在插件仓库里跑（开发时）：

    $env:NEO_ROOT="F:\\Neo-MoFox-Aemeath"
    & "F:\\Neo-MoFox-Aemeath\\.venv\\Scripts\\python.exe" `
        "F:\\mofox插件\\plugin\\repo\\focus_stream\\tests\\check_focus.py"

2) 已经装进实例（``<框架根>\\plugins\\focus_stream\\tests\\check_focus.py``）时，
   不设 ``NEO_ROOT`` 也能跑——框架根从上级目录倒推：

    & "F:\\Neo-MoFox-Aemeath\\.venv\\Scripts\\python.exe" `
        "F:\\Neo-MoFox-Aemeath\\plugins\\focus_stream\\tests\\check_focus.py"

只读验证：不改实例里的任何插件 / 配置；核心配置用不上（昵称与 owner_list 读不到时
由自定义呼唤词与插件白名单补位），reminder 用替身 store，不碰真框架的 prompt 单例。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

#: 插件目录（``…/focus_stream``），manifest 与自检都从它出发
PLUGIN_DIR = Path(__file__).resolve().parents[1]
#: 插件的父目录：仓库里是 ``plugin/repo``，装好后是 ``<框架根>/plugins``
PLUGIN_PARENT = PLUGIN_DIR.parent
#: 框架根：优先环境变量；否则从「插件装在 <根>/plugins/<插件>」倒推
_ENV_ROOT = os.environ.get("NEO_ROOT", "").strip()
NEO_ROOT = Path(_ENV_ROOT).resolve() if _ENV_ROOT else PLUGIN_PARENT.parent

# GBK 控制台下打中文会抛 UnicodeEncodeError，让「断言全过」变成 exit 1
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

_failures: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> bool:
    """打印一条检查结果。"""

    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  — {extra}" if extra else ""))
    if not ok:
        _failures.append(name)
    return ok


# --------------------------------------------------------------------------- #
# 替身
# --------------------------------------------------------------------------- #


class FakeMessage:
    """够用的消息替身：只填插件会读到的那些字段。"""

    def __init__(
        self,
        *,
        stream_id: str,
        chat_type: str,
        sender_id: str,
        text: str = "",
        platform: str = "qq",
        group_name: str = "",
        at_users: list[dict[str, Any]] | None = None,
        self_id: str = "",
        sender_name: str = "",
    ) -> None:
        self.stream_id = stream_id
        self.chat_type = chat_type
        self.sender_id = sender_id
        self.platform = platform
        self.processed_plain_text = text
        self.content = text
        self.sender_name = sender_name or f"user{sender_id}"
        self.sender_cardname = self.sender_name
        self.extra: dict[str, Any] = {}
        if group_name:
            self.extra["group_name"] = group_name
        if at_users is not None:
            self.extra["at_users"] = at_users
        self.raw_data = {"self_id": self_id} if self_id else {}


class FakeReminderStore:
    """替身：system reminder store（只记账）。"""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.deleted: list[tuple[str, str]] = []

    def set(
        self,
        bucket: str,
        name: str,
        content: str,
        insert_type: str | None = None,
        consume: str | None = None,
    ) -> None:
        self.items[(bucket, name)] = {
            "content": content,
            "insert_type": insert_type,
            "consume": consume,
        }

    def delete(self, bucket: str, name: str) -> bool:
        self.deleted.append((bucket, name))
        self.items.pop((bucket, name), None)
        return True


def make_settings(**overrides: Any) -> "Any":
    """造一份设置（默认：白名单 qq:1001，私聊自动，群里要呼唤）。"""

    from focus_stream.rules import Settings

    base = Settings(
        enabled=True,
        state_key="focus_stream_check",
        debug_log=False,
        restore_after_restart=False,
        whitelist_users=["qq:1001"],
        use_owner_list=False,
        fallback_allow_all=False,
        private_auto=True,
        group_requires_call=True,
        call_requires_at=False,
        call_words=["小爱"],
        silence_when_unfocused=True,
        stale_hours=0.0,
        inject_enabled=True,
        inject_buckets=["actor"],
        inject_name="focus_stream_now",
        inject_text="",
        inject_guard="",
        tools_enabled=True,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:  # noqa: C901 - 自检脚本，长一点无妨
    """入口。"""

    sys.path.insert(0, str(NEO_ROOT))
    sys.path.insert(0, str(PLUGIN_PARENT))

    print(f"实例根目录 : {NEO_ROOT}")
    print(f"插件目录   : {PLUGIN_DIR}")
    print(f"测试 cwd   : {Path.cwd()}")
    print("-" * 72)

    # ── 1. 真实框架导入冒烟 ────────────────────────────────────────────────
    try:
        from focus_stream import focus, inject, reply as reply_mod, rules
        from focus_stream.commands import (
            ALL_COMMANDS,
            FocusCommand,
            FocusOffCommand,
            FocusOnCommand,
        )
        from focus_stream.config import FocusStreamConfig
        from focus_stream.gate import FocusGate
        from focus_stream.plugin import FocusStreamPlugin
        from focus_stream.state import FocusState, load as load_state, save as save_state
        from focus_stream.tools import GetFocusStateTool
        from focus_stream.watcher import FocusWatcher
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 导入 focus_stream 失败: {exc!r}")
        return 2
    check("插件模块可导入（真框架 + 真依赖）", True)

    # ── 2. manifest / 组件元数据 ──────────────────────────────────────────
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))
    check(
        "manifest.name 与目录名一致",
        manifest.get("name") == PLUGIN_DIR.name,
        f"{manifest.get('name')} / {PLUGIN_DIR.name}",
    )
    check(
        "manifest 版本与 plugin_version 一致",
        manifest.get("version") == FocusStreamPlugin.plugin_version,
        f"{manifest.get('version')} / {FocusStreamPlugin.plugin_version}",
    )
    check(
        "manifest.json 不带 BOM",
        not (PLUGIN_DIR / "manifest.json").read_bytes().startswith(b"\xef\xbb\xbf"),
    )
    includes = manifest.get("include", [])
    declared = {
        (item.get("component_type"), item.get("component_name")) for item in includes
    }
    expected = {
        ("command", FocusCommand.name),
        ("command", FocusOnCommand.name),
        ("command", FocusOffCommand.name),
        ("tool", GetFocusStateTool.name),
        ("event_handler", FocusGate.handler_name),
        ("event_handler", FocusWatcher.handler_name),
    }
    check(
        "manifest 声明的组件与代码里的名字一一对应",
        declared == expected,
        f"多={sorted(declared - expected)} 少={sorted(expected - declared)}",
    )
    check(
        "dependencies.plugins 为空（软依赖不能写这里）",
        not manifest.get("dependencies", {}).get("plugins"),
    )

    try:
        from src.app.plugin_system.api import PLUGIN_API_VERSIONS

        unknown = sorted(set(manifest.get("api_version", {})) - set(PLUGIN_API_VERSIONS))
        check("manifest 的 api_version 全是合法模块", not unknown, f"未知：{unknown}" if unknown else "")
    except Exception as exc:  # noqa: BLE001
        check("manifest 的 api_version 校验", False, f"取 PLUGIN_API_VERSIONS 失败：{exc!r}")

    # ── 3. 纯逻辑：白名单 ─────────────────────────────────────────────────
    s = make_settings()
    check(
        "白名单命中：平台 + ID 都对",
        rules.is_allowed(s, platform="qq", user_id="1001"),
    )
    check(
        "白名单不命中：ID 对但平台不对",
        not rules.is_allowed(s, platform="wx", user_id="1001"),
    )
    check(
        "白名单不命中：别人",
        not rules.is_allowed(s, platform="qq", user_id="2002"),
    )
    bare = make_settings(whitelist_users=["1001"], use_owner_list=False)
    check(
        "裸 ID 条目对所有平台生效",
        rules.is_allowed(bare, platform="qq", user_id="1001")
        and rules.is_allowed(bare, platform="telegram", user_id="1001"),
    )
    owner_only = make_settings(whitelist_users=[], use_owner_list=True)
    check(
        "名单为空时信任 owner_list",
        rules.is_allowed(owner_only, platform="qq", user_id="1234", owner_entries=["qq:1234"])
        and not rules.is_allowed(owner_only, platform="qq", user_id="1234", owner_entries=[]),
    )
    nothing = make_settings(whitelist_users=[], use_owner_list=False)
    check(
        "名单与 owner_list 都空且未放开时：谁都不许（安全默认）",
        not rules.is_allowed(nothing, platform="qq", user_id="1001"),
    )
    check(
        "显式放开时谁都行（fallback_allow_all）",
        rules.is_allowed(
            make_settings(whitelist_users=[], use_owner_list=False, fallback_allow_all=True),
            platform="qq",
            user_id="9999",
        ),
    )
    check(
        "插件关掉时谁都不能切",
        not rules.is_allowed(make_settings(enabled=False), platform="qq", user_id="1001"),
    )

    # ── 4. 纯逻辑：呼唤 ───────────────────────────────────────────────────
    names = ["小爱", "阿爱"]
    check(
        "@ 到她就算呼唤",
        rules.match_call(
            text="[图片]",
            at_users=[{"user_id": "999"}],
            self_id="999",
            names=names,
            settings=s,
        )
        == "at",
    )
    check(
        "话里叫名字也算",
        rules.match_call(
            text="小爱在吗", at_users=[], self_id="999", names=names, settings=s
        )
        == "name:小爱",
    )
    check(
        "没叫到就不算",
        rules.match_call(
            text="今天天气不错", at_users=[], self_id="999", names=names, settings=s
        )
        == "",
    )
    strict = make_settings(call_requires_at=True)
    check(
        "call_requires_at 时只认 @",
        rules.match_call(text="小爱在吗", at_users=[], self_id="999", names=names, settings=strict) == ""
        and rules.match_call(
            text="在吗", at_users=[{"user_id": "999"}], self_id="999", names=names, settings=strict
        )
        == "at",
    )
    check(
        "拿不到自己的 ID 时不会误判 @",
        rules.match_call(text="在吗", at_users=[{"user_id": "999"}], self_id="", names=names, settings=s)
        == "",
    )

    # ── 5. 纯逻辑：静音判定 ───────────────────────────────────────────────
    st_focus = FocusState(focus_stream_id="s_group", chat_type="group", label="测试群")
    check(
        "聚焦流放行",
        not rules.should_silence(st_focus, s, "s_group"),
    )
    check(
        "非聚焦流静音",
        rules.should_silence(st_focus, s, "s_private"),
    )
    off = FocusState(manual_off=True)
    check(
        "手动关闭后谁都不拦",
        not rules.should_silence(off, s, "s_private")
        and not rules.should_silence(off, s, "s_group"),
    )
    idle = FocusState()
    check(
        "还没聚焦时默认先静音",
        rules.should_silence(idle, s, "s_private"),
    )
    check(
        "silence_when_unfocused=false 时未聚焦不拦",
        not rules.should_silence(idle, make_settings(silence_when_unfocused=False), "s_private"),
    )
    check("插件关掉时永不拦", not rules.should_silence(st_focus, make_settings(enabled=False), "s_private"))

    # stale：超时算陈旧，未超时不算
    #（focused_at=0 表示「没有时间戳」，那是不陈旧的安全默认，所以这里用真实的旧时间戳）
    import time as _time

    old = FocusState(focus_stream_id="s", focused_at=_time.time() - 7200)
    fresh_state = FocusState(focus_stream_id="s", focused_at=_time.time())
    check(
        "stale_hours 生效（2 小时前的聚焦，阈值 1 小时＝陈旧）",
        rules.is_stale(old, make_settings(stale_hours=1.0))
        and not rules.is_stale(old, make_settings(stale_hours=0.0))
        and not rules.is_stale(fresh_state, make_settings(stale_hours=1.0)),
    )

    # 流名字
    check(
        "流的名字认得出来",
        rules.stream_label("private", user_name="主人") == "主人的私聊"
        and rules.stream_label("group", group_name="测试群") == "测试群"
        and rules.message_label(
            FakeMessage(stream_id="x", chat_type="group", sender_id="1", group_name="群A")
        )
        == "群A",
    )

    # ── 6. 闸门（FocusGate） ──────────────────────────────────────────────
    gate = FocusGate(plugin=None)  # type: ignore[arg-type]

    async def run_gate(stream_id: str) -> bool:
        params: dict[str, Any] = {"stream_id": stream_id, "continue": True}
        decision, out = await gate.execute("on_chatter_step", params)
        check_keys = set(params.keys()) == {"stream_id", "continue"}
        assert check_keys, "闸门改动/新增了 params 的 key（框架会丢弃我们的决策）"
        return bool(out.get("continue")) and decision.value == "SUCCESS"

    focus.reset()
    focus.configure(make_settings(restore_after_restart=False))
    focus.state().focus_stream_id = "s_private"
    focus.state().label = "主人的私聊"
    focus.state().chat_type = "private"
    check("闸门：聚焦流放行", asyncio.run(run_gate("s_private")))
    check("闸门：别的流被拦（continue=False）", not asyncio.run(run_gate("s_group")))

    asyncio.run(focus.focus_off(by="check"))
    check("闸门：关掉聚焦后全部放行", asyncio.run(run_gate("s_group")))
    focus.reset()

    # ── 7. 观察者（FocusWatcher）：切换聚焦 ───────────────────────────────
    watcher = FocusWatcher(plugin=None)  # type: ignore[arg-type]
    reminder_store = FakeReminderStore()
    real_store = inject._store
    inject._store = lambda: reminder_store  # type: ignore[assignment]

    async def run_watcher(message: FakeMessage) -> None:
        await watcher.execute("on_message_received", {"message": message})

    async def scenario_fresh() -> None:
        focus.reset()
        focus.configure(make_settings(restore_after_restart=False))

    # 7.1 私聊自动聚焦
    asyncio.run(scenario_fresh())
    asyncio.run(
        run_watcher(
            FakeMessage(
                stream_id="s_private",
                chat_type="private",
                sender_id="1001",
                text="在吗",
                sender_name="主人",
            )
        )
    )
    check(
        "私聊：主人一说话就自动聚焦该私聊",
        focus.state().focus_stream_id == "s_private" and not focus.state().manual_off,
        focus.describe_line(),
    )
    check(
        "聚焦后写入了状态注入（fixed/forever）",
        any(
            payload["insert_type"] == "fixed" and payload["consume"] == "forever"
            for payload in reminder_store.items.values()
        ),
    )

    # 7.2 群里没叫她 → 不动
    asyncio.run(
        run_watcher(
            FakeMessage(
                stream_id="s_group",
                chat_type="group",
                sender_id="1001",
                text="大家早",
                group_name="测试群",
                self_id="999",
            )
        )
    )
    check(
        "群聊：没呼唤就不抢焦点（私聊仍然是焦点）",
        focus.state().focus_stream_id == "s_private",
        focus.describe_line(),
    )

    # 7.3 群里 @ 她 → 切过去
    asyncio.run(
        run_watcher(
            FakeMessage(
                stream_id="s_group",
                chat_type="group",
                sender_id="1001",
                text="在吗",
                group_name="测试群",
                at_users=[{"user_id": "999"}],
                self_id="999",
            )
        )
    )
    check(
        "群聊：主人 @ 她 → 焦点切到群里",
        focus.state().focus_stream_id == "s_group" and focus.state().label == "测试群",
        focus.describe_line(),
    )

    # 7.4 群里叫名字 → 切过去（先挪走私聊）
    asyncio.run(
        run_watcher(
            FakeMessage(
                stream_id="s_private2",
                chat_type="private",
                sender_id="1001",
                text="喂",
            )
        )
    )
    asyncio.run(
        run_watcher(
            FakeMessage(
                stream_id="s_group2",
                chat_type="group",
                sender_id="1001",
                text="小爱过来",
                group_name="另一个群",
                self_id="999",
            )
        )
    )
    check(
        "群聊：叫名字也算呼唤",
        focus.state().focus_stream_id == "s_group2",
        focus.describe_line(),
    )

    # 7.5 非白名单的人 → 怎么喊都不动
    before = focus.state().focus_stream_id
    asyncio.run(
        run_watcher(
            FakeMessage(
                stream_id="s_group3",
                chat_type="group",
                sender_id="2002",
                text="小爱小爱！",
                group_name="陌生群",
                at_users=[{"user_id": "999"}],
                self_id="999",
            )
        )
    )
    check(
        "白名单外的人喊不动焦点",
        focus.state().focus_stream_id == before,
        f"{before} → {focus.state().focus_stream_id}",
    )
    asyncio.run(
        run_watcher(
            FakeMessage(stream_id="s_x", chat_type="private", sender_id="2002", text="你好")
        )
    )
    check(
        "陌生人的私聊不会被自动聚焦",
        focus.state().focus_stream_id == before,
        focus.describe_line(),
    )

    # 7.6 private_auto 关掉时私聊不自动切
    asyncio.run(scenario_fresh())
    focus.configure(make_settings(private_auto=False, restore_after_restart=False))
    asyncio.run(
        run_watcher(
            FakeMessage(stream_id="s_private", chat_type="private", sender_id="1001", text="在吗")
        )
    )
    check(
        "private_auto=false：私聊不自动切（等命令）",
        not focus.state().is_focused,
        focus.describe_line(),
    )

    # 7.7 插件关掉时观察者不动手
    asyncio.run(scenario_fresh())
    focus.configure(make_settings(enabled=False, restore_after_restart=False))
    asyncio.run(
        run_watcher(
            FakeMessage(stream_id="s_private", chat_type="private", sender_id="1001", text="在吗")
        )
    )
    check("插件关掉时不做任何切换", not focus.state().is_focused)

    # ── 8. 持久化往返 ─────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            asyncio.run(scenario_fresh())
            focus.configure(make_settings(restore_after_restart=True))

            async def switch() -> None:
                await focus.focus_on(
                    stream_id="s_persist",
                    chat_type="private",
                    platform="qq",
                    label="主人的私聊",
                    by="qq:1001",
                )

            asyncio.run(switch())
            state_file = Path("data") / "json_storage" / "focus_stream_check.json"
            check("聚焦落地成 json 存档", state_file.exists(), str(state_file))

            focus.reset()
            restored = asyncio.run(focus.ensure_loaded())
            check(
                "重启后按存档恢复聚焦",
                restored.focus_stream_id == "s_persist" and restored.label == "主人的私聊",
                restored.describe(),
            )

            # 坏档回落
            state_file.write_text("{ 这不是 json", encoding="utf-8")
            focus.reset()
            bad = asyncio.run(focus.ensure_loaded())
            check("坏档不抛异常、回落未聚焦", not bad.is_focused and not bad.manual_off)

            # restore_after_restart=false：不读档
            asyncio.run(
                focus.focus_on(stream_id="s_x", chat_type="private", label="X", by="check")
            )
            focus.reset()
            focus.configure(make_settings(restore_after_restart=False))
            fresh = asyncio.run(focus.ensure_loaded())
            check("restore_after_restart=false 时不读档", not fresh.is_focused)

            # 状态模块自身的读写
            asyncio.run(scenario_fresh())
            focus.configure(make_settings(restore_after_restart=True))
            asyncio.run(
                save_state(
                    "focus_stream_check",
                    FocusState(focus_stream_id="abc", chat_type="group", label="群"),
                )
            )
            loaded = asyncio.run(load_state("focus_stream_check"))
            check(
                "state.load/save 往返一致",
                loaded.focus_stream_id == "abc" and loaded.chat_type == "group",
                loaded.describe(),
            )
        finally:
            os.chdir(cwd)

    # ── 9. 命令 ───────────────────────────────────────────────────────────
    check(
        "三条命令的名字与类型正确",
        FocusCommand.name == "focus"
        and FocusOnCommand.name == "focus_on"
        and FocusOffCommand.name == "focus_off"
        and all(cmd.component_type == "command" for cmd in ALL_COMMANDS),
    )
    check(
        "命令认得出中文快捷写法",
        FocusOnCommand.match(["打开聚焦"]) == 1
        and FocusOnCommand.match(["开启聚焦"]) == 1
        and FocusOffCommand.match(["关闭聚焦"]) == 1
        and FocusCommand.match(["聚焦"]) == 1
        and FocusOnCommand.match(["在吗"]) == 0,
    )
    check(
        "命令认得出 /流聚焦 与连写写法",
        FocusCommand.match(["流聚焦"]) == 1
        and FocusCommand.match(["流聚焦", "开启"]) == 1
        and FocusCommand.match(["流聚焦", "关闭"]) == 1
        and FocusCommand.match(["流聚焦开启"]) == 1
        and FocusCommand.match(["流聚焦关闭"]) == 1
        and FocusCommand.match(["聚焦状态"]) == 1
        and FocusCommand.match(["focusing"]) == 0
        and FocusCommand.match(["聚焦了吗"]) == 0,
    )

    # 连写时框架切不出参数，命令要能从原始消息里把后缀抠出来
    inline_msg = FakeMessage(
        stream_id="s_inline", chat_type="private", sender_id="1001", text="/流聚焦开启"
    )
    inline_cmd = FocusCommand(plugin=None, stream_id="s_inline", message=inline_msg)  # type: ignore[arg-type]
    check(
        "连写时能从原始消息解析出后缀",
        inline_cmd._suffix_from_message() == "开启",
        repr(inline_cmd._suffix_from_message()),
    )
    spaced_msg = FakeMessage(
        stream_id="s_inline", chat_type="private", sender_id="1001", text="/聚焦 关闭"
    )
    spaced_cmd = FocusCommand(plugin=None, stream_id="s_inline", message=spaced_msg)  # type: ignore[arg-type]
    check(
        "带空格的写法也解析出后缀",
        spaced_cmd._suffix_from_message() == "关闭",
        repr(spaced_cmd._suffix_from_message()),
    )

    from focus_stream.commands import focus_command as fc

    sent: list[str] = []

    async def fake_reply(text: str, stream_id: str = "", *, hidden: bool = True) -> bool:
        sent.append(text)
        return True

    real_reply = fc.send_reply
    fc.send_reply = fake_reply  # type: ignore[assignment]
    try:
        asyncio.run(scenario_fresh())
        focus.configure(make_settings(restore_after_restart=False))

        master_msg = FakeMessage(
            stream_id="s_group", chat_type="group", sender_id="1001", group_name="测试群"
        )
        stranger_msg = FakeMessage(
            stream_id="s_group", chat_type="group", sender_id="2002", group_name="测试群"
        )

        sent.clear()
        ok, _ = asyncio.run(FocusOnCommand(plugin=None, stream_id="s_group", message=master_msg).execute(""))  # type: ignore[arg-type]
        check(
            "/打开聚焦：白名单里的人能把焦点切到当前流",
            ok and focus.state().focus_stream_id == "s_group" and bool(sent),
            sent[-1] if sent else "（没回话）",
        )

        sent.clear()
        ok_status, _ = asyncio.run(FocusCommand(plugin=None, stream_id="s_group", message=master_msg).execute(""))  # type: ignore[arg-type]
        check(
            "/聚焦：返回状态文本",
            ok_status and sent and "聚焦" in sent[-1],
            (sent[-1].splitlines()[0] if sent else "（没回话）"),
        )

        sent.clear()
        ok_off, _ = asyncio.run(FocusOffCommand(plugin=None, stream_id="s_group", message=master_msg).execute(""))  # type: ignore[arg-type]
        check(
            "/关闭聚焦：关掉后不再拦截",
            ok_off and focus.state().manual_off and bool(sent),
            sent[-1] if sent else "（没回话）",
        )

        sent.clear()
        before_id = focus.state().focus_stream_id
        ok_denied, reason = asyncio.run(FocusOnCommand(plugin=None, stream_id="s_group", message=stranger_msg).execute(""))  # type: ignore[arg-type]
        check(
            "白名单外的人用命令：被挡下且状态不变",
            (not ok_denied) and reason == "focus_denied" and focus.state().focus_stream_id == before_id,
            sent[-1] if sent else "（没回话）",
        )

        sent.clear()
        private_msg = FakeMessage(
            stream_id="s_private", chat_type="private", sender_id="1001", sender_name="主人"
        )
        ok_here, _ = asyncio.run(FocusCommand(plugin=None, stream_id="s_private", message=private_msg).execute("这里"))  # type: ignore[arg-type]
        check(
            "/聚焦 这里：就地切过来（关着也能重新打开）",
            ok_here
            and focus.state().focus_stream_id == "s_private"
            and focus.state().label == "主人的私聊"
            and not focus.state().manual_off,
            focus.describe_line(),
        )

        sent.clear()
        asyncio.run(FocusCommand(plugin=None, stream_id="s_private", message=master_msg).execute("瞎写"))  # type: ignore[arg-type]
        check("认不出的参数给用法提示", bool(sent) and "用法" in sent[-1])
    finally:
        fc.send_reply = real_reply  # type: ignore[assignment]

    # ── 10. 工具 ──────────────────────────────────────────────────────────
    asyncio.run(scenario_fresh())
    focus.configure(make_settings(restore_after_restart=False))
    asyncio.run(
        focus.focus_on(stream_id="s_tool", chat_type="private", label="主人的私聊", by="check")
    )
    ok_tool, tool_text = asyncio.run(GetFocusStateTool(plugin=None).execute())  # type: ignore[arg-type]
    check(
        "get_focus_state 返回状态并声明「不用念出来」",
        ok_tool and "主人的私聊" in tool_text and "不用念出来" in tool_text,
        tool_text.splitlines()[0] if tool_text else "",
    )

    # ── 11. 注入文案 ──────────────────────────────────────────────────────
    focus.reset()
    focus.configure(make_settings(restore_after_restart=False))
    idle_text = inject.text_for(FocusState(), focus.settings())
    check(
        "未聚焦且先静音时：注入「还没确定」+ 防背书",
        "还没确定" in idle_text and "不是要念出来的台词" in idle_text,
    )
    focused_text = inject.text_for(
        FocusState(focus_stream_id="abc", label="测试群"), focus.settings()
    )
    check(
        "聚焦时：注入聚焦流名字 + 防背书，且不含流 ID 明文",
        "测试群" in focused_text
        and "不是要念出来的台词" in focused_text
        and "abc" not in focused_text,
    )
    check(
        "手动关闭时不注入（返回空串）",
        inject.text_for(FocusState(manual_off=True), focus.settings()) == "",
    )
    custom = make_settings(inject_text="只在「{label}」这边说话。", inject_guard="别说出去。")
    check(
        "注入文案可整段替换（含 {label} 占位）",
        inject.text_for(FocusState(focus_stream_id="x", label="群B"), custom)
        == "【当前聚焦】只在「群B」这边说话。\n别说出去。",
    )
    check(
        "注入关掉时不写 reminder",
        inject.sync(FocusState(focus_stream_id="x"), make_settings(inject_enabled=False)) is False,
    )

    inject._store = real_store  # type: ignore[assignment]

    # 真实 reminder store 往返（进程内存里，不落盘）
    try:
        from src.app.plugin_system.api import prompt_api

        focus.reset()
        focus.configure(make_settings(restore_after_restart=False))
        asyncio.run(focus.focus_on(stream_id="s_rt", chat_type="private", label="往返测试", by="check"))
        roundtrip = prompt_api.get_system_reminder("actor", ["focus_stream_now"])
        check("真实 reminder store 往返成功", "往返测试" in roundtrip)
        inject.clear(focus.settings())
        check(
            "真实 store 里也能删干净",
            "往返测试" not in prompt_api.get_system_reminder("actor", ["focus_stream_now"]),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] 真实 reminder store 往返（框架未初始化，属正常）：{exc!r}")

    # ── 12. 组件注册表（可选） ────────────────────────────────────────────
    try:
        from src.core.components.registry import get_global_registry

        signatures = [str(sig) for sig in get_global_registry().list_all()]
        hit = [sig for sig in signatures if "focus_stream" in sig]
        if not signatures:
            print("[SKIP] 注册表检查（注册表为空＝框架没启动过，属正常）")
        else:
            check("组件注册表里能查到本插件", bool(hit), f"{hit}" if hit else f"共 {len(signatures)} 项")
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] 注册表检查（框架未初始化，属正常）：{exc!r}")

    # ── 13. 插件入口 ──────────────────────────────────────────────────────
    plugin = FocusStreamPlugin(FocusStreamConfig())
    components = plugin.get_components()
    check(
        "get_components 交出闸门/观察者/三条命令/工具",
        FocusGate in components
        and FocusWatcher in components
        and all(cmd in components for cmd in ALL_COMMANDS)
        and GetFocusStateTool in components,
        f"{len(components)} 个",
    )
    disabled = FocusStreamConfig()
    disabled.plugin.enabled = False
    check(
        "插件关掉时不注册任何组件",
        FocusStreamPlugin(disabled).get_components() == [],
    )
    no_tools = FocusStreamConfig()
    no_tools.tools.enabled = False
    check(
        "工具开关能单独关掉",
        GetFocusStateTool not in FocusStreamPlugin(no_tools).get_components(),
    )
    built = rules.build_settings(FocusStreamConfig())
    check(
        "默认配置：私聊自动 + 群里要呼唤 + 用框架主人列表",
        built.private_auto and built.group_requires_call and built.use_owner_list,
        f"whitelist={built.whitelist_entries()} owner_list={built.use_owner_list}",
    )
    check("默认回执对她隐身", built.hidden_reply is True)
    check(
        "隐身回执在拿不到流/适配器时安全失败（不抛异常）",
        asyncio.run(reply_mod.send_hidden("测试回执", "no_such_stream")) is False,
    )

    print("-" * 72)
    if _failures:
        print(f"结果：{len(_failures)} 项失败 -> {_failures}")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
