"""focus_stream 插件配置。

配置文件默认路径：``config/plugins/focus_stream/config.toml``

这个插件解决一件事：**她在很多个流（私聊 / 群）里同时醒着，但你只想让其中一个流里
得到回应。** 打开聚焦后，只有「当前聚焦流」的 Tick 会被放行，其他流的消息照常入库
（她看得到，回头还能接上话题），但这一轮不会开口。

三处可配：

- ``[whitelist]`` —— **谁能切换聚焦**。插件只认名单里的人；名单是用户 ID，
  也接受框架 ``owner_list`` 里的主人。名单为空且不允许信任 owner_list 时，
  自动聚焦整个关掉（只留命令），免得谁都能把她的注意力抢走。
- ``[focus]`` —— 什么算「呼唤」。私聊默认她一看到你说话就聚焦过去；群里默认
  要 @ 她或叫她的名字（昵称 / 别名，可以再加几个自定义称呼）。
- ``[inject]`` —— 把「此刻聚焦在哪一边」写进 system reminder，让她自己知道，
  不必靠上下文猜（也不至于以为群消息丢了）。

默认值对普通用户是「装上就能用」的一套：白名单只看框架主人，私聊自动聚焦，
群里要呼唤，聚焦态持久化到重启。
"""

from __future__ import annotations

from typing import ClassVar

from src.core.components.base.config import BaseConfig, Field, SectionBase, config_section


class WhitelistSection(SectionBase):
    """白名单：只有名单里的人能把她的注意力挪到自己这边。"""

    users: list[str] = Field(
        default_factory=list,
        description=(
            "允许切换聚焦的用户名单。\n"
            "格式 ``platform:user_id``（如 ``qq:123456789``）；也接受裸 ``user_id``"
            "（此时对所有平台生效）。\n"
            "留空＝不额外指定，只信任下面的 owner_list。"
        ),
    )
    use_owner_list: bool = Field(
        default=True,
        description=(
            "是否同时信任框架 ``config/core.toml`` 的 ``permissions.owner_list``。\n"
            "默认开——主人列表本来就该有最高话语权，不必在这里重抄一遍。"
        ),
    )
    fallback_allow_all: bool = Field(
        default=False,
        description=(
            "名单为空（且不信任 owner_list）时怎么办。\n"
            "``false``（默认）＝自动聚焦整块关掉，只留命令，谁也别想抢焦点；\n"
            "``true``＝不做身份校验，谁在自己所在的流里说话都能把聚焦挪过来（不推荐）。"
        ),
    )


class FocusSection(SectionBase):
    """聚焦规则：什么情况下把焦点挪到某个流。"""

    private_auto: bool = Field(
        default=True,
        description=(
            "私聊里主人一说话就自动聚焦过去（默认开）。\n"
            "开了就是你私信她、她就只跟你聊；关掉则需要她用 /聚焦 手动切。"
        ),
    )
    group_requires_call: bool = Field(
        default=True,
        description=(
            "群里是否需要「呼唤」才切聚焦（默认需要）。\n"
            "开着＝群里普通聊天不打断你现在的私聊，只有 @ 她或叫她名字才切过去；\n"
            "关掉＝主人一在群里说话就切过去（群里热闹时会被频繁抢焦点）。"
        ),
    )
    call_requires_at: bool = Field(
        default=False,
        description=(
            "群里是否必须 @ 她才算呼唤（默认不必须）。\n"
            "``false``＝@ 她、或者话里出现她的昵称/别名/自定义称呼都算；\n"
            "``true``＝只认 @，免得群里别人提到同名的词也被当成叫她。"
        ),
    )
    call_words: list[str] = Field(
        default_factory=list,
        description=(
            "额外的「呼唤词」，除了她的昵称与别名之外还认哪些叫法。\n"
            "例：``['小爱', '阿爱']``。留空＝只认框架里的昵称与别名。"
        ),
    )
    silence_when_unfocused: bool = Field(
        default=True,
        description=(
            "还没确定聚焦对象时（刚启动、或关掉聚焦后又打开）要不要先静音。\n"
            "``true``（默认）＝在有人呼唤之前谁也不回（等你先开口）；\n"
            "``false``＝没确定聚焦时按平常那样回，直到第一次切换出现。"
        ),
    )
    stale_hours: float = Field(
        default=0.0,
        description=(
            "聚焦超过这么多小时没有新的切换就自动解除（0＝永不解除，默认）。\n"
            "适合「她出差/放假」这种场景：挂太久就自动回到正常状态，不至于一直哑着。"
        ),
    )


class InjectSection(SectionBase):
    """把当前聚焦状态注入她的上下文。

    不注入的话，模型在对话里看不见自己被限制了——它可能以为群里的消息丢了，
    也可能在别处突然冒一句。这里只在**状态变化时**覆盖写一条常驻说明，很省。
    """

    enabled: bool = Field(
        default=True,
        description="是否把聚焦状态注入 system reminder（关掉＝她自己不知道在被聚焦）",
    )
    buckets: list[str] = Field(
        default_factory=lambda: ["actor"],
        description=(
            "写入哪些 reminder bucket。\n"
            "actor = 主回复模型（默认够用）；想让决策模型也知道就加成 [\"actor\", \"sub_actor\"]。"
        ),
    )
    name: str = Field(
        default="focus_stream_now",
        description="reminder 名称（同名覆盖式写入，切来切去也不会堆叠）",
    )
    text: str = Field(
        default="",
        description=(
            "自定义状态文案。``{label}`` 会替换成当前聚焦流的名字。\n"
            "留空＝用内置的通用文案（不绑任何角色）。"
        ),
    )
    guard: str = Field(
        default="",
        description=(
            "末尾那句「这是背景、不是台词」的防背书说明。留空＝用内置文案（推荐）。"
        ),
    )


class ToolsSection(SectionBase):
    """给她一个只读的查询工具，让她被问到时答得上来。"""

    enabled: bool = Field(
        default=True,
        description="是否注册 ``get_focus_state`` 工具（只读，不给她自己解除聚焦的权限）",
    )


class FocusStreamConfig(BaseConfig):
    """focus_stream 插件配置模型。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "聚焦插件（只回应主人当前所在的流）配置"

    @config_section("plugin", title="插件设置", tag="plugin")
    class PluginSection(SectionBase):
        """插件基础配置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用插件（关掉＝完全不拦截，行为与装之前一致）",
        )
        state_key: str = Field(
            default="focus_stream",
            description="聚焦状态持久化文件名（位于 data/json_storage/<state_key>.json）",
        )
        restore_after_restart: bool = Field(
            default=True,
            description=(
                "重启后是否恢复上次的聚焦状态。\n"
                "``true``（默认）＝接着上次继续聚焦同一个流；``false``＝每次重启都重新等呼唤。"
            ),
        )
        debug_log: bool = Field(
            default=False,
            description="是否在日志里输出每次拦截/切换的明细",
        )

    plugin: PluginSection = Field(default_factory=PluginSection)
    whitelist: WhitelistSection = Field(default_factory=WhitelistSection)
    focus: FocusSection = Field(default_factory=FocusSection)
    inject: InjectSection = Field(default_factory=InjectSection)
    tools: ToolsSection = Field(default_factory=ToolsSection)
