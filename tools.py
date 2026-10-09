"""``get_focus_state``：给她一个只读的「我现在被聚焦着吗」查询口。

为什么只给她**查询**、不给她**解除**：聚焦是主人设的边界。她自己把边界去掉，
主人的用意就落空了。所以这里只回答状态——她被问到时答得上来、也能明白
「群里的消息我看到了，只是那会儿不该插话」。

返回值里带一句「这是系统回执，不用念出来」——工具结果也会进上下文，
不声明就容易被整句复述（防背书，三合一指导 §11.8）。
"""

from __future__ import annotations

from src.app.plugin_system.base import BaseTool

from . import focus


class GetFocusStateTool(BaseTool):
    """查看当前的聚焦状态。"""

    tool_name: str = "get_focus_state"
    tool_description: str = "查看自己现在是不是被聚焦限制着（只读）"
    name: str = "get_focus_state"
    description: str = (
        "查看自己此刻的聚焦状态：是不是只在一个流里说话、别处的消息是不是只看不回。\n"
        "用它的时候是「想确认自己现在的处境」（例如：为什么群里没人理我 / 我该不该在群里接话）。\n"
        "这个工具只能看，不能改——聚焦是主人设的边界，要解除得由主人来。\n"
        "查到状态之后不用向任何人汇报，也不用复述返回的文字。"
    )

    async def execute(self) -> tuple[bool, str]:
        """返回当前聚焦状态文本。"""

        try:
            text = focus.status_text()
        except Exception as exc:  # noqa: BLE001
            return False, f"读取聚焦状态失败：{exc}"

        return True, f"{text}\n（这是系统回执，不用念出来，也不用向任何人报告。）"


__all__ = ["GetFocusStateTool"]
