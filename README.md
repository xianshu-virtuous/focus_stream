# focus_stream —— 只回主人当前所在的那个流

她在私聊、在群里、在别的会话里同时醒着，这个插件让她**同一时间只在一个流里说话**：

- 你在**私信**里说话 → 焦点自动落在私聊，群里再热闹她也只看不回；
- 你回**群里**叫她一声（@ 她、或者喊她的名字）→ 焦点跟着回群里；
- 别的流里的消息**照样入库**（她看得到、回头能接着聊），只是那一轮不进入模型——
  不插话，也不白烧 token。

## 装上之后走一遍

1. 你在私聊问一句「在吗」→ 焦点＝那个私聊。此时群里刷屏，她一条都不接。
2. 群里有人 @ 她、或者你喊「小爱过来」→ 焦点＝那个群，群里她正常接话。
3. 你回到私聊再说话 → 焦点又回到私聊。
4. 想临时放开：任意一个流里发 `/关闭聚焦` —— 她恢复成平常那样，哪儿说话都接。
5. 想再锁上：`/打开聚焦`（锁到当前这个流），或 `/聚焦 这里`。
6. 她自己也看得到处境：被问到时能用 `get_focus_state` 查到「现在只在这边说话」。

## 配置

配置文件：`config/plugins/focus_stream/config.toml`（首次加载会生成）。

### `[whitelist]` —— **这是必看的一节**

只有名单里的人能把她的注意力挪走。别人在群里怎么喊，焦点都不动
（那些消息照样进流、留在历史里）。

```toml
[whitelist]
# 格式 平台:用户ID；也接受裸 user_id（对所有平台生效）
users = ["qq:123456789"]
# 是否同时信任 config/core.toml 的 permissions.owner_list（默认 true）
use_owner_list = true
# 名单为空又关了上面那项时：false＝自动聚焦整体停用，只留命令（默认，安全）
fallback_allow_all = false
```

> 名单与 `owner_list` **都为空**时，自动切换不会生效（日志里会有一条 warning）：
> 只剩命令，且命令也用不了。所以第一件事是把主人 ID 填进 `users`。

### `[focus]` —— 什么算「呼唤」

```toml
[focus]
private_auto = true          # 私聊里主人一说话就自动聚焦过去
group_requires_call = true   # 群里需要「呼唤」才切焦点
call_requires_at = false     # false＝@ 她或叫名字都算；true＝只认 @
call_words = []              # 额外称呼，例 ["小爱", "阿爱"]（昵称/别名已自动认）
silence_when_unfocused = true # 还没确定焦点时先静音（等你先开口）
stale_hours = 0.0            # >0 时：聚焦挂这么久自动解除（0＝永不）
```

她的昵称与别名取自 `config/core.toml` 的 `[personality] nickname` / `alias_names`，
不用在这里重抄。

### 其它

```toml
[plugin]
enabled = true
state_key = "focus_stream"
restore_after_restart = true  # 重启后接着上次的聚焦
debug_log = false             # true＝日志里输出每次拦截/切换明细

[inject]
enabled = true                # 把「此刻聚焦在哪边」注入 system reminder（她才知道）
buckets = ["actor"]
name = "focus_stream_now"
text = ""                     # 留空＝用内置通用文案，{label} 会替换成聚焦流名字
guard = ""                    # 防背书说明，留空＝内置

[reply]
hidden = true                 # 命令回执对她隐身：你看得到，她的上下文里没有

[tools]
enabled = true                # 只读的 get_focus_state（不给她自己解除聚焦的权限）
```

## 命令

触发词有四个：`/流聚焦`、`/聚焦`、`/焦点`、`/focus`；开关词有 `开启`、`关闭`、`开`、`关`、`这里`。
**空格与连写都认**（`/流聚焦 开启` ＝ `/流聚焦开启`）。

| 命令 | 作用 |
| --- | --- |
| `/流聚焦`、`/聚焦` | 看当前状态（聚焦在哪、规则是什么、谁在名单里） |
| `/流聚焦 开启`、`/流聚焦开启`、`/聚焦 开` | 把焦点锁到当前这个流 |
| `/流聚焦 关闭`、`/流聚焦关闭`、`/聚焦 关` | 关掉聚焦，恢复成平常那样 |
| `/聚焦 这里` | 就地切过来（等价于「开启」） |
| `/打开聚焦`、`/关闭聚焦`、`/focus_on`、`/focus_off` | 两个快捷写法 |

命令只认 `[whitelist]` 名单，别人发了只会收到一句「这个开关只认主人」，状态一个字都不改。

**回执对她隐身**（默认）：回执照常发进聊天、你看得到，但**不写进聊天流历史**——不然她下一轮
就会看见「/流聚焦 开」这句话，既知道有人在拨开关，也可能把回执当成自己说过的话接下去。
框架的 `send_api` 只有「发就记」这一条路，所以这里照 `MessageSender` 的流程走到「适配器已发送」
为止；任何一步不符合预期都自动退回普通发送，保证回执发得出去。不想要这个行为就
`[reply] hidden = false`（排查命令问题时把回执留在上下文里反而有用）。

**命令不受静音影响**：它走的是命令分发（消息入库那一步直接执行），不经过 Chatter。
所以她正在某个流里静音着，你就在那个流里敲 `/流聚焦 开启`，焦点立刻就回来了——
不需要先跑到别处解除。

## 原理：为什么是「看得到但不回复」

拦在**流循环调用 Chatter 之前**那一拍（框架的 `on_chatter_step` 事件）：

- 消息已经入库（历史里留着、别人的 @ 也记着）；
- 只是把这一拍的 `continue` 置成 `false`，本轮不进入模型 —— 她不出声，也不花钱。

所以等焦点切回那个流、你再说一句，她能把之前那一段都读进上下文接上话题；
当时只是「一声没吭」，不是「没看见」。

## 边界（先知道的坑）

- **静音期间积压的未读会被框架清掉**（这是 `on_chatter_step` 机制的既定行为）：
  消息本身在数据库与历史里，但她看不到「未读小红点」那种意义上的未读。
  如果你的角色卡/别的插件依赖未读列表，观感上会觉得那段时间她「没注意到」。
- **只拦「她的对话回应」**：靠 `send_api` 直接往外发消息的插件（例如定时主动搭话）
  不受本闸门影响。要让它们也守规矩，得在那些插件侧接入（可以后续按需加）。
- **卸载不清存档**：`data/json_storage/focus_stream.json` 会留着，装回来接着用；
  想从零开始就删掉它，或把 `restore_after_restart` 设成 `false`。
- **闸门异常不拖垮流**：任何内部异常都被吞掉并按「放行」处理——宁可她多说一句，
  也不能让整个流循环卡住。

## 依赖与兼容

- 零第三方依赖、零 Python 依赖，`dependencies.plugins` 为空（不写「建议搭配」——写进去
  会让插件被静默剔除）。
- 用到的插件 API：`log_api`（日志）、`send_api`（命令回话）；配置 / 状态 / 提示词注入走内核
  模块：`src.core.components.base.config`、`src.kernel.storage.json_store`、
  `src.core.prompt.get_system_reminder_store`、`src.core.config.get_core_config`。
- **不绑定任何 Chatter 实现**：闸门挂在框架的 `on_chatter_step` 上——`default_chatter`
  也好、别的对话引擎也好，只要走流循环就一样受管，换 Chatter 不用改本插件。
- 白名单与「呼唤词」从框架配置读：`permissions.owner_list`、`personality.nickname`
  与 `alias_names`。框架哪天改了名，只要在这一层对上即可；读不到时会退回插件自己的
  `[whitelist] users` 与 `[focus] call_words`，不会报错。

## 自检

两个脚本，都用目标实例自己的 venv 跑（都不启动 Bot、不连任何平台）：

```powershell
$env:NEO_ROOT="<你的 Neo-MoFox 框架根目录>"

# 1) 逻辑与组件：白名单 / 呼唤 / 静音判定 / 闸门与观察者替身用例 / 持久化往返
& "<框架根>\.venv\Scripts\python.exe" "<插件目录>\tests\check_focus.py"

# 2) 加载链路：把插件交给框架 PluginManager 真装一遍（读 manifest → 注册组件 → 跑钩子）
& "<框架根>\.venv\Scripts\python.exe" "<插件目录>\tests\check_loading.py"
```

## 版本

- **1.0.1** —— 命令更好用 + 回执隐身：触发词加上 `/流聚焦`（`/聚焦` `/焦点` `/focus` 同样认），
  空格与连写都行（`/流聚焦开启`）；命令回执默认**不写进聊天流历史**，她看不到有人在拨开关
  （`[reply] hidden`，失败自动退回普通发送）。
- **1.0.0** —— 首个版本：聚焦闸门（非聚焦流只看不回）、私聊自动聚焦、群里呼唤切换、
  白名单权限、`/打开聚焦` `/关闭聚焦` `/聚焦` 快捷开关、状态持久化与上下文注入、
  只读的 `get_focus_state` 工具。

MIT License © 2026 Aemeath
