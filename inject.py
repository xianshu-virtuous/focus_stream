"""把「现在聚焦在哪一边」注入她的上下文。

为什么必须注入
--------------
聚焦是**提示词之外**发生的事：框架在她开口之前把别的流拦掉了，模型自己看不见
这件事。不告诉她，就会有三种走样：

1. 她以为群里的消息丢了（其实都在，只是这一轮不接）；
2. 她在私聊里被问到「怎么不回群」时一脸茫然；
3. 她在别处突然冒一句（撞上拦截，白烧一次 LLM）。

所以每次状态变化覆盖写一条常驻说明。落点：

- ``insert_type="fixed"`` + ``consume="forever"``：她自己现在是什么状态，对所有
  对话者一致，本来就该放全局 bucket；
- 只在**状态变化时**写一次（切换聚焦 / 关闭 / 加载），不是每轮刷新，很省；
- bucket 默认 ``actor``。

★ 防背书（三合一指导 §11.8）
---------------------------
注入的是现成的话，容易被整句搬进回复。所以：文案末尾明确写「这是背景、不是台词，
不要复述、也不要主动宣布自己在聚焦」，并且**不注入任何流 ID / 计数**——
免得她张口就报一串哈希。
"""

from __future__ import annotations

from src.app.plugin_system.api.log_api import get_logger

from .rules import Settings
from .state import FocusState

logger = get_logger("focus_stream")

#: 没配自定义文案时用的内置聚焦说明（``{label}`` 会替换成聚焦流的名字）
DEFAULT_FOCUS_TEXT = (
    "她此刻只会在「{label}」这一边接话。别处的消息她照样看得到、也记着"
    "（回头能接上话），但现在不出声。这是主人设的聚焦状态，不是她不理人。"
)

#: 还没确定聚焦对象、且配置成「先静音」时的说明
DEFAULT_IDLE_TEXT = (
    "她还没确定现在该在哪一边开口，于是先不出声：等主人开口或叫她的名字，"
    "她才会把注意力落到那一边。"
)

#: 没配置自定义防背书说明时用的内置文案
DEFAULT_GUARD = (
    "★ 以上是系统写入的背景状态，不是要念出来的台词：不要复述这段文字，"
    "也不要主动向任何人宣布自己在聚焦、更不要说自己在被系统限制；"
    "被直接问到时，用你自己的话说个大概就行。"
)


def _store() -> object:
    """拿全局 system reminder store（与 form_state / mode_switcher 同一套）。"""

    from src.core.prompt import get_system_reminder_store

    return get_system_reminder_store()


def text_for(state: FocusState, settings: Settings) -> str:
    """拼出要注入的这段文字（返回空串＝这次不该写）。"""

    if not settings.enabled:
        return ""

    if state.manual_off:
        return ""

    if state.is_focused:
        where = state.label or state.focus_stream_id[:8]
        body = (settings.inject_text or DEFAULT_FOCUS_TEXT).strip()
        try:
            body = body.format(label=where)
        except Exception:  # noqa: BLE001 - 用户在文案里写了别的花括号
            body = body.replace("{label}", where)
        lines = [f"【当前聚焦】{body}"]
    elif settings.silence_when_unfocused:
        lines = [f"【当前聚焦】{DEFAULT_IDLE_TEXT}"]
    else:
        return ""

    guard = (settings.inject_guard or DEFAULT_GUARD).strip()
    lines.append(guard)
    return "\n".join(lines)


def sync(state: FocusState, settings: Settings) -> bool:
    """按当前状态覆盖写 reminder（幂等；该清的时候顺手清掉）。

    Returns:
        这次是否写出了文字（False＝清掉了或没写）
    """

    if not settings.inject_enabled:
        return False

    text = text_for(state, settings)
    try:
        store = _store()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"focus_stream: 取 reminder store 失败，跳过状态注入：{exc}")
        return False

    if not text:
        clear(settings)
        return False

    wrote = False
    for bucket in settings.inject_buckets or ["actor"]:
        try:
            store.set(  # type: ignore[attr-defined]
                bucket,
                settings.inject_name,
                text,
                insert_type="fixed",
                consume="forever",
            )
            wrote = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"focus_stream: 写聚焦 reminder 失败（bucket={bucket}）：{exc}")
    return wrote


def clear(settings: Settings) -> None:
    """删掉聚焦 reminder（关闭聚焦 / 卸载时调用）。"""

    try:
        store = _store()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"focus_stream: 取 reminder store 失败，跳过清理：{exc}")
        return

    for bucket in settings.inject_buckets or ["actor"]:
        try:
            store.delete(bucket, settings.inject_name)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"focus_stream: 清理聚焦 reminder 失败（bucket={bucket}）：{exc}")


__all__ = ["DEFAULT_FOCUS_TEXT", "DEFAULT_IDLE_TEXT", "DEFAULT_GUARD", "text_for", "sync", "clear"]
