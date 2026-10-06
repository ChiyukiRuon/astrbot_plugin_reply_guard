# 引用回复 AI 拦截

AstrBot 插件：按你配置的消息格式，关闭引用机器人消息触发的默认 AI 回复。规则完全自定义，默认列表为空，不拦截任何消息。主动推送消息也可识别，只需平台提供原消息信息。

## 安装

把整个 `astrbot_plugin_reply_guard` 目录复制到 AstrBot 的 `data/plugins/` 下，然后重启 AstrBot 或在 WebUI 中重载插件。在插件配置页设置选项并保存。

当前目录是本地 Git 仓库，尚未发布到 GitHub。发布后可在 `metadata.yaml` 添加真实的 `repo` 地址，再使用 WebUI 的仓库链接安装。

## 行为与配置

### 自定义屏蔽消息格式

在 WebUI 插件配置页编辑 `format_patterns` 列表，每一项是一条 Python 正则表达式。**任意一条匹配被引用的机器人原消息就屏蔽**；空列表不屏蔽。插件不提供预设模式或默认规则。升级时旧的 `mode` 字段不再生效；已保存的规则仍属于你的配置，如果不需要请清空列表。

插件按原消息组件顺序转换格式：文字原样保留，图片为 `[图片]`，语音为 `[语音]`，视频为 `[视频]`，文件为 `[文件]`，@ 为 `[艾特]`，表情为 `[表情]`，引用为 `[引用]`，转发为 `[转发]`，JSON 卡片为 `[卡片]`；Share 为 `[链接]` 加链接地址。组件之间不额外添加空格。图片地址不会加入格式文本，也不展开转发内容。

例如原消息是一张图片加文字 `详情：https://example.com/123`，匹配文本就是 `[图片]详情：https://example.com/123`。这些规则匹配原消息，不匹配用户输入的回复内容。

下面的规则可以直接作为 WebUI 列表中的单项输入（不要加 Markdown 反引号）：

| 想屏蔽的消息格式 | 规则 |
| --- | --- |
| 同时包含图片和链接，不限制顺序 | `(?s)(?=.*\[图片\])(?=.*https?://).+` |
| 任意含图片消息 | `\[图片\]` |
| 只有一张图片，可有空白 | `^\s*\[图片\]\s*$` |
| 一张图片后直接跟一个链接，可有空白 | `^\s*\[图片\]\s*https?://\S+\s*$` |
| 包含“签到成功”的通知 | `签到成功` |
| 图片消息同时含“每日推荐” | `(?s)(?=.*\[图片\])(?=.*每日推荐).+` |
| 含指定站点链接 | `https?://(?:www\.)?bilibili\.com/` |
| 语音或视频消息 | `\[(?:语音|视频)\]` |
| 所有机器人消息引用 | `(?s).*` |

默认是局部匹配；需要严格匹配整条消息时使用 `^` 和 `$`。`(?s)` 允许 `.` 匹配换行。无效正则会跳过并记入日志，空白规则也跳过。只填写你信任的正则，避免复杂嵌套重复造成处理耗时。WebUI 输入单个反斜杠；直接编辑 JSON 文件时反斜杠需要写成 `\\`。

默认群聊、私聊均生效，只在确认引用发送者是当前机器人时拦截。

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| enabled | true | 启用插件 |
| format_patterns | 空列表 | 每项一条正则，任意匹配就屏蔽；未配置时不拦截 |
| apply_group | true | 群聊生效 |
| apply_private | true | 私聊生效 |
| fetch_missing_reply | true | OneBot v11 引用信息缺失时调用 get_msg 补查，超时 5 秒 |
| stop_event | false | true 时同时终止后续插件处理 |

只有你添加规则后才会拦截。规则只匹配被引用的当前机器人的原消息；引用他人消息或不引用消息直接聊天，不受影响。

图片本身的下载地址不算文本链接；图片和链接必须属于**同一条被引用消息**。支持 Plain/Text 文本链接以及 Share 链接组件，不解析 JSON 卡片、嵌套合并转发或以 CQ 字符串返回的原消息。

## 兼容与限制

使用 AstrBot 的 `Reply.sender_id`、`Reply.chain`、`Reply.message_str` 识别引用；其他平台是否可用取决于适配器是否填充这些字段。QQ / OneBot v11（aiocqhttp，常见如 NapCat）有 get_msg 补查支持。消息被撤回、历史不可获取或发送者未知时放行，避免误拦截。无需第三方 Python 依赖。

默认使用 `event.should_call_llm(True)`，AstrBot 此接口的 True 表示**禁止默认 LLM 请求**，保留其他插件指令。其他插件自行调用 LLM 不受此开关控制；需要阻止后续插件时开启 `stop_event`。已在更高优先级处理器中开始的请求无法撤销。

## 验证

在插件目录运行 `python -m unittest discover -s tests -v`。测试使用轻量 AstrBot API 替身检查策略与事件行为，不等同于实机测试。

实机验收：先保持规则为空，检查引用机器人消息仍可正常触发 AI。添加 `签到成功` 规则后，引用机器人签到通知应不自动回复，引用其他消息应正常。清空规则后应恢复正常；引用时发送已有插件指令，默认仍应执行。

参考：[插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)、[消息事件 API](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/astr_message_event.py)、[OneBot 适配器](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py)。
