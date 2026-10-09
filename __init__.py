"""focus_stream 插件包：只回主人当前所在的那一个流。

插件入口在 :mod:`plugin` 模块中由 ``@register_plugin`` 装饰的
``FocusStreamPlugin`` 类注册，由插件加载器自动发现——因此这里**不要**导入
``plugin`` 里的东西：加载器是以 ``focus_stream.plugin`` 为名执行的，包内再做
``from .plugin import ...`` 会变成循环导入（父包初始化时入口模块还没执行完）。

本包职责：

- :mod:`config` —— 插件配置（白名单、聚焦规则、注入、工具开关）；
- :mod:`state` —— 聚焦状态的持久化（``data/json_storage/<state_key>.json``）；
- :mod:`rules` —— 纯逻辑：谁有资格、算不算呼唤、该不该静音；
- :mod:`focus` —— 运行时编排：当前状态、切换动作、状态文案；
- :mod:`gate` —— 非聚焦流的 Tick 闸门（只看不回）；
- :mod:`watcher` —— 认出主人与呼唤，把焦点挪过去；
- :mod:`inject` —— 把当前聚焦状态注入她的上下文；
- :mod:`commands` —— ``/打开聚焦``、``/关闭聚焦``、``/聚焦``；
- :mod:`tools` —— 只读的 ``get_focus_state``。
"""

__all__: list[str] = []
