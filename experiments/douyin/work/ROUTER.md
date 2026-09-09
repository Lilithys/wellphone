# 路由 MVP：实现、证据与下一次验收

## 已实现

- `router/planning.py`：有限中文语法和可选 OpenAI-compatible 通用 LLM 目标提取；目标 schema、时间范围、标题原文和额外字段校验。LLM 不指定 shell、URI、权限或执行器参数。目标最多三项，不支持的语法/业务整项澄清，不悄悄丢弃额外要求。
- `router/planner_client.py`：CLI 当前使用的模型配置与请求层。官方 DeepSeek 精确匹配域名与路径后只读取 `DEEPSEEK_API_KEY`；其他兼容接口只读取 `WELLPHONE_PLANNER_API_KEY`，均不回退到 `PHONE_AGENT_API_KEY`。为了不改变已验收执行哈希，保留 `planning.py` 原文件（其中旧请求函数不再由 CLI 使用）；目标校验仍复用该文件。
- `router/device.py`：只读状态探测、Android 用户绑定、主屏键盘/IME/目标 App 冲突检查和本路由器的单设备文件锁。允许用户切换到其他非冲突 App，不要求主屏 Activity 永远不变。
- `router/calendar_tool.py`：固定 Calendar Provider 白名单写入；不需要 APK，不启动日历 Activity，不操作 IME/剪贴板。ADB shell 是已授权调试身份，**不是声称普通 App 无权限也能操作日历**。
- `router/gui_tool.py`：复用冻结的 AutoGLM 设置→关于手机任务，加每步默认 IME/主屏键盘检查。仅 Launch(Settings)/Tap/Swipe/Back/Wait/Note/finish，不接受任意任务文本或 Type。原来 93 个文件不改，运行时子类只影响当前路由进程，退出恢复类绑定。
- `router/cli.py`：plan（默认不连接手机、不调用模型）、doctor（只读）、calendar-test（首次交互写入验收）、run（逐步路由/预览/确认/执行/验证）。所有用户可见完成结果留在电脑；不创建手机通知、不声称已实现后台通知监听。

## 为什么不是“有 API 就一定静默”

Calendar INSERT Intent 会把用户送到日历表单；本工具直接使用 Provider，前提是这台手机的 ADB 调试身份具有访问权限、日历可写、用户已确认具体事件。参考 [Calendar Provider](https://developer.android.com/identity/providers/calendar-provider)。`AlarmClock` 仅作为后续能力，未注册或实现。

工具只使用固定 `content://com.android.calendar/calendars` 与 `events` URI；数字投影严格解析。事件回读把标题、起止毫秒、时区、日历 ID、操作标记、无提醒、无参与者、未删除状态一起作为查询条件，再读取事件 ID，不抓取已有日程正文。使用 SQL 字符串转义与远端 `shlex.join`；标题支持中文，但首版拒绝控制字符、英文冒号和反斜杠，避免不同 Android `content --bind` 解析规则差异。参考 [AOSP content 工具源码](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/cmds/content/src/com/android/commands/content/Content.java)。

## 写入与失败语义

1. 将“设备、日历、标准化目标”计算为稳定操作键；marker 写入日程 description。
2. 写入前先查 marker；存在且所有字段一致则复用，不创建重复事件。
3. 查询结果表示用户修改/删除或重复时，停止，不覆盖。
4. 在本地 SQLite 提交 `write_started` 后才发送一次插入命令。
5. 命令超时或返回报错后仅回查；只有唯一事件及全部字段匹配才确认数据完成。
6. 已尝试写入但回查不到时保持失败/待核查，不重试、不降级到 GUI；本地日志重启后仍保留。
7. 主屏采样或人工观察失败时，即使事件写入成功也不能报整体完成；不自动删除已经创建的数据。

`outputs/router-journal.sqlite3` 是防重复的执行状态，不只是可随意清除的日志。删除它会丢失未知结果的保护；不要为“重试”而清空。若用户更改了时间/标题，则属于不同目标；如果此前结果不确定，应先人工核对，再提交新目标。

## 实测与尚未验收

- 原基线：`baseline-no-steal-v1`（c9510ab），同机副屏设置导航、主屏持续打字通过。
- ASCII：`checkpoint-ascii-v1`（3f9b925），17:06 那轮副屏 `7`→`7abc123`、70 次主屏采样正常、清理正常。原始证据保留在 sibling `wellphone-keyboard-experiment/outputs/ascii-input-20260908-170623-n8fb4f56`；result SHA256 `0597873b0758aeb996ee4d986ee86874a8dcb9e4fecbebca2b6f94e25cac686b`。此能力仍是独立探针，不等于通用 Type。
- 路由开发：原131项离线测试，加19项规划配置/SDK模拟测试、7项失败诊断测试，共157项。覆盖密钥分离、地址校验、非思考 JSON 参数、拒绝截断/工具调用/无效输出、异常与诊断脱敏、诊断保存失败不重试及 plan 不连接 ADB；新增明确 null 契约和19:37响应的回归。全部测试使用虚拟 Provider/SDK，不调用真实 adb、API 或写入手机。
- 2026-09-08 18:44 本机只读 doctor 通过：HONOR ELZ-AN00 / SDK34、Android 用户0、主屏短信键盘归属0、无已有副屏，日历 ID1 访问级别700且可见，识别 `com.hihonor.calendar`；事件精确回查字段预检返回无匹配而非错误。
- 18:55 静默日历实测通过：`outputs/router-20260908-185534-7919g3h8/result.json`，事件28新建且回读一致，14次主屏采样和人工观察正常，登记本机资格。
- 19:00 规则双通道实测通过：`outputs/router-20260908-190010-gjiczi5g/result.json`，事件29新建、13次主屏采样正常；关联 `outputs/router-gui-20260908-190039-hp8qoroi/result.json` 中 GUI 目标验证通过、90次采样与人工观察正常、副屏清理成功。
- **首轮真实 DeepSeek 请求已收到响应，但目标校验失败，尚未验收通过。**旧版本没有保存被拒绝的响应，不能认定具体是日期、标题或 JSON 结构问题；已补静态错误码和本地诊断，待原命令复测，不放宽规则、不自动重试。日历执行器、规则校验与资格记录不变；实机样本不能证明任意任务/设备的零干扰。
- 19:37复测诊断 `outputs/planner-diagnostic-20260908-193723-mw0h918l/result.json`：两个目标、标题与时间均符合要求，但 `question` 为 `""` 而非 `null`，触发 `CLARIFICATION_SCHEMA`。提示词此前只说明了澄清分支，漏写无澄清时必须返回 null。现补充互斥分支和完整 JSON 示例；不把空字符串静默改成 null，也不修改原验证器。离线对照仅改变该字段即可通过，不表示修正提示词后的真实调用已通过；需再次 plan 验收。

## 新设备：先验收静默日历

运行 `python3 /Users/yishanma/Documents/Codex/wellphone-router/run.py router calendar-test`。不需要 API key；必须在 Terminal 交互运行。它预览一条次日16:00–16:30的 `Wellphone 验收 <时间戳>` 日程，确认日历 ID、标题、时间后输入 yes，并在倒计时后保持手机主屏持续打字。成功写入并回读后，按 0/1/2 记录键盘观察。只有数据、采样和观察同时通过才登记该设备的写入资格。

此日程不设提醒、不发送邀请，但可能随所选日历同步至账号，测试后保留。脚本结束后可去日历中查看或自行删除，不在并发观察期间打开日历。

再运行 README 中的复合任务。第一项选 Calendar，第二项按用户明确的页面目标选择隔离 GUI；任一步条件变化则等待，已完成部分保留并在结果中列明。

## LLM 规划的明确边界

`--planner llm` 才调用模型，需要 `WELLPHONE_PLANNER_MODEL`（账号已开通的通用模型名）；不自动猜测、切换或试用付费模型。模型只接收当前任务文字和固定规则/时间，不接收主屏截图、日程列表或 IME 内容。响应必须严格 JSON，不执行输出中的代码；未知键、重复键、未知能力、缺时区、过期时间和不在原文的标题均被拒绝。

结构校验不能证明模型完全理解了语义，因此实际执行前仍预览所有参数、路由与副作用并人工确认。规则模式不联网，但只能理解 README 的有限句式；缺少时段/时长等信息会澄清。不能把规则模式的成功称为 LLM 验收成功。

## DeepSeek 只规划测试

在已导入 `DEEPSEEK_API_KEY` 的同一终端运行；无需连接手机、持续打字或启用副屏。不要把 DeepSeek Key 导入 `PHONE_AGENT_API_KEY`。如果没有继承 Key，程序会按服务商提示隐藏输入，不保存。

```sh
export WELLPHONE_PLANNER_MODEL="deepseek-v4-flash"
export WELLPHONE_PLANNER_BASE_URL="https://api.deepseek.com"
python3 /Users/yishanma/Documents/Codex/wellphone-router/run.py router plan '帮我把明天下午四点到四点半的「方案评审」记入日历，然后打开设置里的关于手机页面，不修改设置。' --planner llm
```

预期：`source=llm`、`status=PLANNED`、两个目标 `calendar.create` / `gui.settings_about`、日程16:00–16:30且日期为运行当天的次日、`execution=NOT_EXECUTED`。检查的是模型返回及参数，不要只看进程退出成功。命令不会创建日程、查询设备或发送手机通知；会向配置的服务发送任务文本、固定规则与当前时间，产生一次可能计费的 API 请求。成功结果当前打印在终端。

目标校验失败时打印具体静态原因，例如 `ROOT_SCHEMA`、`CLARIFICATION_SCHEMA`、`TIMEZONE_MISSING`、`TITLE_NOT_IN_REQUEST`，并保存 `outputs/planner-diagnostic-*/result.json`：本地任务文字、模型正文（有长度限制且对当前 Key / 常见 sk-形式做脱敏）、请求参考时间及错误码，不保存 SDK 响应头、请求头或思考内容。目录权限700且被 Git 忽略。诊断正文是未经信任的模型数据，不得当作操作指令；诊断脱敏不等于删除所有可能的个人信息，对外分享前需检查。原校验不变，可据此离线重放，不自动修复、重试或执行手机动作。

首例通过后再分别测试：`明天下午开个「方案评审」，帮我记一下。`（缺具体时间/时长，应澄清）；`明天下午四点到四点半安排「方案评审」，提前十分钟提醒我。`（提醒未实现，应澄清而非丢弃要求）；`不要打开关于手机页面。`（否定命令，不应生成目标）。仅调整 plan 的任务文字，不要换成 run。

按 [DeepSeek 首次调用文档](https://api-docs.deepseek.com/zh-cn/) 配置接口；按[请求参数文档](https://api-docs.deepseek.com/api/create-chat-completion/)显式传递 `thinking.type=disabled` 与 `response_format.type=json_object`，避免默认思考耗尽短输出额度；仍要求 `finish_reason=stop`、无工具调用、严格 schema。超时35秒、不自动重试/换模型。模拟通过不等于真实 Key、账号额度或模型语义测试通过。

## 剩余范围

截图/焦点采样是离散观测，不是数学上的零干扰证明。检查与动作之间存在时间间隙；CPU、内存、网络、温度和帧率尚未测量。日历 App 冲突识别针对本机已发现的包名，不保证所有第三方日历客户端。文件锁只协调本路由器实例，不能控制外部 adb 程序。权限、账号、系统更新和新 App 均需要重新验收；不自动开权限或接管主屏。网络 API、闹钟、通知监听、示教和任意 App 任务都留在后续，不使用伪造能力占位。
