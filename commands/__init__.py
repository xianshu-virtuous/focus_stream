"""focus_stream 命令包。

三个入口，对应主人要的快捷开关：

- ``/打开聚焦``（``/focus_on``）：只回应当前这个流；
- ``/关闭聚焦``（``/focus_off``）：恢复成平常那样；
- ``/聚焦``（``/focus``）：看状态、或者 ``/聚焦 这里`` 就地切过来。

命令是走**命令分发**（在消息入库那一步直接执行）的，不经过 Chatter：所以她就算
正在别的流里静音着，主人也能在被静音的那个流里直接敲 ``/打开聚焦`` 把焦点叫回来。
"""

from .focus_command import ALL_COMMANDS, FocusCommand, FocusOffCommand, FocusOnCommand

__all__ = ["ALL_COMMANDS", "FocusCommand", "FocusOffCommand", "FocusOnCommand"]
